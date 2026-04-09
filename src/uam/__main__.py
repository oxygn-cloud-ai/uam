"""Minimal proxy server entry point — no CLI, no argparse."""

import logging
import os
import sys
from pathlib import Path

from aiohttp import web

from uam.config import ensure_config_exists, get_config, parse_listen, CONFIG_DIR
from uam.log import setup_logging
from uam.proxy import create_app
from uam.router import ModelRouter

PID_FILE = CONFIG_DIR / "uam.pid"

logger = logging.getLogger("uam")


def main():
    setup_logging()
    # First-run bootstrap: materialize ~/.uam/config.json with defaults so the
    # user always has a real file to edit. Idempotent — leaves existing config
    # untouched.
    ensure_config_exists()
    config = get_config()
    host, port = parse_listen(config)
    skip_discovery = "--skip-discovery" in sys.argv

    router = ModelRouter(config)

    async def on_startup(app: web.Application):
        msg = "Skipping discovery..." if skip_discovery else "Starting model discovery..."
        logger.info(msg)
        await router.start(skip_discovery=skip_discovery)
        logger.info("Ready — %d models available", router.model_count())
        logger.info("Listening on http://%s:%d", host, port)
        for m in router.list_models():
            logger.info("  %-60s → %-12s (%s)", m['id'], m['backend'], m['original_model'])

        PID_FILE.parent.mkdir(parents=True, exist_ok=True)
        PID_FILE.write_text(str(os.getpid()))

    async def on_shutdown(app: web.Application):
        await router.stop()
        if PID_FILE.exists():
            PID_FILE.unlink()

    app = create_app(router)
    app.on_startup.append(on_startup)
    app.on_shutdown.append(on_shutdown)

    web.run_app(app, host=host, port=port, print=None)


if __name__ == "__main__":
    main()
