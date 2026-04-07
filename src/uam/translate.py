"""Format translation between Anthropic Messages API and OpenAI Chat Completions API."""

import json
import logging
import re
import uuid

logger = logging.getLogger("uam.translate")


def anthropic_to_openai(payload: dict) -> dict:
    """Convert an Anthropic Messages API request to OpenAI Chat Completions format.

    Anthropic: {model, messages, system?, max_tokens, stream?, tools?, ...}
    OpenAI:    {model, messages, max_tokens?, stream?, tools?, ...}
    """
    messages = []

    # System prompt → system message
    system = payload.get("system")
    if system:
        if isinstance(system, str):
            messages.append({"role": "system", "content": system})
        elif isinstance(system, list):
            # Anthropic system can be a list of content blocks
            parts = []
            for b in system:
                if b.get("type") == "text":
                    # M4: use .get() so a malformed {"type": "text"} (no
                    # "text" key, which Anthropic SDK can produce from
                    # cached prompt blocks) does not raise KeyError.
                    parts.append(b.get("text", ""))
                else:
                    # Non-text system blocks: convert to text representation
                    btype = b.get("type", "unknown")
                    parts.append(f"[unsupported: {btype}] {json.dumps(b)}")
                    logger.warning(f"Non-text system block type: {btype}")
            text = "\n".join(parts)
            if text:
                messages.append({"role": "system", "content": text})

    # Convert messages — Anthropic messages with multiple tool_result
    # blocks need to expand into multiple OpenAI tool messages
    for msg in payload.get("messages", []):
        content = msg.get("content")
        if isinstance(content, list):
            tool_results = [b for b in content if b.get("type") == "tool_result"]
            non_tool = [b for b in content if b.get("type") != "tool_result"]
            # H4: any number of tool_result blocks (including exactly one)
            # combined with other content must expand into a non-tool-result
            # message followed by the tool messages — otherwise the text
            # narrative is silently dropped by _convert_message_to_openai.
            if tool_results and (len(tool_results) > 1 or non_tool):
                if non_tool:
                    messages.append(_convert_message_to_openai(
                        {"role": msg.get("role", "user"), "content": non_tool}
                    ))
                for tr in tool_results:
                    tr_content = tr.get("content", "")
                    if isinstance(tr_content, list):
                        tr_content = "\n".join(
                            b.get("text", "") for b in tr_content
                            if b.get("type") == "text"
                        )
                    messages.append({
                        "role": "tool",
                        "tool_call_id": tr.get("tool_use_id", ""),
                        "content": str(tr_content),
                    })
                continue
        messages.append(_convert_message_to_openai(msg))

    result = {
        "model": payload.get("model", ""),
        "messages": messages,
        "stream": payload.get("stream", False),
    }

    if "max_tokens" in payload:
        result["max_tokens"] = payload["max_tokens"]

    if "temperature" in payload:
        result["temperature"] = payload["temperature"]

    if "top_p" in payload:
        result["top_p"] = payload["top_p"]

    if "stop_sequences" in payload:
        result["stop"] = payload["stop_sequences"]

    # Tool conversion
    if "tools" in payload:
        result["tools"] = [_convert_tool_to_openai(t) for t in payload["tools"]]

    # Strip Anthropic-specific thinking parameter (non-Anthropic backends don't understand it)
    if "thinking" in payload:
        logger.debug("Stripped thinking parameter from translated request")

    return result


