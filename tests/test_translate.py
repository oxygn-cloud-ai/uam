"""Tests for uam.translate — Anthropic <-> OpenAI format translation."""

import json

import pytest
from hypothesis import given, settings

from uam.translate import (
    anthropic_to_openai,
    _convert_message_to_openai,
    _convert_tool_to_openai,
    openai_to_anthropic,
    openai_stream_to_anthropic_stream,
    make_anthropic_stream_start,
    _sse_event,
)

from strategies import (
    st_anthropic_payload,
    st_openai_response,
    st_openai_stream_line,
    st_anthropic_tool,
)


# ---------------------------------------------------------------------------
# anthropic_to_openai — deterministic tests
# ---------------------------------------------------------------------------

class TestAnthropicToOpenai:

    def test_anthropic_to_openai_simple_text(self):
        payload = {"model": "m", "messages": [{"role": "user", "content": "hello"}]}
        result = anthropic_to_openai(payload)
        assert result["messages"][0] == {"role": "user", "content": "hello"}

    def test_anthropic_to_openai_system_string(self):
        payload = {
            "model": "m",
            "messages": [{"role": "user", "content": "hi"}],
            "system": "You are helpful",
        }
        result = anthropic_to_openai(payload)
        assert result["messages"][0] == {"role": "system", "content": "You are helpful"}

    def test_anthropic_to_openai_system_list_of_blocks(self):
        payload = {
            "model": "m",
            "messages": [{"role": "user", "content": "hi"}],
            "system": [
                {"type": "text", "text": "A"},
                {"type": "text", "text": "B"},
            ],
        }
        result = anthropic_to_openai(payload)
        assert result["messages"][0] == {"role": "system", "content": "A\nB"}

    def test_anthropic_to_openai_system_list_no_text(self):
        payload = {
            "model": "m",
            "messages": [{"role": "user", "content": "hi"}],
            "system": [{"type": "image", "data": "x"}],
        }
        result = anthropic_to_openai(payload)
        # No system message should be added
        assert all(m["role"] != "system" for m in result["messages"])

    def test_anthropic_to_openai_no_system(self):
        payload = {"model": "m", "messages": [{"role": "user", "content": "hi"}]}
        result = anthropic_to_openai(payload)
        assert all(m["role"] != "system" for m in result["messages"])

    def test_anthropic_to_openai_max_tokens(self):
        payload = {
            "model": "m",
            "messages": [{"role": "user", "content": "hi"}],
            "max_tokens": 512,
        }
        result = anthropic_to_openai(payload)
        assert result["max_tokens"] == 512

    def test_anthropic_to_openai_temperature(self):
        payload = {
            "model": "m",
            "messages": [{"role": "user", "content": "hi"}],
            "temperature": 0.7,
        }
        result = anthropic_to_openai(payload)
        assert result["temperature"] == 0.7

    def test_anthropic_to_openai_top_p(self):
        payload = {
            "model": "m",
            "messages": [{"role": "user", "content": "hi"}],
            "top_p": 0.9,
        }
        result = anthropic_to_openai(payload)
        assert result["top_p"] == 0.9

    def test_anthropic_to_openai_stop_sequences(self):
        payload = {
            "model": "m",
            "messages": [{"role": "user", "content": "hi"}],
            "stop_sequences": ["END", "STOP"],
        }
        result = anthropic_to_openai(payload)
        assert result["stop"] == ["END", "STOP"]

    def test_anthropic_to_openai_stream(self):
        payload = {
            "model": "m",
            "messages": [{"role": "user", "content": "hi"}],
            "stream": True,
        }
        result = anthropic_to_openai(payload)
        assert result["stream"] is True

    def test_anthropic_to_openai_tools(self):
        payload = {
            "model": "m",
            "messages": [{"role": "user", "content": "hi"}],
            "tools": [{
                "name": "get_weather",
                "description": "Get weather",
                "input_schema": {"type": "object", "properties": {"city": {"type": "string"}}},
            }],
        }
        result = anthropic_to_openai(payload)
        tool = result["tools"][0]
        assert tool["type"] == "function"
        assert tool["function"]["name"] == "get_weather"
        assert tool["function"]["parameters"] == {
            "type": "object",
            "properties": {"city": {"type": "string"}},
        }

    def test_anthropic_to_openai_minimal(self):
        payload = {"model": "m", "messages": [{"role": "user", "content": "hi"}]}
        result = anthropic_to_openai(payload)
        assert "model" in result
        assert "messages" in result
        assert "stream" in result
        # No optional fields
        assert "max_tokens" not in result
        assert "temperature" not in result
        assert "top_p" not in result
        assert "stop" not in result
        assert "tools" not in result


