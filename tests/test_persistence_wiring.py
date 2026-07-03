"""Persistence wiring tests.

Verifies that the orchestrator and Harness correctly call
``ArtifactRegistry.persist()`` at the right lifecycle points, that
``save_state()`` delegates properly, that orphan cleanup runs on init,
and that registry state round-trips across instances.
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
)
from ecospheric_harness.orchestrator import Orchestrator
from ecospheric_harness.preflight import PreflightChecker
from ecospheric_harness.registry import ToolRegistry
from ecospheric_harness.resolver import IntentResolver
from ecospheric_harness.result import PipelineResult, StepRecord
from ecospheric_harness.validator import SchemaValidator, ValidationResult
from ecospheric_harness.workspace import WorkspaceManager


# ---------------------------------------------------------------------------
# Helpers (adapted from test_orchestrator.py)
# ---------------------------------------------------------------------------


def _make_mock_orchestrator(
    tmp_path: Path,
    *,
    max_turns: int = 20,
    search_cap: int = 20,
    preflight_ok: bool = True,
    validation_ok: bool = True,
    executor_succeed: bool = True,
    executor_envelope: dict[str, Any] | None = None,
) -> tuple[Orchestrator, ArtifactRegistry, MagicMock, MagicMock]:
    """Build an Orchestrator with mock dependencies and a real ArtifactRegistry."""
    config = HarnessConfig(
        model="test-model",
        max_turns=max_turns,
        search_cap=search_cap,
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
        validator.validate.return_value = ValidationResult(ok=False, errors=["invalid param 'x'"])
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
                message="requires planar CRS",
            )
        ]

    if executor_envelope is not None:
        envelope = executor_envelope
    elif executor_succeed:
        envelope = {
            "status": "success",
            "data": {"format": "geojson", "data_type": "vector"},
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
    )
    return orch, artifact_registry, resolver, corrections


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


# ---------------------------------------------------------------------------
# Test 1: save_state delegates to persist
# ---------------------------------------------------------------------------


class TestSaveStateDelegates:
    @patch("ecospheric_harness.orchestrator.httpx")
    @patch("ecospheric_harness.orchestrator.available_intents")
    def test_save_state_delegates_to_persist(
        self, mock_menu: MagicMock, mock_httpx: MagicMock, tmp_path: Path,
    ) -> None:
        orch, artifact_registry, _, _ = _make_mock_orchestrator(tmp_path)
        mock_menu.return_value = [IntentOption(
            intent="clip", description="Clip", required_params=[],
        )]

        # Spy on persist
        with patch.object(artifact_registry, "persist", wraps=artifact_registry.persist) as persist_spy:
            orch.save_state()
            persist_spy.assert_called_once()


# ---------------------------------------------------------------------------
# Test 2: Harness.save_state delegates
# ---------------------------------------------------------------------------


class TestHarnessSaveState:
    def test_harness_save_state_delegates(self, tmp_path: Path) -> None:
        """Harness.save_state should delegate to orchestrator.save_state."""
        from ecospheric_harness.__main__ import Harness

        harness = Harness(
            tools=[],  # no real tools needed
            workspace_root=tmp_path,
            model="test-model",
        )

        with patch.object(harness._orchestrator, "save_state") as mock_save:
            harness.save_state()
            mock_save.assert_called_once()


# ---------------------------------------------------------------------------
# Test 3: persist called after successful step
# ---------------------------------------------------------------------------


class TestPersistAfterSuccess:
    @patch("ecospheric_harness.orchestrator.httpx")
    @patch("ecospheric_harness.orchestrator.available_intents")
    def test_persist_called_after_successful_step(
        self, mock_menu: MagicMock, mock_httpx: MagicMock, tmp_path: Path,
    ) -> None:
        orch, artifact_registry, _, _ = _make_mock_orchestrator(tmp_path)

        mock_menu.return_value = [IntentOption(
            intent="clip", description="Clip", required_params=[],
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

        with patch.object(artifact_registry, "persist", wraps=artifact_registry.persist) as persist_spy:
            result = orch.run("test prompt")
            assert result.steps[0].status == "success"
            assert persist_spy.call_count >= 1

        # Registry file should exist on disk
        registry_path = artifact_registry._registry_path
        assert registry_path.exists()


# ---------------------------------------------------------------------------
# Test 4: persist NOT called on rejected step
# ---------------------------------------------------------------------------


class TestPersistNotCalledOnRejected:
    @patch("ecospheric_harness.orchestrator.httpx")
    @patch("ecospheric_harness.orchestrator.available_intents")
    def test_persist_not_called_on_rejected_step(
        self, mock_menu: MagicMock, mock_httpx: MagicMock, tmp_path: Path,
    ) -> None:
        orch, artifact_registry, _, _ = _make_mock_orchestrator(
            tmp_path, validation_ok=False,
        )

        mock_menu.return_value = [IntentOption(
            intent="clip", description="Clip", required_params=[],
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

        with patch.object(artifact_registry, "persist", wraps=artifact_registry.persist) as persist_spy:
            result = orch.run("test prompt")
            # The step should be rejected
            rejected_steps = [s for s in result.steps if s.status == "rejected"]
            assert len(rejected_steps) > 0

        # persist should NOT have been called for the rejected step.
        # (It might be called by register() inside ArtifactRegistry for
        #  other reasons, but the orchestrator itself should not trigger
        #  an additional persist for rejected steps.)
        # We check that persist was not called from the orchestrator's
        # _handle_operation success path by verifying no artifacts were
        # registered (which is where the orchestrator calls persist).
        assert len(artifact_registry._artifacts) == 0


# ---------------------------------------------------------------------------
# Test 5: persist NOT called on failed step
# ---------------------------------------------------------------------------


class TestPersistNotCalledOnFailed:
    @patch("ecospheric_harness.orchestrator.httpx")
    @patch("ecospheric_harness.orchestrator.available_intents")
    def test_persist_not_called_on_failed_step(
        self, mock_menu: MagicMock, mock_httpx: MagicMock, tmp_path: Path,
    ) -> None:
        orch, artifact_registry, _, _ = _make_mock_orchestrator(
            tmp_path, executor_succeed=False,
        )

        mock_menu.return_value = [IntentOption(
            intent="clip", description="Clip", required_params=[],
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

        with patch.object(artifact_registry, "persist", wraps=artifact_registry.persist) as persist_spy:
            result = orch.run("test prompt")
            # The step should be an error/failure
            failed_steps = [s for s in result.steps if s.status in ("error", "failed")]
            assert len(failed_steps) > 0

        # No artifacts should have been registered (persist is called
        # inside register(), but the orchestrator's success-path persist
        # should not fire).
        assert len(artifact_registry._artifacts) == 0


# ---------------------------------------------------------------------------
# Test 6: cleanup_orphans on init
# ---------------------------------------------------------------------------


class TestCleanupOrphansOnInit:
    def test_cleanup_orphans_on_init(self, tmp_path: Path) -> None:
        """Harness.__init__ should call cleanup_orphans, removing unregistered files."""
        from ecospheric_harness.__main__ import Harness

        # Create a session directory with a fake unregistered file
        session_dir = tmp_path / "test-session"
        session_dir.mkdir(parents=True)
        orphan_file = session_dir / "orphan.tif"
        orphan_file.write_bytes(b"fake data")

        assert orphan_file.exists()

        # Initializing Harness should trigger cleanup_orphans
        harness = Harness(
            tools=[],
            workspace_root=tmp_path,
            session_id="test-session",
            model="test-model",
        )

        # The orphan file should have been deleted
        assert not orphan_file.exists()


# ---------------------------------------------------------------------------
# Test 7: round-trip across instances
# ---------------------------------------------------------------------------


class TestRoundTripAcrossInstances:
    @patch("ecospheric_harness.orchestrator.httpx")
    @patch("ecospheric_harness.orchestrator.available_intents")
    def test_round_trip_across_instances(
        self, mock_menu: MagicMock, mock_httpx: MagicMock, tmp_path: Path,
    ) -> None:
        """Artifacts persisted by one Harness instance should be loaded by a new one."""
        from ecospheric_harness.__main__ import Harness

        # --- Instance 1: run a successful step ---
        harness1 = Harness(
            tools=[],
            workspace_root=tmp_path,
            session_id="rt-session",
            model="test-model",
        )

        # We need to simulate a successful step without real tool execution.
        # Directly register an artifact via the artifact registry.
        artifact_path = tmp_path / "rt-session" / "test_output.geojson"
        artifact_path.parent.mkdir(parents=True, exist_ok=True)
        artifact_path.write_bytes(b'{"type":"FeatureCollection","features":[]}')

        record = harness1._artifact_registry.register(
            path=artifact_path,
            format="geojson",
            data_type="vector",
            intent="search",
            tool_name="ese",
            tool_version="0.5.0",
            command_name="search",
            step_number=1,
        )

        # Verify registry.json was written
        registry_path = tmp_path / "rt-session" / "registry.json"
        assert registry_path.exists()

        # Verify the artifact is in the first instance
        assert record.artifact_id in harness1._artifact_registry._artifacts

        # Release the session lock so instance 2 can acquire it
        harness1._workspace.cleanup()

        # --- Instance 2: create a new Harness pointing at the same session ---
        harness2 = Harness(
            tools=[],
            workspace_root=tmp_path,
            session_id="rt-session",
            model="test-model",
        )

        # The new instance should have loaded the previous artifacts
        loaded = harness2._artifact_registry._artifacts
        assert record.artifact_id in loaded

        # The loaded record should match
        loaded_record = loaded[record.artifact_id]
        assert loaded_record.format == "geojson"
        assert loaded_record.data_type == "vector"
        assert loaded_record.intent == "search"

        # current should point to the most recent artifact
        current = harness2._artifact_registry.current
        assert current is not None
        assert current.artifact_id == record.artifact_id
