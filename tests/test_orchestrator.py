"""Tests for the multi-turn orchestrator.

Covers AC3, AC5–AC11, AC18–AC20, AC34, AC44, AC50 and the
failed_attempts counter.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

from etp.describe import CommandDescriptor, ParameterDescriptor

from ecospheric_harness.artifact import ArtifactManager
from ecospheric_harness.config import HarnessConfig
from ecospheric_harness.corrections import CorrectionHandler
from ecospheric_harness.executor import ToolExecutor
from ecospheric_harness.intents import (
    IntentEntry,
    IntentOption,
    RegisteredTool,
    ResolvedCall,
    ResolutionError,
)
from ecospheric_harness.orchestrator import Orchestrator
from ecospheric_harness.preflight import PreflightChecker
from ecospheric_harness.registry import ToolRegistry
from ecospheric_harness.resolver import IntentResolver
from ecospheric_harness.result import PipelineResult, StepRecord
from ecospheric_harness.validator import SchemaValidator, ValidationResult


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _make_mock_orchestrator(
    tmp_path: Path,
    *,
    max_turns: int = 20,
    search_cap: int = 20,
    preflight_ok: bool = True,
    validation_ok: bool = True,
    executor_succeed: bool = True,
    executor_error_msg: str = "tool failed",
    executor_envelope: dict[str, Any] | None = None,
) -> tuple[Orchestrator, MagicMock, MagicMock, MagicMock]:
    """Build an Orchestrator with all-MagicMock dependencies."""
    config = HarnessConfig(
        model="test-model",
        max_turns=max_turns,
        search_cap=search_cap,
        workdir=tmp_path,
    )
    registry = MagicMock(spec=ToolRegistry)
    resolver = MagicMock(spec=IntentResolver)
    validator = MagicMock(spec=SchemaValidator)
    executor = MagicMock(spec=ToolExecutor)
    artifacts = ArtifactManager(workdir=tmp_path, disk_limit_bytes=10_000_000)
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
        validator.validate.return_value = ValidationResult(ok=False, errors=["invalid param 'x'"])
    if preflight_ok:
        preflight.check_planar_crs.return_value = MagicMock(ok=True)
        preflight.check_disk.return_value = MagicMock(ok=True)
    else:
        preflight.check_planar_crs.return_value = MagicMock(ok=False, error="requires planar CRS")
        preflight.check_disk.return_value = MagicMock(ok=True)

    if executor_envelope is not None:
        envelope = executor_envelope
    elif executor_succeed:
        envelope = {
            "status": "success",
            "data": {"format": "geotiff", "data_type": "raster"},
        }
    else:
        envelope = {
            "status": "error",
            "error": {"type": "execution_error", "message": executor_error_msg},
        }

    output_file = tmp_path / "output.bin"
    output_file.write_bytes(b"output")
    executor.execute.return_value = MagicMock(
        envelope=envelope,
        returncode=0 if executor_succeed else 1,
        output_path=output_file,
    )

    orch = Orchestrator(
        config=config,
        registry=registry,
        resolver=resolver,
        validator=validator,
        executor=executor,
        artifacts=artifacts,
        preflight=preflight,
        corrections=corrections,
        catalog=catalog,
    )
    return orch, artifacts, resolver, corrections


def _make_model_response(intent: str, **extra: Any) -> dict[str, Any]:
    """Build a mock OpenRouter model response with an emit_intent tool call."""
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


def _make_stac_envelope(items: list[dict[str, Any]]) -> dict[str, Any]:
    """Build a mock STAC search envelope."""
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


def _make_vector_search_envelope() -> dict[str, Any]:
    """Build a mock direct-data (vector) search envelope."""
    return {
        "status": "success",
        "data": {
            "format": "geojson",
            "data_type": "vector",
            "source": "@osm",
            "feature_count": 342,
            "crs": "EPSG:4326",
            "bounds": [-121.5, 38.2, -121.3, 38.4],
        },
    }


# ---------------------------------------------------------------------------
# AC5: Single-step success
# ---------------------------------------------------------------------------


class TestSingleStepSuccess:
    @patch("ecospheric_harness.orchestrator.httpx")
    @patch("ecospheric_harness.orchestrator.available_intents")
    def test_single_step_returns_pipeline_result(
        self, mock_menu: MagicMock, mock_httpx: MagicMock, tmp_path: Path,
    ) -> None:
        """AC5: one intent → resolved → executed → complete."""
        orch, _, _, _ = _make_mock_orchestrator(tmp_path)

        mock_menu.return_value = [IntentOption(
            intent="clip", description="Clip raster", required_params=[],
        )]
        mock_httpx.post.side_effect = [
            MagicMock(
                json=MagicMock(return_value={"choices": [{"message": _make_model_response("clip")}]}),
                raise_for_status=MagicMock(),
            ),
            MagicMock(
                json=MagicMock(return_value={
                    "choices": [{"message": _make_model_response("complete", summary="done")}],
                }),
                raise_for_status=MagicMock(),
            ),
        ]

        result = orch.run("test prompt")

        assert isinstance(result, PipelineResult)
        assert len(result.steps) == 1
        assert result.steps[0].intent == "clip"
        assert result.steps[0].status == "success"


# ---------------------------------------------------------------------------
# AC5: Two-step pipeline
# ---------------------------------------------------------------------------


class TestTwoStepPipeline:
    @patch("ecospheric_harness.orchestrator.httpx")
    @patch("ecospheric_harness.orchestrator.available_intents")
    def test_two_steps_both_stored(
        self, mock_menu: MagicMock, mock_httpx: MagicMock, tmp_path: Path,
    ) -> None:
        """AC5: step1, step2, complete → both stored, provenance correct."""
        orch, artifacts, _, _ = _make_mock_orchestrator(tmp_path)

        mock_menu.return_value = [IntentOption(
            intent="clip", description="Clip raster", required_params=[],
        )]
        mock_httpx.post.side_effect = [
            MagicMock(
                json=MagicMock(return_value={"choices": [{"message": _make_model_response("clip")}]}),
                raise_for_status=MagicMock(),
            ),
            MagicMock(
                json=MagicMock(return_value={"choices": [{"message": _make_model_response("clip")}]}),
                raise_for_status=MagicMock(),
            ),
            MagicMock(
                json=MagicMock(return_value={
                    "choices": [{"message": _make_model_response("complete", summary="done")}],
                }),
                raise_for_status=MagicMock(),
            ),
        ]

        result = orch.run("step1")

        assert len(result.steps) == 2
        assert artifacts.current is not None
        assert artifacts.previous is not None
        assert artifacts.current is not artifacts.previous
        assert result.final_artifact is artifacts.current
        assert len(result.provenance_chain) == 2


# ---------------------------------------------------------------------------
# AC6: Failed step preserves artifacts
# ---------------------------------------------------------------------------


class TestFailedStepPreservesArtifacts:
    @patch("ecospheric_harness.orchestrator.httpx")
    @patch("ecospheric_harness.orchestrator.available_intents")
    def test_failed_step_artifacts_unchanged(
        self, mock_menu: MagicMock, mock_httpx: MagicMock, tmp_path: Path,
    ) -> None:
        """AC6: step fails → artifacts unchanged, model retries."""
        orch, artifacts, _, _ = _make_mock_orchestrator(
            tmp_path, executor_succeed=False,
        )

        mock_menu.return_value = [IntentOption(
            intent="clip", description="Clip raster", required_params=[],
        )]
        # First call: clip → fails.  Second call: complete.
        mock_httpx.post.side_effect = [
            MagicMock(
                json=MagicMock(return_value={"choices": [{"message": _make_model_response("clip")}]}),
                raise_for_status=MagicMock(),
            ),
            MagicMock(
                json=MagicMock(return_value={
                    "choices": [{"message": _make_model_response("complete", summary="gave up")}],
                }),
                raise_for_status=MagicMock(),
            ),
        ]

        result = orch.run("test")

        assert artifacts.current is None  # no artifact stored
        assert result.final_artifact is None


# ---------------------------------------------------------------------------
# AC11: STAC search — no artifact stored
# ---------------------------------------------------------------------------


class TestSearchSTAC:
    @patch("ecospheric_harness.orchestrator.httpx")
    @patch("ecospheric_harness.orchestrator.available_intents")
    def test_stac_search_no_artifact(
        self, mock_menu: MagicMock, mock_httpx: MagicMock, tmp_path: Path,
    ) -> None:
        """AC11: STAC search → turn state with items, no artifact stored."""
        items = [
            {"id": "S2B_001", "title": "Scene 1", "assets": ["visual"], "bbox": [-121, 38, -120, 39]},
            {"id": "S2B_002", "title": "Scene 2", "assets": ["B01"], "bbox": [-121, 38, -120, 39]},
        ]
        envelope = _make_stac_envelope(items)

        orch, artifacts, _, _ = _make_mock_orchestrator(
            tmp_path, executor_envelope=envelope,
        )

        mock_menu.return_value = [IntentOption(
            intent="search_stac", description="Search STAC", required_params=["bbox"],
        )]
        mock_httpx.post.side_effect = [
            MagicMock(
                json=MagicMock(return_value={
                    "choices": [{"message": _make_model_response("search_stac", params={"bbox": "-121,38,-120,39"})}],
                }),
                raise_for_status=MagicMock(),
            ),
            MagicMock(
                json=MagicMock(return_value={
                    "choices": [{"message": _make_model_response("complete", summary="searched")}],
                }),
                raise_for_status=MagicMock(),
            ),
        ]

        result = orch.run("search STAC")

        assert result.final_artifact is None  # no artifact stored
        assert len(result.steps) == 1
        assert result.steps[0].is_search is True


# ---------------------------------------------------------------------------
# AC11: Direct-data search (OSM) — artifact stored
# ---------------------------------------------------------------------------


class TestSearchOSM:
    @patch("ecospheric_harness.orchestrator.httpx")
    @patch("ecospheric_harness.orchestrator.available_intents")
    def test_vector_search_stores_artifact(
        self, mock_menu: MagicMock, mock_httpx: MagicMock, tmp_path: Path,
    ) -> None:
        """AC11: OSM search → artifact stored, menu narrowed."""
        envelope = _make_vector_search_envelope()
        orch, artifacts, _, _ = _make_mock_orchestrator(
            tmp_path, executor_envelope=envelope,
        )

        mock_menu.return_value = [IntentOption(
            intent="search_osm", description="Search OSM", required_params=["bbox"],
        )]
        mock_httpx.post.side_effect = [
            MagicMock(
                json=MagicMock(return_value={
                    "choices": [{"message": _make_model_response("search_osm", params={"bbox": "-121,38,-120,39"})}],
                }),
                raise_for_status=MagicMock(),
            ),
            MagicMock(
                json=MagicMock(return_value={
                    "choices": [{"message": _make_model_response("complete", summary="found data")}],
                }),
                raise_for_status=MagicMock(),
            ),
        ]

        result = orch.run("search OSM")

        assert result.final_artifact is not None
        assert result.final_artifact.format == "geojson"
        assert result.final_artifact.data_type == "vector"
        assert artifacts.current is not None


# ---------------------------------------------------------------------------
# AC18: Complete intent
# ---------------------------------------------------------------------------


class TestCompleteIntent:
    @patch("ecospheric_harness.orchestrator.httpx")
    @patch("ecospheric_harness.orchestrator.available_intents")
    def test_complete_persists_and_returns(
        self, mock_menu: MagicMock, mock_httpx: MagicMock, tmp_path: Path,
    ) -> None:
        """AC18: complete → persists artifact, writes provenance, returns PipelineResult."""
        orch, _, _, _ = _make_mock_orchestrator(tmp_path)

        mock_menu.return_value = [IntentOption(
            intent="clip", description="Clip raster", required_params=[],
        )]
        mock_httpx.post.side_effect = [
            MagicMock(
                json=MagicMock(return_value={"choices": [{"message": _make_model_response("clip")}]}),
                raise_for_status=MagicMock(),
            ),
            MagicMock(
                json=MagicMock(return_value={
                    "choices": [{"message": _make_model_response("complete", summary="all done")}],
                }),
                raise_for_status=MagicMock(),
            ),
        ]

        result = orch.run("do something")

        assert isinstance(result, PipelineResult)
        assert result.final_artifact is not None
        assert len(result.steps) == 1
        assert len(result.provenance_chain) == 1

    @patch("ecospheric_harness.orchestrator.httpx")
    @patch("ecospheric_harness.orchestrator.available_intents")
    def test_complete_persists_files_to_disk(
        self, mock_menu: MagicMock, mock_httpx: MagicMock, tmp_path: Path,
    ) -> None:
        """AC18: complete → persisted provenance.json, summary.json, and output to workdir."""
        orch, _, _, _ = _make_mock_orchestrator(tmp_path)

        mock_menu.return_value = [IntentOption(
            intent="clip", description="Clip raster", required_params=[],
        )]
        mock_httpx.post.side_effect = [
            MagicMock(
                json=MagicMock(return_value={"choices": [{"message": _make_model_response("clip")}]}),
                raise_for_status=MagicMock(),
            ),
            MagicMock(
                json=MagicMock(return_value={
                    "choices": [{"message": _make_model_response("complete", summary="all done")}],
                }),
                raise_for_status=MagicMock(),
            ),
        ]

        orch.run("do something")

        # Check provenance.json exists and is valid JSON
        provenance_path = tmp_path / "provenance.json"
        assert provenance_path.exists()
        provenance_data = json.loads(provenance_path.read_text())
        assert len(provenance_data) == 1

        # Check summary.json exists
        summary_path = tmp_path / "summary.json"
        assert summary_path.exists()
        summary_data = json.loads(summary_path.read_text())
        assert "summary" in summary_data

        # Check output file exists with correct extension
        output_dir = tmp_path / "output"
        assert output_dir.exists()
        output_files = list(output_dir.iterdir())
        assert len(output_files) == 1


# ---------------------------------------------------------------------------
# AC19: Failed intent
# ---------------------------------------------------------------------------


class TestFailedIntent:
    @patch("ecospheric_harness.orchestrator.httpx")
    @patch("ecospheric_harness.orchestrator.available_intents")
    def test_failed_returns_partial_result(
        self, mock_menu: MagicMock, mock_httpx: MagicMock, tmp_path: Path,
    ) -> None:
        """AC19: failed → returns partial result."""
        orch, _, _, _ = _make_mock_orchestrator(tmp_path)

        mock_menu.return_value = [IntentOption(
            intent="clip", description="Clip raster", required_params=[],
        )]
        mock_httpx.post.return_value = MagicMock(
            json=MagicMock(return_value={
                "choices": [{"message": _make_model_response("failed", reason="cannot proceed")}],
            }),
            raise_for_status=MagicMock(),
        )

        result = orch.run("test")

        assert isinstance(result, PipelineResult)
        assert result.final_artifact is None


# ---------------------------------------------------------------------------
# AC20: Max turns
# ---------------------------------------------------------------------------


class TestMaxTurns:
    @patch("ecospheric_harness.orchestrator.httpx")
    @patch("ecospheric_harness.orchestrator.available_intents")
    def test_max_turns_returns_partial(
        self, mock_menu: MagicMock, mock_httpx: MagicMock, tmp_path: Path,
    ) -> None:
        """AC20: max turns exceeded → partial result with 'pipeline incomplete'."""
        orch, _, _, _ = _make_mock_orchestrator(tmp_path, max_turns=2)

        mock_menu.return_value = [IntentOption(
            intent="clip", description="Clip raster", required_params=[],
        )]
        mock_httpx.post.return_value = MagicMock(
            json=MagicMock(return_value={"choices": [{"message": _make_model_response("clip")}]}),
            raise_for_status=MagicMock(),
        )

        result = orch.run("test")

        assert isinstance(result, PipelineResult)
        assert result.final_artifact is not None  # 2 clips executed


# ---------------------------------------------------------------------------
# AC34: can_undo in turn state
# ---------------------------------------------------------------------------


class TestCanUndoInTurnState:
    @patch("ecospheric_harness.orchestrator.httpx")
    @patch("ecospheric_harness.orchestrator.available_intents")
    def test_can_undo_false_then_true(
        self, mock_menu: MagicMock, mock_httpx: MagicMock, tmp_path: Path,
    ) -> None:
        """AC34: can_undo False initially, True after 2 steps."""
        turn_states: list[dict[str, Any]] = []
        original_build = Orchestrator._build_turn_state

        def capture_turn_state(
            self: Orchestrator, status: str, step: int, intent: str,
        ) -> dict[str, Any]:
            state = original_build(self, status, step, intent)
            turn_states.append(state)
            return state

        orch, _, _, _ = _make_mock_orchestrator(tmp_path)

        mock_menu.return_value = [IntentOption(
            intent="clip", description="Clip raster", required_params=[],
        )]
        mock_httpx.post.side_effect = [
            MagicMock(
                json=MagicMock(return_value={"choices": [{"message": _make_model_response("clip")}]}),
                raise_for_status=MagicMock(),
            ),
            MagicMock(
                json=MagicMock(return_value={"choices": [{"message": _make_model_response("clip")}]}),
                raise_for_status=MagicMock(),
            ),
            MagicMock(
                json=MagicMock(return_value={
                    "choices": [{"message": _make_model_response("complete", summary="done")}],
                }),
                raise_for_status=MagicMock(),
            ),
        ]

        with patch.object(Orchestrator, "_build_turn_state", capture_turn_state):
            orch.run("test")

        # After step 1: can_undo = False (only one artifact in window).
        # turn_states layout:
        #   [0] pre-step1 (loop start iter 0)
        #   [1] post-step1 (after dispatch, tool message appended)
        #   [2] pre-step2 (loop start iter 1)
        #   [3] post-step2 (after dispatch, can_undo = True)
        state_after_step1 = turn_states[1]
        assert state_after_step1["can_undo"] is False

        # After step 2: can_undo = True (previous exists).
        state_after_step2 = turn_states[3]
        assert state_after_step2["can_undo"] is True


# ---------------------------------------------------------------------------
# AC44: Search result cap
# ---------------------------------------------------------------------------


class TestSearchResultCap:
    @patch("ecospheric_harness.orchestrator.httpx")
    @patch("ecospheric_harness.orchestrator.available_intents")
    def test_stac_results_capped_at_search_cap(
        self, mock_menu: MagicMock, mock_httpx: MagicMock, tmp_path: Path,
    ) -> None:
        """AC44: STAC results capped at search_cap, results_file present."""
        # Create 5 items but cap at 3.
        items = [
            {"id": f"item_{i}", "title": f"Item {i}", "assets": ["visual"], "bbox": [-121, 38, -120, 39]}
            for i in range(5)
        ]
        envelope = _make_stac_envelope(items)

        orch, _, _, _ = _make_mock_orchestrator(
            tmp_path, executor_envelope=envelope, search_cap=3,
        )

        turn_states: list[dict[str, Any]] = []
        original_build = Orchestrator._build_turn_state

        def capture_turn_state(
            self: Orchestrator, status: str, step: int, intent: str,
        ) -> dict[str, Any]:
            state = original_build(self, status, step, intent)
            turn_states.append(state)
            return state

        mock_menu.return_value = [IntentOption(
            intent="search_stac", description="Search STAC", required_params=["bbox"],
        )]
        mock_httpx.post.side_effect = [
            MagicMock(
                json=MagicMock(return_value={
                    "choices": [{"message": _make_model_response("search_stac", params={"bbox": "-121,38,-120,39"})}],
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

        with patch.object(Orchestrator, "_build_turn_state", capture_turn_state):
            orch.run("search")

        # Find the turn state that has search_results.
        sr_states = [s for s in turn_states if "search_results" in s]
        assert len(sr_states) >= 1
        sr = sr_states[0]["search_results"]
        assert len(sr["items"]) == 3  # capped at search_cap
        assert "results_file" in sr


# ---------------------------------------------------------------------------
# AC50: Extra envelope keys ignored
# ---------------------------------------------------------------------------


class TestExtraEnvelopeKeys:
    @patch("ecospheric_harness.orchestrator.httpx")
    @patch("ecospheric_harness.orchestrator.available_intents")
    def test_ese_version_ignored(
        self, mock_menu: MagicMock, mock_httpx: MagicMock, tmp_path: Path,
    ) -> None:
        """AC50: extra envelope keys (ese_version) ignored without error."""
        envelope = {
            "status": "success",
            "ese_version": "0.5.0",
            "data": {"format": "geotiff", "data_type": "raster"},
        }
        orch, _, _, _ = _make_mock_orchestrator(
            tmp_path, executor_envelope=envelope,
        )

        mock_menu.return_value = [IntentOption(
            intent="clip", description="Clip raster", required_params=[],
        )]
        mock_httpx.post.side_effect = [
            MagicMock(
                json=MagicMock(return_value={"choices": [{"message": _make_model_response("clip")}]}),
                raise_for_status=MagicMock(),
            ),
            MagicMock(
                json=MagicMock(return_value={
                    "choices": [{"message": _make_model_response("complete", summary="done")}],
                }),
                raise_for_status=MagicMock(),
            ),
        ]

        result = orch.run("test")

        assert result.steps[0].envelope is not None
        assert result.steps[0].envelope["ese_version"] == "0.5.0"


# ---------------------------------------------------------------------------
# Invalid model response
# ---------------------------------------------------------------------------


class TestInvalidModelResponse:
    @patch("ecospheric_harness.orchestrator.httpx")
    @patch("ecospheric_harness.orchestrator.available_intents")
    def test_unparseable_retries(
        self, mock_menu: MagicMock, mock_httpx: MagicMock, tmp_path: Path,
    ) -> None:
        """Invalid model response → error returned to model, retries."""
        orch, _, _, _ = _make_mock_orchestrator(tmp_path)

        mock_menu.return_value = [IntentOption(
            intent="clip", description="Clip raster", required_params=[],
        )]
        # First call: no tool_calls.  Second call: valid.
        mock_httpx.post.side_effect = [
            MagicMock(
                json=MagicMock(return_value={"choices": [{"message": {"content": "hello"}}]}),
                raise_for_status=MagicMock(),
            ),
            MagicMock(
                json=MagicMock(return_value={
                    "choices": [{"message": _make_model_response("complete", summary="recovered")}],
                }),
                raise_for_status=MagicMock(),
            ),
        ]

        result = orch.run("test")

        assert isinstance(result, PipelineResult)
        assert mock_httpx.post.call_count == 2


# ---------------------------------------------------------------------------
# AC4: Schema validation failure
# ---------------------------------------------------------------------------


class TestSchemaValidationFailure:
    @patch("ecospheric_harness.orchestrator.httpx")
    @patch("ecospheric_harness.orchestrator.available_intents")
    def test_invalid_params_preserves_artifacts(
        self, mock_menu: MagicMock, mock_httpx: MagicMock, tmp_path: Path,
    ) -> None:
        """AC4: invalid params → rejection, artifacts preserved, model retries."""
        orch, artifacts, _, _ = _make_mock_orchestrator(
            tmp_path, validation_ok=False,
        )

        mock_menu.return_value = [IntentOption(
            intent="clip", description="Clip raster", required_params=[],
        )]
        # First call: clip → validation fails.  Second call: complete (gives up).
        mock_httpx.post.side_effect = [
            MagicMock(
                json=MagicMock(return_value={"choices": [{"message": _make_model_response("clip")}]}),
                raise_for_status=MagicMock(),
            ),
            MagicMock(
                json=MagicMock(return_value={
                    "choices": [{"message": _make_model_response("complete", summary="gave up")}],
                }),
                raise_for_status=MagicMock(),
            ),
        ]

        result = orch.run("test")

        assert artifacts.current is None  # no artifacts stored
        assert result.final_artifact is None
        assert len(result.steps) == 1  # 1 rejected step (not successful)
        assert result.steps[0].status == "rejected"


# ---------------------------------------------------------------------------
# Preflight rejection
# ---------------------------------------------------------------------------


class TestPreflightRejection:
    @patch("ecospheric_harness.orchestrator.httpx")
    @patch("ecospheric_harness.orchestrator.available_intents")
    def test_planar_crs_mismatch_preserves(
        self, mock_menu: MagicMock, mock_httpx: MagicMock, tmp_path: Path,
    ) -> None:
        """Preflight: planar CRS mismatch → error to model, artifacts preserved."""
        orch, artifacts, _, _ = _make_mock_orchestrator(tmp_path)

        preflight_result = MagicMock(ok=False, error="requires planar CRS")
        orch._preflight.check_planar_crs.return_value = preflight_result

        mock_menu.return_value = [IntentOption(
            intent="clip", description="Clip raster", required_params=[],
        )]
        mock_httpx.post.side_effect = [
            MagicMock(
                json=MagicMock(return_value={"choices": [{"message": _make_model_response("clip")}]}),
                raise_for_status=MagicMock(),
            ),
            MagicMock(
                json=MagicMock(return_value={
                    "choices": [{"message": _make_model_response("complete", summary="gave up")}],
                }),
                raise_for_status=MagicMock(),
            ),
        ]

        result = orch.run("test")

        assert artifacts.current is None
        assert result.final_artifact is None


# ---------------------------------------------------------------------------
# AC8: emit_intent enum repopulated
# ---------------------------------------------------------------------------


class TestEmitIntentEnumRepopulated:
    @patch("ecospheric_harness.orchestrator.httpx")
    @patch("ecospheric_harness.orchestrator.available_intents")
    def test_enum_changes_each_turn(
        self, mock_menu: MagicMock, mock_httpx: MagicMock, tmp_path: Path,
    ) -> None:
        """AC8: each turn gets different enum based on available_intents."""
        orch, _, _, _ = _make_mock_orchestrator(tmp_path)

        call_count = 0

        def menu_side_effect(*args: Any, **kwargs: Any) -> list[IntentOption]:
            nonlocal call_count
            call_count += 1
            if call_count <= 2:
                return [IntentOption(intent="clip", description="Clip", required_params=[])]
            return [IntentOption(intent="buffer", description="Buffer", required_params=[])]

        mock_menu.side_effect = menu_side_effect

        mock_httpx.post.side_effect = [
            MagicMock(
                json=MagicMock(return_value={"choices": [{"message": _make_model_response("clip")}]}),
                raise_for_status=MagicMock(),
            ),
            MagicMock(
                json=MagicMock(return_value={
                    "choices": [{"message": _make_model_response("complete", summary="done")}],
                }),
                raise_for_status=MagicMock(),
            ),
        ]

        orch.run("test")

        # Check tool definitions sent to model.
        calls = mock_httpx.post.call_args_list
        tool_def_1 = calls[0].kwargs["json"]["tools"][0]
        tool_def_2 = calls[1].kwargs["json"]["tools"][0]

        enum_1 = tool_def_1["function"]["parameters"]["properties"]["intent"]["enum"]
        enum_2 = tool_def_2["function"]["parameters"]["properties"]["intent"]["enum"]

        assert "clip" in enum_1
        assert "buffer" not in enum_1
        assert "buffer" in enum_2
        assert "clip" not in enum_2


# ---------------------------------------------------------------------------
# failed_attempts counter
# ---------------------------------------------------------------------------


class TestFailedAttemptsCounter:
    @patch("ecospheric_harness.orchestrator.httpx")
    @patch("ecospheric_harness.orchestrator.available_intents")
    def test_failed_redo_increments_counter(
        self, mock_menu: MagicMock, mock_httpx: MagicMock, tmp_path: Path,
    ) -> None:
        """Failed redo increments counter, in turn state when > 0."""
        orch, _, _, corrections = _make_mock_orchestrator(tmp_path)

        # Pre-populate a step so redo has something to target.
        orch._steps.append(StepRecord(step_number=1, tool="ese", command="raster clip"))

        corrections.redo.return_value = MagicMock(status="error", message="redo failed")

        mock_menu.return_value = [IntentOption(
            intent="clip", description="Clip raster", required_params=[],
        )]
        mock_httpx.post.side_effect = [
            MagicMock(
                json=MagicMock(return_value={"choices": [{"message": _make_model_response("redo", params={})}]}),
                raise_for_status=MagicMock(),
            ),
            MagicMock(
                json=MagicMock(return_value={
                    "choices": [{"message": _make_model_response("failed", reason="gave up")}],
                }),
                raise_for_status=MagicMock(),
            ),
        ]

        turn_states: list[dict[str, Any]] = []
        original_build = Orchestrator._build_turn_state

        def capture_turn_state(
            self: Orchestrator, status: str, step: int, intent: str,
        ) -> dict[str, Any]:
            state = original_build(self, status, step, intent)
            turn_states.append(state)
            return state

        with patch.object(Orchestrator, "_build_turn_state", capture_turn_state):
            orch.run("test")

        assert orch._failed_redo_count == 1

        # The turn state sent after the redo failure should have failed_attempts.
        failed_states = [s for s in turn_states if s.get("failed_attempts")]
        assert len(failed_states) >= 1
        assert failed_states[0]["failed_attempts"] == 1

    @patch("ecospheric_harness.orchestrator.httpx")
    @patch("ecospheric_harness.orchestrator.available_intents")
    def test_successful_operation_resets_counter(
        self, mock_menu: MagicMock, mock_httpx: MagicMock, tmp_path: Path,
    ) -> None:
        """Successful operation resets failed_redo_count to 0."""
        orch, _, _, corrections = _make_mock_orchestrator(tmp_path)

        # Pre-populate a step.
        orch._steps.append(StepRecord(step_number=1, tool="ese", command="raster clip"))

        # First: redo fails.  Second: redo succeeds.  Third: complete.
        corrections.redo.side_effect = [
            MagicMock(status="error", message="failed"),
            MagicMock(status="redone"),
        ]

        mock_menu.return_value = [IntentOption(
            intent="clip", description="Clip raster", required_params=[],
        )]
        mock_httpx.post.side_effect = [
            MagicMock(
                json=MagicMock(return_value={"choices": [{"message": _make_model_response("redo", params={})}]}),
                raise_for_status=MagicMock(),
            ),
            MagicMock(
                json=MagicMock(return_value={"choices": [{"message": _make_model_response("redo", params={"to": "EPSG:4326"})}]}),
                raise_for_status=MagicMock(),
            ),
            MagicMock(
                json=MagicMock(return_value={
                    "choices": [{"message": _make_model_response("complete", summary="fixed")}],
                }),
                raise_for_status=MagicMock(),
            ),
        ]

        turn_states: list[dict[str, Any]] = []
        original_build = Orchestrator._build_turn_state

        def capture_turn_state(
            self: Orchestrator, status: str, step: int, intent: str,
        ) -> dict[str, Any]:
            state = original_build(self, status, step, intent)
            turn_states.append(state)
            return state

        with patch.object(Orchestrator, "_build_turn_state", capture_turn_state):
            orch.run("test")

        # After failed redo: counter = 1.
        # Layout: [0] pre-iter0, [1] post-failed-redo, [2] pre-iter1, [3] post-successful-redo, [4] pre-iter2
        assert turn_states[1].get("failed_attempts") == 1

        # After successful redo: counter reset, failed_attempts omitted.
        assert "failed_attempts" not in turn_states[3]

    @patch("ecospheric_harness.orchestrator.httpx")
    @patch("ecospheric_harness.orchestrator.available_intents")
    def test_counter_in_turn_state_when_positive(
        self, mock_menu: MagicMock, mock_httpx: MagicMock, tmp_path: Path,
    ) -> None:
        """failed_attempts omitted when 0, present when > 0."""
        orch, _, _, _ = _make_mock_orchestrator(tmp_path)

        mock_menu.return_value = [IntentOption(
            intent="clip", description="Clip raster", required_params=[],
        )]
        mock_httpx.post.side_effect = [
            MagicMock(
                json=MagicMock(return_value={"choices": [{"message": _make_model_response("clip")}]}),
                raise_for_status=MagicMock(),
            ),
            MagicMock(
                json=MagicMock(return_value={
                    "choices": [{"message": _make_model_response("complete", summary="done")}],
                }),
                raise_for_status=MagicMock(),
            ),
        ]

        turn_states: list[dict[str, Any]] = []
        original_build = Orchestrator._build_turn_state

        def capture_turn_state(
            self: Orchestrator, status: str, step: int, intent: str,
        ) -> dict[str, Any]:
            state = original_build(self, status, step, intent)
            turn_states.append(state)
            return state

        with patch.object(Orchestrator, "_build_turn_state", capture_turn_state):
            orch.run("test")

        # No failed_attempts when counter is 0.
        for state in turn_states:
            assert "failed_attempts" not in state


# ---------------------------------------------------------------------------
# Resolution error → error to model
# ---------------------------------------------------------------------------


class TestResolutionError:
    @patch("ecospheric_harness.orchestrator.httpx")
    @patch("ecospheric_harness.orchestrator.available_intents")
    def test_resolution_error_sends_to_model(
        self, mock_menu: MagicMock, mock_httpx: MagicMock, tmp_path: Path,
    ) -> None:
        """ResolutionError → error turn sent to model, model retries."""
        orch, _, resolver, _ = _make_mock_orchestrator(tmp_path)

        resolver.resolve.side_effect = [
            ResolutionError("unknown intent 'bogus'"),
            ResolvedCall(
                tool=RegisteredTool(name="ese", version="0.5.0", binary="ese", commands=[]),
                command=CommandDescriptor(name="raster clip", description="", category="raster"),
                params={},
            ),
        ]

        mock_menu.return_value = [IntentOption(
            intent="clip", description="Clip raster", required_params=[],
        )]
        mock_httpx.post.side_effect = [
            MagicMock(
                json=MagicMock(return_value={"choices": [{"message": _make_model_response("bogus")}]}),
                raise_for_status=MagicMock(),
            ),
            MagicMock(
                json=MagicMock(return_value={
                    "choices": [{"message": _make_model_response("complete", summary="recovered")}],
                }),
                raise_for_status=MagicMock(),
            ),
        ]

        result = orch.run("test")

        assert isinstance(result, PipelineResult)
        assert mock_httpx.post.call_count == 2


# ---------------------------------------------------------------------------
# Issue 1: tool_call_id matches actual model response id
# ---------------------------------------------------------------------------


class TestToolCallIdMatches:
    @patch("ecospheric_harness.orchestrator.httpx")
    @patch("ecospheric_harness.orchestrator.available_intents")
    def test_tool_message_uses_real_tool_call_id(
        self, mock_menu: MagicMock, mock_httpx: MagicMock, tmp_path: Path,
    ) -> None:
        """tool_call_id in tool messages matches the assistant tool-call id."""
        orch, _, _, _ = _make_mock_orchestrator(tmp_path)

        mock_menu.return_value = [IntentOption(
            intent="clip", description="Clip raster", required_params=[],
        )]

        # Build model responses with a realistic tool_call_id
        def make_response_with_id(intent: str, call_id: str, **extra: Any) -> dict[str, Any]:
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

        mock_httpx.post.side_effect = [
            MagicMock(
                json=MagicMock(return_value={"choices": [{"message": make_response_with_id("clip", "call_abc123")}]}),
                raise_for_status=MagicMock(),
            ),
            MagicMock(
                json=MagicMock(return_value={
                    "choices": [{"message": make_response_with_id("complete", "call_def456", summary="done")}],
                }),
                raise_for_status=MagicMock(),
            ),
        ]

        result = orch.run("test prompt")

        # Check that the messages list was built with the real tool_call_id.
        # We verify indirectly: the orchestrator should have appended tool
        # messages using "call_abc123" (not the hardcoded "emit_intent").
        assert isinstance(result, PipelineResult)
        assert len(result.steps) == 1

        # Inspect the second httpx call to verify messages include correct tool_call_id
        second_call_messages = mock_httpx.post.call_args_list[1].kwargs["json"]["messages"]
        tool_messages = [m for m in second_call_messages if m.get("role") == "tool"]
        assert len(tool_messages) == 1
        assert tool_messages[0]["tool_call_id"] == "call_abc123"

    @patch("ecospheric_harness.orchestrator.httpx")
    @patch("ecospheric_harness.orchestrator.available_intents")
    def test_unparseable_response_uses_real_tool_call_id(
        self, mock_menu: MagicMock, mock_httpx: MagicMock, tmp_path: Path,
    ) -> None:
        """Unparseable response still uses the tool_call_id from the raw response."""
        orch, _, _, _ = _make_mock_orchestrator(tmp_path)

        mock_menu.return_value = [IntentOption(
            intent="clip", description="Clip raster", required_params=[],
        )]

        # First response: has tool_calls with an id, but function name is wrong
        bad_response = {
            "tool_calls": [{
                "id": "call_xyz789",
                "type": "function",
                "function": {
                    "name": "wrong_function",
                    "arguments": "{}",
                },
            }],
        }

        mock_httpx.post.side_effect = [
            MagicMock(
                json=MagicMock(return_value={"choices": [{"message": bad_response}]}),
                raise_for_status=MagicMock(),
            ),
            MagicMock(
                json=MagicMock(return_value={
                    "choices": [{"message": _make_model_response("complete", summary="recovered")}],
                }),
                raise_for_status=MagicMock(),
            ),
        ]

        orch.run("test")

        second_call_messages = mock_httpx.post.call_args_list[1].kwargs["json"]["messages"]
        tool_messages = [m for m in second_call_messages if m.get("role") == "tool"]
        assert len(tool_messages) == 1
        assert tool_messages[0]["tool_call_id"] == "call_xyz789"


# ---------------------------------------------------------------------------
# Issue 2: rejection followed by success → distinct step_numbers
# ---------------------------------------------------------------------------


class TestRejectionThenSuccessDistinctSteps:
    @patch("ecospheric_harness.orchestrator.httpx")
    @patch("ecospheric_harness.orchestrator.available_intents")
    def test_rejection_then_success_has_distinct_step_numbers(
        self, mock_menu: MagicMock, mock_httpx: MagicMock, tmp_path: Path,
    ) -> None:
        """A rejected step followed by a successful step → distinct step_numbers."""
        orch, _, _, _ = _make_mock_orchestrator(tmp_path)

        mock_menu.return_value = [IntentOption(
            intent="clip", description="Clip raster", required_params=[],
        )]

        # First call: validation fails.  Second call: succeeds.  Third: complete.
        mock_httpx.post.side_effect = [
            MagicMock(
                json=MagicMock(return_value={"choices": [{"message": _make_model_response("clip")}]}),
                raise_for_status=MagicMock(),
            ),
            MagicMock(
                json=MagicMock(return_value={"choices": [{"message": _make_model_response("clip")}]}),
                raise_for_status=MagicMock(),
            ),
            MagicMock(
                json=MagicMock(return_value={
                    "choices": [{"message": _make_model_response("complete", summary="done")}],
                }),
                raise_for_status=MagicMock(),
            ),
        ]

        # Make validation fail on first call, succeed on second
        orch._validator.validate.side_effect = [
            ValidationResult(ok=False, errors=["bad param"]),
            ValidationResult(ok=True),
        ]

        result = orch.run("test")

        assert len(result.steps) == 2
        assert result.steps[0].status == "rejected"
        assert result.steps[1].status == "success"
        assert result.steps[0].step_number != result.steps[1].step_number
        assert result.steps[0].step_number == 1
        assert result.steps[1].step_number == 2


# ---------------------------------------------------------------------------
# Issue 3: execution failure creates an error StepRecord
# ---------------------------------------------------------------------------


class TestExecutionFailureCreatesStepRecord:
    @patch("ecospheric_harness.orchestrator.httpx")
    @patch("ecospheric_harness.orchestrator.available_intents")
    def test_execution_failure_creates_error_step_record(
        self, mock_menu: MagicMock, mock_httpx: MagicMock, tmp_path: Path,
    ) -> None:
        """Tool execution failure → error StepRecord exists in result.steps."""
        orch, _, _, _ = _make_mock_orchestrator(tmp_path, executor_succeed=False)

        mock_menu.return_value = [IntentOption(
            intent="clip", description="Clip raster", required_params=[],
        )]
        mock_httpx.post.side_effect = [
            MagicMock(
                json=MagicMock(return_value={"choices": [{"message": _make_model_response("clip")}]}),
                raise_for_status=MagicMock(),
            ),
            MagicMock(
                json=MagicMock(return_value={
                    "choices": [{"message": _make_model_response("failed", reason="giving up")}],
                }),
                raise_for_status=MagicMock(),
            ),
        ]

        result = orch.run("test")

        # There should be at least one step with status="error"
        error_steps = [s for s in result.steps if s.status == "error"]
        assert len(error_steps) == 1
        assert error_steps[0].intent == "clip"
        assert error_steps[0].step_number == 1
        assert error_steps[0].envelope is not None
        assert error_steps[0].envelope["status"] == "error"


# ---------------------------------------------------------------------------
# Issue 4: workdir is created before writing
# ---------------------------------------------------------------------------


class TestWorkdirCreated:
    @patch("ecospheric_harness.orchestrator.httpx")
    @patch("ecospheric_harness.orchestrator.available_intents")
    def test_workdir_created_for_terminal(
        self, mock_menu: MagicMock, mock_httpx: MagicMock, tmp_path: Path,
    ) -> None:
        """workdir is created even if it doesn't exist before _handle_terminal."""
        # Use a nested path that doesn't exist yet
        nested_workdir = tmp_path / "deep" / "nested" / "workdir"
        orch, _, _, _ = _make_mock_orchestrator(tmp_path)
        # Override workdir to point to the nested path after creation
        orch._config = HarnessConfig(
            model="test-model",
            max_turns=20,
            search_cap=20,
            workdir=nested_workdir,
        )

        mock_menu.return_value = [IntentOption(
            intent="clip", description="Clip raster", required_params=[],
        )]
        mock_httpx.post.side_effect = [
            MagicMock(
                json=MagicMock(return_value={"choices": [{"message": _make_model_response("complete", summary="done")}]}),
                raise_for_status=MagicMock(),
            ),
        ]

        orch.run("test")

        # Should not raise — workdir was created
        assert nested_workdir.exists()
        assert (nested_workdir / "provenance.json").exists()
        assert (nested_workdir / "summary.json").exists()


