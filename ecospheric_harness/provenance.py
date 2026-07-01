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


def build_provenance_chain(steps: list[_StepLike]) -> list[dict[str, Any]]:
    """Build a provenance chain from successful, non-undone steps.

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