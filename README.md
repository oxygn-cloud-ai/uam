# uam -- Use Any Model with Claude Code

A multi-backend model router that lets you swap the default AI model powering Claude Code to any supported model -- Anthropic, RunPod vLLM, OpenRouter, or local servers like Ollama, vLLM, and llama.cpp.

uam runs a transparent HTTP proxy on `localhost:5100` that intercepts Claude Code's API requests and routes them to the backend of your choice. The entire interface is slash commands inside Claude Code -- no separate CLI, no config UI, no web dashboard. You configure everything with `/uam` and `/model`.

---

## Features

- **Swap the default AI model** -- route all Claude Code responses through any supported model
- **Multi-backend routing** -- Anthropic, RunPod vLLM pods, OpenRouter (100+ models), local servers
- **Auto-discovery** -- models are discovered automatically from all configured backends
- **One-shot queries** -- type `ask gemini what is X` in normal conversation to query any model
- **Format translation** -- transparent Anthropic-to-OpenAI API translation (and back) for non-Anthropic backends
- **Auto-generated aliases** -- short names like `gemini`, `llama`, `qwen`, `opus` are created automatically
- **Toggle models on/off** -- disable models you don't want cluttering the list
- **Auto-start proxy** -- a SessionStart hook starts the proxy when Claude Code launches
- **No CLI** -- everything happens via `/uam` and `/model` slash commands inside Claude Code
- **Security-first** -- config stores environment variable names, never actual API keys

---

## Supported Backends

| Prefix | Backend | Discovery | Example |
|--------|---------|-----------|---------|
| `claude-*` | Anthropic API | Always available | `claude-sonnet-4-6` |
| `runpod:<pod>/<model>` | RunPod vLLM pods | GraphQL API discovery | `runpod:my-pod/llama-3.1-70b` |
| `openrouter:<org>/<model>` | OpenRouter | API model listing | `openrouter:google/gemini-2.0-flash` |
| `local:<model>` | Ollama, vLLM, etc. | Port probing + explicit servers | `local:qwen3-coder-next:latest` |

---

## Requirements

- Python 3.11 or later
- Claude Code (CLI or desktop app)
- pip (for installation)
- At least one API key (Anthropic is required; RunPod, OpenRouter, and local servers are optional)

---

## Quick Start

```bash
git clone https://github.com/oxygn-cloud/uam
cd uam
pip install -e .
```

Then in Claude Code:

