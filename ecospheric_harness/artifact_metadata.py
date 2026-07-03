"""Extract CRS and bbox metadata from tool envelopes and output files.

ESE and EDD tools emit slightly different envelope shapes. This module
provides a single source of truth for the fallback chain used to recover
CRS and bbox metadata, plus a derivation path that reads the actual
output file when the envelope is silent.

The fallback chain for CRS is:

  1. ``data.crs``                — most common (EDD, ESE convert/buffer)
  2. ``data.output_crs``         — some pipelines
  3. ``data.to_crs``             — ESE ``proj transform`` target CRS
  4. ``data.from_crs``           — ESE ``proj transform`` source CRS
  5. ``data.crs_meta.crs``       — nested (legacy)
  6. ``data.provenance[*].crs_working_crs`` — ESE puts working CRS here

The fallback chain for bbox is:

  1. ``data.bbox``
  2. ``data.bounds``
  3. ``data.extent``

When none of those keys are present (very common — ESE tools do not
currently emit bbox in the envelope), the orchestrator can derive
bbox from the actual output file using geopandas (vector) or rasterio
(raster).  The venv is expected to have geopandas; rasterio is used
opportunistically and the function falls back to ``None`` if it is
not importable.
"""

from __future__ import annotations

import json
import logging
import subprocess
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Envelope key constants
# ---------------------------------------------------------------------------

# Tool envelope CRS keys, in order of preference (most specific first).
# Each tool emits different keys; this is the canonical fallback chain.
_CRS_KEYS: tuple[str, ...] = (
    "crs",  # ESE convert/buffer, EDD search
    "output_crs",  # some tools
    "to_crs",  # ESE proj transform (target)
    "from_crs",  # ESE proj transform (source, fallback)
)

# Tool envelope bbox keys, in order of preference.
_BBOX_KEYS: tuple[str, ...] = (
    "bbox",
    "bounds",
    "extent",
)


# ---------------------------------------------------------------------------
# Envelope-based extraction
# ---------------------------------------------------------------------------


def extract_crs(envelope: dict[str, Any] | None) -> str | None:
    """Extract CRS string from a tool envelope.

    Walks the standard fallback chain of envelope keys. Returns the
    first non-empty CRS value as a string, or ``None`` if no CRS
    can be located anywhere in the envelope.

    Never raises — a malformed envelope is treated as "no CRS found".
    """
    if not envelope:
        return None
    data = envelope.get("data") if isinstance(envelope, dict) else None
    if not isinstance(data, dict):
        return None

    # Direct key lookup
    for key in _CRS_KEYS:
        val = data.get(key)
        if val:
            return str(val)

    # Nested: data.crs_meta.crs
    crs_meta = data.get("crs_meta")
    if isinstance(crs_meta, dict):
        val = crs_meta.get("crs")
        if val:
            return str(val)

    # Nested: data.provenance[*].crs_working_crs (ESE puts working CRS here)
    prov = data.get("provenance")
    if isinstance(prov, list):
        for entry in prov:
            if isinstance(entry, dict):
                val = entry.get("crs_working_crs")
                if val:
                    return str(val)

    return None


def extract_bbox(envelope: dict[str, Any] | None) -> list[float] | None:
    """Extract bbox from a tool envelope.

    Walks the standard fallback chain of bbox keys. Returns the first
    valid 4-element numeric sequence, or ``None`` if no bbox is found.

    Never raises — a malformed envelope is treated as "no bbox found".
    """
    if not envelope:
        return None
    data = envelope.get("data") if isinstance(envelope, dict) else None
    if not isinstance(data, dict):
        return None

    for key in _BBOX_KEYS:
        val = data.get(key)
        if _is_valid_bbox(val):
            return [float(v) for v in val]  # type: ignore[union-attr]

    return None


# ---------------------------------------------------------------------------
# File-based derivation
# ---------------------------------------------------------------------------


def derive_bbox_from_file(
    path: Path | None,
    data_type: str = "unknown",
) -> list[float] | None:
    """Compute bbox from a geospatial file on disk.

    For vector files, uses :mod:`geopandas` to compute ``total_bounds``.
    For raster files, uses :mod:`rasterio` if importable; otherwise
    returns ``None``.

    Parameters
    ----------
    path:
        Path to the geospatial file.  ``None`` or a missing file
        returns ``None``.
    data_type:
        One of ``"vector"``, ``"raster"``, or ``"unknown"``.  For
        ``"unknown"`` the helper tries vector first then raster.

    Returns
    -------
    ``[xmin, ymin, xmax, ymax]`` on success, ``None`` on any failure.
    Errors are logged at DEBUG level and swallowed — bbox derivation
    is a best-effort enhancement, never a hard requirement.
    """
    if path is None:
        return None
    if not path.exists():
        return None

    try:
        if data_type == "vector":
            return _bbox_vector(path)
        if data_type == "raster":
            return _bbox_raster(path)
        # Unknown: try vector first, then raster.
        bbox = _bbox_vector(path)
        if bbox is not None:
            return bbox
        return _bbox_raster(path)
    except Exception as exc:  # pragma: no cover - defensive
        logger.debug("derive_bbox_from_file failed for %s: %s", path, exc)
        return None


