"""Model state management — on/off toggles, default model, aliases."""

import json
import os
import re
import shlex
import tempfile
from pathlib import Path

STATE_PATH = Path.home() / ".uam" / "models.json"
ENV_PATH = Path.home() / ".uam" / "env.sh"


def load_state() -> dict:
    """Load model state from ~/.uam/models.json, return defaults if missing."""
    if STATE_PATH.exists():
        try:
            return json.loads(STATE_PATH.read_text())
        except (json.JSONDecodeError, OSError):
            pass
    return {"default": "", "aliases": {}, "models": {}}


def save_state(state: dict) -> None:
    """Write model state to ~/.uam/models.json atomically.

    SEC-005: Uses tempfile + os.replace() so a SIGTERM or disk-full event
    mid-write cannot leave a truncated/corrupt models.json (which load_state
    would silently treat as empty, wiping the user's configuration).
    """
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(
        dir=str(STATE_PATH.parent), prefix=".models.", suffix=".tmp"
    )
    try:
        with os.fdopen(fd, "w") as f:
            f.write(json.dumps(state, indent=2) + "\n")
        os.replace(tmp_path, STATE_PATH)
    except Exception:
        if os.path.exists(tmp_path):
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
        raise


def get_default(state: dict | None = None) -> str:
    """Return the default model ID."""
    if state is None:
        state = load_state()
    return state.get("default", "")


def is_enabled(model_id: str, state: dict | None = None) -> bool:
    """Check if a model is enabled. Unknown models are disabled."""
    if state is None:
        state = load_state()
    entry = state.get("models", {}).get(model_id)
    if entry is None:
        return False
    return entry.get("enabled", False)


def resolve_alias(name: str, state: dict | None = None) -> str | None:
    """Map a friendly name to a full model ID. Returns None if not found."""
    if state is None:
        state = load_state()
    name_lower = name.lower()
    aliases = state.get("aliases", {})
    # Direct alias match (case-insensitive)
    for alias, model_id in aliases.items():
        if alias.lower() == name_lower:
            return model_id
    return None


def auto_aliases(model_ids: list[str]) -> dict[str, str]:
    """Generate friendly aliases from model IDs.

    Examples:
        "openrouter:google/gemini-2.0-flash" → "gemini"
        "runpod:my-pod/meta-llama/Llama-3.1-70B" → "llama"
        "claude-sonnet-4-6" → "claude"
        "local:qwen2.5-coder" → "qwen"
    """
    aliases: dict[str, str] = {}
    seen_aliases: dict[str, list[str]] = {}  # alias → [model_ids]

    for model_id in model_ids:
        alias = _extract_alias(model_id)
        if alias:
            if alias not in seen_aliases:
                seen_aliases[alias] = []
            seen_aliases[alias].append(model_id)

    # Only assign aliases that are unambiguous (one model per alias)
    for alias, models in seen_aliases.items():
        if len(models) == 1:
            aliases[alias] = models[0]
        else:
            # For ambiguous aliases, try more specific names
            for mid in models:
                specific = _extract_specific_alias(mid)
                if specific and specific != alias:
                    aliases[specific] = mid

    return aliases


def _extract_alias(model_id: str) -> str:
    """Extract the core model family name from a model ID."""
    # Strip backend prefix (openrouter:, runpod:, local:)
    name = model_id
    if ":" in name:
        name = name.split(":", 1)[1]

    # Strip org/provider prefix (google/, meta-llama/, etc.)
    if "/" in name:
        name = name.rsplit("/", 1)[-1]

    # Extract the base family name
    name = name.lower()

    # Known family patterns
    # Order matters: longer/more-specific names first to avoid
    # "codellama" matching "llama" before "codellama"
    families = [
        "codellama", "codestral", "starcoder", "wizardlm",
        "gemini", "claude", "llama", "mistral", "mixtral", "qwen",
        "phi", "deepseek", "gpt", "o1", "o3", "o4",
        "command", "dbrx", "falcon", "yi", "vicuna", "solar",
        "gemma",
    ]
    for family in families:
        if family in name:
            return family

    # Fallback: first word/segment
    match = re.match(r"([a-z]+)", name)
    if match:
        return match.group(1)

    return ""


def _extract_specific_alias(model_id: str) -> str:
    """Extract a more specific alias when the base alias is ambiguous."""
    name = model_id
    if ":" in name:
        name = name.split(":", 1)[1]
    if "/" in name:
        name = name.rsplit("/", 1)[-1]

    name = name.lower()

    # For claude-style models, extract the variant name (sonnet, opus, haiku)
    variants = ["sonnet", "opus", "haiku", "flash", "pro", "ultra", "nano", "mini"]
    for variant in variants:
        if variant in name:
            return variant

    # Try to get family + version, e.g., "gemini-2.0" or "llama-3.1"
    match = re.match(r"([a-z]+)[_-]?(\d+\.?\d*)", name)
    if match:
        return f"{match.group(1)}{match.group(2)}"
    return ""


