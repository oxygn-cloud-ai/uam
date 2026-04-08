# uam Architecture

**uam** (Use Any Model) is a multi-backend model router for Claude Code. It runs as a transparent HTTP proxy on `localhost:5100`, sitting between Claude Code and AI backends -- Anthropic, RunPod, OpenRouter, and local model servers. It swaps the default model, translates between Anthropic and OpenAI API formats, enforces model on/off state, and auto-discovers models from all configured backends.

Version: 0.4.19

---

## 1. Design Philosophy

**Transparent proxy.** Claude Code does not know it is being rerouted. The proxy presents the Anthropic Messages API on all endpoints. Claude Code connects to `localhost:5100` via the `ANTHROPIC_BASE_URL` environment variable and interacts with the standard Anthropic protocol regardless of which backend actually serves the request.

**Security-first.** Configuration stores environment variable names, never API key values. Keys are resolved at runtime via `os.environ.get()`. No key values appear in logs, error messages, or state files.

**No CLI tools.** The entire user interface consists of two slash commands inside Claude Code: `/uam` for proxy lifecycle and `/model` for model management. There is no standalone CLI binary.

**Auto-discovery.** On startup, the proxy discovers models from all configured backends in parallel. RunPod pods are found via GraphQL, OpenRouter models via their API, and local servers by probing known ports. Anthropic models are hardcoded and always available.

**Format translation.** Anthropic and OpenAI Chat Completions API formats are structurally different. The proxy translates between them transparently so that any OpenAI-compatible backend (vLLM, Ollama, OpenRouter) can serve requests that Claude Code sends in Anthropic format.

---

## 2. System Overview

```
Claude Code
    |
    | ANTHROPIC_BASE_URL=http://127.0.0.1:5100
    v
+-------------------------------------------+
|           uam proxy (aiohttp)             |
|                                           |
|  +--------------+  +-------------------+  |
|  | Default Swap |->| Route Resolution  |  |
|  +--------------+  +-------------------+  |
|         |                    |             |
|         v                    v             |
|  +--------------+  +-------------------+  |
|  |  Anthropic   |  |   Translated      |  |
|  |  Passthrough |  |  (OpenAI compat)  |  |
|  +------+-------+  +--------+----------+  |
+---------+--------------------+-------------+
          |                    |
          v                    v
    api.anthropic.com    RunPod / OpenRouter / Local
```

### Hook Integration

```
Claude Code session starts
    |
    v
SessionStart hook (uam-autostart.py)
    |-- checks GET /health on 127.0.0.1:5100
    |-- if not running: spawns `python -m uam` in background
    |-- waits up to 5 seconds for health check to pass

User types "ask gemini what is X"
    |
    v
UserPromptSubmit hook (uam-ask-router.py)
    |-- reads JSON from stdin (Claude Code hook protocol)
    |-- pattern matches: ask <model> <query>
    |-- resolves alias via ~/.uam/models.json
    |-- checks model is enabled
    |-- POST to /v1/messages/ask (non-streaming)
    |-- prints response text to stdout for Claude to relay
```

---

## 3. Request Flow

### Anthropic-native path

This path handles requests where the resolved model maps to an Anthropic backend. No format translation is needed.

1. Claude Code sends `POST /v1/messages` with `model: claude-sonnet-4-6`.
2. **Default swap check.** The proxy loads model state (cached with 5-second TTL). If a non-Claude default is set and enabled, and the incoming model starts with `claude-`, the model is swapped to the default.
3. **Route resolution.** `ModelRouter.resolve()` looks up the model: direct match in the routes table, then Anthropic alias match (e.g., `claude-sonnet-4-6[1m]` maps to `claude-sonnet-4-6`), then fallback to the default backend.
4. **Forward to Anthropic.** The request is sent to `api.anthropic.com/v1/messages` with selective header forwarding (`anthropic-version`, `anthropic-beta`, `x-*`, `request-id`). The API key is sent as `X-Api-Key`.
5. **Response passthrough.** For streaming requests, raw chunks are forwarded directly. For non-streaming, the full response body is returned. No transformation occurs.

### Translation path (OpenAI-compatible backends)

This path handles RunPod, OpenRouter, and local backends that speak the OpenAI Chat Completions API.

