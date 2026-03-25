"""CLI for uam — start, stop, list, refresh, install, uninstall."""

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
from importlib.resources import files
from pathlib import Path

from aiohttp import web

from uam import __version__
from uam.config import CONFIG_DIR, CONFIG_PATH, parse_listen, get_config
from uam.proxy import create_app
from uam.router import ModelRouter

SHELL_WRAPPER_START = "# >>> uam (use-any-model) >>>"
SHELL_WRAPPER_END = "# <<< uam (use-any-model) <<<"
ZSHRC = Path.home() / ".zshrc"
BASHRC = Path.home() / ".bashrc"
PID_FILE = CONFIG_DIR / "uam.pid"


def _get_shell_rc() -> Path:
    """Detect user's shell and return the appropriate rc file."""
    shell = os.environ.get("SHELL", "/bin/zsh")
    if "zsh" in shell:
        return ZSHRC
    return BASHRC


def _shell_wrapper(host: str, port: int) -> str:
    return f"""{SHELL_WRAPPER_START}
claude() {{
  if ! curl -s --max-time 1 http://{host}:{port}/health > /dev/null 2>&1; then
    echo "Starting uam model router..."
    nohup uam start > /tmp/uam.log 2>&1 &
    sleep 2
  fi
  ANTHROPIC_BASE_URL=http://{host}:{port} command claude "$@"
}}
{SHELL_WRAPPER_END}"""


def cmd_start(args):
    """Start the uam proxy server."""
    config = get_config()
    host, port = parse_listen(config)

    router = ModelRouter(config)

    async def on_startup(app: web.Application):
        skip = getattr(args, "skip_discovery", False)
        print("Starting model discovery..." if not skip else "Skipping discovery...")
        await router.start(skip_discovery=skip)
        print(f"\nReady — {router.model_count()} models available")
        print(f"Listening on http://{host}:{port}\n")
        for m in router.list_models():
            print(f"  {m['id']:60s} → {m['backend']:12s} ({m['original_model']})")
        print()

        # Write PID file
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


def cmd_stop(args):
    """Stop the running uam proxy."""
    if PID_FILE.exists():
        pid = int(PID_FILE.read_text().strip())
        try:
            os.kill(pid, 15)  # SIGTERM
            print(f"Stopped uam (pid {pid})")
            PID_FILE.unlink()
        except ProcessLookupError:
            print("uam was not running (stale pid file)")
            PID_FILE.unlink()
    else:
        print("uam is not running")


def cmd_list(args):
    """List discovered models from a running proxy."""
    import urllib.request

    config = get_config()
    host, port = parse_listen(config)
    try:
        with urllib.request.urlopen(f"http://{host}:{port}/v1/models", timeout=5) as resp:
            data = json.loads(resp.read())
        for m in data["data"]:
            print(f"  {m['id']:60s}  [{m['owned_by']}]")
        print(f"\n  Total: {len(data['data'])} models")
    except Exception:
        print("uam is not running. Start it with: uam start")


def cmd_refresh(args):
    """Trigger model re-discovery on a running proxy."""
    import urllib.request

    config = get_config()
    host, port = parse_listen(config)
    try:
        req = urllib.request.Request(f"http://{host}:{port}/refresh", method="POST")
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read())
        print(f"Refreshed: {data['models']} models available")
    except Exception:
        print("uam is not running. Start it with: uam start")


