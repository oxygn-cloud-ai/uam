"""Tests for translation hardening — unknown blocks, images, thinking, <think> tags."""

import json
import logging

import pytest

from uam.translate import (
    anthropic_to_openai,
    _convert_message_to_openai,
    openai_to_anthropic,
    openai_stream_to_anthropic_stream,
)


# ---------------------------------------------------------------------------
# Unknown content block handling
# ---------------------------------------------------------------------------

class TestUnknownBlocks:

    def test_convert_message_unknown_block_type(self):
        """Unknown block type → text with '[unsupported: ...]' marker."""
        msg = {
            "role": "user",
            "content": [
                {"type": "custom_widget", "data": "x"},
            ],
        }
        result = _convert_message_to_openai(msg)
        assert "[unsupported: custom_widget]" in result["content"]

    def test_convert_message_unknown_block_preserves_other_content(self):
        """Text blocks preserved alongside unknown blocks."""
        msg = {
            "role": "user",
            "content": [
                {"type": "text", "text": "Hello"},
                {"type": "custom_widget", "data": "x"},
            ],
        }
        result = _convert_message_to_openai(msg)
        assert "Hello" in result["content"]
        assert "[unsupported: custom_widget]" in result["content"]

    def test_convert_message_unknown_block_logs_warning(self, caplog):
        """Unknown block type logs a WARNING."""
        msg = {
            "role": "user",
            "content": [{"type": "custom_widget", "data": "x"}],
        }
        with caplog.at_level(logging.WARNING, logger="uam.translate"):
            _convert_message_to_openai(msg)
        assert any("Unknown content block type" in r.message for r in caplog.records)

    def test_system_list_non_text_block(self):
        """System with non-text blocks → converted to text representation."""
        payload = {
            "model": "m",
            "messages": [{"role": "user", "content": "hi"}],
            "system": [
                {"type": "image", "source": {"data": "abc"}},
                {"type": "text", "text": "Be helpful"},
            ],
        }
        result = anthropic_to_openai(payload)
        system_msgs = [m for m in result["messages"] if m["role"] == "system"]
        assert len(system_msgs) == 1
        content = system_msgs[0]["content"]
        # Should contain the text block content
        assert "Be helpful" in content
        # Should also contain some representation of the image block
        assert "image" in content.lower() or "unsupported" in content.lower()


# ---------------------------------------------------------------------------
# Image block handling
# ---------------------------------------------------------------------------

class TestImageBlocks:

    def test_convert_message_image_block(self):
        """Image block → placeholder text."""
        msg = {
            "role": "user",
            "content": [
                {"type": "image", "source": {"type": "base64", "data": "abc123"}},
            ],
        }
        result = _convert_message_to_openai(msg)
        assert "Image content" in result["content"]
        assert "not supported" in result["content"]

    def test_convert_message_image_with_text(self):
        """Text block preserved alongside image placeholder."""
        msg = {
            "role": "user",
            "content": [
                {"type": "text", "text": "What is in this image?"},
                {"type": "image", "source": {"type": "base64", "data": "abc123"}},
            ],
        }
        result = _convert_message_to_openai(msg)
        assert "What is in this image?" in result["content"]
        assert "Image content" in result["content"]

    def test_convert_message_image_block_logs_warning(self, caplog):
        """Image block logs a WARNING."""
        msg = {
            "role": "user",
            "content": [
                {"type": "image", "source": {"type": "base64", "data": "abc"}},
            ],
        }
        with caplog.at_level(logging.WARNING, logger="uam.translate"):
            _convert_message_to_openai(msg)
        assert any("image" in r.message.lower() or "Image" in r.message for r in caplog.records)


# ---------------------------------------------------------------------------
# Thinking in requests (anthropic_to_openai)
# ---------------------------------------------------------------------------

