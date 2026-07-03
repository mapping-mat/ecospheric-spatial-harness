"""Unit tests for the evaluation harness (eval fixtures, runner, cases).

These tests verify the eval framework itself, NOT live execution of fixtures.
"""

from __future__ import annotations

from ecospheric_harness.eval.cases import FIXTURES
from ecospheric_harness.eval.fixtures import (
    ArtifactExpectation,
    ErrorExpectation,
    EvalFixture,
    IntentExpectation,
)
from ecospheric_harness.eval.runner import (
    EvalRunner,
    FixtureResult,
    StepResult,
)

# Known-valid tags used across all fixture definitions.
_VALID_TAGS = frozenset({
    "single-step",
    "multi-step",
    "negative",
    "security",
    "raster",
    "live",
    "phase2",
    "preflight",
    "validation",
})


# ---------------------------------------------------------------------------
# Dataclass construction
# ---------------------------------------------------------------------------


class TestFixtureDataclasses:
    """Test fixture dataclass creation and fields."""

    def test_fixture_dataclass_creation(self) -> None:
        """EvalFixture can be created with all required fields."""
        fixture = EvalFixture(
            name="test_fixture",
            prompt="Test prompt",
        )
        assert fixture.name == "test_fixture"
        assert fixture.prompt == "Test prompt"
        assert fixture.tags == []
        assert fixture.expected_intents == []
        assert fixture.expected_artifact is None
        assert fixture.expected_error is None
        assert fixture.max_turns == 15
        assert fixture.skip_live is False

    def test_intent_expectation_defaults(self) -> None:
        ie = IntentExpectation(intent="search_osm")
        assert ie.params_subset is None
        assert ie.tool is None
        assert ie.status == "success"

    def test_artifact_expectation_defaults(self) -> None:
        ae = ArtifactExpectation()
        assert ae.exists is True
        assert ae.data_type is None
        assert ae.format is None
        assert ae.crs is None
        assert ae.crs_type is None
        assert ae.bbox_within is None

    def test_error_expectation_fields(self) -> None:
        ee = ErrorExpectation(error_type="security", error_contains="blocked")
        assert ee.error_type == "security"
        assert ee.error_contains == "blocked"

    def test_fixture_result_dataclass(self) -> None:
        """FixtureResult round-trips correctly."""
        fr = FixtureResult(fixture_name="test", passed=True)
        assert fr.fixture_name == "test"
        assert fr.passed is True
        assert fr.steps == []
        assert fr.final_artifact is None
        assert fr.error is None
        assert fr.duration_ms == 0
        assert fr.token_usage == {}
        assert fr.assertions == []

    def test_step_result_dataclass(self) -> None:
        sr = StepResult(
            intent="search_osm",
            status="success",
            params={"place": "Chico"},
            tool="edd",
        )
        assert sr.intent == "search_osm"
        assert sr.status == "success"
        assert sr.params == {"place": "Chico"}
        assert sr.tool == "edd"
        assert sr.error_message is None
        assert sr.duration_ms == 0


# ---------------------------------------------------------------------------
# Runner initialisation
# ---------------------------------------------------------------------------


class TestRunnerInit:
    def test_runner_initialization(self) -> None:
        runner = EvalRunner()
        assert runner._api_key is None

    def test_runner_with_api_key(self) -> None:
        runner = EvalRunner(api_key="test-key")
        assert runner._api_key == "test-key"


# ---------------------------------------------------------------------------
# Assertion logic
# ---------------------------------------------------------------------------


def _make_runner() -> EvalRunner:
    return EvalRunner()


