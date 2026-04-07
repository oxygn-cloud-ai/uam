"""Tests for uam.proxy — HTTP handlers, helpers, and model swap logic."""

import json
import time

import aiohttp
import pytest
from aiohttp import web
from aioresponses import aioresponses as aioresponses_ctx

from uam.proxy import (
    create_app,
    _openai_chat_url,
    _needs_translation,
    _build_upstream_headers,
    _resolve_default_swap,
    _make_anthropic_error,
    _forward_response_headers,
)
from uam.router import ModelRouter
from uam.state import save_state


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def test_routes():
    return {
        "claude-sonnet-4-6": {
            "backend": "anthropic",
            "url": "https://api.anthropic.com",
            "api_key": "sk-test",
            "original_model": "claude-sonnet-4-6",
        },
        "openrouter:google/gemini-2.0-flash": {
            "backend": "openrouter",
            "url": "https://openrouter.ai/api",
            "api_key": "or-test",
            "original_model": "google/gemini-2.0-flash",
        },
        "local:qwen": {
            "backend": "local",
            "url": "http://127.0.0.1:11434",
            "api_key": "",
            "original_model": "qwen",
        },
    }


@pytest.fixture
async def app_client(aiohttp_client, test_routes, default_config):
    router = ModelRouter(default_config)
    router.routes = dict(test_routes)
    router.session = aiohttp.ClientSession()
    app = create_app(router)
    client = await aiohttp_client(app)
    yield client
    await router.session.close()


def _mock_upstream(app_client):
    """Create aioresponses context with test server passthrough."""
    base = str(app_client.make_url("")).rstrip("/")
    return aioresponses_ctx(passthrough=[base])


# ---------------------------------------------------------------------------
# Helper: _openai_chat_url
# ---------------------------------------------------------------------------


def test_openai_chat_url_with_v1():
    route = {"url": "https://x.com/v1"}
    assert _openai_chat_url(route) == "https://x.com/v1/chat/completions"


def test_openai_chat_url_without_v1():
    route = {"url": "https://x.com"}
    assert _openai_chat_url(route) == "https://x.com/v1/chat/completions"


def test_openai_chat_url_trailing_slash():
    route = {"url": "https://x.com/"}
    assert _openai_chat_url(route) == "https://x.com/v1/chat/completions"


# ---------------------------------------------------------------------------
# Helper: _needs_translation
# ---------------------------------------------------------------------------


def test_needs_translation_anthropic():
    assert _needs_translation({"backend": "anthropic"}) is False


def test_needs_translation_openrouter():
    assert _needs_translation({"backend": "openrouter"}) is True


def test_needs_translation_local():
    assert _needs_translation({"backend": "local"}) is True


# ---------------------------------------------------------------------------
# Helper: _build_upstream_headers
# ---------------------------------------------------------------------------


def test_build_headers_anthropic_with_request():
    """Anthropic route with request containing anthropic-version header."""
    route = {"backend": "anthropic", "api_key": "sk-test"}
    from aiohttp.test_utils import make_mocked_request
    req = make_mocked_request("POST", "/v1/messages", headers={
        "anthropic-version": "2024-01-01",
        "anthropic-beta": "tools-2024-04-04",
    })
    headers = _build_upstream_headers(req, route)
    assert headers["anthropic-version"] == "2024-01-01"
    assert headers["anthropic-beta"] == "tools-2024-04-04"
    assert headers["X-Api-Key"] == "sk-test"
    assert headers["Content-Type"] == "application/json"


def test_build_headers_anthropic_default_version():
    from aiohttp.test_utils import make_mocked_request
    route = {"backend": "anthropic", "api_key": "sk-test"}
    req = make_mocked_request("POST", "/v1/messages")
    headers = _build_upstream_headers(req, route)
    assert headers["anthropic-version"] == "2023-06-01"
    assert headers["X-Api-Key"] == "sk-test"


def test_build_headers_anthropic_no_request():
    route = {"backend": "anthropic", "api_key": "sk-test"}
    headers = _build_upstream_headers(None, route)
    assert headers["anthropic-version"] == "2023-06-01"
    assert headers["X-Api-Key"] == "sk-test"


def test_build_headers_openai_bearer():
    route = {"backend": "openrouter", "api_key": "or-key"}
    headers = _build_upstream_headers(None, route)
    assert headers["Authorization"] == "Bearer or-key"
    assert "X-Api-Key" not in headers


def test_build_headers_openai_no_key():
    route = {"backend": "local", "api_key": ""}
    headers = _build_upstream_headers(None, route)
    assert "Authorization" not in headers
    assert "X-Api-Key" not in headers


# ---------------------------------------------------------------------------
# Helper: _make_anthropic_error
# ---------------------------------------------------------------------------


def test_make_anthropic_error_valid_json():
    body = b'{"error":{"message":"bad request"}}'
    result = json.loads(_make_anthropic_error(body, 400))
    assert result["error"]["type"] == "api_error"
    assert result["error"]["message"] == "bad request"


def test_make_anthropic_error_invalid_json():
    body = b"Server Error"
    result = json.loads(_make_anthropic_error(body, 500))
    assert result["error"]["type"] == "api_error"
    assert result["error"]["message"] == "Server Error"


