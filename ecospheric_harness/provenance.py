"""Provenance chain construction for the Ecospheric Agent Harness."""

from __future__ import annotations

from typing import Any, Protocol


class _StepLike(Protocol):
    step_number: int
    tool: str
    command: str
    intent: str
    params: dict[str, Any]
    status: str
    undone: bool
    duration_ms: int


class _ArtifactLike(Protocol):
    """Protocol for artifact-like objects (ArtifactRecord or Artifact)."""
    artifact_id: str
    intent: str
    tool_name: str
    command_name: str
    params: dict[str, Any]
    parent_ids: list[str]
    undone: bool
    evicted: bool
    duration_ms: int


def build_provenance_chain(steps: list[_StepLike]) -> list[dict[str, Any]]:
    """Build a provenance chain from successful, non-undone steps (legacy API).

    Filters steps to those with ``status == "success"`` and ``undone == False``,
    then returns a list of dicts in ascending step order.
    """
    surviving = sorted(
        (s for s in steps if s.status == "success" and not s.undone),
        key=lambda s: s.step_number,
    )
    return [
        {
            "step": s.step_number,
            "tool": s.tool,
            "command": s.command,
            "intent": s.intent,
            "params": s.params,
            "duration_ms": s.duration_ms,
        }
        for s in surviving
    ]


def build_provenance_from_dag(
    artifacts: dict[str, Any],
    current_id: str | None,
) -> list[dict[str, Any]]:
    """Build a provenance chain from the DAG by walking parent_ids backward.

    Args:
        artifacts: Dict of artifact_id -> ArtifactRecord-like object
        current_id: The ID of the current (final) artifact, or None

    Returns:
        A list of dicts in ascending step order with provenance info.
    """
    if current_id is None:
        return []

    # Walk backward from current to build the chain
    chain: list[dict[str, Any]] = []
    visited: set[str] = set()
    stack: list[str] = [current_id]

    while stack:
        aid = stack.pop()
        if aid in visited:
            continue
        visited.add(aid)

        rec = artifacts.get(aid)
        if rec is None or rec.evicted:
            continue

        chain.append(
            {
                "artifact_id": aid,
                "intent": rec.intent,
                "tool": rec.tool_name,
                "command": rec.command_name,
                "params": rec.params,
                "parent_ids": rec.parent_ids,
                "duration_ms": rec.duration_ms,
                "status": "undone" if rec.undone else "success",
            }
        )

        # Walk parents
        for parent_id in rec.parent_ids:
            if parent_id not in visited:
                stack.append(parent_id)

    # Sort by step_number (derived from suffix) for deterministic output
    # Extract step number from artifact_id like "search_osm_001" -> 1
    def _extract_step(aid: str) -> int:
        parts = aid.rsplit("_", 1)
        if len(parts) == 2 and parts[1].isdigit():
            return int(parts[1])
        return 0

    chain.sort(key=lambda x: _extract_step(x["artifact_id"]))
    return chain
