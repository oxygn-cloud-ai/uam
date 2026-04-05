"""Minimal proxy server entry point — no CLI, no argparse."""

import os
import sys
from pathlib import Path

from aiohttp import web

from uam.config import get_config, parse_listen, CONFIG_DIR
from uam.proxy import create_app
from uam.router import ModelRouter

PID_FILE = CONFIG_DIR / "uam.pid"


def main():
    config = get_config()
    host, port = parse_listen(config)
    skip_discovery = "--skip-discovery" in sys.argv

    router = ModelRouter(config)

    async def on_startup(app: web.Application):
        msg = "Skipping discovery..." if skip_discovery else "Starting model discovery..."
        print(msg)
        await router.start(skip_discovery=skip_discovery)
        print(f"\nReady — {router.model_count()} models available")
        print(f"Listening on http://{host}:{port}\n")
        for m in router.list_models():
            print(f"  {m['id']:60s} → {m['backend']:12s} ({m['original_model']})")
        print()

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
