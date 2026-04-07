"""Anthropic model registration — hardcoded, always available."""

from uam.config import get_backend_timeout, resolve_key

MODELS = [
    "claude-opus-4-6",
    "claude-opus-4-6-20250522",
    "claude-sonnet-4-6",
    "claude-sonnet-4-6-20250514",
    "claude-haiku-4-5-20251001",
]

ALIASES = {
    "claude-opus-4-6[1m]": "claude-opus-4-6",
    "claude-sonnet-4-6[1m]": "claude-sonnet-4-6",
}


def discover_anthropic(config: dict) -> dict[str, dict]:
    """Register Anthropic models. Returns model_id -> route dict."""
    anthropic_cfg = config.get("anthropic", {})
    api_key = resolve_key(anthropic_cfg.get("api_key_env", ""))
    url = anthropic_cfg.get("url", "https://api.anthropic.com")
    timeout = get_backend_timeout(config, "anthropic")

    routes = {}
    for model in MODELS:
        routes[model] = {
            "backend": "anthropic",
            "url": url,
            "api_key": api_key,
            "original_model": model,
            "api_format": "anthropic",
            "timeout": timeout,
        }
    for alias, target in ALIASES.items():
        if target in routes:
            routes[alias] = {**routes[target], "original_model": target}
    return routes
