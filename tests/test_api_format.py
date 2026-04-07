"""Tests for api_format field in routes and proxy routing based on api_format."""

import json

import aiohttp
import pytest
from aiohttp import web
from aioresponses import aioresponses as aioresponses_ctx

from uam.discovery.local import discover_local
from uam.discovery.anthropic import discover_anthropic
from uam.discovery.openrouter import discover_openrouter
from uam.discovery.runpod import discover_runpod
from uam.proxy import _needs_translation, create_app
from uam.router import ModelRouter
from uam.state import save_state


# ---------------------------------------------------------------------------
# Discovery tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestApiFormatDiscovery:
    async def test_local_route_default_api_format(self):
        """Localhost probe routes have api_format: 'openai'."""
        config = {"local": {"probe_ports": [11434], "servers": []}}
        from aioresponses import aioresponses

        with aioresponses() as mocked:
            mocked.get(
                "http://127.0.0.1:11434/v1/models",
                payload={"data": [{"id": "llama3.1"}]},
            )
            async with aiohttp.ClientSession() as session:
                routes = await discover_local(config, session)
        assert routes["local:llama3.1"]["api_format"] == "openai"

    async def test_local_server_string_api_format(self):
        """String server 'http://host:8000' produces api_format: 'openai'."""
        config = {"local": {"probe_ports": [], "servers": ["http://host:8000"]}}
        from aioresponses import aioresponses

        with aioresponses() as mocked:
            mocked.get(
                "http://host:8000/v1/models",
                payload={"data": [{"id": "model-a"}]},
            )
            async with aiohttp.ClientSession() as session:
                routes = await discover_local(config, session)
        assert routes["local:model-a"]["api_format"] == "openai"

    async def test_local_server_dict_with_api_format(self):
        """Dict server with api_format: 'anthropic' propagates to route."""
        config = {
            "local": {
                "probe_ports": [],
                "servers": [{"url": "http://host:11434", "api_format": "anthropic"}],
            }
        }
        from aioresponses import aioresponses

        with aioresponses() as mocked:
            mocked.get(
                "http://host:11434/v1/models",
                payload={"data": [{"id": "claude-local"}]},
            )
            async with aiohttp.ClientSession() as session:
                routes = await discover_local(config, session)
        assert routes["local:claude-local"]["api_format"] == "anthropic"

    async def test_local_server_dict_without_api_format(self):
        """Dict server without api_format defaults to 'openai'."""
        config = {
            "local": {
                "probe_ports": [],
                "servers": [{"url": "http://host:8000"}],
            }
        }
        from aioresponses import aioresponses

        with aioresponses() as mocked:
            mocked.get(
                "http://host:8000/v1/models",
                payload={"data": [{"id": "vllm-model"}]},
            )
            async with aiohttp.ClientSession() as session:
                routes = await discover_local(config, session)
        assert routes["local:vllm-model"]["api_format"] == "openai"

    def test_anthropic_routes_have_api_format(self):
        """Anthropic discovery routes have api_format: 'anthropic'."""
        config = {
            "anthropic": {
                "url": "https://api.anthropic.com",
                "api_key_env": "ANTHROPIC_API_KEY_REAL",
            }
        }
        routes = discover_anthropic(config)
        for route_key, route in routes.items():
            assert route["api_format"] == "anthropic", f"{route_key} missing api_format"

    async def test_openrouter_routes_have_api_format(self):
        """OpenRouter routes have api_format: 'openai'."""
        config = {
            "openrouter": {
                "url": "https://openrouter.ai/api",
                "api_key_env": "OPENROUTER_API_KEY",
            }
        }
        import os

        os.environ["OPENROUTER_API_KEY"] = "test-key"
        try:
            from aioresponses import aioresponses

            with aioresponses() as mocked:
                mocked.get(
                    "https://openrouter.ai/api/v1/models",
                    payload={"data": [{"id": "google/gemini-2.0-flash"}]},
                )
                async with aiohttp.ClientSession() as session:
                    routes = await discover_openrouter(config, session)
            assert len(routes) > 0
            for route_key, route in routes.items():
                assert route["api_format"] == "openai", f"{route_key} missing api_format"
        finally:
            del os.environ["OPENROUTER_API_KEY"]

    async def test_runpod_routes_have_api_format(self):
        """RunPod routes have api_format: 'openai'."""
        config = {
            "runpod": {
                "accounts": {
                    "test": {"api_key_env": "RUNPOD_API_KEY"},
                }
            }
        }
        import os

        os.environ["RUNPOD_API_KEY"] = "test-key"
        try:
            from aioresponses import aioresponses

            with aioresponses() as mocked:
                mocked.post(
                    "https://api.runpod.io/graphql",
                    payload={
                        "data": {
                            "myself": {
                                "pods": [
                                    {
                                        "id": "pod123",
                                        "name": "test-pod",
                                        "desiredStatus": "RUNNING",
                                        "ports": "8000",
                                        "imageName": "vllm",
                                        "env": [],
                                    }
                                ]
                            }
                        }
                    },
                )
                mocked.get(
                    "https://pod123-8000.proxy.runpod.net/v1/models",
                    payload={"data": [{"id": "meta-llama/Llama-3.1-70B"}]},
                )
                async with aiohttp.ClientSession() as session:
                    routes = await discover_runpod(config, session)
            assert len(routes) > 0
            for route_key, route in routes.items():
                assert route["api_format"] == "openai", f"{route_key} missing api_format"
        finally:
            del os.environ["RUNPOD_API_KEY"]


