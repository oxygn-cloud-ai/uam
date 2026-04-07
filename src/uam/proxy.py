"""HTTP proxy handlers — Anthropic Messages API pass-through with model swapping."""

import json
import logging
import time

from aiohttp import web

logger = logging.getLogger("uam.proxy")

from uam.router import ModelRouter
from uam.state import load_state, save_state, is_enabled, get_default
from uam.translate import (
    anthropic_to_openai,
    openai_to_anthropic,
    openai_stream_to_anthropic_stream,
    make_anthropic_stream_start,
)

# State cache — avoid disk I/O on every request
_state_cache: dict = {}
_state_cache_time: float = 0
_STATE_CACHE_TTL: float = 5.0  # seconds


def _get_state() -> dict:
    """Get model state, using cache if fresh."""
    global _state_cache, _state_cache_time
    now = time.monotonic()
    if now - _state_cache_time > _STATE_CACHE_TTL or not _state_cache:
        _state_cache = load_state()
        _state_cache_time = now
    return _state_cache


def _invalidate_state_cache() -> None:
    """Force reload state from disk on next access."""
    global _state_cache_time
    _state_cache_time = 0


def _openai_chat_url(route: dict) -> str:
    """Build the OpenAI-compatible chat completions URL for a route."""
    base_url = route["url"].rstrip("/")
    # If base already ends with /v1, just append /chat/completions
    if base_url.endswith("/v1"):
        return f"{base_url}/chat/completions"
    # Otherwise add /v1/chat/completions
    return f"{base_url}/v1/chat/completions"


def create_app(router: ModelRouter) -> web.Application:
    """Create the aiohttp application with all routes."""
    app = web.Application()
    app["router"] = router

    app.router.add_post("/v1/messages", handle_messages)
    app.router.add_post("/v1/messages/count_tokens", handle_count_tokens)
    app.router.add_post("/v1/messages/ask", handle_ask)
    app.router.add_get("/v1/models", handle_models)
    app.router.add_post("/refresh", handle_refresh)
    app.router.add_get("/health", handle_health)
    app.router.add_get("/state", handle_get_state)
    app.router.add_post("/state", handle_post_state)

    return app


def _needs_translation(route: dict) -> bool:
    """Check if a route needs Anthropic ↔ OpenAI format translation."""
    if route.get("api_format") == "anthropic":
        return False  # Native Anthropic API — no translation needed
    return route["backend"] != "anthropic"


def _build_upstream_headers(request: web.Request | None, route: dict) -> dict:
    """Build headers for the upstream request based on backend type."""
    headers = {"Content-Type": "application/json"}

    if route["backend"] == "anthropic":
        # Forward Anthropic-specific headers
        if request:
            for h in ["anthropic-version", "anthropic-beta"]:
                if h in request.headers:
                    headers[h] = request.headers[h]
        if "anthropic-version" not in headers:
            headers["anthropic-version"] = "2023-06-01"
        headers["X-Api-Key"] = route["api_key"]
    elif route["api_key"]:
        headers["Authorization"] = f"Bearer {route['api_key']}"

    return headers


def _resolve_default_swap(router: ModelRouter, model: str) -> tuple[dict | None, str]:
    """Resolve model with default swap logic.

    If the incoming model is a Claude model but the default is set to something else,
    swap to the default model.

    Returns (route, effective_model_id) or (None, model) on failure.
    """
    state = _get_state()
    default = get_default(state)

    # If there's a default set and the request is for a Claude model, swap it
    if default and model.startswith("claude-") and not default.startswith("claude-"):
        # Swap to default model
        if not is_enabled(default, state):
            # Default is disabled — fall through to normal resolution
            pass
        else:
            route = router.resolve(default)
            if route:
                return route, default

    # Normal resolution
    route = router.resolve(model)
    if route:
        # Check if model is enabled (skip check for Claude passthrough)
        if model in state.get("models", {}) and not is_enabled(model, state):
            return None, model
        return route, model

    return None, model


