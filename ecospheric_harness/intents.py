"""Intent type definitions for the Ecospheric Agent Harness.

Parses function-calling response dicts into typed intent dataclasses,
and provides supporting types for tool registration, resolution, and
execution results.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, TypeAlias

from etp.describe import CommandDescriptor

# Artifact will be defined in ecospheric_harness.artifact (T1.3).
# Forward-compatible placeholder so this module is self-contained until
# artifact.py ships.  With ``from __future__ import annotations`` the
# annotation ``Artifact | None`` is a string at runtime; mypy resolves it
# via the alias below.
Artifact: TypeAlias = Any


# ---------------------------------------------------------------------------
# Core intent types
# ---------------------------------------------------------------------------


@dataclass
class OperationIntent:
    """A tool operation requested by the agent."""

    intent: str
    params: dict[str, Any] = field(default_factory=dict)


@dataclass
class UndoIntent:
    """Undo the last step."""

    intent: str = "undo"


@dataclass
class RedoIntent:
    """Redo a previously undone step."""

    intent: str = "redo"
    params: dict[str, Any] = field(default_factory=dict)


@dataclass
class CompleteIntent:
    """Signal that the agent has completed its work."""

    intent: str = "complete"
    summary: str = ""


@dataclass
class FailedIntent:
    """Signal that the agent has failed."""

    intent: str = "failed"
    reason: str = ""


# Union type for parse_intent return
Intent = OperationIntent | UndoIntent | RedoIntent | CompleteIntent | FailedIntent


def parse_intent(raw: dict[str, Any]) -> Intent:
    """Parse a function-calling response dict into a typed intent.

    Validates:
    - ``CompleteIntent`` requires a non-empty ``summary``.
    - ``FailedIntent`` requires a non-empty ``reason``.

    Raises:
        ValueError: If the intent string is missing, unknown, or fails validation.
    """
    intent = raw.get("intent")
    if not isinstance(intent, str) or not intent:
        raise ValueError("Missing or empty 'intent' field")

    params: dict[str, Any] = raw.get("params", {})

    match intent:
        case "undo":
            return UndoIntent()
        case "redo":
            return RedoIntent(params=params)
        case "complete":
            summary = raw.get("summary", "")
            if not isinstance(summary, str) or not summary.strip():
                raise ValueError("CompleteIntent requires a non-empty summary")
            return CompleteIntent(summary=summary)
        case "failed":
            reason = raw.get("reason", "")
            if not isinstance(reason, str) or not reason.strip():
                raise ValueError("FailedIntent requires a non-empty reason")
            return FailedIntent(reason=reason)
        case _:
            return OperationIntent(intent=intent, params=params)


# ---------------------------------------------------------------------------
# Tool registration & resolution
# ---------------------------------------------------------------------------


@dataclass
class RegisteredTool:
    """A tool registered in the harness registry."""

    name: str
    version: str
    binary: str
    commands: list[CommandDescriptor]


@dataclass
class IntentEntry:
    """A resolved mapping from intent string to tool + command."""

    intent: str
    description: str
    tool: RegisteredTool
    command: CommandDescriptor
    required_params: list[str]


@dataclass
class IntentOption:
    """A lightweight intent option for presentation to the agent."""

    intent: str
    description: str
    required_params: list[str]
    tool: str = ""
    command: str = ""
    data_type: str = ""


@dataclass
class ResolvedCall:
    """A fully resolved tool call ready for execution."""

    tool: RegisteredTool
    command: CommandDescriptor
    params: dict[str, Any]


@dataclass
class ResolutionError:
    """An error that occurred during intent resolution."""

    message: str


# ---------------------------------------------------------------------------
# Execution results
# ---------------------------------------------------------------------------


@dataclass
class CorrectionResult:
    """Result of a correction attempt after a failed execution."""

    status: str
    artifact: Artifact | None
    message: str


@dataclass
class PreflightResult:
    """Result of a pre-flight validation check."""

    ok: bool
    error: str = ""


@dataclass
class ExecuteResult:
    """Result of executing a tool command."""

    envelope: dict[str, Any]
    returncode: int
    output_path: Path
