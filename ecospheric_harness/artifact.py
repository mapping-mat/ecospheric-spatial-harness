"""Artifact types for the Ecospheric Agent Harness."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any


# ---------------------------------------------------------------------------
# Format alias registry
# ---------------------------------------------------------------------------

FORMAT_ALIASES: dict[str, str] = {
    "tif": "geotiff",
    "gtiff": "geotiff",
    "cog": "cog",
    "geoparquet": "geoparquet",
    "parquet": "geoparquet",
    "geojson": "geojson",
    "shp": "shp",
    "gpkg": "gpkg",
    "fgb": "fgb",
    "kml": "kml",
    "laz": "laz",
    "las": "las",
    "ply": "ply",
    "ascii": "ascii",
    "json": "json",
}


def normalize_format(fmt: str) -> str:
    """Lowercase *fmt* and resolve it through :data:`FORMAT_ALIASES`."""
    return FORMAT_ALIASES.get(fmt.lower(), fmt.lower())


# ---------------------------------------------------------------------------
# Artifact dataclass (kept for backward compatibility)
# ---------------------------------------------------------------------------


@dataclass
class Artifact:
    """A single geospatial data artifact with provenance metadata.
    
    Note: This class is kept for backward compatibility. New code should use
    ArtifactRecord from artifact_registry.py instead.
    """

    path: Path
    envelope: dict[str, Any]
    format: str
    data_type: str
    crs: str | None = None
    bbox: list[float] | None = None
    step_number: int = 0