# ---------------------------------------------------------------------------
# _convert_message_to_openai tests
# ---------------------------------------------------------------------------

class TestConvertMessage:

    def test_convert_message_text_string(self):
        result = _convert_message_to_openai({"role": "user", "content": "hello"})
        assert result == {"role": "user", "content": "hello"}

    def test_convert_message_text_blocks(self):
        msg = {
            "role": "assistant",
            "content": [
                {"type": "text", "text": "Hello"},
                {"type": "text", "text": "World"},
            ],
        }
        result = _convert_message_to_openai(msg)
        assert result["content"] == "Hello\nWorld"

    def test_convert_message_tool_use(self):
        msg = {
            "role": "assistant",
            "content": [
                {
                    "type": "tool_use",
                    "id": "toolu_abc123",
                    "name": "get_weather",
                    "input": {"city": "London"},
                },
            ],
        }
        result = _convert_message_to_openai(msg)
        assert result["content"] is None
        assert len(result["tool_calls"]) == 1
        tc = result["tool_calls"][0]
        assert tc["id"] == "toolu_abc123"
        assert tc["type"] == "function"
        assert tc["function"]["name"] == "get_weather"
        assert json.loads(tc["function"]["arguments"]) == {"city": "London"}

    def test_convert_message_tool_use_with_text(self):
        msg = {
            "role": "assistant",
            "content": [
                {"type": "text", "text": "Let me check"},
                {
                    "type": "tool_use",
                    "id": "toolu_abc",
                    "name": "search",
                    "input": {"q": "test"},
                },
            ],
        }
        result = _convert_message_to_openai(msg)
        assert result["content"] == "Let me check"
        assert len(result["tool_calls"]) == 1

    def test_convert_message_tool_use_no_text(self):
        msg = {
            "role": "assistant",
            "content": [
                {
                    "type": "tool_use",
                    "id": "toolu_abc",
                    "name": "run",
                    "input": {},
                },
            ],
        }
        result = _convert_message_to_openai(msg)
        assert result["content"] is None
        assert "tool_calls" in result

    def test_convert_message_tool_result_single(self):
        msg = {
            "role": "user",
            "content": [
                {
                    "type": "tool_result",
                    "tool_use_id": "toolu_abc",
                    "content": "result data",
                },
            ],
        }
        result = _convert_message_to_openai(msg)
        assert result["role"] == "tool"
        assert result["tool_call_id"] == "toolu_abc"
        assert result["content"] == "result data"

    def test_convert_message_tool_result_list_content(self):
        msg = {
            "role": "user",
            "content": [
                {
                    "type": "tool_result",
                    "tool_use_id": "toolu_abc",
                    "content": [
                        {"type": "text", "text": "line 1"},
                        {"type": "text", "text": "line 2"},
                    ],
                },
            ],
        }
        result = _convert_message_to_openai(msg)
        assert result["content"] == "line 1\nline 2"

    def test_convert_message_multiple_tool_results_expanded(self):
        """Multiple tool_results in one message expand via anthropic_to_openai."""
        payload = {
            "model": "m",
            "messages": [{
                "role": "user",
                "content": [
                    {"type": "tool_result", "tool_use_id": "t1", "content": "r1"},
                    {"type": "tool_result", "tool_use_id": "t2", "content": "r2"},
                ],
            }],
        }
        result = anthropic_to_openai(payload)
        tool_msgs = [m for m in result["messages"] if m["role"] == "tool"]
        assert len(tool_msgs) == 2
        assert tool_msgs[0]["tool_call_id"] == "t1"
        assert tool_msgs[1]["tool_call_id"] == "t2"

    def test_convert_message_multiple_tool_results_with_text_and_list_content(self):
        """Multiple tool_results plus non-tool content with list content in tool_result."""
        payload = {
            "model": "m",
            "messages": [{
                "role": "user",
                "content": [
                    {"type": "text", "text": "Here are the results"},
                    {
                        "type": "tool_result",
                        "tool_use_id": "t1",
                        "content": [
                            {"type": "text", "text": "result line 1"},
                            {"type": "text", "text": "result line 2"},
                        ],
                    },
                    {
                        "type": "tool_result",
                        "tool_use_id": "t2",
                        "content": "simple result",
                    },
                ],
            }],
        }
        result = anthropic_to_openai(payload)
        # Should have: one user message for text, then two tool messages
        user_msgs = [m for m in result["messages"] if m["role"] == "user"]
        tool_msgs = [m for m in result["messages"] if m["role"] == "tool"]
        assert len(user_msgs) == 1
        assert user_msgs[0]["content"] == "Here are the results"
        assert len(tool_msgs) == 2
        assert tool_msgs[0]["content"] == "result line 1\nresult line 2"
        assert tool_msgs[1]["content"] == "simple result"

    def test_convert_message_multiple_tool_results_no_other_content(self):
        """Multiple tool_results with no text blocks — only tool messages produced."""
        payload = {
            "model": "m",
            "messages": [{
                "role": "user",
                "content": [
                    {"type": "tool_result", "tool_use_id": "t1", "content": "r1"},
                    {"type": "tool_result", "tool_use_id": "t2", "content": "r2"},
                ],
            }],
        }
        result = anthropic_to_openai(payload)
        # Only tool messages, no user message
        assert all(m["role"] == "tool" for m in result["messages"])

    def test_convert_message_empty_content_list(self):
        result = _convert_message_to_openai({"role": "user", "content": []})
        assert result["content"] == ""

    def test_convert_message_none_content(self):
        result = _convert_message_to_openai({"role": "user", "content": None})
        assert result["content"] == ""

    def test_convert_message_non_string_content(self):
        result = _convert_message_to_openai({"role": "user", "content": 42})
        assert result["content"] == "42"


