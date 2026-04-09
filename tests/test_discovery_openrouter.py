"""Tests for OpenRouter model discovery."""

import pytest
import aiohttp
from aioresponses import aioresponses

from uam.discovery.openrouter import discover_openrouter


@pytest.fixture
def openrouter_config(monkeypatch):
    """Config with a valid OpenRouter API key env var set."""
    monkeypatch.setenv("OPENROUTER_API_KEY", "or-test-key")
    return {
        "openrouter": {
            "url": "https://openrouter.ai/api",
            "api_key_env": "OPENROUTER_API_KEY",
        }
    }


@pytest.mark.asyncio
class TestDiscoverOpenRouter:
    async def test_discover_openrouter_returns_models(self, openrouter_config):
        with aioresponses() as mocked:
            mocked.get(
                "https://openrouter.ai/api/v1/models",
                payload={
                    "data": [
                        {"id": "google/gemini-2.0-flash"},
                        {"id": "meta/llama-3.1"},
                    ]
                },
            )
            async with aiohttp.ClientSession() as session:
                routes = await discover_openrouter(openrouter_config, session)
            assert len(routes) == 2
            assert "openrouter:google/gemini-2.0-flash" in routes
            assert "openrouter:meta/llama-3.1" in routes

    async def test_discover_openrouter_route_structure(self, openrouter_config):
        with aioresponses() as mocked:
            mocked.get(
                "https://openrouter.ai/api/v1/models",
                payload={"data": [{"id": "google/gemini-2.0-flash"}]},
            )
            async with aiohttp.ClientSession() as session:
                routes = await discover_openrouter(openrouter_config, session)
            route = routes["openrouter:google/gemini-2.0-flash"]
            assert route["backend"] == "openrouter"
            assert route["url"] == "https://openrouter.ai/api"
            assert route["api_key"] == "or-test-key"
            assert route["original_model"] == "google/gemini-2.0-flash"

    async def test_discover_openrouter_no_api_key(self, monkeypatch):
        monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
        config = {
            "openrouter": {
                "url": "https://openrouter.ai/api",
                "api_key_env": "OPENROUTER_API_KEY",
            }
        }
        with aioresponses() as mocked:
            # Should NOT make any HTTP call
            async with aiohttp.ClientSession() as session:
                routes = await discover_openrouter(config, session)
            assert routes == {}

    async def test_discover_openrouter_api_error(self, openrouter_config):
        with aioresponses() as mocked:
            mocked.get(
                "https://openrouter.ai/api/v1/models",
                exception=aiohttp.ClientConnectionError("timeout"),
            )
            async with aiohttp.ClientSession() as session:
                routes = await discover_openrouter(openrouter_config, session)
            assert routes == {}

    async def test_discover_openrouter_custom_url(self, monkeypatch):
        monkeypatch.setenv("OPENROUTER_API_KEY", "or-test-key")
        config = {
            "openrouter": {
                "url": "https://custom-or-proxy.example.com",
                "api_key_env": "OPENROUTER_API_KEY",
            }
        }
        with aioresponses() as mocked:
            mocked.get(
                "https://custom-or-proxy.example.com/v1/models",
                payload={"data": [{"id": "anthropic/claude-3"}]},
            )
            async with aiohttp.ClientSession() as session:
                routes = await discover_openrouter(config, session)
            route = routes["openrouter:anthropic/claude-3"]
            assert route["url"] == "https://custom-or-proxy.example.com"

    async def test_discover_openrouter_empty_data(self, openrouter_config):
        with aioresponses() as mocked:
            mocked.get(
                "https://openrouter.ai/api/v1/models",
                payload={"data": []},
            )
            async with aiohttp.ClientSession() as session:
                routes = await discover_openrouter(openrouter_config, session)
            assert routes == {}

    async def test_discover_openrouter_captures_metadata(self, openrouter_config):
        """Metadata (name, pricing, context_length, modality) is captured."""
        with aioresponses() as mocked:
            mocked.get(
                "https://openrouter.ai/api/v1/models",
                payload={
                    "data": [
                        {
                            "id": "google/gemini-2.0-flash",
                            "name": "Google Gemini 2.0 Flash",
                            "description": "Fast flash model",
                            "context_length": 1000000,
                            "pricing": {
                                "prompt": "0.00001",
                                "completion": "0.00004",
                            },
                            "architecture": {
                                "modality": "text+image->text",
                                "input_modalities": ["text", "image"],
                                "output_modalities": ["text"],
                                "tokenizer": "Gemini",
                            },
                            "top_provider": {
                                "max_completion_tokens": 8192,
                            },
                        }
                    ]
                },
            )
            async with aiohttp.ClientSession() as session:
                routes = await discover_openrouter(openrouter_config, session)

        route = routes["openrouter:google/gemini-2.0-flash"]
        md = route["metadata"]
        assert md["name"] == "Google Gemini 2.0 Flash"
        assert md["description"] == "Fast flash model"
        assert md["context_length"] == 1000000
        assert md["pricing_prompt"] == "0.00001"
        assert md["pricing_completion"] == "0.00004"
        assert md["modality"] == "text+image->text"

    async def test_discover_openrouter_metadata_missing_fields(self, openrouter_config):
        """Models with missing optional fields get sensible defaults."""
        with aioresponses() as mocked:
            mocked.get(
                "https://openrouter.ai/api/v1/models",
                payload={"data": [{"id": "some/model"}]},
            )
            async with aiohttp.ClientSession() as session:
                routes = await discover_openrouter(openrouter_config, session)

        route = routes["openrouter:some/model"]
        md = route["metadata"]
        assert md["name"] == "some/model"
        assert md["description"] == ""
        assert md["context_length"] is None
        assert md["pricing_prompt"] == "0"
        assert md["pricing_completion"] == "0"
        assert md["modality"] == ""
