"""Tests for ecospheric_harness.providers.ollama.OllamaProvider."""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import httpx
import pytest

from ecospheric_harness.providers.base import (
    ModelResponse,
    ProviderError,
    StreamChunk,
)
from ecospheric_harness.providers.ollama import OllamaProvider


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------

_HOST = "http://localhost:11434"
_MODEL = "llama3.2"


def _provider(**kwargs) -> OllamaProvider:
    return OllamaProvider(host=kwargs.pop("host", _HOST), model=kwargs.pop("model", _MODEL), **kwargs)


def _ollama_response(
    content: str | None = None,
    tool_calls: list[dict] | None = None,
) -> dict:
    """Build a realistic Ollama /api/chat response body."""
    msg: dict = {"role": "assistant"}
    if content is not None:
        msg["content"] = content
    if tool_calls is not None:
        msg["tool_calls"] = tool_calls
    return {
        "model": _MODEL,
        "message": msg,
        "done": True,
        "total_duration": 1_000_000,
        "prompt_eval_count": 80,
        "eval_count": 20,
    }


def _ollama_tool_calls_dict_args() -> list[dict]:
    """Ollama returns tool_calls with arguments as a dict (not JSON string)."""
    return [{
        "function": {
            "name": "emit_intent",
            "arguments": {"intent": "clip", "params": {"input": "/tmp/raster.tif"}},
        },
    }]


def _mock_post(body: dict, status_code: int = 200) -> MagicMock:
    resp = MagicMock()
    resp.status_code = status_code
    resp.json.return_value = body
    resp.raise_for_status = MagicMock()
    if status_code >= 400:
        resp.raise_for_status.side_effect = httpx.HTTPStatusError(
            f"HTTP {status_code}",
            request=MagicMock(),
            response=MagicMock(status_code=status_code),
        )
    resp.text = json.dumps(body)
    return resp


# ---------------------------------------------------------------------------
# generate() — success
# ---------------------------------------------------------------------------


class TestGenerateSuccess:
    @patch("ecospheric_harness.providers.ollama.httpx.post")
    def test_returns_model_response(self, mock_post: MagicMock) -> None:
        body = _ollama_response(content="Hello!")
        mock_post.return_value = _mock_post(body)

        resp = _provider().generate("sys", [{"role": "user", "content": "hi"}], {})

        assert isinstance(resp, ModelResponse)
        assert resp.finish_reason == "stop"

    @patch("ecospheric_harness.providers.ollama.httpx.post")
    def test_tool_calls_normalized(self, mock_post: MagicMock) -> None:
        """Ollama tool_calls arguments (dict) → JSON string in ModelResponse."""
        tc = _ollama_tool_calls_dict_args()
        body = _ollama_response(tool_calls=tc)
        mock_post.return_value = _mock_post(body)

        resp = _provider().generate("sys", [], {"type": "function", "function": {"name": "emit_intent"}})

        assert len(resp.tool_calls) == 1
        # Arguments must be a JSON string, not a dict
        args = resp.tool_calls[0]["function"]["arguments"]
        assert isinstance(args, str)
        parsed = json.loads(args)
        assert parsed["intent"] == "clip"
        assert parsed["params"]["input"] == "/tmp/raster.tif"

    @patch("ecospheric_harness.providers.ollama.httpx.post")
    def test_tool_call_id_generated(self, mock_post: MagicMock) -> None:
        tc = _ollama_tool_calls_dict_args()
        body = _ollama_response(tool_calls=tc)
        mock_post.return_value = _mock_post(body)

        resp = _provider().generate("sys", [], {})

        # Ollama doesn't provide tool call IDs; provider should generate one
        assert resp.tool_call_id
        assert isinstance(resp.tool_call_id, str)

    @patch("ecospheric_harness.providers.ollama.httpx.post")
    def test_plain_text_no_tool_calls(self, mock_post: MagicMock) -> None:
        body = _ollama_response(content="Just text")
        mock_post.return_value = _mock_post(body)

        resp = _provider().generate("sys", [], {})

        assert resp.tool_calls == []
        assert resp.tool_call_id == ""

    @patch("ecospheric_harness.providers.ollama.httpx.post")
    def test_usage_from_eval_counts(self, mock_post: MagicMock) -> None:
        body = _ollama_response(content="ok")
        mock_post.return_value = _mock_post(body)

        resp = _provider().generate("sys", [], {})

        assert resp.usage.input_tokens == 80  # prompt_eval_count
        assert resp.usage.output_tokens == 20  # eval_count


