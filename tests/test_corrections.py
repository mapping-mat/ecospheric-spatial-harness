"""Tests for the correction handler (undo/redo).

Covers AC12–AC17, AC37, AC39, AC40: undo, redo (both paths),
failure atomicity, provenance chain correctness, and edge cases.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

from etp.describe import CommandDescriptor

from ecospheric_harness.artifact import Artifact, ArtifactManager
from ecospheric_harness.corrections import CorrectionHandler
from ecospheric_harness.intents import ExecuteResult, PreflightResult, RegisteredTool
from ecospheric_harness.preflight import PreflightChecker
from ecospheric_harness.resolver import IntentResolver
from ecospheric_harness.result import StepRecord


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_artifact(
    tmp_path: Path,
    name: str,
    body: bytes = b"x",
    *,
    step_number: int = 0,
    fmt: str = "geotiff",
    data_type: str = "raster",
) -> Artifact:
    """Create a real temp file and return an Artifact."""
    p = tmp_path / name
    p.write_bytes(body)
    return Artifact(
        path=p,
        envelope={"status": "success", "data": {"format": fmt, "data_type": data_type}},
        format=fmt,
        data_type=data_type,
        step_number=step_number,
    )


def _make_step(
    step_number: int,
    *,
    status: str = "success",
    undone: bool = False,
    tool_name: str = "ese",
    command_name: str = "raster clip",
) -> StepRecord:
    """Return a StepRecord with mock tool_ref and command_ref."""
    cmd = CommandDescriptor(
        name=command_name,
        description="test command",
        category="test",
    )
    tool = RegisteredTool(
        name=tool_name,
        version="0.5.0",
        binary="ese",
        commands=[cmd],
    )
    return StepRecord(
        step_number=step_number,
        tool=tool_name,
        command=command_name,
        tool_ref=tool,
        command_ref=cmd,
        status=status,
        undone=undone,
    )


class MockExecutor:
    """Mock ToolExecutor that returns canned ExecuteResult objects."""

    def __init__(
        self,
        tmp_path: Path,
        *,
        succeed: bool = True,
        returncode: int = 0,
    ) -> None:
        self._tmp_path = tmp_path
        self._succeed = succeed
        self._returncode = returncode
        self.call_count = 0
        self.last_params: dict[str, Any] | None = None

    def execute(
        self,
        tool: RegisteredTool,
        command: CommandDescriptor,
        params: dict[str, Any],
        input_artifact: Artifact | None,
        workdir: Path,
    ) -> ExecuteResult:
        self.call_count += 1
        self.last_params = params

        output_path = self._tmp_path / f"redo_out_{self.call_count}.bin"
        output_path.write_bytes(b"redo-output")

        if self._succeed:
            envelope: dict[str, Any] = {
                "status": "success",
                "data": {
                    "format": "geotiff",
                    "data_type": "raster",
                    "crs": "EPSG:4326",
                },
            }
        else:
            envelope = {
                "status": "error",
                "error": {
                    "type": "execution_error",
                    "message": "tool crashed",
                    "retryable": False,
                },
            }

        return ExecuteResult(
            envelope=envelope,
            returncode=self._returncode,
            output_path=output_path,
        )


# ---------------------------------------------------------------------------
# AC12: Undo at step 2
# ---------------------------------------------------------------------------


class TestUndoAtStep2:
    def test_undo_reverts_to_step1(self, tmp_path: Path) -> None:
        """AC12: 2 steps, undo → step2 undone, current=step1, previous=None."""
        mgr = ArtifactManager(workdir=tmp_path, disk_limit_bytes=1_000_000)
        a1 = _make_artifact(tmp_path, "step1.bin", b"step1", step_number=1)
        a2 = _make_artifact(tmp_path, "step2.bin", b"step2", step_number=2)
        mgr.store(a1)
        mgr.store(a2)

        steps = [_make_step(1), _make_step(2)]
        handler = CorrectionHandler(
            artifacts=mgr,
            steps=steps,
            executor=MockExecutor(tmp_path),  # type: ignore[arg-type]
            resolver=MagicMock(spec=IntentResolver),
            workdir=tmp_path,
        )

        result = handler.undo()

        assert result.status == "undone"
        assert result.artifact is a1
        assert steps[1].undone is True
        assert steps[0].undone is False
        assert mgr.current is a1
        assert mgr.previous is None


# ---------------------------------------------------------------------------
# AC14: Undo at step 1 (nothing to undo)
# ---------------------------------------------------------------------------


class TestUndoAtStep1:
    def test_undo_with_no_previous_returns_error(self, tmp_path: Path) -> None:
        """AC14: 1 step, undo → error, pipeline continues."""
        mgr = ArtifactManager(workdir=tmp_path, disk_limit_bytes=1_000_000)
        a1 = _make_artifact(tmp_path, "step1.bin", b"step1", step_number=1)
        mgr.store(a1)

        steps = [_make_step(1)]
        handler = CorrectionHandler(
            artifacts=mgr,
            steps=steps,
            executor=MockExecutor(tmp_path),  # type: ignore[arg-type]
            resolver=MagicMock(spec=IntentResolver),
            workdir=tmp_path,
        )

        result = handler.undo()

        assert result.status == "error"
        assert "Cannot undo" in result.message
        assert steps[0].undone is False
        assert mgr.current is a1


# ---------------------------------------------------------------------------
# AC13: Redo replace-current
# ---------------------------------------------------------------------------


class TestRedoReplaceCurrent:
    def test_redo_replaces_current_artifact(self, tmp_path: Path) -> None:
        """AC13: 2 steps, redo → step2 marked undone, new step2' created,
        current=step2', previous=step1."""
        mgr = ArtifactManager(workdir=tmp_path, disk_limit_bytes=1_000_000)
        a1 = _make_artifact(tmp_path, "step1.bin", b"step1", step_number=1)
        a2 = _make_artifact(tmp_path, "step2.bin", b"step2", step_number=2)
        mgr.store(a1)
        mgr.store(a2)

        steps = [_make_step(1), _make_step(2)]
        mock_exec = MockExecutor(tmp_path, succeed=True)

        handler = CorrectionHandler(
            artifacts=mgr,
            steps=steps,
            executor=mock_exec,  # type: ignore[arg-type]
            resolver=MagicMock(spec=IntentResolver),
            workdir=tmp_path,
        )

        result = handler.redo(params={"to": "EPSG:4326"})

        assert result.status == "redone"
        assert result.artifact is not None
        assert result.artifact is mgr.current
        assert mgr.previous is a1
        assert steps[1].undone is True  # old step2 marked undone
        assert mock_exec.call_count == 1
        assert mock_exec.last_params == {"to": "EPSG:4326"}


