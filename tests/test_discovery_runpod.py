"""Tests for RunPod model discovery."""

import pytest
import aiohttp
from aioresponses import aioresponses

from uam.discovery.runpod import RUNPOD_GRAPHQL, discover_runpod


def _graphql_response(pods):
    """Build a GraphQL response payload with the given pods."""
    return {"data": {"myself": {"pods": pods}}}


def _make_pod(
    pod_id="pod123",
    name="TestPod",
    status="RUNNING",
    ports="8000/tcp,22/tcp",
    env=None,
):
    """Build a pod dict for GraphQL responses."""
    if env is None:
        env = ["VLLM_API_KEY=test-vllm-key"]
    return {
        "id": pod_id,
        "name": name,
        "desiredStatus": status,
        "ports": ports,
        "imageName": "vllm/vllm-openai:latest",
        "env": env,
    }


@pytest.fixture
def runpod_config(monkeypatch):
    """Config with a single RunPod account and valid API key."""
    monkeypatch.setenv("RUNPOD_API_KEY", "rp-test-key")
    return {
        "runpod": {
            "accounts": {
                "default": {
                    "api_key_env": "RUNPOD_API_KEY",
                }
            }
        }
    }


def _mock_graphql_and_probe(mocked, pods, probe_models=None, probe_exception=None, pod_id="pod123"):
    """Helper to mock both GraphQL and model probe endpoints."""
    mocked.post(RUNPOD_GRAPHQL, payload=_graphql_response(pods))
    proxy_url = f"https://{pod_id}-8000.proxy.runpod.net/v1/models"
    if probe_exception:
        mocked.get(proxy_url, exception=probe_exception)
    elif probe_models is not None:
        mocked.get(proxy_url, payload={"data": [{"id": m} for m in probe_models]})


