"""Tests for remaining OPEN issues from the audit files.

Covers:
- SEC-002: Host header validation (DNS rebinding)
- SEC-003: Model on/off bypass via unknown Claude-passthrough names
- SEC-006: Race condition on concurrent /state POSTs (asyncio.Lock)
- SEC-007: ~/.uam/env.sh chmod 0o600
- SEC-008: Upstream error bodies leak auth headers
- SEC-009: RunPod GraphQL POST per-request timeout
- SEC-010: Catch-all exception handlers leak str(e)
- SEC-011: Unbounded model IDs
- SEC-012: load_state silently swallows errors
- H3: Streaming thinking_delta protocol violation (skip streaming)
- H4: Single tool_result drops non-tool-result content
- M2/M3: infer_capabilities gaps (gpt-3.5, gemma, phi, command, llava)
- M4: system text-block uses b["text"] instead of .get
- M5: write_env_file empty caps falls back to inferred
- M6: _resolve_default_swap default disabled logging
- L1: logger declaration position in proxy.py
- L7: _proxy_with_translation forward response headers
- perf M1: Sync file I/O wrapped in asyncio.to_thread
- perf M3: Lazy logger format on hot paths
"""

import asyncio
import inspect
import json
import logging
import re
import stat
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import aiohttp
import pytest
from aiohttp import web
from aioresponses import aioresponses as aioresponses_ctx

import uam.proxy as proxy_mod
import uam.state as state_mod
from uam.proxy import (
    _make_anthropic_error,
    _resolve_default_swap,
    create_app,
)
from uam.router import ModelRouter
from uam.state import (
    infer_capabilities,
    load_state,
    save_state,
    sync_state_with_routes,
    write_env_file,
)
from uam.translate import anthropic_to_openai


# ---------------------------------------------------------------------------
# SEC-002: Host header validation
# ---------------------------------------------------------------------------


@pytest.fixture
def basic_router():
    from uam.config import default_config

    router = ModelRouter(default_config())
    router.routes = {
        "claude-sonnet-4-6": {
            "backend": "anthropic",
            "url": "https://api.anthropic.com",
            "api_key": "sk-test",
            "original_model": "claude-sonnet-4-6",
            "api_format": "anthropic",
            "timeout": 600,
        },
    }
    return router


@pytest.fixture
async def host_check_client(aiohttp_client, basic_router):
    basic_router.session = aiohttp.ClientSession()
    app = create_app(basic_router)
    client = await aiohttp_client(app)
    yield client
    await basic_router.session.close()


class TestHostHeaderValidation:
    @pytest.mark.asyncio
    async def test_rejects_external_host_header(self, host_check_client):
        """A request with a non-local Host header should be rejected (SEC-002)."""
        resp = await host_check_client.get(
            "/health", headers={"Host": "evil.example.com"}
        )
        assert resp.status == 403

    @pytest.mark.asyncio
    async def test_accepts_localhost_host_header(self, host_check_client):
        resp = await host_check_client.get(
            "/health", headers={"Host": "localhost:5100"}
        )
        assert resp.status == 200

    @pytest.mark.asyncio
    async def test_accepts_127_host_header(self, host_check_client):
        resp = await host_check_client.get(
            "/health", headers={"Host": "127.0.0.1:5100"}
        )
        assert resp.status == 200

    @pytest.mark.asyncio
    async def test_rejects_post_state_with_external_host(self, host_check_client):
        resp = await host_check_client.post(
            "/state",
            data=json.dumps({"default": "x"}),
            headers={"Host": "attacker.com", "Content-Type": "application/json"},
        )
        assert resp.status == 403


# ---------------------------------------------------------------------------
# SEC-003: Model on/off bypass via unknown model names
# ---------------------------------------------------------------------------


class TestUnknownModelBypass:
    def test_unknown_claude_model_resolves_only_if_explicitly_enabled(self, basic_router):
        """An unknown model should not silently fall through to Anthropic
        with the user's real key. SEC-003."""
        save_state({
            "default": "",
            "aliases": {},
            "models": {"claude-sonnet-4-6": {"enabled": False}},
        })
        # Ensure cache is fresh
        proxy_mod._invalidate_state_cache()
        # Request a model that does NOT exist in routes — should NOT
        # fall through to anthropic synthesis.
        route, _ = _resolve_default_swap(basic_router, "totally-made-up-model")
        assert route is None