class TestAssertIntents:
    def test_assert_intents_match(self) -> None:
        """Matching intents pass."""
        fixture = EvalFixture(
            name="t",
            prompt="p",
            expected_intents=[
                IntentExpectation(intent="search_osm", tool="edd", status="success"),
                IntentExpectation(intent="complete", status="success"),
            ],
        )
        steps = [
            StepResult(intent="search_osm", status="success", params={}, tool="edd"),
            StepResult(intent="complete", status="success", params={}, tool=""),
        ]
        runner = _make_runner()
        msgs = runner._assert_intents(fixture, steps)
        assert all(m.startswith("PASS") for m in msgs)

    def test_assert_intents_mismatch(self) -> None:
        """Mismatching intents fail."""
        fixture = EvalFixture(
            name="t",
            prompt="p",
            expected_intents=[
                IntentExpectation(intent="buffer", tool="ese", status="success"),
            ],
        )
        steps = [
            StepResult(intent="search_osm", status="success", params={}, tool="edd"),
        ]
        runner = _make_runner()
        msgs = runner._assert_intents(fixture, steps)
        assert any(m.startswith("FAIL") for m in msgs)

    def test_assert_intents_count_mismatch(self) -> None:
        """Fewer actual steps than expected produces a failure."""
        fixture = EvalFixture(
            name="t",
            prompt="p",
            expected_intents=[
                IntentExpectation(intent="search_osm"),
                IntentExpectation(intent="complete"),
            ],
        )
        steps = [
            StepResult(intent="search_osm", status="success", params={}, tool="edd"),
        ]
        runner = _make_runner()
        msgs = runner._assert_intents(fixture, steps)
        assert any("expected 2 intent step(s), got 1" in m for m in msgs)

    def test_assert_intents_empty_expected(self) -> None:
        """Empty expected_intents is a no-op (negative/security cases)."""
        fixture = EvalFixture(name="t", prompt="p", expected_intents=[])
        steps = [StepResult(intent="anything", status="error", params={}, tool="")]
        runner = _make_runner()
        msgs = runner._assert_intents(fixture, steps)
        assert msgs == []

    def test_assert_intents_params_subset(self) -> None:
        """params_subset check verifies key-value inclusion."""
        fixture = EvalFixture(
            name="t",
            prompt="p",
            expected_intents=[
                IntentExpectation(
                    intent="buffer",
                    params_subset={"distance": 500},
                ),
            ],
        )
        steps = [
            StepResult(
                intent="buffer",
                status="success",
                params={"distance": 500, "unit": "meters"},
                tool="ese",
            ),
        ]
        runner = _make_runner()
        msgs = runner._assert_intents(fixture, steps)
        assert all(m.startswith("PASS") for m in msgs)

    def test_assert_intents_params_subset_mismatch(self) -> None:
        """params_subset mismatch produces FAIL."""
        fixture = EvalFixture(
            name="t",
            prompt="p",
            expected_intents=[
                IntentExpectation(
                    intent="buffer",
                    params_subset={"distance": 100},
                ),
            ],
        )
        steps = [
            StepResult(
                intent="buffer",
                status="success",
                params={"distance": 500},
                tool="ese",
            ),
        ]
        runner = _make_runner()
        msgs = runner._assert_intents(fixture, steps)
        assert any(m.startswith("FAIL") for m in msgs)

    def test_assert_intents_status_mismatch(self) -> None:
        fixture = EvalFixture(
            name="t",
            prompt="p",
            expected_intents=[
                IntentExpectation(intent="search_osm", status="error"),
            ],
        )
        steps = [
            StepResult(intent="search_osm", status="success", params={}, tool="edd"),
        ]
        runner = _make_runner()
        msgs = runner._assert_intents(fixture, steps)
        assert any("status" in m and m.startswith("FAIL") for m in msgs)