# ---------------------------------------------------------------------------
# Multiple tool_calls handling
# ---------------------------------------------------------------------------


class TestMultipleToolCalls:
    @patch("ecospheric_harness.orchestrator.httpx")
    @patch("ecospheric_harness.orchestrator.available_intents")
    def test_multiple_tool_calls_get_error_responses(
        self, mock_menu: MagicMock, mock_httpx: MagicMock, tmp_path: Path,
    ) -> None:
        """When model emits ≥2 tool_calls, each extra gets an error tool response."""
        orch, _, _, _ = _make_mock_orchestrator(tmp_path)

        mock_menu.return_value = [IntentOption(
            intent="clip", description="Clip raster", required_params=[],
        )]

        # Build a response with TWO tool_calls.
        multi_tool_response = {
            "tool_calls": [
                {
                    "id": "call_primary",
                    "type": "function",
                    "function": {
                        "name": "emit_intent",
                        "arguments": json.dumps({"intent": "clip"}),
                    },
                },
                {
                    "id": "call_extra",
                    "type": "function",
                    "function": {
                        "name": "emit_intent",
                        "arguments": json.dumps({"intent": "clip"}),
                    },
                },
            ],
        }

        mock_httpx.post.side_effect = [
            MagicMock(
                json=MagicMock(return_value={"choices": [{"message": multi_tool_response}]}),
                raise_for_status=MagicMock(),
            ),
            MagicMock(
                json=MagicMock(return_value={
                    "choices": [{"message": _make_model_response("complete", summary="done")}],
                }),
                raise_for_status=MagicMock(),
            ),
        ]

        orch.run("test")

        # Check the second httpx call's messages for tool responses.
        second_call_messages = mock_httpx.post.call_args_list[1].kwargs["json"]["messages"]
        tool_messages = [m for m in second_call_messages if m.get("role") == "tool"]

        # Both tool_calls should have a response.
        assert len(tool_messages) == 2
        tool_call_ids = [m["tool_call_id"] for m in tool_messages]
        assert "call_primary" in tool_call_ids
        assert "call_extra" in tool_call_ids

        # The extra one should contain an error.
        extra_msg = next(m for m in tool_messages if m["tool_call_id"] == "call_extra")
        extra_content = json.loads(extra_msg["content"])
        assert "error" in extra_content
        assert "one tool call per turn" in extra_content["error"].lower()

    @patch("ecospheric_harness.orchestrator.httpx")
    @patch("ecospheric_harness.orchestrator.available_intents")
    def test_parallel_tool_calls_false_in_request(
        self, mock_menu: MagicMock, mock_httpx: MagicMock, tmp_path: Path,
    ) -> None:
        """parallel_tool_calls: false is included in every API request."""
        orch, _, _, _ = _make_mock_orchestrator(tmp_path)

        mock_menu.return_value = [IntentOption(
            intent="clip", description="Clip raster", required_params=[],
        )]
        mock_httpx.post.side_effect = [
            MagicMock(
                json=MagicMock(return_value={"choices": [{"message": _make_model_response("clip")}]}),
                raise_for_status=MagicMock(),
            ),
            MagicMock(
                json=MagicMock(return_value={
                    "choices": [{"message": _make_model_response("complete", summary="done")}],
                }),
                raise_for_status=MagicMock(),
            ),
        ]

        orch.run("test")

        # Check that every API call included parallel_tool_calls: false.
        for call in mock_httpx.post.call_args_list:
            payload = call.kwargs["json"]
            assert payload.get("parallel_tool_calls") is False