def _convert_message_to_openai(msg: dict) -> dict:
    """Convert a single Anthropic message to OpenAI format."""
    role = msg.get("role", "user")
    content = msg.get("content")

    # Simple string content
    if isinstance(content, str):
        return {"role": role, "content": content}

    # Content blocks
    if isinstance(content, list):
        tool_calls = []
        text_parts = []
        tool_results = []

        for block in content:
            btype = block.get("type", "")
            if btype == "text":
                text_parts.append(block.get("text", ""))
            elif btype == "thinking":
                # Strip Anthropic-specific thinking blocks (history from extended thinking)
                logger.debug("Stripped thinking content block from message")
                continue
            elif btype == "image":
                # Image blocks not supported by OpenAI-compatible backends
                text_parts.append("[Image content — not supported by this model]")
                logger.warning("Image content block converted to placeholder text")
            elif btype == "tool_use":
                tool_calls.append({
                    "id": block.get("id", ""),
                    "type": "function",
                    "function": {
                        "name": block.get("name", ""),
                        "arguments": json.dumps(block.get("input", {})),
                    },
                })
            elif btype == "tool_result":
                tool_content = block.get("content", "")
                if isinstance(tool_content, list):
                    tool_content = "\n".join(
                        b.get("text", "") for b in tool_content
                        if b.get("type") == "text"
                    )
                tool_results.append({
                    "role": "tool",
                    "tool_call_id": block.get("tool_use_id", ""),
                    "content": str(tool_content),
                })
            else:
                # Unknown block type — convert to text representation
                text_parts.append(f"[unsupported: {btype}] {json.dumps(block)}")
                logger.warning(f"Unknown content block type: {btype}")

        # Tool results: return first one (caller must handle multiple
        # tool_result blocks by calling this function per-block)
        if tool_results:
            return tool_results[0]

        result = {"role": role}
        if text_parts:
            result["content"] = "\n".join(text_parts)
        if tool_calls:
            result["tool_calls"] = tool_calls
            if "content" not in result:
                result["content"] = None
        elif not text_parts:
            result["content"] = ""

        return result

    return {"role": role, "content": str(content) if content else ""}


def _convert_tool_to_openai(tool: dict) -> dict:
    """Convert an Anthropic tool definition to OpenAI format."""
    return {
        "type": "function",
        "function": {
            "name": tool.get("name", ""),
            "description": tool.get("description", ""),
            "parameters": tool.get("input_schema", {}),
        },
    }


def openai_to_anthropic(
    response_data: dict,
    model_id: str = "",
    extract_think_tags: bool = False,
) -> dict:
    """Convert an OpenAI Chat Completions response to Anthropic Messages API format.

    OpenAI:    {id, choices, usage, model, ...}
    Anthropic: {id, type, role, content, model, stop_reason, usage}

    Args:
        response_data: OpenAI-format response dict.
        model_id: Override model ID in the response.
        extract_think_tags: If True, extract leading <think>...</think> tags from
            text content into a thinking block. Off by default; enable per-model
            for backends that emit reasoning inline (e.g. some local R1 deploys).
    """
    choice = {}
    if response_data.get("choices"):
        choice = response_data["choices"][0]

    message = choice.get("message", {})
    content_blocks = []

    # Reasoning content (e.g. DeepSeek R1, some vLLM deploys) → thinking block
    reasoning = message.get("reasoning_content")
    if reasoning:
        content_blocks.append({"type": "thinking", "thinking": reasoning})

    # Text content
    text = message.get("content")
    if text:
        # Optional <think> tag extraction (only at start of text)
        if extract_think_tags:
            m = re.match(r"^<think>(.*?)</think>\s*", text, re.DOTALL)
            if m:
                content_blocks.append({
                    "type": "thinking",
                    "thinking": m.group(1),
                })
                text = text[m.end():]
        if text:
            content_blocks.append({"type": "text", "text": text})

    # Tool calls → tool_use blocks
    for tc in message.get("tool_calls", []):
        func = tc.get("function", {})
        try:
            args = json.loads(func.get("arguments", "{}"))
        except json.JSONDecodeError:
            args = {}
        content_blocks.append({
            "type": "tool_use",
            "id": tc.get("id", f"toolu_{uuid.uuid4().hex[:24]}"),
            "name": func.get("name", ""),
            "input": args,
        })

    if not content_blocks:
        content_blocks.append({"type": "text", "text": ""})

    # Map stop reason
    finish = choice.get("finish_reason", "stop")
    stop_reason_map = {
        "stop": "end_turn",
        "length": "max_tokens",
        "tool_calls": "tool_use",
        "content_filter": "end_turn",
    }
    stop_reason = stop_reason_map.get(finish, "end_turn")

    # Usage mapping
    openai_usage = response_data.get("usage", {})
    usage = {
        "input_tokens": openai_usage.get("prompt_tokens", 0),
        "output_tokens": openai_usage.get("completion_tokens", 0),
    }

    return {
        "id": response_data.get("id", f"msg_{uuid.uuid4().hex[:24]}"),
        "type": "message",
        "role": "assistant",
        "content": content_blocks,
        "model": model_id or response_data.get("model", ""),
        "stop_reason": stop_reason,
        "stop_sequence": None,
        "usage": usage,
    }


