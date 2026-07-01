from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from ecospheric_harness.provenance import build_provenance_chain


@dataclass
class MockStep:
    step_number: int
    tool: str
    command: str
    intent: str = ""
    params: dict[str, Any] = field(default_factory=dict)
    status: str = ""
    undone: bool = False
    envelope: dict[str, Any] | None = None
    duration_ms: int = 0
    is_search: bool = False


def test_simple_two_step_chain() -> None:
    steps = [
        MockStep(step_number=1, tool="bash", command="ls", status="success", duration_ms=50),
        MockStep(step_number=2, tool="bash", command="pwd", status="success", duration_ms=30),
    ]
    chain = build_provenance_chain(steps)
    assert len(chain) == 2
    assert chain[0]["step"] == 1
    assert chain[1]["step"] == 2


def test_chain_with_undone_step() -> None:
    steps = [
        MockStep(step_number=1, tool="bash", command="ls", status="success", duration_ms=50),
        MockStep(step_number=2, tool="bash", command="rm", status="success", undone=True, duration_ms=20),
        MockStep(step_number=3, tool="bash", command="pwd", status="success", duration_ms=30),
    ]
    chain = build_provenance_chain(steps)
    assert len(chain) == 2
    assert chain[0]["step"] == 1
    assert chain[1]["step"] == 3


def test_chain_with_undo_and_redo() -> None:
    steps = [
        MockStep(step_number=1, tool="bash", command="ls", status="success", duration_ms=50),
        MockStep(step_number=2, tool="bash", command="rm", status="success", undone=True, duration_ms=20),
        MockStep(step_number=3, tool="bash", command="ls", status="success", duration_ms=40),
    ]
    chain = build_provenance_chain(steps)
    assert len(chain) == 2
    assert chain[0]["step"] == 1
    assert chain[1]["step"] == 3


def test_chain_with_undo_redo_undo_after_redo() -> None:
    steps = [
        MockStep(step_number=1, tool="bash", command="ls", status="success", duration_ms=50),
        MockStep(step_number=2, tool="bash", command="rm", status="success", undone=True, duration_ms=20),
        MockStep(step_number=3, tool="bash", command="ls", status="success", undone=True, duration_ms=40),
    ]
    chain = build_provenance_chain(steps)
    assert len(chain) == 1
    assert chain[0]["step"] == 1


def test_empty_steps_list() -> None:
    chain = build_provenance_chain([])
    assert chain == []


def test_all_failed_steps() -> None:
    steps = [
        MockStep(step_number=1, tool="bash", command="ls", status="error", duration_ms=50),
        MockStep(step_number=2, tool="bash", command="pwd", status="error", duration_ms=30),
    ]
    chain = build_provenance_chain(steps)
    assert chain == []


def test_mixed_statuses() -> None:
    steps = [
        MockStep(step_number=1, tool="bash", command="ls", status="success", duration_ms=50),
        MockStep(step_number=2, tool="bash", command="rm", status="error", duration_ms=20),
        MockStep(step_number=3, tool="bash", command="pwd", status="success", duration_ms=30),
    ]
    chain = build_provenance_chain(steps)
    assert len(chain) == 2
    assert chain[0]["step"] == 1
    assert chain[1]["step"] == 3


def test_verify_dict_contents() -> None:
    steps = [
        MockStep(
            step_number=1,
            tool="bash",
            command="ls -la",
            intent="list files",
            params={"path": "/tmp"},
            status="success",
            duration_ms=150,
        ),
    ]
    chain = build_provenance_chain(steps)
    assert len(chain) == 1
    entry = chain[0]
    assert set(entry.keys()) == {"step", "tool", "command", "intent", "params", "duration_ms"}
    assert entry["step"] == 1
    assert entry["tool"] == "bash"
    assert entry["command"] == "ls -la"
    assert entry["intent"] == "list files"
    assert entry["params"] == {"path": "/tmp"}
    assert entry["duration_ms"] == 150