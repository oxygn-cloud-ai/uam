"""RED-phase tests for uam Phase 1: Logging + Error Handling + Timeouts.

These tests import from uam.log and uam.config functions/modules that do NOT
exist yet.  Every test here is expected to FAIL (ImportError or AssertionError)
until the GREEN implementation is written.
"""

import json
import logging
import os
from logging.handlers import RotatingFileHandler
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import aiohttp
import pytest
from aiohttp import web
from aiohttp.test_utils import AioHTTPTestCase, TestClient

from uam.router import ModelRouter
from uam.state import save_state


# ---------------------------------------------------------------------------
# Logging setup tests (uam.log module — does not exist yet)
# ---------------------------------------------------------------------------


class TestSetupLogging:
    """Tests for setup_logging() from the new uam.log module."""

    def test_setup_logging_creates_file_handler(self, tmp_uam_dir, monkeypatch):
        """setup_logging() attaches a RotatingFileHandler pointing at ~/.uam/uam.log."""
        from uam.log import setup_logging

        log_dir = tmp_uam_dir / ".uam"
        log_dir.mkdir(exist_ok=True)
        monkeypatch.setattr("uam.log.LOG_DIR", log_dir)

        setup_logging()

        root = logging.getLogger("uam")
        file_handlers = [
            h for h in root.handlers if isinstance(h, RotatingFileHandler)
        ]
        assert len(file_handlers) >= 1, "Expected at least one RotatingFileHandler"
        assert Path(file_handlers[0].baseFilename) == log_dir / "uam.log"

    def test_setup_logging_default_level_warning(self, tmp_uam_dir, monkeypatch):
        """With no UAM_LOG_LEVEL env var, root logger level should be WARNING."""
        from uam.log import setup_logging

        log_dir = tmp_uam_dir / ".uam"
        log_dir.mkdir(exist_ok=True)
        monkeypatch.setattr("uam.log.LOG_DIR", log_dir)
        monkeypatch.delenv("UAM_LOG_LEVEL", raising=False)

        setup_logging()

        root = logging.getLogger("uam")
        assert root.level == logging.WARNING

    def test_setup_logging_debug_from_env(self, tmp_uam_dir, monkeypatch):
        """UAM_LOG_LEVEL=DEBUG sets root logger to DEBUG."""
        from uam.log import setup_logging

        log_dir = tmp_uam_dir / ".uam"
        log_dir.mkdir(exist_ok=True)
        monkeypatch.setattr("uam.log.LOG_DIR", log_dir)
        monkeypatch.setenv("UAM_LOG_LEVEL", "DEBUG")

        setup_logging()

        root = logging.getLogger("uam")
        assert root.level == logging.DEBUG

    def test_setup_logging_info_from_env(self, tmp_uam_dir, monkeypatch):
        """UAM_LOG_LEVEL=INFO sets root logger to INFO."""
        from uam.log import setup_logging

        log_dir = tmp_uam_dir / ".uam"
        log_dir.mkdir(exist_ok=True)
        monkeypatch.setattr("uam.log.LOG_DIR", log_dir)
        monkeypatch.setenv("UAM_LOG_LEVEL", "INFO")

        setup_logging()

        root = logging.getLogger("uam")
        assert root.level == logging.INFO

    def test_setup_logging_rotation(self, tmp_uam_dir, monkeypatch):
        """RotatingFileHandler max_bytes is 5 MB and backup_count is 3."""
        from uam.log import setup_logging

        log_dir = tmp_uam_dir / ".uam"
        log_dir.mkdir(exist_ok=True)
        monkeypatch.setattr("uam.log.LOG_DIR", log_dir)

        setup_logging()

        root = logging.getLogger("uam")
        file_handlers = [
            h for h in root.handlers if isinstance(h, RotatingFileHandler)
        ]
        assert len(file_handlers) >= 1
        handler = file_handlers[0]
        assert handler.maxBytes == 5 * 1024 * 1024
        assert handler.backupCount == 3

    def test_setup_logging_format(self, tmp_uam_dir, monkeypatch):
        """Log format includes asctime, levelname, name, and message."""
        from uam.log import setup_logging

        log_dir = tmp_uam_dir / ".uam"
        log_dir.mkdir(exist_ok=True)
        monkeypatch.setattr("uam.log.LOG_DIR", log_dir)

        setup_logging()

        root = logging.getLogger("uam")
        file_handlers = [
            h for h in root.handlers if isinstance(h, RotatingFileHandler)
        ]
        assert len(file_handlers) >= 1
        fmt = file_handlers[0].formatter._fmt
        assert "%(asctime)s" in fmt
        assert "%(levelname)s" in fmt
        assert "%(name)s" in fmt
        assert "%(message)s" in fmt

    def test_setup_logging_creates_parent_dir(self, tmp_uam_dir, monkeypatch):
        """If the log directory doesn't exist, setup_logging() creates it."""
        from uam.log import setup_logging

        log_dir = tmp_uam_dir / ".uam_nonexistent"
        assert not log_dir.exists()
        monkeypatch.setattr("uam.log.LOG_DIR", log_dir)

        setup_logging()

        assert log_dir.exists()


