"""Tests for local model discovery."""

import pytest
import aiohttp
from aioresponses import aioresponses

from uam.discovery.local import discover_local


@pytest.fixture
def local_config():
    """Config with default local probe ports."""
    return {
        "local": {
            "probe_ports": [11434],
            "servers": [],
        }
    }


@pytest.mark.asyncio
class TestDiscoverLocal:
    async def test_discover_local_v1_models(self, local_config):
        with aioresponses() as mocked:
            mocked.get(
                "http://127.0.0.1:11434/v1/models",
                payload={"data": [{"id": "llama3.1"}]},
            )
            async with aiohttp.ClientSession() as session:
                routes = await discover_local(local_config, session)
            assert "local:llama3.1" in routes
            assert routes["local:llama3.1"]["original_model"] == "llama3.1"
            assert routes["local:llama3.1"]["backend"] == "local"

    async def test_discover_local_api_tags_fallback(self, local_config):
        with aioresponses() as mocked:
            mocked.get(
                "http://127.0.0.1:11434/v1/models",
                exception=aiohttp.ClientConnectionError("refused"),
            )
            mocked.get(
                "http://127.0.0.1:11434/api/tags",
                payload={"models": [{"name": "qwen2.5"}]},
            )
            async with aiohttp.ClientSession() as session:
                routes = await discover_local(local_config, session)
            assert "local:qwen2.5" in routes

    async def test_discover_local_prefers_v1_models(self, local_config):
        with aioresponses() as mocked:
            mocked.get(
                "http://127.0.0.1:11434/v1/models",
                payload={"data": [{"id": "llama3.1"}]},
            )
            # /api/tags should not be called because /v1/models succeeds
            mocked.get(
                "http://127.0.0.1:11434/api/tags",
                payload={"models": [{"name": "qwen2.5"}]},
            )
            async with aiohttp.ClientSession() as session:
                routes = await discover_local(local_config, session)
            assert "local:llama3.1" in routes
            assert "local:qwen2.5" not in routes

    async def test_discover_local_multiple_probe_ports(self):
        config = {"local": {"probe_ports": [11434, 8000], "servers": []}}
        with aioresponses() as mocked:
            mocked.get(
                "http://127.0.0.1:11434/v1/models",
                payload={"data": [{"id": "llama3.1"}]},
            )
            mocked.get(
                "http://127.0.0.1:8000/v1/models",
                payload={"data": [{"id": "mistral-7b"}]},
            )
            async with aiohttp.ClientSession() as session:
                routes = await discover_local(config, session)
            assert "local:llama3.1" in routes
            assert "local:mistral-7b" in routes

    async def test_discover_local_explicit_servers(self):
        config = {
            "local": {
                "probe_ports": [],
                "servers": ["http://192.168.1.100:11434"],
            }
        }
        with aioresponses() as mocked:
            mocked.get(
                "http://192.168.1.100:11434/v1/models",
                payload={"data": [{"id": "phi-3"}]},
            )
            async with aiohttp.ClientSession() as session:
                routes = await discover_local(config, session)
            assert "local:phi-3" in routes
            assert routes["local:phi-3"]["url"] == "http://192.168.1.100:11434"

    async def test_discover_local_server_as_dict(self):
        config = {
            "local": {
                "probe_ports": [],
                "servers": [{"url": "http://host:8000"}],
            }
        }
        with aioresponses() as mocked:
            mocked.get(
                "http://host:8000/v1/models",
                payload={"data": [{"id": "codellama"}]},
            )
            async with aiohttp.ClientSession() as session:
                routes = await discover_local(config, session)
            assert "local:codellama" in routes

    async def test_discover_local_trailing_slash_stripped(self):
        config = {
            "local": {
                "probe_ports": [],
                "servers": ["http://host:8000/"],
            }
        }
        with aioresponses() as mocked:
            # The URL should be stripped to http://host:8000 before probing
            mocked.get(
                "http://host:8000/v1/models",
                payload={"data": [{"id": "model-a"}]},
            )
            async with aiohttp.ClientSession() as session:
                routes = await discover_local(config, session)
            assert "local:model-a" in routes
            assert routes["local:model-a"]["url"] == "http://host:8000"

    async def test_discover_local_all_probes_fail(self, local_config):
        with aioresponses() as mocked:
            mocked.get(
                "http://127.0.0.1:11434/v1/models",
                exception=aiohttp.ClientConnectionError("refused"),
            )
            mocked.get(
                "http://127.0.0.1:11434/api/tags",
                exception=aiohttp.ClientConnectionError("refused"),
            )
            async with aiohttp.ClientSession() as session:
                routes = await discover_local(local_config, session)
            assert routes == {}

    async def test_discover_local_model_key_fallback(self, local_config):
        """Ollama tags with 'model' key instead of 'name'."""
        with aioresponses() as mocked:
            mocked.get(
                "http://127.0.0.1:11434/v1/models",
                exception=aiohttp.ClientConnectionError("refused"),
            )
            mocked.get(
                "http://127.0.0.1:11434/api/tags",
                payload={"models": [{"model": "deepseek-r1"}]},
            )
            async with aiohttp.ClientSession() as session:
                routes = await discover_local(local_config, session)
            assert "local:deepseek-r1" in routes

    async def test_discover_local_empty_config(self):
        config = {"local": {"probe_ports": [], "servers": []}}
        async with aiohttp.ClientSession() as session:
            routes = await discover_local(config, session)
        assert routes == {}

    async def test_discover_local_server_empty_url_skipped(self):
        """Server dict with empty url is skipped."""
        config = {"local": {"probe_ports": [], "servers": [{"url": ""}]}}
        async with aiohttp.ClientSession() as session:
            routes = await discover_local(config, session)
        assert routes == {}

    async def test_discover_local_empty_models_in_v1(self, local_config):
        """v1/models returns empty data list."""
        with aioresponses() as mocked:
            mocked.get(
                "http://127.0.0.1:11434/v1/models",
                payload={"data": []},
            )
            async with aiohttp.ClientSession() as session:
                routes = await discover_local(local_config, session)
        assert routes == {}

    async def test_discover_local_empty_models_in_api_tags(self, local_config):
        """api/tags returns empty models list."""
        with aioresponses() as mocked:
            mocked.get(
                "http://127.0.0.1:11434/v1/models",
                exception=aiohttp.ClientConnectionError("refused"),
            )
            mocked.get(
                "http://127.0.0.1:11434/api/tags",
                payload={"models": []},
            )
            async with aiohttp.ClientSession() as session:
                routes = await discover_local(local_config, session)
        assert routes == {}

    async def test_discover_local_api_tags_model_empty_name(self, local_config):
        """api/tags model with empty name and model key is skipped."""
        with aioresponses() as mocked:
            mocked.get(
                "http://127.0.0.1:11434/v1/models",
                exception=aiohttp.ClientConnectionError("refused"),
            )
            mocked.get(
                "http://127.0.0.1:11434/api/tags",
                payload={"models": [{"name": "", "model": ""}]},
            )
            async with aiohttp.ClientSession() as session:
                routes = await discover_local(local_config, session)
        assert routes == {}

    async def test_discover_local_route_has_no_api_key(self, local_config):
        with aioresponses() as mocked:
            mocked.get(
                "http://127.0.0.1:11434/v1/models",
                payload={"data": [{"id": "llama3.1"}, {"id": "phi-3"}]},
            )
            async with aiohttp.ClientSession() as session:
                routes = await discover_local(local_config, session)
            for route in routes.values():
                assert route["api_key"] == ""
