# uam — Use Any Model with Claude Code

> **A transparent proxy that lets Claude Code use any AI model** — Anthropic, OpenRouter, RunPod, Ollama, vLLM, llama.cpp, and more — without changing your workflow.

[![Version](https://img.shields.io/badge/version-0.4.20-blue)](https://github.com/oxygn-cloud-ai/uam/releases)
[![Tests](https://img.shields.io/badge/tests-418_passing-brightgreen)](#contributing)
[![License](https://img.shields.io/badge/license-MIT-green)](LICENSE)
[![Python](https://img.shields.io/badge/python-3.11+-blue)](https://www.python.org/)

---

## Why uam?

Claude Code is excellent — but it's locked to Anthropic's models by default. uam unlocks it.

With uam installed, your `/model` picker shows **every model from every backend you've configured**. Pick GPT-5, Gemini, Qwen3, Llama, DeepSeek, or your local Ollama instance — Claude Code uses it the same way it uses Claude. Same tools, same file edits, same workflow.

**No new CLI to learn. No new config UI. No web dashboard. Just slash commands inside Claude Code.**

---

## Table of Contents

- [How It Works](#how-it-works)
- [Features](#features)
- [Supported Backends](#supported-backends)
- [Requirements](#requirements)
- [Quick Start](#quick-start)
- [Installation](#installation)
- [Usage](#usage)
- [Backend Setup Guides](#backend-setup-guides)
- [Configuration Reference](#configuration-reference)
- [Security Model](#security-model)
- [Troubleshooting](#troubleshooting)
- [Logging and Debugging](#logging-and-debugging)
- [Uninstalling](#uninstalling)
- [Architecture](#architecture)
- [Contributing](#contributing)
- [Reporting Issues](#reporting-issues)
- [License](#license)

---

## How It Works

```
You type a question in Claude Code
        │
        ▼
Claude Code sends API request to ANTHROPIC_BASE_URL
        │  (set to http://127.0.0.1:5100 during /uam setup)
        ▼
uam proxy receives the request
        │
        ▼
Looks at your default model setting:
   ├─ "claude-sonnet-4-6"        → forward to Anthropic (passthrough)
   ├─ "local:qwen3-coder..."     → translate + forward to Ollama
   ├─ "openrouter:gemini..."     → translate + forward to OpenRouter
   └─ "runpod:my-pod/llama..."   → translate + forward to your RunPod GPU
        │
        ▼
Backend generates a response
        │
        ▼
uam translates the response back to Anthropic format
        │
        ▼
Claude Code displays it like normal
```

You never see the proxy. You never edit JSON files manually. Everything is managed via two slash commands inside Claude Code.

---

## Features

**Model routing**
- Swap the default AI powering Claude Code to any supported model
- Multi-backend: Anthropic, RunPod vLLM, OpenRouter (100+ models), local servers
- Auto-discovery from every configured backend
- Auto-generated short aliases (`gemini`, `qwen`, `opus`, `llama`)
- Toggle individual models on/off

**Anthropic ↔ OpenAI translation**
- Transparent format translation in both directions
- Streaming SSE conversion with bounded buffers
- Tool calling, system prompts, multi-turn conversations
- Extended thinking blocks (where backend supports them)
- Image content fallback for non-vision models
- Native Anthropic API passthrough for Ollama 0.14+ and llama.cpp (zero translation overhead)

**One-shot queries**
- Type `ask gemini explain this` in normal conversation to query any model without switching default
- Hook intercepts the pattern, routes the query, returns the response inline

**Production-ready**
- 366 tests, ~99% branch coverage (pytest + hypothesis + aioresponses)
- Structured logging with API key redaction
- Per-backend timeouts (Anthropic 600s, cloud 300s, local 120s)
- Atomic state file writes (no corruption on SIGTERM)
- Retry signaling via `x-should-retry` headers (cooperates with Claude Code's built-in retry loop)
- Bounded SSE buffer prevents OOM from misbehaving upstreams
- Host header validation (prevents DNS rebinding from browsers)
- Shell-injection-safe managed env file (`shlex.quote()` everywhere)
- Race-condition-free state updates (`asyncio.Lock`)
- All sensitive operations stripped of API keys before logging

**Security-first**
- Config stores **environment variable names**, never API key values
- Keys resolved at runtime via `os.environ.get()` — never written to disk
- Proxy binds to `127.0.0.1` only (not network-accessible)
- Hooks use stdlib only (no pip dependencies that could leak data)

**Stay-lean philosophy**
- No web UI, no dashboard, no cloud service
- ~1700 lines of Python total
- Zero config required for basic Anthropic + localhost setup
- Two slash commands and a hook — that's the whole interface

---

## Supported Backends

| Prefix | Backend | Discovery | Example |
|--------|---------|-----------|---------|
| `claude-*` | Anthropic API | Always available (hardcoded) | `claude-sonnet-4-6` |
| `runpod:<pod>/<model>` | RunPod vLLM pods | GraphQL API discovery | `runpod:my-pod/llama-3.1-70b` |
| `openrouter:<org>/<model>` | OpenRouter | API model listing | `openrouter:google/gemini-2.0-flash` |
| `local:<model>` | Ollama, vLLM, llama.cpp, etc. | Port probing + explicit servers | `local:qwen3-coder-next:latest` |

**Local server types supported:**

| Server | Default Port | API Format | Install |
|--------|-------------|-----------|---------|
| Ollama | 11434 | OpenAI or Anthropic (0.14+) | https://ollama.com |
| vLLM | 8000 | OpenAI | `pip install vllm` |
| llama.cpp | 8080 | OpenAI or Anthropic | https://github.com/ggerganov/llama.cpp |
| LocalAI | 8080 | OpenAI | https://localai.io |
| TGI (HuggingFace) | 3000 | OpenAI | https://github.com/huggingface/text-generation-inference |
| Aphrodite | 2242 | OpenAI | https://github.com/PygmalionAI/aphrodite-engine |
| TabbyAPI | 5000 | OpenAI | https://github.com/theroyallab/tabbyAPI |

For Ollama 0.14+ and llama.cpp, configure with `api_format: "anthropic"` for zero-overhead passthrough.

---

## Requirements

- **Python 3.11 or later**
- **Claude Code** (CLI or desktop app)
- **pip** for installation
- **At least one API key**:
  - Anthropic key required for Claude models (the default)
  - RunPod, OpenRouter keys optional
  - Local servers need no keys

---

## Quick Start

```bash
git clone https://github.com/oxygn-cloud-ai/uam
cd uam
pip install -e .
```

Then in Claude Code:

1. Run `/uam setup` and follow the prompts
2. Add your API keys to `~/.zshrc` or `~/.bashrc` (see [API Keys](#3-api-keys))
3. Restart your terminal
4. Start a new Claude Code session — the proxy auto-starts via SessionStart hook
5. Run `/model` to see discovered models, toggle them on/off, set a default

**That's it.** Pick a model and Claude Code starts using it.

---

## Installation

### 1. Clone and install

```bash
git clone https://github.com/oxygn-cloud-ai/uam
cd uam
pip install -e .
```

This installs uam as an editable Python package. The proxy is started with `python -m uam` (but normally the SessionStart hook handles this for you).

### 2. Run setup

Inside Claude Code:

```
/uam setup
```

Setup performs:

1. Verifies the uam package is installed and importable
2. Creates `~/.uam/config.json` with sensible defaults
3. Interactively configures local model servers (asks about Ollama, vLLM, etc.)
4. Copies slash commands to `~/.claude/commands/`
5. Copies hooks to `~/.claude/hooks/`
6. Merges the SessionStart hook into `~/.claude/settings.json`
7. Adds `export ANTHROPIC_BASE_URL=http://127.0.0.1:5100` to your shell profile

Setup is **idempotent and safe to re-run**.

### 3. API keys

Add to `~/.zshrc` or `~/.bashrc`:

```bash
# Required for Claude models
export ANTHROPIC_API_KEY_REAL="sk-ant-..."

# Optional — OpenRouter
export OPENROUTER_API_KEY="sk-or-..."

# Optional — RunPod
export RUNPOD_API_KEY="rpa_..."
```

> **Why `ANTHROPIC_API_KEY_REAL` instead of `ANTHROPIC_API_KEY`?**
> Claude Code itself uses `ANTHROPIC_API_KEY`, and uam overrides `ANTHROPIC_BASE_URL` to point to the proxy. The proxy reads your real Anthropic key from `ANTHROPIC_API_KEY_REAL` and forwards it upstream. This separation prevents key collisions.

### 4. Restart terminal

After setup, restart your terminal to pick up the new environment variables. Then start a new Claude Code session.

---

## Usage

### `/uam` — Proxy management

```
/uam              Show proxy status (running/stopped, model count, default)
/uam start        Start the proxy in the background
/uam stop         Stop the proxy
/uam refresh      Re-discover models from all backends
/uam setup        One-time installation (run once after cloning)
/uam uninstall    Remove everything, restore stock Claude Code
```

Most users only ever run `/uam` to check status. The proxy auto-starts via the SessionStart hook.

### `/model` — Model management

```
/model            List models, toggle on/off, set default
/model refresh    Re-discover models, then show the list
```

Example display:

```
Default: local:qwen3-coder-next:latest

Anthropic:
  [x] claude-sonnet-4-6          (alias: claude)
  [x] claude-opus-4-6            (alias: opus)
  [ ] claude-haiku-4-5-20251001  (alias: haiku)

OpenRouter:
  [x] openrouter:google/gemini-2.0-flash  (alias: gemini)
  [x] openrouter:deepseek/deepseek-chat   (alias: deepseek)

Local (Ollama):
  [x] local:qwen3-coder-next:latest       (alias: qwen)  ← default
  [x] local:llama3.3:70b                  (alias: llama)
```

- `[x]` enabled, `[ ]` disabled
- The **default** receives ALL Claude Code requests
- Setting a non-Claude default means every Claude Code response comes from that model
- Aliases work in `ask` commands and the `/model` picker

### `ask <model> <query>` — One-shot queries

In normal conversation, prefix with `ask`:

```
ask gemini what is the capital of france
ask qwen explain this regex
ask deepseek can you spot the bug in my function
ask llama write a haiku about coding
```

This sends the query to the named model **without changing your default**. Useful for second opinions or comparing answers across models.

If the model is disabled or not found, you get a helpful message (not an error).

### Default model swap

When you set `local:qwen3-coder-next:latest` as default, here's what happens:

1. Claude Code sends a request addressed to `claude-sonnet-4-6` (its internal default)
2. uam intercepts the request
3. uam swaps the model to `local:qwen3-coder-next:latest`
4. uam translates the request from Anthropic format to OpenAI format
5. uam forwards to your Ollama server
6. Response comes back in OpenAI format
7. uam translates back to Anthropic format
8. Claude Code displays it

**The whole tool ecosystem (Read, Edit, Bash, etc.) keeps working** because tool calls are part of the format translation. Your file edits, your bash commands, your web fetches — all of it works through the new model.

---

## Backend Setup Guides

### Anthropic (always available)

Hardcoded model list — no API call required for discovery:

- `claude-opus-4-6`
- `claude-sonnet-4-6`
- `claude-haiku-4-5-20251001`
- `claude-opus-4-6[1m]` (1M context variant)
- `claude-sonnet-4-6[1m]` (1M context variant)

Just set `ANTHROPIC_API_KEY_REAL` in your shell profile.

### OpenRouter (100+ models)

```bash
export OPENROUTER_API_KEY="sk-or-..."
```

Then `/uam refresh`. All available models auto-discovered. They appear as `openrouter:<org>/<model>`.

Get a key at https://openrouter.ai/keys.

### RunPod (your own GPU pods)

```bash
export RUNPOD_API_KEY="rpa_..."
```

Add the account to `~/.uam/config.json`:

```json
"runpod": {
  "accounts": {
    "my-account": { "api_key_env": "RUNPOD_API_KEY" }
  },
  "timeout": 300
}
```

Then `/uam refresh`. uam queries the RunPod GraphQL API to find your running pods.

**Pod requirements:**
- Status: `RUNNING`
- Port `8000` exposed
- vLLM with OpenAI-compatible API running on port 8000
- Optional: `VLLM_API_KEY` env var on the pod (uam picks this up automatically, including `$RUNPOD_POD_ID` substitution)

Models appear as `runpod:<pod-name>/<model-id>`.

Get a key at https://www.runpod.io/console/user/settings.

### Local servers (Ollama, vLLM, llama.cpp, etc.)

**Localhost** — auto-probed on common ports (11434, 8000, 8080, 2242, 5000, 3000). Just start your server and run `/uam refresh`.

**Remote servers** — add to config:

```json
"local": {
  "probe_ports": [11434, 8000, 8080, 2242, 5000, 3000],
  "servers": [
    "http://192.168.1.50:11434",
    {
      "url": "http://my-gpu-box:8000",
      "api_format": "anthropic"
    }
  ]
}
```

**`api_format: "anthropic"`** — set this for Ollama 0.14+ or llama.cpp servers that expose the native Anthropic Messages API. uam will skip translation entirely and proxy requests/responses verbatim. This is the lowest-overhead path.

**Ollama example:**

```bash
ollama serve
ollama pull qwen3-coder
```

Then `/uam refresh` → `local:qwen3-coder:latest` appears.

**vLLM example:**

```bash
python -m vllm.entrypoints.openai.api_server \
  --model meta-llama/Llama-3.1-70B \
  --port 8000
```

Then `/uam refresh` → `local:meta-llama/Llama-3.1-70B` appears.

**llama.cpp example:**

```bash
./llama-server -m model.gguf --port 8080
```

---

## Configuration Reference

### `~/.uam/config.json`

Created by `/uam setup`. Edit by hand if needed.

```json
{
  "listen": "127.0.0.1:5100",
  "anthropic": {
    "url": "https://api.anthropic.com",
    "api_key_env": "ANTHROPIC_API_KEY_REAL",
    "timeout": 600
  },
  "runpod": {
    "accounts": {
      "my-account": { "api_key_env": "RUNPOD_API_KEY" }
    },
    "timeout": 300
  },
  "openrouter": {
    "url": "https://openrouter.ai/api",
    "api_key_env": "OPENROUTER_API_KEY",
    "timeout": 300
  },
  "local": {
    "probe_ports": [11434, 8000, 8080, 2242, 5000, 3000],
    "servers": [],
    "timeout": 120
  },
  "default_backend": "anthropic"
}
```

| Field | Description | Default |
|-------|-------------|---------|
| `listen` | Address and port the proxy listens on | `127.0.0.1:5100` |
| `anthropic.url` | Anthropic API base URL | `https://api.anthropic.com` |
| `anthropic.api_key_env` | Env var name with the Anthropic key | `ANTHROPIC_API_KEY_REAL` |
| `anthropic.timeout` | Request timeout in seconds | `600` |
| `runpod.accounts` | Map of account names to RunPod key env vars | `{}` |
| `runpod.timeout` | RunPod request timeout | `300` |
| `openrouter.url` | OpenRouter API base URL | `https://openrouter.ai/api` |
| `openrouter.api_key_env` | Env var name with the OpenRouter key | `OPENROUTER_API_KEY` |
| `openrouter.timeout` | OpenRouter request timeout | `300` |
| `local.probe_ports` | Localhost ports to probe for model servers | `[11434, 8000, 8080, 2242, 5000, 3000]` |
| `local.servers` | Explicit server URLs (string or `{url, api_format}` dict) | `[]` |
| `local.timeout` | Local server request timeout | `120` |
| `default_backend` | Fallback backend for unknown models | `anthropic` |

### `~/.uam/models.json`

**Auto-managed by uam.** Don't edit by hand — use `/model` instead.

```json
{
  "default": "local:qwen3-coder-next:latest",
  "aliases": {
    "claude": "claude-sonnet-4-6",
    "opus": "claude-opus-4-6",
    "qwen": "local:qwen3-coder-next:latest",
    "gemini": "openrouter:google/gemini-2.0-flash"
  },
  "models": {
    "claude-sonnet-4-6": {
      "enabled": true,
      "capabilities": ["tools", "streaming", "thinking", "vision"]
    },
    "local:qwen3-coder-next:latest": {
      "enabled": true,
      "capabilities": ["tools", "streaming"]
    }
  }
}
```

### `~/.uam/env.sh`

**Managed file.** Sourced by your shell to set Claude Code env vars based on your default model. Updated automatically when you change defaults via `/model`. File mode `0o600` (owner read/write only). Values are `shlex.quote()`-escaped to prevent shell injection.

```bash
# Managed by uam — do not edit manually
export ANTHROPIC_BASE_URL=http://127.0.0.1:5100
export ANTHROPIC_DEFAULT_SONNET_MODEL=local:qwen3-coder-next:latest
export ANTHROPIC_DEFAULT_SONNET_MODEL_NAME=qwen
export ANTHROPIC_DEFAULT_SONNET_MODEL_SUPPORTED_CAPABILITIES=tools,streaming
```

### Environment variables

| Variable | Required | Backend | Description |
|----------|----------|---------|-------------|
| `ANTHROPIC_API_KEY_REAL` | Yes | Anthropic | Your real Anthropic API key (proxy reads, not Claude Code) |
| `ANTHROPIC_BASE_URL` | Yes | Proxy | Set to `http://127.0.0.1:5100` (configured by setup) |
| `OPENROUTER_API_KEY` | No | OpenRouter | OpenRouter API key |
| `RUNPOD_API_KEY` | No | RunPod | RunPod API key |
| `UAM_LOG_LEVEL` | No | Proxy | Log level: `WARNING` (default), `INFO`, `DEBUG` |

---

## Security Model

uam takes security seriously. The proxy is a privileged process — it sees every request you make to AI models, including potentially sensitive prompts. Here's how it protects you:

### API key protection
- **Config stores environment variable names, never values.** Keys are resolved at runtime via `os.environ.get()`.
- **Keys are never written to disk** by uam. Not in config, not in state, not in logs.
- **`redact_headers()` strips `Authorization` and `X-Api-Key` from all log output**, even at DEBUG level.
- **Upstream error bodies are scrubbed** of `Authorization` headers and `Bearer` tokens before being returned to the client (in case a misbehaving upstream echoes them).

### Network isolation
- **Proxy binds to `127.0.0.1` only.** Not accessible from other machines.
- **Host header validation** rejects requests where the `Host` header isn't `127.0.0.1:5100` or `localhost:5100`. This closes the **DNS rebinding** vector — a malicious website can't trick your browser into hitting your local proxy.

### File safety
- **`~/.uam/env.sh` is mode `0o600`** (owner read/write only).
- **All shell-interpolated values use `shlex.quote()`** — no command injection possible from POST /state.
- **State file writes are atomic** (`tempfile + os.replace`) — SIGTERM during write doesn't corrupt your model state.

### Process safety
- **`asyncio.Lock` serializes state writes** — concurrent POST /state requests can't race.
- **`MAX_MODEL_ID_LEN = 512`** prevents memory amplification attacks via huge model IDs.
- **Bounded SSE buffer (1 MiB cap)** prevents OOM from a misbehaving upstream sending a giant unbroken line.
- **Generic error messages** for proxy errors (no URLs, no internal pod IDs leaked to clients). Full detail logged at ERROR level.

### Hook safety
- **Both hooks (`uam-autostart.py` and `uam-ask-router.py`) use Python stdlib only.** No pip dependencies that could pull in malware.
- Hooks always exit `0` — a failed hook can't block your Claude Code session.

### What uam does NOT do (yet)
- **No token authentication** on the proxy endpoints. Anything running on your machine as your user can call the proxy. (Mitigated by `127.0.0.1`-only binding and Host header validation.) Token auth is on the roadmap.
- **No request-level encryption**. Traffic between Claude Code and uam is plain HTTP on localhost (encryption would be theatre — same machine, same user).

---

## Troubleshooting

### Proxy won't start

| Symptom | Cause | Fix |
|---------|-------|-----|
| `Address already in use` | Port 5100 occupied | `lsof -i :5100`, kill the process, or change `listen` in config |
| `ModuleNotFoundError: aiohttp` | Missing dependency | `pip install -e .` from the uam repo directory |
| `python: command not found` | Python 3.11+ not in PATH | Install Python 3.11+ |
| Process exits immediately | Stale PID file | `rm ~/.uam/uam.pid && /uam start` |

### Models not discovered

| Symptom | Fix |
|---------|-----|
| Local server not appearing | Make sure server is running, check it's on a probed port (11434/8000/8080/2242/5000/3000) or in `local.servers`, then `/uam refresh`. To add a new remote server without editing JSON, use `/uam add-server`. |
| Remote server unreachable | Test with `curl http://your-server:port/v1/models` or `curl http://your-server:port/api/tags` |
| OpenRouter empty | `echo $OPENROUTER_API_KEY` to verify it's set, then `/uam refresh` |
| RunPod empty | Verify `echo $RUNPOD_API_KEY`, check pods are RUNNING with port 8000 exposed |

### `ask` not working

| Symptom | Fix |
|---------|-----|
| Pattern not matched | Check `~/.claude/settings.json` has the UserPromptSubmit hook entry, or run `/uam setup` |
| "Model is off" message | Run `/model` and enable the model |
| "Model not configured" | Run `/model` to see exact names/aliases |
| Hangs forever | Run `/uam status` — the proxy may not be running |

### Translation issues

| Symptom | Cause | Fix |
|---------|-------|-----|
| Empty responses | Backend model doesn't support requested params | Try a different model |
| Tool calling errors | Model doesn't support function calling | Use a tool-capable model (qwen3-coder, llama-3.x, gpt-4, claude-*) |
| Garbled streaming | Buffer issue | Check `~/.uam/uam.log` for buffer warnings |

### Connection timeouts

The proxy uses per-backend timeouts:
- Anthropic: 600s
- OpenRouter / RunPod: 300s
- Local: 120s

Override in `~/.uam/config.json` per backend.

---

## Logging and Debugging

### Log file

```bash
tail -f ~/.uam/uam.log
```

The proxy writes to `~/.uam/uam.log` with rotation (5 MB max, 3 backups). API keys are automatically redacted.

### Log levels

```bash
UAM_LOG_LEVEL=DEBUG python -m uam   # Verbose
UAM_LOG_LEVEL=INFO python -m uam    # Normal operations
UAM_LOG_LEVEL=WARNING python -m uam # Errors only (default)
```

### Health check

```bash
curl http://127.0.0.1:5100/health
```

### List discovered models

```bash
curl http://127.0.0.1:5100/v1/models | python3 -m json.tool
```

### Check current state

```bash
curl http://127.0.0.1:5100/state | python3 -m json.tool
```

### Trigger re-discovery

```bash
curl -X POST http://127.0.0.1:5100/refresh \
  -H "Authorization: Bearer $(cat ~/.uam/token)"
```

### Add a remote local backend (re-runnable)

Prefer the `/uam add-server` slash command, which handles probing and discovery for you. Equivalent curl:

```bash
curl -X POST http://127.0.0.1:5100/config/local-servers \
  -H "Authorization: Bearer $(cat ~/.uam/token)" \
  -H "Content-Type: application/json" \
  -d '{"url": "http://192.168.1.50:11434", "api_format": "openai"}'

# Then trigger discovery so the new backend's models become available:
curl -X POST http://127.0.0.1:5100/refresh \
  -H "Authorization: Bearer $(cat ~/.uam/token)"
```

This persists to `~/.uam/config.json` (idempotent — duplicates are silently dropped) so the server is permanently registered. URL normalization: a missing scheme defaults to `http://`; trailing slashes are stripped. Only `http://` and `https://` schemes are accepted.

### Test backends directly

```bash
# Ollama on remote machine
curl http://192.168.1.50:11434/api/tags

# vLLM on localhost
curl http://localhost:8000/v1/models

# OpenRouter
curl -H "Authorization: Bearer $OPENROUTER_API_KEY" \
     https://openrouter.ai/api/v1/models
```

---

## Uninstalling

Inside Claude Code:

```
/uam uninstall
```

This:
1. Stops the proxy (if running)
2. Removes slash commands from `~/.claude/commands/`
3. Removes hooks from `~/.claude/hooks/`
4. Removes hook entries from `~/.claude/settings.json`
5. Removes `ANTHROPIC_BASE_URL` from your shell profile
6. Optionally removes `~/.uam/` (config, state, logs)
7. Uninstalls the pip package

After uninstalling, Claude Code's built-in `/model` command is restored. Restart your terminal and start a new Claude Code session.

---

## Architecture

For a deep dive into how uam works internally — request flow, module structure, security model, error handling, and design decisions — see [ARCHITECTURE.md](ARCHITECTURE.md).

For the project's vision, mission, and design principles, see [PHILOSOPHY.md](PHILOSOPHY.md).

For known issues and security findings, see [SECURITY_ISSUES.md](SECURITY_ISSUES.md), [CODE_ISSUES.md](CODE_ISSUES.md), and [PERFORMANCE_ISSUES.md](PERFORMANCE_ISSUES.md).

---

## Contributing

Contributions welcome. Please follow these guidelines:

### Setup

```bash
git clone https://github.com/oxygn-cloud-ai/uam
cd uam
pip install -e ".[test]"
```

### Development workflow

```bash
python -m uam                  # Start proxy in foreground
python -m uam --skip-discovery # Anthropic-only mode (faster for dev)
```

### Testing

```bash
pytest tests/ -v --cov=uam --cov-report=term-missing --cov-branch
```

uam follows **strict TDD** — write failing tests first, then implement to make them pass. Current state: **374 tests, ~99% branch coverage**. New code must include tests. PRs without tests will be asked to add them.

### Code style

- Python 3.11+ syntax (use `match` statements, `|` union types, etc.)
- Async/await for all I/O
- Logging via `logging.getLogger("uam.<module>")` — never `print()`
- Type hints on public functions
- Docstrings on public functions and modules
- No new dependencies without strong justification (uam is intentionally lean)

### Submitting changes

1. Fork and create a feature branch
2. Write tests first (red)
3. Implement until tests pass (green)
4. Run `pytest tests/` — must be all green
5. Bump the version in `pyproject.toml` and `src/uam/__init__.py`
6. Open a PR with a clear description of the change and why

---

## Reporting Issues

Found a bug? Open an issue at https://github.com/oxygn-cloud-ai/uam/issues.

Please include:

1. **What happened** — describe the issue
2. **What you expected** — the intended behavior
3. **Steps to reproduce** — minimal example
4. **Proxy log** — last 50 lines of `~/.uam/uam.log`
5. **Config** — `cat ~/.uam/config.json` (safe to share — only env var names, no keys)
6. **Model list** — `curl http://127.0.0.1:5100/v1/models`
7. **Environment** — Python version, OS, Claude Code version, uam version

Security issues should be reported privately. See [SECURITY_ISSUES.md](SECURITY_ISSUES.md) for the disclosure process.

---

## License

[MIT](LICENSE) — see the LICENSE file for details.

---

## Acknowledgments

uam stands on the shoulders of giants:

- **Anthropic** for [Claude Code](https://github.com/anthropics/claude-code) and the Messages API
- **claude-code-router** by musistudio for proving the proxy approach scales
- **LiteLLM** for pioneering the multi-model translation patterns
- **Ollama** and **llama.cpp** for native Anthropic API support that makes local inference seamless
- The **OpenRouter** team for unifying access to 100+ models behind one API

---

**Built with care by [@oxygn-cloud-ai](https://github.com/oxygn-cloud-ai) — because Claude Code shouldn't be locked to one model.**
