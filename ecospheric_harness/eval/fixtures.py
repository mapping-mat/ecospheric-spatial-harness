"""Fixture dataclasses for evaluation test cases."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True, slots=True)
class IntentExpectation:
    """Expected intent in the pipeline's step sequence."""

    intent: str
    params_subset: dict[str, Any] | None = None  # key-values that MUST appear in step.params
    tool: str | None = None  # expected tool name
    status: str = "success"  # "success" | "error" | "rejected"


@dataclass(frozen=True, slots=True)
class ArtifactExpectation:
    """Expected properties of the final artifact."""

    exists: bool = True
    data_type: str | None = None
    format: str | None = None
    crs: str | None = None
    crs_type: str | None = None  # "projected" | "geographic"
    bbox_within: list[float] | None = None  # [w, s, e, n]; artifact bbox must be within


@dataclass(frozen=True, slots=True)
class ErrorExpectation:
    """Expected error behavior for negative/security cases."""

    error_type: str  # "resolution" | "validation" | "preflight" | "execution" | "security"
    error_contains: str | None = None  # substring in error message


@dataclass(frozen=True, slots=True)
class EvalFixture:
    """A single evaluation case."""

    name: str
    prompt: str
    tags: list[str] = field(default_factory=list)
    # "single-step", "multi-step", "negative", "security", "raster"
    expected_intents: list[IntentExpectation] = field(default_factory=list)
    expected_artifact: ArtifactExpectation | None = None
    expected_error: ErrorExpectation | None = None
    max_turns: int = 15
    skip_live: bool = False  # if True, requires API key + network
