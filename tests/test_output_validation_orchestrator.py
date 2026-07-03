"""Tests for orchestrator output validation integration (Phase 2.2).

These tests verify that the orchestrator calls output validation
after successful execution and handles failures correctly.

Key behaviors tested:
1. Successful execution + successful validation → normal flow (step status="success")
2. Successful execution + failed validation → step status="validation_failed"
3. Failed validation → orphan file cleaned up via workspace.cleanup_unregistered()
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from etp.describe import CommandDescriptor, ParameterDescriptor

from ecospheric_harness.artifact_registry import ArtifactRegistry
from ecospheric_harness.config import HarnessConfig
from ecospheric_harness.corrections import CorrectionHandler
from ecospheric_harness.executor import ToolExecutor
from ecospheric_harness.intents import (
    IntentEntry,
    IntentOption,
    PreflightResult,
    RegisteredTool,
    ResolvedCall,
    Resolution,
    ResolutionError,
)
from ecospheric_harness.orchestrator import Orchestrator
from ecospheric_harness.output_validator import OutputValidationResult
from ecospheric_harness.preflight import PreflightChecker
from ecospheric_harness.registry import ToolRegistry
from ecospheric_harness.resolver import IntentResolver
from ecospheric_harness.result import StepRecord
from ecospheric_harness.validator import SchemaValidator, ValidationResult
from ecospheric_harness.workspace import WorkspaceManager


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _make_mock_orchestrator(
    tmp_path: Path,
    *,
    executor_envelope: dict[str, Any] | None = None,
    validation_ok: bool = True,
    validation_error: str = "Output validation failed",
) -> tuple[Orchestrator, MagicMock, MagicMock, WorkspaceManager]:
    """Build an Orchestrator with mocked dependencies for output validation tests."""
    config = HarnessConfig(
        model="test-model",
        max_turns=5,
        search_cap=20,
        workspace_root=tmp_path,
    )
    registry = MagicMock(spec=ToolRegistry)
    resolver = MagicMock(spec=IntentResolver)
    resolver.command_needs_input.return_value = False
    validator = MagicMock(spec=SchemaValidator)
    validator.validate.return_value = ValidationResult(ok=True)
    executor = MagicMock(spec=ToolExecutor)
    ws = WorkspaceManager(tmp_path, disk_limit_bytes=10_000_000)
    artifact_registry = ArtifactRegistry(workspace=ws, disk_limit_bytes=10_000_000)
    preflight = MagicMock(spec=PreflightChecker)
    corrections = MagicMock(spec=CorrectionHandler)

    cmd = CommandDescriptor(
        name="raster clip",
        description="Clip raster",
        category="raster",
        parameters=[
            ParameterDescriptor(
                name="input", description="input", type="string", required=False,
            ),
        ],
    )
    tool = RegisteredTool(name="ese", version="0.5.0", binary="ese", commands=[cmd])
    catalog = [
        IntentEntry(
            intent="clip",
            description="Clip raster",
            tool=tool,
            command=cmd,
            required_params=[],
        ),
    ]

    resolver.resolve.return_value = ResolvedCall(tool=tool, command=cmd, params={})
    preflight.check_planar_crs.return_value = MagicMock(ok=True)
    preflight.check_disk.return_value = MagicMock(ok=True)
    preflight.run_all_checks.return_value = []

    # Executor returns a success envelope with an output file
    if executor_envelope is None:
        executor_envelope = {
            "status": "success",
            "data": {
                "format": "geotiff",
                "data_type": "raster",
                "width": 100,
                "height": 100,
                "crs": "EPSG:4326",
            },
        }
    # Put the output file inside the workspace session dir so cleanup_unregistered works
    output_file = ws.session_dir / "output.bin"
    output_file.write_bytes(b"\x00" * 100)
    executor.execute.return_value = MagicMock(
        envelope=executor_envelope,
        returncode=0,
        output_path=output_file,
    )

    # Output validator
    output_validator = MagicMock()
    output_validator.validate.return_value = OutputValidationResult(
        ok=validation_ok,
        checks=[{"check": "file_exists", "passed": True, "message": ""}],
        error="" if validation_ok else validation_error,
    )

    orch = Orchestrator(
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
        output_validator=output_validator,
    )

    return orch, output_validator, executor, ws


def _make_model_response(intent: str, **extra: Any) -> dict[str, Any]:
    """Build a mock model response with an emit_intent tool call."""
    args: dict[str, Any] = {"intent": intent, **extra}
    return {
        "tool_calls": [
            {
                "id": "call_1",
                "type": "function",
                "function": {
                    "name": "emit_intent",
                    "arguments": json.dumps(args),
                },
            }
        ],
    }


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestOrchestratorOutputValidation:
    @patch("ecospheric_harness.orchestrator.httpx")
    @patch("ecospheric_harness.orchestrator.available_intents")
    def test_successful_execution_and_validation(
        self, mock_menu: MagicMock, mock_httpx: MagicMock, tmp_path: Path,
    ) -> None:
        """When execution and validation both succeed, step status is 'success'."""
        orch, output_validator, executor, ws = _make_mock_orchestrator(
            tmp_path, validation_ok=True,
        )

        mock_menu.return_value = [
            IntentOption(intent="clip", description="Clip", required_params=[]),
        ]

        # First call: execute clip, second call: complete
        mock_httpx.post.return_value.json.return_value = {
            "choices": [
                {"message": _make_model_response("clip")},
            ],
        }

        # We need two model calls: first does "clip", second does "complete"
        call_count = 0

        def mock_call_model(system_prompt, tool_def, messages):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return _make_model_response("clip")
            return _make_model_response("complete", summary="done")

        with patch.object(orch, "_call_model", side_effect=mock_call_model):
            result = orch.run("clip the raster")

        # The clip step should have status "success"
        clip_steps = [s for s in result.steps if s.intent == "clip"]
        assert len(clip_steps) == 1
        assert clip_steps[0].status == "success"
        # Output validator was called
        output_validator.validate.assert_called_once()

    @patch("ecospheric_harness.orchestrator.httpx")
    @patch("ecospheric_harness.orchestrator.available_intents")
    def test_failed_validation_sets_validation_failed_status(
        self, mock_menu: MagicMock, mock_httpx: MagicMock, tmp_path: Path,
    ) -> None:
        """When validation fails, step status is 'validation_failed'."""
        orch, output_validator, executor, ws = _make_mock_orchestrator(
            tmp_path, validation_ok=False, validation_error="Raster has no CRS",
        )

        mock_menu.return_value = [
            IntentOption(intent="clip", description="Clip", required_params=[]),
        ]

        call_count = 0

        def mock_call_model(system_prompt, tool_def, messages):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return _make_model_response("clip")
            return _make_model_response("complete", summary="done")

        with patch.object(orch, "_call_model", side_effect=mock_call_model):
            result = orch.run("clip the raster")

        # The clip step should have status "validation_failed"
        clip_steps = [s for s in result.steps if s.intent == "clip"]
        assert len(clip_steps) == 1
        assert clip_steps[0].status == "validation_failed"
        # Output validator was called
        output_validator.validate.assert_called_once()

    @patch("ecospheric_harness.orchestrator.httpx")
    @patch("ecospheric_harness.orchestrator.available_intents")
    def test_failed_validation_cleans_up_orphan_file(
        self, mock_menu: MagicMock, mock_httpx: MagicMock, tmp_path: Path,
    ) -> None:
        """When validation fails, the orphan output file is cleaned up."""
        orch, output_validator, executor, ws = _make_mock_orchestrator(
            tmp_path, validation_ok=False, validation_error="Raster has no CRS",
        )

        # Track the output file path
        output_file = executor.execute.return_value.output_path
        assert output_file.exists()  # should exist before execution

        mock_menu.return_value = [
            IntentOption(intent="clip", description="Clip", required_params=[]),
        ]

        call_count = 0

        def mock_call_model(system_prompt, tool_def, messages):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return _make_model_response("clip")
            return _make_model_response("complete", summary="done")

        with patch.object(orch, "_call_model", side_effect=mock_call_model):
            result = orch.run("clip the raster")

        # The step should be validation_failed
        clip_steps = [s for s in result.steps if s.intent == "clip"]
        assert clip_steps[0].status == "validation_failed"
        # The orphan file should have been cleaned up
        assert not output_file.exists()
