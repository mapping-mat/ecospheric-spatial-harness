"""Raster tile serving via rio-tiler for the Ecospheric Spatial Harness."""

from __future__ import annotations

import math
from pathlib import Path
from typing import Any

from rio_tiler.io import Reader
from rio_tiler.models import ImageData
from rasterio.warp import transform_bounds
from rasterio.windows import Window


def serve_tile(
    raster_path: Path,
    z: int,
    x: int,
    y: int,
    tilesize: int = 256,
) -> bytes:
    """Serve a single XYZ tile from a raster file as PNG bytes.

    Args:
        raster_path: Path to GeoTIFF/COG file
        z, x, y: XYZ tile coordinates (Web Mercator / EPSG:3857)
        tilesize: Tile pixel size (default 256)

    Returns:
        PNG image bytes

    Raises:
        FileNotFoundError: raster_path doesn't exist
        ValueError: tile is outside raster extent
    """
    if not raster_path.exists():
        raise FileNotFoundError(f"Raster file not found: {raster_path}")

    with Reader(str(raster_path)) as reader:
        # tile() returns ImageData with .data (numpy array) and .crs
        img = reader.tile(z, x, y, tilesize=tilesize)
        # Render as PNG bytes
        return img.render(img_format="PNG")


def get_tile_bounds(raster_path: Path) -> dict[str, Any]:
    """Get raster metadata for tile serving.

    Returns dict with:
        bounds: [west, south, east, north] in EPSG:4326
        width: pixel width
        height: pixel height
        bands: band count
        crs: source CRS
        dtype: data type
        min_zoom: minimum zoom level where data is visible
        max_zoom: maximum zoom level
    """
    if not raster_path.exists():
        raise FileNotFoundError(f"Raster file not found: {raster_path}")

    with Reader(str(raster_path)) as reader:
        dataset = reader.dataset
        bounds = list(dataset.bounds)  # (left, bottom, right, top) in source CRS

        # Calculate zoom levels from resolution
        # Web Mercator zoom 0 = world, each level halves resolution
        # Min zoom: where one tile covers the entire raster
        # Max zoom: where one pixel = one tile pixel

        # Transform bounds to EPSG:4326 (for frontend) and EPSG:3857 (for zoom calc)
        wgs84_bounds = transform_bounds(dataset.crs, "EPSG:4326", *bounds)
        mercator_bounds = transform_bounds(dataset.crs, "EPSG:3857", *bounds)

        # Calculate zoom levels from Web Mercator resolution
        # Web Mercator zoom 0 = world, each level halves resolution
        # Earth circumference at equator = 40075016.686 m
        # Tile size at zoom z = 40075016.686 / (2^z) meters per pixel
        mercator_w = abs(mercator_bounds[2] - mercator_bounds[0])
        mercator_h = abs(mercator_bounds[3] - mercator_bounds[1])
        res_x = mercator_w / dataset.width if dataset.width > 0 else 0
        res_y = mercator_h / dataset.height if dataset.height > 0 else 0

        # Max zoom: pixel resolution matches tile resolution
        # Min resolution = max(res_x, res_y) in meters (approximate)
        if res_x > 0 and res_y > 0:
            max_res = max(res_x, res_y)
            # Web Mercator resolution at zoom 0 = 156543.03 meters/pixel
            max_zoom = int(math.log2(156543.03 / max_res))
            max_zoom = max(0, min(20, max_zoom))

            # Min zoom: where the raster fits in one tile (256px)
            raster_width_m = mercator_w
            if raster_width_m > 0:
                min_zoom = int(math.log2(40075016.686 / raster_width_m))
                min_zoom = max(0, min(max_zoom, min_zoom))
            else:
                min_zoom = 0
        else:
            min_zoom = 0
            max_zoom = 18

        return {
            "bounds": wgs84_bounds,
            "width": dataset.width,
            "height": dataset.height,
            "bands": dataset.count,
            "crs": str(dataset.crs),
            "dtype": dataset.dtypes[0] if dataset.dtypes else "unknown",
            "min_zoom": min_zoom,
            "max_zoom": max_zoom,
        }


def render_preview_png(raster_path: Path, max_size: int = 1024) -> bytes:
    """Render a full-raster preview PNG (downsampled to max_size).

    Useful for thumbnails and small previews.
    """
    if not raster_path.exists():
        raise FileNotFoundError(f"Raster file not found: {raster_path}")

    with Reader(str(raster_path)) as reader:
        # Read full raster at reduced resolution
        dataset = reader.dataset
        # Calculate read size
        scale = min(max_size / dataset.width, max_size / dataset.height, 1.0)
        read_width = max(1, int(dataset.width * scale))
        read_height = max(1, int(dataset.height * scale))

        # Read windowed
        data = dataset.read(
            window=Window(0, 0, dataset.width, dataset.height),
            out_shape=(dataset.count, read_height, read_width),
        )

        # Use rio-tiler's ImageData to render
        img = ImageData(data, crs=dataset.crs, bounds=dataset.bounds)
        return img.render(img_format="PNG")
