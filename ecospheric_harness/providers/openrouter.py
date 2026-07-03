"""OpenRouter API model provider."""

from __future__ import annotations

import json
from typing import Any, Iterator

import httpx

from ecospheric_harness.providers.base import (
    ModelResponse,
    ProviderError,
    StreamChunk,
    TokenUsage,
)

_OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"


class OpenRouterProvider:
    """Model provider backed by the OpenRouter API (OpenAI-compatible)."""

    def __init__(
        self,
        api_key: str,
        model: str,
        timeout: float = 60.0,
    ) -> None:
        self._api_key = api_key
        self._model = model
        self._timeout = timeout

    # -- generate ----------------------------------------------------------

    def generate(
        self,
        system_prompt: str,
        messages: list[dict[str, Any]],
        tool_def: dict[str, Any],
    ) -> ModelResponse:
        """Send a non-streaming completion to OpenRouter."""
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }
        payload: dict[str, Any] = {
            "model": self._model,
            "messages": [
                {"role": "system", "content": system_prompt},
                *messages,
            ],
            "tools": [tool_def],
            "parallel_tool_calls": False,
        }
        try:
            resp = httpx.post(
                _OPENROUTER_URL,
                headers=headers,
                json=payload,
                timeout=self._timeout,
            )
        except httpx.TimeoutException as exc:
            raise ProviderError("timeout", error_type="timeout", retryable=True) from exc
        except httpx.HTTPError as exc:
            raise ProviderError(str(exc), error_type="unknown", retryable=False) from exc

        if resp.status_code == 429:
            retry_after: float | None = None
            v = resp.headers.get("Retry-After")
            if v is not None:
                try:
                    retry_after = float(v)
                except ValueError:
                    pass
            raise ProviderError("rate_limit", error_type="rate_limit", retryable=True, retry_after=retry_after)

        if resp.status_code == 401:
            raise ProviderError("auth", error_type="auth", retryable=False)

        if resp.status_code == 400:
            raise ProviderError(resp.text[:500], error_type="parse_failure", retryable=False)

        if resp.status_code >= 400:
            raise ProviderError(resp.text[:500], error_type="unknown", retryable=False)

        body: dict[str, Any] = resp.json()
        message: dict[str, Any] = body["choices"][0]["message"]

        tool_calls: list[dict[str, Any]] = message.get("tool_calls") or []
        tool_call_id = ""
        if tool_calls:
            tool_call_id = tool_calls[0].get("id", "")

        usage_raw = body.get("usage", {})
        usage = TokenUsage(
            input_tokens=usage_raw.get("prompt_tokens", 0),
            output_tokens=usage_raw.get("completion_tokens", 0),
        )
        finish_reason = body["choices"][0].get("finish_reason", "")

        return ModelResponse(
            tool_calls=tool_calls,
            tool_call_id=tool_call_id,
            usage=usage,
            finish_reason=finish_reason,
        )

    # -- stream ------------------------------------------------------------

    def stream(
        self,
        system_prompt: str,
        messages: list[dict[str, Any]],
        tool_def: dict[str, Any],
    ) -> Iterator[StreamChunk]:
        """Stream completion chunks from OpenRouter."""
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }
        payload: dict[str, Any] = {
            "model": self._model,
            "messages": [
                {"role": "system", "content": system_prompt},
                *messages,
            ],
            "tools": [tool_def],
            "parallel_tool_calls": False,
            "stream": True,
        }
        try:
            with httpx.Client(timeout=self._timeout) as client:
                resp = client.stream(
                    "POST", _OPENROUTER_URL, headers=headers, json=payload,
                )

                # Status-specific error handling
                if resp.status_code == 401:
                    raise ProviderError("auth", error_type="auth", retryable=False)
                if resp.status_code == 429:
                    raise ProviderError(
                        "rate_limit", error_type="rate_limit", retryable=True,
                    )
                if resp.status_code >= 400:
                    raise ProviderError(
                        resp.text[:500],
                        error_type="unknown",
                        retryable=False,
                    )
                for line in resp.iter_lines():
                    if not line.startswith("data: "):
                        continue
                    data_text = line[6:]  # strip "data: " prefix
                    if data_text == "[DONE]":
                        return
                    try:
                        chunk: dict[str, Any] = json.loads(data_text)
                    except json.JSONDecodeError:
                        continue
                    delta = (
                        chunk.get("choices", [{}])[0]
                        .get("delta", {})
                    )
                    content = delta.get("content", "") or ""
                    tool_call_delta = None
                    tc_list = delta.get("tool_calls") or []
                    if tc_list:
                        fn = tc_list[0].get("function") or {}
                        tool_call_delta = fn.get("arguments")

                    finish_reason = (
                        chunk.get("choices", [{}])[0].get("finish_reason")
                    )

                    yield StreamChunk(
                        delta=content,
                        tool_call_delta=tool_call_delta,
                        finish_reason=finish_reason,
                    )

                    if finish_reason is not None:
                        return
        except httpx.TimeoutException as exc:
            raise ProviderError("timeout", error_type="timeout", retryable=True) from exc
        except httpx.HTTPError as exc:
            raise ProviderError(str(exc), error_type="unknown", retryable=False) from exc