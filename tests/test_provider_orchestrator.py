"""Tests for Orchestrator provider delegation (Phase 1.5 provider abstraction).

Verifies that the Orchestrator accepts a ModelProvider and delegates
_call_model() to provider.generate(), converting ModelResponse back
to the dict format the existing loop expects.
"""

from __future__ import annotations

import json
import pytest
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

from ecospheric_harness.artifact_registry import ArtifactRegistry
from ecospheric_harness.config import HarnessConfig
from ecospheric_harness.corrections import CorrectionHandler
from ecospheric_harness.executor import ToolExecutor
from ecospheric_harness.intents import (
    IntentEntry,
    IntentOption,
    RegisteredTool,
    ResolvedCall,
)
from ecospheric_harness.orchestrator import Orchestrator
from ecospheric_harness.preflight import PreflightChecker
from ecospheric_harness.registry import ToolRegistry
from ecospheric_harness.resolver import IntentResolver
from ecospheric_harness.result import PipelineResult
from ecospheric_harness.validator import SchemaValidator, ValidationResult
from ecospheric_harness.workspace import WorkspaceManager

from ecospheric_harness.providers.base import (
    ModelProvider,
    ModelResponse,
    ProviderError,
    TokenUsage,
)

from etp.describe import CommandDescriptor, ParameterDescriptor


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_model_response_dict(intent: str, call_id: str = "call_1", **extra: Any) -> dict[str, Any]:
    """Build the dict format the orchestrator expects from _call_model()."""
    args: dict[str, Any] = {"intent": intent, **extra}
    return {
        "tool_calls": [{
            "id": call_id,
            "type": "function",
            "function": {
                "name": "emit_intent",
                "arguments": json.dumps(args),
            },
        }],
    }


def _make_provider_model_response(intent: str, call_id: str = "call_1", **extra: Any) -> ModelResponse:
    """Build a ModelResponse that the provider abstraction should produce."""
    args: dict[str, Any] = {"intent": intent, **extra}
    return ModelResponse(
        tool_calls=[{
            "id": call_id,
            "type": "function",
            "function": {
                "name": "emit_intent",
                "arguments": json.dumps(args),
            },
        }],
        tool_call_id=call_id,
        usage=TokenUsage(input_tokens=100, output_tokens=20),
        finish_reason="tool_calls",
    )


def _make_mock_orchestrator_with_provider(
    tmp_path: Path,
    provider: ModelProvider | None = None,
    *,
    max_turns: int = 20,
    preflight_ok: bool = True,
    validation_ok: bool = True,
    executor_succeed: bool = True,
) -> tuple[Orchestrator, MagicMock]:
    """Build an Orchestrator with a mock provider (or default httpx mock)."""
    config = HarnessConfig(
        model="test-model",
        max_turns=max_turns,
        search_cap=20,
        workspace_root=tmp_path,
    )
    registry = MagicMock(spec=ToolRegistry)
    resolver = MagicMock(spec=IntentResolver)
    resolver.command_needs_input.return_value = False
    validator = MagicMock(spec=SchemaValidator)
    executor = MagicMock(spec=ToolExecutor)
    ws = WorkspaceManager(tmp_path, disk_limit_bytes=10_000_000)
    artifact_registry = ArtifactRegistry(workspace=ws, disk_limit_bytes=10_000_000)
    preflight = MagicMock(spec=PreflightChecker)
    corrections = MagicMock(spec=CorrectionHandler)

    cmd = CommandDescriptor(
        name="raster clip",
        description="Clip raster",
        category="raster",
        parameters=[ParameterDescriptor(name="input", description="input", type="string", required=False)],
    )
    tool = RegisteredTool(name="ese", version="0.5.0", binary="ese", commands=[cmd])
    catalog = [IntentEntry(
        intent="clip",
        description="Clip raster",
        tool=tool,
        command=cmd,
        required_params=[],
    )]

    resolver.resolve.return_value = ResolvedCall(tool=tool, command=cmd, params={})
    if validation_ok:
        validator.validate.return_value = ValidationResult(ok=True)
    else:
        validator.validate.return_value = ValidationResult(ok=False, errors=["bad"])

    if preflight_ok:
        preflight.check_planar_crs.return_value = MagicMock(ok=True)
        preflight.check_disk.return_value = MagicMock(ok=True)
    else:
        preflight.check_planar_crs.return_value = MagicMock(ok=False, error="requires planar CRS")
        preflight.check_disk.return_value = MagicMock(ok=True)

    envelope = {
        "status": "success",
        "data": {"format": "geotiff", "data_type": "raster"},
    }
    output_file = tmp_path / "output.bin"
    output_file.write_bytes(b"output")
    executor.execute.return_value = MagicMock(
        envelope=envelope,
        returncode=0,
        output_path=output_file,
    )

    kwargs: dict[str, Any] = dict(
        config=config,
        registry=registry,
        resolver=resolver,
        validator=validator,
        executor=executor,
        artifact_registry=artifact_registry,
        preflight=preflight,
        corrections=corrections,
        catalog=catalog,
        workspace=ws,
    )
    if provider is not None:
        kwargs["provider"] = provider

    orch = Orchestrator(**kwargs)
    return orch, resolver


# ---------------------------------------------------------------------------
# Orchestrator accepts provider parameter
# ---------------------------------------------------------------------------


class TestOrchestratorAcceptsProvider:
    def test_init_with_provider(self, tmp_path: Path) -> None:
        """Orchestrator.__init__ accepts a provider parameter."""
        mock_provider = MagicMock(spec=ModelProvider)
        orch, _ = _make_mock_orchestrator_with_provider(tmp_path, provider=mock_provider)
        assert orch is not None

    def test_init_without_provider(self, tmp_path: Path) -> None:
        """Orchestrator still works without a provider (backward compat)."""
        orch, _ = _make_mock_orchestrator_with_provider(tmp_path)
        assert orch is not None