# ---------------------------------------------------------------------------
# AC39: Redo post-undo
# ---------------------------------------------------------------------------


class TestRedoPostUndo:
    def test_redo_after_undo(self, tmp_path: Path) -> None:
        """AC39: undo first, then redo → current=step2', previous=step1."""
        mgr = ArtifactManager(workdir=tmp_path, disk_limit_bytes=1_000_000)
        a1 = _make_artifact(tmp_path, "step1.bin", b"step1", step_number=1)
        a2 = _make_artifact(tmp_path, "step2.bin", b"step2", step_number=2)
        mgr.store(a1)
        mgr.store(a2)

        steps = [_make_step(1), _make_step(2)]
        mock_exec = MockExecutor(tmp_path, succeed=True)

        handler = CorrectionHandler(
            artifacts=mgr,
            steps=steps,
            executor=mock_exec,  # type: ignore[arg-type]
            resolver=MagicMock(spec=IntentResolver),
            workdir=tmp_path,
        )

        # Undo first.
        undo_result = handler.undo()
        assert undo_result.status == "undone"
        assert mgr.current is a1
        assert mgr.previous is None

        # Now redo.
        redo_result = handler.redo(params={"to": "EPSG:3857"})

        assert redo_result.status == "redone"
        assert redo_result.artifact is not None
        assert redo_result.artifact is mgr.current
        assert mgr.previous is a1
        assert steps[1].undone is True
        assert mock_exec.call_count == 1


# ---------------------------------------------------------------------------
# AC40: Undo after redo
# ---------------------------------------------------------------------------