1. Claude Code sends `POST /v1/messages` with any model.
2. **Default swap** resolves to a non-Anthropic backend.
3. **Request translation.** `anthropic_to_openai()` converts the payload:
   - Anthropic `system` (string or list of content blocks) becomes an OpenAI system message.
   - Message content blocks are converted: `text` blocks become string content, `tool_use` blocks become `tool_calls`, `tool_result` blocks become `tool` role messages.
   - Multiple `tool_result` blocks in one message are expanded into separate OpenAI tool messages.
   - Parameters are mapped: `max_tokens`, `temperature`, `top_p`, `stop_sequences` (to `stop`), `tools`.
4. **POST to backend.** The translated payload is sent to `{backend_url}/v1/chat/completions` with `Authorization: Bearer {key}`.
5. **Response conversion:**
   - **Non-streaming:** `openai_to_anthropic()` converts the full response. OpenAI `choices[0].message` becomes Anthropic content blocks. Finish reasons are mapped (`stop` to `end_turn`, `length` to `max_tokens`, `tool_calls` to `tool_use`). Usage stats are translated (`prompt_tokens` to `input_tokens`, `completion_tokens` to `output_tokens`).
   - **Streaming:** `openai_stream_to_anthropic_stream()` converts line-by-line. The proxy first emits `message_start` and `content_block_start` events. Then each OpenAI SSE chunk (`data: {...}`) is converted to Anthropic `content_block_delta` events. Raw byte chunks from the upstream are line-buffered (split on `\n` with a carryover buffer for partial lines). When `data: [DONE]` is received, the proxy emits `content_block_stop`, `message_delta` (with `stop_reason`), and `message_stop`.

### One-shot ask path

This path is triggered by the `UserPromptSubmit` hook, not by Claude Code's normal API calls.

1. The hook intercepts user input matching `ask <model> <query>`.
2. It resolves the model name through aliases and state, checks that the model is enabled, and verifies the proxy is running.
3. It sends `POST /v1/messages/ask` to the proxy. This endpoint does not apply default swap logic -- it routes to exactly the model requested.
4. The proxy resolves the route and forwards the request (with translation if needed), always non-streaming.
5. The hook extracts text content blocks from the Anthropic-format response and prints them to stdout.

---

## 4. Module Reference

### proxy.py -- HTTP handlers

The core of the proxy. Registers 8 route handlers across 10 logical endpoints on an `aiohttp.Application`.

Key responsibilities:
- **Default model swap.** `_resolve_default_swap()` checks if the incoming model is a Claude model and a non-Claude default is set and enabled. If so, it swaps the model before routing.
- **Translation dispatch.** `_needs_translation()` checks the route's backend. Anthropic routes pass through directly; all others go through format translation.
- **State caching.** Model state is cached in memory with a 5-second TTL (`_STATE_CACHE_TTL`) to avoid reading `models.json` from disk on every request. The cache is invalidated on state writes and refresh.
- **Header management.** `_build_upstream_headers()` constructs headers per backend type: Anthropic gets `X-Api-Key` + `anthropic-version`; others get `Authorization: Bearer`. `_forward_response_headers()` forwards `x-*`, `anthropic-*`, and `request-id` response headers.
- **JSON body validation.** All three body-reading endpoints (`handle_messages`, `handle_ask`, `handle_count_tokens`) validate JSON bodies with try/except, returning 400 with `"Invalid JSON body"` on malformed input.
- **Error wrapping.** `_make_anthropic_error()` wraps upstream HTTP errors in Anthropic error format (`{type: "error", error: {type, message}}`).

### router.py -- ModelRouter

Orchestrates model discovery and resolution. Holds all known routes and a shared `aiohttp.ClientSession`.

Key responsibilities:
- **Async discovery.** `discover()` runs all backend discovery functions in parallel with `asyncio.gather(*tasks, return_exceptions=True)`. Exceptions are logged but do not prevent other backends from completing.
- **3-step model resolution.** `resolve()` tries: (1) direct match in routes table, (2) Anthropic alias match (e.g., `[1m]` variants), (3) fallback to default backend configuration with the original model name.
- **Refresh.** `refresh()` clears all non-Anthropic routes, re-runs discovery, and re-syncs state.
- **Session management.** A single `aiohttp.ClientSession` with 600-second total timeout and 10-second connect timeout is shared across all discovery and proxy requests.

### translate.py -- Format translation

Stateless functions that convert between Anthropic Messages API and OpenAI Chat Completions API formats.