# ---------------------------------------------------------------------------
# generate() — payload construction
# ---------------------------------------------------------------------------


class TestGeneratePayload:
    @patch("ecospheric_harness.providers.ollama.httpx.post")
    def test_posts_to_correct_endpoint(self, mock_post: MagicMock) -> None:
        body = _ollama_response(content="ok")
        mock_post.return_value = _mock_post(body)

        _provider().generate("sys", [], {})

        url = mock_post.call_args.args[0]
        assert url == f"{_HOST}/api/chat"

    @patch("ecospheric_harness.providers.ollama.httpx.post")
    def test_custom_host(self, mock_post: MagicMock) -> None:
        body = _ollama_response(content="ok")
        mock_post.return_value = _mock_post(body)

        _provider(host="http://gpu-server:11434").generate("sys", [], {})

        url = mock_post.call_args.args[0]
        assert url == "http://gpu-server:11434/api/chat"

    @patch("ecospheric_harness.providers.ollama.httpx.post")
    def test_payload_includes_model_and_messages(self, mock_post: MagicMock) -> None:
        body = _ollama_response(content="ok")
        mock_post.return_value = _mock_post(body)

        messages = [{"role": "user", "content": "hello"}]
        tool_def = {"type": "function", "function": {"name": "emit_intent"}}

        _provider().generate("system prompt", messages, tool_def)

        payload = mock_post.call_args.kwargs["json"]
        assert payload["model"] == _MODEL
        assert payload["messages"][0] == {"role": "system", "content": "system prompt"}
        assert payload["messages"][1] == {"role": "user", "content": "hello"}
        assert payload["tools"] == [tool_def]
        assert payload["stream"] is False

    @patch("ecospheric_harness.providers.ollama.httpx.post")
    def test_timeout_param(self, mock_post: MagicMock) -> None:
        body = _ollama_response(content="ok")
        mock_post.return_value = _mock_post(body)

        _provider(timeout=15.0).generate("sys", [], {})

        assert mock_post.call_args.kwargs.get("timeout") == 15.0


# ---------------------------------------------------------------------------
# generate() — errors
# ---------------------------------------------------------------------------


class TestGenerateErrors:
    @patch("ecospheric_harness.providers.ollama.httpx.post")
    def test_connection_error(self, mock_post: MagicMock) -> None:
        mock_post.side_effect = httpx.ConnectError("Connection refused")

        with pytest.raises(ProviderError) as exc_info:
            _provider().generate("sys", [], {})
        assert exc_info.value.error_type == "unknown"
        assert exc_info.value.retryable is True

    @patch("ecospheric_harness.providers.ollama.httpx.post")
    def test_timeout_error(self, mock_post: MagicMock) -> None:
        mock_post.side_effect = httpx.TimeoutException("timed out")

        with pytest.raises(ProviderError) as exc_info:
            _provider().generate("sys", [], {})
        assert exc_info.value.error_type == "timeout"
        assert exc_info.value.retryable is True

    @patch("ecospheric_harness.providers.ollama.httpx.post")
    def test_http_500_error(self, mock_post: MagicMock) -> None:
        mock_post.return_value = _mock_post({}, status_code=500)

        with pytest.raises(ProviderError) as exc_info:
            _provider().generate("sys", [], {})
        assert exc_info.value.error_type == "unknown"
        assert exc_info.value.retryable is False


# ---------------------------------------------------------------------------
# stream() — success
# ---------------------------------------------------------------------------