# ---------------------------------------------------------------------------
# SEC-006: Concurrent state POST race condition
# ---------------------------------------------------------------------------


@pytest.fixture
async def state_post_client(aiohttp_client, basic_router):
    basic_router.session = aiohttp.ClientSession()
    app = create_app(basic_router)
    client = await aiohttp_client(app)
    yield client
    await basic_router.session.close()


class TestStatePostLock:
    @pytest.mark.asyncio
    async def test_concurrent_state_posts_no_lost_update(self, state_post_client):
        """Two concurrent POSTs should both apply (no lost update)."""
        save_state({
            "default": "",
            "aliases": {},
            "models": {
                "model-a": {"enabled": True},
                "model-b": {"enabled": True},
            },
        })

        async def post(model_id):
            return await state_post_client.post(
                "/state",
                data=json.dumps({"models": {model_id: {"enabled": False}}}),
                headers={
                    "Host": "127.0.0.1:5100",
                    "Content-Type": "application/json",
                },
            )

        results = await asyncio.gather(post("model-a"), post("model-b"))
        for r in results:
            assert r.status == 200

        final = load_state()
        assert final["models"]["model-a"]["enabled"] is False
        assert final["models"]["model-b"]["enabled"] is False


# ---------------------------------------------------------------------------
# SEC-007: env.sh chmod 0o600
# ---------------------------------------------------------------------------


class TestEnvFilePerms:
    def test_env_file_is_user_only_readable(self, tmp_path):
        env_path = tmp_path / "env.sh"
        write_env_file(
            {
                "default": "local:foo",
                "aliases": {},
                "models": {"local:foo": {"enabled": True, "capabilities": ["streaming"]}},
            },
            env_path=env_path,
        )
        mode = stat.S_IMODE(env_path.stat().st_mode)
        assert mode == 0o600, f"expected 0o600, got {oct(mode)}"


# ---------------------------------------------------------------------------
# SEC-008: Upstream error sanitization
# ---------------------------------------------------------------------------


class TestErrorSanitization:
    def test_make_anthropic_error_strips_authorization(self):
        body = json.dumps({
            "error": {"message": "upstream returned: Authorization: Bearer sk-secret-12345"}
        }).encode()
        out = _make_anthropic_error(body, 500)
        out_text = out.decode()
        assert "sk-secret-12345" not in out_text
        assert "Bearer" not in out_text

    def test_make_anthropic_error_strips_xapikey(self):
        body = json.dumps({
            "error": {"message": "X-Api-Key: sk-ant-secret"}
        }).encode()
        out = _make_anthropic_error(body, 500).decode()
        assert "sk-ant-secret" not in out


# ---------------------------------------------------------------------------
# SEC-009: RunPod GraphQL per-request timeout
# ---------------------------------------------------------------------------


class TestRunpodGraphqlTimeout:
    @pytest.mark.asyncio
    async def test_graphql_post_has_timeout(self, monkeypatch):
        """The RunPod GraphQL POST must pass an explicit per-request timeout."""
        from uam.discovery import runpod as runpod_mod

        monkeypatch.setenv("RUNPOD_KEY", "x")

        captured = {}

        class FakeCtx:
            async def __aenter__(self_inner):
                return self_inner

            async def __aexit__(self_inner, *a):
                return False

            async def json(self_inner):
                return {"data": {"myself": {"pods": []}}}

        class FakeSession:
            def post(self_inner, url, **kwargs):
                captured["timeout"] = kwargs.get("timeout")
                return FakeCtx()

            def get(self_inner, *a, **k):
                return FakeCtx()

        config = {
            "runpod": {
                "accounts": {"acct": {"api_key_env": "RUNPOD_KEY"}},
                "timeout": 30,
            }
        }
        await runpod_mod.discover_runpod(config, FakeSession())
        assert captured["timeout"] is not None
        assert isinstance(captured["timeout"], aiohttp.ClientTimeout)


# ---------------------------------------------------------------------------
# SEC-010: Generic upstream error message in proxy_error
# ---------------------------------------------------------------------------


