"""Tests for model capability inference and managed env file."""

from pathlib import Path

import pytest

from uam.state import (
    infer_capabilities,
    sync_state_with_routes,
    write_env_file,
)


# ---------------------------------------------------------------------------
# infer_capabilities
# ---------------------------------------------------------------------------


def test_infer_claude():
    caps = infer_capabilities("claude-sonnet-4-6")
    assert caps == ["tools", "streaming", "thinking", "vision"]


def test_infer_gpt():
    caps = infer_capabilities("openrouter:openai/gpt-4o")
    assert caps == ["tools", "streaming", "thinking", "vision"]


def test_infer_gpt5():
    caps = infer_capabilities("openrouter:openai/gpt-5-turbo")
    assert caps == ["tools", "streaming", "thinking", "vision"]


def test_infer_gemini():
    caps = infer_capabilities("openrouter:google/gemini-2.0-flash")
    assert "tools" in caps
    assert "streaming" in caps
    assert "thinking" in caps
    assert "vision" in caps


def test_infer_deepseek():
    caps = infer_capabilities("deepseek-chat")
    assert "tools" in caps
    assert "streaming" in caps
    assert "thinking" in caps
    assert "vision" not in caps


def test_infer_qwen():
    caps = infer_capabilities("local:qwen3-coder-next:latest")
    assert "tools" in caps
    assert "streaming" in caps
    assert "thinking" not in caps
    assert "vision" not in caps


def test_infer_llama():
    caps = infer_capabilities("runpod:my-pod/meta-llama/Llama-3.1-70B")
    assert "tools" in caps
    assert "streaming" in caps
    assert "thinking" not in caps
    assert "vision" not in caps


def test_infer_mistral():
    caps = infer_capabilities("local:mistral-7b")
    assert "tools" in caps
    assert "streaming" in caps
    assert "thinking" not in caps


def test_infer_mixtral():
    caps = infer_capabilities("openrouter:mistralai/mixtral-8x7b")
    assert "tools" in caps
    assert "streaming" in caps


def test_infer_unknown_default():
    caps = infer_capabilities("local:weird-model-xyz")
    assert caps == ["streaming"]


# ---------------------------------------------------------------------------
# sync_state_with_routes — capabilities
# ---------------------------------------------------------------------------


def test_sync_adds_capabilities_to_new_models():
    state = {"default": "", "aliases": {}, "models": {}}
    route_keys = [
        "claude-sonnet-4-6",
        "local:qwen3-coder-next:latest",
        "openrouter:google/gemini-2.0-flash",
    ]
    result = sync_state_with_routes(route_keys, state)
    for key in route_keys:
        assert "capabilities" in result["models"][key]
        assert isinstance(result["models"][key]["capabilities"], list)
        assert len(result["models"][key]["capabilities"]) > 0
    # Claude should have vision + thinking
    claude_caps = result["models"]["claude-sonnet-4-6"]["capabilities"]
    assert "vision" in claude_caps
    assert "thinking" in claude_caps
    # Qwen should not
    qwen_caps = result["models"]["local:qwen3-coder-next:latest"]["capabilities"]
    assert "vision" not in qwen_caps
    assert "thinking" not in qwen_caps


def test_sync_preserves_existing_capabilities():
    state = {
        "default": "",
        "aliases": {},
        "models": {
            "local:qwen3-coder-next:latest": {
                "enabled": True,
                "capabilities": ["streaming", "custom-cap"],
            }
        },
    }
    result = sync_state_with_routes(
        ["local:qwen3-coder-next:latest"], state
    )
    assert result["models"]["local:qwen3-coder-next:latest"]["capabilities"] == [
        "streaming",
        "custom-cap",
    ]


# ---------------------------------------------------------------------------
# write_env_file
# ---------------------------------------------------------------------------


def test_write_env_file_basic(tmp_path):
    env_path = tmp_path / "env.sh"
    state = {"default": "", "aliases": {}, "models": {}}
    write_env_file(state, env_path)
    assert env_path.exists()
    content = env_path.read_text()
    assert "export ANTHROPIC_BASE_URL=http://127.0.0.1:5100" in content
    assert "ANTHROPIC_DEFAULT_SONNET_MODEL" not in content