def test_make_anthropic_error_empty_object():
    body = b"{}"
    result = json.loads(_make_anthropic_error(body, 500))
    assert result["error"]["type"] == "api_error"
    # No 'error' key in {}, so str({}) is used
    assert result["error"]["message"] == str({})


# ---------------------------------------------------------------------------
# _resolve_default_swap
# ---------------------------------------------------------------------------


def test_swap_claude_to_non_claude(default_config, test_routes):
    """State default is non-Claude, incoming is Claude -> swap."""
    state = {
        "default": "openrouter:google/gemini-2.0-flash",
        "aliases": {},
        "models": {
            "claude-sonnet-4-6": {"enabled": True},
            "openrouter:google/gemini-2.0-flash": {"enabled": True},
        },
    }
    save_state(state)

    router = ModelRouter(default_config)
    router.routes = dict(test_routes)

    route, effective = _resolve_default_swap(router, "claude-sonnet-4-6")
    assert route is not None
    assert effective == "openrouter:google/gemini-2.0-flash"
    assert route["backend"] == "openrouter"


def test_swap_claude_to_claude_no_swap(default_config, test_routes):
    """State default is Claude -> no swap."""
    state = {
        "default": "claude-opus-4-6",
        "aliases": {},
        "models": {
            "claude-sonnet-4-6": {"enabled": True},
            "claude-opus-4-6": {"enabled": True},
        },
    }
    save_state(state)

    router = ModelRouter(default_config)
    router.routes = dict(test_routes)

    route, effective = _resolve_default_swap(router, "claude-sonnet-4-6")
    assert route is not None
    # Default starts with claude- so no swap
    assert effective == "claude-sonnet-4-6"


def test_swap_non_claude_incoming_no_swap(default_config, test_routes):
    """Incoming is non-Claude -> no swap regardless of default."""
    state = {
        "default": "openrouter:google/gemini-2.0-flash",
        "aliases": {},
        "models": {
            "openrouter:google/gemini-2.0-flash": {"enabled": True},
        },
    }
    save_state(state)

    router = ModelRouter(default_config)
    router.routes = dict(test_routes)

    route, effective = _resolve_default_swap(router, "openrouter:google/gemini-2.0-flash")
    assert route is not None
    assert effective == "openrouter:google/gemini-2.0-flash"


def test_swap_disabled_default(default_config, test_routes):
    """Default model is disabled -> falls through to normal resolve."""
    state = {
        "default": "openrouter:google/gemini-2.0-flash",
        "aliases": {},
        "models": {
            "claude-sonnet-4-6": {"enabled": True},
            "openrouter:google/gemini-2.0-flash": {"enabled": False},
        },
    }
    save_state(state)

    router = ModelRouter(default_config)
    router.routes = dict(test_routes)

    route, effective = _resolve_default_swap(router, "claude-sonnet-4-6")
    assert route is not None
    # Falls through to normal resolve since default is disabled
    assert effective == "claude-sonnet-4-6"
    assert route["backend"] == "anthropic"


def test_swap_model_disabled(default_config, test_routes):
    """Incoming model is in state but disabled -> returns (None, model)."""
    state = {
        "default": "",
        "aliases": {},
        "models": {
            "claude-sonnet-4-6": {"enabled": False},
        },
    }
    save_state(state)

    router = ModelRouter(default_config)
    router.routes = dict(test_routes)

    route, effective = _resolve_default_swap(router, "claude-sonnet-4-6")
    assert route is None
    assert effective == "claude-sonnet-4-6"


def test_swap_default_resolves_to_none(default_config, test_routes):
    """Default enabled but router.resolve(default) returns None."""
    state = {
        "default": "nonexistent:model",
        "aliases": {},
        "models": {
            "claude-sonnet-4-6": {"enabled": True},
            "nonexistent:model": {"enabled": True},
        },
    }
    save_state(state)

    # Set default_backend to something non-anthropic so resolve returns None
    default_config["default_backend"] = "none"
    router = ModelRouter(default_config)
    router.routes = dict(test_routes)

    route, effective = _resolve_default_swap(router, "claude-sonnet-4-6")
    # Default resolves to None, falls through to normal resolve for claude-sonnet
    # But since default_backend is "none", even normal resolve returns None for unknown
    # However claude-sonnet-4-6 IS in routes as direct match, so it resolves
    assert route is not None
    assert effective == "claude-sonnet-4-6"


# ---------------------------------------------------------------------------
# Endpoint: /health
# ---------------------------------------------------------------------------


async def test_health(app_client):
    resp = await app_client.get("/health")
    assert resp.status == 200
    data = await resp.json()
    assert data["status"] == "ok"
    assert isinstance(data["models"], int)
    assert "default" in data


# ---------------------------------------------------------------------------
# Endpoint: /v1/models
# ---------------------------------------------------------------------------


async def test_models(app_client):
    # Write a state so models have enabled status
    save_state({
        "default": "claude-sonnet-4-6",
        "aliases": {},
        "models": {
            "claude-sonnet-4-6": {"enabled": True},
            "openrouter:google/gemini-2.0-flash": {"enabled": True},
            "local:qwen": {"enabled": True},
        },
    })
    resp = await app_client.get("/v1/models")
    assert resp.status == 200
    data = await resp.json()
    assert data["object"] == "list"
    assert isinstance(data["data"], list)
    assert len(data["data"]) == 3
    assert "default" in data