# ---------------------------------------------------------------------------
# _call_model delegates to provider.generate()
# ---------------------------------------------------------------------------


class TestCallModelDelegation:
    @patch("ecospheric_harness.orchestrator.available_intents")
    def test_provider_generate_called(self, mock_menu: MagicMock, tmp_path: Path) -> None:
        """When provider is set, _call_model delegates to provider.generate()."""
        mock_provider = MagicMock(spec=ModelProvider)
        provider_resp = _make_provider_model_response("complete", summary="done")
        mock_provider.generate.return_value = provider_resp

        orch, _ = _make_mock_orchestrator_with_provider(tmp_path, provider=mock_provider)

        mock_menu.return_value = [IntentOption(
            intent="clip", description="Clip raster", required_params=[],
        )]

        result = orch.run("test prompt")

        assert mock_provider.generate.call_count >= 1
        assert isinstance(result, PipelineResult)

    @patch("ecospheric_harness.orchestrator.available_intents")
    def test_provider_receives_system_prompt_and_messages(
        self, mock_menu: MagicMock, tmp_path: Path,
    ) -> None:
        """provider.generate() receives system_prompt, messages, and tool_def."""
        mock_provider = MagicMock(spec=ModelProvider)
        provider_resp = _make_provider_model_response("complete", summary="done")
        mock_provider.generate.return_value = provider_resp

        orch, _ = _make_mock_orchestrator_with_provider(tmp_path, provider=mock_provider)

        mock_menu.return_value = [IntentOption(
            intent="clip", description="Clip raster", required_params=[],
        )]

        orch.run("test prompt")

        call_args = mock_provider.generate.call_args
        # First positional arg is system_prompt (str containing rules)
        system_prompt = call_args[0][0]
        assert isinstance(system_prompt, str)
        assert "emit_intent" in system_prompt

    @patch("ecospheric_harness.orchestrator.available_intents")
    def test_model_response_converted_to_dict(
        self, mock_menu: MagicMock, tmp_path: Path,
    ) -> None:
        """ModelResponse from provider is converted to dict format with tool_calls key."""
        mock_provider = MagicMock(spec=ModelProvider)

        # First call: clip. Second call: complete.
        clip_resp = _make_provider_model_response("clip")
        complete_resp = _make_provider_model_response("complete", summary="done")
        mock_provider.generate.side_effect = [clip_resp, complete_resp]

        orch, _ = _make_mock_orchestrator_with_provider(tmp_path, provider=mock_provider)

        mock_menu.return_value = [IntentOption(
            intent="clip", description="Clip raster", required_params=[],
        )]

        result = orch.run("test")

        assert isinstance(result, PipelineResult)
        assert len(result.steps) == 1
        assert result.steps[0].intent == "clip"


# ---------------------------------------------------------------------------
# ProviderError propagation
# ---------------------------------------------------------------------------


class TestProviderErrorPropagation:
    @patch("ecospheric_harness.orchestrator.available_intents")
    def test_non_retryable_error_propagates(
        self, mock_menu: MagicMock, tmp_path: Path,
    ) -> None:
        """ProviderError with retryable=False propagates (crashes the loop)."""
        mock_provider = MagicMock(spec=ModelProvider)
        mock_provider.generate.side_effect = ProviderError(
            "Invalid API key",
            error_type="auth",
            retryable=False,
        )

        orch, _ = _make_mock_orchestrator_with_provider(tmp_path, provider=mock_provider)

        mock_menu.return_value = [IntentOption(
            intent="clip", description="Clip raster", required_params=[],
        )]

        with pytest.raises(ProviderError) as exc_info:
            orch.run("test")
        assert exc_info.value.error_type == "auth"
        assert exc_info.value.retryable is False

    @patch("ecospheric_harness.orchestrator.available_intents")
    def test_retryable_error_can_be_caught(
        self, mock_menu: MagicMock, tmp_path: Path,
    ) -> None:
        """ProviderError with retryable=True can be caught by caller."""
        mock_provider = MagicMock(spec=ModelProvider)
        mock_provider.generate.side_effect = ProviderError(
            "Rate limited",
            error_type="rate_limit",
            retryable=True,
            retry_after=30.0,
        )

        orch, _ = _make_mock_orchestrator_with_provider(tmp_path, provider=mock_provider)

        mock_menu.return_value = [IntentOption(
            intent="clip", description="Clip raster", required_params=[],
        )]

        with pytest.raises(ProviderError) as exc_info:
            orch.run("test")
        assert exc_info.value.error_type == "rate_limit"
        assert exc_info.value.retry_after == 30.0


# ---------------------------------------------------------------------------
# Multi-step with provider
# ---------------------------------------------------------------------------


class TestMultiStepWithProvider:
    @patch("ecospheric_harness.orchestrator.available_intents")
    def test_two_step_pipeline_via_provider(
        self, mock_menu: MagicMock, tmp_path: Path,
    ) -> None:
        """Two-step pipeline works through provider delegation."""
        mock_provider = MagicMock(spec=ModelProvider)
        mock_provider.generate.side_effect = [
            _make_provider_model_response("clip"),
            _make_provider_model_response("clip"),
            _make_provider_model_response("complete", summary="done"),
        ]

        orch, _ = _make_mock_orchestrator_with_provider(tmp_path, provider=mock_provider)

        mock_menu.return_value = [IntentOption(
            intent="clip", description="Clip raster", required_params=[],
        )]

        result = orch.run("two clips")

        assert len(result.steps) == 2
        assert mock_provider.generate.call_count == 3
