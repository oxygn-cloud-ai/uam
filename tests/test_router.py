"""Tests for uam.router — ModelRouter discovery orchestration and model resolution."""

import aiohttp
from unittest.mock import AsyncMock, patch, call

from uam.router import ModelRouter
from uam.state import save_state


# --- init ---


def test_router_init(default_config):
    router = ModelRouter(default_config)
    assert router.config is default_config
    assert router.routes == {}
    assert router.session is None


# --- start / stop ---


async def test_router_start_creates_session(default_config):
    router = ModelRouter(default_config)
    try:
        await router.start(skip_discovery=True)
        assert isinstance(router.session, aiohttp.ClientSession)
    finally:
        await router.stop()


async def test_router_start_registers_anthropic(default_config, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY_REAL", "sk-test")
    router = ModelRouter(default_config)
    try:
        await router.start(skip_discovery=True)
        assert "claude-sonnet-4-6" in router.routes
        assert "claude-opus-4-6" in router.routes
        for route in router.routes.values():
            assert route["backend"] == "anthropic"
    finally:
        await router.stop()


async def test_router_start_with_discovery(default_config, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY_REAL", "sk-test")
    fake_runpod = {"runpod:test-pod": {
        "backend": "runpod", "url": "http://runpod", "api_key": "rp", "original_model": "test"
    }}
    fake_openrouter = {"openrouter:test-model": {
        "backend": "openrouter", "url": "http://or", "api_key": "or", "original_model": "test"
    }}
    fake_local = {"local:qwen": {
        "backend": "local", "url": "http://127.0.0.1:11434", "api_key": "", "original_model": "qwen"
    }}

    async def mock_runpod(cfg, sess):
        return fake_runpod

    async def mock_openrouter(cfg, sess):
        return fake_openrouter

    async def mock_local(cfg, sess):
        return fake_local

    # Ensure config has sections so discovery branches are taken
    default_config["runpod"] = {"accounts": {"test": {}}}
    default_config["openrouter"] = {"url": "http://or", "api_key_env": "OPENROUTER_API_KEY"}
    default_config["local"] = {"probe_ports": [11434]}

    with patch("uam.router.discover_runpod", mock_runpod), \
         patch("uam.router.discover_openrouter", mock_openrouter), \
         patch("uam.router.discover_local", mock_local):
        router = ModelRouter(default_config)
        try:
            await router.start(skip_discovery=False)
            assert "runpod:test-pod" in router.routes
            assert "openrouter:test-model" in router.routes
            assert "local:qwen" in router.routes
            # Anthropic models should also be present
            assert "claude-sonnet-4-6" in router.routes
        finally:
            await router.stop()


async def test_router_start_syncs_state(default_config, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY_REAL", "sk-test")
    router = ModelRouter(default_config)
    with patch("uam.router.save_state") as mock_save:
        try:
            await router.start(skip_discovery=True)
            assert mock_save.called
        finally:
            await router.stop()


async def test_router_stop_closes_session(default_config):
    router = ModelRouter(default_config)
    await router.start(skip_discovery=True)
    session = router.session
    await router.stop()
    assert session.closed


async def test_router_stop_without_session(default_config):
    router = ModelRouter(default_config)
    # Should not raise even though session is None
    await router.stop()


# --- discover ---


async def test_router_discover_gathers_all(default_config):
    default_config["runpod"] = {"accounts": {"a": {}}}
    default_config["openrouter"] = {"url": "http://or"}
    default_config["local"] = {"probe_ports": [11434]}

    mock_rp = AsyncMock(return_value={})
    mock_or = AsyncMock(return_value={})
    mock_lc = AsyncMock(return_value={})

    router = ModelRouter(default_config)
    router.session = AsyncMock()

    with patch("uam.router.discover_runpod", mock_rp), \
         patch("uam.router.discover_openrouter", mock_or), \
         patch("uam.router.discover_local", mock_lc):
        await router.discover()

    mock_rp.assert_awaited_once()
    mock_or.assert_awaited_once()
    mock_lc.assert_awaited_once()


async def test_router_discover_handles_exceptions(default_config):
    default_config["runpod"] = {"accounts": {"a": {}}}
    default_config["openrouter"] = {"url": "http://or"}
    default_config["local"] = {"probe_ports": [11434]}

    good_route = {"local:good": {
        "backend": "local", "url": "http://x", "api_key": "", "original_model": "good"
    }}

    mock_rp = AsyncMock(side_effect=ConnectionError("fail"))
    mock_or = AsyncMock(return_value=good_route)
    mock_lc = AsyncMock(side_effect=TimeoutError("timeout"))

    router = ModelRouter(default_config)
    router.session = AsyncMock()

    with patch("uam.router.discover_runpod", mock_rp), \
         patch("uam.router.discover_openrouter", mock_or), \
         patch("uam.router.discover_local", mock_lc):
        await router.discover()

    assert "local:good" in router.routes


async def test_router_discover_no_local_config(default_config):
    """discover() skips local when config has no 'local' section."""
    default_config["runpod"] = {"accounts": {"a": {}}}
    default_config["openrouter"] = {"url": "http://or"}
    default_config.pop("local", None)

    mock_rp = AsyncMock(return_value={})
    mock_or = AsyncMock(return_value={})
    mock_lc = AsyncMock(return_value={})

    router = ModelRouter(default_config)
    router.session = AsyncMock()

    with patch("uam.router.discover_runpod", mock_rp), \
         patch("uam.router.discover_openrouter", mock_or), \
         patch("uam.router.discover_local", mock_lc):
        await router.discover()

    mock_rp.assert_awaited_once()
    mock_or.assert_awaited_once()
    mock_lc.assert_not_awaited()


async def test_router_discover_no_openrouter_config(default_config):
    """discover() skips openrouter when config has no 'openrouter' section."""
    default_config["runpod"] = {"accounts": {"a": {}}}
    default_config["local"] = {"probe_ports": [11434]}
    default_config.pop("openrouter", None)

    mock_rp = AsyncMock(return_value={})
    mock_or = AsyncMock(return_value={})
    mock_lc = AsyncMock(return_value={})

    router = ModelRouter(default_config)
    router.session = AsyncMock()

    with patch("uam.router.discover_runpod", mock_rp), \
         patch("uam.router.discover_openrouter", mock_or), \
         patch("uam.router.discover_local", mock_lc):
        await router.discover()

    mock_rp.assert_awaited_once()
    mock_or.assert_not_awaited()
    mock_lc.assert_awaited_once()


async def test_router_discover_no_backends_configured(default_config):
    """discover() with no backends configured runs nothing."""
    default_config.pop("runpod", None)
    default_config.pop("openrouter", None)
    default_config.pop("local", None)

    mock_rp = AsyncMock(return_value={})
    mock_or = AsyncMock(return_value={})
    mock_lc = AsyncMock(return_value={})

    router = ModelRouter(default_config)
    router.session = AsyncMock()

    with patch("uam.router.discover_runpod", mock_rp), \
         patch("uam.router.discover_openrouter", mock_or), \
         patch("uam.router.discover_local", mock_lc):
        await router.discover()

    mock_rp.assert_not_awaited()
    mock_or.assert_not_awaited()
    mock_lc.assert_not_awaited()


async def test_router_discover_skips_unconfigured_runpod(default_config):
    # Empty accounts means runpod should be skipped
    default_config["runpod"] = {"accounts": {}}
    default_config["openrouter"] = {"url": "http://or"}

    mock_rp = AsyncMock(return_value={})
    mock_or = AsyncMock(return_value={})

    router = ModelRouter(default_config)
    router.session = AsyncMock()

    with patch("uam.router.discover_runpod", mock_rp), \
         patch("uam.router.discover_openrouter", mock_or):
        await router.discover()

    mock_rp.assert_not_awaited()
    mock_or.assert_awaited_once()


# --- refresh ---


async def test_router_refresh_clears_non_anthropic(default_config):
    router = ModelRouter(default_config)
    router.session = AsyncMock()
    # Pre-populate with anthropic and non-anthropic routes
    router.routes = {
        "claude-sonnet-4-6": {
            "backend": "anthropic", "url": "http://a", "api_key": "k", "original_model": "claude-sonnet-4-6"
        },
        "openrouter:test": {
            "backend": "openrouter", "url": "http://or", "api_key": "k", "original_model": "test"
        },
    }

    new_routes = {"local:new": {
        "backend": "local", "url": "http://l", "api_key": "", "original_model": "new"
    }}

    with patch.object(router, "discover", new_callable=AsyncMock) as mock_disc, \
         patch("uam.router.save_state"):
        async def add_new_routes():
            router.routes.update(new_routes)
        mock_disc.side_effect = add_new_routes

        await router.refresh()

    # Anthropic should remain, openrouter:test gone, local:new added
    assert "claude-sonnet-4-6" in router.routes
    assert "openrouter:test" not in router.routes
    assert "local:new" in router.routes


# --- resolve ---


def test_router_resolve_direct_match(default_config):
    router = ModelRouter(default_config)
    route = {
        "backend": "openrouter", "url": "http://or", "api_key": "k", "original_model": "test"
    }
    router.routes["openrouter:test"] = route
    assert router.resolve("openrouter:test") is route


def test_router_resolve_anthropic_alias(default_config, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY_REAL", "sk-test")
    router = ModelRouter(default_config)
    route = {
        "backend": "anthropic", "url": "http://a", "api_key": "sk-test",
        "original_model": "claude-opus-4-6",
    }
    router.routes["claude-opus-4-6"] = route
    # "claude-opus-4-6[1m]" is an alias for "claude-opus-4-6"
    result = router.resolve("claude-opus-4-6[1m]")
    assert result is route


def test_router_resolve_alias_target_missing(default_config, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY_REAL", "sk-test")
    router = ModelRouter(default_config)
    # Alias exists but target NOT in routes
    # Should fall through to default backend
    result = router.resolve("claude-opus-4-6[1m]")
    # Falls back to anthropic default
    assert result["backend"] == "anthropic"
    assert result["original_model"] == "claude-opus-4-6[1m]"


def test_router_resolve_fallback_anthropic(default_config, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY_REAL", "sk-test")
    default_config["default_backend"] = "anthropic"
    router = ModelRouter(default_config)
    result = router.resolve("totally-unknown-model")
    assert result is not None
    assert result["backend"] == "anthropic"
    assert result["original_model"] == "totally-unknown-model"


def test_router_resolve_fallback_non_anthropic_returns_none(default_config):
    default_config["default_backend"] = "other"
    router = ModelRouter(default_config)
    result = router.resolve("totally-unknown-model")
    assert result is None


# --- model_count / list_models ---


def test_router_model_count_and_list(default_config):
    router = ModelRouter(default_config)
    router.routes = {
        "a": {"backend": "anthropic", "original_model": "a"},
        "b": {"backend": "openrouter", "original_model": "b"},
        "c": {"backend": "local", "original_model": "c"},
    }
    assert router.model_count() == 3

    models = router.list_models()
    assert len(models) == 3
    # Sorted by key
    assert [m["id"] for m in models] == ["a", "b", "c"]
    for m in models:
        assert "id" in m
        assert "backend" in m
        assert "original_model" in m
