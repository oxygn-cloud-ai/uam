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

    # Anthropic should remain (rebuilt from config), openrouter:test gone, local:new added
    assert "claude-sonnet-4-6" in router.routes
    assert "openrouter:test" not in router.routes
    assert "local:new" in router.routes


async def test_router_refresh_rereads_config_from_disk(default_config, tmp_path, monkeypatch):
    """refresh() must re-read config.json so newly-added local.servers are picked up."""
    import uam.config as config_mod
    import json

    cfg_path = tmp_path / "config.json"
    cfg_path.parent.mkdir(parents=True, exist_ok=True)
    starting = default_config.copy()
    starting["local"] = {"probe_ports": [], "servers": [], "timeout": 120}
    cfg_path.write_text(json.dumps(starting))

    monkeypatch.setattr(config_mod, "CONFIG_PATH", cfg_path)
    monkeypatch.setattr(config_mod, "CONFIG_DIR", cfg_path.parent)

    router = ModelRouter(starting)
    router.session = AsyncMock()
    router.routes = {}

    # External actor (e.g. POST /config/local-servers) writes a new server.
    on_disk = json.loads(cfg_path.read_text())
    on_disk["local"]["servers"] = [
        {"url": "http://192.0.2.99:11434", "api_format": "openai"}
    ]
    cfg_path.write_text(json.dumps(on_disk))

    seen_configs: list[dict] = []

    async def fake_discover():
        # Capture the config that discover sees so we can assert it includes
        # the new server.
        seen_configs.append(router.config)

    with patch.object(router, "discover", side_effect=fake_discover), \
         patch("uam.router.save_state"):
        await router.refresh()

    assert len(seen_configs) == 1
    assert seen_configs[0]["local"]["servers"] == [
        {"url": "http://192.0.2.99:11434", "api_format": "openai"}
    ]


async def test_router_refresh_rebuilds_anthropic_from_new_config(default_config, tmp_path, monkeypatch):
    """If anthropic config changed on disk, refresh() must rebuild Anthropic routes."""
    import uam.config as config_mod
    import json

    cfg_path = tmp_path / "config.json"
    cfg_path.parent.mkdir(parents=True, exist_ok=True)
    cfg_path.write_text(json.dumps(default_config))

    monkeypatch.setattr(config_mod, "CONFIG_PATH", cfg_path)
    monkeypatch.setattr(config_mod, "CONFIG_DIR", cfg_path.parent)

    router = ModelRouter(default_config)
    router.session = AsyncMock()
    # Stale Anthropic route from initial start
    router.routes = {
        "claude-sonnet-4-6": {
            "backend": "anthropic", "url": "http://stale", "api_key": "old",
            "original_model": "claude-sonnet-4-6", "api_format": "anthropic", "timeout": 600,
        }
    }

    # Update on-disk config to a new Anthropic URL
    on_disk = json.loads(cfg_path.read_text())
    on_disk["anthropic"]["url"] = "https://new-anthropic.example"
    cfg_path.write_text(json.dumps(on_disk))

    with patch.object(router, "discover", new_callable=AsyncMock), \
         patch("uam.router.save_state"):
        await router.refresh()

    assert router.routes["claude-sonnet-4-6"]["url"] == "https://new-anthropic.example"


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


def test_list_models_no_metadata_by_default(default_config):
    """list_models() excludes metadata by default (backwards compat)."""
    router = ModelRouter(default_config)
    router.routes = {
        "openrouter:test": {
            "backend": "openrouter", "original_model": "test",
            "metadata": {"name": "Test", "context_length": 128000},
        },
    }
    models = router.list_models()
    assert "metadata" not in models[0]


def test_list_models_include_metadata(default_config):
    """list_models(include_metadata=True) includes metadata when present."""
    router = ModelRouter(default_config)
    router.routes = {
        "openrouter:test": {
            "backend": "openrouter", "original_model": "test",
            "metadata": {"name": "Test Model", "context_length": 128000},
        },
        "claude-sonnet": {
            "backend": "anthropic", "original_model": "claude-sonnet",
        },
    }
    models = router.list_models(include_metadata=True)
    or_model = next(m for m in models if m["id"] == "openrouter:test")
    assert or_model["metadata"] == {"name": "Test Model", "context_length": 128000}
    claude_model = next(m for m in models if m["id"] == "claude-sonnet")
    assert claude_model.get("metadata") is None
