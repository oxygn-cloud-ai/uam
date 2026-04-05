# uam — Use Any Model

A multi-backend model router for Claude Code. Routes API requests to Anthropic, RunPod, OpenRouter, or local model servers. Swap the default AI model powering Claude Code to any supported model.

## Architecture

```
Claude Code → localhost:5100 (uam proxy)
                ├─ Default model swap (remap claude-* → any model)
                ├─ Format translation (Anthropic ↔ OpenAI Chat API)
                ├─ Model on/off enforcement (~/.uam/models.json)
                └─ Backend routing:
                     ├─ claude-*        → api.anthropic.com
                     ├─ runpod:*        → RunPod vLLM pods
                     ├─ openrouter:*    → openrouter.ai
                     └─ local:*         → localhost (Ollama, vLLM)

Hooks:
  SessionStart     → auto-start proxy
  UserPromptSubmit → intercept "ask <model> <query>" for one-shot routing
```

## Slash Commands (the only user interface)

- `/uam` — Proxy lifecycle: status, start, stop, refresh, setup, uninstall
- `/model` — List models, toggle on/off, set default, refresh discovery

There are NO CLI tools. Everything happens inside Claude Code.

## Key Files
- `src/uam/__main__.py` — Minimal proxy server entry point
- `src/uam/proxy.py` — HTTP handlers with model swap + format translation
- `src/uam/router.py` — ModelRouter (discovery + resolve)
- `src/uam/state.py` — Model state management (on/off, default, aliases)
- `src/uam/translate.py` — Anthropic ↔ OpenAI format translation
- `src/uam/config.py` — Config loading from ~/.uam/config.json
- `src/uam/discovery/` — Per-backend discovery modules
- `.claude/commands/` — Slash command definitions
- `.claude/hooks/` — SessionStart + UserPromptSubmit hooks

## State Files
- `~/.uam/config.json` — Backend configuration (API key env var names, URLs)
- `~/.uam/models.json` — Model state (on/off, default, aliases)
- `~/.uam/uam.pid` — Running proxy PID

## Development
```
pip install -e .
python -m uam              # Start proxy (foreground)
python -m uam --skip-discovery  # Anthropic only
```

## Security
- Never write, log, or display API key values
- Config stores env var names only (e.g. `"api_key_env": "OPENROUTER_API_KEY"`)
- Keys are resolved at runtime via `os.environ.get()`
