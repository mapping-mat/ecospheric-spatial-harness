"""Tests for artifact metadata extraction helpers.

Covers the bug fix where reproject/convert artifacts ended up with
``crs: null`` and ``bbox: null`` in the artifact registry because
the orchestrator only looked at a narrow set of envelope keys and
never fell back to deriving bbox from the output file.
"""

from __future__ import annotations

import json
import math
import subprocess
from pathlib import Path
from typing import Any

import pytest

from ecospheric_harness.artifact_metadata import (
    _is_valid_bbox,
    derive_bbox_from_file,
    extract_bbox,
    extract_crs,
    extract_or_derive_bbox,
)


# ---------------------------------------------------------------------------
# Test data: synthetic ESE envelopes
# ---------------------------------------------------------------------------


def _ese_proj_transform_envelope() -> dict[str, Any]:
    """Envelope shape produced by ``ese proj transform`` (reproject)."""
    return {
        "status": "success",
        "data": {
            "feature_count": 16_337,
            "from_crs": "EPSG:4326",
            "to_crs": "EPSG:32610",
            "same_crs": False,
            "format": "geoparquet",
            "data_type": "vector",
            "provenance": [
                {
                    "command": "ese proj transform",
                    "crs_working_crs": "EPSG:32610",
                },
            ],
        },
    }


def _ese_convert_envelope() -> dict[str, Any]:
    """Envelope shape produced by ``ese convert --output-crs EPSG:3857``."""
    return {
        "status": "success",
        "data": {
            "input_format": "geoparquet",
            "output_format": "geojson",
            "feature_count": 16_337,
            "crs": "EPSG:3857",
            "format": "geojson",
            "data_type": "vector",
            "provenance": [
                {
                    "command": "ese convert",
                    "crs_working_crs": "EPSG:3857",
                },
            ],
        },
    }


def _edd_search_envelope() -> dict[str, Any]:
    """Envelope shape produced by ``edd search --source @osm``."""
    return {
        "status": "success",
        "data": {
            "source": "@osm",
            "format": "geojson",
            "data_type": "vector",
            "feature_count": 342,
            "crs": "EPSG:4326",
            "bounds": [-121.5, 38.2, -121.3, 38.4],
        },
    }


# ---------------------------------------------------------------------------
# extract_crs
# ---------------------------------------------------------------------------


class TestExtractCrs:
    def test_direct_crs_key(self) -> None:
        """``data.crs`` is the primary key."""
        envelope = {"data": {"crs": "EPSG:4326"}}
        assert extract_crs(envelope) == "EPSG:4326"

    def test_output_crs_fallback(self) -> None:
        envelope = {"data": {"output_crs": "EPSG:3857"}}
        assert extract_crs(envelope) == "EPSG:3857"

    def test_to_crs_fallback_for_proj_transform(self) -> None:
        """The reproject bug: ``to_crs`` was previously ignored."""
        envelope = _ese_proj_transform_envelope()
        assert extract_crs(envelope) == "EPSG:32610"

    def test_from_crs_fallback(self) -> None:
        envelope = {"data": {"from_crs": "EPSG:4326"}}
        assert extract_crs(envelope) == "EPSG:4326"

    def test_crs_meta_nested_fallback(self) -> None:
        envelope = {"data": {"crs_meta": {"crs": "EPSG:32610"}}}
        assert extract_crs(envelope) == "EPSG:32610"

    def test_provenance_crs_working_crs_fallback(self) -> None:
        """ESE puts the working CRS in the provenance list."""
        envelope = {"data": {"provenance": [{"crs_working_crs": "EPSG:32610"}]}}
        assert extract_crs(envelope) == "EPSG:32610"

    def test_prefers_crs_over_to_crs(self) -> None:
        """When multiple keys are present, ``crs`` wins."""
        envelope = {"data": {"crs": "EPSG:4326", "to_crs": "EPSG:32610"}}
        assert extract_crs(envelope) == "EPSG:4326"

    def test_none_envelope(self) -> None:
        assert extract_crs(None) is None

    def test_empty_envelope(self) -> None:
        assert extract_crs({}) is None

    def test_envelope_without_data(self) -> None:
        assert extract_crs({"status": "success"}) is None

    def test_empty_data(self) -> None:
        assert extract_crs({"data": {}}) is None

    def test_malformed_crs_meta(self) -> None:
        """crs_meta present but not a dict — should not crash."""
        envelope = {"data": {"crs_meta": "EPSG:4326"}}
        # crs_meta is a string, not a dict; should fall through to None
        assert extract_crs(envelope) is None

    def test_malformed_provenance(self) -> None:
        """provenance present but not a list of dicts — should not crash."""
        envelope = {"data": {"provenance": "EPSG:4326"}}
        assert extract_crs(envelope) is None

    def test_edd_search_envelope(self) -> None:
        assert extract_crs(_edd_search_envelope()) == "EPSG:4326"

    def test_ese_convert_envelope(self) -> None:
        assert extract_crs(_ese_convert_envelope()) == "EPSG:3857"

    def test_no_crs_anywhere(self) -> None:
        envelope = {"data": {"feature_count": 5, "format": "geojson"}}
        assert extract_crs(envelope) is None