def infer_capabilities(model_id: str) -> list[str]:
    """Infer model capabilities from the model ID based on family patterns.

    Strips backend prefixes (local:, runpod:, openrouter:) and org prefixes
    (google/, meta-llama/, etc.) before matching, similar to _extract_alias.
    """
    name = model_id
    if ":" in name:
        name = name.split(":", 1)[1]
    if "/" in name:
        name = name.rsplit("/", 1)[-1]
    name = name.lower()

    # Full capability set: tools + streaming + thinking + vision
    if name.startswith("claude"):
        return ["tools", "streaming", "thinking", "vision"]
    if name.startswith("gpt-4") or name.startswith("gpt-5") or name.startswith("gpt4") or name.startswith("gpt5"):
        return ["tools", "streaming", "thinking", "vision"]
    if name.startswith("gemini"):
        return ["tools", "streaming", "thinking", "vision"]

    # Tools + streaming + thinking (no vision)
    if name.startswith("deepseek"):
        return ["tools", "streaming", "thinking"]

    # Tools + streaming
    if name.startswith("qwen"):
        return ["tools", "streaming"]
    if name.startswith("llama"):
        return ["tools", "streaming"]
    if name.startswith("mistral") or name.startswith("mixtral"):
        return ["tools", "streaming"]

    # Default: streaming only
    return ["streaming"]


def write_env_file(state: dict, env_path: Path | None = None) -> None:
    """Write the managed env file at ~/.uam/env.sh (or custom path).

    Contents:
      - Always exports ANTHROPIC_BASE_URL=http://127.0.0.1:5100
      - If default is a non-Claude enabled model, also exports:
          ANTHROPIC_DEFAULT_SONNET_MODEL
          ANTHROPIC_DEFAULT_SONNET_MODEL_NAME
          ANTHROPIC_DEFAULT_SONNET_MODEL_SUPPORTED_CAPABILITIES
    """
    if env_path is None:
        env_path = ENV_PATH

    lines = [
        "# Managed by uam — do not edit manually",
        "export ANTHROPIC_BASE_URL=http://127.0.0.1:5100",
    ]

    default = state.get("default", "")
    models = state.get("models", {})
    model_entry = models.get(default) if default else None

    should_override = (
        default
        and not default.startswith("claude-")
        and model_entry is not None
        and model_entry.get("enabled", False)
    )

    if should_override:
        # Find friendly name via alias lookup
        friendly_name = default
        for alias, mid in state.get("aliases", {}).items():
            if mid == default:
                friendly_name = alias
                break

        capabilities = model_entry.get("capabilities", [])
        caps_str = ",".join(str(c) for c in capabilities)

        # SEC-001: Use shlex.quote() on every value to prevent shell injection.
        # State values come from POST /state which is unauthenticated; without
        # quoting, a malicious value could inject arbitrary shell commands when
        # the user sources ~/.uam/env.sh.
        lines.append(
            f"export ANTHROPIC_DEFAULT_SONNET_MODEL={shlex.quote(str(default))}"
        )
        lines.append(
            f"export ANTHROPIC_DEFAULT_SONNET_MODEL_NAME={shlex.quote(str(friendly_name))}"
        )
        lines.append(
            f"export ANTHROPIC_DEFAULT_SONNET_MODEL_SUPPORTED_CAPABILITIES={shlex.quote(caps_str)}"
        )

    env_path.parent.mkdir(parents=True, exist_ok=True)
    content = "\n".join(lines) + "\n"
    env_path.write_text(content)
    env_path.chmod(0o644)


def sync_state_with_routes(route_keys: list[str], state: dict | None = None) -> dict:
    """Sync model state with discovered routes.

    - New models are added as enabled
    - Removed models are kept in state (may come back on refresh)
    - Aliases are regenerated (auto + user overrides preserved)
    """
    if state is None:
        state = load_state()

    models = state.get("models", {})
    user_aliases = {k: v for k, v in state.get("aliases", {}).items()
                    if v in models}  # preserve user-set aliases for known models

    # Add new models as enabled with inferred capabilities
    for key in route_keys:
        if key not in models:
            models[key] = {
                "enabled": True,
                "capabilities": infer_capabilities(key),
            }
        elif "capabilities" not in models[key]:
            # Existing model without capabilities — backfill
            models[key]["capabilities"] = infer_capabilities(key)

    state["models"] = models

    # Regenerate auto-aliases, then overlay user aliases
    auto = auto_aliases(route_keys)
    auto.update(user_aliases)
    state["aliases"] = auto

    # Set default if not set
    if not state.get("default") and route_keys:
        # Prefer a Claude model as initial default
        for key in route_keys:
            if key.startswith("claude-"):
                state["default"] = key
                break
        if not state.get("default"):
            state["default"] = route_keys[0]

    return state