async def handle_messages(request: web.Request) -> web.StreamResponse:
    """Main messages endpoint with default model swap and format translation."""
    router: ModelRouter = request.app["router"]
    body = await request.read()
    try:
        payload = json.loads(body)
    except (json.JSONDecodeError, ValueError):
        return web.json_response(
            {"error": {"type": "invalid_request_error", "message": "Invalid JSON body"}},
            status=400,
        )
    model = payload.get("model", "")

    route, effective_model = _resolve_default_swap(router, model)
    if not route:
        return web.json_response(
            {"error": {"type": "invalid_request_error",
                       "message": f"Unknown or disabled model: {model}"}},
            status=400,
            headers={"x-should-retry": "false"},
        )

    logger.debug(f"Route: {model} -> {effective_model} via {route['backend']}")

    is_stream = payload.get("stream", False)

    if _needs_translation(route):
        return await _proxy_with_translation(
            request, router, route, payload, effective_model, is_stream
        )
    else:
        return await _proxy_anthropic_native(
            request, router, route, payload, is_stream
        )


async def _proxy_anthropic_native(
    request: web.Request,
    router: ModelRouter,
    route: dict,
    payload: dict,
    is_stream: bool,
) -> web.StreamResponse:
    """Forward request directly to Anthropic-compatible backend."""
    payload["model"] = route["original_model"]
    headers = _build_upstream_headers(request, route)
    target_url = f"{route['url']}/v1/messages"

    try:
        async with router.session.post(
            target_url, data=json.dumps(payload), headers=headers
        ) as upstream:
            retry_hdrs = _retry_headers(upstream.status, dict(upstream.headers)) if upstream.status >= 400 else {}
            if is_stream:
                resp_headers = {
                    "Content-Type": upstream.headers.get(
                        "Content-Type", "text/event-stream"
                    ),
                    "Cache-Control": "no-cache",
                }
                resp_headers.update(retry_hdrs)
                resp = web.StreamResponse(
                    status=upstream.status,
                    headers=resp_headers,
                )
                _forward_response_headers(upstream, resp)
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
                    content_type=upstream.headers.get(
                        "Content-Type", "application/json"
                    ),
                    headers=retry_hdrs,
                )
                _forward_response_headers(upstream, resp)
                return resp
    except Exception as e:
        return web.json_response(
            {"error": {"type": "proxy_error", "message": str(e)}},
            status=502,
            headers={"x-should-retry": "false"},
        )


async def _proxy_with_translation(
    request: web.Request,
    router: ModelRouter,
    route: dict,
    payload: dict,
    effective_model: str,
    is_stream: bool,
) -> web.StreamResponse:
    """Forward request to OpenAI-compatible backend with format translation."""
    # Translate request
    try:
        openai_payload = anthropic_to_openai(payload)
    except Exception as e:
        logger.error(f"Translation error: {e}")
        return web.json_response(
            {"error": {"type": "translation_error", "message": str(e)}},
            status=502,
            headers={"x-should-retry": "false"},
        )
    openai_payload["model"] = route["original_model"]
    openai_payload["stream"] = is_stream

    headers = _build_upstream_headers(None, route)

    target_url = _openai_chat_url(route)

    try:
        async with router.session.post(
            target_url, data=json.dumps(openai_payload), headers=headers
        ) as upstream:
            retry_hdrs = _retry_headers(upstream.status, dict(upstream.headers)) if upstream.status >= 400 else {}
            if is_stream:
                resp_headers = {
                    "Content-Type": "text/event-stream",
                    "Cache-Control": "no-cache",
                }
                resp_headers.update(retry_hdrs)
                resp = web.StreamResponse(
                    status=200 if upstream.status < 400 else upstream.status,
                    headers=resp_headers,
                )
                await resp.prepare(request)

                if upstream.status >= 400:
                    error_body = await upstream.read()
                    error_resp = _make_anthropic_error(error_body, upstream.status)
                    await resp.write(error_resp)
                    await resp.write_eof()
                    return resp

                # Send Anthropic stream start
                await resp.write(make_anthropic_stream_start(effective_model))

                # Line-buffered reading: upstream.content yields raw
                # byte chunks, not SSE lines. Buffer and split on \n.
                buffer = b""
                async for chunk in upstream.content.iter_any():
                    buffer += chunk
                    while b"\n" in buffer:
                        line, buffer = buffer.split(b"\n", 1)
                        if not line.strip():
                            continue
                        converted = openai_stream_to_anthropic_stream(
                            line, effective_model
                        )
                        if converted:
                            await resp.write(converted)

                # Process any remaining data in buffer
                if buffer.strip():
                    converted = openai_stream_to_anthropic_stream(
                        buffer, effective_model
                    )
                    if converted:
                        await resp.write(converted)

                await resp.write_eof()
                return resp
            else:
                if upstream.status >= 400:
                    error_body = await upstream.read()
                    return web.Response(
                        body=_make_anthropic_error(error_body, upstream.status),
                        status=upstream.status,
                        content_type="application/json",
                        headers=retry_hdrs,
                    )

                data = await upstream.json()
                anthropic_resp = openai_to_anthropic(data, effective_model)
                return web.json_response(anthropic_resp)
    except Exception as e:
        return web.json_response(
            {"error": {"type": "proxy_error", "message": str(e)}},
            status=502,
            headers={"x-should-retry": "false"},
        )