def openai_stream_to_anthropic_stream(line: bytes, model_id: str = "") -> bytes | None:
    """Convert a single OpenAI SSE streaming line to Anthropic SSE format.

    OpenAI streams:  data: {"choices": [{"delta": {...}}]}
    Anthropic streams: event: content_block_delta\\ndata: {...}

    Returns the converted line(s) as bytes, or None if the line should be skipped.
    """
    text = line.decode("utf-8", errors="replace").strip()
    if not text or not text.startswith("data: "):
        return None

    data_str = text[6:]  # strip "data: "
    if data_str == "[DONE]":
        # Send content_block_stop + message_stop (Anthropic protocol)
        return (
            _sse_event("content_block_stop", {
                "type": "content_block_stop",
                "index": 0,
            })
            + _sse_event("message_delta", {
                "type": "message_delta",
                "delta": {"stop_reason": "end_turn", "stop_sequence": None},
                "usage": {"output_tokens": 0},
            })
            + _sse_event("message_stop", {
                "type": "message_stop",
            })
        )

    try:
        data = json.loads(data_str)
    except json.JSONDecodeError:
        return None

    choice = data.get("choices", [{}])[0]
    delta = choice.get("delta", {})
    finish_reason = choice.get("finish_reason")

    parts = []

    # H3: Streaming reasoning_content is intentionally NOT emitted.
    # The Anthropic streaming protocol requires that thinking_delta events
    # only target a content block whose content_block_start declared
    # type=thinking, but make_anthropic_stream_start opens index 0 as a
    # text block. Emitting thinking_delta against a text block is a
    # protocol violation that strict clients (newer Claude Code) reject.
    # The non-streaming path (openai_to_anthropic) remains the
    # authoritative source for reasoning_content.

    # Text delta
    if "content" in delta and delta["content"]:
        parts.append(_sse_event("content_block_delta", {
            "type": "content_block_delta",
            "index": 0,
            "delta": {
                "type": "text_delta",
                "text": delta["content"],
            },
        }))

    # Tool call delta
    if "tool_calls" in delta:
        for tc in delta["tool_calls"]:
            idx = tc.get("index", 0)
            func = tc.get("function", {})
            if "name" in func:
                # Tool call start
                parts.append(_sse_event("content_block_start", {
                    "type": "content_block_start",
                    "index": idx + 1,  # text block is index 0
                    "content_block": {
                        "type": "tool_use",
                        "id": tc.get("id", f"toolu_{uuid.uuid4().hex[:24]}"),
                        "name": func["name"],
                        "input": {},
                    },
                }))
            if "arguments" in func:
                parts.append(_sse_event("content_block_delta", {
                    "type": "content_block_delta",
                    "index": idx + 1,
                    "delta": {
                        "type": "input_json_delta",
                        "partial_json": func["arguments"],
                    },
                }))

    # Note: finish_reason is handled when [DONE] is received,
    # not here, to avoid duplicate message_delta events.

    if parts:
        return b"".join(parts)
    return None


def make_anthropic_stream_start(model_id: str) -> bytes:
    """Create the message_start SSE event for an Anthropic stream."""
    return _sse_event("message_start", {
        "type": "message_start",
        "message": {
            "id": f"msg_{uuid.uuid4().hex[:24]}",
            "type": "message",
            "role": "assistant",
            "content": [],
            "model": model_id,
            "stop_reason": None,
            "stop_sequence": None,
            "usage": {"input_tokens": 0, "output_tokens": 0},
        },
    }) + _sse_event("content_block_start", {
        "type": "content_block_start",
        "index": 0,
        "content_block": {"type": "text", "text": ""},
    })


def _sse_event(event_type: str, data: dict) -> bytes:
    """Format an SSE event."""
    return f"event: {event_type}\ndata: {json.dumps(data)}\n\n".encode()
