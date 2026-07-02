"""Evaluation runner for executing fixtures against the harness."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

from ecospheric_harness.eval.fixtures import EvalFixture

# Mapping from error_type to the set of statuses that satisfy it.
# "resolution" / "validation" / "preflight" → step was rejected before execution.
# "execution" → step ran but failed.
# "security" → step was blocked for security reasons (rejected or error).
_ERROR_TYPE_STATUSES: dict[str, frozenset[str]] = {
    "resolution": frozenset({"rejected"}),
    "validation": frozenset({"rejected"}),
    "preflight": frozenset({"rejected"}),
    "execution": frozenset({"error"}),
    "security": frozenset({"rejected", "error"}),
}


@dataclass
class StepResult:
    """Actual result of a single step."""

    intent: str
    status: str
    params: dict[str, Any]
    tool: str
    error_message: str | None = None
    duration_ms: int = 0


@dataclass
class FixtureResult:
    """Result of running a single fixture."""

    fixture_name: str
    passed: bool
    steps: list[StepResult] = field(default_factory=list)
    final_artifact: dict[str, Any] | None = None
    error: str | None = None
    duration_ms: int = 0
    token_usage: dict[str, int] = field(default_factory=dict)
    assertions: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _step_record_to_result(step: Any) -> StepResult:
    """Convert a PipelineResult StepRecord into a StepResult."""
    # StepRecord is not directly importable here without risk of circular deps,
    # so use getattr for safety.
    envelope = getattr(step, "envelope", None)
    error_msg: str | None = None
    if isinstance(envelope, dict):
        error_msg = envelope.get("error") or envelope.get("message")
    if error_msg is None:
        error_msg = getattr(step, "error_message", None)
    return StepResult(
        intent=getattr(step, "intent", ""),
        status=getattr(step, "status", ""),
        params=dict(getattr(step, "params", {})),
        tool=getattr(step, "tool", ""),
        error_message=error_msg,
        duration_ms=getattr(step, "duration_ms", 0),
    )


def _artifact_to_dict(artifact: Any) -> dict[str, Any] | None:
    """Normalise an ArtifactRecord (or dict / None) to a plain dict."""
    if artifact is None:
        return None
    if isinstance(artifact, dict):
        return artifact
    # ArtifactRecord-like object
    return {
        "data_type": getattr(artifact, "data_type", None),
        "format": getattr(artifact, "format", None),
        "crs": getattr(artifact, "crs", None),
        "bbox": getattr(artifact, "bbox", None),
    }


def _is_projected_crs(crs_str: str) -> bool:
    """Return True if *crs_str* represents a projected (non-geographic) CRS.

    Uses pyproj when available; otherwise falls back to string heuristics.
    """
    try:
        from pyproj import CRS

        crs = CRS(crs_str)
        return not crs.is_geographic
    except Exception:
        pass
    # Heuristic fallback
    upper = crs_str.upper()
    if "EPSG:3" in upper or "EPSG:4" in upper:
        # EPSG:326xx / EPSG:3857 = projected; EPSG:4326 = geographic
        return "EPSG:4326" not in upper
    if "UTM" in upper:
        return True
    return False


# ---------------------------------------------------------------------------
# EvalRunner
# ---------------------------------------------------------------------------


class EvalRunner:
    """Runs evaluation fixtures against the harness and asserts on results."""

    def __init__(self, api_key: str | None = None) -> None:
        self._api_key = api_key

    # -- public API ---------------------------------------------------------

    def run_fixture(self, fixture: EvalFixture) -> FixtureResult:
        """Run a single fixture and return results."""
        if fixture.skip_live and not self._api_key:
            return FixtureResult(
                fixture_name=fixture.name,
                passed=True,
                error="SKIPPED: no API key",
            )

        t0 = time.monotonic()
        assertions: list[str] = []

        try:
            from ecospheric_harness.__main__ import Harness

            harness = Harness(max_turns=fixture.max_turns)
            pipeline_result = harness.run(fixture.prompt)

            steps = [_step_record_to_result(s) for s in pipeline_result.steps]
            artifact = _artifact_to_dict(getattr(pipeline_result, "final_artifact", None))

            assertions.extend(self._assert_intents(fixture, steps))
            if fixture.expected_artifact is not None:
                assertions.extend(self._assert_artifact(fixture, artifact))
            if fixture.expected_error is not None:
                assertions.extend(self._assert_error(fixture, steps))

            passed = all(a.startswith("PASS") for a in assertions)
            return FixtureResult(
                fixture_name=fixture.name,
                passed=passed,
                steps=steps,
                final_artifact=artifact,
                duration_ms=int((time.monotonic() - t0) * 1000),
                assertions=assertions,
            )
        except Exception as exc:
            return FixtureResult(
                fixture_name=fixture.name,
                passed=False,
                duration_ms=int((time.monotonic() - t0) * 1000),
                assertions=[f"FAIL: exception — {exc}"],
                error=str(exc),
            )

    def run_fixtures(
        self,
        fixtures: list[EvalFixture],
        n: int = 1,
    ) -> list[FixtureResult]:
        """Run *n* iterations of each fixture for variance checking."""
        results: list[FixtureResult] = []
        for fixture in fixtures:
            run_results = [self.run_fixture(fixture) for _ in range(n)]
            if n == 1:
                results.extend(run_results)
            else:
                # For multiple runs, keep all but attach a summary to the
                # final returned result for convenience.
                results.extend(run_results)
        return results

    # -- assertion helpers --------------------------------------------------

    def _assert_intents(
        self,
        fixture: EvalFixture,
        steps: list[StepResult],
    ) -> list[str]:
        """Compare actual step intents against expected. Returns assertion messages."""
        expected = fixture.expected_intents
        msgs: list[str] = []

        if not expected:
            # No expected intents — used for negative/security cases where
            # we only assert on error behaviour.  Nothing to check here.
            return msgs

        if len(steps) < len(expected):
            msgs.append(
                f"FAIL: expected {len(expected)} intent step(s), "
                f"got {len(steps)}"
            )
            return msgs

        for idx, exp in enumerate(expected):
            actual = steps[idx]
            tag = f"[step {idx}]"

            if actual.intent != exp.intent:
                msgs.append(
                    f"FAIL: {tag} intent '{actual.intent}' != expected '{exp.intent}'"
                )

            if actual.status != exp.status:
                msgs.append(
                    f"FAIL: {tag} status '{actual.status}' != expected '{exp.status}'"
                )

            if exp.tool is not None and actual.tool and actual.tool != exp.tool:
                msgs.append(
                    f"FAIL: {tag} tool '{actual.tool}' != expected '{exp.tool}'"
                )

            if exp.params_subset:
                for key, val in exp.params_subset.items():
                    if key not in actual.params or actual.params[key] != val:
                        msgs.append(
                            f"FAIL: {tag} params[{key!r}] = "
                            f"{actual.params.get(key)!r} != {val!r}"
                        )

        if not msgs:
            msgs.append(
                f"PASS: {len(expected)} intent step(s) matched expectations"
            )
        return msgs

    def _assert_artifact(
        self,
        fixture: EvalFixture,
        artifact: dict[str, Any] | None,
    ) -> list[str]:
        """Check artifact properties. Returns assertion messages."""
        exp = fixture.expected_artifact
        if exp is None:
            return []

        msgs: list[str] = []

        if exp.exists and artifact is None:
            msgs.append("FAIL: expected artifact to exist but got None")
            return msgs

        if not exp.exists:
            if artifact is not None:
                msgs.append("FAIL: expected no artifact but one was produced")
            else:
                msgs.append("PASS: no artifact as expected")
            return msgs

        # artifact is not None below
        assert artifact is not None

        if exp.data_type is not None:
            actual_type = artifact.get("data_type")
            if actual_type != exp.data_type:
                msgs.append(
                    f"FAIL: artifact data_type '{actual_type}' != "
                    f"expected '{exp.data_type}'"
                )

        if exp.format is not None:
            actual_fmt = artifact.get("format")
            if actual_fmt != exp.format:
                msgs.append(
                    f"FAIL: artifact format '{actual_fmt}' != "
                    f"expected '{exp.format}'"
                )

        if exp.crs is not None:
            actual_crs = artifact.get("crs")
            if actual_crs != exp.crs:
                msgs.append(
                    f"FAIL: artifact crs '{actual_crs}' != expected '{exp.crs}'"
                )

        if exp.crs_type is not None:
            actual_crs = artifact.get("crs")
            if actual_crs is None:
                msgs.append("FAIL: artifact CRS is None, cannot check crs_type")
            else:
                projected = _is_projected_crs(str(actual_crs))
                if exp.crs_type == "projected" and not projected:
                    msgs.append(
                        f"FAIL: expected projected CRS, "
                        f"got '{actual_crs}' (geographic)"
                    )
                elif exp.crs_type == "geographic" and projected:
                    msgs.append(
                        f"FAIL: expected geographic CRS, "
                        f"got '{actual_crs}' (projected)"
                    )

        if exp.bbox_within is not None:
            actual_bbox = artifact.get("bbox")
            if actual_bbox is None:
                msgs.append("FAIL: artifact bbox is None, cannot check bbox_within")
            else:
                # bbox_within: [w, s, e, n]; artifact bbox must be within
                w, s, e, n = exp.bbox_within
                aw, as_, ae, an = actual_bbox[:4]
                if not (aw >= w and as_ >= s and ae <= e and an <= n):
                    msgs.append(
                        f"FAIL: artifact bbox {actual_bbox} not within "
                        f"expected bounds {exp.bbox_within}"
                    )

        if not msgs:
            msgs.append("PASS: artifact matched expectations")
        return msgs

    def _assert_error(
        self,
        fixture: EvalFixture,
        steps: list[StepResult],
    ) -> list[str]:
        """Check error behaviour for negative/security cases. Returns assertion messages."""
        exp = fixture.expected_error
        if exp is None:
            return []

        msgs: list[str] = []

        expected_statuses = _ERROR_TYPE_STATUSES.get(exp.error_type, frozenset({"error", "rejected"}))

        # Check at least one step has a qualifying error/rejected status
        error_steps = [s for s in steps if s.status in expected_statuses]
        if not error_steps:
            statuses_seen = [s.status for s in steps]
            msgs.append(
                f"FAIL: expected error_type '{exp.error_type}' (status in "
                f"{set(expected_statuses)}), but no step matched. "
                f"Statuses seen: {statuses_seen}"
            )
            return msgs

        # Check error_contains if specified
        if exp.error_contains is not None:
            found = any(
                s.error_message is not None and exp.error_contains in s.error_message
                for s in error_steps
            )
            if not found:
                all_errors = [
                    s.error_message for s in error_steps if s.error_message
                ]
                msgs.append(
                    f"FAIL: expected error message containing "
                    f"'{exp.error_contains}', got: {all_errors}"
                )

        if not msgs:
            msgs.append(f"PASS: error_type '{exp.error_type}' as expected")
        return msgs