Key responsibilities:
- **`anthropic_to_openai()`**: Converts system prompts (string or list of content blocks), messages (with tool_use/tool_result expansion), parameters, and tool definitions. Multiple `tool_result` blocks in a single message are expanded into separate OpenAI `tool` role messages.
- **`openai_to_anthropic()`**: Constructs Anthropic content blocks from OpenAI response messages. Maps finish reasons. Translates usage statistics. Generates synthetic message IDs with `msg_` prefix when the upstream does not provide one.
- **`openai_stream_to_anthropic_stream()`**: Converts a single OpenAI SSE line to Anthropic SSE events. Handles text deltas, tool call deltas (both start and argument chunks), and `[DONE]` termination. Returns `None` for lines that should be skipped (empty lines, non-data lines).
- **`make_anthropic_stream_start()`**: Generates the initial `message_start` + `content_block_start` SSE events that Anthropic streams begin with.
- **Edge cases handled:** Multiple tool_results in one message, missing tool call IDs (synthetic `toolu_` prefix), JSON decode errors in tool arguments (falls back to empty dict), system prompt as list of content blocks.

### state.py -- Model state management

Manages `~/.uam/models.json` -- the persistent record of which models are on/off, the default model, and friendly aliases.

Key responsibilities:
- **State structure.** `{default: str, aliases: {str: str}, models: {str: {enabled: bool}}}`.
- **Auto-alias generation.** `auto_aliases()` extracts family names from model IDs using a priority-ordered list of known families (codellama, gemini, claude, llama, mistral, qwen, etc.). Aliases are only assigned when unambiguous (one model per family). For ambiguous cases, `_extract_specific_alias()` tries variant names (sonnet, opus, haiku, flash, pro) or family+version (e.g., `gemini2.0`).
- **State sync.** `sync_state_with_routes()` merges newly discovered models into existing state. New models are added as enabled. Existing models are preserved (including disabled ones -- they may reappear on refresh). Auto-aliases are regenerated, then user-set aliases are overlaid. If no default is set, a Claude model is preferred.

### config.py -- Configuration

Loads and parses `~/.uam/config.json`.

Key responsibilities:
- **`get_config()`**: Reads config from disk. Falls back to `default_config()` if the file does not exist. Pure read — does not write.
- **`ensure_config_exists()`**: First-run bootstrap — materializes `~/.uam/config.json` with `default_config()` content if missing. Idempotent (existing user config is never overwritten). Called from `__main__.main()` on every proxy startup so users always have a real file to edit.
- **`add_local_server(url, api_format)`**: Re-runnable helper that appends a remote local-backend server to `local.servers` in `~/.uam/config.json`. Normalizes the URL (prepends `http://` if no scheme, strips trailing slash), rejects non-`http(s)` schemes, dedupes against existing entries, atomically writes the file via temp + `os.replace()`. Used by the `POST /config/local-servers` endpoint and the `/uam add-server` slash command.
- **`resolve_key()`**: Takes an environment variable name, returns its value via `os.environ.get()`. Never logs or stores the resolved value.
- **`parse_listen()`**: Parses the `listen` field (format: `host:port`) into a tuple. Defaults to `127.0.0.1:5100`.
- **Default config:** Includes Anthropic (api.anthropic.com), RunPod (empty accounts), OpenRouter (openrouter.ai/api), and local (probe ports 11434, 8000, 8080, 2242, 5000, 3000 covering Ollama, vLLM, Aphrodite, TabbyAPI, and TGI; `"servers": []` for explicit server URLs).

### discovery/anthropic.py -- Anthropic models

Hardcoded model registration. Always runs, requires no network calls.

- Registers 5 models: `claude-opus-4-6`, `claude-opus-4-6-20250522`, `claude-sonnet-4-6`, `claude-sonnet-4-6-20250514`, `claude-haiku-4-5-20251001`.
- Registers 2 aliases for `[1m]` context variants: `claude-opus-4-6[1m]` and `claude-sonnet-4-6[1m]`, which map to their base models.

### discovery/runpod.py -- RunPod discovery

Discovers models running on RunPod vLLM pods via the RunPod GraphQL API.

- Iterates over configured RunPod accounts.
- Queries the GraphQL endpoint for all pods, filters to RUNNING pods with port 8000 exposed. Port matching uses exact token matching via `re.split(r'[\s,/]+', ports)` to avoid substring false positives (e.g., "18000" no longer matches "8000").
- Parses pod environment variables to extract `VLLM_API_KEY` (with `$RUNPOD_POD_ID` template substitution).
- Probes each pod's `/v1/models` endpoint to discover served models.
- Route keys follow the format: `runpod:{pod-name}/{model-id}`.

