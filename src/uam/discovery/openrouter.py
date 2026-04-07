"""OpenRouter discovery — fetches available models from OpenRouter API."""

import logging

import aiohttp

from uam.config import get_backend_timeout, resolve_key

logger = logging.getLogger("uam.discovery.openrouter")


async def discover_openrouter(config: dict, session: aiohttp.ClientSession) -> dict[str, dict]:
    """Discover models from OpenRouter. Returns model_id -> route dict."""
    or_config = config.get("openrouter", {})
    api_key = resolve_key(or_config.get("api_key_env", ""))
    if not api_key:
        logger.warning("[openrouter] no API key in env, skipping")
        return {}

    url = or_config.get("url", "https://openrouter.ai/api")
    timeout = get_backend_timeout(config, "openrouter")
    routes = {}

    try:
        async with session.get(
            f"{url}/v1/models",
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=aiohttp.ClientTimeout(total=15),
        ) as resp:
            data = await resp.json()

        for m in data.get("data", []):
            model_id = m["id"]
            route_key = f"openrouter:{model_id}"
            routes[route_key] = {
                "backend": "openrouter",
                "url": url,
                "api_key": api_key,
                "original_model": model_id,
                "api_format": "openai",
                "timeout": timeout,
            }
        logger.info(f"[openrouter] discovered {len(routes)} models")
    except Exception as e:
        logger.error(f"[openrouter] discovery error: {e}")

    return routes