def test_write_env_file_claude_default(tmp_path):
    env_path = tmp_path / "env.sh"
    state = {
        "default": "claude-sonnet-4-6",
        "aliases": {},
        "models": {
            "claude-sonnet-4-6": {
                "enabled": True,
                "capabilities": ["tools", "streaming", "thinking", "vision"],
            }
        },
    }
    write_env_file(state, env_path)
    content = env_path.read_text()
    assert "export ANTHROPIC_BASE_URL=http://127.0.0.1:5100" in content
    # claude IS sonnet — no override
    assert "ANTHROPIC_DEFAULT_SONNET_MODEL" not in content


def test_write_env_file_non_claude_default(tmp_path):
    env_path = tmp_path / "env.sh"
    state = {
        "default": "local:qwen3-coder-next:latest",
        "aliases": {},
        "models": {
            "local:qwen3-coder-next:latest": {
                "enabled": True,
                "capabilities": ["tools", "streaming"],
            }
        },
    }
    write_env_file(state, env_path)
    content = env_path.read_text()
    assert "export ANTHROPIC_BASE_URL=http://127.0.0.1:5100" in content
    # SEC-001: values are now shlex.quote()d. For safe characters this is
    # the bare unquoted form (semantically identical when sourced).
    assert "ANTHROPIC_DEFAULT_SONNET_MODEL=local:qwen3-coder-next:latest" in content


def test_write_env_file_includes_capabilities(tmp_path):
    env_path = tmp_path / "env.sh"
    state = {
        "default": "local:qwen3-coder-next:latest",
        "aliases": {},
        "models": {
            "local:qwen3-coder-next:latest": {
                "enabled": True,
                "capabilities": ["tools", "streaming"],
            }
        },
    }
    write_env_file(state, env_path)
    content = env_path.read_text()
    assert "ANTHROPIC_DEFAULT_SONNET_MODEL_SUPPORTED_CAPABILITIES" in content
    assert "tools,streaming" in content


def test_write_env_file_uses_alias_as_name(tmp_path):
    env_path = tmp_path / "env.sh"
    state = {
        "default": "local:qwen3-coder-next:latest",
        "aliases": {"qwen": "local:qwen3-coder-next:latest"},
        "models": {
            "local:qwen3-coder-next:latest": {
                "enabled": True,
                "capabilities": ["tools", "streaming"],
            }
        },
    }
    write_env_file(state, env_path)
    content = env_path.read_text()
    assert "ANTHROPIC_DEFAULT_SONNET_MODEL_NAME=qwen" in content


def test_write_env_file_uses_model_id_when_no_alias(tmp_path):
    env_path = tmp_path / "env.sh"
    state = {
        "default": "local:qwen3-coder-next:latest",
        "aliases": {},
        "models": {
            "local:qwen3-coder-next:latest": {
                "enabled": True,
                "capabilities": ["tools", "streaming"],
            }
        },
    }
    write_env_file(state, env_path)
    content = env_path.read_text()
    assert "ANTHROPIC_DEFAULT_SONNET_MODEL_NAME=local:qwen3-coder-next:latest" in content


def test_write_env_file_disabled_default_skipped(tmp_path):
    env_path = tmp_path / "env.sh"
    state = {
        "default": "local:qwen3-coder-next:latest",
        "aliases": {},
        "models": {
            "local:qwen3-coder-next:latest": {
                "enabled": False,
                "capabilities": ["tools", "streaming"],
            }
        },
    }
    write_env_file(state, env_path)
    content = env_path.read_text()
    assert "ANTHROPIC_DEFAULT_SONNET_MODEL" not in content


def test_write_env_file_permissions(tmp_path):
    env_path = tmp_path / "env.sh"
    state = {"default": "", "aliases": {}, "models": {}}
    write_env_file(state, env_path)
    mode = env_path.stat().st_mode & 0o777
    assert mode == 0o644


def test_write_env_file_has_header_comment(tmp_path):
    env_path = tmp_path / "env.sh"
    state = {"default": "", "aliases": {}, "models": {}}
    write_env_file(state, env_path)
    content = env_path.read_text()
    assert "# Managed by uam" in content
