# uam Performance Review — Phases 1-5 Hardening

Scope: changes since commit `23e094c` (Phase 1 logging, Phase 2 retry headers,
Phase 3 translation hardening, Phase 4 api_format, Phase 5 capabilities).

Files reviewed:
- `src/uam/log.py` (new)
- `src/uam/proxy.py`
- `src/uam/translate.py`
- `src/uam/state.py`
- `src/uam/router.py`
- `src/uam/discovery/*.py`

Overall verdict: no severe bugs. The largest real issue is the unbounded /
quadratic SSE line buffer; everything else is minor or only matters at debug
log levels.

---

## HIGH

### H1. Unbounded SSE buffer growth + O(n^2) newline scan
**File:** `src/uam/proxy.py` lines 274-293 (`_proxy_with_translation` streaming loop)

```python
buffer = b""
async for chunk in upstream.content.iter_any():
    buffer += chunk
    while b"\n" in buffer:
        line, buffer = buffer.split(b"\n", 1)
        ...
```

**Description:**
1. If an upstream backend ever sends a very large "line" with no `\n` (a
   pathological vLLM/OpenRouter response, an HTML error page, an attacker-
   controlled local backend, etc.), `buffer` will accumulate the entire
   response in memory before being processed. There is no max-size guard.
2. `b"\n" in buffer` plus `buffer += chunk` re-scans the whole buffer every
   chunk. For an N-byte response delivered in K chunks with no newline, that
   is O(N*K) work before any line is emitted.

**Impact:** Memory pressure / DoS on a misbehaving upstream; quadratic CPU on
chunky streams. In normal operation upstreams send `\n\n`-separated SSE
frames so this rarely bites — but it is a real liveness risk.

**Recommended fix:**
- Cap `buffer` at e.g. 1 MiB; if exceeded, log and abort the stream with an
  Anthropic-format error.
- Scan only the newly arrived chunk for newlines (track an `index` into
  `buffer` instead of using `in`), or use `bytearray` + `find` from a
  remembered offset, to make the scan linear.

---

## MEDIUM

### M1. Sync file I/O on the event loop in state writes
**File:** `src/uam/state.py` lines 21-24, 186-235; called from
`src/uam/proxy.py` lines 561, 576-578 (`handle_post_state`)

`handle_post_state` runs (all synchronous, on the asyncio thread):
- `load_state()` -> `STATE_PATH.read_text()`
- `save_state()` -> `STATE_PATH.write_text()`
- `write_env_file()` -> `mkdir` + `write_text` + `chmod`

**Impact:** Each POST /state blocks the proxy event loop for the duration of
3 disk operations. On SSD this is sub-millisecond, but it does stall every
in-flight stream. POST /state is user-initiated (toggle, set default), so
typical volume is single-digit per session — impact LOW in practice, MEDIUM
in principle because the *streaming* loop can be paused mid-frame.

**Recommended fix:** wrap the three calls in
`await asyncio.to_thread(...)`. Cheap and removes any event-loop blocking.

### M2. Sync RotatingFileHandler I/O from async handlers
**File:** `src/uam/log.py` lines 32-40; consumers throughout `proxy.py`,
`translate.py`, `router.py`, `discovery/*`.

`setup_logging` installs a `RotatingFileHandler`, which performs blocking
`write()` + occasional rotation rename from inside `logger.debug/info/warning/
error` calls. Every such call from an async handler blocks the event loop
briefly.

**Impact:**
- At default `WARNING` level: negligible (only error paths log).
- At `INFO`: per-request hits in `router.start`, discovery, etc. — fine
  outside the request hot path.
- At `DEBUG`: `_convert_message_to_openai` (translate.py:117, 122, 147) emits
  one debug record *per content block* while translating each request. Long
  message histories with many thinking/unknown blocks => many sync writes per
  request, on the event loop.

**Recommended fix:**
- Either wrap the handler with `logging.handlers.QueueHandler` +
  `QueueListener` so file I/O happens on a background thread, or
- Document that `UAM_LOG_LEVEL=DEBUG` is for diagnosis only, not production.

### M3. f-string evaluated even when debug disabled (hot path)
**File:** `src/uam/proxy.py` line 150

```python
logger.debug(f"Route: {model} -> {effective_model} via {route['backend']}")
```

Runs on every `/v1/messages` request regardless of log level. Cheap, but it
is the only logging in the per-request hot path and uses an eager f-string
plus a dict lookup. Same pattern in `translate.py:91, 117, 147`.

**Impact:** Tens of nanoseconds per request — measurable only under load.

**Recommended fix:** use lazy form:
```python
logger.debug("Route: %s -> %s via %s", model, effective_model, route["backend"])
```
Or guard with `if logger.isEnabledFor(logging.DEBUG):`.

---

## LOW

### L1. State cache reload performs sync disk read
**File:** `src/uam/proxy.py` lines 26-33 (`_get_state`)

When the 5-second TTL expires, the next request handler calls `load_state()`
synchronously, which reads `~/.uam/models.json` from disk on the event loop.

**Impact:** sub-ms on SSD. Negligible. Documenting for completeness — same
fix applies as M1 (`asyncio.to_thread`) if you want a strict no-blocking
guarantee.

