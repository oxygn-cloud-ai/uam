# Code Issues — Adversarial Audit of Phases 1–5 (23e094c..HEAD)

Scope: commits cdc7be7, 6354f2c, a857494, 1bcf736, f48c210, b430300, c7fc95f.

---

## CRITICAL

### C1. `_retry_headers` silently drops `Retry-After` due to case-sensitive lookup [FIXED]
**File:** `src/uam/proxy.py:331-348` (and all callers passing `dict(upstream.headers)`)
**Severity:** CRITICAL

**Fix:** `_retry_headers()` now normalizes all header keys to lowercase
before lookup, so `Retry-After`, `RETRY-AFTER`, and `retry-after` all
work. Call sites updated to pass `upstream.headers` (CIMultiDict)
directly instead of `dict(upstream.headers)`. Regression test:
`TestRetryHeadersCanonicalCase` covers canonical case, uppercase,
lowercase, and CIMultiDict inputs.

`_retry_headers` is called like this from every error path:

```python
retry_hdrs = _retry_headers(upstream.status, dict(upstream.headers)) if upstream.status >= 400 else {}
```

`upstream.headers` is an aiohttp `CIMultiDict` (case-insensitive). The moment you `dict()`-cast it, you get a plain `dict` whose keys preserve the original casing. Inside `_retry_headers`:

```python
for h in ("retry-after", "retry-after-ms"):
    val = upstream_headers.get(h)
```

