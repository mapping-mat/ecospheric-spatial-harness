"""Pipeline result tracking for the Ecospheric Agent Harness.

Provides ``StepRecord`` for individual execution steps and ``PipelineResult``
for the overall pipeline outcome.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class StepRecord:
    """Record of a single execution step in the pipeline."""

    step_number: int
    tool: str  # tool name for display
    command: str  # CommandDescriptor.name
    tool_ref: Any = None  # RegisteredTool reference
    command_ref: Any = None  # CommandDescriptor reference
    intent: str = ""
    params: dict[str, Any] = field(default_factory=dict)
    status: str = ""  # "success" | "error" | "rejected"
    undone: bool = False
    envelope: dict[str, Any] | None = None
    duration_ms: int = 0
    is_search: bool = False
    output_path: Path | None = None


@dataclass
class PipelineResult:
    """Aggregate result of a full pipeline run."""

    steps: list[StepRecord]
    final_artifact: Any | None  # Artifact instance when available
    provenance_chain: list[dict[str, Any]]

    def summary(self) -> str:
        """Human-readable summary of the pipeline result.

        Includes step count, successful steps, corrections applied,
        and final artifact format/data_type when present.
        """
        total = len(self.steps)
        active = [s for s in self.steps if not s.undone]
        successful = [s for s in active if s.status == "success"]
        failed = [s for s in active if s.status in ("error", "rejected")]

        lines: list[str] = []
        lines.append(
            f"Pipeline: {total} step(s), "
            f"{len(successful)} successful, {len(failed)} failed"
        )

        if self.provenance_chain:
            lines.append(f"Corrections applied: {len(self.provenance_chain)}")

        if self.final_artifact is not None:
            artifact = self.final_artifact
            if isinstance(artifact, dict):
                fmt = artifact.get("format", "unknown")
                dtype = artifact.get("data_type", "unknown")
            else:
                fmt = getattr(artifact, "format", "unknown")
                dtype = getattr(artifact, "data_type", "unknown")
            lines.append(f"Final artifact: format={fmt}, data_type={dtype}")

        return "\n".join(lines)
