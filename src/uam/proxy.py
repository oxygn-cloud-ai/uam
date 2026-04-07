"""HTTP proxy handlers — Anthropic Messages API pass-through with model swapping."""

import asyncio
import json
import logging
import re
import time

import aiohttp
from aiohttp import web

from uam.log import redact_headers
from uam.router import ModelRouter
from uam.state import load_state, save_state, is_enabled, get_default, write_env_file
from uam.translate import (
    anthropic_to_openai,
    openai_to_anthropic,
    openai_stream_to_anthropic_stream,
    make_anthropic_stream_start,
)

# L1: logger declaration goes after the import block (style nit; isort/ruff
# previously flagged this).
logger = logging.getLogger("uam.proxy")

# SEC-006: Serialize all load → mutate → save → write_env_file flows so two
# concurrent POST /state requests cannot lose updates by interleaving.
_state_write_lock = asyncio.Lock()

# SEC-002: Allowed Host header values. The proxy only ever listens on
# localhost; rejecting any other Host blocks DNS-rebinding from a browser
# tab that has resolved an attacker-controlled hostname to 127.0.0.1.
_ALLOWED_HOSTS = {
    "127.0.0.1",
    "localhost",
}

# SEC-008: Patterns scrubbed from upstream error messages before they are
# forwarded to the client. A misbehaving upstream that echoes the request's
# Authorization / X-Api-Key header in its error body must not leak the user's
# key back through the proxy.
_SECRET_HEADER_RE = re.compile(
    # Match "Authorization: Bearer <token>", "X-Api-Key: <token>",
    # "Bearer <token>", and similar variants. The trailing token group is
    # \S+ but we also consume an optional second \S+ so "Authorization:
    # Bearer sk-..." is fully eaten in one match.
    r"(authorization|x[-_]api[-_]key)\s*[:=]\s*(?:bearer\s+)?\S+"
    r"|bearer\s+\S+",
    re.IGNORECASE,
)

# State cache — avoid disk I/O on every request.
#
# Concurrency model: uam runs in a single asyncio event loop (aiohttp), so
# reads/writes of module globals are atomic at the bytecode level in CPython
# — no true data race. The remaining concern is *eventual consistency*: a
# request that starts reading _state_cache right before _invalidate_state_cache
# fires may observe the pre-invalidation snapshot. This is bounded by the
# 5-second TTL: in the worst case, a mutation takes up to 5s to be reflected
# for concurrent in-flight readers. This is intentional — callers of POST
# /state and POST /refresh should not rely on read-your-writes semantics
# across concurrent requests. If stronger guarantees are needed, wrap cache
# reads/writes in an asyncio.Lock.
_state_cache: dict = {}
_state_cache_time: float = 0
_STATE_CACHE_TTL: float = 5.0  # seconds


def _get_state() -> dict:
    """Get model state, using cache if fresh.

    Eventually consistent with _invalidate_state_cache — see module
    comment above the cache globals.
    """
    global _state_cache, _state_cache_time
    now = time.monotonic()
    if now - _state_cache_time > _STATE_CACHE_TTL or not _state_cache:
        _state_cache = load_state()
        _state_cache_time = now
    return _state_cache


def _invalidate_state_cache() -> None:
    """Force reload state from disk on next access.

    Eventually consistent: concurrent readers may still see the old
    snapshot until the next _get_state() call. Bounded by _STATE_CACHE_TTL.
    """
    global _state_cache, _state_cache_time
    _state_cache_time = 0
    _state_cache = {}


def _route_timeout(route: dict) -> aiohttp.ClientTimeout | None:
    """Return per-route ClientTimeout, or None to fall through to session default.

    H1 fix: per-backend timeouts from config are now actually applied to each
    upstream request rather than being silently ignored in favor of the
    600s session default.
    """
    t = route.get("timeout")
    if t is None:
        return None
    return aiohttp.ClientTimeout(total=int(t))


def _openai_chat_url(route: dict) -> str:
    """Build the OpenAI-compatible chat completions URL for a route."""
    base_url = route["url"].rstrip("/")
    # If base already ends with /v1, just append /chat/completions
    if base_url.endswith("/v1"):
        return f"{base_url}/chat/completions"
    # Otherwise add /v1/chat/completions
    return f"{base_url}/v1/chat/completions"


@web.middleware
async def host_header_middleware(request: web.Request, handler):
    """SEC-002: Reject requests whose Host header is not localhost.

    Defends against DNS rebinding attacks where a malicious page in the
    user's browser binds an attacker-controlled hostname to 127.0.0.1 and
    POSTs to the proxy. aiohttp's request.host is `Host` header verbatim
    when present, otherwise the listening socket address.
    """
    host = (request.host or "").split(":", 1)[0].lower()
    if host not in _ALLOWED_HOSTS:
        return web.json_response(
            {"error": {"type": "forbidden",
                       "message": "Host header not allowed"}},
            status=403,
        )
    return await handler(request)


