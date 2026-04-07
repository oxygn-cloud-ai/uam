"""Local model discovery — probes localhost ports for Ollama, vLLM, etc."""

import json
import logging

import aiohttp

from uam.config import get_backend_timeout

logger = logging.getLogger("uam.discovery.local")


async def discover_local(config: dict, session: aiohttp.ClientSession) -> dict[str, dict]:
    """Discover models from local servers. Returns model_id -> route dict."""
    local_config = config.get("local", {})
    routes = {}
    timeout = get_backend_timeout(config, "local")

    # Build list of (base_url, api_format) to probe
    urls_to_probe: list[tuple[str, str]] = []

    # Localhost port probing — always openai format
    for port in local_config.get("probe_ports", []):
        urls_to_probe.append((f"http://127.0.0.1:{port}", "openai"))

    # Explicit servers (remote Ollama, vLLM, etc.)
    for server in local_config.get("servers", []):
        if isinstance(server, str):
            url = server
            api_format = "openai"
        else:
            url = server.get("url", "")
            api_format = server.get("api_format", "openai")
        if url:
            urls_to_probe.append((url.rstrip("/"), api_format))

    for url, api_format in urls_to_probe:
        label = url.replace("http://", "").replace("https://", "")
        await _probe_server(url, label, routes, session, api_format, timeout)

    return routes


async def _probe_server(
    url: str,
    label: str,
    routes: dict,
    session: aiohttp.ClientSession,
    api_format: str = "openai",
    timeout: int = 120,
) -> None:
    """Probe a single server URL for models."""
    for path in ["/v1/models", "/api/tags"]:
        endpoint = f"{url}{path}"
        try:
            async with session.get(
                endpoint,
                timeout=aiohttp.ClientTimeout(total=5),
            ) as resp:
                try:
                    data = await resp.json()
                except (json.JSONDecodeError, aiohttp.ContentTypeError, ValueError) as e:
                    logger.warning(f"Failed to parse {endpoint}: {e}")
                    continue

            if path == "/api/tags":
                for m in data.get("models", []):
                    model_id = m.get("name", m.get("model", ""))
                    if model_id:
                        route_key = f"local:{model_id}"
                        routes[route_key] = {
                            "backend": "local",
                            "url": url,
                            "api_key": "",
                            "original_model": model_id,
                            "api_format": api_format,
                            "timeout": timeout,
                        }
                        logger.info(f"[local:{label}] {route_key}")
            else:
                for m in data.get("data", []):
                    model_id = m["id"]
                    route_key = f"local:{model_id}"
                    routes[route_key] = {
                        "backend": "local",
                        "url": url,
                        "api_key": "",
                        "original_model": model_id,
                        "api_format": api_format,
                        "timeout": timeout,
                    }
                    logger.info(f"[local:{label}] {route_key}")
            break  # Found models on this server
        except Exception:
            continue