### discovery/openrouter.py -- OpenRouter discovery

Fetches the full model catalog from the OpenRouter API.

- `GET /v1/models` with bearer auth.
- Each model is registered with route key `openrouter:{model-id}`.
- 15-second timeout per request.

### discovery/local.py -- Local server discovery

Probes localhost ports and explicit server URLs for running model servers (Ollama, vLLM, etc.).

- Probes configured ports (default: 11434, 8000, 8080) and any explicit server URLs.
- Tries `/v1/models` first (OpenAI-compatible), then `/api/tags` (Ollama native).
- 5-second timeout per probe. Failures are silently skipped.
- Route keys follow the format: `local:{model-id}`.

---

## 5. Proxy Endpoints

| Method | Path                       | Purpose                                                    |
|--------|----------------------------|------------------------------------------------------------|
| POST   | `/v1/messages`             | Main messages API with default swap + format translation   |
| POST   | `/v1/messages/count_tokens`| Token counting (estimates ~4 chars/token for non-Anthropic)|
| POST   | `/v1/messages/ask`         | One-shot query from hook (no default swap, non-streaming)  |
| GET    | `/v1/models`               | List all models with enabled status and default            |
| POST   | `/refresh`                 | Trigger re-discovery of all backends                       |
| GET    | `/health`                  | Health check (status, model count, current default)        |
| GET    | `/state`                   | Get model state (default, aliases, models)                 |
| POST   | `/state`                   | Update model state (partial merge of default/aliases/models)|
| POST   | `/config/local-servers`    | Add a remote local backend to `~/.uam/config.json` (re-runnable; URL normalized + deduped). Caller must POST `/refresh` afterwards. |

---

## 6. State Files

All state is stored under `~/.uam/`.

### ~/.uam/config.json

Backend configuration. Created by the `/uam setup` command. Stores connection details and environment variable names for API keys.

```json
{
  "listen": "127.0.0.1:5100",
  "anthropic": {
    "url": "https://api.anthropic.com",
    "api_key_env": "ANTHROPIC_API_KEY_REAL"
  },
  "runpod": {
    "accounts": {
      "my-account": {
        "api_key_env": "RUNPOD_API_KEY"
      }
    }
  },
  "openrouter": {
    "url": "https://openrouter.ai/api",
    "api_key_env": "OPENROUTER_API_KEY"
  },
  "local": {
    "probe_ports": [11434, 8000, 8080, 2242, 5000, 3000],
    "servers": []
  },
  "default_backend": "anthropic"
}
```

### ~/.uam/models.json

Model state. Auto-managed by the proxy on startup and refresh. Contains enabled flags, the default model, and aliases. Can be modified via `POST /state` or the `/model` slash command.

```json
{
  "default": "claude-sonnet-4-6",
  "aliases": {
    "claude": "claude-sonnet-4-6",
    "opus": "claude-opus-4-6",
    "sonnet": "claude-sonnet-4-6",
    "haiku": "claude-haiku-4-5-20251001",
    "gemini": "openrouter:google/gemini-2.0-flash"
  },
  "models": {
    "claude-sonnet-4-6": {"enabled": true},
    "claude-opus-4-6": {"enabled": true},
    "openrouter:google/gemini-2.0-flash": {"enabled": true}
  }
}
```

### ~/.uam/uam.pid

Contains the PID of the running proxy process. Written on startup in the `on_startup` callback. Deleted on shutdown in the `on_shutdown` callback. Used by the `/uam` command to check proxy status and send stop signals.

---

## 7. Security Model

- **Config stores environment variable names, never API key values.** The `api_key_env` fields contain strings like `"OPENROUTER_API_KEY"`, not the actual key.
- **Keys are resolved at runtime.** `config.resolve_key()` calls `os.environ.get()` and returns the value. The resolved value is held only in memory for the duration of the request.
- **Proxy binds to localhost only.** The default listen address is `127.0.0.1:5100`. The proxy is not accessible from other machines on the network.
- **No key values in logs or error messages.** Error wrapping in `_make_anthropic_error()` passes through upstream error text but the proxy itself never logs key values.
- **Hook scripts use stdlib only.** Both hooks (`uam-autostart.py` and `uam-ask-router.py`) use only Python standard library modules (`urllib.request`, `json`, `subprocess`, `pathlib`). They have no dependency on `aiohttp` or any pip-installed package.

