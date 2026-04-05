"""Hypothesis strategies for generating Anthropic/OpenAI API types."""

import json

from hypothesis import strategies as st


def st_text_block():
    """Generate an Anthropic text content block."""
    return st.fixed_dictionaries({
        "type": st.just("text"),
        "text": st.text(min_size=1, max_size=200),
    })


def st_tool_use_block():
    """Generate an Anthropic tool_use content block."""
    return st.fixed_dictionaries({
        "type": st.just("tool_use"),
        "id": st.from_regex(r"toolu_[a-f0-9]{24}", fullmatch=True),
        "name": st.from_regex(r"[a-z_]{1,30}", fullmatch=True),
        "input": st.fixed_dictionaries({"key": st.text(max_size=50)}),
    })


def st_tool_result_block():
    """Generate an Anthropic tool_result content block."""
    return st.fixed_dictionaries({
        "type": st.just("tool_result"),
        "tool_use_id": st.from_regex(r"toolu_[a-f0-9]{24}", fullmatch=True),
        "content": st.one_of(st.text(max_size=200), st.just("ok")),
    })


def st_anthropic_message():
    """Generate a single Anthropic message."""
    return st.fixed_dictionaries({
        "role": st.sampled_from(["user", "assistant"]),
        "content": st.one_of(
            st.text(min_size=1, max_size=200),
            st.lists(st_text_block(), min_size=1, max_size=3),
        ),
    })


def st_anthropic_tool():
    """Generate an Anthropic tool definition."""
    return st.fixed_dictionaries({
        "name": st.from_regex(r"[a-z_]{1,30}", fullmatch=True),
        "description": st.text(max_size=100),
        "input_schema": st.just({"type": "object", "properties": {}}),
    })


@st.composite
def st_anthropic_payload(draw):
    """Build a full Anthropic Messages API payload with valid alternating roles."""
    raw_messages = draw(st.lists(st_anthropic_message(), min_size=1, max_size=6))

    # Force alternating user/assistant roles starting with user
    messages = []
    for i, msg in enumerate(raw_messages):
        role = "user" if i % 2 == 0 else "assistant"
        messages.append({"role": role, "content": msg["content"]})

    # Ensure last message is from user (required by Anthropic API)
    if messages[-1]["role"] != "user":
        messages.append({"role": "user", "content": "continue"})

    payload = {
        "model": "test-model",
        "messages": messages,
        "max_tokens": draw(st.integers(min_value=1, max_value=4096)),
    }

    # Optionally add system
    if draw(st.booleans()):
        payload["system"] = draw(st.text(min_size=1, max_size=200))

    # Optionally add temperature
    if draw(st.booleans()):
        payload["temperature"] = draw(st.floats(min_value=0.0, max_value=2.0))

    # Optionally add tools
    if draw(st.booleans()):
        payload["tools"] = draw(st.lists(st_anthropic_tool(), min_size=1, max_size=3))

    return payload


def st_openai_response():
    """Generate an OpenAI Chat Completions response."""
    return st.fixed_dictionaries({
        "id": st.just("chatcmpl-test"),
        "choices": st.just([]).flatmap(lambda _: st.fixed_dictionaries({
            "message": st.fixed_dictionaries({
                "role": st.just("assistant"),
                "content": st.text(max_size=200),
            }),
            "finish_reason": st.sampled_from(
                ["stop", "length", "tool_calls", "content_filter"]
            ),
        }).map(lambda c: [c])),
        "usage": st.fixed_dictionaries({
            "prompt_tokens": st.integers(0, 1000),
            "completion_tokens": st.integers(0, 1000),
        }),
        "model": st.just("test-model"),
    })


def st_openai_stream_line():
    """Generate a single OpenAI SSE stream line as bytes."""
    return st.text(min_size=1, max_size=200).map(
        lambda text: b"data: " + json.dumps({
            "choices": [{
                "delta": {"content": text},
                "finish_reason": None,
            }]
        }).encode()
    )