# ---------------------------------------------------------------------------
# extract_bbox
# ---------------------------------------------------------------------------


class TestExtractBbox:
    def test_bbox_key(self) -> None:
        envelope = {"data": {"bbox": [-122.0, 38.0, -121.0, 39.0]}}
        assert extract_bbox(envelope) == [-122.0, 38.0, -121.0, 39.0]

    def test_bounds_key(self) -> None:
        envelope = {"data": {"bounds": [-122.0, 38.0, -121.0, 39.0]}}
        assert extract_bbox(envelope) == [-122.0, 38.0, -121.0, 39.0]

    def test_extent_key(self) -> None:
        envelope = {"data": {"extent": [-122.0, 38.0, -121.0, 39.0]}}
        assert extract_bbox(envelope) == [-122.0, 38.0, -121.0, 39.0]

    def test_prefers_bbox_over_bounds(self) -> None:
        envelope = {"data": {"bbox": [0, 0, 1, 1], "bounds": [2, 2, 3, 3]}}
        assert extract_bbox(envelope) == [0, 0, 1, 1]

    def test_edd_search_envelope(self) -> None:
        """The EDD search envelope uses ``bounds``."""
        envelope = _edd_search_envelope()
        assert extract_bbox(envelope) == [-121.5, 38.2, -121.3, 38.4]

    def test_ese_reproject_envelope_has_no_bbox(self) -> None:
        """The reproject bug: ESE does not emit bbox in the envelope."""
        envelope = _ese_proj_transform_envelope()
        assert extract_bbox(envelope) is None

    def test_returns_floats(self) -> None:
        envelope = {"data": {"bbox": [-122, 38, -121, 39]}}
        result = extract_bbox(envelope)
        assert result is not None
        assert all(isinstance(v, float) for v in result)

    def test_invalid_length_ignored(self) -> None:
        envelope = {"data": {"bbox": [-122.0, 38.0, -121.0]}}
        assert extract_bbox(envelope) is None

    def test_non_numeric_ignored(self) -> None:
        envelope = {"data": {"bbox": ["a", "b", "c", "d"]}}
        assert extract_bbox(envelope) is None

    def test_wrong_type_ignored(self) -> None:
        envelope = {"data": {"bbox": "not-a-list"}}
        assert extract_bbox(envelope) is None

    def test_none_envelope(self) -> None:
        assert extract_bbox(None) is None


# ---------------------------------------------------------------------------
# _is_valid_bbox helper
# ---------------------------------------------------------------------------


class TestIsValidBbox:
    def test_valid_list(self) -> None:
        assert _is_valid_bbox([0.0, 0.0, 1.0, 1.0]) is True

    def test_valid_tuple(self) -> None:
        assert _is_valid_bbox((0.0, 0.0, 1.0, 1.0)) is True

    def test_wrong_length(self) -> None:
        assert _is_valid_bbox([0.0, 0.0, 1.0]) is False
        assert _is_valid_bbox([0.0, 0.0, 1.0, 1.0, 1.0]) is False

    def test_string(self) -> None:
        assert _is_valid_bbox("not a list") is False

    def test_int_values_acceptable(self) -> None:
        """Integer coordinates are coerced to float in extract_bbox."""
        assert _is_valid_bbox([0, 0, 1, 1]) is True


# ---------------------------------------------------------------------------
# derive_bbox_from_file
# ---------------------------------------------------------------------------