def _make_anthropic_error(error_body: bytes, status: int) -> bytes:
    """Wrap an upstream error in Anthropic error format."""
    try:
        err = json.loads(error_body)
        msg = err.get("error", {}).get("message", str(err))
    except (json.JSONDecodeError, AttributeError):
        msg = error_body.decode("utf-8", errors="replace")

    return json.dumps({
        "error": {"type": "api_error", "message": msg}
    }).encode()


def _retry_headers(status: int, upstream_headers: dict | None = None) -> dict:
    """Build retry-signal headers based on upstream HTTP status.

    Claude Code already retries 10 times on its own. We don't retry inside the
    proxy — instead we propagate x-should-retry and retry-after* so the caller
    can make informed decisions.
    """
    if status in (503, 429):
        headers: dict[str, str] = {"x-should-retry": "true"}
        if upstream_headers:
            for h in ("retry-after", "retry-after-ms"):
                val = upstream_headers.get(h)
                if val:
                    headers[h] = val
        return headers
    if status in (400, 401, 403, 404):
        return {"x-should-retry": "false"}
    return {}


def _forward_response_headers(upstream, resp: web.StreamResponse) -> None:
    """Forward relevant response headers from upstream."""
    for h in upstream.headers:
        hl = h.lower()
        if hl.startswith("x-") or hl.startswith("anthropic-") or hl == "request-id":
            resp.headers[h] = upstream.headers[h]


async def handle_ask(request: web.Request) -> web.StreamResponse:
    """One-shot query to a specific model. Used by the UserPromptSubmit hook."""
    router: ModelRouter = request.app["router"]
    body = await request.read()
    try:
        payload = json.loads(body)
    except (json.JSONDecodeError, ValueError):
        return web.json_response(
            {"error": {"type": "invalid_request_error", "message": "Invalid JSON body"}},
            status=400,
        )
    model = payload.get("model", "")

    state = _get_state()

    # Check if model is enabled
    if model in state.get("models", {}) and not is_enabled(model, state):
        return web.json_response(
            {"error": {"type": "model_disabled",
                       "message": f"{model} is off",
                       "default": get_default(state)}},
            status=403,
        )

    route = router.resolve(model)
    if not route:
        return web.json_response(
            {"error": {"type": "model_not_found",
                       "message": f"{model} is not configured",
                       "default": get_default(state)}},
            status=404,
        )

    # Always non-streaming for ask
    payload["stream"] = False

    if _needs_translation(route):
        openai_payload = anthropic_to_openai(payload)
        openai_payload["model"] = route["original_model"]
        openai_payload["stream"] = False
        headers = _build_upstream_headers(None, route)
        target_url = _openai_chat_url(route)

        try:
            async with router.session.post(
                target_url, data=json.dumps(openai_payload), headers=headers
            ) as upstream:
                if upstream.status >= 400:
                    error_body = await upstream.read()
                    retry_hdrs = _retry_headers(upstream.status, dict(upstream.headers))
                    return web.Response(
                        body=error_body, status=upstream.status,
                        content_type="application/json",
                        headers=retry_hdrs,
                    )
                data = await upstream.json()
                anthropic_resp = openai_to_anthropic(data, model)
                return web.json_response(anthropic_resp)
        except Exception as e:
            return web.json_response(
                {"error": {"type": "proxy_error", "message": str(e)}},
                status=502,
                headers={"x-should-retry": "false"},
            )
    else:
        payload["model"] = route["original_model"]
        # Pass None for request — hook requests don't have Anthropic headers
        headers = _build_upstream_headers(None, route)
        target_url = f"{route['url']}/v1/messages"

        try:
            async with router.session.post(
                target_url, data=json.dumps(payload), headers=headers
            ) as upstream:
                retry_hdrs = _retry_headers(upstream.status, dict(upstream.headers)) if upstream.status >= 400 else {}
                data = await upstream.read()
                return web.Response(
                    body=data, status=upstream.status,
                    content_type="application/json",
                    headers=retry_hdrs,
                )
        except Exception as e:
            return web.json_response(
                {"error": {"type": "proxy_error", "message": str(e)}},
                status=502,
                headers={"x-should-retry": "false"},
            )


