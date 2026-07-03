"""Tests for raster tile serving (web/tiles.py)."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest
import rasterio
from rasterio.transform import from_bounds

from ecospheric_harness.web.tiles import get_tile_bounds, render_preview_png, serve_tile


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def test_raster(tmp_path: Path) -> Path:
    """Create a small single-band GeoTIFF for tile tests."""
    path = tmp_path / "test.tif"
    data = np.random.randint(0, 255, (1, 256, 256), dtype="uint8")
    with rasterio.open(
        path,
        "w",
        driver="GTiff",
        width=256,
        height=256,
        count=1,
        dtype="uint8",
        crs="EPSG:4326",
        transform=from_bounds(0, 0, 1, 1, 256, 256),
    ) as dst:
        dst.write(data)
    return path


# ---------------------------------------------------------------------------
# 1. serve_tile returns PNG bytes
# ---------------------------------------------------------------------------


def test_serve_tile_returns_png(test_raster: Path):
    """serve_tile() must return bytes starting with the PNG magic signature."""
    # z=0, x=0, y=0 covers the entire world — our tiny raster is inside it.
    result = serve_tile(test_raster, z=0, x=0, y=0)

    assert isinstance(result, bytes)
    assert len(result) > 0
    # PNG magic bytes: 0x89 0x50 0x4E 0x47
    assert result[:4] == b"\x89PNG"


# ---------------------------------------------------------------------------
# 2. get_tile_bounds returns expected metadata
# ---------------------------------------------------------------------------


def test_get_tile_bounds_returns_metadata(test_raster: Path):
    """get_tile_bounds() dict must contain all required keys."""
    meta = get_tile_bounds(test_raster)

    expected_keys = {"bounds", "width", "height", "bands", "crs", "dtype", "min_zoom", "max_zoom"}
    assert set(meta.keys()) == expected_keys

    # Sanity-check values
    assert meta["width"] == 256
    assert meta["height"] == 256
    assert meta["bands"] == 1
    assert meta["dtype"] == "uint8"
    assert isinstance(meta["bounds"], (list, tuple))
    assert len(meta["bounds"]) == 4
    assert isinstance(meta["min_zoom"], int)
    assert isinstance(meta["max_zoom"], int)
    assert meta["min_zoom"] <= meta["max_zoom"]


# ---------------------------------------------------------------------------
# 3. serve_tile raises on missing file
# ---------------------------------------------------------------------------


def test_serve_tile_missing_file(tmp_path: Path):
    """serve_tile() with a non-existent path must raise FileNotFoundError."""
    bad_path = tmp_path / "does_not_exist.tif"

    with pytest.raises(FileNotFoundError):
        serve_tile(bad_path, z=0, x=0, y=0)


# ---------------------------------------------------------------------------
# 4. render_preview_png returns PNG bytes
# ---------------------------------------------------------------------------


def test_render_preview_png(test_raster: Path):
    """render_preview_png() must return bytes starting with the PNG magic signature."""
    result = render_preview_png(test_raster)

    assert isinstance(result, bytes)
    assert len(result) > 0
    assert result[:4] == b"\x89PNG"