class TestAssertArtifact:
    def test_assert_artifact_match(self) -> None:
        """Matching artifact properties pass."""
        fixture = EvalFixture(
            name="t",
            prompt="p",
            expected_artifact=ArtifactExpectation(
                data_type="vector",
                crs_type="geographic",
            ),
        )
        artifact = {
            "data_type": "vector",
            "format": "geojson",
            "crs": "EPSG:4326",
            "bbox": [-122.0, 39.0, -121.0, 40.0],
        }
        runner = _make_runner()
        msgs = runner._assert_artifact(fixture, artifact)
        assert all(m.startswith("PASS") for m in msgs)

    def test_assert_artifact_missing(self) -> None:
        """Expected artifact but None fails."""
        fixture = EvalFixture(
            name="t",
            prompt="p",
            expected_artifact=ArtifactExpectation(exists=True),
        )
        runner = _make_runner()
        msgs = runner._assert_artifact(fixture, None)
        assert any(m.startswith("FAIL") for m in msgs)

    def test_assert_artifact_data_type_mismatch(self) -> None:
        fixture = EvalFixture(
            name="t",
            prompt="p",
            expected_artifact=ArtifactExpectation(data_type="raster"),
        )
        artifact = {"data_type": "vector", "crs": "EPSG:4326"}
        runner = _make_runner()
        msgs = runner._assert_artifact(fixture, artifact)
        assert any("data_type" in m and m.startswith("FAIL") for m in msgs)

    def test_assert_artifact_projected_crs(self) -> None:
        fixture = EvalFixture(
            name="t",
            prompt="p",
            expected_artifact=ArtifactExpectation(crs_type="projected"),
        )
        artifact = {"data_type": "raster", "crs": "EPSG:3857"}
        runner = _make_runner()
        msgs = runner._assert_artifact(fixture, artifact)
        assert all(m.startswith("PASS") for m in msgs)

    def test_assert_artifact_bbox_within_pass(self) -> None:
        fixture = EvalFixture(
            name="t",
            prompt="p",
            expected_artifact=ArtifactExpectation(
                bbox_within=[-125.0, 32.0, -114.0, 42.0],
            ),
        )
        artifact = {
            "data_type": "vector",
            "crs": "EPSG:4326",
            "bbox": [-122.0, 39.0, -121.0, 40.0],
        }
        runner = _make_runner()
        msgs = runner._assert_artifact(fixture, artifact)
        assert all(m.startswith("PASS") for m in msgs)

    def test_assert_artifact_bbox_within_fail(self) -> None:
        fixture = EvalFixture(
            name="t",
            prompt="p",
            expected_artifact=ArtifactExpectation(
                bbox_within=[-122.0, 39.5, -121.0, 40.0],
            ),
        )
        artifact = {
            "data_type": "vector",
            "crs": "EPSG:4326",
            "bbox": [-123.0, 39.0, -121.0, 40.0],
        }
        runner = _make_runner()
        msgs = runner._assert_artifact(fixture, artifact)
        assert any("bbox" in m and m.startswith("FAIL") for m in msgs)

    def test_assert_artifact_none_expectation(self) -> None:
        """If fixture has no artifact expectation, returns empty."""
        fixture = EvalFixture(name="t", prompt="p", expected_artifact=None)
        runner = _make_runner()
        msgs = runner._assert_artifact(fixture, {"data_type": "vector"})
        assert msgs == []


class TestAssertError:
    def test_assert_error_match(self) -> None:
        """Expected error type matches when step has rejected status."""
        fixture = EvalFixture(
            name="t",
            prompt="p",
            expected_error=ErrorExpectation(error_type="resolution"),
        )
        steps = [
            StepResult(intent="buffer", status="rejected", params={}, tool=""),
        ]
        runner = _make_runner()
        msgs = runner._assert_error(fixture, steps)
        assert all(m.startswith("PASS") for m in msgs)

    def test_assert_error_execution(self) -> None:
        fixture = EvalFixture(
            name="t",
            prompt="p",
            expected_error=ErrorExpectation(error_type="execution"),
        )
        steps = [
            StepResult(intent="buffer", status="error", params={}, tool="ese", error_message="segfault"),
        ]
        runner = _make_runner()
        msgs = runner._assert_error(fixture, steps)
        assert all(m.startswith("PASS") for m in msgs)

    def test_assert_error_no_error(self) -> None:
        """Expected error but all steps succeeded fails."""
        fixture = EvalFixture(
            name="t",
            prompt="p",
            expected_error=ErrorExpectation(error_type="resolution"),
        )
        steps = [
            StepResult(intent="buffer", status="success", params={}, tool="ese"),
        ]
        runner = _make_runner()
        msgs = runner._assert_error(fixture, steps)
        assert any(m.startswith("FAIL") for m in msgs)

    def test_assert_error_contains_pass(self) -> None:
        fixture = EvalFixture(
            name="t",
            prompt="p",
            expected_error=ErrorExpectation(
                error_type="resolution",
                error_contains="nonexistent",
            ),
        )
        steps = [
            StepResult(
                intent="buffer",
                status="rejected",
                params={},
                tool="",
                error_message="artifact 'nonexistent_999' not found",
            ),
        ]
        runner = _make_runner()
        msgs = runner._assert_error(fixture, steps)
        assert all(m.startswith("PASS") for m in msgs)

    def test_assert_error_contains_fail(self) -> None:
        fixture = EvalFixture(
            name="t",
            prompt="p",
            expected_error=ErrorExpectation(
                error_type="security",
                error_contains="path traversal",
            ),
        )
        steps = [
            StepResult(
                intent="save",
                status="rejected",
                params={},
                tool="ese",
                error_message="unknown error occurred",
            ),
        ]
        runner = _make_runner()
        msgs = runner._assert_error(fixture, steps)
        assert any("path traversal" in m and m.startswith("FAIL") for m in msgs)

    def test_assert_error_none_expectation(self) -> None:
        """If fixture has no error expectation, returns empty."""
        fixture = EvalFixture(name="t", prompt="p", expected_error=None)
        runner = _make_runner()
        msgs = runner._assert_error(fixture, [])
        assert msgs == []

    def test_assert_error_security_both_statuses(self) -> None:
        """Security accepts both 'rejected' and 'error' statuses."""
        fixture = EvalFixture(
            name="t",
            prompt="p",
            expected_error=ErrorExpectation(error_type="security"),
        )
        # 'error' status should also satisfy security
        steps = [
            StepResult(intent="fetch", status="error", params={}, tool=""),
        ]
        runner = _make_runner()
        msgs = runner._assert_error(fixture, steps)
        assert all(m.startswith("PASS") for m in msgs)


