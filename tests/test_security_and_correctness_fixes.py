"""Tests for SEC-001, SEC-004, SEC-005, C1, H1, H2, perf H1, and discovery print fixes.

Each test corresponds to a specific issue from SECURITY_ISSUES.md /
PERFORMANCE_ISSUES.md / CODE_ISSUES.md.
"""

import asyncio
import json
import logging
import subprocess
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import aiohttp
import pytest
from aiohttp import web
from aioresponses import aioresponses as aioresponses_ctx
from multidict import CIMultiDict

import uam.state as state_mod
from uam.proxy import (
    _retry_headers,
    _build_upstream_headers,
    create_app,
)
from uam.router import ModelRouter
from uam.state import save_state, write_env_file


# ---------------------------------------------------------------------------
# 1. SEC-001: Shell injection in write_env_file
# ---------------------------------------------------------------------------


class TestWriteEnvFileShellInjectionSafe:
    def test_malicious_default_does_not_execute(self, tmp_path):
        """Malicious model id is preserved literally; sourcing the file does
        not execute injected code."""
        env_path = tmp_path / "env.sh"
        marker = tmp_path / "pwned"
        # Attempt to inject a command that would create `marker` when sourced
        malicious = f'x";touch {marker};echo "'
        state = {
            "default": malicious,
            "aliases": {malicious: "evilalias"},
            "models": {malicious: {"enabled": True, "capabilities": ["streaming"]}},
        }
        write_env_file(state, env_path=env_path)

        # File contains the literal characters (escaped/quoted), not bare
        content = env_path.read_text()
        assert malicious in content or "touch" in content  # the literal text appears

        # Critically: sourcing the file in a clean shell must NOT create marker
        result = subprocess.run(
            ["sh", "-c", f". {env_path}"],
            capture_output=True,
            text=True,
        )
        assert not marker.exists(), (
            f"Shell injection executed! marker created. "
            f"stdout={result.stdout!r} stderr={result.stderr!r}\n"
            f"file content:\n{content}"
        )

    def test_malicious_friendly_name_does_not_execute(self, tmp_path):
        env_path = tmp_path / "env.sh"
        marker = tmp_path / "pwned2"
        malicious_alias = f'evil";touch {marker};echo "'
        model_id = "local:safe-model"
        state = {
            "default": model_id,
            "aliases": {malicious_alias: model_id},
            "models": {model_id: {"enabled": True, "capabilities": ["streaming"]}},
        }
        write_env_file(state, env_path=env_path)
        subprocess.run(["sh", "-c", f". {env_path}"], capture_output=True)
        assert not marker.exists()

    def test_malicious_capabilities_does_not_execute(self, tmp_path):
        env_path = tmp_path / "env.sh"
        marker = tmp_path / "pwned3"
        model_id = "local:safe-model"
        state = {
            "default": model_id,
            "aliases": {},
            "models": {
                model_id: {
                    "enabled": True,
                    "capabilities": [f'streaming";touch {marker};echo "'],
                }
            },
        }
        write_env_file(state, env_path=env_path)
        subprocess.run(["sh", "-c", f". {env_path}"], capture_output=True)
        assert not marker.exists()


# ---------------------------------------------------------------------------
# 2. C1: _retry_headers must support canonical-case Retry-After
# ---------------------------------------------------------------------------


class TestRetryHeadersCanonicalCase:
    def test_retry_headers_canonical_case_dict(self):
        """Plain dict with canonical-case Retry-After should still work."""
        headers = _retry_headers(503, {"Retry-After": "5"})
        assert headers.get("retry-after") == "5"

    def test_retry_headers_uppercase(self):
        headers = _retry_headers(429, {"RETRY-AFTER": "10"})
        assert headers.get("retry-after") == "10"

    def test_retry_headers_lowercase_still_works(self):
        headers = _retry_headers(429, {"retry-after": "7"})
        assert headers.get("retry-after") == "7"

    def test_retry_headers_cimultidict(self):
        """CIMultiDict (real aiohttp upstream.headers type) works."""
        cim = CIMultiDict([("Retry-After", "3"), ("Retry-After-Ms", "3000")])
        headers = _retry_headers(503, cim)
        assert headers.get("retry-after") == "3"
        assert headers.get("retry-after-ms") == "3000"


# ---------------------------------------------------------------------------
# 3. H1 (CodeAuditor): per-backend timeouts wired to routes
# ---------------------------------------------------------------------------


