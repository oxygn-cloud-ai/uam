#!/usr/bin/env python3
"""SessionStart hook — auto-start uam proxy if not running."""

import os
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

UAM_HOST = "127.0.0.1"
UAM_PORT = 5100
UAM_DIR = Path(os.path.expanduser("~/.uam"))
UAM_LOG = UAM_DIR / "uam.log"


def main():
    # Check if proxy is already running
    if _is_running():
        return

    # Ensure log directory exists
    UAM_DIR.mkdir(parents=True, exist_ok=True)

    # Start the proxy in the background
    with open(UAM_LOG, "w") as log:
        subprocess.Popen(
            [sys.executable, "-m", "uam"],
            stdout=log,
            stderr=log,
            start_new_session=True,
        )

    # Wait for it to come up (max 5 seconds)
    for _ in range(5):
        time.sleep(1)
        if _is_running():
            return


def _is_running() -> bool:
    try:
        urllib.request.urlopen(
            f"http://{UAM_HOST}:{UAM_PORT}/health", timeout=2
        )
        return True
    except Exception:
        return False


if __name__ == "__main__":
    main()