# ---------------------------------------------------------------------------
# Endpoint: /state GET and POST
# ---------------------------------------------------------------------------


async def test_get_state(app_client):
    save_state({"default": "x", "aliases": {}, "models": {}})
    resp = await app_client.get("/state")
    assert resp.status == 200
    data = await resp.json()
    assert data["default"] == "x"


async def test_post_state_update_default(app_client):
    save_state({"default": "", "aliases": {}, "models": {}})
    resp = await app_client.post(
        "/state",
        data=json.dumps({"default": "claude-sonnet-4-6"}),
        headers={"Content-Type": "application/json"},
    )
    assert resp.status == 200
    data = await resp.json()
    assert data["state"]["default"] == "claude-sonnet-4-6"


async def test_post_state_writes_env_file(app_client):
    """POST /state with a new default should write the managed env file."""
    import uam.state as state_mod
    save_state({
        "default": "",
        "aliases": {"qwen": "local:qwen3-coder-next:latest"},
        "models": {
            "local:qwen3-coder-next:latest": {
                "enabled": True,
                "capabilities": ["tools", "streaming"],
            }
        },
    })
    resp = await app_client.post(
        "/state",
        data=json.dumps({"default": "local:qwen3-coder-next:latest"}),
        headers={"Content-Type": "application/json"},
    )
    assert resp.status == 200
    assert state_mod.ENV_PATH.exists()
    content = state_mod.ENV_PATH.read_text()
    assert "ANTHROPIC_BASE_URL=http://127.0.0.1:5100" in content
    # SEC-001: shlex-quoted; safe chars produce bare unquoted form.
    assert "ANTHROPIC_DEFAULT_SONNET_MODEL=local:qwen3-coder-next:latest" in content
    assert "ANTHROPIC_DEFAULT_SONNET_MODEL_NAME=qwen" in content


async def test_post_state_update_models(app_client):
    save_state({
        "default": "",
        "aliases": {},
        "models": {"claude-sonnet-4-6": {"enabled": True}},
    })
    resp = await app_client.post(
        "/state",
        data=json.dumps({"models": {"claude-sonnet-4-6": {"enabled": False}}}),
        headers={"Content-Type": "application/json"},
    )
    assert resp.status == 200
    data = await resp.json()
    assert data["state"]["models"]["claude-sonnet-4-6"]["enabled"] is False


async def test_post_state_invalid_json(app_client):
    resp = await app_client.post(
        "/state",
        data=b"not json{",
        headers={"Content-Type": "application/json"},
    )
    assert resp.status == 400


async def test_post_state_not_dict(app_client):
    resp = await app_client.post(
        "/state",
        data=json.dumps([1, 2]),
        headers={"Content-Type": "application/json"},
    )
    assert resp.status == 400


# ---------------------------------------------------------------------------
# Endpoint: /refresh
# ---------------------------------------------------------------------------


async def test_refresh(app_client):
    from unittest.mock import AsyncMock, patch
    router = app_client.app["router"]
    with patch.object(router, "refresh", new_callable=AsyncMock):
        resp = await app_client.post("/refresh")
    assert resp.status == 200
    data = await resp.json()
    assert data["status"] == "ok"


# ---------------------------------------------------------------------------
# Endpoint: /v1/messages — upstream calls
# ---------------------------------------------------------------------------


async def test_messages_unknown_model(app_client):
    # Write state with model explicitly disabled
    save_state({
        "default": "",
        "aliases": {},
        "models": {"nonexistent:xyz": {"enabled": False}},
    })
    # Set non-anthropic default_backend so resolve returns None for unknown
    app_client.app["router"].config["default_backend"] = "none"
    resp = await app_client.post(
        "/v1/messages",
        data=json.dumps({"model": "nonexistent:xyz", "messages": []}),
        headers={"Content-Type": "application/json"},
    )
    assert resp.status == 400
    data = await resp.json()
    assert "Unknown or disabled" in data["error"]["message"]


async def test_messages_anthropic_non_stream(app_client):
    save_state({
        "default": "claude-sonnet-4-6",
        "aliases": {},
        "models": {"claude-sonnet-4-6": {"enabled": True}},
    })
    upstream_body = json.dumps({
        "id": "msg_test",
        "type": "message",
        "content": [{"type": "text", "text": "hello"}],
    })
    with _mock_upstream(app_client) as m:
        m.post(
            "https://api.anthropic.com/v1/messages",
            body=upstream_body,
            status=200,
            content_type="application/json",
        )
        resp = await app_client.post(
            "/v1/messages",
            data=json.dumps({
                "model": "claude-sonnet-4-6",
                "messages": [{"role": "user", "content": "hi"}],
                "stream": False,
            }),
            headers={"Content-Type": "application/json"},
        )
    assert resp.status == 200
    data = await resp.json()
    assert data["id"] == "msg_test"


async def test_messages_anthropic_stream(app_client):
    save_state({
        "default": "claude-sonnet-4-6",
        "aliases": {},
        "models": {"claude-sonnet-4-6": {"enabled": True}},
    })
    sse_body = b"event: message_start\ndata: {}\n\nevent: content_block_delta\ndata: {}\n\n"
    with _mock_upstream(app_client) as m:
        m.post(
            "https://api.anthropic.com/v1/messages",
            body=sse_body,
            status=200,
            content_type="text/event-stream",
        )
        resp = await app_client.post(
            "/v1/messages",
            data=json.dumps({
                "model": "claude-sonnet-4-6",
                "messages": [{"role": "user", "content": "hi"}],
                "stream": True,
            }),
            headers={"Content-Type": "application/json"},
        )
    assert resp.status == 200
    body = await resp.read()
    assert len(body) > 0