---

## 8. Hook System

Hooks are Python scripts that Claude Code runs at specific lifecycle points. They are installed to the project's `.claude/hooks/` directory and registered in `.claude/settings.json`.

### SessionStart hook -- uam-autostart.py

**Trigger:** Every time a Claude Code session starts.

**Behavior:**
1. Sends `GET /health` to `127.0.0.1:5100` with a 2-second timeout.
2. If the proxy responds, exits immediately (already running).
3. If not running, spawns `python -m uam` as a detached background process with stdout/stderr redirected to `~/.uam/uam.log`.
4. Polls the health endpoint once per second for up to 5 seconds.
5. Exits silently whether or not the proxy started successfully.

### UserPromptSubmit hook -- uam-ask-router.py

**Trigger:** Every time the user submits a prompt in Claude Code.

**Behavior:**
1. Reads JSON from stdin (Claude Code hook protocol: `{prompt: "..."}`)
2. Matches the prompt against the pattern `ask <model> <query>` (case-insensitive, dotall).
3. If no match, exits immediately (normal prompt flow continues).
4. Resolves `<model>` through aliases in `~/.uam/models.json`, then checks direct model IDs.
5. Verifies the model is enabled. If disabled, prints a message suggesting `/model` and exits.
6. Checks the proxy is running via the health endpoint.
7. Sends `POST /v1/messages/ask` with the query as a single user message (max_tokens: 4096, non-streaming).
8. Extracts text content blocks from the response and prints `[model]: response text` to stdout.

### Design constraints

- Both hooks use only Python stdlib -- no pip dependencies. This ensures they work in any environment where Python is available, without requiring `pip install uam` to have completed.
- Hooks communicate with the proxy over HTTP on localhost. They do not import any `uam` modules.
- Hook errors are silent by design. A failing hook must not block the Claude Code session.

---

## 9. Error Handling

### Proxy error responses

All errors returned by the proxy follow the Anthropic error format:

```json
{
  "type": "error",
  "error": {
    "type": "<error_type>",
    "message": "<description>"
  }
}
```

### Error types

| Condition              | HTTP Status | Error Type              | Source                    |
|------------------------|-------------|-------------------------|---------------------------|
| Unknown model          | 400         | `invalid_request_error` | `handle_messages`         |
| Disabled model (ask)   | 403         | `model_disabled`        | `handle_ask`              |
| Model not found (ask)  | 404         | `model_not_found`       | `handle_ask`              |
| Backend unreachable    | 502         | `proxy_error`           | All proxy handlers        |
| Upstream HTTP error    | Varies      | `api_error`             | `_make_anthropic_error`   |
| Invalid JSON body      | 400         | `invalid_request_error` | `handle_messages` / `handle_ask` / `handle_count_tokens` |
| Invalid JSON (state)   | 400         | `invalid_request_error` | `handle_post_state`       |

### Resilience patterns

- **Discovery failures are non-fatal.** `asyncio.gather(*tasks, return_exceptions=True)` catches per-backend exceptions. If RunPod discovery fails, OpenRouter and local discovery still complete. The proxy starts with whatever models were found.
- **Partial discovery.** The `--skip-discovery` flag starts the proxy with only Anthropic models. Useful when backends are unavailable or discovery is slow.
- **JSON decode fallback.** When upstream error bodies are not valid JSON, `_make_anthropic_error()` decodes the raw bytes as UTF-8 and includes them in the error message.
- **Token count estimation.** Non-Anthropic backends do not support `/v1/messages/count_tokens`. The proxy returns a rough estimate of ~4 characters per token rather than failing.
- **State file resilience.** `load_state()` catches `JSONDecodeError` and `OSError`, returning clean defaults. A corrupted `models.json` does not crash the proxy.

---

## 10. Test Suite

The project has 242 tests with 99% code coverage, run via `pytest`.

- **Framework:** pytest with aioresponses for mocking async HTTP calls and hypothesis for property-based testing of format translation edge cases.
- **Coverage:** All modules under `src/uam/` are tested, including proxy handlers, router resolution, discovery backends, state management, config loading, and format translation.
- **Run:** `pytest` from the project root. Configuration is in `pyproject.toml`.