def create_app(router: ModelRouter) -> web.Application:
    """Create the aiohttp application with all routes."""
    app = web.Application(middlewares=[host_header_middleware])
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

    # SEC-004: Always log via redact_headers so any future "log the request"
    # change cannot accidentally leak Authorization / X-Api-Key.
    logger.debug("Upstream headers: %s", redact_headers(headers))

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
            # M6: previously a silent fall-through. Log a warning so the user
            # has observability that their swap was bypassed.
            logger.warning(
                "Default model %s is disabled — falling through to %s",
                default,
                model,
            )
        else:
            route = router.resolve(default)
            if route:
                return route, default

    # Normal resolution
    route = router.resolve(model)
    if route:
        # SEC-003: previously the enabled check was gated on
        # `model in state.models`, so an unknown model id silently bypassed
        # on/off enforcement and fell through to the Anthropic default
        # backend with the user's real key. We now apply the enabled check
        # to ALL resolved models — known or not.
        if not is_enabled(model, state):
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

    # perf M3: lazy %-formatting so f-string interpolation is skipped when
    # debug is disabled (which is the production default).
    logger.debug("Route: %s -> %s via %s", model, effective_model, route["backend"])

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
            target_url, data=json.dumps(payload), headers=headers,
            timeout=_route_timeout(route),
        ) as upstream:
            retry_hdrs = _retry_headers(upstream.status, upstream.headers) if upstream.status >= 400 else {}
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
        # SEC-010: do not leak str(e) — it may contain pod ids, full URLs,
        # or even auth headers from broken upstreams. Log full detail to
        # the rotating log file and return a generic message to the client.
        logger.exception("Upstream proxy error: %s", e)
        return web.json_response(
            {"error": {"type": "proxy_error", "message": "upstream connection failed"}},
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
            target_url, data=json.dumps(openai_payload), headers=headers,
            timeout=_route_timeout(route),
        ) as upstream:
            retry_hdrs = _retry_headers(upstream.status, upstream.headers) if upstream.status >= 400 else {}
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
                #
                # perf H1 fix:
                #   - Cap buffer size at 1 MiB so a pathological upstream
                #     sending no newlines cannot OOM the proxy.
                #   - Use buffer.find() with linear scan instead of
                #     `b"\n" in buffer` + split, which was O(n^2) on chunky
                #     streams.
                MAX_BUFFER_SIZE = 1024 * 1024  # 1 MiB max line
                buffer = b""
                buffer_oversize = False
                async for chunk in upstream.content.iter_any():
                    buffer += chunk
                    if len(buffer) > MAX_BUFFER_SIZE:
                        logger.error(
                            "SSE buffer exceeded %d bytes — closing stream",
                            MAX_BUFFER_SIZE,
                        )
                        buffer_oversize = True
                        break
                    while True:
                        idx = buffer.find(b"\n")
                        if idx == -1:
                            break
                        line = buffer[:idx]
                        buffer = buffer[idx + 1:]
                        if not line.strip():
                            continue
                        converted = openai_stream_to_anthropic_stream(
                            line, effective_model
                        )
                        if converted:
                            await resp.write(converted)

                # Process any remaining data in buffer (only if we didn't abort)
                if not buffer_oversize and buffer.strip():
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
                # H2 fix: enable <think> tag extraction for non-streaming
                # responses. Safe — only strips complete balanced tags from
                # the start of text content. Local R1 / DeepSeek style models
                # benefit; other models are unaffected.
                anthropic_resp = openai_to_anthropic(
                    data, effective_model, extract_think_tags=True
                )
                resp = web.json_response(anthropic_resp)
                # L7: forward request-id / anthropic-ratelimit-* / x-* from
                # upstream so Claude Code sees the same headers it would on
                # the native Anthropic path.
                _forward_response_headers(upstream, resp)
                return resp
    except Exception as e:
        # SEC-010: do not leak str(e) — it may contain pod ids, full URLs,
        # or even auth headers from broken upstreams. Log full detail to
        # the rotating log file and return a generic message to the client.
        logger.exception("Upstream proxy error: %s", e)
        return web.json_response(
            {"error": {"type": "proxy_error", "message": "upstream connection failed"}},
            status=502,
            headers={"x-should-retry": "false"},
        )


def _scrub_secrets(text: str) -> str:
    """SEC-008: strip Authorization / X-Api-Key / Bearer tokens from a
    string before forwarding it to the client. A misbehaving upstream that
    echoes the request's auth headers in its error body must not leak the
    user's key back through the proxy."""
    return _SECRET_HEADER_RE.sub("[redacted]", text)


