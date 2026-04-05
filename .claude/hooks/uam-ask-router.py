#!/usr/bin/env python3
"""UserPromptSubmit hook — intercept 'ask <model> <query>' patterns.

Routes one-shot queries to specific models via the uam proxy.
Reads hook input from stdin as JSON (Claude Code hook protocol).
Outputs result to stdout for Claude to relay.
"""

import json
import re
import sys
import urllib.request
import urllib.error
from pathlib import Path

UAM_HOST = "127.0.0.1"
UAM_PORT = 5100
UAM_STATE = Path.home() / ".uam" / "models.json"
ASK_PATTERN = re.compile(r"^ask\s+([a-zA-Z0-9._-]+)\s+(.+)", re.IGNORECASE | re.DOTALL)


def main():
    # Read hook input from stdin
    try:
        hook_input = json.load(sys.stdin)
    except (json.JSONDecodeError, EOFError):
        return

    message = hook_input.get("prompt", "")
    if not message:
        return

    # Check for "ask <model> <query>" pattern
    match = ASK_PATTERN.match(message.strip())
    if not match:
        return

    model_name = match.group(1)
    query = match.group(2).strip()

    # Load state
    if not UAM_STATE.exists():
        print(f"{model_name} is not configured (no models.json). Use /model to set up models.")
        return

    try:
        state = json.loads(UAM_STATE.read_text())
    except (json.JSONDecodeError, OSError):
        print(f"Failed to read model state. Use /model to set up models.")
        return

    # Resolve alias
    model_id = _resolve_alias(model_name, state)
    default = state.get("default", "unknown")

    if not model_id:
        print(f"{model_name} is not configured, using '{default}'")
        return

    # Check if enabled
    model_entry = state.get("models", {}).get(model_id, {})
    if not model_entry.get("enabled", False):
        print(f"{model_name} is off, use /model to turn it on. Meanwhile using '{default}'")
        return

    # Check proxy is running
    try:
        urllib.request.urlopen(
            f"http://{UAM_HOST}:{UAM_PORT}/health", timeout=2
        )
    except Exception:
        print("uam proxy is not running. Use /uam start to start it.")
        return

    # Send query to proxy
    payload = json.dumps({
        "model": model_id,
        "max_tokens": 4096,
        "messages": [{"role": "user", "content": query}],
    }).encode()

    req = urllib.request.Request(
        f"http://{UAM_HOST}:{UAM_PORT}/v1/messages/ask",
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )

    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            data = json.loads(resp.read())
    except urllib.error.HTTPError as e:
        try:
            err_data = json.loads(e.read())
            err = err_data.get("error", {})
            etype = err.get("type", "")
            msg = err.get("message", "")
            err_default = err.get("default", default)
            if etype == "model_disabled":
                print(f"{msg}, use /model to turn it on. Meanwhile using '{err_default}'")
            elif etype == "model_not_found":
                print(f"{msg}, using '{err_default}'")
            else:
                print(f"Error from {model_name}: {msg}")
        except Exception:
            print(f"Failed to reach {model_name} via uam proxy.")
        return
    except Exception:
        print(f"Failed to reach {model_name} via uam proxy.")
        return

    # Extract text response
    content = data.get("content", [])
    parts = [block.get("text", "") for block in content if block.get("type") == "text"]
    text = "\n".join(parts)

    if text:
        print(f"[{model_name}]: {text}")
    else:
        print(f"No response from {model_name}.")


def _resolve_alias(name: str, state: dict) -> str | None:
    """Resolve a friendly name to a full model ID."""
    name_lower = name.lower()

    # Check aliases
    for alias, model_id in state.get("aliases", {}).items():
        if alias.lower() == name_lower:
            return model_id

    # Check direct model IDs
    for model_id in state.get("models", {}):
        if model_id.lower() == name_lower or model_id == name:
            return model_id

    return None


if __name__ == "__main__":
    main()