class TestRouteTimeouts:
    @pytest.mark.asyncio
    async def test_local_route_has_timeout(self):
        from uam.discovery.local import discover_local
        from aioresponses import aioresponses

        config = {"local": {"probe_ports": [11434], "servers": [], "timeout": 120}}
        with aioresponses() as mocked:
            mocked.get(
                "http://127.0.0.1:11434/v1/models",
                payload={"data": [{"id": "llama3.1"}]},
            )
            async with aiohttp.ClientSession() as session:
                routes = await discover_local(config, session)
        assert routes["local:llama3.1"]["timeout"] == 120

    def test_anthropic_route_has_timeout(self):
        from uam.discovery.anthropic import discover_anthropic

        config = {
            "anthropic": {
                "url": "https://api.anthropic.com",
                "api_key_env": "X",
                "timeout": 600,
            }
        }
        routes = discover_anthropic(config)
        for route in routes.values():
            assert route["timeout"] == 600

    @pytest.mark.asyncio
    async def test_openrouter_route_has_timeout(self, monkeypatch):
        from uam.discovery.openrouter import discover_openrouter
        from aioresponses import aioresponses

        monkeypatch.setenv("OPENROUTER_API_KEY", "test")
        config = {
            "openrouter": {
                "url": "https://openrouter.ai/api",
                "api_key_env": "OPENROUTER_API_KEY",
                "timeout": 300,
            }
        }
        with aioresponses() as mocked:
            mocked.get(
                "https://openrouter.ai/api/v1/models",
                payload={"data": [{"id": "google/gemini-flash"}]},
            )
            async with aiohttp.ClientSession() as session:
                routes = await discover_openrouter(config, session)
        for route in routes.values():
            assert route["timeout"] == 300


# ---------------------------------------------------------------------------
# 4. H2: extract_think_tags wired into proxy
# ---------------------------------------------------------------------------


@pytest.fixture
def test_routes_with_local():
    return {
        "claude-sonnet-4-6": {
            "backend": "anthropic",
            "url": "https://api.anthropic.com",
            "api_key": "sk-test",
            "original_model": "claude-sonnet-4-6",
            "api_format": "anthropic",
            "timeout": 600,
        },
        "local:think-model": {
            "backend": "local",
            "url": "http://127.0.0.1:11434",
            "api_key": "",
            "original_model": "think-model",
            "api_format": "openai",
            "timeout": 120,
        },
    }


@pytest.fixture
async def app_client_with_local(aiohttp_client, test_routes_with_local):
    from uam.config import default_config

    router = ModelRouter(default_config())
    router.routes = dict(test_routes_with_local)
    router.session = aiohttp.ClientSession()
    app = create_app(router)
    client = await aiohttp_client(app)
    yield client
    await router.session.close()


def _mock_upstream_for(client):
    base = str(client.make_url("")).rstrip("/")
    return aioresponses_ctx(passthrough=[base])


class TestProxyExtractsThinkTags:
    @pytest.mark.asyncio
    async def test_proxy_extracts_think_tags_nonstreaming(self, app_client_with_local):
        save_state({
            "default": "",
            "aliases": {},
            "models": {"local:think-model": {"enabled": True}},
        })
        upstream_response = {
            "id": "x",
            "object": "chat.completion",
            "model": "think-model",
            "choices": [
                {
                    "index": 0,
                    "message": {
                        "role": "assistant",
                        "content": "<think>step 1</think>final answer",
                    },
                    "finish_reason": "stop",
                }
            ],
            "usage": {"prompt_tokens": 5, "completion_tokens": 10},
        }
        with _mock_upstream_for(app_client_with_local) as m:
            m.post(
                "http://127.0.0.1:11434/v1/chat/completions",
                payload=upstream_response,
            )
            resp = await app_client_with_local.post(
                "/v1/messages",
                data=json.dumps({
                    "model": "local:think-model",
                    "messages": [{"role": "user", "content": "hi"}],
                    "stream": False,
                }),
                headers={"Content-Type": "application/json"},
            )
        assert resp.status == 200
        data = await resp.json()
        # Expect a thinking block followed by a text block
        types = [b["type"] for b in data["content"]]
        assert "thinking" in types
        thinking_block = next(b for b in data["content"] if b["type"] == "thinking")
        assert thinking_block["thinking"] == "step 1"
        text_block = next(b for b in data["content"] if b["type"] == "text")
        assert text_block["text"] == "final answer"


# ---------------------------------------------------------------------------
# 5. perf H1: SSE buffer size limit + linear scan
# ---------------------------------------------------------------------------


class TestStreamBufferSizeLimit:
    @pytest.mark.asyncio
    async def test_stream_buffer_size_limit(self, app_client_with_local):
        """A pathological upstream sending >1MB without a newline must not OOM."""
        save_state({
            "default": "",
            "aliases": {},
            "models": {"local:think-model": {"enabled": True}},
        })
        # 2MB of data with no newline
        huge = b"X" * (2 * 1024 * 1024)
        with _mock_upstream_for(app_client_with_local) as m:
            m.post(
                "http://127.0.0.1:11434/v1/chat/completions",
                body=huge,
                content_type="text/event-stream",
                status=200,
            )
            resp = await app_client_with_local.post(
                "/v1/messages",
                data=json.dumps({
                    "model": "local:think-model",
                    "messages": [{"role": "user", "content": "hi"}],
                    "stream": True,
                }),
                headers={"Content-Type": "application/json"},
            )
            # Should not hang or OOM — should close cleanly
            body = await resp.read()
        # Stream closes; we don't care about exact body, just no crash/OOM
        assert resp.status == 200