def _make_anthropic_error(error_body: bytes, status: int) -> bytes:
    """Wrap an upstream error in Anthropic error format.

    SEC-008: strips auth headers from the upstream message before forwarding.
    Truncates long messages to 1 KiB to bound information disclosure.
    """
    try:
        err = json.loads(error_body)
        msg = err.get("error", {}).get("message", str(err))
    except (json.JSONDecodeError, AttributeError):
        msg = error_body.decode("utf-8", errors="replace")

    msg = _scrub_secrets(msg)
    if len(msg) > 1024:
        msg = msg[:1024] + "...[truncated]"

    return json.dumps({
        "error": {"type": "api_error", "message": msg}
    }).encode()


def _retry_headers(status: int, upstream_headers=None) -> dict:
    """Build retry-signal headers based on upstream HTTP status.

    Claude Code already retries 10 times on its own. We don't retry inside the
    proxy — instead we propagate x-should-retry and retry-after* so the caller
    can make informed decisions.

    upstream_headers may be a CIMultiDict (from aiohttp upstream.headers) or a
    plain dict. C1 fix: do a case-insensitive lookup so canonical-case
    'Retry-After' (the form most servers actually emit) is not silently dropped.
    """
    if status in (503, 429):
        headers: dict[str, str] = {"x-should-retry": "true"}
        if upstream_headers:
            # Normalize all keys to lowercase for a case-insensitive lookup.
            # Works for both CIMultiDict (already CI) and plain dict.
            try:
                lowered = {str(k).lower(): v for k, v in upstream_headers.items()}
            except AttributeError:
                lowered = {}
            ra = lowered.get("retry-after")
            ram = lowered.get("retry-after-ms")
            if ra:
                headers["retry-after"] = ra
            if ram:
                headers["retry-after-ms"] = ram
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
                target_url, data=json.dumps(openai_payload), headers=headers,
                timeout=_route_timeout(route),
            ) as upstream:
                if upstream.status >= 400:
                    error_body = await upstream.read()
                    retry_hdrs = _retry_headers(upstream.status, upstream.headers)
                    return web.Response(
                        body=error_body, status=upstream.status,
                        content_type="application/json",
                        headers=retry_hdrs,
                    )
                data = await upstream.json()
                anthropic_resp = openai_to_anthropic(data, model)
                return web.json_response(anthropic_resp)
        except Exception as e:
            # SEC-010: log full detail; return generic message.
            logger.exception("Upstream proxy error: %s", e)
            return web.json_response(
                {"error": {"type": "proxy_error", "message": "upstream connection failed"}},
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
                target_url, data=json.dumps(payload), headers=headers,
                timeout=_route_timeout(route),
            ) as upstream:
                retry_hdrs = _retry_headers(upstream.status, upstream.headers) if upstream.status >= 400 else {}
                data = await upstream.read()
                return web.Response(
                    body=data, status=upstream.status,
                    content_type="application/json",
                    headers=retry_hdrs,
                )
        except Exception as e:
            # SEC-010: log full detail; return generic message.
            logger.exception("Upstream proxy error: %s", e)
            return web.json_response(
                {"error": {"type": "proxy_error", "message": "upstream connection failed"}},
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
            target_url, data=json.dumps(payload), headers=headers,
            timeout=_route_timeout(route),
        ) as upstream:
            retry_hdrs = _retry_headers(upstream.status, upstream.headers) if upstream.status >= 400 else {}
            data = await upstream.read()
            return web.Response(
                body=data, status=upstream.status, content_type="application/json",
                headers=retry_hdrs,
            )
    except Exception as e:
        # SEC-010: do not leak str(e) — it may contain pod ids, full URLs,
        # or even auth headers from broken upstreams. Log full detail to
        # the rotating log file and return a generic message to the client.
        logger.exception("Upstream proxy error: %s", e)
        return web.json_response(
            {"error": {"type": "proxy_error", "message": "upstream connection failed"}},
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
    logger.info("Refreshing model discovery...")
    await router.refresh()
    _invalidate_state_cache()
    count = router.model_count()
    logger.info("Discovery complete: %d models available", count)
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

    # SEC-006: serialize the load → mutate → save → write_env_file flow
    # so two concurrent POSTs cannot lose updates by interleaving. Run the
    # synchronous file I/O in a worker thread (perf M1) so the proxy event
    # loop is never blocked even briefly mid-stream.
    async with _state_write_lock:
        state = await asyncio.to_thread(load_state)

        if "default" in updates:
            state["default"] = updates["default"]

        if "aliases" in updates:
            state.setdefault("aliases", {}).update(updates["aliases"])

        if "models" in updates:
            for model_id, model_state in updates["models"].items():
                if model_id in state.get("models", {}):
                    state["models"][model_id].update(model_state)
                else:
                    state.setdefault("models", {})[model_id] = model_state

        await asyncio.to_thread(save_state, state)
        try:
            await asyncio.to_thread(write_env_file, state)
        except OSError as e:
            logger.warning("Failed to write env file: %s", e)
        _invalidate_state_cache()
    return web.json_response({"status": "ok", "state": state})
