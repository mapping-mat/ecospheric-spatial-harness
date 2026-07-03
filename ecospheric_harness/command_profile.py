"""Command memory classification for the Ecospheric Agent Harness."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class CommandProfile:
    """Memory behavior profile for an ESE command."""
    memory_class: str   # "streaming", "full_load", "depends"
    memory_multiplier: float


# Keyed by (command_name, data_type) — avoids clip-raster vs clip-vector confusion
COMMAND_PROFILES: dict[tuple[str, str], CommandProfile] = {
    # Raster ops
    ("reproject", "raster"): CommandProfile("full_load", 3.0),
    ("clip", "raster"): CommandProfile("streaming", 1.5),
    ("slope", "raster"): CommandProfile("streaming", 1.5),
    ("aspect", "raster"): CommandProfile("streaming", 1.5),
    ("hillshade", "raster"): CommandProfile("streaming", 1.5),
    ("contour", "raster"): CommandProfile("streaming", 2.0),
    ("rasterize", "raster"): CommandProfile("full_load", 3.0),
    ("reclassify", "raster"): CommandProfile("streaming", 1.5),
    ("tile", "raster"): CommandProfile("streaming", 1.5),
    ("mosaic", "raster"): CommandProfile("full_load", 2.0),
    ("calc", "raster"): CommandProfile("full_load", 3.0),
    # Vector ops
    ("reproject", "vector"): CommandProfile("full_load", 2.0),
    ("clip", "vector"): CommandProfile("full_load", 2.0),
    ("buffer", "vector"): CommandProfile("full_load", 2.0),
    ("dissolve", "vector"): CommandProfile("full_load", 2.0),
    ("intersection", "vector"): CommandProfile("full_load", 2.0),
    ("union", "vector"): CommandProfile("full_load", 2.0),
    ("difference", "vector"): CommandProfile("full_load", 2.0),
    ("simplify", "vector"): CommandProfile("full_load", 2.0),
    ("centroid", "vector"): CommandProfile("full_load", 1.5),
    # Diagnostic
    ("info", "raster"): CommandProfile("streaming", 1.0),
    ("info", "vector"): CommandProfile("streaming", 1.0),
    ("describe", "raster"): CommandProfile("streaming", 1.0),
    ("describe", "vector"): CommandProfile("streaming", 1.0),
    # Pointcloud
    ("info", "pointcloud"): CommandProfile("streaming", 1.0),
    ("reproject", "pointcloud"): CommandProfile("full_load", 3.0),
    ("clip", "pointcloud"): CommandProfile("full_load", 2.0),
}

DEFAULT_PROFILE = CommandProfile("full_load", 3.0)

# Dtype name → byte size
_DTYPE_SIZES: dict[str, int] = {
    "int8": 1, "uint8": 1,
    "int16": 2, "uint16": 2,
    "int32": 4, "uint32": 4,
    "int64": 8, "uint64": 8,
    "float32": 4, "float64": 8,
    "cfloat32": 8, "cfloat64": 16,
}


def get_profile(command_name: str, data_type: str) -> CommandProfile:
    """Look up the memory profile for a command + data type."""
    return COMMAND_PROFILES.get((command_name.lower(), data_type.lower()), DEFAULT_PROFILE)


def dtype_size(dtype: str | None) -> int:
    """Get byte size for a dtype name. Defaults to 4 (float32)."""
    if dtype is None:
        return 4
    return _DTYPE_SIZES.get(dtype.lower().strip(), 4)


def estimate_rss_bytes(
    profile: CommandProfile,
    envelope: dict,
    file_size_bytes: int = 0,
) -> tuple[int, str]:
    """Estimate peak RSS in bytes for a command execution.

    Returns (estimate_bytes, confidence) where confidence is "high" or "low".
    """
    data = envelope.get("data", {})
    data_type = data.get("data_type", "unknown")

    if data_type == "raster":
        width = data.get("width")
        height = data.get("height")
        bands = data.get("bands", 1)
        dt = data.get("dtype")

        if width is not None and height is not None:
            dt_bytes = dtype_size(dt)
            estimate = int(width * height * bands * dt_bytes * profile.memory_multiplier)
            confidence = "high" if dt is not None else "low"
            return estimate, confidence
        else:
            # Can't determine raster size from metadata
            estimate = int(file_size_bytes * profile.memory_multiplier) if file_size_bytes > 0 else 0
            return estimate, "low"

    elif data_type == "vector":
        feature_count = data.get("feature_count")
        if feature_count is not None and feature_count > 0:
            # 500 bytes/feature empirical default for polygon geometries
            estimate = int(feature_count * 500 * profile.memory_multiplier)
            return estimate, "high"
        else:
            # Fall back to file size × 5 (GeoParquet compression factor)
            estimate = int(file_size_bytes * 5) if file_size_bytes > 0 else 0
            return estimate, "low"

    elif data_type == "pointcloud":
        estimate = int(file_size_bytes * 3) if file_size_bytes > 0 else 0
        return estimate, "low"

    else:
        # Unknown data type — conservative fallback
        estimate = int(file_size_bytes * 3) if file_size_bytes > 0 else 0
        return estimate, "low"