class TestThinkingRequests:

    def test_anthropic_to_openai_strips_thinking_param(self):
        """thinking parameter stripped from translated output."""
        payload = {
            "model": "m",
            "messages": [{"role": "user", "content": "hi"}],
            "thinking": {"type": "enabled", "budget_tokens": 1024},
        }
        result = anthropic_to_openai(payload)
        assert "thinking" not in result

    def test_anthropic_to_openai_strips_thinking_blocks(self):
        """Thinking content blocks stripped from messages."""
        payload = {
            "model": "m",
            "messages": [{
                "role": "assistant",
                "content": [
                    {"type": "thinking", "thinking": "Let me reason about this..."},
                    {"type": "text", "text": "The answer is 42"},
                ],
            }],
        }
        result = anthropic_to_openai(payload)
        msg = result["messages"][0]
        # Should have text but not thinking content
        assert "42" in msg.get("content", "")
        # The thinking content should not appear
        assert "reason" not in msg.get("content", "")

    def test_anthropic_to_openai_preserves_non_thinking(self):
        """Non-thinking blocks preserved when thinking blocks stripped."""
        payload = {
            "model": "m",
            "messages": [
                {
                    "role": "assistant",
                    "content": [
                        {"type": "thinking", "thinking": "hmm"},
                        {"type": "text", "text": "Hello"},
                        {
                            "type": "tool_use",
                            "id": "toolu_abc",
                            "name": "search",
                            "input": {"q": "test"},
                        },
                    ],
                },
            ],
        }
        result = anthropic_to_openai(payload)
        msg = result["messages"][0]
        assert msg.get("content") == "Hello"
        assert len(msg.get("tool_calls", [])) == 1


# ---------------------------------------------------------------------------
# Thinking in responses (openai_to_anthropic, non-streaming)
# ---------------------------------------------------------------------------

class TestThinkingResponses:

    def test_openai_to_anthropic_reasoning_content(self):
        """reasoning_content in response → thinking block before text."""
        resp = {
            "id": "chatcmpl-1",
            "choices": [{
                "message": {
                    "role": "assistant",
                    "content": "The answer is 42",
                    "reasoning_content": "Let me think step by step...",
                },
                "finish_reason": "stop",
            }],
            "usage": {"prompt_tokens": 10, "completion_tokens": 20},
            "model": "deepseek-r1",
        }
        result = openai_to_anthropic(resp)
        # Should have thinking block first, then text
        assert len(result["content"]) >= 2
        assert result["content"][0]["type"] == "thinking"
        assert result["content"][0]["thinking"] == "Let me think step by step..."
        assert result["content"][1]["type"] == "text"
        assert result["content"][1]["text"] == "The answer is 42"

    def test_openai_to_anthropic_no_reasoning_content(self):
        """No reasoning_content → no thinking block (existing behavior)."""
        resp = {
            "id": "chatcmpl-1",
            "choices": [{
                "message": {"role": "assistant", "content": "Hello"},
                "finish_reason": "stop",
            }],
            "usage": {"prompt_tokens": 10, "completion_tokens": 5},
            "model": "gpt-4",
        }
        result = openai_to_anthropic(resp)
        assert len(result["content"]) == 1
        assert result["content"][0]["type"] == "text"

    def test_openai_to_anthropic_empty_reasoning_content(self):
        """Empty reasoning_content → no thinking block."""
        resp = {
            "id": "chatcmpl-1",
            "choices": [{
                "message": {
                    "role": "assistant",
                    "content": "Hello",
                    "reasoning_content": "",
                },
                "finish_reason": "stop",
            }],
            "usage": {"prompt_tokens": 10, "completion_tokens": 5},
            "model": "m",
        }
        result = openai_to_anthropic(resp)
        # No thinking block for empty reasoning
        thinking_blocks = [b for b in result["content"] if b["type"] == "thinking"]
        assert len(thinking_blocks) == 0


# ---------------------------------------------------------------------------
# <think> tag extraction
# ---------------------------------------------------------------------------

