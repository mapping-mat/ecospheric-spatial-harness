"""Ollama local model provider."""

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


class OllamaProvider:
    """Model provider backed by a local Ollama instance."""

    def __init__(
        self,
        host: str = "http://localhost:11434",
        model: str = "llama3.2",
        timeout: float = 60.0,
    ) -> None:
        self._host = host.rstrip("/")
        self._model = model
        self._timeout = timeout

    # -- generate ----------------------------------------------------------

    def generate(
        self,
        system_prompt: str,
        messages: list[dict[str, Any]],
        tool_def: dict[str, Any],
    ) -> ModelResponse:
        """Send a non-streaming completion to Ollama /api/chat."""
        payload: dict[str, Any] = {
            "model": self._model,
            "messages": [
                {"role": "system", "content": system_prompt},
                *messages,
            ],
            "tools": [tool_def],
            "stream": False,
        }
        url = f"{self._host}/api/chat"
        try:
            resp = httpx.post(url, json=payload, timeout=self._timeout)
        except httpx.TimeoutException as exc:
            raise ProviderError("timeout", error_type="timeout", retryable=True) from exc
        except httpx.ConnectError as exc:
            raise ProviderError(str(exc), error_type="unknown", retryable=True) from exc
        except httpx.HTTPError as exc:
            raise ProviderError(str(exc), error_type="unknown", retryable=False) from exc

        if resp.status_code >= 400:
            raise ProviderError(resp.text[:500], error_type="unknown", retryable=False)

        body: dict[str, Any] = resp.json()
        message: dict[str, Any] = body.get("message", {})

        # Normalise tool_calls: Ollama sends arguments as dict, OpenAI as
        # JSON string.  Add id + type fields.
        raw_calls: list[dict[str, Any]] = message.get("tool_calls") or []
        tool_calls: list[dict[str, Any]] = []
        tool_call_id = ""
        for idx, call in enumerate(raw_calls):
            fn = call.get("function") or {}
            arguments = fn.get("arguments")
            if isinstance(arguments, dict):
                arguments = json.dumps(arguments)
            elif arguments is None:
                arguments = "{}"
            normed: dict[str, Any] = dict(call)  # shallow copy
            normed.setdefault("id", f"call_{idx}")
            normed["type"] = "function"
            normed["function"] = {**fn, "arguments": arguments}
            tool_calls.append(normed)
            if idx == 0:
                tool_call_id = normed["id"]

        input_tokens = body.get("prompt_eval_count", 0)
        output_tokens = body.get("eval_count", 0)
        usage = TokenUsage(
            input_tokens=input_tokens,
            output_tokens=output_tokens,
        )

        finish_reason = "tool_calls" if tool_calls else "stop"

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
        """Stream completion chunks from Ollama /api/chat."""
        payload: dict[str, Any] = {
            "model": self._model,
            "messages": [
                {"role": "system", "content": system_prompt},
                *messages,
            ],
            "tools": [tool_def],
            "stream": True,
        }
        url = f"{self._host}/api/chat"
        try:
            with httpx.Client(timeout=self._timeout) as client:
                resp = client.stream("POST", url, json=payload)
                if resp.status_code >= 400:
                    raise ProviderError(
                        resp.text[:500],
                        error_type="unknown",
                        retryable=False,
                    )
                for line in resp.iter_lines():
                    try:
                        chunk: dict[str, Any] = json.loads(line)
                    except json.JSONDecodeError:
                        continue

                    msg = chunk.get("message", {})
                    delta = msg.get("content", "") or ""

                    tool_call_delta = None
                    tc_list = msg.get("tool_calls") or []
                    if tc_list:
                        tool_call_delta = tc_list[0].get("function")

                    done = chunk.get("done", False)
                    finish_reason = "stop" if done else None

                    yield StreamChunk(
                        delta=delta,
                        tool_call_delta=tool_call_delta,
                        finish_reason=finish_reason,
                    )

                    if finish_reason is not None:
                        return
        except httpx.TimeoutException as exc:
            raise ProviderError("timeout", error_type="timeout", retryable=True) from exc
        except httpx.ConnectError as exc:
            raise ProviderError(str(exc), error_type="unknown", retryable=True) from exc
        except httpx.HTTPError as exc:
            raise ProviderError(str(exc), error_type="unknown", retryable=False) from exc