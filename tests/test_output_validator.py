"""Tests for output validation (Phase 2.2)."""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from etp.describe import CommandDescriptor

from ecospheric_harness.artifact_registry import ArtifactRecord
from ecospheric_harness.output_validator import OutputValidator, OutputValidationResult
from ecospheric_harness.workspace import WorkspaceManager


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_cmd(name="buffer", category="vector", requires_planar_crs=True):
    return CommandDescriptor(
        name=name,
        description=f"{name} command",
        category=category,
        requires_planar_crs=requires_planar_crs,
    )


def _make_artifact(tmp_path, crs="EPSG:4326", bbox=None, data_type="vector", fmt="geojson"):
    p = tmp_path / "input.geojson"
    p.write_text('{"type":"FeatureCollection","features":[]}')
    return ArtifactRecord(
        artifact_id="test_001",
        path=p,
        format=fmt,
        data_type=data_type,
        crs=crs,
        bbox=bbox or [-180, -90, 180, 90],
    )


def _raster_envelope(width=100, height=100, crs="EPSG:4326", bbox=None):
    return {
        "data": {
            "data_type": "raster",
            "format": "geotiff",
            "width": width,
            "height": height,
            "crs": crs,
            "bbox": bbox or [-180, -90, 180, 90],
        }
    }


def _vector_envelope(feature_count=10, crs="EPSG:4326", bbox=None):
    return {
        "data": {
            "data_type": "vector",
            "format": "geojson",
            "feature_count": feature_count,
            "crs": crs,
            "bbox": bbox or [-180, -90, 180, 90],
        }
    }


# ---------------------------------------------------------------------------
# 1. File-exists checks
# ---------------------------------------------------------------------------

class TestCheckFileExists:
    def test_file_exists_nonempty(self, tmp_path):
        p = tmp_path / "out.tif"
        p.write_bytes(b"\x00" * 100)
        v = OutputValidator()
        result = v.validate(p, _raster_envelope(), _make_cmd("reproject", "raster", False))
        assert result.ok is True

    def test_file_missing(self, tmp_path):
        """File-not-found is warning-level — check reports it but ok stays True."""
        v = OutputValidator()
        result = v.validate(
            tmp_path / "nonexistent.tif",
            _raster_envelope(),
            _make_cmd("reproject", "raster", False),
        )
        # file_exists check should report failure
        file_checks = [c for c in result.checks if c["check"] == "file_exists"]
        assert len(file_checks) == 1
        assert file_checks[0]["passed"] is False
        assert "does not exist" in file_checks[0]["message"].lower()

    def test_file_empty(self, tmp_path):
        """Empty file is warning-level — check reports it but ok stays True."""
        p = tmp_path / "out.tif"
        p.write_bytes(b"")
        v = OutputValidator()
        result = v.validate(p, _raster_envelope(), _make_cmd("reproject", "raster", False))
        file_checks = [c for c in result.checks if c["check"] == "file_exists"]
        assert len(file_checks) == 1
        assert file_checks[0]["passed"] is False
        assert "empty" in file_checks[0]["message"].lower()

    def test_file_exists_check_in_results(self, tmp_path):
        """The checks list should contain a file_exists entry."""
        p = tmp_path / "out.tif"
        p.write_bytes(b"\x00" * 100)
        v = OutputValidator()
        result = v.validate(p, _raster_envelope(), _make_cmd("reproject", "raster", False))
        file_checks = [c for c in result.checks if c["check"] == "file_exists"]
        assert len(file_checks) == 1
        assert file_checks[0]["passed"] is True

    def test_file_missing_still_runs_other_checks(self, tmp_path):
        """file_exists is warning-level — other checks still run."""
        v = OutputValidator()
        result = v.validate(
            tmp_path / "nonexistent.tif",
            _raster_envelope(),
            _make_cmd("reproject", "raster", False),
        )
        # All checks should be present (no short-circuit)
        check_names = [c["check"] for c in result.checks]
        assert "file_exists" in check_names
        assert "raster_validity" in check_names
        assert "output_vs_intent" in check_names
        assert "metadata_completeness" in check_names


# ---------------------------------------------------------------------------
# 2. Raster validity checks
# ---------------------------------------------------------------------------