# ---------------------------------------------------------------------------
# 6. SEC-005: Atomic state file writes
# ---------------------------------------------------------------------------


class TestSaveStateAtomic:
    def test_save_state_atomic_no_partial_file(self, tmp_path, monkeypatch):
        """If write fails mid-way, original file is preserved (atomic)."""
        state_path = tmp_path / "models.json"
        monkeypatch.setattr(state_mod, "STATE_PATH", state_path)

        original = {"default": "model-a", "aliases": {}, "models": {"a": {"enabled": True}}}
        state_mod.save_state(original)
        assert state_path.exists()
        original_content = state_path.read_text()

        # Patch os.replace to simulate a failure mid-write
        import os
        real_replace = os.replace

        def fail_replace(src, dst):
            raise OSError("simulated failure")

        monkeypatch.setattr("os.replace", fail_replace)

        new_state = {"default": "model-b", "aliases": {}, "models": {}}
        with pytest.raises(OSError):
            state_mod.save_state(new_state)

        # Original file unchanged
        assert state_path.read_text() == original_content
        # No leftover .tmp files
        leftovers = list(tmp_path.glob(".models.*.tmp"))
        assert leftovers == [], f"leftover tmp files: {leftovers}"

    def test_save_state_atomic_uses_rename(self, tmp_path, monkeypatch):
        """save_state should use atomic rename (write tmp + os.replace)."""
        state_path = tmp_path / "models.json"
        monkeypatch.setattr(state_mod, "STATE_PATH", state_path)

        import os
        replace_calls = []
        real_replace = os.replace

        def tracked_replace(src, dst):
            replace_calls.append((str(src), str(dst)))
            return real_replace(src, dst)

        monkeypatch.setattr("os.replace", tracked_replace)

        state_mod.save_state({"default": "x", "aliases": {}, "models": {}})
        assert len(replace_calls) == 1
        src, dst = replace_calls[0]
        assert dst == str(state_path)
        assert src != str(state_path)  # written to a different (tmp) path first


# ---------------------------------------------------------------------------
# 7. SEC-004: redact_headers wired into _build_upstream_headers debug log
# ---------------------------------------------------------------------------


class TestBuildHeadersLogsRedacted:
    def test_build_headers_redacts_api_key_in_debug_log(self, caplog):
        from uam.log import redact_headers  # ensure importable

        route = {
            "backend": "anthropic",
            "url": "https://api.anthropic.com",
            "api_key": "sk-secret-leaked-key-12345",
            "original_model": "claude-sonnet-4-6",
        }
        with caplog.at_level(logging.DEBUG, logger="uam.proxy"):
            headers = _build_upstream_headers(None, route)

        # The header itself contains the real key (used for upstream call)
        assert headers["X-Api-Key"] == "sk-secret-leaked-key-12345"

        # But no log line should contain it
        all_log_text = " ".join(r.getMessage() for r in caplog.records)
        assert "sk-secret-leaked-key-12345" not in all_log_text
        # And there should be at least one debug record from proxy logging headers
        proxy_records = [r for r in caplog.records if r.name == "uam.proxy"]
        assert len(proxy_records) >= 1

    def test_build_headers_redacts_bearer_token(self, caplog):
        route = {
            "backend": "openrouter",
            "url": "https://openrouter.ai/api",
            "api_key": "or-secret-token-67890",
            "original_model": "x",
        }
        with caplog.at_level(logging.DEBUG, logger="uam.proxy"):
            _build_upstream_headers(None, route)
        all_log_text = " ".join(r.getMessage() for r in caplog.records)
        assert "or-secret-token-67890" not in all_log_text


# ---------------------------------------------------------------------------
# 8. discovery/local.py:80 print() → logger.info()
# ---------------------------------------------------------------------------


class TestLocalDiscoveryNoPrint:
    @pytest.mark.asyncio
    async def test_local_discovery_uses_logger_not_print(self, capsys, caplog):
        from uam.discovery.local import discover_local
        from aioresponses import aioresponses

        config = {"local": {"probe_ports": [11434], "servers": []}}
        with caplog.at_level(logging.INFO, logger="uam.discovery.local"):
            with aioresponses() as mocked:
                mocked.get(
                    "http://127.0.0.1:11434/v1/models",
                    payload={"data": [{"id": "llama3.1"}]},
                )
                async with aiohttp.ClientSession() as session:
                    await discover_local(config, session)

        # Nothing should have been printed to stdout
        captured = capsys.readouterr()
        assert "[local:" not in captured.out
        assert "llama3.1" not in captured.out

        # The route should have been logged via logger
        info_messages = [r.getMessage() for r in caplog.records]
        assert any("llama3.1" in m for m in info_messages)