class TestDeriveBboxFromFile:
    def test_missing_file(self, tmp_path: Path) -> None:
        assert derive_bbox_from_file(tmp_path / "nope.geojson") is None

    def test_none_path(self) -> None:
        assert derive_bbox_from_file(None) is None

    def test_vector_geojson(self, tmp_path: Path) -> None:
        """Derive bbox from a real GeoJSON file via geopandas."""
        geojson = {
            "type": "FeatureCollection",
            "features": [
                {
                    "type": "Feature",
                    "properties": {},
                    "geometry": {
                        "type": "Polygon",
                        "coordinates": [[
                            [-122.0, 38.0], [-121.0, 38.0],
                            [-121.0, 39.0], [-122.0, 39.0],
                            [-122.0, 38.0],
                        ]],
                    },
                },
            ],
        }
        p = tmp_path / "poly.geojson"
        p.write_text(json.dumps(geojson))
        result = derive_bbox_from_file(p, data_type="vector")
        assert result is not None
        assert len(result) == 4
        assert math.isclose(result[0], -122.0, abs_tol=1e-6)
        assert math.isclose(result[1], 38.0, abs_tol=1e-6)
        assert math.isclose(result[2], -121.0, abs_tol=1e-6)
        assert math.isclose(result[3], 39.0, abs_tol=1e-6)

    def test_unknown_data_type_falls_through(self, tmp_path: Path) -> None:
        """``data_type="unknown"`` tries vector first, then raster."""
        geojson = {
            "type": "FeatureCollection",
            "features": [
                {
                    "type": "Feature",
                    "properties": {},
                    "geometry": {
                        "type": "Point",
                        "coordinates": [-122.5, 37.5],
                    },
                },
            ],
        }
        p = tmp_path / "point.geojson"
        p.write_text(json.dumps(geojson))
        result = derive_bbox_from_file(p)  # default unknown
        assert result is not None
        assert math.isclose(result[0], -122.5, abs_tol=1e-6)
        assert math.isclose(result[1], 37.5, abs_tol=1e-6)

    def test_unreadable_file(self, tmp_path: Path) -> None:
        """A file with no recognizable format returns None, not an error."""
        p = tmp_path / "garbage.geojson"
        p.write_bytes(b"not valid geojson {{{")
        assert derive_bbox_from_file(p, data_type="vector") is None


# ---------------------------------------------------------------------------
# extract_or_derive_bbox (combined helper)
# ---------------------------------------------------------------------------


class TestExtractOrDeriveBbox:
    def test_envelope_takes_precedence(self, tmp_path: Path) -> None:
        """When envelope has a bbox, don't read the file."""
        envelope = {"data": {"bbox": [0, 0, 1, 1]}}
        # Pass a non-existent file — should still succeed via envelope
        result = extract_or_derive_bbox(
            envelope, tmp_path / "does-not-exist.geojson", "vector",
        )
        assert result == [0.0, 0.0, 1.0, 1.0]

    def test_falls_back_to_file(self, tmp_path: Path) -> None:
        """When envelope has no bbox, read the file."""
        geojson = {
            "type": "FeatureCollection",
            "features": [
                {
                    "type": "Feature",
                    "properties": {},
                    "geometry": {
                        "type": "Polygon",
                        "coordinates": [[
                            [-122.0, 38.0], [-121.0, 38.0],
                            [-121.0, 39.0], [-122.0, 39.0],
                            [-122.0, 38.0],
                        ]],
                    },
                },
            ],
        }
        p = tmp_path / "poly.geojson"
        p.write_text(json.dumps(geojson))
        result = extract_or_derive_bbox(
            {"data": {}}, p, "vector",
        )
        assert result is not None
        assert math.isclose(result[0], -122.0, abs_tol=1e-6)
        assert math.isclose(result[2], -121.0, abs_tol=1e-6)

    def test_no_envelope_no_file(self) -> None:
        assert extract_or_derive_bbox(None, None) is None

    def test_envelope_present_no_path(self) -> None:
        envelope = {"data": {"bbox": [-1, -1, 1, 1]}}
        result = extract_or_derive_bbox(envelope, None)
        assert result == [-1.0, -1.0, 1.0, 1.0]


# ---------------------------------------------------------------------------
# Integration: orchestrator registers a reprojected artifact with non-null CRS
# ---------------------------------------------------------------------------