async def test_messages_anthropic_error(app_client):
    save_state({
        "default": "claude-sonnet-4-6",
        "aliases": {},
        "models": {"claude-sonnet-4-6": {"enabled": True}},
    })
    with _mock_upstream(app_client) as m:
        m.post(
            "https://api.anthropic.com/v1/messages",
            exception=aiohttp.ClientError("upstream fail"),
        )
        resp = await app_client.post(
            "/v1/messages",
            data=json.dumps({
                "model": "claude-sonnet-4-6",
                "messages": [{"role": "user", "content": "hi"}],
                "stream": False,
            }),
            headers={"Content-Type": "application/json"},
        )
    assert resp.status == 502


async def test_messages_translation_non_stream(app_client):
    save_state({
        "default": "",
        "aliases": {},
        "models": {"openrouter:google/gemini-2.0-flash": {"enabled": True}},
    })
    openai_resp = {
        "id": "chatcmpl-test",
        "choices": [{"message": {"content": "hello"}, "finish_reason": "stop"}],
        "usage": {"prompt_tokens": 10, "completion_tokens": 5},
    }
    with _mock_upstream(app_client) as m:
        m.post(
            "https://openrouter.ai/api/v1/chat/completions",
            payload=openai_resp,
            status=200,
        )
        resp = await app_client.post(
            "/v1/messages",
            data=json.dumps({
                "model": "openrouter:google/gemini-2.0-flash",
                "messages": [{"role": "user", "content": "hi"}],
                "max_tokens": 100,
                "stream": False,
            }),
            headers={"Content-Type": "application/json"},
        )
    assert resp.status == 200
    data = await resp.json()
    # Should be translated to Anthropic format
    assert data["type"] == "message"
    assert data["content"][0]["type"] == "text"
    assert data["content"][0]["text"] == "hello"


async def test_messages_translation_stream(app_client):
    save_state({
        "default": "",
        "aliases": {},
        "models": {"openrouter:google/gemini-2.0-flash": {"enabled": True}},
    })
    sse_body = (
        b'data: {"choices":[{"delta":{"content":"hi"},"finish_reason":null}]}\n\n'
        b"data: [DONE]\n\n"
    )
    with _mock_upstream(app_client) as m:
        m.post(
            "https://openrouter.ai/api/v1/chat/completions",
            body=sse_body,
            status=200,
            content_type="text/event-stream",
        )
        resp = await app_client.post(
            "/v1/messages",
            data=json.dumps({
                "model": "openrouter:google/gemini-2.0-flash",
                "messages": [{"role": "user", "content": "hi"}],
                "max_tokens": 100,
                "stream": True,
            }),
            headers={"Content-Type": "application/json"},
        )
    assert resp.status == 200
    body = await resp.read()
    text = body.decode()
    assert "message_start" in text
    assert "content_block_delta" in text


async def test_messages_translation_error_stream(app_client):
    save_state({
        "default": "",
        "aliases": {},
        "models": {"openrouter:google/gemini-2.0-flash": {"enabled": True}},
    })
    with _mock_upstream(app_client) as m:
        m.post(
            "https://openrouter.ai/api/v1/chat/completions",
            body=b'{"error":{"message":"rate limited"}}',
            status=429,
            content_type="application/json",
        )
        resp = await app_client.post(
            "/v1/messages",
            data=json.dumps({
                "model": "openrouter:google/gemini-2.0-flash",
                "messages": [{"role": "user", "content": "hi"}],
                "max_tokens": 100,
                "stream": True,
            }),
            headers={"Content-Type": "application/json"},
        )
    assert resp.status == 429
    body = await resp.read()
    data = json.loads(body)
    assert data["error"]["message"] == "rate limited"


async def test_messages_translation_error_non_stream(app_client):
    save_state({
        "default": "",
        "aliases": {},
        "models": {"openrouter:google/gemini-2.0-flash": {"enabled": True}},
    })
    with _mock_upstream(app_client) as m:
        m.post(
            "https://openrouter.ai/api/v1/chat/completions",
            body=b'{"error":{"message":"bad request"}}',
            status=400,
            content_type="application/json",
        )
        resp = await app_client.post(
            "/v1/messages",
            data=json.dumps({
                "model": "openrouter:google/gemini-2.0-flash",
                "messages": [{"role": "user", "content": "hi"}],
                "max_tokens": 100,
                "stream": False,
            }),
            headers={"Content-Type": "application/json"},
        )
    assert resp.status == 400
    body = await resp.read()
    data = json.loads(body)
    assert data["error"]["message"] == "bad request"


async def test_messages_translation_exception(app_client):
    save_state({
        "default": "",
        "aliases": {},
        "models": {"openrouter:google/gemini-2.0-flash": {"enabled": True}},
    })
    with _mock_upstream(app_client) as m:
        m.post(
            "https://openrouter.ai/api/v1/chat/completions",
            exception=aiohttp.ClientError("connection refused"),
        )
        resp = await app_client.post(
            "/v1/messages",
            data=json.dumps({
                "model": "openrouter:google/gemini-2.0-flash",
                "messages": [{"role": "user", "content": "hi"}],
                "max_tokens": 100,
                "stream": False,
            }),
            headers={"Content-Type": "application/json"},
        )
    assert resp.status == 502


