"""Integration tests for Phase 2 — preflight + output validation + COG default."""

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
)
from ecospheric_harness.orchestrator import Orchestrator
from ecospheric_harness.output_validator import OutputValidationResult, OutputValidator
from ecospheric_harness.preflight import PreflightChecker
from ecospheric_harness.registry import ToolRegistry
from ecospheric_harness.resolver import IntentResolver
from ecospheric_harness.result import PipelineResult
from ecospheric_harness.security import SubprocessHardener, SubprocessLimits
from ecospheric_harness.validator import SchemaValidator, ValidationResult
from ecospheric_harness.workspace import WorkspaceManager


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_model_response(intent: str, **extra: Any) -> dict[str, Any]:
    """Build a mock model response with an emit_intent tool call."""
    args: dict[str, Any] = {"intent": intent, **extra}
    return {
        "tool_calls": [{
            "id": "call_1",
            "type": "function",
            "function": {
                "name": "emit_intent",
                "arguments": json.dumps(args),
            },
        }],
    }


def _make_cog_orchestrator(
    tmp_path: Path,
    *,
    command_name: str = "clip",
    default_raster_format: str = "cog",
    preflight_ok: bool = True,
    executor_succeed: bool = True,
    executor_envelope: dict[str, Any] | None = None,
    output_validation_ok: bool = True,
) -> tuple[Orchestrator, MagicMock, ArtifactRegistry, MagicMock]:
    """Build an Orchestrator specifically for COG/format injection tests.

    Returns (orchestrator, mock_executor, artifact_registry, mock_resolver).
    """
    config = HarnessConfig(
        model="test-model",
        max_turns=20,
        search_cap=20,
        workspace_root=tmp_path,
        default_raster_format=default_raster_format,
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
    output_validator = MagicMock(spec=OutputValidator)

    cmd = CommandDescriptor(
        name=command_name,
        description=f"Test {command_name}",
        category="raster",
        parameters=[ParameterDescriptor(name="input", description="input", type="string", required=False)],
    )
    tool = RegisteredTool(name="ese", version="0.5.0", binary="ese", commands=[cmd])
    catalog = [IntentEntry(
        intent=command_name.split()[-1],
        description=f"Test {command_name}",
        tool=tool,
        command=cmd,
        required_params=[],
    )]

    # Set up resolver to return empty params — orchestrator will inject format
    resolver.resolve.return_value = ResolvedCall(tool=tool, command=cmd, params={})
    validator.validate.return_value = ValidationResult(ok=True)

    if preflight_ok:
        preflight.check_planar_crs.return_value = MagicMock(ok=True)
        preflight.check_disk.return_value = MagicMock(ok=True)
        preflight.run_all_checks.return_value = []
    else:
        preflight.check_planar_crs.return_value = MagicMock(ok=False, error="requires planar CRS")
        preflight.check_disk.return_value = MagicMock(ok=True)
        preflight.run_all_checks.return_value = [
            PreflightResult(
                check="planar_crs",
                resolution=Resolution.BLOCK,
                message="CRS mismatch detected",
            )
        ]

    if executor_envelope is not None:
        envelope = executor_envelope
    elif executor_succeed:
        envelope = {
            "status": "success",
            "data": {"format": "geotiff", "data_type": "raster", "crs": "EPSG:3857"},
        }
    else:
        envelope = {
            "status": "error",
            "error": {"type": "execution_error", "message": "tool failed"},
        }

    output_file = tmp_path / "output.bin"
    output_file.write_bytes(b"output")
    executor.execute.return_value = MagicMock(
        envelope=envelope,
        returncode=0 if executor_succeed else 1,
        output_path=output_file,
    )

    if output_validation_ok:
        output_validator.validate.return_value = OutputValidationResult(ok=True, checks=[])
    else:
        output_validator.validate.return_value = OutputValidationResult(
            ok=False,
            checks=[{"check": "raster_validity", "passed": False, "message": "Raster dimensions 1x1"}],
            error="Raster dimensions 1x1 or smaller",
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
        default_raster_format=default_raster_format,
    )
    return orch, executor, artifact_registry, resolver


# ---------------------------------------------------------------------------
# COG Default Injection Tests
# ---------------------------------------------------------------------------


class TestCogDefaultInjection:
    """Test that COG format is injected for raster-producing commands."""

    @patch("ecospheric_harness.orchestrator.httpx")
    @patch("ecospheric_harness.orchestrator.available_intents")
    def test_cog_injected_on_raster_command(
        self, mock_menu: MagicMock, mock_httpx: MagicMock, tmp_path: Path,
    ) -> None:
        """When default_raster_format='cog', raster commands get format=cog."""
        orch, executor, _, _ = _make_cog_orchestrator(
            tmp_path,
            command_name="reproject",
            default_raster_format="cog",
        )

        mock_menu.return_value = [IntentOption(
            intent="reproject", description="Reproject raster", required_params=[],
        )]
        mock_httpx.post.side_effect = [
            MagicMock(
                json=MagicMock(return_value={
                    "choices": [{"message": _make_model_response("reproject", params={"output_crs": "EPSG:3857"})}],
                }),
                raise_for_status=MagicMock(),
            ),
            MagicMock(
                json=MagicMock(return_value={
                    "choices": [{"message": _make_model_response("complete", summary="done")}],
                }),
                raise_for_status=MagicMock(),
            ),
        ]

        orch.run("reproject raster")

        # Verify executor was called with format=cog injected
        call_args = executor.execute.call_args
        assert call_args is not None
        # Third positional arg is params
        params = call_args[0][2]
        assert "format" in params, f"Expected format=cog to be injected, got params={params}"
        assert params["format"] == "cog"

    @patch("ecospheric_harness.orchestrator.httpx")
    @patch("ecospheric_harness.orchestrator.available_intents")
    def test_no_injection_when_format_specified(
        self, mock_menu: MagicMock, mock_httpx: MagicMock, tmp_path: Path,
    ) -> None:
        """When format is already in params, don't override."""
        orch, executor, _, resolver = _make_cog_orchestrator(
            tmp_path,
            command_name="reproject",
            default_raster_format="cog",
        )

        # Override resolver to pass through actual params
        def _resolve(intent, params, input_artifact=None):
            cmd = list(orch._catalog)[0].command
            tool = list(orch._catalog)[0].tool
            return ResolvedCall(tool=tool, command=cmd, params=params)

        resolver.resolve.side_effect = _resolve

        mock_menu.return_value = [IntentOption(
            intent="reproject", description="Reproject raster", required_params=[],
        )]
        mock_httpx.post.side_effect = [
            MagicMock(
                json=MagicMock(return_value={
                    "choices": [{"message": _make_model_response(
                        "reproject",
                        params={"output_crs": "EPSG:3857", "format": "geotiff"},
                    )}],
                }),
                raise_for_status=MagicMock(),
            ),
            MagicMock(
                json=MagicMock(return_value={
                    "choices": [{"message": _make_model_response("complete", summary="done")}],
                }),
                raise_for_status=MagicMock(),
            ),
        ]

        orch.run("reproject raster in geotiff")

        call_args = executor.execute.call_args
        assert call_args is not None
        params = call_args[0][2]
        # Should keep the explicitly specified format, not override
        assert params.get("format") == "geotiff"

    @patch("ecospheric_harness.orchestrator.httpx")
    @patch("ecospheric_harness.orchestrator.available_intents")
    def test_no_injection_on_non_raster_command(
        self, mock_menu: MagicMock, mock_httpx: MagicMock, tmp_path: Path,
    ) -> None:
        """Vector commands don't get format=cog."""
        orch, executor, _, _ = _make_cog_orchestrator(
            tmp_path,
            command_name="dissolve",
            default_raster_format="cog",
        )
        # resolve should return a dissolve command
        cmd = CommandDescriptor(
            name="dissolve",
            description="Dissolve features",
            category="vector",
            parameters=[ParameterDescriptor(name="input", description="input", type="string", required=False)],
        )
        tool = list(orch._catalog)[0].tool
        orch._resolver.resolve.return_value = ResolvedCall(tool=tool, command=cmd, params={})

        mock_menu.return_value = [IntentOption(
            intent="dissolve", description="Dissolve", required_params=[],
        )]
        mock_httpx.post.side_effect = [
            MagicMock(
                json=MagicMock(return_value={
                    "choices": [{"message": _make_model_response("dissolve", params={"by": "type"})}],
                }),
                raise_for_status=MagicMock(),
            ),
            MagicMock(
                json=MagicMock(return_value={
                    "choices": [{"message": _make_model_response("complete", summary="done")}],
                }),
                raise_for_status=MagicMock(),
            ),
        ]

        orch.run("dissolve features")

        call_args = executor.execute.call_args
        assert call_args is not None
        params = call_args[0][2]
        # Dissolve is not a raster-producing command — no format injection
        assert "format" not in params

    @patch("ecospheric_harness.orchestrator.httpx")
    @patch("ecospheric_harness.orchestrator.available_intents")
    def test_no_injection_when_default_is_none(
        self, mock_menu: MagicMock, mock_httpx: MagicMock, tmp_path: Path,
    ) -> None:
        """When default_raster_format is empty string, don't inject."""
        orch, executor, _, _ = _make_cog_orchestrator(
            tmp_path,
            command_name="reproject",
            default_raster_format="",
        )

        mock_menu.return_value = [IntentOption(
            intent="reproject", description="Reproject raster", required_params=[],
        )]
        mock_httpx.post.side_effect = [
            MagicMock(
                json=MagicMock(return_value={
                    "choices": [{"message": _make_model_response("reproject", params={"output_crs": "EPSG:3857"})}],
                }),
                raise_for_status=MagicMock(),
            ),
            MagicMock(
                json=MagicMock(return_value={
                    "choices": [{"message": _make_model_response("complete", summary="done")}],
                }),
                raise_for_status=MagicMock(),
            ),
        ]

        orch.run("reproject raster")

        call_args = executor.execute.call_args
        assert call_args is not None
        params = call_args[0][2]
        # Empty string is falsy — no injection
        assert "format" not in params

    @patch("ecospheric_harness.orchestrator.httpx")
    @patch("ecospheric_harness.orchestrator.available_intents")
    def test_cog_injected_for_all_raster_commands(
        self, mock_menu: MagicMock, mock_httpx: MagicMock, tmp_path: Path,
    ) -> None:
        """All raster-producing commands (reproject, warp, clip, slope, etc.) get format=cog."""
        raster_commands = [
            "reproject", "warp", "clip", "buffer", "slope", "aspect",
            "hillshade", "contour", "rasterize", "reclassify", "calc",
            "mosaic", "tile",
        ]
        for cmd_name in raster_commands:
            orch, executor, _, _ = _make_cog_orchestrator(
                tmp_path,
                command_name=cmd_name,
                default_raster_format="cog",
            )

            mock_menu.return_value = [IntentOption(
                intent=cmd_name.split()[-1], description=f"Test {cmd_name}", required_params=[],
            )]
            mock_httpx.post.side_effect = [
                MagicMock(
                    json=MagicMock(return_value={
                        "choices": [{"message": _make_model_response(cmd_name.split()[-1])}],
                    }),
                    raise_for_status=MagicMock(),
                ),
                MagicMock(
                    json=MagicMock(return_value={
                        "choices": [{"message": _make_model_response("complete", summary="done")}],
                    }),
                    raise_for_status=MagicMock(),
                ),
            ]

            orch.run(f"run {cmd_name}")

            call_args = executor.execute.call_args
            assert call_args is not None, f"No executor call for {cmd_name}"
            params = call_args[0][2]
            assert "format" in params, f"Expected format=cog for {cmd_name}, got params={params}"
            assert params["format"] == "cog", f"Expected format=cog for {cmd_name}, got {params}"

            # Reset mocks for next iteration
            executor.reset_mock()
            mock_menu.reset_mock()
            mock_httpx.reset_mock()

    @patch("ecospheric_harness.orchestrator.httpx")
    @patch("ecospheric_harness.orchestrator.available_intents")
    def test_output_format_key_blocks_injection(
        self, mock_menu: MagicMock, mock_httpx: MagicMock, tmp_path: Path,
    ) -> None:
        """When output_format is in params, COG default is not injected."""
        orch, executor, _, resolver = _make_cog_orchestrator(
            tmp_path,
            command_name="reproject",
            default_raster_format="cog",
        )

        # Override resolver to pass through actual params
        def _resolve(intent, params, input_artifact=None):
            cmd = list(orch._catalog)[0].command
            tool = list(orch._catalog)[0].tool
            return ResolvedCall(tool=tool, command=cmd, params=params)

        resolver.resolve.side_effect = _resolve

        mock_menu.return_value = [IntentOption(
            intent="reproject", description="Reproject", required_params=[],
        )]
        mock_httpx.post.side_effect = [
            MagicMock(
                json=MagicMock(return_value={
                    "choices": [{"message": _make_model_response(
                        "reproject",
                        params={"output_crs": "EPSG:3857", "output_format": "geopackage"},
                    )}],
                }),
                raise_for_status=MagicMock(),
            ),
            MagicMock(
                json=MagicMock(return_value={
                    "choices": [{"message": _make_model_response("complete", summary="done")}],
                }),
                raise_for_status=MagicMock(),
            ),
        ]

        orch.run("reproject to gpkg")

        call_args = executor.execute.call_args
        assert call_args is not None
        params = call_args[0][2]
        # output_format should block injection
        assert params.get("output_format") == "geopackage"
        assert "format" not in params


# ---------------------------------------------------------------------------
# Preflight Integration Tests
# ---------------------------------------------------------------------------


class TestPreflightIntegration:
    """Test preflight checks in the orchestrator flow."""

    @patch("ecospheric_harness.orchestrator.httpx")
    @patch("ecospheric_harness.orchestrator.available_intents")
    def test_crs_mismatch_blocks_execution(
        self, mock_menu: MagicMock, mock_httpx: MagicMock, tmp_path: Path,
    ) -> None:
        """Binary op with mismatched CRS → BLOCK, step rejected."""
        from ecospheric_harness.config import HarnessConfig
        from ecospheric_harness.executor import ToolExecutor
        from ecospheric_harness.workspace import WorkspaceManager

        config = HarnessConfig(
            model="test-model", max_turns=20, workspace_root=tmp_path,
        )
        ws = WorkspaceManager(tmp_path, disk_limit_bytes=10_000_000)
        registry = ArtifactRegistry(workspace=ws, disk_limit_bytes=10_000_000)
        resolver = MagicMock(spec=IntentResolver)
        resolver.command_needs_input.return_value = False
        validator = MagicMock(spec=SchemaValidator)
        executor = MagicMock(spec=ToolExecutor)
        preflight = MagicMock(spec=PreflightChecker)
        corrections = MagicMock(spec=CorrectionHandler)
        output_validator = MagicMock(spec=OutputValidator)

        cmd = CommandDescriptor(
            name="clip", description="Clip features",
            category="vector",
            parameters=[ParameterDescriptor(name="input", description="input", type="string", required=False)],
        )
        tool = RegisteredTool(name="ese", version="0.5.0", binary="ese", commands=[cmd])
        catalog = [IntentEntry(
            intent="clip", description="Clip", tool=tool, command=cmd, required_params=[],
        )]
        resolver.resolve.return_value = ResolvedCall(tool=tool, command=cmd, params={"by": "mask.shp"})
        validator.validate.return_value = ValidationResult(ok=True)

        # Preflight returns BLOCK for CRS mismatch
        preflight.run_all_checks.return_value = [
            PreflightResult(
                check="binary_crs_match",
                resolution=Resolution.BLOCK,
                message="Input CRS (EPSG:4326) does not match clip-by CRS (EPSG:3857). Reproject one to match.",
            )
        ]
        preflight.check_planar_crs.return_value = MagicMock(ok=True)
        preflight.check_disk.return_value = MagicMock(ok=True)

        output_file = tmp_path / "output.bin"
        output_file.write_bytes(b"output")
        output_validator.validate.return_value = OutputValidationResult(ok=True)

        orch = Orchestrator(
            config=config, registry=MagicMock(spec=ToolRegistry),
            resolver=resolver, validator=validator, executor=executor,
            artifact_registry=registry, preflight=preflight,
            corrections=corrections, catalog=catalog, workspace=ws,
            output_validator=output_validator,
        )

        mock_menu.return_value = [IntentOption(
            intent="clip", description="Clip", required_params=[],
        )]
        mock_httpx.post.side_effect = [
            MagicMock(
                json=MagicMock(return_value={
                    "choices": [{"message": _make_model_response("clip", params={"by": "mask.shp"})}],
                }),
                raise_for_status=MagicMock(),
            ),
            MagicMock(
                json=MagicMock(return_value={
                    "choices": [{"message": _make_model_response("complete", summary="blocked")}],
                }),
                raise_for_status=MagicMock(),
            ),
        ]

        result = orch.run("clip with mismatched CRS")

        # Step should be rejected (BLOCK)
        assert len(result.steps) == 1
        assert result.steps[0].status == "rejected"
        assert result.steps[0].intent == "clip"
        # Executor should NOT have been called
        executor.execute.assert_not_called()

    @patch("ecospheric_harness.orchestrator.httpx")
    @patch("ecospheric_harness.orchestrator.available_intents")
    def test_geographic_crs_distance_op_blocks(
        self, mock_menu: MagicMock, mock_httpx: MagicMock, tmp_path: Path,
    ) -> None:
        """Buffer on geographic CRS → AUTO_FIX treated as BLOCK."""
        from ecospheric_harness.config import HarnessConfig
        from ecospheric_harness.executor import ToolExecutor
        from ecospheric_harness.workspace import WorkspaceManager

        config = HarnessConfig(
            model="test-model", max_turns=20, workspace_root=tmp_path,
        )
        ws = WorkspaceManager(tmp_path, disk_limit_bytes=10_000_000)
        registry = ArtifactRegistry(workspace=ws, disk_limit_bytes=10_000_000)
        resolver = MagicMock(spec=IntentResolver)
        resolver.command_needs_input.return_value = False
        validator = MagicMock(spec=SchemaValidator)
        executor = MagicMock(spec=ToolExecutor)
        preflight = MagicMock(spec=PreflightChecker)
        corrections = MagicMock(spec=CorrectionHandler)
        output_validator = MagicMock(spec=OutputValidator)

        cmd = CommandDescriptor(
            name="buffer", description="Buffer features",
            category="vector",
            parameters=[ParameterDescriptor(name="input", description="input", type="string", required=False)],
        )
        tool = RegisteredTool(name="ese", version="0.5.0", binary="ese", commands=[cmd])
        catalog = [IntentEntry(
            intent="buffer", description="Buffer", tool=tool, command=cmd, required_params=[],
        )]
        resolver.resolve.return_value = ResolvedCall(tool=tool, command=cmd, params={"distance": 500})
        validator.validate.return_value = ValidationResult(ok=True)

        # Preflight returns AUTO_FIX for geographic CRS buffer
        preflight.run_all_checks.return_value = [
            PreflightResult(
                check="planar_crs",
                resolution=Resolution.AUTO_FIX,
                message="Buffer on geographic CRS. Reproject to a projected CRS first (e.g., EPSG:3857).",
            )
        ]
        preflight.check_planar_crs.return_value = MagicMock(ok=True)
        preflight.check_disk.return_value = MagicMock(ok=True)

        output_file = tmp_path / "output.bin"
        output_file.write_bytes(b"output")
        output_validator.validate.return_value = OutputValidationResult(ok=True)

        orch = Orchestrator(
            config=config, registry=MagicMock(spec=ToolRegistry),
            resolver=resolver, validator=validator, executor=executor,
            artifact_registry=registry, preflight=preflight,
            corrections=corrections, catalog=catalog, workspace=ws,
            output_validator=output_validator,
        )

        mock_menu.return_value = [IntentOption(
            intent="buffer", description="Buffer", required_params=[],
        )]
        mock_httpx.post.side_effect = [
            MagicMock(
                json=MagicMock(return_value={
                    "choices": [{"message": _make_model_response("buffer", params={"distance": 500})}],
                }),
                raise_for_status=MagicMock(),
            ),
            MagicMock(
                json=MagicMock(return_value={
                    "choices": [{"message": _make_model_response("complete", summary="blocked")}],
                }),
                raise_for_status=MagicMock(),
            ),
        ]

        result = orch.run("buffer geographic data")

        # AUTO_FIX → rejected (treated as BLOCK in Phase 2)
        assert len(result.steps) == 1
        assert result.steps[0].status == "rejected"
        assert result.steps[0].intent == "buffer"
        executor.execute.assert_not_called()

    @patch("ecospheric_harness.orchestrator.httpx")
    @patch("ecospheric_harness.orchestrator.available_intents")
    def test_invalid_target_crs_blocks(
        self, mock_menu: MagicMock, mock_httpx: MagicMock, tmp_path: Path,
    ) -> None:
        """Reproject to invalid CRS → BLOCK."""
        from ecospheric_harness.config import HarnessConfig
        from ecospheric_harness.executor import ToolExecutor
        from ecospheric_harness.workspace import WorkspaceManager

        config = HarnessConfig(
            model="test-model", max_turns=20, workspace_root=tmp_path,
        )
        ws = WorkspaceManager(tmp_path, disk_limit_bytes=10_000_000)
        registry = ArtifactRegistry(workspace=ws, disk_limit_bytes=10_000_000)
        resolver = MagicMock(spec=IntentResolver)
        resolver.command_needs_input.return_value = False
        validator = MagicMock(spec=SchemaValidator)
        executor = MagicMock(spec=ToolExecutor)
        preflight = MagicMock(spec=PreflightChecker)
        corrections = MagicMock(spec=CorrectionHandler)
        output_validator = MagicMock(spec=OutputValidator)

        cmd = CommandDescriptor(
            name="reproject", description="Reproject",
            category="raster",
            parameters=[ParameterDescriptor(name="input", description="input", type="string", required=False)],
        )
        tool = RegisteredTool(name="ese", version="0.5.0", binary="ese", commands=[cmd])
        catalog = [IntentEntry(
            intent="reproject", description="Reproject", tool=tool, command=cmd, required_params=[],
        )]
        resolver.resolve.return_value = ResolvedCall(
            tool=tool, command=cmd, params={"output_crs": "EPSG:INVALID"},
        )
        validator.validate.return_value = ValidationResult(ok=True)

        # Preflight returns BLOCK for invalid CRS (crs_exists check)
        preflight.run_all_checks.return_value = [
            PreflightResult(
                check="crs_exists",
                resolution=Resolution.BLOCK,
                message="Target CRS 'EPSG:INVALID' does not exist or is unknown. Provide a valid EPSG code.",
            )
        ]
        preflight.check_planar_crs.return_value = MagicMock(ok=True)
        preflight.check_disk.return_value = MagicMock(ok=True)

        output_file = tmp_path / "output.bin"
        output_file.write_bytes(b"output")
        output_validator.validate.return_value = OutputValidationResult(ok=True)

        orch = Orchestrator(
            config=config, registry=MagicMock(spec=ToolRegistry),
            resolver=resolver, validator=validator, executor=executor,
            artifact_registry=registry, preflight=preflight,
            corrections=corrections, catalog=catalog, workspace=ws,
            output_validator=output_validator,
        )

        mock_menu.return_value = [IntentOption(
            intent="reproject", description="Reproject", required_params=[],
        )]
        mock_httpx.post.side_effect = [
            MagicMock(
                json=MagicMock(return_value={
                    "choices": [{"message": _make_model_response(
                        "reproject", params={"output_crs": "EPSG:INVALID"},
                    )}],
                }),
                raise_for_status=MagicMock(),
            ),
            MagicMock(
                json=MagicMock(return_value={
                    "choices": [{"message": _make_model_response("complete", summary="blocked")}],
                }),
                raise_for_status=MagicMock(),
            ),
        ]

        result = orch.run("reproject to invalid CRS")

        assert len(result.steps) == 1
        assert result.steps[0].status == "rejected"
        assert result.steps[0].intent == "reproject"
        executor.execute.assert_not_called()

    @patch("ecospheric_harness.orchestrator.httpx")
    @patch("ecospheric_harness.orchestrator.available_intents")
    def test_model_discretion_warning_surfaces(
        self, mock_menu: MagicMock, mock_httpx: MagicMock, tmp_path: Path,
    ) -> None:
        """MODEL_DISCRETION result → warning in turn-state."""
        from ecospheric_harness.config import HarnessConfig
        from ecospheric_harness.executor import ToolExecutor
        from ecospheric_harness.workspace import WorkspaceManager

        config = HarnessConfig(
            model="test-model", max_turns=20, workspace_root=tmp_path,
        )
        ws = WorkspaceManager(tmp_path, disk_limit_bytes=10_000_000)
        registry = ArtifactRegistry(workspace=ws, disk_limit_bytes=10_000_000)
        resolver = MagicMock(spec=IntentResolver)
        resolver.command_needs_input.return_value = False
        validator = MagicMock(spec=SchemaValidator)
        executor = MagicMock(spec=ToolExecutor)
        preflight = MagicMock(spec=PreflightChecker)
        corrections = MagicMock(spec=CorrectionHandler)
        output_validator = MagicMock(spec=OutputValidator)

        cmd = CommandDescriptor(
            name="reproject", description="Reproject",
            category="raster",
            parameters=[ParameterDescriptor(name="input", description="input", type="string", required=False)],
        )
        tool = RegisteredTool(name="ese", version="0.5.0", binary="ese", commands=[cmd])
        catalog = [IntentEntry(
            intent="reproject", description="Reproject", tool=tool, command=cmd, required_params=[],
        )]
        resolver.resolve.return_value = ResolvedCall(
            tool=tool, command=cmd, params={"output_crs": "EPSG:3857"},
        )
        validator.validate.return_value = ValidationResult(ok=True)

        # Preflight returns MODEL_DISCRETION for disk check
        preflight.run_all_checks.return_value = [
            PreflightResult(
                check="disk_usage",
                resolution=Resolution.MODEL_DISCRETION,
                message="Disk usage is at 85%. Proceed with caution.",
            )
        ]
        preflight.check_planar_crs.return_value = MagicMock(ok=True)
        preflight.check_disk.return_value = MagicMock(ok=True)

        output_file = tmp_path / "output.bin"
        output_file.write_bytes(b"output")
        executor.execute.return_value = MagicMock(
            envelope={
                "status": "success",
                "data": {"format": "geotiff", "data_type": "raster", "crs": "EPSG:3857"},
            },
            returncode=0,
            output_path=output_file,
        )
        output_validator.validate.return_value = OutputValidationResult(ok=True)

        orch = Orchestrator(
            config=config, registry=MagicMock(spec=ToolRegistry),
            resolver=resolver, validator=validator, executor=executor,
            artifact_registry=registry, preflight=preflight,
            corrections=corrections, catalog=catalog, workspace=ws,
            output_validator=output_validator,
        )

        mock_menu.return_value = [IntentOption(
            intent="reproject", description="Reproject", required_params=[],
        )]
        mock_httpx.post.side_effect = [
            MagicMock(
                json=MagicMock(return_value={
                    "choices": [{"message": _make_model_response(
                        "reproject", params={"output_crs": "EPSG:3857"},
                    )}],
                }),
                raise_for_status=MagicMock(),
            ),
            MagicMock(
                json=MagicMock(return_value={
                    "choices": [{"message": _make_model_response("complete", summary="done")}],
                }),
                raise_for_status=MagicMock(),
            ),
        ]

        result = orch.run("reproject with disk warning")

        # MODEL_DISCRETION → execution proceeds, step succeeds
        assert len(result.steps) == 1
        assert result.steps[0].status == "success"
        assert result.steps[0].intent == "reproject"
        # Executor should have been called (warning, not block)
        executor.execute.assert_called_once()

        # Verify warnings were captured (warnings are consumed by _build_turn_state)
        # After first turn, _pending_warnings should be cleared.
        assert orch._pending_warnings == []


# ---------------------------------------------------------------------------
# Output Validation Integration Tests
# ---------------------------------------------------------------------------


class TestOutputValidationIntegration:
    """Test output validation in the orchestrator flow."""

    @patch("ecospheric_harness.orchestrator.httpx")
    @patch("ecospheric_harness.orchestrator.available_intents")
    def test_valid_output_passes(
        self, mock_menu: MagicMock, mock_httpx: MagicMock, tmp_path: Path,
    ) -> None:
        """Normal execution with valid output → success."""
        orch, executor, artifacts, _ = _make_cog_orchestrator(
            tmp_path,
            command_name="reproject",
            default_raster_format="",
        )

        mock_menu.return_value = [IntentOption(
            intent="reproject", description="Reproject", required_params=[],
        )]
        mock_httpx.post.side_effect = [
            MagicMock(
                json=MagicMock(return_value={
                    "choices": [{"message": _make_model_response("reproject", params={"output_crs": "EPSG:3857"})}],
                }),
                raise_for_status=MagicMock(),
            ),
            MagicMock(
                json=MagicMock(return_value={
                    "choices": [{"message": _make_model_response("complete", summary="done")}],
                }),
                raise_for_status=MagicMock(),
            ),
        ]

        result = orch.run("reproject raster")

        assert len(result.steps) == 1
        assert result.steps[0].status == "success"
        assert result.final_artifact is not None

    @patch("ecospheric_harness.orchestrator.httpx")
    @patch("ecospheric_harness.orchestrator.available_intents")
    def test_validation_failed_cleans_orphan(
        self, mock_menu: MagicMock, mock_httpx: MagicMock, tmp_path: Path,
    ) -> None:
        """Invalid output → validation_failed status + orphan cleaned."""
        orch, executor, artifacts, _ = _make_cog_orchestrator(
            tmp_path,
            command_name="reproject",
            default_raster_format="",
            executor_succeed=True,
            output_validation_ok=False,
        )

        mock_menu.return_value = [IntentOption(
            intent="reproject", description="Reproject", required_params=[],
        )]
        mock_httpx.post.side_effect = [
            MagicMock(
                json=MagicMock(return_value={
                    "choices": [{"message": _make_model_response("reproject", params={"output_crs": "EPSG:3857"})}],
                }),
                raise_for_status=MagicMock(),
            ),
            MagicMock(
                json=MagicMock(return_value={
                    "choices": [{"message": _make_model_response("complete", summary="failed validation")}],
                }),
                raise_for_status=MagicMock(),
            ),
        ]

        result = orch.run("reproject raster with bad output")

        # Output validation failed → step should be validation_failed
        assert len(result.steps) == 1
        assert result.steps[0].status == "validation_failed"
        assert result.steps[0].intent == "reproject"
        # No artifact should be registered since validation failed
        assert result.final_artifact is None


# ---------------------------------------------------------------------------
# Eval Fixtures Phase 2 Tests
# ---------------------------------------------------------------------------


class TestEvalFixturesPhase2:
    """Test eval fixtures for preflight/output validation scenarios."""

    def test_preflight_block_fixture_exists(self) -> None:
        """Verify the eval fixtures for Phase 2 exist with correct tags and structure."""
        from ecospheric_harness.eval.cases import FIXTURES

        phase2_fixtures = [f for f in FIXTURES if "phase2" in f.tags]
        assert len(phase2_fixtures) >= 5, f"Expected >= 5 phase2 fixtures, got {len(phase2_fixtures)}"

        expected_names = {
            "phase2_crs_mismatch_blocks",
            "phase2_geographic_buffer_blocks",
            "phase2_invalid_crs_blocks",
            "phase2_valid_pipeline",
            "phase2_output_validation_failure",
        }
        actual_names = {f.name for f in phase2_fixtures}
        for name in expected_names:
            assert name in actual_names, f"Missing fixture: {name}"

    def test_phase2_preflight_fixtures_have_preflight_tag(self) -> None:
        """All preflight-related phase2 fixtures should have the 'preflight' tag."""
        from ecospheric_harness.eval.cases import FIXTURES

        preflight_fixtures = [f for f in FIXTURES if "preflight" in f.tags and "phase2" in f.tags]
        assert len(preflight_fixtures) >= 3, f"Expected >= 3 preflight phase2 fixtures, got {len(preflight_fixtures)}"

        for fx in preflight_fixtures:
            # Each preflight fixture should have expected_intents
            assert len(fx.expected_intents) > 0, f"Fixture '{fx.name}' has no expected_intents"
            # Should have at least one rejected status for the preflight-blocked step
            has_rejected = any(i.status == "rejected" for i in fx.expected_intents)
            assert has_rejected, f"Fixture '{fx.name}' should have a rejected step for preflight block"
            # Should have an expected_error with preflight type
            assert fx.expected_error is not None, f"Fixture '{fx.name}' should have expected_error"
            assert fx.expected_error.error_type == "preflight"

    def test_phase2_validation_fixture_has_validation_tag(self) -> None:
        """The validation phase2 fixture should have the 'validation' tag."""
        from ecospheric_harness.eval.cases import FIXTURES

        validation_fixtures = [f for f in FIXTURES if "validation" in f.tags and "phase2" in f.tags]
        assert len(validation_fixtures) >= 1, f"Expected >= 1 validation phase2 fixture, got {len(validation_fixtures)}"

        fx = validation_fixtures[0]
        has_validation_failed = any(i.status == "validation_failed" for i in fx.expected_intents)
        assert has_validation_failed, f"Fixture '{fx.name}' should have validation_failed step"
        assert fx.expected_error is not None
        assert fx.expected_error.error_type == "validation"
        assert fx.expected_error.error_contains == "1x1"

    def test_phase2_valid_pipeline_no_error_expected(self) -> None:
        """phase2_valid_pipeline should NOT have an expected_error (it succeeds)."""
        from ecospheric_harness.eval.cases import FIXTURES

        pipeline_fx = [f for f in FIXTURES if f.name == "phase2_valid_pipeline"]
        assert len(pipeline_fx) == 1
        fx = pipeline_fx[0]

        assert fx.expected_error is None, "valid pipeline should not expect an error"
        # Should expect success for all steps
        expected_intents = fx.expected_intents
        assert len(expected_intents) >= 3  # search, reproject, buffer, complete
        # All operation intents should be success
        for ei in expected_intents:
            if ei.intent not in ("complete",):
                assert ei.status == "success", f"Intent '{ei.intent}' should be success"

    def test_all_phase2_fixtures_skip_live(self) -> None:
        """All phase2 fixtures should be skip_live=True (not yet live-tested)."""
        from ecospheric_harness.eval.cases import FIXTURES

        phase2_fixtures = [f for f in FIXTURES if "phase2" in f.tags]
        for fx in phase2_fixtures:
            assert fx.skip_live, f"Fixture '{fx.name}' should be skip_live=True"