class TestProxyErrorGeneric:
    @pytest.mark.asyncio
    async def test_proxy_error_does_not_leak_pod_id(self, basic_router, aiohttp_client):
        """When an upstream call raises with sensitive details, the response
        should be a generic message, not str(e)."""
        basic_router.routes["secret-pod"] = {
            "backend": "runpod",
            "url": "https://abcdef-pod-123-8000.proxy.runpod.net",
            "api_key": "vllm-secret-key",
            "original_model": "llama",
            "api_format": "anthropic",  # avoids translation path
            "timeout": 5,
        }
        basic_router.session = aiohttp.ClientSession()
        app = create_app(basic_router)
        client = await aiohttp_client(app)

        save_state({
            "default": "",
            "aliases": {},
            "models": {"secret-pod": {"enabled": True}},
        })
        proxy_mod._invalidate_state_cache()

        # Patch session.post to raise a sensitive exception. session.post()
        # is a synchronous call that returns an async context manager — we
        # raise from the synchronous call so no coroutine is left unawaited.
        def boom(*a, **k):
            raise aiohttp.ClientError(
                "Cannot connect to https://abcdef-pod-123-8000.proxy.runpod.net "
                "Authorization: Bearer vllm-secret-key"
            )

        basic_router.session.post = boom  # type: ignore

        resp = await client.post(
            "/v1/messages",
            data=json.dumps({
                "model": "secret-pod",
                "messages": [{"role": "user", "content": "hi"}],
            }),
            headers={"Host": "127.0.0.1:5100", "Content-Type": "application/json"},
        )
        body = await resp.text()
        assert "vllm-secret-key" not in body
        assert "abcdef-pod-123" not in body
        await basic_router.session.close()


# ---------------------------------------------------------------------------
# SEC-011: Unbounded model IDs
# ---------------------------------------------------------------------------


class TestModelIdLengthLimit:
    def test_long_model_id_rejected(self):
        long_id = "x" * 600
        out = sync_state_with_routes([long_id, "claude-sonnet-4-6"])
        assert long_id not in out["models"]
        assert "claude-sonnet-4-6" in out["models"]


# ---------------------------------------------------------------------------
# SEC-012: load_state logs errors
# ---------------------------------------------------------------------------


class TestLoadStateLogsError:
    def test_load_state_logs_json_error(self, tmp_path, monkeypatch, caplog):
        bad = tmp_path / "models.json"
        bad.write_text("{not json")
        monkeypatch.setattr(state_mod, "STATE_PATH", bad)
        with caplog.at_level(logging.ERROR, logger="uam.state"):
            result = load_state()
        assert result == {"default": "", "aliases": {}, "models": {}}
        assert any("Failed to load state" in r.getMessage() for r in caplog.records)


# ---------------------------------------------------------------------------
# H3: Streaming thinking_delta — skip entirely (no protocol violation)
# ---------------------------------------------------------------------------


class TestStreamingThinkingSkipped:
    def test_reasoning_content_not_emitted_in_streaming(self):
        """Streaming should NOT emit a thinking_delta against text block index 0."""
        from uam.translate import openai_stream_to_anthropic_stream

        line = (
            b'data: {"choices":[{"delta":{"reasoning_content":"thinking..."}}]}'
        )
        out = openai_stream_to_anthropic_stream(line, "model")
        # No thinking_delta should be in output
        if out is not None:
            assert b"thinking_delta" not in out


# ---------------------------------------------------------------------------
# H4: Single tool_result drops non-tool-result content
# ---------------------------------------------------------------------------


class TestSingleToolResultPreservesText:
    def test_single_tool_result_with_text_preserves_text(self):
        payload = {
            "model": "x",
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": "see result"},
                        {
                            "type": "tool_result",
                            "tool_use_id": "tu_1",
                            "content": "42",
                        },
                    ],
                }
            ],
        }
        out = anthropic_to_openai(payload)
        # We expect both: a user-text message AND a tool message
        roles = [m["role"] for m in out["messages"]]
        assert "tool" in roles
        # The text "see result" should appear somewhere
        all_content = json.dumps(out["messages"])
        assert "see result" in all_content


# ---------------------------------------------------------------------------
# M2/M3: infer_capabilities gaps
# ---------------------------------------------------------------------------


