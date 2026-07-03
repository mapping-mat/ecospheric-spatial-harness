"""Base Protocol and dataclass types for model providers."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterator, Protocol, runtime_checkable


@dataclass
class TokenUsage:
    """Token usage statistics for a model response."""

    input_tokens: int = 0
    output_tokens: int = 0


@dataclass
class ModelResponse:
    """Normalised response from a model provider's generate() call."""

    tool_calls: list[dict] = field(default_factory=list)
    tool_call_id: str = ""
    usage: TokenUsage = field(default_factory=TokenUsage)
    finish_reason: str = ""


@dataclass
class StreamChunk:
    """A single chunk from a model provider's stream() call."""

    delta: str = ""
    tool_call_delta: dict | None = None
    finish_reason: str | None = None


class ProviderError(Exception):
    """Exception raised by model providers for request-level failures.

    Attributes:
        error_type: A machine-readable error category
            (e.g. "timeout", "rate_limit", "auth", "parse_failure", "unknown").
        retryable: Whether the caller may retry the same request.
        retry_after: If retryable and known, the suggested wait in seconds.
    """

    def __init__(
        self,
        message: str,
        *,
        error_type: str,
        retryable: bool,
        retry_after: float | None = None,
    ) -> None:
        super().__init__(message)
        self.error_type: str = error_type
        self.retryable: bool = retryable
        self.retry_after: float | None = retry_after


@runtime_checkable
class ModelProvider(Protocol):
    """Protocol that every model provider must implement."""

    def generate(
        self,
        system_prompt: str,
        messages: list[dict],
        tool_def: dict,
    ) -> ModelResponse:
        """Send a single non-streaming completion request."""
        ...

    def stream(
        self,
        system_prompt: str,
        messages: list[dict],
        tool_def: dict,
    ) -> Iterator[StreamChunk]:
        """Send a streaming completion request, yielding chunks."""
        ...