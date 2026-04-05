"""Tests for uam.state module."""

import json

import uam.state as state_mod
from uam.state import (
    _extract_alias,
    _extract_specific_alias,
    auto_aliases,
    get_default,
    is_enabled,
    load_state,
    resolve_alias,
    save_state,
)


# --- load/save ---


def test_load_state_returns_default_when_no_file():
    if state_mod.STATE_PATH.exists():
        state_mod.STATE_PATH.unlink()
    assert load_state() == {"default": "", "aliases": {}, "models": {}}


def test_load_state_returns_default_on_corrupt_json():
    state_mod.STATE_PATH.write_text("not json")
    assert load_state() == {"default": "", "aliases": {}, "models": {}}


def test_load_state_returns_default_on_os_error():
    if state_mod.STATE_PATH.exists():
        state_mod.STATE_PATH.unlink()
    state_mod.STATE_PATH.mkdir()
    assert load_state() == {"default": "", "aliases": {}, "models": {}}
    state_mod.STATE_PATH.rmdir()


def test_save_state_creates_parent_directory():
    parent = state_mod.STATE_PATH.parent
    if parent.exists():
        import shutil
        shutil.rmtree(parent)
    state = {"default": "test-model", "aliases": {}, "models": {}}
    save_state(state)
    assert state_mod.STATE_PATH.exists()


def test_save_state_roundtrip():
    state = {"default": "claude-sonnet-4-6", "aliases": {"s": "claude-sonnet-4-6"}, "models": {"claude-sonnet-4-6": {"enabled": True}}}
    save_state(state)
    loaded = load_state()
    assert loaded == state


def test_save_state_overwrites_existing():
    save_state({"default": "first", "aliases": {}, "models": {}})
    save_state({"default": "second", "aliases": {}, "models": {}})
    assert load_state()["default"] == "second"


# --- get_default ---


def test_get_default_with_state_arg():
    assert get_default({"default": "claude-sonnet-4-6"}) == "claude-sonnet-4-6"


def test_get_default_empty_state():
    assert get_default({"default": ""}) == ""


def test_get_default_missing_key():
    assert get_default({}) == ""


def test_get_default_loads_from_disk_when_none():
    save_state({"default": "from-disk", "aliases": {}, "models": {}})
    assert get_default(None) == "from-disk"


# --- is_enabled ---


def test_is_enabled_true():
    state = {"models": {"m1": {"enabled": True}}}
    assert is_enabled("m1", state) is True


def test_is_enabled_false():
    state = {"models": {"m1": {"enabled": False}}}
    assert is_enabled("m1", state) is False


def test_is_enabled_unknown_model():
    state = {"models": {}}
    assert is_enabled("unknown", state) is False


def test_is_enabled_missing_enabled_key():
    state = {"models": {"m1": {}}}
    assert is_enabled("m1", state) is False


def test_is_enabled_loads_from_disk_when_none():
    save_state({"default": "", "aliases": {}, "models": {"m1": {"enabled": True}}})
    assert is_enabled("m1", None) is True


# --- resolve_alias ---


def test_resolve_alias_exact_match():
    state = {"aliases": {"gemini": "openrouter:google/gemini-2.0-flash"}}
    assert resolve_alias("gemini", state) == "openrouter:google/gemini-2.0-flash"


def test_resolve_alias_case_insensitive():
    state = {"aliases": {"Gemini": "openrouter:google/gemini-2.0-flash"}}
    assert resolve_alias("gemini", state) == "openrouter:google/gemini-2.0-flash"


def test_resolve_alias_not_found():
    state = {"aliases": {"gemini": "openrouter:google/gemini-2.0-flash"}}
    assert resolve_alias("nonexistent", state) is None


def test_resolve_alias_loads_from_disk_when_none():
    save_state({"default": "", "aliases": {"llama": "local:llama-3"}, "models": {}})
    assert resolve_alias("llama", None) == "local:llama-3"


# --- auto_aliases ---


def test_auto_aliases_single_model():
    result = auto_aliases(["openrouter:google/gemini-2.0-flash"])
    assert "gemini" in result
    assert result["gemini"] == "openrouter:google/gemini-2.0-flash"


def test_auto_aliases_ambiguous_falls_to_specific():
    result = auto_aliases(["claude-sonnet-4-6", "claude-opus-4-6"])
    assert "sonnet" in result
    assert "opus" in result
    assert result["sonnet"] == "claude-sonnet-4-6"
    assert result["opus"] == "claude-opus-4-6"


def test_auto_aliases_empty_input():
    assert auto_aliases([]) == {}


