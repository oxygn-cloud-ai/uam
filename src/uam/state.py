"""Model state management — on/off toggles, default model, aliases."""

import json
import re
from pathlib import Path

STATE_PATH = Path.home() / ".uam" / "models.json"


def load_state() -> dict:
    """Load model state from ~/.uam/models.json, return defaults if missing."""
    if STATE_PATH.exists():
        try:
            return json.loads(STATE_PATH.read_text())
        except (json.JSONDecodeError, OSError):
            pass
    return {"default": "", "aliases": {}, "models": {}}


def save_state(state: dict) -> None:
    """Write model state to ~/.uam/models.json."""
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    STATE_PATH.write_text(json.dumps(state, indent=2) + "\n")


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

    # Add new models as enabled
    for key in route_keys:
        if key not in models:
            models[key] = {"enabled": True}

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