1. Type `/uam setup` and follow the prompts
2. Add your API keys to your shell profile (see [API Keys](#environment-variables))
3. Restart your terminal
4. Start a new Claude Code session (the proxy auto-starts)
5. Type `/model` to see discovered models, toggle them on/off, and set a default

---

## Installation

### Clone and Install

```bash
git clone https://github.com/oxygn-cloud/uam
cd uam
pip install -e .
```

This installs uam as an editable package. The proxy can be started with `python -m uam`.

### Run Setup

Inside Claude Code, type:

```
/uam setup
```

Setup performs the following steps:

1. Verifies the uam package is installed and importable
2. Creates `~/.uam/config.json` with default settings
3. Configures local model servers (interactive -- asks about Ollama, vLLM, etc.)
4. Copies slash commands to `~/.claude/commands/`
5. Copies hooks to `~/.claude/hooks/`
6. Merges hook configuration into `~/.claude/settings.json`
7. Adds `export ANTHROPIC_BASE_URL=http://127.0.0.1:5100` to your shell profile

### Set Up API Keys

Add the following to `~/.zshrc` or `~/.bashrc`:

```bash
# Required -- Anthropic
export ANTHROPIC_API_KEY_REAL="sk-ant-..."

# Optional -- OpenRouter
export OPENROUTER_API_KEY="sk-or-..."

# Optional -- RunPod
export RUNPOD_API_KEY="rpa_..."
```

**Why `ANTHROPIC_API_KEY_REAL` instead of `ANTHROPIC_API_KEY`?** Claude Code sets `ANTHROPIC_API_KEY` itself, and setup overrides `ANTHROPIC_BASE_URL` to point to the proxy. The proxy reads your actual Anthropic key from `ANTHROPIC_API_KEY_REAL` and forwards it to the Anthropic API.

### Restart Terminal

After setup, restart your terminal to pick up the new environment variables. Then start a new Claude Code session. The proxy auto-starts via the SessionStart hook.

---

## Usage

### /uam -- Proxy Management

```
/uam              Show proxy status (running/stopped, model count, current default)
/uam start        Start the proxy in the background
/uam stop         Stop the proxy
/uam refresh      Re-discover models from all backends
/uam setup        One-time installation (run once after cloning)
/uam uninstall    Remove everything, restore stock Claude Code
```

**`/uam`** (no arguments) -- Shows whether the proxy is running, how many models are discovered, and which model is the current default.

**`/uam start`** -- Starts the proxy as a background process. The proxy listens on the address configured in `~/.uam/config.json` (default `127.0.0.1:5100`). The PID is written to `~/.uam/uam.pid`.

**`/uam stop`** -- Stops the running proxy. Reads the PID from `~/.uam/uam.pid` and terminates the process.

**`/uam refresh`** -- Re-runs model discovery across all backends. Use this after starting a new local server, deploying a new RunPod pod, or adding a backend to your config.

**`/uam setup`** -- The one-time installation command. Installs slash commands, hooks, config, and shell environment. Safe to re-run.

**`/uam uninstall`** -- Removes all uam components and restores Claude Code to its stock configuration. See [Uninstalling](#uninstalling) for details.

### /model -- Model Management

```
/model            List all models, toggle on/off, set default
/model refresh    Re-discover models, then show the list
```

The `/model` command displays all discovered models grouped by backend, with their enabled/disabled status, aliases, and which is the current default:

```
Default: claude-sonnet-4-6

Anthropic:
  [x] claude-sonnet-4-6          (alias: claude)
  [x] claude-opus-4-6            (alias: opus)
  [ ] claude-haiku-4-5-20251001  (alias: haiku)

OpenRouter:
  [x] openrouter:google/gemini-2.0-flash  (alias: gemini)

Local:
  [x] local:qwen3-coder-next:latest       (alias: qwen)
```

**Toggling models** -- Models marked `[x]` are enabled; `[ ]` are disabled. Disabled models are ignored by the proxy and cannot be used with `ask`. Use `/model` to toggle individual models on or off.

**Setting the default** -- The default model receives ALL Claude Code requests. When you set a non-Claude model as default, every Claude Code response comes from that model instead of Anthropic. Use `/model` to change the default.

### ask \<model\> \<query\>

In normal conversation with Claude Code, prefix your message with `ask` followed by a model name or alias:

```
ask gemini what is the capital of france
ask llama explain quicksort in python
ask qwen write a haiku about code
```

This works with full model IDs or auto-generated aliases. The query is sent as a one-shot request to the specified model, and the response is returned inline.

If the model is disabled, you get a message explaining how to enable it with `/model`. If the model is not found, you get a list of available models and aliases.

The `ask` feature is powered by a UserPromptSubmit hook that intercepts matching patterns before Claude Code processes them.

### Default Model Swap

When you set a non-Claude model as default (for example, `local:qwen3-coder-next:latest`), all Claude Code responses come from that model. Claude Code still sends requests addressed to `claude-*`, but the proxy transparently rewrites them to target your chosen model and translates between the Anthropic and OpenAI API formats as needed.

To revert to Anthropic, set any `claude-*` model as the default via `/model`.

### Model Aliases

uam auto-generates short aliases from model IDs:

- `openrouter:google/gemini-2.0-flash` becomes `gemini`
- `local:qwen3-coder-next:latest` becomes `qwen`
- `claude-opus-4-6` becomes `opus`
- `claude-sonnet-4-6` becomes `claude`

Aliases are used in `ask` commands and displayed alongside models in `/model`. They are stored in `~/.uam/models.json` and regenerated on each discovery.

---

## Configuration Reference

### ~/.uam/config.json

The main configuration file. Created by `/uam setup`, editable by hand.

```json
{
  "listen": "127.0.0.1:5100",
  "anthropic": {
    "url": "https://api.anthropic.com",
    "api_key_env": "ANTHROPIC_API_KEY_REAL"
  },
  "runpod": {
    "accounts": {
      "account-name": {
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
    "servers": [
      "http://192.168.1.50:11434"
    ]
  },
  "default_backend": "anthropic"
}
```

**Field reference:**

| Field | Description | Default |
|-------|-------------|---------|
| `listen` | Address and port the proxy listens on | `127.0.0.1:5100` |
| `anthropic.url` | Anthropic API base URL | `https://api.anthropic.com` |
| `anthropic.api_key_env` | Environment variable name containing the Anthropic API key | `ANTHROPIC_API_KEY_REAL` |
| `runpod.accounts` | Map of account names to RunPod API key env vars. Set to `{}` to disable RunPod | `{}` |
| `openrouter.url` | OpenRouter API base URL | `https://openrouter.ai/api` |
| `openrouter.api_key_env` | Environment variable name containing the OpenRouter API key | `OPENROUTER_API_KEY` |
| `local.probe_ports` | Ports to probe on localhost for model servers | `[11434, 8000, 8080, 2242, 5000, 3000]` |
| `local.servers` | Explicit server URLs for remote or non-standard servers | `[]` |
| `default_backend` | Fallback backend for models that don't match any prefix | `anthropic` |

**Config stores environment variable names, never actual API key values.** Keys are resolved at runtime via `os.environ.get()`.

### ~/.uam/models.json

Auto-managed by the proxy. Do not edit manually -- use `/model` instead.

```json
{
  "default": "claude-sonnet-4-6",
  "aliases": {
    "claude": "claude-sonnet-4-6",
    "opus": "claude-opus-4-6",
    "gemini": "openrouter:google/gemini-2.0-flash"
  },
  "models": {
    "claude-sonnet-4-6": { "enabled": true },
    "claude-opus-4-6": { "enabled": true },
    "openrouter:google/gemini-2.0-flash": { "enabled": true }
  }
}
```

- `default` -- the model that receives all Claude Code requests
- `aliases` -- mapping of short names to full model IDs
- `models` -- per-model state (enabled/disabled)

### Environment Variables

| Variable | Required | Backend | Description |
|----------|----------|---------|-------------|
| `ANTHROPIC_API_KEY_REAL` | Yes | Anthropic | Your Anthropic API key |
| `ANTHROPIC_BASE_URL` | Yes | Proxy | Set to `http://127.0.0.1:5100` (configured by setup) |
| `OPENROUTER_API_KEY` | No | OpenRouter | OpenRouter API key |
| `RUNPOD_API_KEY` | No | RunPod | RunPod API key |

---

## Backend Setup Guides

### Anthropic

Always available. Anthropic models are hardcoded -- no API call is needed for discovery:

- `claude-opus-4-6`
- `claude-sonnet-4-6`
- `claude-haiku-4-5-20251001`

Set `ANTHROPIC_API_KEY_REAL` in your shell profile and you're done.

### RunPod

For running vLLM inference on RunPod GPU pods.

1. Get a RunPod API key from https://www.runpod.io/console/user/settings
2. Add `export RUNPOD_API_KEY="rpa_..."` to your shell profile
3. Add the account to `~/.uam/config.json`:

```json
"runpod": {
  "accounts": {
    "my-account": { "api_key_env": "RUNPOD_API_KEY" }
  }
}
```

4. Run `/uam refresh` -- uam discovers running pods via the RunPod GraphQL API
5. Models appear as `runpod:<pod-name>/<model-id>`

**Requirements for RunPod pods:**

- Pod must be in RUNNING state
- Port 8000 must be exposed (the vLLM default)
- vLLM server must be running with the OpenAI-compatible API

### OpenRouter

Access 100+ models through OpenRouter.

1. Get an API key from https://openrouter.ai/keys
2. Add `export OPENROUTER_API_KEY="sk-or-..."` to your shell profile
3. The OpenRouter config is pre-configured -- it just needs the key
4. Run `/uam refresh` -- all available models are auto-discovered
5. Models appear as `openrouter:<org>/<model>`

### Local Servers

uam supports any server that speaks the OpenAI Chat Completions API or the Ollama API.

**Supported server types:**

| Server | Default Port | Discovery Endpoint | Install |
|--------|-------------|-------------------|---------|
| Ollama | 11434 | `/api/tags` | https://ollama.com |
| vLLM | 8000 | `/v1/models` | `pip install vllm` |
| llama.cpp | 8080 | `/v1/models` | https://github.com/ggerganov/llama.cpp |
| LocalAI | 8080 | `/v1/models` | https://localai.io |
| TGI | 3000 | `/v1/models` | https://github.com/huggingface/text-generation-inference |
| Aphrodite | 2242 | `/v1/models` | https://github.com/PygmalionAI/aphrodite-engine |
| TabbyAPI | 5000 | `/v1/models` | https://github.com/theroyallab/tabbyAPI |

**Localhost servers** are auto-probed on the default ports listed above. No configuration is needed -- start the server, run `/model refresh`, and your models appear.

**Remote servers** (on your local network or another machine) must be added to the config:

```json
"local": {
  "probe_ports": [11434, 8000, 8080, 2242, 5000, 3000],
  "servers": [
    "http://192.168.1.50:11434",
    "http://my-gpu-box:8000"
  ]
}
```

**Examples:**

Ollama running on another machine:

```json
"servers": ["http://192.168.1.50:11434"]
```

vLLM on a local GPU (auto-discovered on localhost:8000, no config change needed):

```bash
python -m vllm.entrypoints.openai.api_server --model meta-llama/Llama-3.1-70B --port 8000
```

llama.cpp server (auto-discovered on localhost:8080):

```bash
./llama-server -m model.gguf --port 8080
```

---

## Troubleshooting

### Proxy won't start

- **Port already in use** -- Another process is on port 5100. Check with `lsof -i :5100`. Kill the process or change the `listen` port in `~/.uam/config.json`.
- **Python version** -- Requires 3.11+. Check with `python3 --version`.
- **Missing aiohttp** -- Run `pip install -e .` from the uam repo directory.
- **Permission denied** -- Another uam instance may be running. Check with `cat ~/.uam/uam.pid` and kill the old process.

### Models not discovered

- **Local server not running** -- Start your Ollama/vLLM server, then run `/uam refresh`.
- **Wrong port** -- Ensure your server is on a probed port (11434, 8000, 8080, 2242, 5000, 3000) or add it to `servers` in the config.
- **Remote server unreachable** -- Test connectivity: `curl http://your-server:port/api/tags` (Ollama) or `curl http://your-server:port/v1/models` (OpenAI-compatible servers).
- **API key not set** -- For RunPod/OpenRouter, verify the env var is set: `echo $RUNPOD_API_KEY`.
- **RunPod pods not running** -- Pods must be in RUNNING state with port 8000 exposed.

### "ask" not working

- **Hook not installed** -- Run `/uam setup` or check `~/.claude/settings.json` for the UserPromptSubmit hook entry.
- **Model disabled** -- Enable it with `/model`.
- **Model not found** -- Check the exact name or alias with `/model`.
- **Proxy not running** -- Start it with `/uam start`.

### Format translation issues

- **Tool calling errors** -- Some models don't support function/tool calling. Try a model that does, or simplify the request.
- **Streaming glitches** -- Check `/tmp/uam.log` for buffering errors.
- **Empty responses** -- The backend model may not support the requested parameters.

### Connection timeouts

- **Discovery timeout** -- Remote servers have a 5-second probe timeout. Ensure the server is responsive.
- **Request timeout** -- The proxy has a 600-second overall timeout. This should be sufficient for even very long requests.

---

## Logging and Debugging

**Proxy log:**

```bash
cat /tmp/uam.log
```

**Health check:**

```bash
curl http://127.0.0.1:5100/health
```

**List discovered models:**

```bash
curl http://127.0.0.1:5100/v1/models | python3 -m json.tool
```

**Check current state:**

```bash
curl http://127.0.0.1:5100/state | python3 -m json.tool
```

**Trigger re-discovery:**

```bash
curl -X POST http://127.0.0.1:5100/refresh
```

**Test a specific backend directly:**

```bash
# Ollama
curl http://192.168.1.50:11434/api/tags

# vLLM
curl http://localhost:8000/v1/models

# OpenRouter
curl -H "Authorization: Bearer $OPENROUTER_API_KEY" https://openrouter.ai/api/v1/models
```

---

## Uninstalling

Inside Claude Code:

```
/uam uninstall
```

This performs the following steps:

1. Stops the proxy if it is running
2. Removes slash commands from `~/.claude/commands/`
3. Removes hooks from `~/.claude/hooks/`
4. Removes hook entries from `~/.claude/settings.json`
5. Removes `ANTHROPIC_BASE_URL` from your shell profile
6. Optionally removes `~/.uam/` (config and model state)
7. Uninstalls the pip package

After uninstalling, Claude Code's built-in `/model` command is restored. Restart your terminal and start a new Claude Code session.

---

## Reporting Issues

Found a bug? Open an issue at https://github.com/oxygn-cloud/uam/issues

Include the following:

1. **What happened** -- describe the issue
2. **What you expected** -- what should have happened
3. **Proxy log** -- `cat /tmp/uam.log` (last 50 lines)
4. **Config** -- `cat ~/.uam/config.json` (safe to share -- it contains env var names, not keys)
5. **Model list** -- `curl http://127.0.0.1:5100/v1/models`
6. **Error message** -- the exact error text
7. **Environment** -- Python version, OS, Claude Code version

---

## Contributing

```bash
git clone https://github.com/oxygn-cloud/uam
cd uam
pip install -e .
python -m uam                  # Start proxy in foreground
python -m uam --skip-discovery # Anthropic-only mode (faster for development)
```

The proxy runs on `http://127.0.0.1:5100`. Use curl to test endpoints directly.

---

## License

MIT
