"""Local model discovery — probes localhost ports for Ollama, vLLM, etc."""

import logging

import aiohttp

logger = logging.getLogger("uam.discovery.local")


async def discover_local(config: dict, session: aiohttp.ClientSession) -> dict[str, dict]:
    """Discover models from local servers. Returns model_id -> route dict."""
    local_config = config.get("local", {})
    routes = {}

    # Build list of base URLs to probe
    urls_to_probe: list[str] = []

    # Localhost port probing
    for port in local_config.get("probe_ports", []):
        urls_to_probe.append(f"http://127.0.0.1:{port}")

    # Explicit servers (remote Ollama, vLLM, etc.)
    for server in local_config.get("servers", []):
        url = server if isinstance(server, str) else server.get("url", "")
        if url:
            urls_to_probe.append(url.rstrip("/"))

    for url in urls_to_probe:
        label = url.replace("http://", "").replace("https://", "")
        await _probe_server(url, label, routes, session)

    return routes


async def _probe_server(
    url: str, label: str, routes: dict, session: aiohttp.ClientSession
) -> None:
    """Probe a single server URL for models."""
    for path in ["/v1/models", "/api/tags"]:
        try:
            async with session.get(
                f"{url}{path}",
                timeout=aiohttp.ClientTimeout(total=5),
            ) as resp:
                data = await resp.json()

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
                    }
                    print(f"  [local:{label}] {route_key}")
            break  # Found models on this server
        except Exception:
            continue
