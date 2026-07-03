"""Tests for ecospheric_harness.providers.base — data types and Protocol."""

from __future__ import annotations

from typing import Iterator
from unittest.mock import MagicMock

import pytest

from ecospheric_harness.providers.base import (
    ModelProvider,
    ModelResponse,
    ProviderError,
    StreamChunk,
    TokenUsage,
)


# ---------------------------------------------------------------------------
# TokenUsage
# ---------------------------------------------------------------------------


class TestTokenUsage:
    def test_construction(self) -> None:
        tu = TokenUsage(input_tokens=100, output_tokens=50)
        assert tu.input_tokens == 100
        assert tu.output_tokens == 50

    def test_zero_counts(self) -> None:
        tu = TokenUsage(input_tokens=0, output_tokens=0)
        assert tu.input_tokens == 0
        assert tu.output_tokens == 0

    def test_large_counts(self) -> None:
        tu = TokenUsage(input_tokens=1_000_000, output_tokens=500_000)
        assert tu.input_tokens == 1_000_000
        assert tu.output_tokens == 500_000


# ---------------------------------------------------------------------------
# ModelResponse
# ---------------------------------------------------------------------------


class TestModelResponse:
    def test_construction(self) -> None:
        usage = TokenUsage(input_tokens=10, output_tokens=5)
        resp = ModelResponse(
            tool_calls=[{"id": "c1", "function": {"name": "fn"}}],
            tool_call_id="c1",
            usage=usage,
            finish_reason="stop",
        )
        assert resp.tool_calls[0]["id"] == "c1"
        assert resp.tool_call_id == "c1"
        assert resp.usage.input_tokens == 10
        assert resp.finish_reason == "stop"

    def test_empty_tool_calls(self) -> None:
        usage = TokenUsage(input_tokens=0, output_tokens=0)
        resp = ModelResponse(
            tool_calls=[],
            tool_call_id="",
            usage=usage,
            finish_reason="stop",
        )
        assert resp.tool_calls == []

    def test_multiple_tool_calls(self) -> None:
        usage = TokenUsage(input_tokens=20, output_tokens=10)
        calls = [{"id": "c1"}, {"id": "c2"}, {"id": "c3"}]
        resp = ModelResponse(
            tool_calls=calls,
            tool_call_id="c1",
            usage=usage,
            finish_reason="tool_calls",
        )
        assert len(resp.tool_calls) == 3


# ---------------------------------------------------------------------------
# StreamChunk
# ---------------------------------------------------------------------------


class TestStreamChunk:
    def test_construction_text(self) -> None:
        chunk = StreamChunk(delta="hello", tool_call_delta=None, finish_reason=None)
        assert chunk.delta == "hello"
        assert chunk.tool_call_delta is None
        assert chunk.finish_reason is None

    def test_construction_tool_call(self) -> None:
        tc_delta = {"function": {"arguments": " partial"}}
        chunk = StreamChunk(delta="", tool_call_delta=tc_delta, finish_reason=None)
        assert chunk.tool_call_delta == tc_delta
        assert chunk.delta == ""

    def test_construction_finish(self) -> None:
        chunk = StreamChunk(delta="", tool_call_delta=None, finish_reason="stop")
        assert chunk.finish_reason == "stop"


# ---------------------------------------------------------------------------
# ProviderError
# ---------------------------------------------------------------------------


class TestProviderError:
    def test_attributes(self) -> None:
        err = ProviderError(
            "rate limited",
            error_type="rate_limit",
            retryable=True,
            retry_after=30.0,
        )
        assert str(err) == "rate limited"
        assert err.error_type == "rate_limit"
        assert err.retryable is True
        assert err.retry_after == 30.0

    def test_non_retryable(self) -> None:
        err = ProviderError(
            "invalid key",
            error_type="auth",
            retryable=False,
            retry_after=None,
        )
        assert err.retryable is False
        assert err.retry_after is None

    def test_is_exception(self) -> None:
        err = ProviderError("boom", error_type="unknown", retryable=False)
        assert isinstance(err, Exception)

    def test_error_types_valid(self) -> None:
        """All documented error_type values can be constructed."""
        for etype in ("rate_limit", "context_length", "parse_failure", "timeout", "auth", "unknown"):
            err = ProviderError("msg", error_type=etype, retryable=False)
            assert err.error_type == etype


# ---------------------------------------------------------------------------
# ModelProvider Protocol
# ---------------------------------------------------------------------------


class TestModelProviderProtocol:
    def test_is_runtime_checkable(self) -> None:
        """ModelProvider Protocol supports isinstance() checks."""
        mock_provider = MagicMock()
        mock_provider.generate = MagicMock()
        mock_provider.stream = MagicMock()
        assert isinstance(mock_provider, ModelProvider)

    def test_satisfies_protocol_with_class(self) -> None:
        """A class implementing generate() and stream() satisfies the Protocol."""

        class FakeProvider:
            def generate(
                self,
                system_prompt: str,
                messages: list[dict],
                tool_def: dict,
            ) -> ModelResponse:
                ...  # pragma: no cover

            def stream(
                self,
                system_prompt: str,
                messages: list[dict],
                tool_def: dict,
            ) -> Iterator[StreamChunk]:
                ...  # pragma: no cover

        assert isinstance(FakeProvider(), ModelProvider)

    def test_missing_generate_fails_check(self) -> None:
        """A class without generate() does NOT satisfy the Protocol."""

        class IncompleteProvider:
            def stream(
                self,
                system_prompt: str,
                messages: list[dict],
                tool_def: dict,
            ) -> Iterator[StreamChunk]:
                ...  # pragma: no cover

        assert not isinstance(IncompleteProvider(), ModelProvider)

    def test_missing_stream_fails_check(self) -> None:
        """A class without stream() does NOT satisfy the Protocol."""

        class IncompleteProvider:
            def generate(
                self,
                system_prompt: str,
                messages: list[dict],
                tool_def: dict,
            ) -> ModelResponse:
                ...  # pragma: no cover

        assert not isinstance(IncompleteProvider(), ModelProvider)

    def test_plain_object_fails_check(self) -> None:
        """A plain object does NOT satisfy the Protocol."""
        assert not isinstance(object(), ModelProvider)