class TestUndoAfterRedo:
    def test_undo_after_redo(self, tmp_path: Path) -> None:
        """AC40: redo first, then undo → step2' undone, current=step1."""
        mgr = ArtifactManager(workdir=tmp_path, disk_limit_bytes=1_000_000)
        a1 = _make_artifact(tmp_path, "step1.bin", b"step1", step_number=1)
        a2 = _make_artifact(tmp_path, "step2.bin", b"step2", step_number=2)
        mgr.store(a1)
        mgr.store(a2)

        steps = [_make_step(1), _make_step(2)]
        mock_exec = MockExecutor(tmp_path, succeed=True)

        handler = CorrectionHandler(
            artifacts=mgr,
            steps=steps,
            executor=mock_exec,  # type: ignore[arg-type]
            resolver=MagicMock(spec=IntentResolver),
            workdir=tmp_path,
        )

        # Redo (replace-current path).
        redo_result = handler.redo(params={"to": "EPSG:4326"})
        assert redo_result.status == "redone"

        # The new artifact is current, step1 is previous.
        new_artifact = redo_result.artifact
        assert mgr.current is new_artifact
        assert mgr.previous is a1

        # Now undo the redone step.
        undo_result = handler.undo()

        assert undo_result.status == "undone"
        assert undo_result.artifact is a1
        assert mgr.current is a1
        assert mgr.previous is None
        # The redone step (appended as steps[2]) should be undone.
        assert steps[2].undone is True
        assert steps[1].undone is True  # original step2 also undone


# ---------------------------------------------------------------------------
# AC16: Redo failure — atomic
# ---------------------------------------------------------------------------


class TestRedoFailure:
    def test_failed_redo_preserves_state(self, tmp_path: Path) -> None:
        """AC16: executor returns error → artifacts untouched, step state unchanged."""
        mgr = ArtifactManager(workdir=tmp_path, disk_limit_bytes=1_000_000)
        a1 = _make_artifact(tmp_path, "step1.bin", b"step1", step_number=1)
        a2 = _make_artifact(tmp_path, "step2.bin", b"step2", step_number=2)
        mgr.store(a1)
        mgr.store(a2)

        steps = [_make_step(1), _make_step(2)]
        mock_exec = MockExecutor(tmp_path, succeed=False)

        handler = CorrectionHandler(
            artifacts=mgr,
            steps=steps,
            executor=mock_exec,  # type: ignore[arg-type]
            resolver=MagicMock(spec=IntentResolver),
            workdir=tmp_path,
        )

        # Snapshot pre-state.
        pre_current = mgr.current
        pre_previous = mgr.previous
        pre_undone = [s.undone for s in steps]

        result = handler.redo(params={"to": "EPSG:4326"})

        assert result.status == "error"
        assert "failed" in result.message.lower()
        # Artifacts unchanged.
        assert mgr.current is pre_current
        assert mgr.previous is pre_previous
        # Step flags unchanged.
        assert [s.undone for s in steps] == pre_undone


class TestRedoFailureReturncode:
    def test_failed_redo_nonzero_returncode(self, tmp_path: Path) -> None:
        """Redo with nonzero returncode → error, state unchanged."""
        mgr = ArtifactManager(workdir=tmp_path, disk_limit_bytes=1_000_000)
        a1 = _make_artifact(tmp_path, "step1.bin", b"step1", step_number=1)
        a2 = _make_artifact(tmp_path, "step2.bin", b"step2", step_number=2)
        mgr.store(a1)
        mgr.store(a2)

        steps = [_make_step(1), _make_step(2)]
        # succeed=True but nonzero returncode → still fails the check
        mock_exec = MockExecutor(tmp_path, succeed=True, returncode=1)

        handler = CorrectionHandler(
            artifacts=mgr,
            steps=steps,
            executor=mock_exec,  # type: ignore[arg-type]
            resolver=MagicMock(spec=IntentResolver),
            workdir=tmp_path,
        )

        result = handler.redo(params={})

        assert result.status == "error"
        assert mgr.current is a2
        assert mgr.previous is a1
        assert all(not s.undone for s in steps)


# ---------------------------------------------------------------------------
# AC15: Redo no previous (replace-current path, 1 step)
# ---------------------------------------------------------------------------