def _bbox_vector(path: Path) -> list[float] | None:
    """Compute bbox for a vector file.

    Tries :mod:`geopandas` first (the most general path for
    GeoJSON, Shapefile, GeoPackage, etc.), and falls back to a
    :mod:`pyarrow` + :mod:`shapely` direct path for ``.parquet`` /
    ``.geoparquet`` files when geopandas fails (for example, when
    the environment is missing a backend like ``libduckdb``).
    """
    # 1. Try geopandas — handles GeoJSON, Shapefile, GeoPackage, GDB, KML, …
    try:
        import geopandas as gpd  # type: ignore[import-not-found]
    except ImportError:
        gpd = None  # type: ignore[assignment]

    if gpd is not None:
        try:
            gdf = gpd.read_file(str(path))
        except Exception as exc:
            logger.debug("geopandas read_file failed for %s: %s", path, exc)
            gdf = None  # type: ignore[assignment]
        if gdf is not None:
            if gdf.empty:
                return None
            try:
                bounds = gdf.total_bounds
            except Exception as exc:
                logger.debug("geopandas total_bounds failed for %s: %s", path, exc)
                bounds = None
            if bounds is not None and len(bounds) == 4:
                return [
                    float(bounds[0]), float(bounds[1]),
                    float(bounds[2]), float(bounds[3]),
                ]

    # 2. Fallback for parquet / geoparquet: pyarrow + shapely WKB.
    #    This avoids dependencies on geopandas' default backend
    #    (e.g. fiona / pyogrio / duckdb) which may be missing in
    #    some environments.
    suffix = path.suffix.lower()
    if suffix in (".parquet", ".geoparquet"):
        return _bbox_parquet_pyarrow(path)

    return None


def _bbox_parquet_pyarrow(path: Path) -> list[float] | None:
    """Compute bbox for a Parquet file by reading the WKB-encoded
    geometry column via pyarrow and decoding each geometry with
    :mod:`shapely`.
    """
    try:
        import pyarrow.parquet as pq  # type: ignore[import-not-found]
        import shapely.wkb as wkb_mod  # type: ignore[import-not-found]
    except ImportError as exc:
        logger.debug("pyarrow/shapely not available for parquet bbox: %s", exc)
        return None

    try:
        table = pq.read_table(str(path), columns=["geometry"])
    except Exception as exc:
        logger.debug("pyarrow read_table failed for %s: %s", path, exc)
        return None

    col = table.column("geometry")
    # Materialize once; for very large files, this is bounded by
    # available memory but acceptable for the post-execution metadata
    # extraction use case.
    try:
        rows = col.to_pylist()
    except Exception as exc:
        logger.debug("pyarrow to_pylist failed for %s: %s", path, exc)
        return None

    minx: float | None = None
    miny: float | None = None
    maxx: float | None = None
    maxy: float | None = None

    for raw in rows:
        if raw is None:
            continue
        if not isinstance(raw, (bytes, bytearray, memoryview)):
            continue
        try:
            geom = wkb_mod.loads(bytes(raw))
            bx0, by0, bx1, by1 = geom.bounds
        except Exception:
            continue
        if minx is None or bx0 < minx:
            minx = bx0
        if miny is None or by0 < miny:
            miny = by0
        if maxx is None or bx1 > maxx:
            maxx = bx1
        if maxy is None or by1 > maxy:
            maxy = by1

    if minx is None:
        return None
    return [float(minx), float(miny), float(maxx), float(maxy)]


def _bbox_raster(path: Path) -> list[float] | None:
    """Compute bbox for a raster file.

    Tries :mod:`rasterio` first (preferred when available), then falls
    back to a subprocess call to ``gdalinfo -json`` if rasterio is not
    importable.  Returns ``None`` on any failure.
    """
    try:
        import rasterio  # type: ignore[import-not-found]
    except ImportError:
        rasterio = None  # type: ignore[assignment]

    if rasterio is not None:
        try:
            with rasterio.open(str(path)) as src:
                b = src.bounds
                return [float(b.left), float(b.bottom), float(b.right), float(b.top)]
        except Exception as exc:
            logger.debug("rasterio open failed for %s: %s", path, exc)
            # fall through to subprocess attempt

    # Subprocess fallback: gdalinfo -json
    try:
        result = subprocess.run(
            ["gdalinfo", "-json", str(path)],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode != 0:
            return None
        info = json.loads(result.stdout)
        corners = info.get("cornerCoordinates", {})
        ul = corners.get("upperLeft")
        lr = corners.get("lowerRight")
        if ul and lr and len(ul) >= 2 and len(lr) >= 2:
            return [float(ul[0]), float(lr[1]), float(lr[0]), float(ul[1])]
    except (FileNotFoundError, subprocess.TimeoutExpired, json.JSONDecodeError):
        return None
    except Exception as exc:  # pragma: no cover - defensive
        logger.debug("gdalinfo subprocess failed for %s: %s", path, exc)
        return None

    return None


# ---------------------------------------------------------------------------
# Combined helper
# ---------------------------------------------------------------------------


def extract_or_derive_bbox(
    envelope: dict[str, Any] | None,
    output_path: Path | None,
    data_type: str = "unknown",
) -> list[float] | None:
    """Return bbox from envelope, falling back to deriving from output file.

    This is the canonical entry point the orchestrator and corrections
    handler should use when populating an :class:`ArtifactRecord`'s
    ``bbox`` field.
    """
    bbox = extract_bbox(envelope)
    if bbox is not None:
        return bbox
    return derive_bbox_from_file(output_path, data_type)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _is_valid_bbox(val: Any) -> bool:
    """Return True if ``val`` is a 4-element numeric sequence."""
    if not isinstance(val, (list, tuple)):
        return False
    if len(val) != 4:
        return False
    try:
        for v in val:
            float(v)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return False
    return True
