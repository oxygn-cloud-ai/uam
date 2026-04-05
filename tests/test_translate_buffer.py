"""Tests for the buffer-splitting logic used in proxy streaming.

These test the line-buffered SSE conversion algorithm in isolation,
without requiring aiohttp.
"""

from uam.translate import openai_stream_to_anthropic_stream


def simulate_buffer_split(chunks: list[bytes], model_id: str) -> list[bytes]:
    """Simulate the line-buffered SSE conversion from _proxy_with_translation."""
    results = []
    buffer = b""
    for chunk in chunks:
        buffer += chunk
        while b"\n" in buffer:
            line, buffer = buffer.split(b"\n", 1)
            if not line.strip():
                continue
            converted = openai_stream_to_anthropic_stream(line, model_id)
            if converted:
                results.append(converted)
    if buffer.strip():
        converted = openai_stream_to_anthropic_stream(buffer, model_id)
        if converted:
            results.append(converted)
    return results


class TestBufferSplit:

    def test_buffer_multiple_lines_one_chunk(self):
        chunk = (
            b'data: {"choices":[{"delta":{"content":"hi"},"finish_reason":null}]}\n'
            b'data: {"choices":[{"delta":{"content":" there"},"finish_reason":null}]}\n'
        )
        results = simulate_buffer_split([chunk], "m")
        assert len(results) == 2
        assert b"hi" in results[0]
        assert b" there" in results[1]

    def test_buffer_partial_line_carryover(self):
        chunk1 = b'data: {"choices":[{'
        chunk2 = b'"delta":{"content":"hello"},"finish_reason":null}]}\n'
        results = simulate_buffer_split([chunk1, chunk2], "m")
        assert len(results) == 1
        assert b"hello" in results[0]

    def test_buffer_no_trailing_newline(self):
        chunk = b'data: {"choices":[{"delta":{"content":"end"},"finish_reason":null}]}'
        results = simulate_buffer_split([chunk], "m")
        # Processed as leftover (no newline, but buffer.strip() is truthy)
        assert len(results) == 1
        assert b"end" in results[0]

    def test_buffer_empty_chunks(self):
        chunks = [
            b"",
            b'data: {"choices":[{"delta":{"content":"x"},"finish_reason":null}]}\n',
            b"",
        ]
        results = simulate_buffer_split(chunks, "m")
        assert len(results) == 1

    def test_buffer_done_signal(self):
        chunk = b"data: [DONE]\n"
        results = simulate_buffer_split([chunk], "m")
        assert len(results) == 1
        text = results[0].decode()
        assert "message_stop" in text
