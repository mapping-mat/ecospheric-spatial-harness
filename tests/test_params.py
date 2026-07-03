"""Tests for ecospheric_harness.params — parameter key normalization."""

from __future__ import annotations

from ecospheric_harness.params import normalize_param_key, normalize_params


# ---------------------------------------------------------------------------
# normalize_param_key
# ---------------------------------------------------------------------------


def test_normalize_param_key_strips_dashes() -> None:
    """Leading double-dashes are removed: '--source' → 'source'."""
    assert normalize_param_key("--source") == "source"


def test_normalize_param_key_converts_hyphens() -> None:
    """Leading dashes stripped AND interior hyphens become underscores."""
    assert normalize_param_key("--output-crs") == "output_crs"


def test_normalize_param_key_passthrough() -> None:
    """An already-normalized key passes through unchanged."""
    assert normalize_param_key("bbox") == "bbox"


# ---------------------------------------------------------------------------
# normalize_params
# ---------------------------------------------------------------------------


def test_normalize_params_all_keys() -> None:
    """Every key in the dict is normalized."""
    raw = {"--source": "@osm", "--bbox": [-121.9, 39.7, -121.8, 39.8], "intent": "buildings"}
    result = normalize_params(raw)
    assert set(result.keys()) == {"source", "bbox", "intent"}
    assert result["source"] == "@osm"
    assert result["bbox"] == [-121.9, 39.7, -121.8, 39.8]
    assert result["intent"] == "buildings"


def test_normalize_params_collision_last_wins() -> None:
    """When two raw keys normalize to the same name, the later one wins.

    Dict insertion order determines 'later' — Python 3.7+ preserves it.
    """
    raw = {"--source": "a", "source": "b"}
    result = normalize_params(raw)
    assert result == {"source": "b"}