This is a case-sensitive lookup. RFC 7231 / 6585 specify the header as `Retry-After` and most servers (Anthropic, OpenRouter, RunPod's vLLM, Cloudflare in front of them) emit it as `Retry-After`. Reproduction:

```python
>>> from multidict import CIMultiDict
>>> dict(CIMultiDict([('Retry-After', '5')])).get('retry-after')
None
```

**Impact:** Phase 2's entire stated goal — "propagate retry-after* so the caller can make informed decisions" — silently fails for the most common upstream casing. Tests in `test_proxy.py::test_retry_headers_429_with_retry_after` only pass because the test feeds a lowercase dict, so the bug is invisible to the test suite.

**Fix:** Either pass the `CIMultiDict` straight through and use case-insensitive `.get()`, or normalize keys to lowercase before lookup:

```python
retry_hdrs = _retry_headers(upstream.status, upstream.headers)
```
and inside `_retry_headers` rely on CIMultiDict semantics. Add a regression test using `Retry-After` (capitalized).

---

## HIGH

### H1. `get_backend_timeout` is dead code — per-backend timeouts not actually applied [FIXED]
**File:** `src/uam/config.py:48-53`, `src/uam/router.py:28`
**Severity:** HIGH

**Fix:**
- All four discovery modules (`anthropic`, `local`, `openrouter`, `runpod`)
  now call `get_backend_timeout(config, backend)` and store the value as
  `route["timeout"]`.
- `router.resolve()` fallback synthesizes a timeout via the same call.
- `proxy.py` adds a `_route_timeout(route)` helper and passes
  `timeout=_route_timeout(route)` to every `session.post(...)` call (5
  call sites: native messages, translated messages, ask translated, ask
  native, count_tokens).
- Session-level `total=600` remains as a safety net.

Verified by `TestRouteTimeouts` (local=120, anthropic=600, openrouter=300).

Phase 1's commit message and CLAUDE.md announce "per-backend timeouts." `get_backend_timeout()` is implemented and tested in `tests/test_logging.py::TestBackendTimeouts`, and `default_config()` now includes a `timeout` field per backend. **None of this is wired into anything.**

`router.py` still creates a single `aiohttp.ClientSession` with a hardcoded `total=600, connect=10`:
```python
self.session = aiohttp.ClientSession(
    timeout=aiohttp.ClientTimeout(total=600, connect=10)
)
```

That session is reused for every upstream POST in `proxy.py`. There is no per-request `timeout=` override anywhere. The runpod-30s, local-120s, openrouter-300s configurations are lies.

**Impact:** A long-running local llama call can be killed at 600s; a fast openrouter call can hang for 600s on a stuck connection.

**Fix:** Either (a) pass `timeout=aiohttp.ClientTimeout(total=get_backend_timeout(config, route['backend']))` on every `session.post(...)` call in `proxy.py`, or (b) remove the field, the function, and the tests, and document that the proxy uses a global 600s timeout.

---

### H2. `extract_think_tags` parameter is dead — never invoked from production code [FIXED]
**File:** `src/uam/translate.py:181-223`
**Severity:** HIGH (per the user's explicit prompt)

**Fix:** `_proxy_with_translation()` now calls
`openai_to_anthropic(data, effective_model, extract_think_tags=True)`
on the non-streaming response path. Safe — only strips complete balanced
tags from the start of text. Verified by
`TestProxyExtractsThinkTags::test_proxy_extracts_think_tags_nonstreaming`
which posts a `<think>step 1</think>final answer` upstream response and
asserts the proxy returns a thinking block + a text block.

`openai_to_anthropic` accepts `extract_think_tags: bool = False`, but no caller ever passes `True`:

```
$ rg extract_think_tags src/
src/uam/translate.py:184:    extract_think_tags: bool = False,
src/uam/translate.py:194:        extract_think_tags: If True, ...
src/uam/translate.py:214:        if extract_think_tags:
```

`proxy.py` calls `openai_to_anthropic(data, model)` and `openai_to_anthropic(data, effective_model)` — never with the kwarg. There is no per-route or per-config flag to enable it. The 5 tests in `test_translate_hardened.py::TestThinkTagExtraction` cover it, but in production it is unreachable.

**Impact:** Local R1 / DeepSeek-style models that emit `<think>...</think>` inline get the raw tags shipped to Claude Code as text. The "best-effort" comment in the streaming path makes this worse: the streaming codepath emits a `thinking_delta` for `reasoning_content` regardless of any flag, while the non-streaming path does nothing for `<think>` tags. **Inconsistent behavior between streaming and non-streaming for the same model.**

**Fix:** Either (a) wire it to a per-route field — e.g. `route.get("extract_think_tags", False)` populated from config — and call `openai_to_anthropic(data, effective_model, extract_think_tags=route.get("extract_think_tags", False))`, or (b) delete the parameter, the implementation, and the tests, and document that `<think>` tags are passed through verbatim.

---

### H3. Streaming `thinking_delta` collides with `index: 0` text block [FIXED]

**Fix:** Streaming `reasoning_content` is now intentionally skipped in
`openai_stream_to_anthropic_stream()`. The non-streaming
(`openai_to_anthropic`) path remains the authoritative source for
reasoning. This avoids the protocol violation (thinking_delta against a
text block) entirely. Verified by
`TestStreamingThinkingSkipped::test_reasoning_content_not_emitted_in_streaming`
and the updated `test_translate_hardened.py::test_stream_reasoning_content_delta`.
**File:** `src/uam/translate.py:312-326`
**Severity:** HIGH

The new streaming reasoning code emits:
```python
parts.append(_sse_event("content_block_delta", {
    "index": 0,
    "delta": {"type": "thinking_delta", "thinking": ...},
}))
```

But `make_anthropic_stream_start` (line 387) opens index 0 as a `text` content block:
```python
"content_block": {"type": "text", "text": ""},
```

The Anthropic streaming protocol requires that `thinking_delta` events only target a content block whose `content_block_start` declared `type: thinking`. Sending a `thinking_delta` to a `text` block is a protocol violation. The code's own NOTE comment admits this: "Most clients tolerate this; the non-streaming path is the authoritative source." That is not a fix — it's a TODO.

**Impact:** Strict Anthropic SDK clients (including newer Claude Code versions) may error or render reasoning text as garbled output. Inconsistent with non-streaming output (H2).

**Fix:** Either emit a proper `content_block_stop` for index 0 followed by a new `content_block_start` with `type: thinking` at index 1, then a `content_block_stop` and re-`content_block_start` for text at index 2. Or omit the streaming reasoning entirely and document it.

---

### H4. Multiple `tool_result` blocks: non-tool-result content dropped if `len(tool_results) == 1` [FIXED]

**Fix:** The expansion path in `anthropic_to_openai()` now triggers when
`tool_results and (len(tool_results) > 1 or non_tool)` — i.e. any
tool_result with companion text is expanded into a separate
non-tool-result message followed by tool messages. Verified by
`TestSingleToolResultPreservesText`.
**File:** `src/uam/translate.py:39-65, 96-166`
**Severity:** HIGH

The expansion path at line 45 only triggers when `len(tool_results) > 1`:
```python
if len(tool_results) > 1:
    # Expand: non-tool-result content first, then each tool_result
    ...
    continue
messages.append(_convert_message_to_openai(msg))
```

When `len(tool_results) == 1`, control falls through to `_convert_message_to_openai(msg)` which (line 151-152) returns ONLY the first tool_result and discards everything else:
```python
if tool_results:
    return tool_results[0]
```

So a message containing `[{type:text,text:"see result"}, {type:tool_result,...}]` returns just the tool message — the text block is silently lost. This is a pre-existing behavior, but the hardening commit explicitly added new block-handling logic without fixing it.

**Impact:** Tool-result follow-ups lose narrative context; downstream models may produce confused responses.

**Fix:** Treat the single-tool-result case the same way as the multi-result case: emit non-tool-result content first as a separate message, then the tool message.

---

## MEDIUM

### M1. Leftover `print()` in `discover_local` after logging migration [FIXED]
**File:** `src/uam/discovery/local.py:80`
**Severity:** MEDIUM

**Fix:** Replaced `print(f"  [local:{label}] {route_key}")` with
`logger.info(f"[local:{label}] {route_key}")` to match the `/api/tags`
branch. Verified by `TestLocalDiscoveryNoPrint` (capsys + caplog).

Phase 1 migrated all discovery `print()` calls to `logger.info(...)`, except this one in the OpenAI-format branch:
```python
print(f"  [local:{label}] {route_key}")
```
Line 68 (the `/api/tags` Ollama branch) was correctly updated to `logger.info(...)`. Line 80 was missed.

**Impact:** Inconsistent log destinations (stdout vs `~/.uam/uam.log`); breaks structured-log expectations; clutters Claude Code's hook output.

**Fix:** Replace with `logger.info(f"[local:{label}] {route_key}")`.

---

### M2. `infer_capabilities` missing families (gemma, phi, command, llava, dbrx, yi) [FIXED]

**Fix:** `infer_capabilities()` now recognizes `gemma`, `phi`, `command`,
`dbrx`, `yi`, `codellama`, `codestral`, `llava` (vision), and `gemma-3`
(vision). Verified by `TestInferCapabilitiesGaps`.

---

### M2-orig. `infer_capabilities` two-segment prefix corner cases (historical analysis)
**File:** `src/uam/state.py:149-183`
**Severity:** MEDIUM

The function strips `backend:` then `org/`:
```python
if ":" in name:
    name = name.split(":", 1)[1]
if "/" in name:
    name = name.rsplit("/", 1)[-1]
```

`rsplit("/", 1)[-1]` keeps only the last `/`-segment. For runpod IDs the route key looks like `runpod:my-pod/meta-llama/Llama-3.1-70B`. After `:` strip: `my-pod/meta-llama/Llama-3.1-70B`. After `rsplit("/", 1)[-1]`: `Llama-3.1-70B`. Lowercased: `llama-3.1-70b`. `name.startswith("llama")` → True. OK for `llama`.

But test_infer_qwen uses `local:qwen3-coder-next:latest`. After `:` split-1: `qwen3-coder-next:latest`. There is no `/`, so it stays. Lowercased: `qwen3-coder-next:latest`. `startswith("qwen")` → True. OK.

Now consider `local:my-server/qwen2.5`. After `:` strip: `my-server/qwen2.5`. After `rsplit("/", 1)[-1]`: `qwen2.5`. OK.

But: `runpod:account-name/llava-hf/llava-1.5-7b-hf`. After `:` strip: `account-name/llava-hf/llava-1.5-7b-hf`. After `rsplit("/", 1)[-1]`: `llava-1.5-7b-hf`. `startswith("llama")` → False, `startswith("llava")` → not in any branch → falls to `["streaming"]`. The model is vision-capable but capabilities will be `["streaming"]` only.

More concretely: any model not starting with one of {claude, gpt-4, gpt-5, gemini, deepseek, qwen, llama, mistral, mixtral} silently degrades to `["streaming"]`. There's no fallback for `phi`, `gemma`, `command-r`, `mixtral` (substring fine), `yi`, etc., even though `_extract_alias` knows about them.

**Impact:** Models like `gemma`, `phi`, `command-r`, `dbrx`, `falcon`, `yi` get capability `["streaming"]` only — no `tools`. Claude Code may then refuse to assign them as a swap target or refuse tool-use turns, breaking the flow.

**Fix:** Add the missing families, or share the families list with `_extract_alias`. Also consider a `vision`-aware list (`llava`, `gemma-3`, `gpt-4o-mini`, etc.).

---

### M3. `infer_capabilities` `gpt-3.5` falls through to streaming-only [FIXED]

**Fix:** Added explicit `gpt-3` / `gpt3` branch returning
`["tools", "streaming"]`. Verified by
`TestInferCapabilitiesGaps::test_gpt_35_has_tools`.

---

### M3-orig. (historical analysis)
**File:** `src/uam/state.py:165`
**Severity:** MEDIUM

```python
if name.startswith("gpt-4") or name.startswith("gpt-5") or name.startswith("gpt4") or name.startswith("gpt5"):
```

But the input is post-`rsplit("/", 1)[-1]`, so `openrouter:openai/gpt-5-turbo` → `gpt-5-turbo` → `startswith("gpt-5")` → True. Good. But `openrouter:openai/gpt-4o` → `gpt-4o` → True. Good. However `openrouter:openai/gpt-3.5-turbo` → `gpt-3.5-turbo` → no match → `["streaming"]`. GPT-3.5 supports tools and streaming. Minor regression.

**Fix:** Replace with `name.startswith("gpt-")` and `name[4:5].isdigit()` and the digit `>= 3`, or simpler: a regex / explicit list.

---

### M4. `system` text-block dict access uses `b["text"]` instead of `b.get` [FIXED]

**Fix:** `anthropic_to_openai()` now uses `b.get("text", "")` for system
text blocks. Verified by `TestSystemBlockNoText`.

---

### M4-orig. (historical analysis)
**File:** `src/uam/translate.py:29`
**Severity:** MEDIUM

```python
for b in system:
    if b.get("type") == "text":
        parts.append(b["text"])
```

If a malformed system block is `{"type": "text"}` with no `text` key (Anthropic SDK can produce this from cached prompt blocks), this raises `KeyError`, which now gets caught at `_proxy_with_translation`'s try/except and turned into a 502 `translation_error`. Pre-Phase-3 the same bug existed silently. Hardening commit added the try/except wrapper that masks this.

**Fix:** Use `parts.append(b.get("text", ""))`.

---

### M5. `write_env_file` writes empty `SUPPORTED_CAPABILITIES=""` [FIXED]

**Fix:** `write_env_file()` now falls back to `infer_capabilities(default)`
when `model_entry.get("capabilities")` is missing or empty. Verified by
`TestEnvFileEmptyCapsFallback`.

---

### M5-orig. (historical analysis)
**File:** `src/uam/state.py:223-229`
**Severity:** MEDIUM

```python
capabilities = model_entry.get("capabilities", [])
caps_str = ",".join(capabilities)
...
lines.append(
    f'export ANTHROPIC_DEFAULT_SONNET_MODEL_SUPPORTED_CAPABILITIES="{caps_str}"'
)
```

If `model_entry` exists and is enabled but lacks the `capabilities` key (e.g. user manually edited models.json, or a pre-Phase-1 state file), `caps_str` is `""` and the env exports `ANTHROPIC_DEFAULT_SONNET_MODEL_SUPPORTED_CAPABILITIES=""`. Claude Code interprets the empty string as "no capabilities supported," disabling tool use, streaming, and thinking for the swap target.

**Fix:** When `capabilities` is missing or empty, fall back to `infer_capabilities(default)` and use that.

---

### M6. `_resolve_default_swap` "default disabled" branch silently falls through [FIXED]

**Fix:** When the default model is disabled, `_resolve_default_swap()`
now logs `logger.warning("Default model %s is disabled — falling
through to %s", default, model)` so the user has observability.
Verified by `TestDefaultDisabledLogged`.

---

### M6-orig. (historical analysis)
**File:** `src/uam/proxy.py:107-115`
**Severity:** MEDIUM

```python
if default and model.startswith("claude-") and not default.startswith("claude-"):
    if not is_enabled(default, state):
        # Default is disabled — fall through to normal resolution
        pass
    else:
        route = router.resolve(default)
        if route:
            return route, default
```

If the user has set a default but disabled it via `/model`, the proxy silently falls through to the requested model — without any log line, status code, or warning. The user expects "my default is qwen, claude requests should hit qwen" but they're getting Claude. There is no observability that the swap was bypassed.

**Fix:** Add `logger.warning(f"Default {default} is disabled — falling through to {model}")` before the fall-through, and consider returning a 503 instead so the user notices.

---

## LOW

### L1. Logger imports placed after `from ... import` block [FIXED]

**Fix:** `proxy.py` now declares `logger = logging.getLogger("uam.proxy")`
after all imports. Verified by `TestLoggerImportOrder`.

---

### L1-orig. (historical analysis)
**File:** `src/uam/proxy.py:9`, `src/uam/router.py:9`, `src/uam/translate.py:8`
**Severity:** LOW

```python
import logging
...

logger = logging.getLogger("uam.proxy")

from uam.router import ModelRouter
```

`logger = ...` appears between two import statements. Style nit; isort/ruff will flag this.

**Fix:** Move all `from uam...` imports above the `logger = ...` declaration.

---

### L2. `_invalidate_state_cache` is module-state racy under concurrent requests
**File:** `src/uam/proxy.py:36-39`
**Severity:** LOW

`_state_cache_time` is mutated from `_invalidate_state_cache`, `_get_state`, and `handle_post_state` without a lock. Under aiohttp's single-threaded event loop this is benign because there are no awaits between read and write of `_state_cache_time` in the critical sections, but it still violates the principle of least surprise and will break if anyone wraps these in `run_in_executor`.

**Fix:** Document the assumption, or wrap in `asyncio.Lock`.

---

### L3. `redact_headers` is defined but never imported anywhere
**File:** `src/uam/log.py:43-52`
**Severity:** LOW

```
$ rg redact_headers src/ tests/
src/uam/log.py:43:def redact_headers(headers: dict) -> dict:
tests/test_logging.py:...
```

It's tested but not used anywhere in proxy/router/discovery. The point of Phase 1 was supposedly "redact headers when logging request errors," but `logger.error(f"Discovery error: {result}")` and friends never call `redact_headers`. Either dead code or a missed integration.

**Fix:** Either wire it into the upstream POST error logs (add a `logger.debug(f"Upstream {target_url} headers={redact_headers(headers)}")` on failure), or remove.

---

### L4. `anthropic_to_openai` "Strip thinking parameter" log line runs but the parameter is never actually present in `result`
**File:** `src/uam/translate.py:89-91`
**Severity:** LOW

```python
if "thinking" in payload:
    logger.debug("Stripped thinking parameter from translated request")
```

The code correctly never copies `thinking` into `result`, so this is "stripping" something that was never there in the first place. The log is fine, but it's confusing because there's no actual `del` or `pop` — readers may think `thinking` is somehow leaking through.

**Fix:** Add a comment clarifying that `thinking` is never copied across, or rename log to "Ignoring thinking parameter."

---

### L5. Translation streaming: empty `delta.content == ""` falls through but `if delta["content"]` is falsy
**File:** `src/uam/translate.py:328`
**Severity:** LOW

```python
if "content" in delta and delta["content"]:
```

vLLM occasionally sends `delta: {"content": ""}` as a heartbeat. The current code skips these correctly. But the comment in `test_stream_reasoning_content_empty` (`test_translate_hardened.py:371`) shows the proxy returns `None` for empty reasoning_content — verifying behavior parity with text. Good. Just noting: if a backend ever sends `content: " "` (space), it gets emitted as a delta — fine. No bug, just observation.

---

### L6. `test_proxy.py::test_retry_headers_429_with_retry_after` uses lowercase keys, hiding C1
**File:** `tests/test_proxy.py:1248-1253`
**Severity:** LOW (test gap, but also evidence for C1)

```python
upstream = {"retry-after-ms": "5000", "retry-after": "5"}
headers = _retry_headers(429, upstream)
```

This is the only retry-after test and it uses lowercase keys, exactly the case that happens to work. Add an explicit test using `"Retry-After"` (canonical) and a `CIMultiDict` to catch the regression in C1.

---

### L7. `_proxy_with_translation` does not forward `_forward_response_headers` [FIXED]

**Fix:** The non-streaming translation path now calls
`_forward_response_headers(upstream, resp)` after building the
JSON response, matching the native Anthropic path. `request-id` and
`anthropic-ratelimit-*` headers from upstream are now visible to
Claude Code on translated routes.

---

### L7-orig. (historical analysis)
**File:** `src/uam/proxy.py:219-309`
**Severity:** LOW (pre-existing, not introduced in these phases)

The translation path never calls `_forward_response_headers(upstream, resp)`, so `request-id`, `anthropic-ratelimit-*` etc. are never forwarded back to Claude Code from openrouter/runpod backends. The native path does forward them. Inconsistent. Pre-existing — flag for backlog.

---

### L8. `discover_local` swallows JSON decode and HTTP errors silently
**File:** `src/uam/discovery/local.py:48-83`
**Severity:** LOW

```python
try:
    async with session.get(...) as resp:
        data = await resp.json()
    ...
except Exception:
    continue
```

When a probed port returns HTML or 500, the exception is silently swallowed. Phase 1 added logging but did not log probe failures. Good when probing 6 ports for 3 free ones; bad when the user thinks their server is up but it's misconfigured.

**Fix:** `logger.debug(f"[local] probe {url}{path} failed: {e}")` so users can find issues with `UAM_LOG_LEVEL=DEBUG`.

---

### L9. `discover_local` retries `/v1/models` then `/api/tags` on the same URL even after the first one returns 200 with `{}`
**File:** `src/uam/discovery/local.py:48-83`
**Severity:** LOW

If a server responds 200 to `/v1/models` with empty `data: []`, the loop's `break` (line 81) still fires inside the `else:` branch, but only after entering the for-m loop with zero iterations. Combined with the bug that `print(...)` lives at line 80 inside the for-loop, the print only triggers when there's at least one model. OK for now but confusing.

---

## Test Coverage Gaps

- **TG1.** No test exercises the `_proxy_with_translation` streaming success path (the meat of Phase 5). The streaming reasoning_content delta has no end-to-end test; only a unit test of `openai_stream_to_anthropic_stream`. The collision in H3 is invisible to the suite.
- **TG2.** No test verifies that `extract_think_tags` is reachable from a real `/v1/messages` request (because it isn't — H2).
- **TG3.** No test verifies that per-backend timeouts are actually applied to upstream calls (because they aren't — H1).
- **TG4.** No test for `write_env_file` when `capabilities` key is absent (M5).
- **TG5.** No test for `_resolve_default_swap` when default is disabled (M6).
- **TG6.** No test for `_retry_headers` with a `CIMultiDict` containing `Retry-After` (canonical casing) — L6 / C1.
- **TG7.** No test for `infer_capabilities` for gemma, phi, command, llava, gpt-3.5 (M2/M3).
- **TG8.** No test that `redact_headers` is invoked anywhere from production code paths (L3).

---

## Documentation Drift

- **D1.** `CLAUDE.md` lists "per-backend timeouts" as a Phase 1 deliverable. Code does not implement this (H1).
- **D2.** Phase 3 commit message says "translation hardening — unknown blocks, images, thinking." `thinking` block stripping in requests works; `<think>` tag extraction in responses is implemented but unreachable (H2).
- **D3.** `_retry_headers` docstring claims "We don't retry inside the proxy — instead we propagate retry-after*". The propagation is broken for canonical-case headers (C1).
- **D4.** `make_anthropic_stream_start` docstring says it opens a text content block at index 0. The new streaming reasoning code emits thinking_delta at the same index without re-opening the block (H3).

---

## Summary

| Severity | Count |
|----------|-------|
| CRITICAL | 1     |
| HIGH     | 4     |
| MEDIUM   | 6     |
| LOW      | 9     |
| Test gap | 8     |
| Doc drift| 4     |

**Top 3 fixes to make first:**

1. **C1** — pass `upstream.headers` (CIMultiDict) into `_retry_headers` directly; add a regression test using `Retry-After`.
2. **H1** — either wire `get_backend_timeout()` into every `session.post(..., timeout=...)` call in `proxy.py`, or delete it and update CLAUDE.md.
3. **H2** — wire `extract_think_tags` to a per-route config flag, or delete the parameter and the tests; document the chosen behavior.
