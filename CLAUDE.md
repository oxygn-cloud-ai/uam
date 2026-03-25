# uam — Use Any Model

A multi-backend model router for Claude Code. Routes API requests to Anthropic, RunPod, OpenRouter, or local model servers based on model ID prefix.

## Architecture

```
Claude Code → localhost:5100 (uam proxy)
                ├─ claude-*        → api.anthropic.com
                ├─ runpod:*        → RunPod vLLM pods (auto-discovered)
                ├─ openrouter:*    → openrouter.ai
                └─ local:*         → localhost (Ollama, vLLM)
```

## Key files
- `src/uam/cli.py` — CLI entry point (start, stop, install, uninstall)
- `src/uam/router.py` — ModelRouter class (discovery + resolve)
- `src/uam/proxy.py` — aiohttp HTTP handlers
- `src/uam/discovery/` — per-backend discovery modules
- `src/uam/config.py` — config loading from ~/.uam/config.json

## Development
```
pip install -e .
uam start
```

## Security
- Never write, log, or display API key values
- Config stores env var names only (e.g. `"api_key_env": "OPENROUTER_API_KEY"`)
- Keys are resolved at runtime via `os.environ.get()`
