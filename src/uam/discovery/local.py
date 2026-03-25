"""Local model discovery — probes localhost ports for Ollama, vLLM, etc."""

import aiohttp


async def discover_local(config: dict, session: aiohttp.ClientSession) -> dict[str, dict]:
    """Discover models from local servers. Returns model_id -> route dict."""
    local_config = config.get("local", {})
    routes = {}

    for port in local_config.get("probe_ports", []):
        url = f"http://127.0.0.1:{port}"

        # Try OpenAI-compatible endpoint first, then Ollama
        for path in ["/v1/models", "/api/tags"]:
            try:
                async with session.get(
                    f"{url}{path}",
                    timeout=aiohttp.ClientTimeout(total=3),
                ) as resp:
                    data = await resp.json()

                if path == "/api/tags":
                    # Ollama format
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
                            print(f"  [local:{port}] {route_key}")
                else:
                    # OpenAI-compatible format
                    for m in data.get("data", []):
                        model_id = m["id"]
                        route_key = f"local:{model_id}"
                        routes[route_key] = {
                            "backend": "local",
                            "url": url,
                            "api_key": "",
                            "original_model": model_id,
                        }
                        print(f"  [local:{port}] {route_key}")
                break  # Found models on this port
            except Exception:
                continue

    return routes
