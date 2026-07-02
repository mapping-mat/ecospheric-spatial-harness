"""Undo/redo correction handler for the Ecospheric Agent Harness.

Implements atomic undo and redo operations over a two-artifact sliding window,
with two redo paths: replace-current (no undo before redo) and post-undo
(undo was performed first).
"""

from __future__ import annotations

import time
from typing import Any

from ecospheric_harness.artifact import Artifact, ArtifactManager
from ecospheric_harness.executor import ToolExecutor
from ecospheric_harness.intents import CorrectionResult, ExecuteResult
from ecospheric_harness.preflight import PreflightChecker
from ecospheric_harness.resolver import IntentResolver
from ecospheric_harness.result import StepRecord
from ecospheric_harness.workspace import WorkspaceManager

# etp types used for _build_artifact signature
from etp.describe import CommandDescriptor


class CorrectionHandler:
    """Handles undo/redo corrections over the pipeline artifact window.

    Redo has two paths depending on whether an undo was performed first:

    1. **Replace-current** (no undo before redo): The target step is the
       current one. Input = previous. The old current is freed and replaced.
       Previous stays intact.
    2. **Post-undo** (undo was done first): The target step is the last
       undone step. Input = current (which is the previous artifact after
       undo). The window shifts: current→previous, new→current.

    Both paths execute into a fresh temp file and only mutate state on success.
    """

    def __init__(
        self,
        artifacts: ArtifactManager,
        steps: list[StepRecord],
        executor: ToolExecutor,
        resolver: IntentResolver,
        workspace: WorkspaceManager,
        preflight: PreflightChecker | None = None,
    ) -> None:
        self._artifacts = artifacts
        self._steps = steps
        self._executor = executor
        self._resolver = resolver
        self._workspace = workspace
        self._preflight = preflight

    def undo(self) -> CorrectionResult:
        """Revert the last successful step.

        Returns ``CorrectionResult(status="undone", ...)`` on success or
        ``CorrectionResult(status="error", ...)`` when there is nothing to
        undo.
        """
        if not self._artifacts.can_undo:
            return CorrectionResult(
                status="error",
                artifact=None,
                message="Cannot undo — no previous artifact to revert to",
            )

        # Mark last successful non-undone step as undone.
        for step in reversed(self._steps):
            if step.status == "success" and not step.undone:
                step.undone = True
                break

        restored = self._artifacts.undo()
        return CorrectionResult(status="undone", artifact=restored, message="")

    def redo(self, params: dict[str, Any]) -> CorrectionResult:
        """Re-execute the last step with new params.

        Atomic — a failed execution leaves artifacts and step state
        completely unchanged.
        """
        # 1. Find the last successful step (undone or not).
        target: StepRecord | None = None
        for step in reversed(self._steps):
            if step.status == "success":
                target = step
                break

        if target is None:
            return CorrectionResult(
                status="error",
                artifact=None,
                message="No step to redo",
            )

        # 2. Determine input artifact and mutation strategy.
        if target.undone:
            # POST-UNDO path: input is current (which was previous before undo).
            input_artifact = self._artifacts.current
            if input_artifact is None:
                return CorrectionResult(
                    status="error",
                    artifact=None,
                    message="No input artifact for redo",
                )
            use_store = True
        else:
            # REPLACE-CURRENT path: input is previous.
            input_artifact = self._artifacts.previous
            if input_artifact is None:
                return CorrectionResult(
                    status="error",
                    artifact=None,
                    message="No input artifact for redo",
                )
            use_store = False

        # 3. Execute into a fresh temp path — don't touch artifacts yet.
        #    First run preflight checks if a PreflightChecker is available.
        if self._preflight is not None:
            crs_result = self._preflight.check_planar_crs(
                target.command_ref, input_artifact,
            )
            if not crs_result.ok:
                return CorrectionResult(
                    status="error",
                    artifact=None,
                    message=crs_result.error or "Preflight CRS check failed",
                )

            disk_result = self._preflight.check_disk(input_artifact=input_artifact)
            if not disk_result.ok:
                return CorrectionResult(
                    status="error",
                    artifact=None,
                    message=disk_result.error or "Preflight disk check failed",
                )

        t0 = time.monotonic()
        result = self._executor.execute(
            target.tool_ref,
            target.command_ref,
            params,
            input_artifact,
            self._workspace,
        )
        duration_ms = int((time.monotonic() - t0) * 1000)

        # 4. Check for failure — atomic: no state mutation.
        if result.returncode != 0 or result.envelope.get("status") != "success":
            error_msg = (
                result.envelope.get("error", {}).get("message", "unknown")
                if isinstance(result.envelope.get("error"), dict)
                else "unknown"
            )
            return CorrectionResult(
                status="error",
                artifact=None,
                message=f"Redo execution failed: {error_msg}",
            )

        # 5. SUCCESS — atomically mutate state.
        if not target.undone:
            # Replace-current path: mark old step as undone.
            target.undone = True

        new_artifact = self._build_artifact(
            result, target.command_ref, step_number=len(self._steps) + 1,
        )

        if use_store:
            # Post-undo: shift window (current→previous, new→current).
            self._artifacts.store(new_artifact)
        else:
            # Replace-current: swap current, keep previous.
            self._artifacts.replace_current(new_artifact)

        # Append a new StepRecord for the redone step.
        self._steps.append(StepRecord(
            step_number=new_artifact.step_number,
            tool=target.tool,
            command=target.command,
            tool_ref=target.tool_ref,
            command_ref=target.command_ref,
            intent=target.intent,
            params=params,
            status="success",
            undone=False,
            envelope=result.envelope,
            duration_ms=duration_ms,
            output_path=result.output_path,
        ))

        return CorrectionResult(status="redone", artifact=new_artifact, message="")

    def _build_artifact(
        self,
        result: ExecuteResult,
        command: CommandDescriptor,
        step_number: int,
    ) -> Artifact:
        """Construct an :class:`Artifact` from an execution result envelope."""
        data: dict[str, Any] = result.envelope.get("data", {})

        fmt = data.get("format", "unknown")
        data_type = data.get("data_type", "unknown")

        # CRS: best-effort extraction.
        crs: str | None = (
            data.get("crs")
            or (data.get("crs_meta", {}) or {}).get("crs")
            or data.get("output_crs")
        )

        # Bounding box: best-effort extraction.
        bbox: list[float] | None = (
            data.get("bbox")
            or data.get("bounds")
            or data.get("extent")
        )

        return Artifact(
            path=result.output_path,
            envelope=result.envelope,
            format=fmt,
            data_type=data_type,
            crs=crs,
            bbox=bbox,
            step_number=step_number,
        )