class TestStreamSuccess:
    @patch("ecospheric_harness.providers.ollama.httpx.Client")
    def test_yields_stream_chunks(self, mock_client_cls: MagicMock) -> None:
        ndjson_lines = [
            json.dumps({"message": {"content": "Hello"}, "done": False}),
            json.dumps({"message": {"content": " world"}, "done": False}),
            json.dumps({"message": {"content": ""}, "done": True}),
        ]

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.raise_for_status = MagicMock()
        mock_resp.iter_lines.return_value = iter(ndjson_lines)

        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.stream.return_value = mock_resp
        mock_client_cls.return_value = mock_client

        chunks = list(_provider().stream("sys", [], {}))

        assert len(chunks) >= 2
        assert all(isinstance(c, StreamChunk) for c in chunks)
        assert chunks[0].delta == "Hello"
        assert chunks[1].delta == " world"

    @patch("ecospheric_harness.providers.ollama.httpx.Client")
    def test_stream_finish_reason_on_done(self, mock_client_cls: MagicMock) -> None:
        ndjson_lines = [
            json.dumps({"message": {"content": "ok"}, "done": False}),
            json.dumps({"message": {"content": ""}, "done": True}),
        ]

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.raise_for_status = MagicMock()
        mock_resp.iter_lines.return_value = iter(ndjson_lines)

        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.stream.return_value = mock_resp
        mock_client_cls.return_value = mock_client

        chunks = list(_provider().stream("sys", [], {}))

        # Last chunk should have finish_reason="stop" when done=True
        assert chunks[-1].finish_reason == "stop"

    @patch("ecospheric_harness.providers.ollama.httpx.Client")
    def test_stream_tool_call_delta(self, mock_client_cls: MagicMock) -> None:
        tc = {"function": {"name": "emit_intent", "arguments": {"intent": "clip"}}}
        ndjson_lines = [
            json.dumps({"message": {"tool_calls": [tc]}, "done": False}),
            json.dumps({"message": {"content": ""}, "done": True}),
        ]

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.raise_for_status = MagicMock()
        mock_resp.iter_lines.return_value = iter(ndjson_lines)

        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.stream.return_value = mock_resp
        mock_client_cls.return_value = mock_client

        chunks = list(_provider().stream("sys", [], {}))

        tc_chunks = [c for c in chunks if c.tool_call_delta is not None]
        assert len(tc_chunks) >= 1


# ---------------------------------------------------------------------------
# stream() — errors
# ---------------------------------------------------------------------------


class TestStreamErrors:
    @patch("ecospheric_harness.providers.ollama.httpx.Client")
    def test_stream_connection_error(self, mock_client_cls: MagicMock) -> None:
        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.stream.side_effect = httpx.ConnectError("Connection refused")
        mock_client_cls.return_value = mock_client

        with pytest.raises(ProviderError) as exc_info:
            list(_provider().stream("sys", [], {}))
        assert exc_info.value.retryable is True


# ---------------------------------------------------------------------------
# Tool-call normalization — edge cases
# ---------------------------------------------------------------------------


class TestToolCallNormalization:
    @patch("ecospheric_harness.providers.ollama.httpx.post")
    def test_nested_dict_arguments_become_json_string(self, mock_post: MagicMock) -> None:
        """Deeply nested dict arguments are serialized to JSON string."""
        tc = [{
            "function": {
                "name": "emit_intent",
                "arguments": {
                    "intent": "search_stac",
                    "params": {
                        "bbox": [-121.5, 38.2, -121.3, 38.4],
                        "source": "sentinel-2",
                        "nested": {"key": "value"},
                    },
                },
            },
        }]
        body = _ollama_response(tool_calls=tc)
        mock_post.return_value = _mock_post(body)

        resp = _provider().generate("sys", [], {})

        args_str = resp.tool_calls[0]["function"]["arguments"]
        assert isinstance(args_str, str)
        parsed = json.loads(args_str)
        assert parsed["params"]["bbox"] == [-121.5, 38.2, -121.3, 38.4]
        assert parsed["params"]["nested"]["key"] == "value"

    @patch("ecospheric_harness.providers.ollama.httpx.post")
    def test_already_string_arguments_unchanged(self, mock_post: MagicMock) -> None:
        """If arguments are already a string, they stay as-is."""
        tc = [{
            "function": {
                "name": "emit_intent",
                "arguments": '{"intent": "clip"}',
            },
        }]
        body = _ollama_response(tool_calls=tc)
        mock_post.return_value = _mock_post(body)

        resp = _provider().generate("sys", [], {})

        args = resp.tool_calls[0]["function"]["arguments"]
        assert isinstance(args, str)
        assert json.loads(args)["intent"] == "clip"

    @patch("ecospheric_harness.providers.ollama.httpx.post")
    def test_multiple_tool_calls_all_normalized(self, mock_post: MagicMock) -> None:
        """Multiple tool_calls all get their arguments normalized."""
        tc = [
            {"function": {"name": "fn1", "arguments": {"a": 1}}},
            {"function": {"name": "fn2", "arguments": {"b": 2}}},
        ]
        body = _ollama_response(tool_calls=tc)
        mock_post.return_value = _mock_post(body)

        resp = _provider().generate("sys", [], {})

        assert len(resp.tool_calls) == 2
        for call in resp.tool_calls:
            assert isinstance(call["function"]["arguments"], str)