class TestRedoNoPrevious:
    def test_redo_with_single_step_no_previous(self, tmp_path: Path) -> None:
        """AC15: 1 step, redo → error (no previous artifact)."""
        mgr = ArtifactManager(workdir=tmp_path, disk_limit_bytes=1_000_000)
        a1 = _make_artifact(tmp_path, "step1.bin", b"step1", step_number=1)
        mgr.store(a1)

        steps = [_make_step(1)]
        mock_exec = MockExecutor(tmp_path, succeed=True)

        handler = CorrectionHandler(
            artifacts=mgr,
            steps=steps,
            executor=mock_exec,  # type: ignore[arg-type]
            resolver=MagicMock(spec=IntentResolver),
            workdir=tmp_path,
        )

        result = handler.redo(params={})

        assert result.status == "error"
        assert "No input artifact" in result.message
        assert mock_exec.call_count == 0  # execute never called


# ---------------------------------------------------------------------------
# Redo with no steps at all
# ---------------------------------------------------------------------------


class TestRedoNoSteps:
    def test_redo_with_empty_steps(self, tmp_path: Path) -> None:
        """No steps → error 'No step to redo'."""
        mgr = ArtifactManager(workdir=tmp_path, disk_limit_bytes=1_000_000)
        steps: list[StepRecord] = []
        mock_exec = MockExecutor(tmp_path, succeed=True)

        handler = CorrectionHandler(
            artifacts=mgr,
            steps=steps,
            executor=mock_exec,  # type: ignore[arg-type]
            resolver=MagicMock(spec=IntentResolver),
            workdir=tmp_path,
        )

        result = handler.redo(params={})

        assert result.status == "error"
        assert "No step to redo" in result.message
        assert mock_exec.call_count == 0


# ---------------------------------------------------------------------------
# AC17, AC37: Provenance after corrections
# ---------------------------------------------------------------------------


class TestProvenanceAfterCorrections:
    def test_provenance_excludes_undone_steps(self, tmp_path: Path) -> None:
        """AC17: provenance chain excludes undone steps after undo."""
        mgr = ArtifactManager(workdir=tmp_path, disk_limit_bytes=1_000_000)
        a1 = _make_artifact(tmp_path, "step1.bin", b"step1", step_number=1)
        a2 = _make_artifact(tmp_path, "step2.bin", b"step2", step_number=2)
        mgr.store(a1)
        mgr.store(a2)

        steps = [_make_step(1), _make_step(2)]
        handler = CorrectionHandler(
            artifacts=mgr,
            steps=steps,
            executor=MockExecutor(tmp_path),  # type: ignore[arg-type]
            resolver=MagicMock(spec=IntentResolver),
            workdir=tmp_path,
        )

        handler.undo()

        active = [s for s in steps if s.status == "success" and not s.undone]
        assert len(active) == 1
        assert active[0].step_number == 1

    def test_provenance_includes_redone_step(self, tmp_path: Path) -> None:
        """AC37: provenance includes the redone step, excludes undone originals."""
        mgr = ArtifactManager(workdir=tmp_path, disk_limit_bytes=1_000_000)
        a1 = _make_artifact(tmp_path, "step1.bin", b"step1", step_number=1)
        a2 = _make_artifact(tmp_path, "step2.bin", b"step2", step_number=2)
        mgr.store(a1)
        mgr.store(a2)

        steps = [_make_step(1), _make_step(2)]
        mock_exec = MockExecutor(tmp_path, succeed=True)

        handler = CorrectionHandler(
            artifacts=mgr,
            steps=steps,
            executor=mock_exec,  # type: ignore[arg-type]
            resolver=MagicMock(spec=IntentResolver),
            workdir=tmp_path,
        )

        handler.redo(params={"to": "EPSG:4326"})

        active = [s for s in steps if s.status == "success" and not s.undone]
        # step1 active, step2 undone, step2' (appended) active.
        assert len(active) == 2
        assert active[0].step_number == 1
        # The appended redo step.
        assert steps[2].undone is False
        assert steps[2].status == "success"

    def test_provenance_after_undo_then_redo(self, tmp_path: Path) -> None:
        """AC37: undo then redo → provenance = [step1, step2']."""
        mgr = ArtifactManager(workdir=tmp_path, disk_limit_bytes=1_000_000)
        a1 = _make_artifact(tmp_path, "step1.bin", b"step1", step_number=1)
        a2 = _make_artifact(tmp_path, "step2.bin", b"step2", step_number=2)
        mgr.store(a1)
        mgr.store(a2)

        steps = [_make_step(1), _make_step(2)]
        mock_exec = MockExecutor(tmp_path, succeed=True)

        handler = CorrectionHandler(
            artifacts=mgr,
            steps=steps,
            executor=mock_exec,  # type: ignore[arg-type]
            resolver=MagicMock(spec=IntentResolver),
            workdir=tmp_path,
        )

        handler.undo()
        handler.redo(params={})

        active = [s for s in steps if s.status == "success" and not s.undone]
        assert len(active) == 2
        assert active[0].step_number == 1
        assert steps[1].undone is True  # original step2
        assert steps[2].undone is False  # redone step2'