# ---------------------------------------------------------------------------
# run_fixture behaviour (no live API)
# ---------------------------------------------------------------------------


class TestRunFixture:
    def test_skip_live_returns_skipped(self) -> None:
        """When skip_live=True and no API key, fixture is skipped."""
        fixture = EvalFixture(name="skip_test", prompt="p", skip_live=True)
        runner = EvalRunner(api_key=None)
        result = runner.run_fixture(fixture)
        assert result.passed is True
        assert result.error is not None
        assert "SKIPPED" in result.error

    def test_skip_live_not_skipped_with_key(self) -> None:
        """When API key is provided, skip_live fixtures are NOT automatically skipped.

        They will proceed to attempt real execution, which may raise — that's OK
        for this test (we just verify it doesn't short-circuit).
        """
        fixture = EvalFixture(name="run_test", prompt="p", skip_live=True)
        runner = EvalRunner(api_key="fake-key")
        # This will fail because there's no real harness setup, but it won't
        # return "SKIPPED".
        result = runner.run_fixture(fixture)
        # The result is either a real result or a caught exception result
        # but NOT the skipped sentinel.
        if result.error is not None:
            assert "SKIPPED" not in result.error


# ---------------------------------------------------------------------------
# Cases module integrity
# ---------------------------------------------------------------------------


class TestCasesModule:
    def test_cases_import(self) -> None:
        """FIXTURES list is non-empty, all are EvalFixture instances."""
        assert len(FIXTURES) > 0
        assert all(isinstance(f, EvalFixture) for f in FIXTURES)

    def test_cases_names_unique(self) -> None:
        """All fixture names are unique."""
        names = [f.name for f in FIXTURES]
        assert len(names) == len(set(names))

    def test_cases_tags_valid(self) -> None:
        """All tags are from the known set."""
        for fixture in FIXTURES:
            for tag in fixture.tags:
                assert tag in _VALID_TAGS, (
                    f"Unknown tag '{tag}' in fixture '{fixture.name}'"
                )

    def test_cases_count_is_30(self) -> None:
        """Exactly 30 fixture cases are defined (25 original + 5 Phase 2)."""
        assert len(FIXTURES) == 30

    def test_cases_all_have_prompt(self) -> None:
        """Every fixture has a non-empty prompt."""
        for f in FIXTURES:
            assert f.prompt, f"Fixture '{f.name}' has empty prompt"

    def test_fixture_by_name(self) -> None:
        """Can find a specific fixture by name."""
        by_name = {f.name: f for f in FIXTURES}
        assert "single_osm_water_chico" in by_name
        assert "security_path_traversal" in by_name
