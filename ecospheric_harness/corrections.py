"""Undo/redo correction handler for the Ecospheric Agent Harness.

Implements undo and redo operations over the artifact registry,
with the new named artifact system.
"""

from __future__ import annotations

import time
from typing import Any

from ecospheric_harness.artifact_metadata import (
    extract_crs,
    extract_or_derive_bbox,
)
from ecospheric_harness.artifact_registry import ArtifactRegistry
from ecospheric_harness.executor import ToolExecutor
from ecospheric_harness.intents import CorrectionResult
from ecospheric_harness.preflight import PreflightChecker
from ecospheric_harness.resolver import IntentResolver
from ecospheric_harness.result import StepRecord
from ecospheric_harness.workspace import WorkspaceManager

# etp types used for signatures
from etp.describe import CommandDescriptor  # noqa: F401


class CorrectionHandler:
    """Handles undo/redo corrections over the pipeline artifact registry.

    With the named artifact registry, undo marks the most recent non-undone
    artifact as undone. Redo re-executes the last undone step with new params.
    """

    def __init__(
        self,
        registry: ArtifactRegistry,
        steps: list[StepRecord],
        executor: ToolExecutor,
        resolver: IntentResolver,
        workspace: WorkspaceManager,
        preflight: PreflightChecker | None = None,
    ) -> None:
        self._registry = registry
        self._steps = steps
        self._executor = executor
        self._resolver = resolver
        self._workspace = workspace
        self._preflight = preflight

    def undo(self) -> CorrectionResult:
        """Mark the most recent non-undone artifact as undone.

        Returns ``CorrectionResult(status="undone", ...)`` on success or
        ``CorrectionResult(status="error", ...)`` when there is nothing to
        undo.
        """
        if not self._registry.can_undo:
            return CorrectionResult(
                status="error",
                artifact=None,
                message="Cannot undo — no artifacts to undo",
            )

        # Find the most recent non-undone artifact
        recent = self._registry.get_recent(1)
        if not recent:
            return CorrectionResult(
                status="error",
                artifact=None,
                message="Cannot undo — no active artifact found",
            )

        artifact = recent[0]
        # Mark as undone
        self._registry.mark_undone(artifact.artifact_id)

        # Mark last successful non-undone step as undone
        for step in reversed(self._steps):
            if step.status == "success" and not step.undone:
                step.undone = True
                break

        return CorrectionResult(status="undone", artifact=artifact, message="")

    def redo(self, params: dict[str, Any]) -> CorrectionResult:
        """Re-execute the last undone step with new params.

        Atomic — a failed execution leaves artifacts and step state
        completely unchanged.
        """
        # 1. Find the last undone step
        target: StepRecord | None = None
        for step in reversed(self._steps):
            if step.undone:
                target = step
                break

        if target is None:
            return CorrectionResult(
                status="error",
                artifact=None,
                message="No undone step to redo",
            )

        # 2. Get the input artifact (the most recent non-undone)
        input_artifact = self._registry.current
        if input_artifact is None:
            return CorrectionResult(
                status="error",
                artifact=None,
                message="No input artifact for redo",
            )

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
        # Keep the target step marked as undone — the new step replaces it in provenance.
        # (Don't clear target.undone; the new step carries the redo result.)

        # Register new artifact — use the shared metadata helpers so
        # ESE's `to_crs`/`from_crs` and provenance `crs_working_crs`
        # keys are recognized, and bbox is derived from the output
        # file when the envelope is silent.
        redo_data_type = result.envelope.get("data", {}).get("data_type", "unknown")
        new_artifact = self._registry.register(
            path=result.output_path,
            format=result.envelope.get("data", {}).get("format", "unknown"),
            data_type=redo_data_type,
            crs=extract_crs(result.envelope),
            bbox=extract_or_derive_bbox(
                result.envelope, result.output_path, data_type=redo_data_type,
            ),
            step_number=len(self._steps) + 1,
            envelope=result.envelope,
            parent_ids=[input_artifact.artifact_id] if input_artifact else [],
            intent=target.intent,
            tool_name=target.tool,
            tool_version=getattr(target.tool_ref, "version", "") if target.tool_ref else "",
            command_name=target.command,
            params=params,
            duration_ms=duration_ms,
        )

        # Append a new StepRecord for the redone step
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
