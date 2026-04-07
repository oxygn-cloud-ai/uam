"""RunPod discovery — finds running vLLM pods via GraphQL API."""

import logging
import re

import aiohttp

from uam.config import resolve_key

logger = logging.getLogger("uam.discovery.runpod")

RUNPOD_GRAPHQL = "https://api.runpod.io/graphql"
PODS_QUERY = '{"query":"{ myself { pods { id name desiredStatus ports imageName env } } }"}'


async def discover_runpod(config: dict, session: aiohttp.ClientSession) -> dict[str, dict]:
    """Discover models from running RunPod pods. Returns model_id -> route dict."""
    rp_config = config.get("runpod", {})
    routes = {}

    for account_name, account in rp_config.get("accounts", {}).items():
        api_key = resolve_key(account.get("api_key_env", ""))
        if not api_key:
            logger.warning(f"[runpod:{account_name}] no API key in env, skipping")
            continue

        try:
            async with session.post(
                RUNPOD_GRAPHQL,
                data=PODS_QUERY,
                headers={
                    "Content-Type": "application/json",
                    "Authorization": f"Bearer {api_key}",
                },
            ) as resp:
                data = await resp.json()

            pods = data.get("data", {}).get("myself", {}).get("pods", [])
            for pod in pods:
                if pod.get("desiredStatus") != "RUNNING":
                    continue

                ports = pod.get("ports") or ""
                if isinstance(ports, list):
                    ports = " ".join(ports)
                port_tokens = re.split(r'[\s,/]+', ports)
                if "8000" not in port_tokens:
                    continue

                pod_id = pod["id"]
                pod_name = pod.get("name", pod_id)
                proxy_url = f"https://{pod_id}-8000.proxy.runpod.net"

                # Parse env — GraphQL returns list of "KEY=VALUE" strings
                raw_env = pod.get("env") or []
                if isinstance(raw_env, list):
                    env = {}
                    for item in raw_env:
                        if "=" in item:
                            k, v = item.split("=", 1)
                            env[k] = v
                else:
                    env = raw_env

                # Resolve vLLM API key
                vllm_key_template = env.get("VLLM_API_KEY", "")
                if "$RUNPOD_POD_ID" in vllm_key_template:
                    vllm_key = vllm_key_template.replace("$RUNPOD_POD_ID", pod_id)
                elif vllm_key_template:
                    vllm_key = vllm_key_template
                else:
                    vllm_key = ""

                # Probe pod for models
                try:
                    headers = {}
                    if vllm_key:
                        headers["Authorization"] = f"Bearer {vllm_key}"
                    async with session.get(
                        f"{proxy_url}/v1/models",
                        headers=headers,
                        timeout=aiohttp.ClientTimeout(total=10),
                    ) as model_resp:
                        model_data = await model_resp.json()

                    for m in model_data.get("data", []):
                        model_id = m["id"]
                        safe_pod = pod_name.replace(" ", "-").lower()
                        route_key = f"runpod:{safe_pod}/{model_id}"
                        routes[route_key] = {
                            "backend": "runpod",
                            "url": proxy_url,
                            "api_key": vllm_key,
                            "original_model": model_id,
                        }
                        logger.info(f"[runpod:{account_name}] {route_key}")
                except Exception as e:
                    logger.error(f"[runpod:{account_name}] failed to probe {pod_name}: {e}")

        except Exception as e:
            logger.error(f"[runpod:{account_name}] discovery error: {e}")

    return routes
