"""Tests for artifact types and format normalization."""

from __future__ import annotations

from pathlib import Path


from ecospheric_harness.artifact import (
    FORMAT_ALIASES,
    Artifact,
    normalize_format,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_artifact(tmp_path: Path, name: str, body: bytes = b"x", **kw: object) -> Artifact:
    """Create a real temp file and return an Artifact pointing at it."""
    p = tmp_path / name
    p.write_bytes(body)
    defaults: dict[str, object] = {
        "path": p,
        "envelope": {},
        "format": "json",
        "data_type": "vector",
    }
    defaults.update(kw)
    return Artifact(**defaults)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Artifact dataclass
# ---------------------------------------------------------------------------


class TestArtifact:
    def test_create_artifact(self, tmp_path: Path) -> None:
        p = tmp_path / "test.bin"
        p.write_bytes(b"test")
        artifact = Artifact(
            path=p,
            envelope={"status": "success"},
            format="geotiff",
            data_type="raster",
            crs="EPSG:4326",
            bbox=[-180, -90, 180, 90],
            step_number=1,
        )
        assert artifact.path == p
        assert artifact.format == "geotiff"
        assert artifact.data_type == "raster"
        assert artifact.crs == "EPSG:4326"
        assert artifact.bbox == [-180, -90, 180, 90]
        assert artifact.step_number == 1


# ---------------------------------------------------------------------------
# Format normalization
# ---------------------------------------------------------------------------


class TestNormalizeFormat:
    def test_all_aliases(self) -> None:
        for alias, canonical in FORMAT_ALIASES.items():
            assert normalize_format(alias) == canonical

    def test_uppercase(self) -> None:
        assert normalize_format("TIF") == "geotiff"
        assert normalize_format("GeoJSON") == "geojson"

    def test_unknown_passthrough(self) -> None:
        assert normalize_format("weirdfmt") == "weirdfmt"
        assert normalize_format("WEIRDFMT") == "weirdfmt"