### L2. Double JSON encode/decode on translated non-stream responses
**File:** `src/uam/proxy.py` lines 307-309

```python
data = await upstream.json()           # parse upstream JSON
anthropic_resp = openai_to_anthropic(data, effective_model)
return web.json_response(anthropic_resp)  # serialize again
```

Acceptable — translation requires the parsed dict — but worth noting that
each non-stream response pays one parse + one serialize. If max_tokens are
large, this is the dominant CPU cost on the response path. No fix
recommended.

### L3. `dict(upstream.headers)` allocation per error
**File:** `src/uam/proxy.py` lines 180, 249, 408, 433, 484

`_retry_headers(upstream.status, dict(upstream.headers))` materializes the
multidict to a dict only to do two `.get()` lookups. Negligible, but you can
pass `upstream.headers` directly — `CIMultiDict.get()` already exists.

### L4. `print()` from async handlers / discovery
**File:** `src/uam/proxy.py` lines 520, 524; `src/uam/discovery/local.py`
line 80.

These do sync stdout writes on the event loop. Volume is low (refresh +
discovery only). Replace with `logger.info` for consistency.

---

## INFO

### I1. State `models` dict grows monotonically across refreshes
**File:** `src/uam/state.py` lines 238-263 (`sync_state_with_routes`)

Removed models are intentionally retained ("may come back"). Over many
refreshes against rotating RunPod pods, `models.json` grows without bound.
Not a leak in the GC sense, but disk + JSON-parse cost trends upward.

**Recommendation:** add a `last_seen` timestamp and an opt-in prune for
entries older than e.g. 30 days. Not urgent.

### I2. Route GC on refresh is correct
**File:** `src/uam/router.py` lines 61-66 (`refresh`)

```python
self.routes = {k: v for k, v in self.routes.items() if v["backend"] == "anthropic"}
await self.discover()
```

Old route dicts are dereferenced and collected normally. No leak. Sessions
and connectors are reused via `self.session` (a single ClientSession started
once in `start`), which is the correct pattern.

### I3. `infer_capabilities` and `auto_aliases` complexity
**File:** `src/uam/state.py` lines 57-87, 149-183

`infer_capabilities` is O(1) per model (a small fixed sequence of
`startswith` checks). `sync_state_with_routes` calls it once per *new*
model -> O(n) total. `auto_aliases` is also O(n * F) where F is the family
list (~22). No O(n^2) anywhere. Fine for thousands of models.

### I4. State cache invalidation coverage
**File:** `src/uam/proxy.py`

`_invalidate_state_cache()` is called from `handle_refresh` (line 522) and
`handle_post_state` (line 581). `router.start()` and `router.refresh()` also
write state via `_sync_state` -> `save_state`, but:
- `start()` runs once before the HTTP server accepts requests, so the cache
  is empty anyway.
- `refresh()` is only reachable through `handle_refresh`, which already
  invalidates.

So invalidation is correctly tied to writes that can happen while the cache
is warm. OK.

### I5. Translation try/except overhead is negligible
**File:** `src/uam/proxy.py` lines 229-237

A single `try/except` around `anthropic_to_openai`. Python try/except has
near-zero cost on the no-exception path. Not a concern.

---

## Summary table

| ID  | Sev    | File:line                       | One-line                                          |
|-----|--------|---------------------------------|---------------------------------------------------|
| H1  | HIGH   | proxy.py:274-293                | SSE buffer unbounded + O(n^2) newline scan        |
| M1  | MEDIUM | state.py + proxy.py:544-582     | Sync file I/O on event loop in handle_post_state  |
| M2  | MEDIUM | log.py:32 (all callers)         | RotatingFileHandler blocks event loop on writes   |
| M3  | MEDIUM | proxy.py:150, translate.py:*    | Eager f-strings in debug logs on hot path         |
| L1  | LOW    | proxy.py:26-33                  | _get_state cache miss does sync disk read         |
| L2  | LOW    | proxy.py:307-309                | Double JSON parse/serialize on translated resp    |
| L3  | LOW    | proxy.py:180,249,408,433,484    | Unnecessary dict() of CIMultiDict                 |
| L4  | LOW    | proxy.py:520,524; local.py:80   | print() from async paths                          |
| I1  | INFO   | state.py:238-263                | models dict grows monotonically                   |
| I2  | INFO   | router.py:61-66                 | Route GC on refresh is correct (no leak)          |
| I3  | INFO   | state.py:57-183                 | Capability/alias inference is O(n), not O(n^2)    |
| I4  | INFO   | proxy.py                        | State cache invalidation is correctly placed      |
| I5  | INFO   | proxy.py:229-237                | Translation try/except overhead negligible        |

## Recommended action order
1. **H1** — fix the SSE buffer (cap size + linear scan).
2. **M1, M2** — wrap state writes and the file log handler so the event loop
   never blocks. These are tiny patches with outsized correctness benefit.
3. **M3, L4** — convert eager f-string debug logs and `print()` calls to
   lazy `logger.*("...", arg)` form.
4. The rest can wait.