@pytest.mark.asyncio
class TestDiscoverRunPod:
    async def test_discover_runpod_running_pod(self, runpod_config):
        pod = _make_pod()
        with aioresponses() as mocked:
            _mock_graphql_and_probe(mocked, [pod], probe_models=["meta-llama/Llama-3.1-70B"])
            async with aiohttp.ClientSession() as session:
                routes = await discover_runpod(runpod_config, session)
            assert "runpod:testpod/meta-llama/Llama-3.1-70B" in routes
            route = routes["runpod:testpod/meta-llama/Llama-3.1-70B"]
            assert route["backend"] == "runpod"
            assert route["url"] == "https://pod123-8000.proxy.runpod.net"
            assert route["api_key"] == "test-vllm-key"
            assert route["original_model"] == "meta-llama/Llama-3.1-70B"

    async def test_discover_runpod_skips_stopped_pod(self, runpod_config):
        pod = _make_pod(status="STOPPED")
        with aioresponses() as mocked:
            mocked.post(RUNPOD_GRAPHQL, payload=_graphql_response([pod]))
            async with aiohttp.ClientSession() as session:
                routes = await discover_runpod(runpod_config, session)
            assert routes == {}

    async def test_discover_runpod_skips_no_port_8000(self, runpod_config):
        pod = _make_pod(ports="22/tcp")
        with aioresponses() as mocked:
            mocked.post(RUNPOD_GRAPHQL, payload=_graphql_response([pod]))
            async with aiohttp.ClientSession() as session:
                routes = await discover_runpod(runpod_config, session)
            assert routes == {}

    async def test_discover_runpod_no_api_key(self, monkeypatch):
        monkeypatch.delenv("RUNPOD_API_KEY", raising=False)
        config = {
            "runpod": {
                "accounts": {
                    "default": {"api_key_env": "RUNPOD_API_KEY"}
                }
            }
        }
        with aioresponses() as mocked:
            async with aiohttp.ClientSession() as session:
                routes = await discover_runpod(config, session)
            assert routes == {}

    async def test_discover_runpod_graphql_error(self, runpod_config):
        with aioresponses() as mocked:
            mocked.post(RUNPOD_GRAPHQL, exception=aiohttp.ClientConnectionError("failed"))
            async with aiohttp.ClientSession() as session:
                routes = await discover_runpod(runpod_config, session)
            assert routes == {}

    async def test_discover_runpod_probe_failure(self, runpod_config):
        pod = _make_pod()
        with aioresponses() as mocked:
            _mock_graphql_and_probe(
                mocked, [pod],
                probe_exception=aiohttp.ClientConnectionError("probe failed"),
            )
            async with aiohttp.ClientSession() as session:
                routes = await discover_runpod(runpod_config, session)
            assert routes == {}

    async def test_discover_runpod_vllm_key_substitution(self, runpod_config):
        pod = _make_pod(pod_id="abc999", env=["VLLM_API_KEY=$RUNPOD_POD_ID-secret"])
        with aioresponses() as mocked:
            mocked.post(RUNPOD_GRAPHQL, payload=_graphql_response([pod]))
            mocked.get(
                "https://abc999-8000.proxy.runpod.net/v1/models",
                payload={"data": [{"id": "model-x"}]},
            )
            async with aiohttp.ClientSession() as session:
                routes = await discover_runpod(runpod_config, session)
            route = routes["runpod:testpod/model-x"]
            assert route["api_key"] == "abc999-secret"

    async def test_discover_runpod_vllm_key_literal(self, runpod_config):
        pod = _make_pod(env=["VLLM_API_KEY=static-key"])
        with aioresponses() as mocked:
            _mock_graphql_and_probe(mocked, [pod], probe_models=["model-y"])
            async with aiohttp.ClientSession() as session:
                routes = await discover_runpod(runpod_config, session)
            route = list(routes.values())[0]
            assert route["api_key"] == "static-key"

    async def test_discover_runpod_env_as_list(self, runpod_config):
        pod = _make_pod(env=["VLLM_API_KEY=from-list", "OTHER=val"])
        with aioresponses() as mocked:
            _mock_graphql_and_probe(mocked, [pod], probe_models=["model-z"])
            async with aiohttp.ClientSession() as session:
                routes = await discover_runpod(runpod_config, session)
            route = list(routes.values())[0]
            assert route["api_key"] == "from-list"

    async def test_discover_runpod_env_as_dict(self, runpod_config):
        pod = _make_pod(env={"VLLM_API_KEY": "from-dict", "OTHER": "val"})
        with aioresponses() as mocked:
            _mock_graphql_and_probe(mocked, [pod], probe_models=["model-w"])
            async with aiohttp.ClientSession() as session:
                routes = await discover_runpod(runpod_config, session)
            route = list(routes.values())[0]
            assert route["api_key"] == "from-dict"

    async def test_discover_runpod_ports_as_list(self, runpod_config):
        pod = _make_pod(ports=["8000/tcp", "22/tcp"])
        with aioresponses() as mocked:
            _mock_graphql_and_probe(mocked, [pod], probe_models=["model-a"])
            async with aiohttp.ClientSession() as session:
                routes = await discover_runpod(runpod_config, session)
            assert len(routes) == 1

    async def test_discover_runpod_multiple_accounts(self, monkeypatch):
        monkeypatch.setenv("RUNPOD_KEY_A", "key-a")
        monkeypatch.setenv("RUNPOD_KEY_B", "key-b")
        config = {
            "runpod": {
                "accounts": {
                    "team-a": {"api_key_env": "RUNPOD_KEY_A"},
                    "team-b": {"api_key_env": "RUNPOD_KEY_B"},
                }
            }
        }
        pod_a = _make_pod(pod_id="podA", name="PodA")
        pod_b = _make_pod(pod_id="podB", name="PodB")
        with aioresponses() as mocked:
            mocked.post(RUNPOD_GRAPHQL, payload=_graphql_response([pod_a]))
            mocked.post(RUNPOD_GRAPHQL, payload=_graphql_response([pod_b]))
            mocked.get(
                "https://podA-8000.proxy.runpod.net/v1/models",
                payload={"data": [{"id": "model-a"}]},
            )
            mocked.get(
                "https://podB-8000.proxy.runpod.net/v1/models",
                payload={"data": [{"id": "model-b"}]},
            )
            async with aiohttp.ClientSession() as session:
                routes = await discover_runpod(config, session)
            assert "runpod:poda/model-a" in routes
            assert "runpod:podb/model-b" in routes

    async def test_discover_runpod_multiple_models(self, runpod_config):
        pod = _make_pod()
        with aioresponses() as mocked:
            _mock_graphql_and_probe(mocked, [pod], probe_models=["model-1", "model-2"])
            async with aiohttp.ClientSession() as session:
                routes = await discover_runpod(runpod_config, session)
            assert "runpod:testpod/model-1" in routes
            assert "runpod:testpod/model-2" in routes
            assert len(routes) == 2

    async def test_discover_runpod_pod_name_sanitization(self, runpod_config):
        pod = _make_pod(name="My Cool Pod")
        with aioresponses() as mocked:
            _mock_graphql_and_probe(mocked, [pod], probe_models=["model-s"])
            async with aiohttp.ClientSession() as session:
                routes = await discover_runpod(runpod_config, session)
            # Spaces replaced with hyphens and lowered
            assert "runpod:my-cool-pod/model-s" in routes

    async def test_discover_runpod_port_substring_false_positive(self, runpod_config):
        """Port '18000' contains '8000' as substring -- the current implementation
        treats this as a match. This test documents that behavior (potential bug)."""
        pod = _make_pod(ports="18000/tcp,22/tcp")
        with aioresponses() as mocked:
            _mock_graphql_and_probe(mocked, [pod], probe_models=["model-fp"])
            async with aiohttp.ClientSession() as session:
                routes = await discover_runpod(runpod_config, session)
            # Current implementation: "8000" in "18000/tcp 22/tcp" is True
            # This is a known false positive -- the port 18000 matches the substring check
            assert len(routes) == 1  # Documents current (buggy) behavior

    async def test_discover_runpod_empty_model_list(self, runpod_config):
        pod = _make_pod()
        with aioresponses() as mocked:
            _mock_graphql_and_probe(mocked, [pod], probe_models=[])
            async with aiohttp.ClientSession() as session:
                routes = await discover_runpod(runpod_config, session)
            assert routes == {}