async def test_messages_default_swap(app_client):
    """Incoming Claude model swaps to non-Claude default."""
    save_state({
        "default": "openrouter:google/gemini-2.0-flash",
        "aliases": {},
        "models": {
            "claude-sonnet-4-6": {"enabled": True},
            "openrouter:google/gemini-2.0-flash": {"enabled": True},
        },
    })
    openai_resp = {
        "id": "chatcmpl-swap",
        "choices": [{"message": {"content": "swapped"}, "finish_reason": "stop"}],
        "usage": {"prompt_tokens": 5, "completion_tokens": 3},
    }
    with _mock_upstream(app_client) as m:
        m.post(
            "https://openrouter.ai/api/v1/chat/completions",
            payload=openai_resp,
            status=200,
        )
        resp = await app_client.post(
            "/v1/messages",
            data=json.dumps({
                "model": "claude-sonnet-4-6",
                "messages": [{"role": "user", "content": "hi"}],
                "max_tokens": 100,
                "stream": False,
            }),
            headers={"Content-Type": "application/json"},
        )
    assert resp.status == 200
    data = await resp.json()
    assert data["content"][0]["text"] == "swapped"


# ---------------------------------------------------------------------------
# Endpoint: /v1/messages/ask
# ---------------------------------------------------------------------------


async def test_ask_disabled(app_client):
    save_state({
        "default": "claude-sonnet-4-6",
        "aliases": {},
        "models": {"openrouter:google/gemini-2.0-flash": {"enabled": False}},
    })
    resp = await app_client.post(
        "/v1/messages/ask",
        data=json.dumps({
            "model": "openrouter:google/gemini-2.0-flash",
            "messages": [{"role": "user", "content": "hi"}],
            "max_tokens": 100,
        }),
        headers={"Content-Type": "application/json"},
    )
    assert resp.status == 403
    data = await resp.json()
    assert "off" in data["error"]["message"]


async def test_ask_not_found(app_client):
    save_state({"default": "", "aliases": {}, "models": {}})
    # Set non-anthropic default_backend so resolve returns None for unknown models
    app_client.app["router"].config["default_backend"] = "none"
    resp = await app_client.post(
        "/v1/messages/ask",
        data=json.dumps({
            "model": "nonexistent:model",
            "messages": [{"role": "user", "content": "hi"}],
            "max_tokens": 100,
        }),
        headers={"Content-Type": "application/json"},
    )
    assert resp.status == 404
    data = await resp.json()
    assert "not configured" in data["error"]["message"]


async def test_ask_anthropic(app_client):
    save_state({
        "default": "",
        "aliases": {},
        "models": {"claude-sonnet-4-6": {"enabled": True}},
    })
    upstream_body = json.dumps({
        "id": "msg_ask",
        "type": "message",
        "content": [{"type": "text", "text": "response"}],
    })
    with _mock_upstream(app_client) as m:
        m.post(
            "https://api.anthropic.com/v1/messages",
            body=upstream_body,
            status=200,
            content_type="application/json",
        )
        resp = await app_client.post(
            "/v1/messages/ask",
            data=json.dumps({
                "model": "claude-sonnet-4-6",
                "messages": [{"role": "user", "content": "hi"}],
                "max_tokens": 100,
            }),
            headers={"Content-Type": "application/json"},
        )
    assert resp.status == 200
    data = await resp.json()
    assert data["id"] == "msg_ask"


async def test_ask_translation(app_client):
    save_state({
        "default": "",
        "aliases": {},
        "models": {"openrouter:google/gemini-2.0-flash": {"enabled": True}},
    })
    openai_resp = {
        "id": "chatcmpl-ask",
        "choices": [{"message": {"content": "translated"}, "finish_reason": "stop"}],
        "usage": {"prompt_tokens": 5, "completion_tokens": 3},
    }
    with _mock_upstream(app_client) as m:
        m.post(
            "https://openrouter.ai/api/v1/chat/completions",
            payload=openai_resp,
            status=200,
        )
        resp = await app_client.post(
            "/v1/messages/ask",
            data=json.dumps({
                "model": "openrouter:google/gemini-2.0-flash",
                "messages": [{"role": "user", "content": "hi"}],
                "max_tokens": 100,
            }),
            headers={"Content-Type": "application/json"},
        )
    assert resp.status == 200
    data = await resp.json()
    assert data["content"][0]["text"] == "translated"


async def test_ask_error(app_client):
    save_state({
        "default": "",
        "aliases": {},
        "models": {"claude-sonnet-4-6": {"enabled": True}},
    })
    with _mock_upstream(app_client) as m:
        m.post(
            "https://api.anthropic.com/v1/messages",
            exception=aiohttp.ClientError("fail"),
        )
        resp = await app_client.post(
            "/v1/messages/ask",
            data=json.dumps({
                "model": "claude-sonnet-4-6",
                "messages": [{"role": "user", "content": "hi"}],
                "max_tokens": 100,
            }),
            headers={"Content-Type": "application/json"},
        )
    assert resp.status == 502


# ---------------------------------------------------------------------------
# Endpoint: /v1/messages/count_tokens
# ---------------------------------------------------------------------------