class TestCheckRaster:
    def test_valid_raster(self, tmp_path):
        p = tmp_path / "out.tif"
        p.write_bytes(b"\x00" * 100)
        v = OutputValidator()
        result = v.validate(
            p,
            _raster_envelope(width=100, height=100, crs="EPSG:4326"),
            _make_cmd("reproject", "raster", False),
        )
        assert result.ok is True

    def test_one_by_one_raster(self, tmp_path):
        p = tmp_path / "out.tif"
        p.write_bytes(b"\x00" * 100)
        v = OutputValidator()
        result = v.validate(
            p,
            _raster_envelope(width=1, height=1, crs="EPSG:4326"),
            _make_cmd("reproject", "raster", False),
        )
        assert result.ok is False
        assert "1" in result.error.lower()

    def test_missing_crs_raster(self, tmp_path):
        """When CRS is present but empty string, validation should fail."""
        p = tmp_path / "out.tif"
        p.write_bytes(b"\x00" * 100)
        v = OutputValidator()
        env = _raster_envelope(width=100, height=100, crs="")
        result = v.validate(p, env, _make_cmd("reproject", "raster", False))
        assert result.ok is False
        assert "crs" in result.error.lower()

    def test_raster_check_in_results(self, tmp_path):
        p = tmp_path / "out.tif"
        p.write_bytes(b"\x00" * 100)
        v = OutputValidator()
        result = v.validate(
            p,
            _raster_envelope(width=100, height=100, crs="EPSG:4326"),
            _make_cmd("reproject", "raster", False),
        )
        raster_checks = [c for c in result.checks if c["check"] == "raster_validity"]
        assert len(raster_checks) == 1
        assert raster_checks[0]["passed"] is True


# ---------------------------------------------------------------------------
# 3. Vector validity checks
# ---------------------------------------------------------------------------

class TestCheckVector:
    def test_valid_vector(self, tmp_path):
        p = tmp_path / "out.geojson"
        p.write_text(
            '{"type":"FeatureCollection","features":['
            '{"type":"Feature","geometry":{"type":"Point","coordinates":[0,0]},"properties":{}}]}'
        )
        v = OutputValidator()
        result = v.validate(
            p,
            _vector_envelope(feature_count=10, crs="EPSG:4326"),
            _make_cmd("buffer", "vector", True),
        )
        assert result.ok is True

    def test_zero_features(self, tmp_path):
        p = tmp_path / "out.geojson"
        p.write_text('{"type":"FeatureCollection","features":[]}')
        v = OutputValidator()
        result = v.validate(
            p,
            _vector_envelope(feature_count=0, crs="EPSG:4326"),
            _make_cmd("buffer", "vector", True),
        )
        assert result.ok is False
        assert "0" in result.error.lower()

    def test_missing_crs_vector(self, tmp_path):
        """When CRS is present but empty string, validation should fail."""
        p = tmp_path / "out.geojson"
        p.write_text('{}')
        v = OutputValidator()
        result = v.validate(
            p,
            _vector_envelope(feature_count=10, crs=""),
            _make_cmd("buffer", "vector", True),
        )
        assert result.ok is False
        assert "crs" in result.error.lower()

    def test_vector_check_in_results(self, tmp_path):
        p = tmp_path / "out.geojson"
        p.write_text('{}')
        v = OutputValidator()
        result = v.validate(
            p,
            _vector_envelope(feature_count=10, crs="EPSG:4326"),
            _make_cmd("buffer", "vector", True),
        )
        vector_checks = [c for c in result.checks if c["check"] == "vector_validity"]
        assert len(vector_checks) == 1
        assert vector_checks[0]["passed"] is True


# ---------------------------------------------------------------------------
# 4. Output-vs-intent checks
# ---------------------------------------------------------------------------

class TestCheckOutputVsIntent:
    def test_reproject_matching_crs(self, tmp_path):
        p = tmp_path / "out.tif"
        p.write_bytes(b"\x00" * 100)
        v = OutputValidator()
        env = _raster_envelope(width=100, height=100, crs="EPSG:3857")
        params = {"output_crs": "EPSG:3857"}
        result = v.validate(
            p, env, _make_cmd("reproject", "raster", False), params=params,
        )
        assert result.ok is True

    def test_reproject_mismatched_crs(self, tmp_path):
        p = tmp_path / "out.tif"
        p.write_bytes(b"\x00" * 100)
        v = OutputValidator()
        env = _raster_envelope(width=100, height=100, crs="EPSG:4326")
        params = {"output_crs": "EPSG:3857"}
        result = v.validate(
            p, env, _make_cmd("reproject", "raster", False), params=params,
        )
        assert result.ok is False
        assert "does not match" in result.error.lower()

    def test_buffer_output_contains_input(self, tmp_path):
        p = tmp_path / "out.geojson"
        p.write_text('{}')
        v = OutputValidator()
        # Output bbox larger than input
        env = _vector_envelope(feature_count=10, crs="EPSG:4326", bbox=[-200, -100, 200, 100])
        art = _make_artifact(tmp_path, bbox=[-180, -90, 180, 90])
        result = v.validate(
            p, env, _make_cmd("buffer", "vector", True), input_artifact=art,
        )
        assert result.ok is True

    def test_buffer_output_does_not_contain_input(self, tmp_path):
        p = tmp_path / "out.geojson"
        p.write_text('{}')
        v = OutputValidator()
        # Output bbox smaller than input — buffer should expand
        env = _vector_envelope(feature_count=10, crs="EPSG:4326", bbox=[-10, -10, 10, 10])
        art = _make_artifact(tmp_path, bbox=[-180, -90, 180, 90])
        result = v.validate(
            p, env, _make_cmd("buffer", "vector", True), input_artifact=art,
        )
        assert result.ok is False
        assert "contain" in result.error.lower()

    def test_intent_check_in_results(self, tmp_path):
        """output_vs_intent check should appear in results."""
        p = tmp_path / "out.tif"
        p.write_bytes(b"\x00" * 100)
        v = OutputValidator()
        env = _raster_envelope(width=100, height=100, crs="EPSG:3857")
        params = {"output_crs": "EPSG:3857"}
        result = v.validate(
            p, env, _make_cmd("reproject", "raster", False), params=params,
        )
        intent_checks = [c for c in result.checks if c["check"] == "output_vs_intent"]
        assert len(intent_checks) == 1
        assert intent_checks[0]["passed"] is True


