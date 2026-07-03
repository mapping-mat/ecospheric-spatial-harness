"""End-to-end orchestrator tests for the reproject/convert CRS metadata bug.

These tests exercise the orchestrator's artifact registration path with
real ESE-shaped envelopes (which use ``to_crs`` / ``from_crs`` instead
of ``crs`` and don't carry bbox at all).  Before the fix, the
registered ArtifactRecord had ``crs=None`` and ``bbox=None`` for
reproject output, which then caused downstream buffer preflight to
reject the input for "CRS unknown".

The fix introduces ``ecospheric_harness.artifact_metadata`` and routes
both the orchestrator and the corrections handler through the shared
helper.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

from etp.describe import CommandDescriptor, ParameterDescriptor

from ecospheric_harness.artifact_registry import ArtifactRegistry
from ecospheric_harness.config import HarnessConfig
from ecospheric_harness.corrections import CorrectionHandler
from ecospheric_harness.executor import ToolExecutor
from ecospheric_harness.intents import (
    IntentEntry,
    RegisteredTool,
    ResolvedCall,
    Resolution,
)
from ecospheric_harness.orchestrator import Orchestrator
from ecospheric_harness.output_validator import OutputValidator
from ecospheric_harness.preflight import PreflightChecker, PreflightResult
from ecospheric_harness.registry import ToolRegistry
from ecospheric_harness.resolver import IntentResolver
from ecospheric_harness.validator import SchemaValidator, ValidationResult
from ecospheric_harness.workspace import WorkspaceManager


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _build_orchestrator(
    tmp_path: Path,
    *,
    input_artifact: object | None = None,
    resolved_tool: RegisteredTool | None = None,
    resolved_command: CommandDescriptor | None = None,
    resolved_params: dict[str, Any] | None = None,
    preflight_results: list[PreflightResult] | None = None,
) -> tuple[Orchestrator, ArtifactRegistry, MagicMock]:
    """Build a real Orchestrator with all dependencies wired.

    Returns (orchestrator, real_artifact_registry, mock_executor).
    The executor is a MagicMock so we can control envelope shape per test.
    """
    config = HarnessConfig(
        model="test-model",
        max_turns=5,
        search_cap=20,
        workspace_root=tmp_path,
    )
    registry = MagicMock(spec=ToolRegistry)
    resolver = MagicMock(spec=IntentResolver)
    resolver.command_needs_input.return_value = True  # reproject needs input

    validator = MagicMock(spec=SchemaValidator)
    validator.validate.return_value = ValidationResult(ok=True)

    # Real output validator
    output_validator = OutputValidator()

    preflight = MagicMock(spec=PreflightChecker)
    preflight.run_all_checks.return_value = preflight_results or [
        PreflightResult(check="planar_crs"),
    ]
    preflight.check_planar_crs.return_value = PreflightResult(check="planar_crs")
    preflight.check_disk.return_value = PreflightResult(check="disk")
    preflight.check_ssrf.return_value = PreflightResult(check="ssrf")

    corrections = MagicMock(spec=CorrectionHandler)

    # Build a real workspace and artifact registry
    ws = WorkspaceManager(tmp_path, disk_limit_bytes=100_000_000)
    artifact_registry = ArtifactRegistry(workspace=ws, disk_limit_bytes=100_000_000)

    # Seed with input artifact if provided
    if input_artifact is not None:
        artifact_registry._artifacts["input_001"] = input_artifact  # type: ignore[attr-defined]
        artifact_registry._counter = 1  # type: ignore[attr-defined]
        # Mark it as not current (so it doesn't auto-resolve as input)
        # Actually we want it to be available; current defaults to recent[0]
        # which will be this one — that's fine.

    if resolved_command is None:
        resolved_command = CommandDescriptor(
            name="proj transform",
            description="Reproject",
            category="vector",
            parameters=[
                ParameterDescriptor(name="input", description="input", type="string", required=False),
                ParameterDescriptor(name="--to", description="target CRS", type="string", required=True),
            ],
            requires_planar_crs=False,
        )
    if resolved_tool is None:
        resolved_tool = RegisteredTool(
            name="ese", version="0.5.0", binary="ese", commands=[resolved_command],
        )

    resolver.resolve.return_value = ResolvedCall(
        tool=resolved_tool,
        command=resolved_command,
        params=resolved_params or {"to": "EPSG:32610"},
    )

    catalog: list[IntentEntry] = []

    orch = Orchestrator(
        config=config,
        registry=registry,
        resolver=resolver,
        validator=validator,
        executor=MagicMock(spec=ToolExecutor),  # replaced by caller
        artifact_registry=artifact_registry,
        preflight=preflight,
        corrections=corrections,
        catalog=catalog,
        workspace=ws,
        output_validator=output_validator,
    )

    return orch, artifact_registry, orch._executor  # type: ignore[attr-defined]


def _make_input_artifact(
    artifact_registry: ArtifactRegistry, crs: str = "EPSG:4326",
) -> Any:
    """Register a minimal input artifact for the reproject step."""
    p = artifact_registry._workspace.create_temp_path(suffix=".geojson")  # type: ignore[attr-defined]
    # Write a tiny but valid geojson so output validation passes
    p.write_text(json.dumps({
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "properties": {},
                "geometry": {
                    "type": "Point",
                    "coordinates": [-122.5, 37.7],
                },
            },
        ],
    }))
    rec = artifact_registry.register(
        path=p,
        format="geojson",
        data_type="vector",
        crs=crs,
        bbox=[-122.6, 37.6, -122.4, 37.8],
        step_number=1,
        envelope={"status": "success", "data": {"crs": crs}},
        intent="search_osm",
        tool_name="edd",
        tool_version="0.5.0",
        command_name="search",
    )
    return rec


def _make_ese_proj_transform_envelope(output_path: Path) -> dict[str, Any]:
    """Real-shaped ESE ``proj transform`` envelope (no data.crs, no bbox)."""
    return {
        "status": "success",
        "tool": "ese",
        "command": "proj transform",
        "data": {
            "feature_count": 1,
            "from_crs": "EPSG:4326",
            "to_crs": "EPSG:32610",
            "same_crs": False,
            "output_path": str(output_path),
            "provenance": [
                {
                    "command": "ese proj transform",
                    "crs_working_crs": "EPSG:32610",
                },
            ],
            "format": "geoparquet",
            "data_type": "vector",
        },
    }


def _make_ese_convert_envelope(output_path: Path) -> dict[str, Any]:
    """ESE ``convert --output-crs EPSG:3857`` envelope (data.crs set)."""
    return {
        "status": "success",
        "tool": "ese",
        "command": "convert",
        "data": {
            "input_format": "geoparquet",
            "output_format": "geojson",
            "feature_count": 1,
            "crs": "EPSG:3857",
            "output_path": str(output_path),
            "provenance": [
                {"command": "ese convert", "crs_working_crs": "EPSG:3857"},
            ],
            "format": "geojson",
            "data_type": "vector",
        },
    }


# ---------------------------------------------------------------------------
# Reproject → register → record has CRS
# ---------------------------------------------------------------------------


class TestReprojectMetadata:
    def test_reproject_records_crs_from_to_crs(
        self, tmp_path: Path,
    ) -> None:
        """The fix: ESE ``proj transform`` envelope has ``to_crs``;
        before the fix, this registered as ``crs=None``."""
        orch, artifact_registry, mock_executor = _build_orchestrator(tmp_path)
        input_rec = _make_input_artifact(artifact_registry)

        # Configure mock executor to return a real geojson file as the
        # output of the reproject, with an ESE-shaped envelope.
        output_path = orch._workspace.create_temp_path(suffix=".parquet")  # type: ignore[attr-defined]
        output_path.write_text("not really parquet, but enough for tests")
        mock_executor.execute.return_value = MagicMock(
            envelope=_make_ese_proj_transform_envelope(output_path),
            returncode=0,
            output_path=output_path,
        )

        # Pre-set the input artifact as the current artifact so the
        # auto-resolution picks it up
        artifact_registry._artifacts[input_rec.artifact_id] = input_rec  # type: ignore[attr-defined]
        # Mark it as the current by clearing anything newer
        # (it's already the only artifact)

        result, error_turn = orch._handle_operation("reproject", {})

        # No error
        assert error_turn is None
        assert result is None  # not a terminal intent

        # The new artifact was registered
        all_records = artifact_registry.list_all()
        assert len(all_records) == 2  # input + reproject
        reproj = next(r for r in all_records if r.intent == "reproject")

        # The fix: CRS is non-null and matches the to_crs from the envelope.
        assert reproj.crs is not None
        assert "32610" in reproj.crs or "UTM" in reproj.crs.upper()

    def test_convert_records_crs(
        self, tmp_path: Path,
    ) -> None:
        """Convert already had ``data.crs`` so this always worked, but
        verify the fix didn't regress it."""
        orch, artifact_registry, mock_executor = _build_orchestrator(tmp_path)
        input_rec = _make_input_artifact(artifact_registry)

        output_path = orch._workspace.create_temp_path(suffix=".geojson")  # type: ignore[attr-defined]
        output_path.write_text(json.dumps({
            "type": "FeatureCollection",
            "features": [],
        }))
        mock_executor.execute.return_value = MagicMock(
            envelope=_make_ese_convert_envelope(output_path),
            returncode=0,
            output_path=output_path,
        )

        result, error_turn = orch._handle_operation("convert", {})

        assert error_turn is None
        assert result is None

        all_records = artifact_registry.list_all()
        assert len(all_records) == 2
        convert = next(r for r in all_records if r.intent == "convert")
        assert convert.crs is not None
        assert "3857" in convert.crs