async def test_count_tokens_anthropic(app_client):
    save_state({
        "default": "claude-sonnet-4-6",
        "aliases": {},
        "models": {"claude-sonnet-4-6": {"enabled": True}},
    })
    with _mock_upstream(app_client) as m:
        m.post(
            "https://api.anthropic.com/v1/messages/count_tokens",
            payload={"input_tokens": 42},
            status=200,
        )
        resp = await app_client.post(
            "/v1/messages/count_tokens",
            data=json.dumps({
                "model": "claude-sonnet-4-6",
                "messages": [{"role": "user", "content": "hello world"}],
            }),
            headers={"Content-Type": "application/json"},
        )
    assert resp.status == 200
    data = await resp.json()
    assert data["input_tokens"] == 42


async def test_count_tokens_translation_estimate(app_client):
    """Non-anthropic backend returns rough token estimate."""
    save_state({
        "default": "",
        "aliases": {},
        "models": {"openrouter:google/gemini-2.0-flash": {"enabled": True}},
    })
    resp = await app_client.post(
        "/v1/messages/count_tokens",
        data=json.dumps({
            "model": "openrouter:google/gemini-2.0-flash",
            "messages": [{"role": "user", "content": "hello world"}],
        }),
        headers={"Content-Type": "application/json"},
    )
    assert resp.status == 200
    data = await resp.json()
    assert "input_tokens" in data
    assert isinstance(data["input_tokens"], int)
    assert data["input_tokens"] > 0


async def test_count_tokens_unknown(app_client):
    save_state({"default": "", "aliases": {}, "models": {}})
    app_client.app["router"].config["default_backend"] = "none"
    resp = await app_client.post(
        "/v1/messages/count_tokens",
        data=json.dumps({
            "model": "nonexistent:xyz",
            "messages": [{"role": "user", "content": "hello"}],
        }),
        headers={"Content-Type": "application/json"},
    )
    assert resp.status == 400


# ---------------------------------------------------------------------------
# State cache
# ---------------------------------------------------------------------------


async def test_messages_translation_stream_remaining_buffer(app_client):
    """Streaming with data remaining in buffer after all newlines processed."""
    save_state({
        "default": "",
        "aliases": {},
        "models": {"openrouter:google/gemini-2.0-flash": {"enabled": True}},
    })
    # Send a chunk that doesn't end with \n — so the remaining buffer has data
    sse_body = (
        b'data: {"choices":[{"delta":{"content":"hi"},"finish_reason":null}]}\n\n'
        b'data: {"choices":[{"delta":{"content":" there"},"finish_reason":null}]}'
    )
    with _mock_upstream(app_client) as m:
        m.post(
            "https://openrouter.ai/api/v1/chat/completions",
            body=sse_body,
            status=200,
            content_type="text/event-stream",
        )
        resp = await app_client.post(
            "/v1/messages",
            data=json.dumps({
                "model": "openrouter:google/gemini-2.0-flash",
                "messages": [{"role": "user", "content": "hi"}],
                "max_tokens": 100,
                "stream": True,
            }),
            headers={"Content-Type": "application/json"},
        )
    assert resp.status == 200
    body = await resp.read()
    text = body.decode()
    assert "there" in text


async def test_messages_translation_stream_remaining_buffer_unconvertible(app_client):
    """Remaining buffer data that doesn't convert (not SSE data: prefix)."""
    save_state({
        "default": "",
        "aliases": {},
        "models": {"openrouter:google/gemini-2.0-flash": {"enabled": True}},
    })
    # The remaining buffer after processing has content that won't convert
    # (a partial line that is not "data: ..." format)
    sse_body = (
        b'data: {"choices":[{"delta":{"content":"hi"},"finish_reason":null}]}\n\n'
        b"some-non-sse-trailing-data"
    )
    with _mock_upstream(app_client) as m:
        m.post(
            "https://openrouter.ai/api/v1/chat/completions",
            body=sse_body,
            status=200,
            content_type="text/event-stream",
        )
        resp = await app_client.post(
            "/v1/messages",
            data=json.dumps({
                "model": "openrouter:google/gemini-2.0-flash",
                "messages": [{"role": "user", "content": "hi"}],
                "max_tokens": 100,
                "stream": True,
            }),
            headers={"Content-Type": "application/json"},
        )
    assert resp.status == 200
    body = await resp.read()
    text = body.decode()
    # Should still have the initial content but not crash on non-convertible buffer
    assert "hi" in text


async def test_messages_translation_stream_empty_converted(app_client):
    """Streaming line that converts to None is skipped."""
    save_state({
        "default": "",
        "aliases": {},
        "models": {"openrouter:google/gemini-2.0-flash": {"enabled": True}},
    })
    # Empty delta -> converted is None, then [DONE] ends the stream
    sse_body = (
        b'data: {"choices":[{"delta":{},"finish_reason":null}]}\n\n'
        b"data: [DONE]\n\n"
    )
    with _mock_upstream(app_client) as m:
        m.post(
            "https://openrouter.ai/api/v1/chat/completions",
            body=sse_body,
            status=200,
            content_type="text/event-stream",
        )
        resp = await app_client.post(
            "/v1/messages",
            data=json.dumps({
                "model": "openrouter:google/gemini-2.0-flash",
                "messages": [{"role": "user", "content": "hi"}],
                "max_tokens": 100,
                "stream": True,
            }),
            headers={"Content-Type": "application/json"},
        )
    assert resp.status == 200
    body = await resp.read()
    text = body.decode()
    assert "message_stop" in text