# ---------------------------------------------------------------------------
# Malformed API response handling
# ---------------------------------------------------------------------------


class TestMalformedApiResponse:
    @patch("ecospheric_harness.orchestrator.httpx")
    @patch("ecospheric_harness.orchestrator.available_intents")
    def test_malformed_body_raises_valueerror(
        self, mock_menu: MagicMock, mock_httpx: MagicMock, tmp_path: Path,
    ) -> None:
        """Unexpected API body structure raises ValueError (retryable)."""
        orch, _, _, _ = _make_mock_orchestrator(tmp_path, max_turns=2)

        mock_menu.return_value = [IntentOption(
            intent="clip", description="Clip raster", required_params=[],
        )]

        # First call returns error body (no choices key), second recovers.
        mock_httpx.post.side_effect = [
            MagicMock(
                json=MagicMock(return_value={"error": {"message": "rate limited"}}),
                raise_for_status=MagicMock(),
                text='{"error": {"message": "rate limited"}}',
            ),
            MagicMock(
                json=MagicMock(return_value={
                    "choices": [{"message": _make_model_response("complete", summary="done")}],
                }),
                raise_for_status=MagicMock(),
            ),
        ]

        # Should not raise — the ValueError triggers retry.
        result = orch.run("test")
        assert isinstance(result, PipelineResult)

    @patch("ecospheric_harness.orchestrator.httpx")
    @patch("ecospheric_harness.orchestrator.available_intents")
    def test_invalid_json_raises_valueerror(
        self, mock_menu: MagicMock, mock_httpx: MagicMock, tmp_path: Path,
    ) -> None:
        """Non-JSON API response raises ValueError (retryable)."""
        orch, _, _, _ = _make_mock_orchestrator(tmp_path, max_turns=2)

        mock_menu.return_value = [IntentOption(
            intent="clip", description="Clip raster", required_params=[],
        )]

        # First call returns invalid JSON, second recovers.
        bad_resp = MagicMock()
        bad_resp.raise_for_status = MagicMock()
        bad_resp.json = MagicMock(side_effect=json.JSONDecodeError("bad", "bad", 0))
        bad_resp.text = "<html>502 Bad Gateway</html>"

        mock_httpx.post.side_effect = [
            bad_resp,
            MagicMock(
                json=MagicMock(return_value={
                    "choices": [{"message": _make_model_response("complete", summary="done")}],
                }),
                raise_for_status=MagicMock(),
            ),
        ]

        result = orch.run("test")
        assert isinstance(result, PipelineResult)


