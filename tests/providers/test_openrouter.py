"""Tests for ecospheric_harness.providers.openrouter.OpenRouterProvider."""

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
from ecospheric_harness.providers.openrouter import OpenRouterProvider


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------

_API_KEY = "test-key-abc123"
_MODEL = "anthropic/claude-sonnet-4"


def _provider(**kwargs) -> OpenRouterProvider:
    return OpenRouterProvider(api_key=_API_KEY, model=_MODEL, **kwargs)


def _success_response(tool_calls: list[dict] | None = None) -> dict:
    """Build a realistic OpenRouter / chat completions JSON body."""
    tc = tool_calls or [{
        "id": "call_1",
        "type": "function",
        "function": {
            "name": "emit_intent",
            "arguments": json.dumps({"intent": "clip"}),
        },
    }]
    return {
        "choices": [{
            "message": {
                "tool_calls": tc,
                "finish_reason": "tool_calls",
            },
            "finish_reason": "tool_calls",
        }],
        "usage": {
            "prompt_tokens": 120,
            "completion_tokens": 30,
        },
        "model": _MODEL,
    }


def _mock_httpx_post(body: dict, status_code: int = 200) -> MagicMock:
    """Return a mock httpx.Response."""
    resp = MagicMock()
    resp.status_code = status_code
    resp.json.return_value = body
    resp.raise_for_status = MagicMock()
    if status_code >= 400:
        resp.raise_for_status.side_effect = httpx.HTTPStatusError(
            message=f"HTTP {status_code}",
            request=MagicMock(),
            response=MagicMock(status_code=status_code),
        )
    resp.text = json.dumps(body)
    return resp


# ---------------------------------------------------------------------------
# generate() — success
# ---------------------------------------------------------------------------


class TestGenerateSuccess:
    @patch("ecospheric_harness.providers.openrouter.httpx.post")
    def test_returns_model_response(self, mock_post: MagicMock) -> None:
        body = _success_response()
        mock_post.return_value = _mock_httpx_post(body)

        resp = _provider().generate(
            system_prompt="sys",
            messages=[{"role": "user", "content": "hi"}],
            tool_def={"type": "function", "function": {"name": "emit_intent"}},
        )

        assert isinstance(resp, ModelResponse)
        assert len(resp.tool_calls) == 1
        assert resp.tool_calls[0]["id"] == "call_1"
        assert resp.finish_reason == "tool_calls"

    @patch("ecospheric_harness.providers.openrouter.httpx.post")
    def test_usage_parsed(self, mock_post: MagicMock) -> None:
        body = _success_response()
        mock_post.return_value = _mock_httpx_post(body)

        resp = _provider().generate("sys", [], {})

        assert resp.usage.input_tokens == 120
        assert resp.usage.output_tokens == 30

    @patch("ecospheric_harness.providers.openrouter.httpx.post")
    def test_tool_call_id_from_first_call(self, mock_post: MagicMock) -> None:
        tc = [{
            "id": "call_abc",
            "type": "function",
            "function": {"name": "emit_intent", "arguments": "{}"},
        }]
        body = _success_response(tool_calls=tc)
        mock_post.return_value = _mock_httpx_post(body)

        resp = _provider().generate("sys", [], {})
        assert resp.tool_call_id == "call_abc"

    @patch("ecospheric_harness.providers.openrouter.httpx.post")
    def test_no_tool_calls_text_response(self, mock_post: MagicMock) -> None:
        """Model returns plain text (no tool_calls)."""
        body = {
            "choices": [{
                "message": {"content": "Hello there!", "finish_reason": "stop"},
                "finish_reason": "stop",
            }],
            "usage": {"prompt_tokens": 50, "completion_tokens": 10},
        }
        mock_post.return_value = _mock_httpx_post(body)

        resp = _provider().generate("sys", [], {})
        assert resp.tool_calls == []
        assert resp.finish_reason == "stop"


# ---------------------------------------------------------------------------
# generate() — error mapping
# ---------------------------------------------------------------------------


class TestGenerateErrors:
    @patch("ecospheric_harness.providers.openrouter.httpx.post")
    def test_429_rate_limit(self, mock_post: MagicMock) -> None:
        mock_post.return_value = _mock_httpx_post({}, status_code=429)

        with pytest.raises(ProviderError) as exc_info:
            _provider().generate("sys", [], {})
        assert exc_info.value.error_type == "rate_limit"
        assert exc_info.value.retryable is True

    @patch("ecospheric_harness.providers.openrouter.httpx.post")
    def test_401_auth(self, mock_post: MagicMock) -> None:
        mock_post.return_value = _mock_httpx_post({}, status_code=401)

        with pytest.raises(ProviderError) as exc_info:
            _provider().generate("sys", [], {})
        assert exc_info.value.error_type == "auth"
        assert exc_info.value.retryable is False

    @patch("ecospheric_harness.providers.openrouter.httpx.post")
    def test_400_parse_failure(self, mock_post: MagicMock) -> None:
        mock_post.return_value = _mock_httpx_post({}, status_code=400)

        with pytest.raises(ProviderError) as exc_info:
            _provider().generate("sys", [], {})
        assert exc_info.value.error_type == "parse_failure"
        assert exc_info.value.retryable is False

    @patch("ecospheric_harness.providers.openrouter.httpx.post")
    def test_timeout(self, mock_post: MagicMock) -> None:
        mock_post.side_effect = httpx.TimeoutException("timed out")

        with pytest.raises(ProviderError) as exc_info:
            _provider().generate("sys", [], {})
        assert exc_info.value.error_type == "timeout"
        assert exc_info.value.retryable is True

    @patch("ecospheric_harness.providers.openrouter.httpx.post")
    def test_unknown_status(self, mock_post: MagicMock) -> None:
        mock_post.return_value = _mock_httpx_post({}, status_code=500)

        with pytest.raises(ProviderError) as exc_info:
            _provider().generate("sys", [], {})
        assert exc_info.value.error_type == "unknown"
        assert exc_info.value.retryable is False


