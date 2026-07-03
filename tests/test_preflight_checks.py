"""Tests for the 8 new spatial preflight checks + secondary input resolution.

Slice 2.1 — Preflight Foundation + Spatial Checks 1-8.

Tests cover:
- _check_crs_agreement
- _check_extent_intersection
- _check_unit_awareness
- _check_extent_containment
- _check_crs_validity
- _check_planar_crs (new method signature)
- _check_resolution_sanity
- _check_geometry_validity
- _resolve_secondary_input
- _is_binary_op
- run_all_checks pipeline
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch, call

import pytest
from etp.describe import CommandDescriptor, ParameterDescriptor

from ecospheric_harness.artifact_registry import ArtifactRecord, ArtifactRegistry
from ecospheric_harness.intents import PreflightResult, Resolution
from ecospheric_harness.preflight import PreflightChecker
from ecospheric_harness.workspace import WorkspaceManager


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def checker(tmp_path: Path) -> PreflightChecker:
    """Return a PreflightChecker backed by a 1 GB ArtifactRegistry."""
    ws = WorkspaceManager(tmp_path, disk_limit_bytes=1024 * 1024 * 1024)
    registry = ArtifactRegistry(workspace=ws, disk_limit_bytes=1024 * 1024 * 1024)
    return PreflightChecker(registry, workspace=ws)


@pytest.fixture()
def registry(tmp_path: Path) -> ArtifactRegistry:
    """Return a standalone ArtifactRegistry."""
    ws = WorkspaceManager(tmp_path, disk_limit_bytes=1024 * 1024 * 1024)
    return ArtifactRegistry(workspace=ws, disk_limit_bytes=1024 * 1024 * 1024)


@pytest.fixture()
def checker_with_registry(
    tmp_path: Path,
) -> tuple[PreflightChecker, ArtifactRegistry]:
    """Return both checker and registry for tests that register artifacts."""
    ws = WorkspaceManager(tmp_path, disk_limit_bytes=1024 * 1024 * 1024)
    reg = ArtifactRegistry(workspace=ws, disk_limit_bytes=1024 * 1024 * 1024)
    chk = PreflightChecker(reg, workspace=ws)
    return chk, reg


# ---------------------------------------------------------------------------
# Command fixtures
# ---------------------------------------------------------------------------


def _make_command(
    name: str = "buffer",
    category: str = "vector",
    requires_planar_crs: bool = False,
    **kwargs: Any,
) -> CommandDescriptor:
    """Create a CommandDescriptor for testing."""
    return CommandDescriptor(
        name=name,
        description=f"Test command: {name}",
        category=category,
        requires_planar_crs=requires_planar_crs,
        **kwargs,
    )


def _make_binary_command() -> CommandDescriptor:
    """Create a command that represents a binary (two-input) operation."""
    return CommandDescriptor(
        name="intersection",
        description="Compute spatial intersection of two layers",
        category="vector",
        parameters=[
            ParameterDescriptor(
                name="secondary",
                description="Secondary input layer",
                type="string",
                required=True,
            ),
        ],
    )


def _make_distance_command() -> CommandDescriptor:
    """Create a command that involves distance parameters."""
    return CommandDescriptor(
        name="buffer",
        description="Buffer geometries by a distance",
        category="vector",
        parameters=[
            ParameterDescriptor(
                name="distance",
                description="Buffer distance",
                type="string",
                required=True,
            ),
        ],
    )


def _make_planar_command() -> CommandDescriptor:
    """Create a command that requires planar CRS."""
    return CommandDescriptor(
        name="buffer",
        description="Buffer geometries",
        category="vector",
        requires_planar_crs=True,
    )


# ---------------------------------------------------------------------------
# Artifact helpers
# ---------------------------------------------------------------------------


def _make_artifact_record(
    registry: ArtifactRegistry,
    tmp_path: Path,
    *,
    crs: str | None = "EPSG:3857",
    bbox: list[float] | None = None,
    data_type: str = "raster",
    format: str = "geotiff",
    artifact_id: str | None = None,
    size_bytes: int = 1024,
) -> ArtifactRecord:
    """Register an artifact with the given properties."""
    p = tmp_path / f"input_{registry._counter + 1}.bin"
    p.write_bytes(b"\x00" * size_bytes)
    record = registry.register(
        path=p,
        format=format,
        data_type=data_type,
        crs=crs,
        bbox=bbox,
    )
    if artifact_id is not None:
        # Override the auto-generated ID
        del registry._artifacts[record.artifact_id]
        record.artifact_id = artifact_id
        registry._artifacts[artifact_id] = record
    return record


def _make_artifact(
    tmp_path: Path,
    *,
    crs: str | None = "EPSG:3857",
    bbox: list[float] | None = None,
    data_type: str = "raster",
    format: str = "geotiff",
) -> MagicMock:
    """Create a mock artifact with given properties."""
    p = tmp_path / "mock_input.bin"
    p.write_bytes(b"\x00" * 1024)
    art = MagicMock()
    art.path = p
    art.crs = crs
    art.bbox = bbox
    art.data_type = data_type
    art.format = format
    art.file_size_bytes = 1024
    return art


def _write_geojson(
    tmp_path: Path,
    filename: str,
    features: list[dict[str, Any]],
    crs_code: str = "EPSG:4326",
) -> Path:
    """Write a GeoJSON file with given features."""
    geojson = {
        "type": "FeatureCollection",
        "features": features,
        "crs": {
            "type": "name",
            "properties": {"name": f"urn:ogc:def:crs:EPSG::{crs_code.split(':')[1]}"},
        },
    }
    path = tmp_path / filename
    path.write_text(json.dumps(geojson), encoding="utf-8")
    return path


def _make_valid_feature(
    coords: list[list[tuple[float, float]]] | None = None,
) -> dict[str, Any]:
    """Create a valid GeoJSON feature."""
    if coords is None:
        coords = [[(0, 0), (1, 0), (1, 1), (0, 1), (0, 0)]]
    return {
        "type": "Feature",
        "geometry": {"type": "Polygon", "coordinates": coords},
        "properties": {},
    }


def _make_self_intersecting_feature() -> dict[str, Any]:
    """Create a self-intersecting (bowtie) polygon."""
    return {
        "type": "Feature",
        "geometry": {
            "type": "Polygon",
            "coordinates": [[(0, 0), (2, 2), (2, 0), (0, 2), (0, 0)]],
        },
        "properties": {},
    }


# ---------------------------------------------------------------------------
# TestCheckCrsAgreement (5 tests)
# ---------------------------------------------------------------------------


class TestCheckCrsAgreement:
    """Check 1: _check_crs_agreement — BLOCK on CRS mismatch for binary ops."""

    def test_no_secondary_input_passes(
        self,
        checker_with_registry: tuple[PreflightChecker, ArtifactRegistry],
        tmp_path: Path,
    ) -> None:
        """No secondary input → PASS (nothing to compare)."""
        checker, registry = checker_with_registry
        cmd = _make_binary_command()
        art = _make_artifact(tmp_path, crs="EPSG:4326")

        result = checker._check_crs_agreement(cmd, art, {})
        assert result.resolution == Resolution.PASS

    def test_artifact_id_matching_crs_passes(
        self,
        checker_with_registry: tuple[PreflightChecker, ArtifactRegistry],
        tmp_path: Path,
    ) -> None:
        """Secondary artifact with matching CRS → PASS."""
        checker, registry = checker_with_registry
        cmd = _make_binary_command()

        primary = _make_artifact(tmp_path, crs="EPSG:4326")
        secondary = _make_artifact_record(registry, tmp_path, crs="EPSG:4326")

        result = checker._check_crs_agreement(
            cmd, primary, {"secondary": secondary.artifact_id}
        )
        assert result.resolution == Resolution.PASS

    def test_artifact_id_different_crs_blocks(
        self,
        checker_with_registry: tuple[PreflightChecker, ArtifactRegistry],
        tmp_path: Path,
    ) -> None:
        """Secondary artifact with different CRS → BLOCK."""
        checker, registry = checker_with_registry
        cmd = _make_binary_command()

        primary = _make_artifact(tmp_path, crs="EPSG:4326")
        secondary = _make_artifact_record(registry, tmp_path, crs="EPSG:3857")

        result = checker._check_crs_agreement(
            cmd, primary, {"secondary": secondary.artifact_id}
        )
        assert result.resolution == Resolution.BLOCK
        assert result.check == "crs_agreement"
        assert "CRS" in result.message or "crs" in result.message.lower()

    def test_vector_file_matching_crs_passes(
        self,
        checker_with_registry: tuple[PreflightChecker, ArtifactRegistry],
        tmp_path: Path,
    ) -> None:
        """Secondary is a vector file with matching CRS → PASS."""
        checker, registry = checker_with_registry
        cmd = _make_binary_command()

        primary = _make_artifact(tmp_path, crs="EPSG:4326")
        geojson_path = _write_geojson(
            tmp_path, "secondary.geojson", [_make_valid_feature()], crs_code="EPSG:4326"
        )

        result = checker._check_crs_agreement(
            cmd, primary, {"secondary": str(geojson_path)}
        )
        # Should PASS because both are EPSG:4326
        assert result.resolution == Resolution.PASS

    def test_nonexistent_file_model_discretion(
        self,
        checker_with_registry: tuple[PreflightChecker, ArtifactRegistry],
        tmp_path: Path,
    ) -> None:
        """Secondary is a non-existent file path → MODEL_DISCRETION."""
        checker, registry = checker_with_registry
        cmd = _make_binary_command()
        primary = _make_artifact(tmp_path, crs="EPSG:4326")

        result = checker._check_crs_agreement(
            cmd, primary, {"secondary": "/nonexistent/path.geojson"}
        )
        assert result.resolution == Resolution.MODEL_DISCRETION


# ---------------------------------------------------------------------------
# TestCheckExtentIntersection (4 tests)
# ---------------------------------------------------------------------------


class TestCheckExtentIntersection:
    """Check 2: _check_extent_intersection — BLOCK on zero overlap."""

    def test_no_secondary_input_passes(
        self,
        checker_with_registry: tuple[PreflightChecker, ArtifactRegistry],
        tmp_path: Path,
    ) -> None:
        """No secondary input → PASS."""
        checker, registry = checker_with_registry
        cmd = _make_binary_command()
        art = _make_artifact(tmp_path, bbox=[-121, 38, -120, 39])

        result = checker._check_extent_intersection(cmd, art, {})
        assert result.resolution == Resolution.PASS

    def test_overlapping_bboxes_passes(
        self,
        checker_with_registry: tuple[PreflightChecker, ArtifactRegistry],
        tmp_path: Path,
    ) -> None:
        """Overlapping bboxes → PASS."""
        checker, registry = checker_with_registry
        cmd = _make_binary_command()

        primary = _make_artifact(tmp_path, bbox=[-121, 38, -120, 39])
        secondary = _make_artifact_record(
            registry, tmp_path, bbox=[-120.5, 38.5, -119.5, 39.5]
        )

        result = checker._check_extent_intersection(
            cmd, primary, {"secondary": secondary.artifact_id}
        )
        assert result.resolution == Resolution.PASS

    def test_non_overlapping_bboxes_blocks(
        self,
        checker_with_registry: tuple[PreflightChecker, ArtifactRegistry],
        tmp_path: Path,
    ) -> None:
        """Non-overlapping bboxes → BLOCK."""
        checker, registry = checker_with_registry
        cmd = _make_binary_command()

        primary = _make_artifact(tmp_path, bbox=[-121, 38, -120, 39])
        secondary = _make_artifact_record(
            registry, tmp_path, bbox=[-10, -10, -5, -5]
        )

        result = checker._check_extent_intersection(
            cmd, primary, {"secondary": secondary.artifact_id}
        )
        assert result.resolution == Resolution.BLOCK
        assert "overlap" in result.message.lower() or "extent" in result.message.lower()

    def test_missing_bbox_passes(
        self,
        checker_with_registry: tuple[PreflightChecker, ArtifactRegistry],
        tmp_path: Path,
    ) -> None:
        """Missing bbox on one input → PASS (can't determine overlap)."""
        checker, registry = checker_with_registry
        cmd = _make_binary_command()

        primary = _make_artifact(tmp_path, bbox=None)
        secondary = _make_artifact_record(
            registry, tmp_path, bbox=[-121, 38, -120, 39]
        )

        result = checker._check_extent_intersection(
            cmd, primary, {"secondary": secondary.artifact_id}
        )
        # Can't determine → should pass or model discretion, not block
        assert result.resolution in (Resolution.PASS, Resolution.MODEL_DISCRETION)


# ---------------------------------------------------------------------------
# TestCheckUnitAwareness (3 tests)
# ---------------------------------------------------------------------------


class TestCheckUnitAwareness:
    """Check 3: _check_unit_awareness — AUTO_FIX for geographic CRS + distance op."""

    def test_geographic_crs_buffer_auto_fix(
        self,
        checker_with_registry: tuple[PreflightChecker, ArtifactRegistry],
        tmp_path: Path,
    ) -> None:
        """Geographic CRS + buffer command → AUTO_FIX."""
        checker, registry = checker_with_registry
        cmd = _make_distance_command()
        art = _make_artifact(tmp_path, crs="EPSG:4326")

        result = checker._check_unit_awareness(cmd, art)
        assert result.resolution == Resolution.AUTO_FIX
        assert result.check == "unit_awareness"

    def test_projected_crs_buffer_passes(
        self,
        checker_with_registry: tuple[PreflightChecker, ArtifactRegistry],
        tmp_path: Path,
    ) -> None:
        """Projected CRS + buffer command → PASS."""
        checker, registry = checker_with_registry
        cmd = _make_distance_command()
        art = _make_artifact(tmp_path, crs="EPSG:3857")

        result = checker._check_unit_awareness(cmd, art)
        assert result.resolution == Resolution.PASS

    def test_geographic_crs_non_distance_passes(
        self,
        checker_with_registry: tuple[PreflightChecker, ArtifactRegistry],
        tmp_path: Path,
    ) -> None:
        """Geographic CRS + non-distance command → PASS."""
        checker, registry = checker_with_registry
        cmd = _make_command(name="clip", category="raster")
        art = _make_artifact(tmp_path, crs="EPSG:4326")

        result = checker._check_unit_awareness(cmd, art)
        assert result.resolution == Resolution.PASS


# ---------------------------------------------------------------------------
# TestCheckExtentContainment (3 tests)
# ---------------------------------------------------------------------------


class TestCheckExtentContainment:
    """Check 4: _check_extent_containment — BLOCK if requested extent exceeds input."""

    def test_no_bbox_param_passes(
        self,
        checker_with_registry: tuple[PreflightChecker, ArtifactRegistry],
        tmp_path: Path,
    ) -> None:
        """No bbox param → PASS."""
        checker, registry = checker_with_registry
        cmd = _make_command()
        art = _make_artifact(tmp_path, bbox=[-121, 38, -120, 39])

        result = checker._check_extent_containment(cmd, art, {})
        assert result.resolution == Resolution.PASS

    def test_bbox_within_input_passes(
        self,
        checker_with_registry: tuple[PreflightChecker, ArtifactRegistry],
        tmp_path: Path,
    ) -> None:
        """Requested bbox within input extent → PASS."""
        checker, registry = checker_with_registry
        cmd = _make_command()
        art = _make_artifact(tmp_path, bbox=[-121, 38, -120, 39])

        result = checker._check_extent_containment(
            cmd, art, {"bbox": [-120.8, 38.2, -120.2, 38.8]}
        )
        assert result.resolution == Resolution.PASS

    def test_bbox_exceeds_input_blocks(
        self,
        checker_with_registry: tuple[PreflightChecker, ArtifactRegistry],
        tmp_path: Path,
    ) -> None:
        """Requested bbox exceeds input extent → BLOCK."""
        checker, registry = checker_with_registry
        cmd = _make_command()
        art = _make_artifact(tmp_path, bbox=[-121, 38, -120, 39])

        result = checker._check_extent_containment(
            cmd, art, {"bbox": [-130, 30, -110, 50]}
        )
        assert result.resolution == Resolution.BLOCK
        assert "extent" in result.message.lower() or "contain" in result.message.lower()


# ---------------------------------------------------------------------------
# TestCheckCrsValidity (3 tests)
# ---------------------------------------------------------------------------


class TestCheckCrsValidity:
    """Check 5: _check_crs_validity — BLOCK if pyproj.CRS() raises."""

    def test_no_crs_param_passes(
        self,
        checker_with_registry: tuple[PreflightChecker, ArtifactRegistry],
    ) -> None:
        """No CRS param → PASS."""
        checker, registry = checker_with_registry
        cmd = _make_command()

        result = checker._check_crs_validity(cmd, {})
        assert result.resolution == Resolution.PASS

    def test_valid_crs_passes(
        self,
        checker_with_registry: tuple[PreflightChecker, ArtifactRegistry],
    ) -> None:
        """Valid CRS string → PASS."""
        checker, registry = checker_with_registry
        cmd = _make_command()

        result = checker._check_crs_validity(cmd, {"crs": "EPSG:4326"})
        assert result.resolution == Resolution.PASS

    def test_invalid_crs_blocks(
        self,
        checker_with_registry: tuple[PreflightChecker, ArtifactRegistry],
    ) -> None:
        """Invalid CRS string → BLOCK."""
        checker, registry = checker_with_registry
        cmd = _make_command()

        result = checker._check_crs_validity(cmd, {"crs": "NOT_A_CRS"})
        assert result.resolution == Resolution.BLOCK
        assert result.check == "crs_validity"
        assert "CRS" in result.message or "crs" in result.message.lower()


# ---------------------------------------------------------------------------
# TestCheckPlanarCrs (4 tests) — new signature
# ---------------------------------------------------------------------------


class TestCheckPlanarCrsNew:
    """Check 6: _check_planar_crs — BLOCK if geographic on planar-requiring command.

    This tests the NEW method signature that accepts command and input_artifact
    and returns PreflightResult with Resolution enum.
    """

    def test_non_planar_command_passes(
        self,
        checker_with_registry: tuple[PreflightChecker, ArtifactRegistry],
        tmp_path: Path,
    ) -> None:
        """Non-planar command → PASS regardless of CRS."""
        checker, registry = checker_with_registry
        cmd = _make_command(requires_planar_crs=False)
        art = _make_artifact(tmp_path, crs="EPSG:4326")

        result = checker._check_planar_crs(cmd, art)
        assert result.resolution == Resolution.PASS

    def test_planar_command_projected_crs_passes(
        self,
        checker_with_registry: tuple[PreflightChecker, ArtifactRegistry],
        tmp_path: Path,
    ) -> None:
        """Planar command with projected CRS → PASS."""
        checker, registry = checker_with_registry
        cmd = _make_planar_command()
        art = _make_artifact(tmp_path, crs="EPSG:3857")

        result = checker._check_planar_crs(cmd, art)
        assert result.resolution == Resolution.PASS

    def test_planar_command_geographic_crs_blocks(
        self,
        checker_with_registry: tuple[PreflightChecker, ArtifactRegistry],
        tmp_path: Path,
    ) -> None:
        """Planar command with geographic CRS → BLOCK."""
        checker, registry = checker_with_registry
        cmd = _make_planar_command()
        art = _make_artifact(tmp_path, crs="EPSG:4326")

        result = checker._check_planar_crs(cmd, art)
        assert result.resolution == Resolution.BLOCK
        assert "geographic" in result.message.lower()

    def test_planar_command_none_crs_blocks(
        self,
        checker_with_registry: tuple[PreflightChecker, ArtifactRegistry],
        tmp_path: Path,
    ) -> None:
        """Planar command with None CRS → BLOCK."""
        checker, registry = checker_with_registry
        cmd = _make_planar_command()
        art = _make_artifact(tmp_path, crs=None)

        result = checker._check_planar_crs(cmd, art)
        assert result.resolution == Resolution.BLOCK


# ---------------------------------------------------------------------------
# TestCheckResolutionSanity (3 tests)
# ---------------------------------------------------------------------------


class TestCheckResolutionSanity:
    """Check 7: _check_resolution_sanity — MODEL_DISCRETION if ratio > 1000x."""

    def test_no_resolution_param_passes(
        self,
        checker_with_registry: tuple[PreflightChecker, ArtifactRegistry],
        tmp_path: Path,
    ) -> None:
        """No resolution param → PASS."""
        checker, registry = checker_with_registry
        cmd = _make_command()
        art = _make_artifact(tmp_path)

        result = checker._check_resolution_sanity(cmd, art, {})
        assert result.resolution == Resolution.PASS

    def test_resolution_within_range_passes(
        self,
        checker_with_registry: tuple[PreflightChecker, ArtifactRegistry],
        tmp_path: Path,
    ) -> None:
        """Resolution within reasonable range → PASS."""
        checker, registry = checker_with_registry
        cmd = _make_command()
        art = _make_artifact(tmp_path)

        result = checker._check_resolution_sanity(cmd, art, {"resolution": 30})
        assert result.resolution == Resolution.PASS

    def test_resolution_ratio_excessive_model_discretion(
        self,
        checker_with_registry: tuple[PreflightChecker, ArtifactRegistry],
        tmp_path: Path,
    ) -> None:
        """Resolution ratio > 1000x → MODEL_DISCRETION."""
        checker, registry = checker_with_registry
        cmd = _make_command()

        # Create artifact with known resolution in envelope
        p = tmp_path / "input_res.bin"
        p.write_bytes(b"\x00" * 1024)
        art = MagicMock()
        art.path = p
        art.crs = "EPSG:3857"
        art.bbox = None
        art.data_type = "raster"
        art.format = "geotiff"
        art.file_size_bytes = 1024
        art.envelope = {"data": {"resolution": 30.0}}

        # Request extremely fine resolution: 30m → 0.0001 = ratio 300000x
        result = checker._check_resolution_sanity(cmd, art, {"resolution": 0.0001})
        assert result.resolution == Resolution.MODEL_DISCRETION
        assert result.check == "resolution_sanity"


# ---------------------------------------------------------------------------
# TestCheckGeometryValidity (3 tests)
# ---------------------------------------------------------------------------


class TestCheckGeometryValidity:
    """Check 8: _check_geometry_validity — MODEL_DISCRETION if >10% invalid."""

    def test_non_vector_input_passes(
        self,
        checker_with_registry: tuple[PreflightChecker, ArtifactRegistry],
        tmp_path: Path,
    ) -> None:
        """Non-vector input → PASS (geometry check not applicable)."""
        checker, registry = checker_with_registry
        cmd = _make_command()
        art = _make_artifact(tmp_path, data_type="raster")

        result = checker._check_geometry_validity(cmd, art)
        assert result.resolution == Resolution.PASS

    def test_valid_geometries_passes(
        self,
        checker_with_registry: tuple[PreflightChecker, ArtifactRegistry],
        tmp_path: Path,
    ) -> None:
        """Valid geometries in vector → PASS."""
        checker, registry = checker_with_registry
        cmd = _make_command()

        geojson_path = _write_geojson(
            tmp_path,
            "valid.geojson",
            [_make_valid_feature() for _ in range(10)],
        )
        art = _make_artifact(tmp_path, data_type="vector", format="geojson")
        art.path = geojson_path

        result = checker._check_geometry_validity(cmd, art)
        assert result.resolution == Resolution.PASS

    def test_more_than_10pct_invalid_model_discretion(
        self,
        checker_with_registry: tuple[PreflightChecker, ArtifactRegistry],
        tmp_path: Path,
    ) -> None:
        """>10% invalid geometries → MODEL_DISCRETION."""
        checker, registry = checker_with_registry
        cmd = _make_command()

        # 2 valid + 8 self-intersecting = 80% invalid (>10%)
        features = [_make_valid_feature() for _ in range(2)]
        features += [_make_self_intersecting_feature() for _ in range(8)]
        geojson_path = _write_geojson(tmp_path, "invalid.geojson", features)

        art = _make_artifact(tmp_path, data_type="vector", format="geojson")
        art.path = geojson_path

        result = checker._check_geometry_validity(cmd, art)
        assert result.resolution == Resolution.MODEL_DISCRETION
        assert result.check == "geometry_validity"


# ---------------------------------------------------------------------------
# TestResolveSecondaryInput (4 tests)
# ---------------------------------------------------------------------------


class TestResolveSecondaryInput:
    """_resolve_secondary_input — resolves secondary input from params."""

    def test_no_secondary_param(
        self,
        checker_with_registry: tuple[PreflightChecker, ArtifactRegistry],
    ) -> None:
        """No secondary param → (None, None)."""
        checker, registry = checker_with_registry

        meta, warning = checker._resolve_secondary_input({})
        assert meta is None
        assert warning is None

    def test_artifact_id_in_registry(
        self,
        checker_with_registry: tuple[PreflightChecker, ArtifactRegistry],
        tmp_path: Path,
    ) -> None:
        """Artifact ID in registry → metadata dict."""
        checker, registry = checker_with_registry
        record = _make_artifact_record(
            registry, tmp_path, crs="EPSG:4326", bbox=[-121, 38, -120, 39]
        )

        meta, warning = checker._resolve_secondary_input(
            {"secondary": record.artifact_id}
        )
        assert meta is not None
        assert meta.get("crs") == "EPSG:4326"
        assert meta.get("bbox") == [-121, 38, -120, 39]
        assert warning is None

    def test_vector_file_path(
        self,
        checker_with_registry: tuple[PreflightChecker, ArtifactRegistry],
        tmp_path: Path,
    ) -> None:
        """Vector file path → CRS and bbox from gpd.read_file."""
        checker, registry = checker_with_registry

        geojson_path = _write_geojson(
            tmp_path,
            "secondary.geojson",
            [_make_valid_feature()],
            crs_code="EPSG:4326",
        )

        meta, warning = checker._resolve_secondary_input(
            {"secondary": str(geojson_path)}
        )
        # Should return metadata with CRS info
        assert meta is not None
        assert warning is None

    def test_nonexistent_file(
        self,
        checker_with_registry: tuple[PreflightChecker, ArtifactRegistry],
    ) -> None:
        """Non-existent file → (None, warning)."""
        checker, registry = checker_with_registry

        meta, warning = checker._resolve_secondary_input(
            {"secondary": "/nonexistent/file.geojson"}
        )
        assert meta is None
        assert warning is not None
        assert len(warning) > 0


# ---------------------------------------------------------------------------
# TestIsBinaryOp (2 tests)
# ---------------------------------------------------------------------------


class TestIsBinaryOp:
    """_is_binary_op — determines if a command is a binary (two-input) operation."""

    def test_binary_command_returns_true(
        self,
        checker_with_registry: tuple[PreflightChecker, ArtifactRegistry],
    ) -> None:
        """Command with secondary/second input → True."""
        checker, registry = checker_with_registry
        cmd = _make_binary_command()

        assert checker._is_binary_op(cmd) is True

    def test_unary_command_returns_false(
        self,
        checker_with_registry: tuple[PreflightChecker, ArtifactRegistry],
    ) -> None:
        """Command without secondary input → False."""
        checker, registry = checker_with_registry
        cmd = _make_command()

        assert checker._is_binary_op(cmd) is False


# ---------------------------------------------------------------------------
# TestRunAllChecks (3 tests)
# ---------------------------------------------------------------------------


class TestRunAllChecks:
    """run_all_checks — pipeline of all preflight checks.

    Note: run_all_checks takes a ResolvedCall (with .command attribute),
    not a raw CommandDescriptor.
    """

    def _make_resolved(self, cmd: CommandDescriptor) -> MagicMock:
        """Create a mock ResolvedCall with the given command."""
        resolved = MagicMock()
        resolved.command = cmd
        resolved.params = {}
        return resolved

    def test_all_pass_no_blocks(
        self,
        checker_with_registry: tuple[PreflightChecker, ArtifactRegistry],
        tmp_path: Path,
    ) -> None:
        """All checks pass → no BLOCK in results."""
        checker, registry = checker_with_registry
        cmd = _make_command()
        resolved = self._make_resolved(cmd)
        art = _make_artifact(tmp_path, crs="EPSG:3857", bbox=[-121, 38, -120, 39])

        results = checker.run_all_checks(resolved, art, {})
        assert all(r.resolution != Resolution.BLOCK for r in results)

    def test_first_block_stops(
        self,
        checker_with_registry: tuple[PreflightChecker, ArtifactRegistry],
        tmp_path: Path,
    ) -> None:
        """First BLOCK stops the pipeline — verify ordering."""
        checker, registry = checker_with_registry
        cmd = _make_planar_command()
        resolved = self._make_resolved(cmd)
        art = _make_artifact(tmp_path, crs="EPSG:4326")

        results = checker.run_all_checks(resolved, art, {})
        # At least one BLOCK should exist
        block_results = [r for r in results if r.resolution == Resolution.BLOCK]
        assert len(block_results) >= 1

    def test_model_discretion_collected(
        self,
        checker_with_registry: tuple[PreflightChecker, ArtifactRegistry],
        tmp_path: Path,
    ) -> None:
        """MODEL_DISCRETION results are collected and returned."""
        checker, registry = checker_with_registry
        cmd = _make_command()
        resolved = self._make_resolved(cmd)

        # Create artifact with resolution in envelope for resolution_sanity check
        p = tmp_path / "input_res.bin"
        p.write_bytes(b"\x00" * 1024)
        art = MagicMock()
        art.path = p
        art.crs = "EPSG:3857"
        art.bbox = None
        art.data_type = "raster"
        art.format = "geotiff"
        art.file_size_bytes = 1024
        art.envelope = {"data": {"resolution": 30.0}}

        # Pass an extremely fine resolution to trigger MODEL_DISCRETION
        results = checker.run_all_checks(resolved, art, {"resolution": 0.00001})
        md_results = [
            r for r in results if r.resolution == Resolution.MODEL_DISCRETION
        ]
        assert len(md_results) >= 1


# ---------------------------------------------------------------------------
# TestPreflightResultDataclass (4 tests)
# ---------------------------------------------------------------------------


class TestPreflightResultDataclass:
    """PreflightResult dataclass properties."""

    def test_ok_true_for_pass(self) -> None:
        """ok property returns True for PASS."""
        r = PreflightResult(check="test", resolution=Resolution.PASS, message="")
        assert r.ok is True
        assert r.error == ""

    def test_ok_true_for_model_discretion(self) -> None:
        """ok property returns True for MODEL_DISCRETION."""
        r = PreflightResult(
            check="test", resolution=Resolution.MODEL_DISCRETION, message="warn"
        )
        assert r.ok is True
        assert r.error == ""

    def test_ok_false_for_block(self) -> None:
        """ok property returns False for BLOCK."""
        r = PreflightResult(
            check="test", resolution=Resolution.BLOCK, message="blocked"
        )
        assert r.ok is False

    def test_error_returns_message_for_block(self) -> None:
        """error property returns message for BLOCK."""
        r = PreflightResult(
            check="test", resolution=Resolution.BLOCK, message="something wrong"
        )
        assert r.error == "something wrong"

    def test_error_empty_for_auto_fix(self) -> None:
        """error property returns empty string for AUTO_FIX."""
        r = PreflightResult(
            check="test", resolution=Resolution.AUTO_FIX, message="auto fix msg"
        )
        assert r.error == ""

    def test_diagnostics_default_empty(self) -> None:
        """diagnostics defaults to empty dict."""
        r = PreflightResult(check="test", resolution=Resolution.PASS)
        assert r.diagnostics == {}