def cmd_install(args):
    """Set up uam config and shell wrapper."""
    config = get_config()
    host, port = parse_listen(config)

    # 1. Create config directory
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)

    # 2. Write default config if not exists
    if not CONFIG_PATH.exists():
        from uam.config import default_config
        CONFIG_PATH.write_text(json.dumps(default_config(), indent=2) + "\n")
        print(f"Created {CONFIG_PATH}")
    else:
        print(f"Config already exists: {CONFIG_PATH}")

    # 3. Add shell wrapper
    rc_file = _get_shell_rc()
    if rc_file.exists():
        rc_content = rc_file.read_text()
    else:
        rc_content = ""

    if SHELL_WRAPPER_START in rc_content:
        print(f"Shell wrapper already in {rc_file}")
    else:
        # Back up current state
        backup_path = CONFIG_DIR / f"{rc_file.name}.backup"
        backup_path.write_text(rc_content)
        print(f"Backed up {rc_file} → {backup_path}")

        # Append wrapper
        wrapper = _shell_wrapper(host, port)
        with open(rc_file, "a") as f:
            f.write(f"\n{wrapper}\n")
        print(f"Added shell wrapper to {rc_file}")

    print(f"""
uam installed successfully.

Next steps:
  1. Set your API keys as environment variables in {rc_file}:

     export ANTHROPIC_API_KEY_REAL="sk-ant-..."
     export OPENROUTER_API_KEY="sk-or-..."
     export RUNPOD_API_KEY_CHOC="rpa_..."
     export RUNPOD_API_KEY_OXYGN="rpa_..."

  2. Edit {CONFIG_PATH} to configure which backends to discover.

  3. Open a new terminal and run `claude` — uam starts automatically.
""")


def cmd_uninstall(args):
    """Remove uam config, shell wrapper, and pip package."""
    # 1. Stop proxy if running
    cmd_stop(args)

    # 2. Remove shell wrapper from rc file
    rc_file = _get_shell_rc()
    if rc_file.exists():
        content = rc_file.read_text()
        pattern = re.compile(
            rf"\n?{re.escape(SHELL_WRAPPER_START)}.*?{re.escape(SHELL_WRAPPER_END)}\n?",
            re.DOTALL,
        )
        new_content = pattern.sub("", content)
        if new_content != content:
            rc_file.write_text(new_content)
            print(f"Removed shell wrapper from {rc_file}")
        else:
            print(f"No shell wrapper found in {rc_file}")

    # 3. Remove config directory
    if CONFIG_DIR.exists():
        if not args.yes:
            answer = input(f"Remove {CONFIG_DIR}? [y/N] ").strip().lower()
            if answer != "y":
                print("Skipping config removal")
                return
        shutil.rmtree(CONFIG_DIR)
        print(f"Removed {CONFIG_DIR}")

    # 4. Uninstall pip package
    print("Uninstalling uam package...")
    subprocess.run([sys.executable, "-m", "pip", "uninstall", "uam", "-y"], check=False)

    print("\nuam removed. Claude Code will use standard Anthropic models.")


def main():
    parser = argparse.ArgumentParser(
        prog="uam",
        description="Use Any Model with Claude Code",
    )
    parser.add_argument("--version", action="version", version=f"uam {__version__}")

    sub = parser.add_subparsers(dest="command")

    # start
    p_start = sub.add_parser("start", help="Start the model router proxy")
    p_start.add_argument(
        "--skip-discovery", action="store_true",
        help="Skip model discovery, use Anthropic models only",
    )
    p_start.set_defaults(func=cmd_start)

    # stop
    p_stop = sub.add_parser("stop", help="Stop the running proxy")
    p_stop.set_defaults(func=cmd_stop)

    # list
    p_list = sub.add_parser("list", help="List all discovered models")
    p_list.set_defaults(func=cmd_list)

    # refresh
    p_refresh = sub.add_parser("refresh", help="Re-run model discovery")
    p_refresh.set_defaults(func=cmd_refresh)

    # install
    p_install = sub.add_parser("install", help="Set up config and shell wrapper")
    p_install.set_defaults(func=cmd_install)

    # uninstall
    p_uninstall = sub.add_parser("uninstall", help="Remove uam completely")
    p_uninstall.add_argument("-y", "--yes", action="store_true", help="Skip confirmation prompts")
    p_uninstall.set_defaults(func=cmd_uninstall)

    args = parser.parse_args()
    if hasattr(args, "func"):
        args.func(args)
    else:
        parser.print_help()
