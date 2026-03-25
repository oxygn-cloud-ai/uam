"""HTTP proxy handlers — Anthropic Messages API pass-through."""

import json

from aiohttp import web

from uam.router import ModelRouter


def create_app(router: ModelRouter) -> web.Application:
    """Create the aiohttp application with all routes."""
    app = web.Application()
    app["router"] = router

    app.router.add_post("/v1/messages", handle_messages)
    app.router.add_post("/v1/messages/count_tokens", handle_count_tokens)
    app.router.add_get("/v1/models", handle_models)
    app.router.add_post("/refresh", handle_refresh)
    app.router.add_get("/health", handle_health)

    return app


def _build_upstream_headers(request: web.Request, route: dict) -> dict:
    """Build headers for the upstream request based on backend type."""
    headers = {"Content-Type": "application/json"}

    # Forward Anthropic-specific headers
    for h in ["anthropic-version", "anthropic-beta"]:
        if h in request.headers:
            headers[h] = request.headers[h]

    if "anthropic-version" not in headers:
        headers["anthropic-version"] = "2023-06-01"

    # Auth per backend
    if route["backend"] == "anthropic":
        headers["X-Api-Key"] = route["api_key"]
    elif route["api_key"]:
        headers["Authorization"] = f"Bearer {route['api_key']}"

    return headers


async def handle_messages(request: web.Request) -> web.StreamResponse:
    router: ModelRouter = request.app["router"]
    body = await request.read()
    payload = json.loads(body)
    model = payload.get("model", "")

    route = router.resolve(model)
    if not route:
        return web.json_response(
            {"error": {"type": "invalid_request_error", "message": f"Unknown model: {model}"}},
            status=400,
        )

    payload["model"] = route["original_model"]
    headers = _build_upstream_headers(request, route)
    target_url = f"{route['url']}/v1/messages"
    is_stream = payload.get("stream", False)

    try:
        async with router.session.post(
            target_url, data=json.dumps(payload), headers=headers
        ) as upstream:
            if is_stream:
                resp = web.StreamResponse(
                    status=upstream.status,
                    headers={
                        "Content-Type": upstream.headers.get("Content-Type", "text/event-stream"),
                        "Cache-Control": "no-cache",
                    },
                )
                for h in upstream.headers:
                    hl = h.lower()
                    if hl.startswith("x-") or hl.startswith("anthropic-") or hl == "request-id":
                        resp.headers[h] = upstream.headers[h]
                await resp.prepare(request)
                async for chunk in upstream.content.iter_any():
                    await resp.write(chunk)
                await resp.write_eof()
                return resp
            else:
                data = await upstream.read()
                resp = web.Response(
                    body=data,
                    status=upstream.status,
                    content_type=upstream.headers.get("Content-Type", "application/json"),
                )
                for h in upstream.headers:
                    hl = h.lower()
                    if hl.startswith("x-") or hl.startswith("anthropic-") or hl == "request-id":
                        resp.headers[h] = upstream.headers[h]
                return resp
    except Exception as e:
        return web.json_response(
            {"error": {"type": "proxy_error", "message": str(e)}},
            status=502,
        )


async def handle_count_tokens(request: web.Request) -> web.Response:
    router: ModelRouter = request.app["router"]
    body = await request.read()
    payload = json.loads(body)
    model = payload.get("model", "")

    route = router.resolve(model)
    if not route:
        return web.json_response(
            {"error": {"type": "invalid_request_error", "message": f"Unknown model: {model}"}},
            status=400,
        )

    payload["model"] = route["original_model"]
    headers = _build_upstream_headers(request, route)
    target_url = f"{route['url']}/v1/messages/count_tokens"

    try:
        async with router.session.post(
            target_url, data=json.dumps(payload), headers=headers
        ) as upstream:
            data = await upstream.read()
            return web.Response(
                body=data,
                status=upstream.status,
                content_type="application/json",
            )
    except Exception as e:
        return web.json_response(
            {"error": {"type": "proxy_error", "message": str(e)}},
            status=502,
        )


async def handle_models(request: web.Request) -> web.Response:
    router: ModelRouter = request.app["router"]
    models = [
        {
            "id": m["id"],
            "object": "model",
            "owned_by": m["backend"],
            "original_model": m["original_model"],
        }
        for m in router.list_models()
    ]
    return web.json_response({"object": "list", "data": models})


async def handle_refresh(request: web.Request) -> web.Response:
    router: ModelRouter = request.app["router"]
    print("\nRefreshing model discovery...")
    await router.refresh()
    count = router.model_count()
    print(f"Discovery complete: {count} models available\n")
    return web.json_response({"status": "ok", "models": count})


async def handle_health(request: web.Request) -> web.Response:
    router: ModelRouter = request.app["router"]
    return web.json_response({"status": "ok", "models": router.model_count()})