class TestThinkTagExtraction:

    def test_extract_think_tags_enabled(self):
        """<think> tags at start extracted into thinking block when enabled."""
        resp = {
            "id": "chatcmpl-1",
            "choices": [{
                "message": {
                    "role": "assistant",
                    "content": "<think>Let me reason</think>The answer is 42",
                },
                "finish_reason": "stop",
            }],
            "usage": {"prompt_tokens": 10, "completion_tokens": 20},
            "model": "m",
        }
        result = openai_to_anthropic(resp, extract_think_tags=True)
        assert result["content"][0]["type"] == "thinking"
        assert result["content"][0]["thinking"] == "Let me reason"
        assert result["content"][1]["type"] == "text"
        assert result["content"][1]["text"] == "The answer is 42"

    def test_extract_think_tags_disabled_by_default(self):
        """<think> tags NOT extracted when extract_think_tags is False (default)."""
        resp = {
            "id": "chatcmpl-1",
            "choices": [{
                "message": {
                    "role": "assistant",
                    "content": "<think>reasoning</think>answer",
                },
                "finish_reason": "stop",
            }],
            "usage": {"prompt_tokens": 10, "completion_tokens": 5},
            "model": "m",
        }
        result = openai_to_anthropic(resp)
        # Should NOT extract — default is False
        assert result["content"][0]["type"] == "text"
        assert "<think>" in result["content"][0]["text"]

    def test_extract_think_tags_not_at_start(self):
        """<think> tags in middle of text → no extraction."""
        resp = {
            "id": "chatcmpl-1",
            "choices": [{
                "message": {
                    "role": "assistant",
                    "content": "Hello <think>reasoning</think> world",
                },
                "finish_reason": "stop",
            }],
            "usage": {"prompt_tokens": 10, "completion_tokens": 5},
            "model": "m",
        }
        result = openai_to_anthropic(resp, extract_think_tags=True)
        # No extraction — tags not at start
        thinking_blocks = [b for b in result["content"] if b["type"] == "thinking"]
        assert len(thinking_blocks) == 0
        assert "<think>" in result["content"][0]["text"]

    def test_extract_think_tags_incomplete(self):
        """Unclosed <think> tag → no extraction."""
        resp = {
            "id": "chatcmpl-1",
            "choices": [{
                "message": {
                    "role": "assistant",
                    "content": "<think>no closing tag and some text",
                },
                "finish_reason": "stop",
            }],
            "usage": {"prompt_tokens": 10, "completion_tokens": 5},
            "model": "m",
        }
        result = openai_to_anthropic(resp, extract_think_tags=True)
        thinking_blocks = [b for b in result["content"] if b["type"] == "thinking"]
        assert len(thinking_blocks) == 0

    def test_extract_think_tags_with_whitespace_after(self):
        """Whitespace between </think> and answer text is stripped."""
        resp = {
            "id": "chatcmpl-1",
            "choices": [{
                "message": {
                    "role": "assistant",
                    "content": "<think>reasoning</think>\n\nThe answer",
                },
                "finish_reason": "stop",
            }],
            "usage": {"prompt_tokens": 10, "completion_tokens": 5},
            "model": "m",
        }
        result = openai_to_anthropic(resp, extract_think_tags=True)
        assert result["content"][0]["type"] == "thinking"
        assert result["content"][1]["type"] == "text"
        assert result["content"][1]["text"] == "The answer"


# ---------------------------------------------------------------------------
# Streaming thinking (best-effort)
# ---------------------------------------------------------------------------

class TestStreamingThinking:

    def test_stream_reasoning_content_delta(self):
        """H3: streaming reasoning_content is intentionally skipped to avoid
        a thinking_delta on the text content block (protocol violation).
        Non-streaming path is the authoritative source."""
        line = json.dumps({
            "choices": [{
                "delta": {"reasoning_content": "Let me think..."},
                "finish_reason": None,
            }],
        }).encode()
        line = b"data: " + line
        result = openai_stream_to_anthropic_stream(line, "m")
        # Per H3 fix: no output for reasoning_content alone in streaming.
        assert result is None or b"thinking_delta" not in result

    def test_stream_reasoning_content_empty(self):
        """Empty reasoning_content in delta → skip."""
        line = json.dumps({
            "choices": [{
                "delta": {"reasoning_content": ""},
                "finish_reason": None,
            }],
        }).encode()
        line = b"data: " + line
        result = openai_stream_to_anthropic_stream(line, "m")
        # Empty reasoning → None (nothing to emit)
        assert result is None
