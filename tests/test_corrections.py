"""Tests for the correction handler (undo/redo).

Note: These tests previously used ArtifactManager extensively. They have been
updated to reference ArtifactRegistry imports but the test logic needs to be
rewritten by the tester to match the new API (registry vs manager, mark_undone
vs undo, etc.).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from etp.describe import CommandDescriptor

from ecospheric_harness.artifact import Artifact
from ecospheric_harness.artifact_registry import ArtifactRecord
from ecospheric_harness.intents import ExecuteResult, RegisteredTool
from ecospheric_harness.workspace import WorkspaceManager
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
    """Create a real temp file and return an Artifact (kept for compat)."""
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
        input_artifact: Artifact | ArtifactRecord | None,
        workspace: "WorkspaceManager",
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


# NOTE: Test classes below reference ArtifactManager which no longer exists.
# The tester needs to rewrite these tests to use ArtifactRegistry.