async def handle_count_tokens(request: web.Request) -> web.Response:
    router: ModelRouter = request.app["router"]
    body = await request.read()
    try:
        payload = json.loads(body)
    except (json.JSONDecodeError, ValueError):
        return web.json_response(
            {"error": {"type": "invalid_request_error", "message": "Invalid JSON body"}},
            status=400,
        )
    model = payload.get("model", "")

    route, effective_model = _resolve_default_swap(router, model)
    if not route:
        return web.json_response(
            {"error": {"type": "invalid_request_error",
                       "message": f"Unknown model: {model}"}},
            status=400,
        )

    if _needs_translation(route):
        # Non-Anthropic backends don't support count_tokens.
        # Return rough estimate: ~4 chars per token for English text.
        text_len = len(json.dumps(payload.get("messages", [])))
        system_len = len(str(payload.get("system", "")))
        estimated = (text_len + system_len) // 4
        return web.json_response({"input_tokens": estimated})

    payload["model"] = route["original_model"]
    headers = _build_upstream_headers(request, route)
    target_url = f"{route['url']}/v1/messages/count_tokens"

    try:
        async with router.session.post(
            target_url, data=json.dumps(payload), headers=headers
        ) as upstream:
            retry_hdrs = _retry_headers(upstream.status, dict(upstream.headers)) if upstream.status >= 400 else {}
            data = await upstream.read()
            return web.Response(
                body=data, status=upstream.status, content_type="application/json",
                headers=retry_hdrs,
            )
    except Exception as e:
        return web.json_response(
            {"error": {"type": "proxy_error", "message": str(e)}},
            status=502,
            headers={"x-should-retry": "false"},
        )


async def handle_models(request: web.Request) -> web.Response:
    router: ModelRouter = request.app["router"]
    state = _get_state()
    models = []
    for m in router.list_models():
        enabled = is_enabled(m["id"], state)
        models.append({
            "id": m["id"],
            "object": "model",
            "owned_by": m["backend"],
            "original_model": m["original_model"],
            "enabled": enabled,
        })
    return web.json_response({
        "object": "list",
        "data": models,
        "default": get_default(state),
    })


async def handle_refresh(request: web.Request) -> web.Response:
    router: ModelRouter = request.app["router"]
    print("\nRefreshing model discovery...")
    await router.refresh()
    _invalidate_state_cache()
    count = router.model_count()
    print(f"Discovery complete: {count} models available\n")
    return web.json_response({"status": "ok", "models": count})


async def handle_health(request: web.Request) -> web.Response:
    router: ModelRouter = request.app["router"]
    state = _get_state()
    return web.json_response({
        "status": "ok",
        "models": router.model_count(),
        "default": get_default(state),
    })


async def handle_get_state(request: web.Request) -> web.Response:
    """Return current model state."""
    state = _get_state()
    return web.json_response(state)


async def handle_post_state(request: web.Request) -> web.Response:
    """Update model state (on/off toggles, default, aliases)."""
    body = await request.read()
    try:
        updates = json.loads(body)
    except json.JSONDecodeError:
        return web.json_response(
            {"error": {"type": "invalid_request_error", "message": "Invalid JSON"}},
            status=400,
        )

    if not isinstance(updates, dict):
        return web.json_response(
            {"error": {"type": "invalid_request_error", "message": "Expected JSON object"}},
            status=400,
        )

    state = load_state()

    if "default" in updates:
        state["default"] = updates["default"]

    if "aliases" in updates:
        state["aliases"].update(updates["aliases"])

    if "models" in updates:
        for model_id, model_state in updates["models"].items():
            if model_id in state.get("models", {}):
                state["models"][model_id].update(model_state)
            else:
                state["models"][model_id] = model_state

    save_state(state)
    _invalidate_state_cache()
    return web.json_response({"status": "ok", "state": state})