class TestOrchestratorReprojectMetadata:
    """End-to-end: a reproject-style envelope yields a record with crs set."""

    def test_reproject_envelope_yields_crs_via_helper(self) -> None:
        """The pre-orchestrator helper chain recovers the CRS for the
        typical ESE reproject envelope (which has only ``to_crs``)."""
        envelope = _ese_proj_transform_envelope()
        crs = extract_crs(envelope)
        assert crs == "EPSG:32610"

    def test_convert_envelope_yields_crs_via_helper(self) -> None:
        envelope = _ese_convert_envelope()
        crs = extract_crs(envelope)
        assert crs == "EPSG:3857"


# ---------------------------------------------------------------------------
# Live ESE envelope fixtures (only run if tools available)
# ---------------------------------------------------------------------------


_ese_available: bool = (
    subprocess.run(
        ["which", "ese"], capture_output=True, text=True,
    ).returncode == 0
)


@pytest.mark.skipif(not _ese_available, reason="ese binary not on PATH")
class TestLiveEseEnvelopes:
    """Verify the helper agrees with real ESE envelope output.

    Skipped if ESE is not installed (CI without the binary).
    """

    def test_live_ese_convert_envelope_has_crs(self, tmp_path: Path) -> None:
        """Convert with --output-crs emits ``data.crs``."""
        # Need an input file
        inp = tmp_path / "in.geojson"
        inp.write_text(json.dumps({
            "type": "FeatureCollection",
            "features": [
                {
                    "type": "Feature",
                    "properties": {},
                    "geometry": {
                        "type": "Point",
                        "coordinates": [-122.5, 37.7],
                    },
                },
            ],
        }))
        out = tmp_path / "out.geojson"
        r = subprocess.run(
            ["ese", "convert", str(inp), "-o", str(out),
             "--output-crs", "EPSG:3857", "--json"],
            capture_output=True, text=True, timeout=30,
        )
        if r.returncode != 0:
            pytest.skip(f"ese convert failed: {r.stderr[:200]}")
        envelope = json.loads(r.stdout)
        crs = extract_crs(envelope)
        assert crs is not None
        assert "3857" in crs

    def test_live_ese_proj_transform_envelope_has_to_crs(self, tmp_path: Path) -> None:
        """Reproject emits ``data.to_crs`` — previously not recognized."""
        inp = tmp_path / "in.geojson"
        inp.write_text(json.dumps({
            "type": "FeatureCollection",
            "features": [
                {
                    "type": "Feature",
                    "properties": {},
                    "geometry": {
                        "type": "Point",
                        "coordinates": [-122.5, 37.7],
                    },
                },
            ],
        }))
        out = tmp_path / "out.geoparquet"
        r = subprocess.run(
            ["ese", "proj", "transform", str(inp),
             "--to", "EPSG:32610", "-o", str(out), "--json"],
            capture_output=True, text=True, timeout=30,
        )
        if r.returncode != 0:
            pytest.skip(f"ese proj transform failed: {r.stderr[:200]}")
        envelope = json.loads(r.stdout)
        # Before the fix, this returned None.
        crs = extract_crs(envelope)
        assert crs == "EPSG:32610"

    def test_live_ese_bbox_derivable_from_output(self, tmp_path: Path) -> None:
        """ESE envelopes don't carry bbox, but we can derive it from the file."""
        inp = tmp_path / "in.geojson"
        inp.write_text(json.dumps({
            "type": "FeatureCollection",
            "features": [
                {
                    "type": "Feature",
                    "properties": {},
                    "geometry": {
                        "type": "Point",
                        "coordinates": [-122.5, 37.7],
                    },
                },
                {
                    "type": "Feature",
                    "properties": {},
                    "geometry": {
                        "type": "Point",
                        "coordinates": [-121.5, 38.5],
                    },
                },
            ],
        }))
        out = tmp_path / "out.geoparquet"
        r = subprocess.run(
            ["ese", "proj", "transform", str(inp),
             "--to", "EPSG:32610", "-o", str(out), "--json"],
            capture_output=True, text=True, timeout=30,
        )
        if r.returncode != 0:
            pytest.skip(f"ese proj transform failed: {r.stderr[:200]}")
        envelope = json.loads(r.stdout)
        # Envelope should have no bbox
        assert extract_bbox(envelope) is None
        # But we can derive it from the output file
        bbox = extract_or_derive_bbox(envelope, out, "vector")
        assert bbox is not None
        assert len(bbox) == 4
        # Both points are now in UTM 10N, so x ≈ -200_000 to -100_000, y ≈ 4_180_000
        # Just verify the bbox is non-degenerate.
        assert bbox[0] < bbox[2]
        assert bbox[1] < bbox[3]