def test_forward_response_headers_all_types():
    """_forward_response_headers forwards x-*, anthropic-*, and request-id."""
    from unittest.mock import MagicMock
    from multidict import CIMultiDict

    upstream = MagicMock()
    upstream.headers = CIMultiDict({
        "x-request-id": "abc123",
        "anthropic-ratelimit-tokens": "5000",
        "request-id": "req-456",
        "content-type": "application/json",  # should NOT be forwarded
    })
    resp = web.StreamResponse()
    _forward_response_headers(upstream, resp)
    assert resp.headers.get("x-request-id") == "abc123"
    assert resp.headers.get("anthropic-ratelimit-tokens") == "5000"
    assert resp.headers.get("request-id") == "req-456"
    assert "content-type" not in resp.headers


async def test_ask_translation_upstream_error(app_client):
    """Ask with translated backend returns upstream error directly."""
    save_state({
        "default": "",
        "aliases": {},
        "models": {"openrouter:google/gemini-2.0-flash": {"enabled": True}},
    })
    with _mock_upstream(app_client) as m:
        m.post(
            "https://openrouter.ai/api/v1/chat/completions",
            body=b'{"error":{"message":"rate limited"}}',
            status=429,
            content_type="application/json",
        )
        resp = await app_client.post(
            "/v1/messages/ask",
            data=json.dumps({
                "model": "openrouter:google/gemini-2.0-flash",
                "messages": [{"role": "user", "content": "hi"}],
                "max_tokens": 100,
            }),
            headers={"Content-Type": "application/json"},
        )
    assert resp.status == 429


async def test_ask_translation_exception(app_client):
    """Ask with translated backend raises exception -> 502."""
    save_state({
        "default": "",
        "aliases": {},
        "models": {"openrouter:google/gemini-2.0-flash": {"enabled": True}},
    })
    with _mock_upstream(app_client) as m:
        m.post(
            "https://openrouter.ai/api/v1/chat/completions",
            exception=aiohttp.ClientError("connection failed"),
        )
        resp = await app_client.post(
            "/v1/messages/ask",
            data=json.dumps({
                "model": "openrouter:google/gemini-2.0-flash",
                "messages": [{"role": "user", "content": "hi"}],
                "max_tokens": 100,
            }),
            headers={"Content-Type": "application/json"},
        )
    assert resp.status == 502


async def test_count_tokens_anthropic_exception(app_client):
    """count_tokens with anthropic backend raises exception -> 502."""
    save_state({
        "default": "claude-sonnet-4-6",
        "aliases": {},
        "models": {"claude-sonnet-4-6": {"enabled": True}},
    })
    with _mock_upstream(app_client) as m:
        m.post(
            "https://api.anthropic.com/v1/messages/count_tokens",
            exception=aiohttp.ClientError("upstream fail"),
        )
        resp = await app_client.post(
            "/v1/messages/count_tokens",
            data=json.dumps({
                "model": "claude-sonnet-4-6",
                "messages": [{"role": "user", "content": "hello"}],
            }),
            headers={"Content-Type": "application/json"},
        )
    assert resp.status == 502


async def test_post_state_add_new_model(app_client):
    """POST /state with a model not in state adds it."""
    save_state({"default": "", "aliases": {}, "models": {}})
    resp = await app_client.post(
        "/state",
        data=json.dumps({"models": {"new:model": {"enabled": True}}}),
        headers={"Content-Type": "application/json"},
    )
    assert resp.status == 200
    data = await resp.json()
    assert data["state"]["models"]["new:model"]["enabled"] is True


async def test_post_state_update_aliases(app_client):
    """POST /state with aliases merges them."""
    save_state({"default": "", "aliases": {"old": "old:model"}, "models": {}})
    resp = await app_client.post(
        "/state",
        data=json.dumps({"aliases": {"new": "new:model"}}),
        headers={"Content-Type": "application/json"},
    )
    assert resp.status == 200
    data = await resp.json()
    assert data["state"]["aliases"]["old"] == "old:model"
    assert data["state"]["aliases"]["new"] == "new:model"


def test_cache_within_ttl(monkeypatch):
    """Second call within TTL returns cached state, no disk reload."""
    import uam.proxy as proxy_mod

    save_state({"default": "first", "aliases": {}, "models": {}})

    # First call loads from disk
    state1 = proxy_mod._get_state()
    assert state1["default"] == "first"

    # Write new state to disk
    save_state({"default": "second", "aliases": {}, "models": {}})

    # Second call within TTL should still return cached "first"
    state2 = proxy_mod._get_state()
    assert state2["default"] == "first"


def test_cache_invalidation(monkeypatch):
    """_invalidate_state_cache forces reload."""
    import uam.proxy as proxy_mod

    save_state({"default": "first", "aliases": {}, "models": {}})
    proxy_mod._get_state()

    save_state({"default": "second", "aliases": {}, "models": {}})
    proxy_mod._invalidate_state_cache()

    state = proxy_mod._get_state()
    assert state["default"] == "second"


# --- Malformed JSON body tests ---


