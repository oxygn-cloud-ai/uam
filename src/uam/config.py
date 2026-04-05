"""Configuration loading for uam."""

import json
import os
from pathlib import Path

CONFIG_DIR = Path.home() / ".uam"
CONFIG_PATH = CONFIG_DIR / "config.json"
DEFAULT_PORT = 5100
DEFAULT_HOST = "127.0.0.1"


def get_config() -> dict:
    """Load config from ~/.uam/config.json, return defaults if missing."""
    if CONFIG_PATH.exists():
        return json.loads(CONFIG_PATH.read_text())
    return default_config()


def default_config() -> dict:
    return {
        "listen": f"{DEFAULT_HOST}:{DEFAULT_PORT}",
        "anthropic": {
            "url": "https://api.anthropic.com",
            "api_key_env": "ANTHROPIC_API_KEY_REAL",
        },
        "runpod": {"accounts": {}},
        "openrouter": {
            "url": "https://openrouter.ai/api",
            "api_key_env": "OPENROUTER_API_KEY",
        },
        "local": {"probe_ports": [11434, 8000, 8080, 2242, 5000, 3000], "servers": []},
        "default_backend": "anthropic",
    }


def resolve_key(env_var_name: str) -> str:
    """Resolve an API key from an environment variable name. Never logs the value."""
    return os.environ.get(env_var_name, "")


def parse_listen(config: dict) -> tuple[str, int]:
    """Parse listen address from config."""
    listen = config.get("listen", f"{DEFAULT_HOST}:{DEFAULT_PORT}")
    if ":" in listen:
        host, port_str = listen.rsplit(":", 1)
        return host, int(port_str)
    return DEFAULT_HOST, int(listen)