# ---------------------------------------------------------------------------
# Proxy routing tests
# ---------------------------------------------------------------------------


class TestApiFormatProxy:
    def test_needs_translation_anthropic_api_format(self):
        """Route with api_format 'anthropic' and backend 'local' needs no translation."""
        route = {
            "backend": "local",
            "url": "http://host:11434",
            "api_key": "",
            "original_model": "claude-local",
            "api_format": "anthropic",
        }
        assert _needs_translation(route) is False

    def test_needs_translation_openai_api_format(self):
        """Route with api_format 'openai' and backend 'local' needs translation."""
        route = {
            "backend": "local",
            "url": "http://127.0.0.1:11434",
            "api_key": "",
            "original_model": "llama3.1",
            "api_format": "openai",
        }
        assert _needs_translation(route) is True

    @pytest.mark.asyncio
    async def test_native_local_passthrough(self, aiohttp_client):
        """Local route with api_format 'anthropic' uses _proxy_anthropic_native path."""
        # Set up routes with a local model that has api_format: anthropic
        routes = {
            "local:claude-local": {
                "backend": "local",
                "url": "http://localhost:9999",
                "api_key": "",
                "original_model": "claude-local",
                "api_format": "anthropic",
            }
        }
        # Create a minimal state so the model is enabled
        save_state({
            "default": "",
            "aliases": {},
            "models": {"local:claude-local": {"enabled": True}},
        })

        async with aiohttp.ClientSession() as session:
            router = ModelRouter.__new__(ModelRouter)
            router.routes = routes
            router.session = session

            app = create_app(router)

            # Mock upstream Anthropic-format response
            upstream_response = {
                "id": "msg_123",
                "type": "message",
                "role": "assistant",
                "content": [{"type": "text", "text": "Hello from local anthropic"}],
                "model": "claude-local",
                "stop_reason": "end_turn",
                "usage": {"input_tokens": 10, "output_tokens": 5},
            }

            with aioresponses_ctx() as mocked:
                # The native path posts to {url}/v1/messages
                mocked.post(
                    "http://localhost:9999/v1/messages",
                    payload=upstream_response,
                )

                client = await aiohttp_client(app)
                resp = await client.post(
                    "/v1/messages",
                    json={
                        "model": "local:claude-local",
                        "messages": [{"role": "user", "content": "Hello"}],
                        "max_tokens": 100,
                    },
                )

                assert resp.status == 200
                data = await resp.json()
                # Should get the upstream response directly (no translation)
                assert data["content"][0]["text"] == "Hello from local anthropic"
                assert data["type"] == "message"
