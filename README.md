# uam — Use Any Model with Claude Code

A lightweight proxy that lets you use **any model** from inside Claude Code's `/model` picker — not just Anthropic models.

uam auto-discovers models from multiple backends and routes Claude Code's API requests to the right one:

| Prefix | Backend | Discovery |
|--------|---------|-----------|
| `claude-*` | Anthropic API | Always available |
| `runpod:<pod>/<model>` | RunPod vLLM pods | Auto-discovered from RunPod API |
| `openrouter:<model>` | OpenRouter | Auto-discovered from OpenRouter API |
| `local:<model>` | Ollama, vLLM, etc. | Probed on localhost ports |

## Quickstart

```bash
pip install git+https://github.com/oxygn-cloud/uam
uam install
```

Then open a new terminal and run `claude` as normal. The router starts automatically.

## How it works

```
Claude Code → localhost:5100 (uam) → correct backend
```

1. `uam install` adds a shell wrapper that auto-starts the proxy when you launch `claude`
2. On startup, uam discovers all available models from your configured backends
3. Claude Code sees all models via `ANTHROPIC_BASE_URL=http://127.0.0.1:5100`
4. When you pick a model, uam routes the request to the right backend

## Configuration

Config lives at `~/.uam/config.json`:

```json
{
  "listen": "127.0.0.1:5100",
  "anthropic": {
    "url": "https://api.anthropic.com",
    "api_key_env": "ANTHROPIC_API_KEY_REAL"
  },
  "runpod": {
    "accounts": {
      "my-account": { "api_key_env": "RUNPOD_API_KEY" }
    }
  },
  "openrouter": {
    "url": "https://openrouter.ai/api",
    "api_key_env": "OPENROUTER_API_KEY"
  },
  "local": {
    "probe_ports": [11434, 8000, 8080]
  },
  "default_backend": "anthropic"
}
```

The config stores **environment variable names**, never actual API keys.

## Where to put your API keys

Add your keys as exports in `~/.zshrc` (or `~/.bashrc`):

```bash
# Anthropic (your real key, used when routing to Anthropic)
export ANTHROPIC_API_KEY_REAL="sk-ant-..."

# OpenRouter (optional)
export OPENROUTER_API_KEY="sk-or-..."

# RunPod (optional — one per account)
export RUNPOD_API_KEY="rpa_..."
```

Then reference them by name in `~/.uam/config.json` via the `api_key_env` field.

## CLI commands

```
uam start                    # Start the proxy, discover models
uam start --skip-discovery   # Start with Anthropic models only
uam stop                     # Stop the proxy
uam list                     # Show all discovered models
uam refresh                  # Re-discover models without restart
uam install                  # Set up config + shell wrapper
uam uninstall                # Remove everything, restore original state
```

## Uninstall

```bash
uam uninstall
```

This removes the shell wrapper from your rc file, deletes `~/.uam/`, and uninstalls the pip package. Claude Code goes back to using standard Anthropic models.

## Requirements

- Python 3.11+
- Claude Code CLI

## License

MIT
