"""Tests for uam.__main__ — server entry point."""

import os
import sys
from pathlib import Path
from unittest.mock import patch, MagicMock, AsyncMock

from aiohttp import web

from uam.__main__ import main, PID_FILE
import uam.__main__ as main_mod


def test_skip_discovery_flag(default_config, monkeypatch, tmp_uam_dir):
    """--skip-discovery flag passes skip_discovery=True to router.start."""
    monkeypatch.setattr(sys, "argv", ["uam", "--skip-discovery"])

    captured = {}

    async def mock_start(self, skip_discovery=False):
        captured["skip_discovery"] = skip_discovery
        self.session = MagicMock()

    async def mock_stop(self):
        pass

    with patch("uam.__main__.get_config", return_value=default_config), \
         patch("uam.__main__.ModelRouter.start", mock_start), \
         patch("uam.__main__.ModelRouter.stop", mock_stop), \
         patch("uam.__main__.web.run_app") as mock_run:
        # Simulate run_app calling on_startup callbacks
        def fake_run_app(app, **kwargs):
            import asyncio
            loop = asyncio.new_event_loop()
            for cb in app.on_startup:
                loop.run_until_complete(cb(app))
            loop.close()

        mock_run.side_effect = fake_run_app
        main()

    assert captured["skip_discovery"] is True


def test_pid_file_created(default_config, monkeypatch, tmp_uam_dir):
    """on_startup creates PID file with current process ID."""
    monkeypatch.setattr(sys, "argv", ["uam"])
    pid_file = tmp_uam_dir / ".uam" / "uam.pid"
    monkeypatch.setattr(main_mod, "PID_FILE", pid_file)

    async def mock_start(self, skip_discovery=False):
        self.session = MagicMock()

    async def mock_stop(self):
        pass

    with patch("uam.__main__.get_config", return_value=default_config), \
         patch("uam.__main__.ModelRouter.start", mock_start), \
         patch("uam.__main__.ModelRouter.stop", mock_stop), \
         patch("uam.__main__.web.run_app") as mock_run:
        def fake_run_app(app, **kwargs):
            import asyncio
            loop = asyncio.new_event_loop()
            for cb in app.on_startup:
                loop.run_until_complete(cb(app))
            loop.close()

        mock_run.side_effect = fake_run_app
        main()

    assert pid_file.exists()
    assert pid_file.read_text() == str(os.getpid())


def test_pid_file_removed_on_shutdown(default_config, monkeypatch, tmp_uam_dir):
    """on_shutdown removes PID file."""
    monkeypatch.setattr(sys, "argv", ["uam"])
    pid_file = tmp_uam_dir / ".uam" / "uam.pid"
    monkeypatch.setattr(main_mod, "PID_FILE", pid_file)

    async def mock_start(self, skip_discovery=False):
        self.session = MagicMock()

    async def mock_stop(self):
        pass

    with patch("uam.__main__.get_config", return_value=default_config), \
         patch("uam.__main__.ModelRouter.start", mock_start), \
         patch("uam.__main__.ModelRouter.stop", mock_stop), \
         patch("uam.__main__.web.run_app") as mock_run:
        def fake_run_app(app, **kwargs):
            import asyncio
            loop = asyncio.new_event_loop()
            # Run startup
            for cb in app.on_startup:
                loop.run_until_complete(cb(app))
            assert pid_file.exists()
            # Run shutdown
            for cb in app.on_shutdown:
                loop.run_until_complete(cb(app))
            loop.close()

        mock_run.side_effect = fake_run_app
        main()

    assert not pid_file.exists()


def test_pid_file_parent_created(default_config, monkeypatch, tmp_uam_dir):
    """on_startup creates PID file parent directory if it doesn't exist."""
    monkeypatch.setattr(sys, "argv", ["uam"])
    pid_file = tmp_uam_dir / "nested" / "dir" / "uam.pid"
    monkeypatch.setattr(main_mod, "PID_FILE", pid_file)

    async def mock_start(self, skip_discovery=False):
        self.session = MagicMock()

    async def mock_stop(self):
        pass

    with patch("uam.__main__.get_config", return_value=default_config), \
         patch("uam.__main__.ModelRouter.start", mock_start), \
         patch("uam.__main__.ModelRouter.stop", mock_stop), \
         patch("uam.__main__.web.run_app") as mock_run:
        def fake_run_app(app, **kwargs):
            import asyncio
            loop = asyncio.new_event_loop()
            for cb in app.on_startup:
                loop.run_until_complete(cb(app))
            loop.close()

        mock_run.side_effect = fake_run_app
        main()

    assert pid_file.exists()


def test_listen_from_config(default_config, monkeypatch, tmp_uam_dir):
    """Custom listen address from config is passed to web.run_app."""
    monkeypatch.setattr(sys, "argv", ["uam"])
    default_config["listen"] = "0.0.0.0:9999"

    async def mock_start(self, skip_discovery=False):
        self.session = MagicMock()

    async def mock_stop(self):
        pass

    with patch("uam.__main__.get_config", return_value=default_config), \
         patch("uam.__main__.ModelRouter.start", mock_start), \
         patch("uam.__main__.ModelRouter.stop", mock_stop), \
         patch("uam.__main__.web.run_app") as mock_run:
        mock_run.side_effect = lambda app, **kw: None
        main()

    mock_run.assert_called_once()
    call_kwargs = mock_run.call_args
    assert call_kwargs.kwargs["host"] == "0.0.0.0"
    assert call_kwargs.kwargs["port"] == 9999