async def test_messages_malformed_json(app_client):
    resp = await app_client.post(
        "/v1/messages",
        data=b"not json at all",
        headers={"Content-Type": "application/json"},
    )
    assert resp.status == 400
    data = await resp.json()
    assert data["error"]["type"] == "invalid_request_error"
    assert "Invalid JSON" in data["error"]["message"]


async def test_ask_malformed_json(app_client):
    resp = await app_client.post(
        "/v1/messages/ask",
        data=b"{broken",
        headers={"Content-Type": "application/json"},
    )
    assert resp.status == 400
    data = await resp.json()
    assert data["error"]["type"] == "invalid_request_error"


async def test_count_tokens_malformed_json(app_client):
    resp = await app_client.post(
        "/v1/messages/count_tokens",
        data=b"",
        headers={"Content-Type": "application/json"},
    )
    assert resp.status == 400
    data = await resp.json()
    assert data["error"]["type"] == "invalid_request_error"


# ---------------------------------------------------------------------------
# Phase 2: Retry signaling — _retry_headers + integration
# ---------------------------------------------------------------------------


from uam.proxy import _retry_headers


def test_retry_headers_503():
    """503 Service Unavailable returns x-should-retry: true."""
    headers = _retry_headers(503)
    assert headers["x-should-retry"] == "true"


def test_retry_headers_429():
    """429 Too Many Requests returns x-should-retry: true."""
    headers = _retry_headers(429)
    assert headers["x-should-retry"] == "true"


def test_retry_headers_429_with_retry_after():
    """429 with upstream Retry-After (canonical case) forwards the value.

    Real servers emit the canonical 'Retry-After' / 'Retry-After-Ms' form.
    This test uses canonical case so a case-sensitivity regression in
    _retry_headers (C1 fix) would be caught.
    """
    upstream = {"Retry-After-Ms": "5000", "Retry-After": "5"}
    headers = _retry_headers(429, upstream)
    assert headers["x-should-retry"] == "true"
    assert headers["retry-after-ms"] == "5000"
    assert headers["retry-after"] == "5"


def test_retry_headers_400():
    """400 Bad Request returns x-should-retry: false."""
    headers = _retry_headers(400)
    assert headers["x-should-retry"] == "false"


def test_retry_headers_401():
    """401 Unauthorized returns x-should-retry: false."""
    headers = _retry_headers(401)
    assert headers["x-should-retry"] == "false"


def test_retry_headers_404():
    """404 Not Found returns x-should-retry: false."""
    headers = _retry_headers(404)
    assert headers["x-should-retry"] == "false"


def test_retry_headers_200():
    """200 OK returns empty dict (no retry headers for success)."""
    headers = _retry_headers(200)
    assert headers == {}


async def test_messages_upstream_503_has_retry_header(app_client):
    """POST /v1/messages with upstream 503 includes x-should-retry: true."""
    save_state({
        "default": "claude-sonnet-4-6",
        "aliases": {},
        "models": {"claude-sonnet-4-6": {"enabled": True}},
    })
    with _mock_upstream(app_client) as m:
        m.post(
            "https://api.anthropic.com/v1/messages",
            body=b'{"error":{"type":"overloaded_error","message":"Overloaded"}}',
            status=503,
            content_type="application/json",
        )
        resp = await app_client.post(
            "/v1/messages",
            data=json.dumps({
                "model": "claude-sonnet-4-6",
                "messages": [{"role": "user", "content": "hi"}],
                "stream": False,
            }),
            headers={"Content-Type": "application/json"},
        )
    assert resp.status == 503
    assert resp.headers.get("x-should-retry") == "true"


async def test_messages_upstream_400_has_no_retry_header(app_client):
    """POST /v1/messages with upstream 400 includes x-should-retry: false."""
    save_state({
        "default": "claude-sonnet-4-6",
        "aliases": {},
        "models": {"claude-sonnet-4-6": {"enabled": True}},
    })
    with _mock_upstream(app_client) as m:
        m.post(
            "https://api.anthropic.com/v1/messages",
            body=b'{"error":{"type":"invalid_request_error","message":"bad"}}',
            status=400,
            content_type="application/json",
        )
        resp = await app_client.post(
            "/v1/messages",
            data=json.dumps({
                "model": "claude-sonnet-4-6",
                "messages": [{"role": "user", "content": "hi"}],
                "stream": False,
            }),
            headers={"Content-Type": "application/json"},
        )
    assert resp.status == 400
    assert resp.headers.get("x-should-retry") == "false"


async def test_ask_upstream_error_has_retry_headers(app_client):
    """Ask endpoint with upstream 503 includes x-should-retry: true."""
    save_state({
        "default": "",
        "aliases": {},
        "models": {"claude-sonnet-4-6": {"enabled": True}},
    })
    with _mock_upstream(app_client) as m:
        m.post(
            "https://api.anthropic.com/v1/messages",
            body=b'{"error":{"type":"overloaded_error","message":"Overloaded"}}',
            status=503,
            content_type="application/json",
        )
        resp = await app_client.post(
            "/v1/messages/ask",
            data=json.dumps({
                "model": "claude-sonnet-4-6",
                "messages": [{"role": "user", "content": "hi"}],
                "max_tokens": 100,
            }),
            headers={"Content-Type": "application/json"},
        )
    assert resp.status == 503
    assert resp.headers.get("x-should-retry") == "true"
