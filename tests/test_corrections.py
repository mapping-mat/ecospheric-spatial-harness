"""Tests for the CorrectionHandler with ArtifactRegistry."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ecospheric_harness.artifact_registry import ArtifactRecord, ArtifactRegistry
from ecospheric_harness.corrections import CorrectionHandler
from ecospheric_harness.intents import CorrectionResult, ExecuteResult, RegisteredTool
from ecospheric_harness.result import StepRecord
from ecospheric_harness.workspace import WorkspaceManager

from etp.describe import CommandDescriptor


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_registry(tmp_path: Path, *, disk_limit_bytes: int = 10_000_000) -> ArtifactRegistry:
    """Create a fresh ArtifactRegistry backed by a temporary workspace."""
    workspace = WorkspaceManager(
        workspace_root=tmp_path / "sessions",
        disk_limit_bytes=disk_limit_bytes,
    )
    return ArtifactRegistry(workspace=workspace, disk_limit_bytes=disk_limit_bytes)


def _make_test_record(
    registry: ArtifactRegistry,
    intent: str = "test",
    *,
    file_size: int = 100,
) -> ArtifactRecord:
    """Create and register a minimal test artifact."""
    path = registry._workspace.create_temp_path(suffix=".bin")
    path.write_bytes(b"x" * file_size)
    return registry.register(
        path=path,
        format="geotiff",
        data_type="raster",
        crs="EPSG:4326",
        bbox=[-122.5, 39.7, -122.3, 39.8],
        step_number=len(registry.list_all()) + 1,
        envelope={"status": "success", "data": {}},
        parent_ids=[],
        intent=intent,
        tool_name="ese",
        tool_version="1.0.0",
        command_name="test command",
        params={},
        duration_ms=100,
        is_search=False,
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
        input_artifact: Any,
        workspace: WorkspaceManager,
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


class MockResolver:
    """Mock IntentResolver."""

    def resolve(self, intent: str, params: dict[str, Any], current_artifact: Any) -> Any:
        return None


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestCorrectionHandlerUndo:
    def test_undo_marks_most_recent_undone(self, tmp_path: Path) -> None:
        registry = _make_registry(tmp_path)
        r1 = _make_test_record(registry, intent="clip")
        r2 = _make_test_record(registry, intent="fetch")
        r3 = _make_test_record(registry, intent="clip")

        steps = [
            _make_step(1),
            _make_step(2),
            _make_step(3),
        ]

        executor = MockExecutor(tmp_path)
        resolver = MockResolver()
        workspace = registry._workspace

        handler = CorrectionHandler(
            registry=registry,
            steps=steps,
            executor=executor,
            resolver=resolver,
            workspace=workspace,
        )

        handler.undo()

        # r3 should be marked as undone
        assert r3.undone is True
        # r1 and r2 should not be undone
        assert r1.undone is False
        assert r2.undone is False

    def test_undo_returns_correction_result(self, tmp_path: Path) -> None:
        registry = _make_registry(tmp_path)
        record = _make_test_record(registry)

        steps = [_make_step(1)]
        executor = MockExecutor(tmp_path)
        resolver = MockResolver()
        workspace = registry._workspace

        handler = CorrectionHandler(
            registry=registry,
            steps=steps,
            executor=executor,
            resolver=resolver,
            workspace=workspace,
        )

        result = handler.undo()

        assert isinstance(result, CorrectionResult)
        assert result.status == "undone"
        assert result.artifact is not None
        assert result.artifact.artifact_id == record.artifact_id

    def test_undo_when_nothing_to_undo(self, tmp_path: Path) -> None:
        registry = _make_registry(tmp_path)

        steps: list[StepRecord] = []
        executor = MockExecutor(tmp_path)
        resolver = MockResolver()
        workspace = registry._workspace

        handler = CorrectionHandler(
            registry=registry,
            steps=steps,
            executor=executor,
            resolver=resolver,
            workspace=workspace,
        )

        result = handler.undo()

        assert result.status == "error"
        assert result.artifact is None
        assert "Cannot undo" in result.message


class TestCorrectionHandlerRedo:
    def test_redo_with_params_re_executes(self, tmp_path: Path) -> None:
        registry = _make_registry(tmp_path)
        _make_test_record(registry, intent="clip")

        step = _make_step(1)
        step.undone = True
        steps = [step]

        executor = MockExecutor(tmp_path)
        resolver = MockResolver()
        workspace = registry._workspace

        handler = CorrectionHandler(
            registry=registry,
            steps=steps,
            executor=executor,
            resolver=resolver,
            workspace=workspace,
        )

        new_params = {"buffer": 500}
        result = handler.redo(new_params)

        # Executor should have been called
        assert executor.call_count == 1
        assert executor.last_params == new_params

        # Result should be successful
        assert result.status == "redone"
        assert result.artifact is not None

    def test_redo_clears_undone_flag(self, tmp_path: Path) -> None:
        registry = _make_registry(tmp_path)
        # Create an artifact that will be "current" for the redo input
        _make_test_record(registry, intent="base")
        _make_test_record(registry, intent="clip")

        step = _make_step(2, undone=True)
        steps = [_make_step(1), step]

        executor = MockExecutor(tmp_path)
        resolver = MockResolver()
        workspace = registry._workspace

        handler = CorrectionHandler(
            registry=registry,
            steps=steps,
            executor=executor,
            resolver=resolver,
            workspace=workspace,
        )

        # Before redo, step is undone
        assert step.undone is True

        result = handler.redo({"new_param": "value"})

        # A new step was appended, and the result artifact is active
        assert result.status == "redone"
        assert result.artifact is not None
        assert result.artifact.undone is False

        # Verify the new artifact is in the registry
        loaded = registry.get(result.artifact.artifact_id)
        assert loaded is not None