# ---------------------------------------------------------------------------
# Redaction tests (uam.log.redact_headers — does not exist yet)
# ---------------------------------------------------------------------------


class TestRedactHeaders:
    """Tests for redact_headers() from the new uam.log module."""

    def test_redact_headers_removes_api_key(self):
        """X-Api-Key header value is replaced with [REDACTED]."""
        from uam.log import redact_headers

        headers = {"X-Api-Key": "sk-secret-value", "Content-Type": "application/json"}
        result = redact_headers(headers)
        assert result["X-Api-Key"] == "[REDACTED]"
        assert result["Content-Type"] == "application/json"

    def test_redact_headers_removes_authorization(self):
        """Authorization header value is replaced with [REDACTED]."""
        from uam.log import redact_headers

        headers = {"Authorization": "Bearer sk-or-secret-1234"}
        result = redact_headers(headers)
        assert result["Authorization"] == "[REDACTED]"

    def test_redact_headers_preserves_safe_headers(self):
        """Non-sensitive headers pass through unchanged."""
        from uam.log import redact_headers

        headers = {"anthropic-version": "2023-06-01", "Content-Type": "application/json"}
        result = redact_headers(headers)
        assert result == headers


# ---------------------------------------------------------------------------
# Per-backend timeout tests (uam.config — new functions)
# ---------------------------------------------------------------------------


class TestBackendTimeouts:
    """Tests for per-backend timeout configuration."""

    def test_default_config_has_timeouts(self):
        """default_config() includes timeout values for every backend."""
        from uam.config import default_config

        config = default_config()
        assert config["anthropic"]["timeout"] == 600
        assert config["runpod"]["timeout"] == 300
        assert config["openrouter"]["timeout"] == 300
        assert config["local"]["timeout"] == 120

    def test_get_backend_timeout_anthropic(self):
        """get_backend_timeout returns 600 for anthropic."""
        from uam.config import default_config, get_backend_timeout

        config = default_config()
        assert get_backend_timeout(config, "anthropic") == 600

    def test_get_backend_timeout_local(self):
        """get_backend_timeout returns 120 for local."""
        from uam.config import default_config, get_backend_timeout

        config = default_config()
        assert get_backend_timeout(config, "local") == 120

    def test_get_backend_timeout_custom(self):
        """get_backend_timeout returns a custom override when set."""
        from uam.config import default_config, get_backend_timeout

        config = default_config()
        config["anthropic"]["timeout"] = 900
        assert get_backend_timeout(config, "anthropic") == 900

    def test_get_backend_timeout_missing_uses_default(self):
        """When a backend section has no timeout key, fall back to default."""
        from uam.config import get_backend_timeout

        config = {"anthropic": {"url": "https://api.anthropic.com"}}
        result = get_backend_timeout(config, "anthropic")
        assert result == 600, "Expected default timeout of 600 for anthropic"


# ---------------------------------------------------------------------------
# Proxy error handling tests
# ---------------------------------------------------------------------------


class TestProxyErrorHandling:
    """Tests for improved error responses from the proxy layer."""

    @pytest.fixture
    def test_routes(self):
        return {
            "openrouter:google/gemini-2.0-flash": {
                "backend": "openrouter",
                "url": "https://openrouter.ai/api",
                "api_key": "or-test-key",
                "original_model": "google/gemini-2.0-flash",
            },
        }

    @pytest.fixture
    def app_client(self, test_routes, tmp_uam_dir, aiohttp_client):
        """Create a test client with routes pre-loaded."""

        async def make_client():
            config = {
                "listen": "127.0.0.1:5100",
                "anthropic": {"url": "https://api.anthropic.com", "api_key_env": ""},
                "default_backend": "anthropic",
            }
            router = ModelRouter(config)
            router.routes = test_routes
            router.session = aiohttp.ClientSession()

            # Save a state so proxy can load it
            save_state({
                "default": "openrouter:google/gemini-2.0-flash",
                "aliases": {},
                "models": {
                    "openrouter:google/gemini-2.0-flash": {"enabled": True},
                },
            })

            from uam.proxy import create_app
            app = create_app(router)
            return app

        return make_client

    @pytest.mark.asyncio
    async def test_proxy_translation_error_returns_502(
        self, app_client, aiohttp_client
    ):
        """When anthropic_to_openai raises, handle_messages returns 502."""
        app = await app_client()
        client = await aiohttp_client(app)

        with patch("uam.proxy.anthropic_to_openai", side_effect=ValueError("bad format")):
            resp = await client.post(
                "/v1/messages",
                json={
                    "model": "claude-sonnet-4-6",
                    "messages": [{"role": "user", "content": "hi"}],
                    "max_tokens": 100,
                },
            )
            assert resp.status == 502
            body = await resp.json()
            assert "error" in body
            assert "bad format" in body["error"]["message"]

    @pytest.mark.asyncio
    async def test_proxy_error_has_no_retry_header(self, app_client, aiohttp_client):
        """Error responses from translation failures include x-should-retry: false."""
        app = await app_client()
        client = await aiohttp_client(app)

        with patch("uam.proxy.anthropic_to_openai", side_effect=ValueError("fail")):
            resp = await client.post(
                "/v1/messages",
                json={
                    "model": "claude-sonnet-4-6",
                    "messages": [{"role": "user", "content": "hi"}],
                    "max_tokens": 100,
                },
            )
            assert resp.status == 502
            assert resp.headers.get("x-should-retry") == "false"