def test_auto_aliases_local_model():
    result = auto_aliases(["local:qwen2.5-coder"])
    assert "qwen" in result
    assert result["qwen"] == "local:qwen2.5-coder"


def test_auto_aliases_codellama_before_llama():
    result = auto_aliases(["local:codellama-34b"])
    assert "codellama" in result
    assert "llama" not in result


# --- _extract_alias ---


def test_extract_alias_strips_backend_prefix():
    assert _extract_alias("openrouter:google/gemini-2.0-flash") == "gemini"


def test_extract_alias_claude_model():
    assert _extract_alias("claude-sonnet-4-6") == "claude"


def test_extract_alias_fallback_first_word():
    assert _extract_alias("local:some-unknown-model") == "some"


def test_extract_alias_numeric_only():
    assert _extract_alias("local:12345") == ""


# --- _extract_specific_alias ---


def test_extract_specific_alias_sonnet():
    assert _extract_specific_alias("claude-sonnet-4-6") == "sonnet"


def test_extract_specific_alias_no_match():
    assert _extract_specific_alias("local:???") == ""


def test_extract_specific_alias_family_version():
    """Extracts family+version when no variant keyword found."""
    assert _extract_specific_alias("local:llama3.1-chat") == "llama3.1"


def test_extract_specific_alias_with_prefix():
    """Strips backend and org prefix before matching."""
    assert _extract_specific_alias("openrouter:google/gemini-2.0-flash") == "flash"


# --- auto_aliases edge cases ---


def test_auto_aliases_ambiguous_no_specific():
    """Ambiguous aliases where _extract_specific_alias returns same as base alias."""
    # Two models both map to 'gemma', specific alias for them should be different
    result = auto_aliases(["local:gemma-2b", "local:gemma-7b"])
    # Base alias "gemma" is ambiguous, should try specific
    assert "gemma" not in result  # ambiguous
    assert "gemma2" in result or "gemma7" in result


def test_auto_aliases_ambiguous_specific_same_as_alias():
    """When specific alias equals the base alias, it's skipped."""
    # Models where specific gives the same result as base
    result = auto_aliases(["local:12345-a", "local:12345-b"])
    # Both map to "" as alias (numeric only), so neither gets an alias
    assert result == {}


# --- sync_state_with_routes edge cases ---


def test_sync_state_sets_default_to_claude():
    """sync_state prefers a Claude model as initial default."""
    from uam.state import sync_state_with_routes
    state = {"default": "", "aliases": {}, "models": {}}
    result = sync_state_with_routes(
        ["openrouter:google/gemini-2.0-flash", "claude-sonnet-4-6"],
        state,
    )
    assert result["default"] == "claude-sonnet-4-6"


def test_sync_state_sets_default_to_first_when_no_claude():
    """sync_state falls back to first route key when no Claude model."""
    from uam.state import sync_state_with_routes
    state = {"default": "", "aliases": {}, "models": {}}
    result = sync_state_with_routes(
        ["openrouter:google/gemini-2.0-flash", "local:qwen"],
        state,
    )
    assert result["default"] == "openrouter:google/gemini-2.0-flash"


def test_sync_state_keeps_existing_default():
    """sync_state keeps existing default if already set."""
    from uam.state import sync_state_with_routes
    state = {"default": "existing-model", "aliases": {}, "models": {}}
    result = sync_state_with_routes(["claude-sonnet-4-6"], state)
    assert result["default"] == "existing-model"


def test_sync_state_empty_routes():
    """sync_state with empty route keys does not set a default."""
    from uam.state import sync_state_with_routes
    state = {"default": "", "aliases": {}, "models": {}}
    result = sync_state_with_routes([], state)
    assert result["default"] == ""


def test_sync_state_preserves_user_aliases():
    """sync_state preserves user-set aliases for known models."""
    from uam.state import sync_state_with_routes
    state = {
        "default": "claude-sonnet-4-6",
        "aliases": {"my-alias": "claude-sonnet-4-6"},
        "models": {"claude-sonnet-4-6": {"enabled": True}},
    }
    result = sync_state_with_routes(["claude-sonnet-4-6"], state)
    # User alias preserved, auto alias also generated
    assert result["aliases"]["my-alias"] == "claude-sonnet-4-6"


def test_sync_state_loads_from_disk_when_none():
    """sync_state loads state from disk when state is None."""
    from uam.state import sync_state_with_routes
    save_state({"default": "", "aliases": {}, "models": {}})
    result = sync_state_with_routes(["claude-sonnet-4-6"], None)
    assert "claude-sonnet-4-6" in result["models"]
    assert result["models"]["claude-sonnet-4-6"]["enabled"] is True
