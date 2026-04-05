"""Tests for Anthropic model discovery."""

import pytest

from uam.discovery.anthropic import ALIASES, MODELS, discover_anthropic


@pytest.fixture
def anthropic_config(monkeypatch):
    """Config with a valid Anthropic API key env var set."""
    monkeypatch.setenv("ANTHROPIC_API_KEY_REAL", "sk-test-key")
    return {
        "anthropic": {
            "url": "https://api.anthropic.com",
            "api_key_env": "ANTHROPIC_API_KEY_REAL",
        }
    }


class TestDiscoverAnthropic:
    def test_discover_anthropic_returns_all_models(self, anthropic_config):
        routes = discover_anthropic(anthropic_config)
        for model in MODELS:
            assert model in routes, f"Expected model {model} in routes"
        for alias in ALIASES:
            assert alias in routes, f"Expected alias {alias} in routes"

    def test_discover_anthropic_route_structure(self, anthropic_config):
        routes = discover_anthropic(anthropic_config)
        for model in MODELS:
            route = routes[model]
            assert route["backend"] == "anthropic"
            assert route["url"] == "https://api.anthropic.com"
            assert route["api_key"] == "sk-test-key"
            assert route["original_model"] == model

    def test_discover_anthropic_aliases_point_to_base(self, anthropic_config):
        routes = discover_anthropic(anthropic_config)
        for alias, base_model in ALIASES.items():
            assert alias in routes
            route = routes[alias]
            assert route["original_model"] == base_model
            # Alias route should match base route except original_model is the base
            base_route = routes[base_model]
            assert route["backend"] == base_route["backend"]
            assert route["url"] == base_route["url"]
            assert route["api_key"] == base_route["api_key"]

    def test_discover_anthropic_uses_config_url(self, monkeypatch):
        monkeypatch.setenv("ANTHROPIC_API_KEY_REAL", "sk-test-key")
        config = {
            "anthropic": {
                "url": "https://custom-proxy.example.com",
                "api_key_env": "ANTHROPIC_API_KEY_REAL",
            }
        }
        routes = discover_anthropic(config)
        for route in routes.values():
            assert route["url"] == "https://custom-proxy.example.com"

    def test_discover_anthropic_no_api_key(self, monkeypatch):
        monkeypatch.delenv("ANTHROPIC_API_KEY_REAL", raising=False)
        config = {
            "anthropic": {
                "url": "https://api.anthropic.com",
                "api_key_env": "ANTHROPIC_API_KEY_REAL",
            }
        }
        routes = discover_anthropic(config)
        # Routes are still created, just with empty api_key
        assert len(routes) == len(MODELS) + len(ALIASES)
        for route in routes.values():
            assert route["api_key"] == ""

    def test_anthropic_aliases_constant(self):
        assert "claude-opus-4-6[1m]" in ALIASES
        assert ALIASES["claude-opus-4-6[1m]"] == "claude-opus-4-6"
        assert "claude-sonnet-4-6[1m]" in ALIASES
        assert ALIASES["claude-sonnet-4-6[1m]"] == "claude-sonnet-4-6"