# ---------------------------------------------------------------------------
# Atomicity: redo failure leaves everything untouched
# ---------------------------------------------------------------------------


class TestAtomicity:
    def test_failed_redo_preserves_all_state(self, tmp_path: Path) -> None:
        """Atomicity: redo fails → artifacts.current(), previous(),
        and all step.undone flags unchanged."""
        mgr = ArtifactManager(workdir=tmp_path, disk_limit_bytes=1_000_000)
        a1 = _make_artifact(tmp_path, "step1.bin", b"step1", step_number=1)
        a2 = _make_artifact(tmp_path, "step2.bin", b"step2", step_number=2)
        a3 = _make_artifact(tmp_path, "step3.bin", b"step3", step_number=3)
        mgr.store(a1)
        mgr.store(a2)
        mgr.store(a3)

        steps = [_make_step(1), _make_step(2), _make_step(3)]
        mock_exec = MockExecutor(tmp_path, succeed=False)

        handler = CorrectionHandler(
            artifacts=mgr,
            steps=steps,
            executor=mock_exec,  # type: ignore[arg-type]
            resolver=MagicMock(spec=IntentResolver),
            workdir=tmp_path,
        )

        # Full snapshot.
        snap_current = mgr.current
        snap_previous = mgr.previous
        snap_undone = [s.undone for s in steps]
        snap_status = [s.status for s in steps]

        result = handler.redo(params={"bad": True})

        assert result.status == "error"
        assert mgr.current is snap_current
        assert mgr.previous is snap_previous
        assert [s.undone for s in steps] == snap_undone
        assert [s.status for s in steps] == snap_status

    def test_failed_redo_post_undo_preserves_state(self, tmp_path: Path) -> None:
        """Atomicity: post-undo redo fails → state unchanged after undo."""
        mgr = ArtifactManager(workdir=tmp_path, disk_limit_bytes=1_000_000)
        a1 = _make_artifact(tmp_path, "step1.bin", b"step1", step_number=1)
        a2 = _make_artifact(tmp_path, "step2.bin", b"step2", step_number=2)
        mgr.store(a1)
        mgr.store(a2)

        steps = [_make_step(1), _make_step(2)]
        mock_exec = MockExecutor(tmp_path, succeed=False)

        handler = CorrectionHandler(
            artifacts=mgr,
            steps=steps,
            executor=mock_exec,  # type: ignore[arg-type]
            resolver=MagicMock(spec=IntentResolver),
            workdir=tmp_path,
        )

        # Undo first.
        handler.undo()
        assert mgr.current is a1
        assert mgr.previous is None
        assert steps[1].undone is True

        # Snapshot post-undo state.
        snap_current = mgr.current
        snap_previous = mgr.previous
        snap_undone = [s.undone for s in steps]

        # Failed redo.
        result = handler.redo(params={})

        assert result.status == "error"
        assert mgr.current is snap_current
        assert mgr.previous is snap_previous
        assert [s.undone for s in steps] == snap_undone


# ---------------------------------------------------------------------------
# ISSUE 2: redo runs preflight checks
# ---------------------------------------------------------------------------