# ---------------------------------------------------------------------------
# Router logging tests
# ---------------------------------------------------------------------------


class TestRouterLogging:
    """Tests that ModelRouter emits log messages during discovery."""

    @pytest.mark.asyncio
    async def test_router_logs_discovery_start(self, tmp_uam_dir, caplog):
        """During start(), an INFO log message about discovery is emitted."""
        config = {
            "anthropic": {"url": "https://api.anthropic.com", "api_key_env": ""},
            "default_backend": "anthropic",
        }
        router = ModelRouter(config)

        with caplog.at_level(logging.INFO, logger="uam.router"):
            await router.start(skip_discovery=True)

        assert any(
            "discovery" in record.message.lower() for record in caplog.records
        ), f"Expected a log message about discovery, got: {[r.message for r in caplog.records]}"

        await router.stop()

    @pytest.mark.asyncio
    async def test_router_logs_model_count(self, tmp_uam_dir, caplog):
        """After discovery, logs 'N models available' at INFO level."""
        config = {
            "anthropic": {"url": "https://api.anthropic.com", "api_key_env": ""},
            "default_backend": "anthropic",
        }
        router = ModelRouter(config)

        with caplog.at_level(logging.INFO, logger="uam.router"):
            await router.start(skip_discovery=True)

        assert any(
            "models available" in record.message.lower() for record in caplog.records
        ), f"Expected 'models available' log, got: {[r.message for r in caplog.records]}"

        await router.stop()


# ---------------------------------------------------------------------------
# Integration: proxy logging
# ---------------------------------------------------------------------------


class TestProxyLogging:
    """Integration test: proxy logs route resolution at DEBUG level."""

    @pytest.mark.asyncio
    async def test_proxy_logs_route_resolution(
        self, tmp_uam_dir, aiohttp_client, caplog
    ):
        """handle_messages logs model resolution at DEBUG level."""
        from uam.proxy import create_app

        config = {
            "listen": "127.0.0.1:5100",
            "anthropic": {
                "url": "https://api.anthropic.com",
                "api_key_env": "ANTHROPIC_API_KEY_REAL",
            },
            "default_backend": "anthropic",
        }
        router = ModelRouter(config)
        router.routes = {
            "claude-sonnet-4-6": {
                "backend": "anthropic",
                "url": "https://api.anthropic.com",
                "api_key": "sk-test",
                "original_model": "claude-sonnet-4-6",
            },
        }
        router.session = aiohttp.ClientSession()

        save_state({
            "default": "",
            "aliases": {},
            "models": {"claude-sonnet-4-6": {"enabled": True}},
        })

        app = create_app(router)
        client = await aiohttp_client(app)

        # Mock the upstream so we don't make real HTTP calls
        mock_resp = MagicMock()
        mock_resp.status = 200
        mock_resp.headers = {"Content-Type": "application/json"}
        mock_resp.read = AsyncMock(return_value=json.dumps({
            "id": "msg_test",
            "type": "message",
            "role": "assistant",
            "content": [{"type": "text", "text": "hello"}],
            "model": "claude-sonnet-4-6",
            "stop_reason": "end_turn",
            "usage": {"input_tokens": 10, "output_tokens": 5},
        }).encode())

        ctx_manager = AsyncMock()
        ctx_manager.__aenter__ = AsyncMock(return_value=mock_resp)
        ctx_manager.__aexit__ = AsyncMock(return_value=False)

        with caplog.at_level(logging.DEBUG, logger="uam.proxy"):
            with patch.object(router.session, "post", return_value=ctx_manager):
                resp = await client.post(
                    "/v1/messages",
                    json={
                        "model": "claude-sonnet-4-6",
                        "messages": [{"role": "user", "content": "hi"}],
                        "max_tokens": 100,
                    },
                )

        # The response should succeed (proxy works)
        assert resp.status == 200

        # Check that route resolution was logged at DEBUG level
        debug_messages = [
            r.message for r in caplog.records if r.levelno == logging.DEBUG
        ]
        assert any(
            "claude-sonnet-4-6" in msg for msg in debug_messages
        ), f"Expected DEBUG log with model name, got: {debug_messages}"

        await router.session.close()