# ---------------------------------------------------------------------------
# _convert_tool_to_openai tests
# ---------------------------------------------------------------------------

class TestConvertTool:

    def test_convert_tool_full(self):
        tool = {
            "name": "get_weather",
            "description": "Get the weather",
            "input_schema": {"type": "object", "properties": {"city": {"type": "string"}}},
        }
        result = _convert_tool_to_openai(tool)
        assert result["type"] == "function"
        assert result["function"]["name"] == "get_weather"
        assert result["function"]["description"] == "Get the weather"
        assert result["function"]["parameters"] == tool["input_schema"]

    def test_convert_tool_missing_fields(self):
        result = _convert_tool_to_openai({})
        assert result["function"]["name"] == ""
        assert result["function"]["description"] == ""
        assert result["function"]["parameters"] == {}


# ---------------------------------------------------------------------------
# openai_to_anthropic tests
# ---------------------------------------------------------------------------

class TestOpenaiToAnthropic:

    def test_openai_to_anthropic_simple_text(self):
        resp = {
            "id": "chatcmpl-1",
            "choices": [{"message": {"role": "assistant", "content": "Hi there"}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 5},
            "model": "gpt-4",
        }
        result = openai_to_anthropic(resp)
        assert result["content"][0] == {"type": "text", "text": "Hi there"}
        assert result["role"] == "assistant"

    def test_openai_to_anthropic_tool_calls(self):
        resp = {
            "id": "chatcmpl-1",
            "choices": [{
                "message": {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [{
                        "id": "call_abc",
                        "type": "function",
                        "function": {
                            "name": "get_weather",
                            "arguments": '{"city": "London"}',
                        },
                    }],
                },
                "finish_reason": "tool_calls",
            }],
            "usage": {"prompt_tokens": 10, "completion_tokens": 5},
            "model": "gpt-4",
        }
        result = openai_to_anthropic(resp)
        tool_block = result["content"][0]
        assert tool_block["type"] == "tool_use"
        assert tool_block["name"] == "get_weather"
        assert tool_block["input"] == {"city": "London"}

    def test_openai_to_anthropic_invalid_json_args(self):
        resp = {
            "id": "chatcmpl-1",
            "choices": [{
                "message": {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [{
                        "id": "call_abc",
                        "type": "function",
                        "function": {
                            "name": "f",
                            "arguments": "not json",
                        },
                    }],
                },
                "finish_reason": "tool_calls",
            }],
            "usage": {"prompt_tokens": 0, "completion_tokens": 0},
            "model": "gpt-4",
        }
        result = openai_to_anthropic(resp)
        assert result["content"][0]["input"] == {}

    def test_openai_to_anthropic_empty_choices(self):
        resp = {
            "id": "chatcmpl-1",
            "choices": [],
            "usage": {"prompt_tokens": 0, "completion_tokens": 0},
            "model": "gpt-4",
        }
        result = openai_to_anthropic(resp)
        assert result["content"] == [{"type": "text", "text": ""}]

    def test_openai_to_anthropic_no_content_no_tools(self):
        resp = {
            "id": "chatcmpl-1",
            "choices": [{"message": {"role": "assistant"}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 0, "completion_tokens": 0},
            "model": "gpt-4",
        }
        result = openai_to_anthropic(resp)
        assert result["content"] == [{"type": "text", "text": ""}]

    def test_openai_to_anthropic_stop_reason_stop(self):
        resp = {
            "id": "chatcmpl-1",
            "choices": [{"message": {"content": "x"}, "finish_reason": "stop"}],
            "usage": {},
            "model": "m",
        }
        assert openai_to_anthropic(resp)["stop_reason"] == "end_turn"

    def test_openai_to_anthropic_stop_reason_length(self):
        resp = {
            "id": "chatcmpl-1",
            "choices": [{"message": {"content": "x"}, "finish_reason": "length"}],
            "usage": {},
            "model": "m",
        }
        assert openai_to_anthropic(resp)["stop_reason"] == "max_tokens"

    def test_openai_to_anthropic_stop_reason_tool_calls(self):
        resp = {
            "id": "chatcmpl-1",
            "choices": [{"message": {"content": "x"}, "finish_reason": "tool_calls"}],
            "usage": {},
            "model": "m",
        }
        assert openai_to_anthropic(resp)["stop_reason"] == "tool_use"

    def test_openai_to_anthropic_stop_reason_unknown(self):
        resp = {
            "id": "chatcmpl-1",
            "choices": [{"message": {"content": "x"}, "finish_reason": "something_new"}],
            "usage": {},
            "model": "m",
        }
        assert openai_to_anthropic(resp)["stop_reason"] == "end_turn"

    def test_openai_to_anthropic_usage(self):
        resp = {
            "id": "chatcmpl-1",
            "choices": [{"message": {"content": "x"}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 100, "completion_tokens": 50},
            "model": "m",
        }
        result = openai_to_anthropic(resp)
        assert result["usage"]["input_tokens"] == 100
        assert result["usage"]["output_tokens"] == 50

    def test_openai_to_anthropic_model_id_override(self):
        resp = {
            "id": "chatcmpl-1",
            "choices": [{"message": {"content": "x"}, "finish_reason": "stop"}],
            "usage": {},
            "model": "original-model",
        }
        result = openai_to_anthropic(resp, model_id="override-model")
        assert result["model"] == "override-model"


# ---------------------------------------------------------------------------
# Stream conversion tests
# ---------------------------------------------------------------------------

class TestStreamConversion:

    def test_stream_empty_line(self):
        assert openai_stream_to_anthropic_stream(b"", "m") is None

    def test_stream_non_data_line(self):
        assert openai_stream_to_anthropic_stream(b": keep-alive", "m") is None

    def test_stream_done(self):
        result = openai_stream_to_anthropic_stream(b"data: [DONE]", "m")
        assert result is not None
        text = result.decode()
        assert "content_block_stop" in text
        assert "message_delta" in text
        assert "message_stop" in text

    def test_stream_text_delta(self):
        line = b'data: {"choices": [{"delta": {"content": "Hello"}, "finish_reason": null}]}'
        result = openai_stream_to_anthropic_stream(line, "m")
        assert result is not None
        text = result.decode()
        assert "content_block_delta" in text
        assert "text_delta" in text
        assert "Hello" in text

    def test_stream_tool_call_start(self):
        line = json.dumps({
            "choices": [{
                "delta": {
                    "tool_calls": [{
                        "index": 0,
                        "id": "call_abc",
                        "function": {"name": "get_weather"},
                    }],
                },
                "finish_reason": None,
            }],
        }).encode()
        line = b"data: " + line
        result = openai_stream_to_anthropic_stream(line, "m")
        assert result is not None
        text = result.decode()
        assert "content_block_start" in text
        assert "tool_use" in text
        assert "get_weather" in text

    def test_stream_tool_call_args(self):
        line = json.dumps({
            "choices": [{
                "delta": {
                    "tool_calls": [{
                        "index": 0,
                        "function": {"arguments": '{"city":'},
                    }],
                },
                "finish_reason": None,
            }],
        }).encode()
        line = b"data: " + line
        result = openai_stream_to_anthropic_stream(line, "m")
        assert result is not None
        text = result.decode()
        assert "content_block_delta" in text
        assert "input_json_delta" in text

    def test_stream_invalid_json(self):
        assert openai_stream_to_anthropic_stream(b"data: {invalid json", "m") is None

    def test_stream_empty_delta(self):
        line = b'data: {"choices": [{"delta": {}, "finish_reason": null}]}'
        assert openai_stream_to_anthropic_stream(line, "m") is None


# ---------------------------------------------------------------------------
# Utility tests
# ---------------------------------------------------------------------------

class TestUtilities:

    def test_make_anthropic_stream_start(self):
        result = make_anthropic_stream_start("test-model")
        text = result.decode()
        assert "message_start" in text
        assert "content_block_start" in text
        assert "test-model" in text

    def test_sse_event_format(self):
        result = _sse_event("content_block_delta", {"type": "content_block_delta"})
        text = result.decode()
        assert text.startswith("event: content_block_delta\n")
        assert "data: " in text
        assert text.endswith("\n\n")


# ---------------------------------------------------------------------------
# Hypothesis property tests
# ---------------------------------------------------------------------------

class TestPropertyBased:

    @given(payload=st_anthropic_payload())
    @settings(max_examples=200)
    def test_prop_anthropic_to_openai_valid_structure(self, payload):
        result = anthropic_to_openai(payload)
        assert "model" in result
        assert "messages" in result
        assert "stream" in result
        assert isinstance(result["messages"], list)

    @given(resp=st_openai_response())
    @settings(max_examples=200)
    def test_prop_openai_to_anthropic_valid_structure(self, resp):
        result = openai_to_anthropic(resp)
        assert "id" in result
        assert result["type"] == "message"
        assert result["role"] == "assistant"
        assert "content" in result
        assert isinstance(result["content"], list)
        assert "model" in result
        assert "stop_reason" in result
        assert "usage" in result

    @given(tool=st_anthropic_tool())
    @settings(max_examples=200)
    def test_prop_tool_roundtrip(self, tool):
        result = _convert_tool_to_openai(tool)
        assert result["function"]["name"] == tool["name"]
        assert result["function"]["parameters"] == tool["input_schema"]

    @given(line=st_openai_stream_line())
    @settings(max_examples=200)
    def test_prop_stream_never_crashes(self, line):
        result = openai_stream_to_anthropic_stream(line, "m")
        assert result is None or isinstance(result, bytes)

    @given(payload=st_anthropic_payload())
    @settings(max_examples=200)
    def test_prop_anthropic_to_openai_message_count(self, payload):
        result = anthropic_to_openai(payload)
        input_count = len(payload["messages"])
        output_count = len(result["messages"])
        # System message adds one if present
        has_system = "system" in payload and payload["system"]
        if has_system:
            assert output_count >= input_count  # system adds at least one
        else:
            assert output_count >= input_count