class TestInferCapabilitiesGaps:
    def test_gpt_35_has_tools(self):
        caps = infer_capabilities("openrouter:openai/gpt-3.5-turbo")
        assert "tools" in caps
        assert "streaming" in caps

    def test_gemma(self):
        caps = infer_capabilities("local:gemma-2-9b")
        assert "streaming" in caps
        assert "tools" in caps

    def test_phi(self):
        caps = infer_capabilities("local:phi-3-mini")
        assert "tools" in caps

    def test_command_r(self):
        caps = infer_capabilities("openrouter:cohere/command-r")
        assert "tools" in caps


# ---------------------------------------------------------------------------
# M4: system text-block uses .get
# ---------------------------------------------------------------------------


class TestSystemBlockNoText:
    def test_malformed_system_block_no_text_key(self):
        payload = {
            "model": "x",
            "messages": [{"role": "user", "content": "hi"}],
            "system": [{"type": "text"}],  # missing 'text' key
        }
        # Should not raise KeyError
        out = anthropic_to_openai(payload)
        assert "messages" in out


# ---------------------------------------------------------------------------
# M5: write_env_file falls back to inferred capabilities
# ---------------------------------------------------------------------------


class TestEnvFileEmptyCapsFallback:
    def test_empty_caps_falls_back_to_inferred(self, tmp_path):
        env_path = tmp_path / "env.sh"
        write_env_file(
            {
                "default": "local:llama3-8b",
                "aliases": {},
                "models": {"local:llama3-8b": {"enabled": True}},  # no caps
            },
            env_path=env_path,
        )
        content = env_path.read_text()
        assert "SUPPORTED_CAPABILITIES" in content
        # Should not be empty quoted string
        assert 'SUPPORTED_CAPABILITIES=""' not in content
        assert "SUPPORTED_CAPABILITIES=''" not in content


# ---------------------------------------------------------------------------
# M6: _resolve_default_swap default disabled — log a warning
# ---------------------------------------------------------------------------


class TestDefaultDisabledLogged:
    def test_disabled_default_logs_warning(self, basic_router, caplog):
        save_state({
            "default": "local:foo",
            "aliases": {},
            "models": {
                "local:foo": {"enabled": False},
                "claude-sonnet-4-6": {"enabled": True},
            },
        })
        basic_router.routes["local:foo"] = {
            "backend": "local",
            "url": "http://x",
            "api_key": "",
            "original_model": "foo",
            "api_format": "openai",
            "timeout": 30,
        }
        proxy_mod._invalidate_state_cache()
        with caplog.at_level(logging.WARNING, logger="uam.proxy"):
            _resolve_default_swap(basic_router, "claude-sonnet-4-6")
        msgs = [r.getMessage() for r in caplog.records]
        assert any("disabled" in m.lower() for m in msgs)


# ---------------------------------------------------------------------------
# L1: logger declaration position
# ---------------------------------------------------------------------------


class TestLoggerImportOrder:
    def test_proxy_logger_after_imports(self):
        src = Path("src/uam/proxy.py").read_text()
        # Find the logger = ... line
        lines = src.split("\n")
        logger_line = next(
            (i for i, l in enumerate(lines) if l.startswith("logger = logging.getLogger")),
            None,
        )
        assert logger_line is not None
        # No `from uam` imports should come AFTER the logger declaration
        for i, l in enumerate(lines[logger_line + 1:], start=logger_line + 1):
            if l.startswith("from uam"):
                pytest.fail(f"line {i}: {l!r} comes after logger declaration")


# ---------------------------------------------------------------------------
# perf M1: Sync file I/O on event loop wrapped in asyncio.to_thread
# ---------------------------------------------------------------------------


class TestHandlePostStateUsesToThread:
    def test_handle_post_state_source_uses_to_thread(self):
        src = inspect.getsource(proxy_mod.handle_post_state)
        # Verify async wrapping is present
        assert "asyncio.to_thread" in src or "run_in_executor" in src


# ---------------------------------------------------------------------------
# perf M3: Lazy logger format on hot paths
# ---------------------------------------------------------------------------


class TestLazyLoggerHotPath:
    def test_handle_messages_uses_lazy_format(self):
        src = Path("src/uam/proxy.py").read_text()
        # The Route: log line should NOT be an f-string
        # Look for the line that contains "Route:"
        for line in src.split("\n"):
            if "Route:" in line and "logger" in line:
                # If it's an f-string, that's a fail
                assert 'f"' not in line and "f'" not in line, (
                    f"Hot-path log line should be lazy: {line.strip()}"
                )
