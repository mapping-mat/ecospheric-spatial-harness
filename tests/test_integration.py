"""Integration tests for the Ecospheric Agent Harness.

Uses mocked subprocess (for tools) and mocked httpx (for model) but
real ArtifactRegistry, CorrectionHandler, PreflightChecker, IntentResolver,
ToolRegistry, SchemaValidator, and Orchestrator.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

from etp.describe import CommandDescriptor, ParameterDescriptor

from ecospheric_harness.artifact import Artifact
from ecospheric_harness.artifact_registry import ArtifactRegistry
from ecospheric_harness.config import HarnessConfig
from ecospheric_harness.corrections import CorrectionHandler
from ecospheric_harness.executor import ToolExecutor
from ecospheric_harness.workspace import WorkspaceManager
from ecospheric_harness.intents import IntentEntry, RegisteredTool, ResolutionError
from ecospheric_harness.menu import available_intents
from ecospheric_harness.orchestrator import Orchestrator
from ecospheric_harness.preflight import PreflightChecker
from ecospheric_harness.registry import ToolRegistry
from ecospheric_harness.resolver import IntentResolver
from ecospheric_harness.result import StepRecord


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def _cmd(
    name: str,
    *,
    category: str = "",
    data_type: str = "any",
    input_formats: list[str] | None = None,
    requires_planar_crs: bool = False,
    parameters: list[ParameterDescriptor] | None = None,
) -> CommandDescriptor:
    if parameters is None and input_formats:
        # Commands that accept input artifacts need an "input" parameter
        # for the executor's _route_input to work.
        parameters = [ParameterDescriptor(name="input", description="input file", type="string", required=False)]
    return CommandDescriptor(
        name=name,
        description=f"{name} command",
        category=category,
        parameters=parameters or [],
        input_formats=input_formats if input_formats is not None else [],
        data_type=data_type,
        requires_planar_crs=requires_planar_crs,
    )


def _tool(name: str, commands: list[CommandDescriptor]) -> RegisteredTool:
    return RegisteredTool(name=name, version="1.0", binary=name, commands=commands)


def _artifact(tmp_path: Path, name: str, **kwargs: Any) -> Artifact:
    p = tmp_path / name
    p.write_bytes(b"data")
    defaults = dict(
        path=p,
        envelope={"status": "success", "data": {}},
        format="geotiff",
        data_type="raster",
        crs=None,
        bbox=None,
        step_number=0,
    )
    defaults.update(kwargs)
    return Artifact(**defaults)  # type: ignore[arg-type]


def _model_response(intent: str, **extra: Any) -> dict[str, Any]:
    args: dict[str, Any] = {"intent": intent, **extra}
    return {
        "tool_calls": [{
            "id": "c1",
            "type": "function",
            "function": {
                "name": "emit_intent",
                "arguments": json.dumps(args),
            },
        }],
    }


def _stac_envelope(items: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "status": "success",
        "data": {
            "format": "json",
            "data_type": "metadata",
            "source": "@stac",
            "total_count": len(items),
            "items": items,
        },
    }


def _vector_envelope() -> dict[str, Any]:
    return {
        "status": "success",
        "data": {
            "format": "geojson",
            "data_type": "vector",
            "source": "@osm",
            "feature_count": 100,
            "crs": "EPSG:4326",
            "bounds": [-121.5, 38.2, -121.3, 38.4],
        },
    }


def _raster_envelope(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "status": "success",
        "data": {"format": "geotiff", "data_type": "raster"},
    }
    base["data"].update(overrides)
    return base


def _httpx_post(responses: list[dict[str, Any]]) -> MagicMock:
    """Return a mock httpx whose .post() yields successive model responses."""
    mock = MagicMock()
    mock.post.side_effect = [
        MagicMock(
            json=MagicMock(return_value={"choices": [{"message": r}]}),
            raise_for_status=MagicMock(),
        )
        for r in responses
    ]
    return mock


class _MockSubprocess:
    """Records calls and returns canned envelopes keyed by substring match."""

    def __init__(self, responses: dict[str, dict[str, Any]]) -> None:
        self.responses = responses
        self.calls: list[list[str]] = []

    def __call__(self, args: Any, **kwargs: Any) -> subprocess.CompletedProcess[Any]:
        self.calls.append(list(args))
        for key, resp in self.responses.items():
            if key in args:
                return subprocess.CompletedProcess(
                    args, 0, json.dumps(resp).encode(), b"",
                )
        return subprocess.CompletedProcess(
            args, 1,
            b'{"status":"error","error":{"type":"not_found","message":"no mock"}}',
            b"",
        )


def _build_orchestrator(
    tmp_path: Path,
    catalog: list[IntentEntry],
    *,
    search_cap: int = 20,
    disk_limit_bytes: int = 10_000_000,
) -> tuple[Orchestrator, ArtifactRegistry]:
    config = HarnessConfig(model="test", search_cap=search_cap, workspace_root=tmp_path)
    ws = WorkspaceManager(tmp_path, disk_limit_bytes=disk_limit_bytes)
    artifact_registry = ArtifactRegistry(workspace=ws, disk_limit_bytes=disk_limit_bytes)
    resolver = IntentResolver(catalog)
    executor = ToolExecutor()
    shared_steps: list[StepRecord] = []
    ws_integ = WorkspaceManager(tmp_path, disk_limit_bytes=disk_limit_bytes)
    corrections = CorrectionHandler(artifact_registry, shared_steps, executor, resolver, workspace=ws_integ)
    orch = Orchestrator(
        config=config,
        registry=ToolRegistry(),
        resolver=resolver,
        validator=MagicMock(validate=MagicMock(return_value=MagicMock(ok=True, errors=[]))),
        executor=executor,
        artifact_registry=artifact_registry,
        preflight=MagicMock(
            check_planar_crs=MagicMock(return_value=MagicMock(ok=True)),
            check_disk=MagicMock(return_value=MagicMock(ok=True)),
        ),
        corrections=corrections,
        catalog=catalog,
        workspace=ws_integ,
    )
    # Share the same steps list between orchestrator and corrections handler.
    orch._steps = shared_steps
    return orch, artifact_registry


def _orch_run(
    orch: Orchestrator,
    mock_sub: _MockSubprocess,
    mock_httpx: MagicMock,
    prompt: str = "test",
) -> Any:
    with (
        patch("ecospheric_harness.orchestrator.httpx", mock_httpx),
        patch("ecospheric_harness.orchestrator.available_intents", return_value=[]),
        patch("ecospheric_harness.executor.subprocess.run", mock_sub),
    ):
        return orch.run(prompt)


# ---------------------------------------------------------------------------
# AC9: vector pipeline — search → buffer
# ---------------------------------------------------------------------------


class TestVectorPipelineSearchToBuffer:
    def test_two_artifacts_with_correct_provenance(self, tmp_path: Path) -> None:
        edd = _tool("edd", [_cmd("search", data_type="any", input_formats=[])])
        ese = _tool("ese", [_cmd("vector buffer", data_type="vector", input_formats=["geojson"])])
        catalog = [
            IntentEntry("search_osm", "search", edd, edd.commands[0], []),
            IntentEntry("buffer", "buffer", ese, ese.commands[0], []),
        ]
        orch, artifacts = _build_orchestrator(tmp_path, catalog)

        mock_sub = _MockSubprocess({
            "search": _vector_envelope(),
            "buffer": _raster_envelope(format="geojson", data_type="vector"),
        })
        mock_httpx = _httpx_post([
            _model_response("search_osm", params={"bbox": "-121,38,-120,39"}),
            _model_response("buffer", params={"distance": 100}),
            _model_response("complete", summary="done"),
        ])

        result = _orch_run(orch, mock_sub, mock_httpx)

        assert len(result.steps) == 2
        assert artifacts.current is not None
        assert len(result.provenance_chain) == 2
        assert result.provenance_chain[0]["intent"] == "search_osm"
        assert result.provenance_chain[1]["intent"] == "buffer"


# ---------------------------------------------------------------------------
# AC10: raster pipeline — search_stac → fetch → clip → reproject
# ---------------------------------------------------------------------------


class TestRasterPipelineSearchFetchClipReproject:
    def test_four_steps_executed(self, tmp_path: Path) -> None:
        edd = _tool("edd", [
            _cmd("search", data_type="any", input_formats=[]),
            _cmd("fetch", data_type="raster", input_formats=[], parameters=[
                ParameterDescriptor(name="input", description="input file", type="string", required=False),
            ]),
        ])
        ese = _tool("ese", [
            _cmd("raster clip", data_type="raster", input_formats=["geotiff"]),
            _cmd("raster reproject", data_type="raster", input_formats=["geotiff"]),
        ])
        catalog = [
            IntentEntry("search_stac", "search stac", edd, edd.commands[0], []),
            IntentEntry("fetch", "fetch", edd, edd.commands[1], ["item", "asset"]),
            IntentEntry("clip", "clip", ese, ese.commands[0], []),
            IntentEntry("reproject", "reproject", ese, ese.commands[1], []),
        ]
        orch, _ = _build_orchestrator(tmp_path, catalog)

        items = [{"id": f"S2_{i}", "title": f"Scene {i}", "assets": ["visual"], "bbox": [-121, 38, -120, 39]} for i in range(3)]
        mock_sub = _MockSubprocess({
            "search": _stac_envelope(items),
            "fetch": _raster_envelope(),
            "clip": _raster_envelope(),
            "reproject": _raster_envelope(crs="EPSG:3857"),
        })
        mock_httpx = _httpx_post([
            _model_response("search_stac", params={"bbox": "-121,38,-120,39"}),
            _model_response("fetch", params={"item": "S2_0", "asset": "visual"}),
            _model_response("clip", params={"bbox": "-121.1,38.1,-120.9,38.3"}),
            _model_response("reproject", params={"to": "EPSG:3857"}),
            _model_response("complete", summary="done"),
        ])

        result = _orch_run(orch, mock_sub, mock_httpx)

        assert len(result.steps) == 4
        assert [s.intent for s in result.steps] == [
            "search_stac", "fetch", "clip", "reproject",
        ]


# ---------------------------------------------------------------------------
# AC37, AC39: undo → redo with new params
# ---------------------------------------------------------------------------


class TestUndoRedoPostUndoPath:
    def test_clip_undone_redo_in_provenance(self, tmp_path: Path) -> None:
        edd = _tool("edd", [_cmd("fetch", data_type="raster", input_formats=[], parameters=[
            ParameterDescriptor(name="input", description="input file", type="string", required=False),
        ])])
        ese = _tool("ese", [_cmd("raster clip", data_type="raster", input_formats=["geotiff"])])
        catalog = [
            IntentEntry("fetch", "fetch", edd, edd.commands[0], ["item", "asset"]),
            IntentEntry("clip", "clip", ese, ese.commands[0], []),
        ]
        orch, _ = _build_orchestrator(tmp_path, catalog)

        mock_sub = _MockSubprocess({
            "fetch": _raster_envelope(),
            "clip": _raster_envelope(),
        })
        mock_httpx = _httpx_post([
            _model_response("fetch", params={"item": "S2_0", "asset": "visual"}),
            _model_response("clip", params={"bbox": "-121,38,-120,39"}),
            _model_response("undo"),
            _model_response("redo", params={"bbox": "-121.1,38.1,-120.9,38.3"}),
            _model_response("complete", summary="done"),
        ])

        result = _orch_run(orch, mock_sub, mock_httpx)

        # fetch + clip + redo = 3 steps recorded (clip undone)
        assert len(result.steps) == 3
        assert result.steps[1].undone is True
        # provenance = [fetch, redo]
        assert len(result.provenance_chain) == 2
        assert result.provenance_chain[0]["intent"] == "fetch"
        assert result.provenance_chain[1]["params"]["bbox"] == "-121.1,38.1,-120.9,38.3"


# ---------------------------------------------------------------------------
# AC40: undo after redo — both undone
# ---------------------------------------------------------------------------


class TestUndoAfterRedo:
    def test_clip_and_redo_both_undone(self, tmp_path: Path) -> None:
        edd = _tool("edd", [_cmd("fetch", data_type="raster", input_formats=[], parameters=[
            ParameterDescriptor(name="input", description="input file", type="string", required=False),
        ])])
        ese = _tool("ese", [_cmd("raster clip", data_type="raster", input_formats=["geotiff"])])
        catalog = [
            IntentEntry("fetch", "fetch", edd, edd.commands[0], ["item", "asset"]),
            IntentEntry("clip", "clip", ese, ese.commands[0], []),
        ]
        orch, _ = _build_orchestrator(tmp_path, catalog)

        mock_sub = _MockSubprocess({
            "fetch": _raster_envelope(),
            "clip": _raster_envelope(),
        })
        mock_httpx = _httpx_post([
            _model_response("fetch", params={"item": "S2_0", "asset": "visual"}),
            _model_response("clip", params={"bbox": "-121,38,-120,39"}),
            _model_response("undo"),
            _model_response("redo", params={"bbox": "-121.1,38.1,-120.9,38.3"}),
            _model_response("undo"),
            _model_response("complete", summary="done"),
        ])

        result = _orch_run(orch, mock_sub, mock_httpx)

        # Both clip (step 2) and redo (step 3) should be undone.
        undone_steps = [s for s in result.steps if s.undone]
        assert len(undone_steps) == 2
        assert len(result.provenance_chain) == 1
        assert result.provenance_chain[0]["intent"] == "fetch"


# ---------------------------------------------------------------------------
# AC41: planar CRS rejection
# ---------------------------------------------------------------------------


class TestPlanarCRSRejection:
    def test_geographic_crs_rejected(self, tmp_path: Path) -> None:
        art = _artifact(tmp_path, "a.bin", crs="EPSG:4326")
        cmd = _cmd("distance", requires_planar_crs=True)
        checker = PreflightChecker(
            registry=MagicMock(), workspace=WorkspaceManager(tmp_path, disk_limit_bytes=10_000_000),
        )
        result = checker.check_planar_crs(cmd, art)

        assert result.ok is False
        assert "geographic" in result.error.lower() or "planar" in result.error.lower()


# ---------------------------------------------------------------------------
# AC42: disk limit rejection
# ---------------------------------------------------------------------------


class TestDiskLimitRejection:
    def test_small_limit_rejects(self, tmp_path: Path) -> None:
        art = _artifact(tmp_path, "big.bin")
        art.path.write_bytes(b"x" * 2000)

        ws = WorkspaceManager(tmp_path, disk_limit_bytes=500)
        registry = ArtifactRegistry(workspace=ws, disk_limit_bytes=500)
        checker = PreflightChecker(registry=registry, workspace=ws)

        result = checker.check_disk(input_artifact=art)

        assert result.ok is False
        assert "insufficient" in result.error.lower() or "disk" in result.error.lower()


# ---------------------------------------------------------------------------
# AC43: subprocess timeout
# ---------------------------------------------------------------------------


class TestSubprocessTimeout:
    def test_timeout_produces_error_envelope(self, tmp_path: Path) -> None:
        executor = ToolExecutor(subprocess_timeout=1)
        cmd = _cmd("raster clip", data_type="raster", input_formats=["geotiff"])
        tool = _tool("ese", [cmd])

        with patch("ecospheric_harness.executor.subprocess.run") as mock_run:
            mock_run.side_effect = subprocess.TimeoutExpired(cmd="ese", timeout=1)
            result = executor.execute(tool, cmd, {}, None, WorkspaceManager(tmp_path, disk_limit_bytes=10_000_000))

        assert result.envelope["status"] == "error"
        assert result.envelope["error"]["type"] == "timeout"


# ---------------------------------------------------------------------------
# AC33: format normalization
# ---------------------------------------------------------------------------


class TestFormatNormalization:
    def test_tif_matches_geotiff(self) -> None:
        art = MagicMock(format="tif", data_type="raster")
        cmd = _cmd("clip", data_type="raster", input_formats=["geotiff"])
        entry = IntentEntry("clip", "clip", MagicMock(), cmd, [])

        options = available_intents([entry], art, MagicMock())

        assert any(o.intent == "clip" for o in options)


# ---------------------------------------------------------------------------
# AC44: search result cap at 20
# ---------------------------------------------------------------------------


class TestSearchResultCap:
    def test_47_items_capped_at_20(self, tmp_path: Path) -> None:
        edd = _tool("edd", [_cmd("search", data_type="any", input_formats=[])])
        catalog = [IntentEntry("search_stac", "search", edd, edd.commands[0], [])]
        orch, _ = _build_orchestrator(tmp_path, catalog, search_cap=20)

        items = [
            {"id": f"item_{i}", "title": f"I{i}", "assets": ["a"], "bbox": [-121, 38, -120, 39]}
            for i in range(47)
        ]
        mock_sub = _MockSubprocess({"search": _stac_envelope(items)})
        mock_httpx = _httpx_post([
            _model_response("search_stac", params={"bbox": "-121,38,-120,39"}),
            _model_response("complete", summary="done"),
        ])

        with (
            patch("ecospheric_harness.orchestrator.httpx", mock_httpx),
            patch("ecospheric_harness.orchestrator.available_intents", return_value=[]),
            patch("ecospheric_harness.executor.subprocess.run", mock_sub),
        ):
            turn_states: list[dict[str, Any]] = []
            orig = Orchestrator._build_turn_state

            def capture(self: Orchestrator, status: str, step: int, intent: str) -> dict[str, Any]:
                state = orig(self, status, step, intent)
                turn_states.append(state)
                return state

            with patch.object(Orchestrator, "_build_turn_state", capture):
                orch.run("search")

        sr_states = [s for s in turn_states if "search_results" in s]
        assert len(sr_states) >= 1
        sr = sr_states[0]["search_results"]
        assert sr["total_count"] == 47
        assert sr["returned_count"] == 20
        assert len(sr["items"]) == 20


# ---------------------------------------------------------------------------
# AC48: fetch without item/asset → ResolutionError
# ---------------------------------------------------------------------------


class TestFetchEnforcement:
    def test_fetch_missing_item_asset(self, tmp_path: Path) -> None:
        cmd = _cmd("fetch", input_formats=[])
        tool = _tool("edd", [cmd])
        catalog = [IntentEntry("fetch", "fetch", tool, cmd, ["item", "asset"])]
        resolver = IntentResolver(catalog)

        result = resolver.resolve("fetch", {}, None)

        assert isinstance(result, ResolutionError)
        assert "item" in result.message and "asset" in result.message


# ---------------------------------------------------------------------------
# AC49: intent overrides in catalog
# ---------------------------------------------------------------------------


class TestIntentOverrides:
    def test_proj_distance_maps_to_geodesic(self, tmp_path: Path) -> None:
        with patch("ecospheric_harness.registry.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(
                stdout=json.dumps({
                    "status": "success",
                    "data": {"commands": [{
                        "name": "proj distance",
                        "description": "Geodesic distance",
                        "category": "proj",
                        "parameters": [],
                        "input_formats": ["geotiff"],
                        "data_type": "raster",
                    }]},
                    "tool_version": "1.0",
                }),
                returncode=0,
            )
            tools = ToolRegistry.discover_tools(["ese"])

        catalog = ToolRegistry.build_catalog(tools, {})
        intents = [e.intent for e in catalog]

        assert "geodesic_distance" in intents

    def test_vector_distance_stays_distance(self, tmp_path: Path) -> None:
        with patch("ecospheric_harness.registry.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(
                stdout=json.dumps({
                    "status": "success",
                    "data": {"commands": [{
                        "name": "vector distance",
                        "description": "Vector distance",
                        "category": "vector",
                        "parameters": [],
                        "input_formats": ["geojson"],
                        "data_type": "vector",
                    }]},
                    "tool_version": "1.0",
                }),
                returncode=0,
            )
            tools = ToolRegistry.discover_tools(["ese"])

        catalog = ToolRegistry.build_catalog(tools, {})
        intents = [e.intent for e in catalog]

        assert "distance" in intents


# ---------------------------------------------------------------------------
# AC50: extra envelope keys ignored
# ---------------------------------------------------------------------------


class TestExtraEnvelopeKeysIgnored:
    def test_ese_version_key_no_error(self, tmp_path: Path) -> None:
        edd = _tool("edd", [_cmd("fetch", data_type="raster", input_formats=[], parameters=[
            ParameterDescriptor(name="input", description="input file", type="string", required=False),
        ])])
        ese = _tool("ese", [_cmd("raster clip", data_type="raster", input_formats=["geotiff"])])
        catalog = [
            IntentEntry("fetch", "fetch", edd, edd.commands[0], ["item", "asset"]),
            IntentEntry("clip", "clip", ese, ese.commands[0], []),
        ]
        orch, _ = _build_orchestrator(tmp_path, catalog)

        envelope_with_extra = {
            "status": "success",
            "ese_version": "0.5.0",
            "tool_name": "ese",
            "data": {"format": "geotiff", "data_type": "raster"},
        }
        mock_sub = _MockSubprocess({
            "fetch": _raster_envelope(),
            "clip": envelope_with_extra,
        })
        mock_httpx = _httpx_post([
            _model_response("fetch", params={"item": "S2_0", "asset": "visual"}),
            _model_response("clip", params={}),
            _model_response("complete", summary="done"),
        ])

        result = _orch_run(orch, mock_sub, mock_httpx)

        assert len(result.steps) == 2
        assert result.steps[1].envelope["ese_version"] == "0.5.0"


# ---------------------------------------------------------------------------
# AC47: type-driven serialization
# ---------------------------------------------------------------------------


class TestTypeDrivenSerialization:
    def test_string_list_comma_join(self, tmp_path: Path) -> None:
        executor = ToolExecutor()
        cmd = _cmd(
            "clip",
            parameters=[ParameterDescriptor(name="--bbox", description="", type="string", required=False)],
        )
        args = executor._serialize_params({"bbox": [-121.5, 38.2, -121.3, 38.4]}, cmd)

        assert "--bbox" in args
        idx = args.index("--bbox")
        assert args[idx + 1] == "-121.5,38.2,-121.3,38.4"

    def test_array_list_space_separated(self, tmp_path: Path) -> None:
        executor = ToolExecutor()
        cmd = _cmd(
            "merge",
            parameters=[ParameterDescriptor(name="--inputs", description="", type="array", required=False)],
        )
        args = executor._serialize_params({"inputs": ["a.tif", "b.tif"]}, cmd)

        assert "--inputs" in args
        idx = args.index("--inputs")
        assert args[idx + 1] == "a.tif"
        assert args[idx + 2] == "b.tif"


# ---------------------------------------------------------------------------
# AC31: param name reverse map
# ---------------------------------------------------------------------------


class TestParamNameReverseMap:
    def test_min_area_maps_to_cli_flag(self, tmp_path: Path) -> None:
        executor = ToolExecutor()
        cmd = _cmd(
            "simplify",
            parameters=[ParameterDescriptor(name="--min-area", description="", type="number", required=False)],
        )
        args = executor._serialize_params({"min_area": 500}, cmd)

        assert "--min-area" in args
        idx = args.index("--min-area")
        assert args[idx + 1] == "500"


# ---------------------------------------------------------------------------
# AC30: boolean serialization
# ---------------------------------------------------------------------------


class TestBooleanSerialization:
    def test_true_is_bare_flag(self, tmp_path: Path) -> None:
        executor = ToolExecutor()
        cmd = _cmd(
            "clip",
            parameters=[ParameterDescriptor(name="--keep-nodata", description="", type="boolean", required=False)],
        )
        args = executor._serialize_params({"keep_nodata": True}, cmd)

        assert "--keep-nodata" in args
        # Should not have a value after it
        idx = args.index("--keep-nodata")
        assert idx + 1 == len(args) or args[idx + 1].startswith("--")

    def test_false_is_omitted(self, tmp_path: Path) -> None:
        executor = ToolExecutor()
        cmd = _cmd(
            "clip",
            parameters=[ParameterDescriptor(name="--keep-nodata", description="", type="boolean", required=False)],
        )
        args = executor._serialize_params({"keep_nodata": False}, cmd)

        assert "--keep-nodata" not in args


# ---------------------------------------------------------------------------
# AC36: diagnostic exclusion
# ---------------------------------------------------------------------------


class TestDiagnosticExclusion:
    def test_diagnostic_not_in_available_intents(self, tmp_path: Path) -> None:
        diag_cmd = _cmd("doctor", category="diagnostic", input_formats=[])
        normal_cmd = _cmd("fetch", input_formats=[])
        tool = _tool("edd", [diag_cmd, normal_cmd])
        catalog = [
            IntentEntry("doctor", "diagnose", tool, diag_cmd, []),
            IntentEntry("fetch", "fetch", tool, normal_cmd, []),
        ]

        options = available_intents(catalog, None, MagicMock())
        intents = [o.intent for o in options]

        assert "doctor" not in intents
        assert "fetch" in intents


# ---------------------------------------------------------------------------
# AC38: single-word intent stays unchanged
# ---------------------------------------------------------------------------


class TestSingleWordIntentRule:
    def test_fetch_stays_fetch(self, tmp_path: Path) -> None:
        with patch("ecospheric_harness.registry.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(
                stdout=json.dumps({
                    "status": "success",
                    "data": {"commands": [{
                        "name": "fetch",
                        "description": "Fetch data",
                        "category": "edd",
                        "parameters": [],
                        "input_formats": [],
                        "data_type": "any",
                    }]},
                    "tool_version": "1.0",
                }),
                returncode=0,
            )
            tools = ToolRegistry.discover_tools(["edd"])

        catalog = ToolRegistry.build_catalog(tools, {})
        intents = [e.intent for e in catalog]

        assert "fetch" in intents