# ---------------------------------------------------------------------------
# generate() — payload construction
# ---------------------------------------------------------------------------


class TestGeneratePayload:
    @patch("ecospheric_harness.providers.openrouter.httpx.post")
    def test_payload_model_and_messages(self, mock_post: MagicMock) -> None:
        body = _success_response()
        mock_post.return_value = _mock_httpx_post(body)

        messages = [{"role": "user", "content": "clip it"}]
        tool_def = {"type": "function", "function": {"name": "emit_intent"}}

        _provider().generate("system text", messages, tool_def)

        call_kwargs = mock_post.call_args.kwargs
        payload = call_kwargs["json"]
        assert payload["model"] == _MODEL
        assert payload["messages"][0] == {"role": "system", "content": "system text"}
        assert payload["messages"][1] == {"role": "user", "content": "clip it"}
        assert payload["tools"] == [tool_def]
        assert payload["parallel_tool_calls"] is False

    @patch("ecospheric_harness.providers.openrouter.httpx.post")
    def test_url_is_openrouter(self, mock_post: MagicMock) -> None:
        body = _success_response()
        mock_post.return_value = _mock_httpx_post(body)

        _provider().generate("sys", [], {})

        url = mock_post.call_args.args[0] if mock_post.call_args.args else mock_post.call_args.kwargs.get("url")
        assert url == "https://openrouter.ai/api/v1/chat/completions"

    @patch("ecospheric_harness.providers.openrouter.httpx.post")
    def test_auth_header(self, mock_post: MagicMock) -> None:
        body = _success_response()
        mock_post.return_value = _mock_httpx_post(body)

        _provider().generate("sys", [], {})

        headers = mock_post.call_args.kwargs.get("headers", {})
        assert headers.get("Authorization") == f"Bearer {_API_KEY}"

    @patch("ecospheric_harness.providers.openrouter.httpx.post")
    def test_timeout_param(self, mock_post: MagicMock) -> None:
        body = _success_response()
        mock_post.return_value = _mock_httpx_post(body)

        _provider(timeout=30.0).generate("sys", [], {})

        assert mock_post.call_args.kwargs.get("timeout") == 30.0


# ---------------------------------------------------------------------------
# stream() — success
# ---------------------------------------------------------------------------


class TestStreamSuccess:
    @patch("ecospheric_harness.providers.openrouter.httpx.Client")
    def test_yields_stream_chunks(self, mock_client_cls: MagicMock) -> None:
        sse_lines = [
            'data: {"choices":[{"delta":{"content":"Hello"}}]}',
            'data: {"choices":[{"delta":{"content":" world"}}]}',
            'data: {"choices":[{"delta":{},"finish_reason":"stop"}]}',
            "data: [DONE]",
        ]

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.raise_for_status = MagicMock()
        mock_resp.iter_lines.return_value = iter(sse_lines)

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

    @patch("ecospheric_harness.providers.openrouter.httpx.Client")
    def test_stream_finish_reason(self, mock_client_cls: MagicMock) -> None:
        sse_lines = [
            'data: {"choices":[{"delta":{"content":"ok"},"finish_reason":null}]}',
            'data: {"choices":[{"delta":{},"finish_reason":"stop"}]}',
            "data: [DONE]",
        ]

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.raise_for_status = MagicMock()
        mock_resp.iter_lines.return_value = iter(sse_lines)

        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.stream.return_value = mock_resp
        mock_client_cls.return_value = mock_client

        chunks = list(_provider().stream("sys", [], {}))

        last_chunk = chunks[-1]
        assert last_chunk.finish_reason == "stop"

    @patch("ecospheric_harness.providers.openrouter.httpx.Client")
    def test_stream_tool_call_delta(self, mock_client_cls: MagicMock) -> None:
        sse_lines = [
            'data: {"choices":[{"delta":{"tool_calls":[{"index":0,"function":{"name":"emit_intent"}}]}}]}',
            'data: {"choices":[{"delta":{"tool_calls":[{"index":0,"function":{"arguments":"{\\\\"}}]}}]}',
            'data: {"choices":[{"delta":{},"finish_reason":"tool_calls"}]}',
            "data: [DONE]",
        ]

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.raise_for_status = MagicMock()
        mock_resp.iter_lines.return_value = iter(sse_lines)

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
    @patch("ecospheric_harness.providers.openrouter.httpx.Client")
    def test_stream_401_raises_provider_error(self, mock_client_cls: MagicMock) -> None:
        mock_resp = MagicMock()
        mock_resp.status_code = 401
        mock_resp.raise_for_status.side_effect = httpx.HTTPStatusError(
            "401", request=MagicMock(), response=MagicMock(status_code=401),
        )

        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.stream.return_value = mock_resp
        mock_client_cls.return_value = mock_client

        with pytest.raises(ProviderError) as exc_info:
            list(_provider().stream("sys", [], {}))
        assert exc_info.value.error_type == "auth"

    @patch("ecospheric_harness.providers.openrouter.httpx.Client")
    def test_stream_timeout_raises_provider_error(self, mock_client_cls: MagicMock) -> None:
        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.stream.side_effect = httpx.TimeoutException("timeout")
        mock_client_cls.return_value = mock_client

        with pytest.raises(ProviderError) as exc_info:
            list(_provider().stream("sys", [], {}))
        assert exc_info.value.error_type == "timeout"
        assert exc_info.value.retryable is True