# ---------------------------------------------------------------------------
# ISSUE 1: last_result should contain actual step number and intent
# ---------------------------------------------------------------------------


class TestLastResultContainsRealStepAndIntent:
    @patch("ecospheric_harness.orchestrator.httpx")
    @patch("ecospheric_harness.orchestrator.available_intents")
    def test_success_path_uses_real_step_and_intent(
        self, mock_menu: MagicMock, mock_httpx: MagicMock, tmp_path: Path,
    ) -> None:
        """After a successful operation, last_result.step and last_result.intent
        reflect the actual step number and intent (not 0 and '')."""
        orch, _, _, _ = _make_mock_orchestrator(tmp_path)

        mock_menu.return_value = [IntentOption(
            intent="clip", description="Clip raster", required_params=[],
        )]
        mock_httpx.post.side_effect = [
            MagicMock(
                json=MagicMock(return_value={"choices": [{"message": _make_model_response("clip")}]}),
                raise_for_status=MagicMock(),
            ),
            MagicMock(
                json=MagicMock(return_value={
                    "choices": [{"message": _make_model_response("complete", summary="done")}],
                }),
                raise_for_status=MagicMock(),
            ),
        ]

        turn_states: list[dict[str, Any]] = []
        original_build = Orchestrator._build_turn_state

        def capture_turn_state(
            self: Orchestrator, status: str, step: int, intent: str,
        ) -> dict[str, Any]:
            state = original_build(self, status, step, intent)
            turn_states.append(state)
            return state

        with patch.object(Orchestrator, "_build_turn_state", capture_turn_state):
            orch.run("test")

        # After step 1: the post-dispatch turn state should have
        # last_result.step=1 and last_result.intent="clip"
        # Layout: [0] pre-step1, [1] post-step1, [2] pre-complete
        post_step1_state = turn_states[1]
        assert post_step1_state["last_result"]["step"] == 1
        assert post_step1_state["last_result"]["intent"] == "clip"
        assert post_step1_state["last_result"]["status"] == "success"

    @patch("ecospheric_harness.orchestrator.httpx")
    @patch("ecospheric_harness.orchestrator.available_intents")
    def test_error_path_uses_real_step_number(
        self, mock_menu: MagicMock, mock_httpx: MagicMock, tmp_path: Path,
    ) -> None:
        """After an execution error, last_result.step reflects the actual step."""
        orch, _, _, _ = _make_mock_orchestrator(tmp_path, executor_succeed=False)

        mock_menu.return_value = [IntentOption(
            intent="clip", description="Clip raster", required_params=[],
        )]
        mock_httpx.post.side_effect = [
            MagicMock(
                json=MagicMock(return_value={"choices": [{"message": _make_model_response("clip")}]}),
                raise_for_status=MagicMock(),
            ),
            MagicMock(
                json=MagicMock(return_value={
                    "choices": [{"message": _make_model_response("failed", reason="giving up")}],
                }),
                raise_for_status=MagicMock(),
            ),
        ]

        turn_states: list[dict[str, Any]] = []
        original_build = Orchestrator._build_turn_state

        def capture_turn_state(
            self: Orchestrator, status: str, step: int, intent: str,
        ) -> dict[str, Any]:
            state = original_build(self, status, step, intent)
            turn_states.append(state)
            return state

        with patch.object(Orchestrator, "_build_turn_state", capture_turn_state):
            orch.run("test")

        # After a failed execution (step 1 was recorded as "error"):
        # the error turn should have step=1 and intent="clip"
        error_states = [s for s in turn_states if s["last_result"]["status"] == "error"]
        assert len(error_states) >= 1
        assert error_states[0]["last_result"]["step"] == 1
        assert error_states[0]["last_result"]["intent"] == "clip"