class TestRedoPreflightChecks:
    def test_redo_calls_preflight_planar_crs_check(self, tmp_path: Path) -> None:
        """Redo runs preflight.check_planar_crs before execute."""
        mgr = ArtifactManager(workdir=tmp_path, disk_limit_bytes=1_000_000)
        a1 = _make_artifact(tmp_path, "step1.bin", b"step1", step_number=1)
        a2 = _make_artifact(tmp_path, "step2.bin", b"step2", step_number=2)
        mgr.store(a1)
        mgr.store(a2)

        steps = [_make_step(1), _make_step(2)]
        mock_exec = MockExecutor(tmp_path, succeed=True)

        preflight = MagicMock(spec=PreflightChecker)
        preflight.check_planar_crs.return_value = PreflightResult(ok=True)
        preflight.check_disk.return_value = PreflightResult(ok=True)

        handler = CorrectionHandler(
            artifacts=mgr,
            steps=steps,
            executor=mock_exec,  # type: ignore[arg-type]
            resolver=MagicMock(spec=IntentResolver),
            workdir=tmp_path,
            preflight=preflight,
        )

        result = handler.redo(params={"to": "EPSG:4326"})

        assert result.status == "redone"
        preflight.check_planar_crs.assert_called_once()
        preflight.check_disk.assert_called_once()

    def test_redo_returns_error_when_preflight_fails(self, tmp_path: Path) -> None:
        """Redo returns error and doesn't execute when preflight fails."""
        mgr = ArtifactManager(workdir=tmp_path, disk_limit_bytes=1_000_000)
        a1 = _make_artifact(tmp_path, "step1.bin", b"step1", step_number=1)
        a2 = _make_artifact(tmp_path, "step2.bin", b"step2", step_number=2)
        mgr.store(a1)
        mgr.store(a2)

        steps = [_make_step(1), _make_step(2)]
        mock_exec = MockExecutor(tmp_path, succeed=True)

        preflight = MagicMock(spec=PreflightChecker)
        preflight.check_planar_crs.return_value = PreflightResult(
            ok=False, error="requires planar CRS",
        )
        preflight.check_disk.return_value = PreflightResult(ok=True)

        handler = CorrectionHandler(
            artifacts=mgr,
            steps=steps,
            executor=mock_exec,  # type: ignore[arg-type]
            resolver=MagicMock(spec=IntentResolver),
            workdir=tmp_path,
            preflight=preflight,
        )

        result = handler.redo(params={"to": "EPSG:4326"})

        assert result.status == "error"
        assert "planar CRS" in result.message
        # execute should NOT have been called
        assert mock_exec.call_count == 0

    def test_redo_returns_error_when_disk_preflight_fails(
        self, tmp_path: Path,
    ) -> None:
        """Redo returns error when disk preflight fails."""
        mgr = ArtifactManager(workdir=tmp_path, disk_limit_bytes=1_000_000)
        a1 = _make_artifact(tmp_path, "step1.bin", b"step1", step_number=1)
        a2 = _make_artifact(tmp_path, "step2.bin", b"step2", step_number=2)
        mgr.store(a1)
        mgr.store(a2)

        steps = [_make_step(1), _make_step(2)]
        mock_exec = MockExecutor(tmp_path, succeed=True)

        preflight = MagicMock(spec=PreflightChecker)
        preflight.check_planar_crs.return_value = PreflightResult(ok=True)
        preflight.check_disk.return_value = PreflightResult(
            ok=False, error="Insufficient disk space",
        )

        handler = CorrectionHandler(
            artifacts=mgr,
            steps=steps,
            executor=mock_exec,  # type: ignore[arg-type]
            resolver=MagicMock(spec=IntentResolver),
            workdir=tmp_path,
            preflight=preflight,
        )

        result = handler.redo(params={})

        assert result.status == "error"
        assert "disk" in result.message.lower()
        assert mock_exec.call_count == 0

    def test_redo_without_preflight_works_as_before(self, tmp_path: Path) -> None:
        """Redo still works when no PreflightChecker is provided (backwards compat)."""
        mgr = ArtifactManager(workdir=tmp_path, disk_limit_bytes=1_000_000)
        a1 = _make_artifact(tmp_path, "step1.bin", b"step1", step_number=1)
        a2 = _make_artifact(tmp_path, "step2.bin", b"step2", step_number=2)
        mgr.store(a1)
        mgr.store(a2)

        steps = [_make_step(1), _make_step(2)]
        mock_exec = MockExecutor(tmp_path, succeed=True)

        # No preflight parameter (backwards compat)
        handler = CorrectionHandler(
            artifacts=mgr,
            steps=steps,
            executor=mock_exec,  # type: ignore[arg-type]
            resolver=MagicMock(spec=IntentResolver),
            workdir=tmp_path,
        )

        result = handler.redo(params={"to": "EPSG:4326"})

        assert result.status == "redone"
        assert mock_exec.call_count == 1