class TestReprojectMetadataPreflightChain:
    """After the fix, the registered CRS is correct so the next step's
    preflight check (which needs a planar CRS) should pass."""

    def test_registered_reproject_crs_is_planar(
        self, tmp_path: Path,
    ) -> None:
        """Reproject to UTM 10N → registered CRS is planar (UTM, not 4326)."""
        from pyproj import CRS as PyprojCRS

        orch, artifact_registry, mock_executor = _build_orchestrator(tmp_path)
        input_rec = _make_input_artifact(artifact_registry)

        output_path = orch._workspace.create_temp_path(suffix=".parquet")  # type: ignore[attr-defined]
        output_path.write_text("placeholder")
        mock_executor.execute.return_value = MagicMock(
            envelope=_make_ese_proj_transform_envelope(output_path),
            returncode=0,
            output_path=output_path,
        )

        orch._handle_operation("reproject", {})

        reproj = next(
            r for r in artifact_registry.list_all() if r.intent == "reproject"
        )
        # Use pyproj to confirm the registered CRS is planar
        crs = PyprojCRS(reproj.crs)
        assert not crs.is_geographic
        # And not None (regression check for the bug)
        assert reproj.crs is not None


class TestReprojectBboxDerivation:
    """ESE envelopes don't carry bbox — verify the fix derives it from
    the output file (or sets it to None if unreadable)."""

    def test_bbox_derived_from_geojson_output(
        self, tmp_path: Path,
    ) -> None:
        """When output is geojson, bbox is derived from the file."""
        orch, artifact_registry, mock_executor = _build_orchestrator(tmp_path)
        input_rec = _make_input_artifact(artifact_registry)

        # Make the executor produce a real GeoJSON file
        output_path = orch._workspace.create_temp_path(suffix=".geojson")  # type: ignore[attr-defined]
        output_path.write_text(json.dumps({
            "type": "FeatureCollection",
            "features": [
                {
                    "type": "Feature",
                    "properties": {},
                    "geometry": {
                        "type": "Polygon",
                        "coordinates": [[
                            [-122.0, 38.0], [-121.0, 38.0],
                            [-121.0, 39.0], [-122.0, 39.0],
                            [-122.0, 38.0],
                        ]],
                    },
                },
            ],
        }))
        mock_executor.execute.return_value = MagicMock(
            envelope=_make_ese_proj_transform_envelope(output_path),
            returncode=0,
            output_path=output_path,
        )

        orch._handle_operation("reproject", {})

        reproj = next(
            r for r in artifact_registry.list_all() if r.intent == "reproject"
        )
        # Bbox is derived from the file when the envelope is silent
        assert reproj.bbox is not None
        assert len(reproj.bbox) == 4