# ---------------------------------------------------------------------------
# 5. Metadata completeness checks (warning-level, always passes)
# ---------------------------------------------------------------------------

class TestMetadataCompleteness:
    def test_complete_metadata(self, tmp_path):
        p = tmp_path / "out.tif"
        p.write_bytes(b"\x00" * 100)
        v = OutputValidator()
        result = v.validate(
            p,
            _raster_envelope(width=100, height=100, crs="EPSG:4326"),
            _make_cmd("reproject", "raster", False),
        )
        # Metadata check always passes but may have warning message
        meta_check = [c for c in result.checks if c["check"] == "metadata_completeness"]
        assert len(meta_check) == 1
        assert meta_check[0]["passed"] is True

    def test_missing_fields_warning(self, tmp_path):
        p = tmp_path / "out.tif"
        p.write_bytes(b"\x00" * 100)
        v = OutputValidator()
        # Envelope missing bands — metadata check should report it
        env = {
            "data": {
                "data_type": "raster",
                "format": "geotiff",
                "width": 100,
                "height": 100,
                "crs": "EPSG:4326",
            }
        }
        result = v.validate(p, env, _make_cmd("reproject", "raster", False))
        assert result.ok is True  # warning only
        meta_check = [c for c in result.checks if c["check"] == "metadata_completeness"]
        assert "Missing" in meta_check[0]["message"]

    def test_metadata_never_fails(self, tmp_path):
        """Even with many missing fields, metadata check should pass."""
        p = tmp_path / "out.tif"
        p.write_bytes(b"\x00" * 100)
        v = OutputValidator()
        # Minimal envelope — missing most metadata
        env = {"data": {"data_type": "raster", "format": "geotiff"}}
        result = v.validate(p, env, _make_cmd("reproject", "raster", False))
        # Should fail on raster validity (no CRS), not on metadata
        meta_check = [c for c in result.checks if c["check"] == "metadata_completeness"]
        assert meta_check[0]["passed"] is True


# ---------------------------------------------------------------------------
# 6. OutputValidationResult dataclass
# ---------------------------------------------------------------------------

class TestOutputValidationResult:
    def test_default_values(self):
        r = OutputValidationResult(ok=True)
        assert r.ok is True
        assert r.checks == []
        assert r.error == ""

    def test_with_values(self):
        checks = [{"check": "file_exists", "passed": True, "message": ""}]
        r = OutputValidationResult(ok=True, checks=checks, error="")
        assert r.ok is True
        assert len(r.checks) == 1

    def test_failed_result(self):
        r = OutputValidationResult(ok=False, checks=[], error="something went wrong")
        assert r.ok is False
        assert r.error == "something went wrong"


# ---------------------------------------------------------------------------
# 7. Workspace cleanup_unregistered
# ---------------------------------------------------------------------------

class TestWorkspaceCleanupUnregistered:
    def test_deletes_confined_file(self, tmp_workspace):
        p = tmp_workspace.session_dir / "orphan.tif"
        p.write_bytes(b"\x00" * 100)
        assert p.exists()
        result = tmp_workspace.cleanup_unregistered(p)
        assert result is True
        assert not p.exists()

    def test_nonexistent_returns_false(self, tmp_workspace):
        p = tmp_workspace.session_dir / "nonexistent.tif"
        result = tmp_workspace.cleanup_unregistered(p)
        assert result is False

    def test_unconfined_returns_false(self, tmp_workspace, tmp_path_factory):
        """File outside workspace root should not be deleted."""
        # Create a file in a completely separate temp directory
        other_dir = tmp_path_factory.mktemp("outside")
        p = other_dir / "outside.tif"
        p.write_bytes(b"\x00" * 100)
        result = tmp_workspace.cleanup_unregistered(p)
        assert result is False
        assert p.exists()  # not deleted
