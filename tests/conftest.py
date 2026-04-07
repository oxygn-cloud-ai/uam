"""Shared fixtures for uam tests."""

import pytest
from pathlib import Path


@pytest.fixture(autouse=True)
def tmp_uam_dir(tmp_path, monkeypatch):
    """Redirect all uam config/state paths to a temp directory."""
    config_dir = tmp_path / ".uam"
    config_dir.mkdir()

    monkeypatch.setattr("uam.config.CONFIG_DIR", config_dir)
    monkeypatch.setattr("uam.config.CONFIG_PATH", config_dir / "config.json")
    monkeypatch.setattr("uam.state.STATE_PATH", config_dir / "models.json")
    monkeypatch.setattr("uam.state.ENV_PATH", config_dir / "env.sh")

    return tmp_path


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    """Remove real API keys from environment to prevent test pollution."""
    for var in ["ANTHROPIC_API_KEY_REAL", "OPENROUTER_API_KEY", "RUNPOD_API_KEY"]:
        monkeypatch.delenv(var, raising=False)


@pytest.fixture(autouse=True)
def reset_proxy_cache(monkeypatch):
    """Reset proxy module-level state cache between tests."""
    monkeypatch.setattr("uam.proxy._state_cache", {})
    monkeypatch.setattr("uam.proxy._state_cache_time", 0)


@pytest.fixture
def default_config():
    """Return a copy of the default config."""
    from uam.config import default_config
    return default_config()


@pytest.fixture
def sample_state():
    """Return a sample model state dict."""
    return {
        "default": "claude-sonnet-4-6",
        "aliases": {
            "claude": "claude-sonnet-4-6",
            "opus": "claude-opus-4-6",
            "gemini": "openrouter:google/gemini-2.0-flash",
        },
        "models": {
            "claude-sonnet-4-6": {"enabled": True},
            "claude-opus-4-6": {"enabled": True},
            "openrouter:google/gemini-2.0-flash": {"enabled": True},
        },
    }


@pytest.fixture
def mock_route_anthropic():
    """Return a mock Anthropic route dict."""
    return {
        "backend": "anthropic",
        "url": "https://api.anthropic.com",
        "api_key": "sk-test-key",
        "original_model": "claude-sonnet-4-6",
    }


@pytest.fixture
def mock_route_openai():
    """Return a mock OpenAI-compatible route dict."""
    return {
        "backend": "openrouter",
        "url": "https://openrouter.ai/api",
        "api_key": "or-test-key",
        "original_model": "google/gemini-2.0-flash",
    }


@pytest.fixture
def mock_route_local():
    """Return a mock local route dict."""
    return {
        "backend": "local",
        "url": "http://127.0.0.1:11434",
        "api_key": "",
        "original_model": "qwen3-coder-next:latest",
    }
