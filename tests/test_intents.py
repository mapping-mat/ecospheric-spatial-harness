"""Tests for ecospheric_harness.intents."""

from __future__ import annotations

import pytest

from ecospheric_harness.intents import (
    CompleteIntent,
    FailedIntent,
    OperationIntent,
    RedoIntent,
    UndoIntent,
    parse_intent,
)


# ---------------------------------------------------------------------------
# parse_intent: happy paths
# ---------------------------------------------------------------------------


class TestParseIntent:
    """parse_intent should return the correct typed intent for each kind."""

    def test_parse_operation(self) -> None:
        raw = {"intent": "buffer", "params": {"distance": 100}}
        result = parse_intent(raw)
        assert isinstance(result, OperationIntent)
        assert result.intent == "buffer"
        assert result.params == {"distance": 100}

    def test_parse_undo(self) -> None:
        raw = {"intent": "undo"}
        result = parse_intent(raw)
        assert isinstance(result, UndoIntent)
        assert result.intent == "undo"

    def test_parse_redo(self) -> None:
        raw = {"intent": "redo", "params": {"step": 3}}
        result = parse_intent(raw)
        assert isinstance(result, RedoIntent)
        assert result.intent == "redo"
        assert result.params == {"step": 3}

    def test_parse_complete(self) -> None:
        raw = {"intent": "complete", "summary": "Buffered 5 features by 100m"}
        result = parse_intent(raw)
        assert isinstance(result, CompleteIntent)
        assert result.summary == "Buffered 5 features by 100m"

    def test_parse_failed(self) -> None:
        raw = {"intent": "failed", "reason": "Input file not found"}
        result = parse_intent(raw)
        assert isinstance(result, FailedIntent)
        assert result.reason == "Input file not found"


# ---------------------------------------------------------------------------
# parse_intent: validation
# ---------------------------------------------------------------------------


class TestParseIntentValidation:
    """parse_intent should reject invalid inputs."""

    def test_complete_requires_summary(self) -> None:
        raw = {"intent": "complete", "summary": ""}
        with pytest.raises(ValueError, match="non-empty summary"):
            parse_intent(raw)

    def test_complete_requires_non_whitespace_summary(self) -> None:
        raw = {"intent": "complete", "summary": "   "}
        with pytest.raises(ValueError, match="non-empty summary"):
            parse_intent(raw)

    def test_failed_requires_reason(self) -> None:
        raw = {"intent": "failed", "reason": ""}
        with pytest.raises(ValueError, match="non-empty reason"):
            parse_intent(raw)

    def test_failed_requires_non_whitespace_reason(self) -> None:
        raw = {"intent": "failed", "reason": "   "}
        with pytest.raises(ValueError, match="non-empty reason"):
            parse_intent(raw)

    def test_missing_intent_field(self) -> None:
        raw = {"params": {}}
        with pytest.raises(ValueError, match="Missing or empty"):
            parse_intent(raw)

    def test_empty_intent_field(self) -> None:
        raw = {"intent": ""}
        with pytest.raises(ValueError, match="Missing or empty"):
            parse_intent(raw)


# ---------------------------------------------------------------------------
# Round-trip: construct -> verify fields
# ---------------------------------------------------------------------------


class TestIntentConstruction:
    """Direct construction should set fields correctly."""

    def test_operation_intent_round_trip(self) -> None:
        intent = OperationIntent(
            intent="clip", params={"mask": "boundary.shp"}
        )
        assert intent.intent == "clip"
        assert intent.params == {"mask": "boundary.shp"}
        # Default params
        bare = OperationIntent(intent="dissolve")
        assert bare.params == {}

    def test_redo_intent_with_params(self) -> None:
        intent = RedoIntent(params={"step": 5, "force": True})
        assert intent.intent == "redo"
        assert intent.params == {"step": 5, "force": True}

    def test_undo_intent_no_extras(self) -> None:
        intent = UndoIntent()
        assert intent.intent == "undo"

    def test_complete_intent_construction(self) -> None:
        intent = CompleteIntent(summary="All done")
        assert intent.intent == "complete"
        assert intent.summary == "All done"

    def test_failed_intent_construction(self) -> None:
        intent = FailedIntent(reason="Out of memory")
        assert intent.intent == "failed"
        assert intent.reason == "Out of memory"
