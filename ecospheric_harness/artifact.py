"""Sliding-window artifact manager for the Ecospheric Agent Harness."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ecospheric_harness.workspace import WorkspaceManager


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
# Artifact dataclass
# ---------------------------------------------------------------------------


@dataclass
class Artifact:
    """A single geospatial data artifact with provenance metadata."""

    path: Path
    envelope: dict[str, Any]
    format: str
    data_type: str
    crs: str | None = None
    bbox: list[float] | None = None
    step_number: int = 0


# ---------------------------------------------------------------------------
# ArtifactManager — two-artifact sliding window
# ---------------------------------------------------------------------------


class ArtifactManager:
    """Manages a sliding window of at most two artifacts on disk.

    State transitions::

        store(a):      current=a  previous=None
        store(b):      current=b  previous=a   (a evicted from window but NOT deleted)
        store(c):      current=c  previous=b   (a's file deleted)

    The key insight: *previous* keeps one step back for undo.
    On every third ``store``, the oldest artifact is truly deleted.
    """

    def __init__(
        self, workspace: WorkspaceManager, disk_limit_bytes: int
    ) -> None:
        self._workspace = workspace
        self._disk_limit = disk_limit_bytes
        self._current: Artifact | None = None
        self._previous: Artifact | None = None
        self._total_bytes: int = 0

    # -- helpers -----------------------------------------------------------

    @staticmethod
    def _artifact_size(artifact: Artifact) -> int:
        """Return byte size of *artifact* on disk, or 0 if stat fails."""
        try:
            return artifact.path.stat().st_size
        except OSError:
            return 0

    def _unlink(self, artifact: Artifact | None) -> int:
        """Unlink *artifact*'s file (missing_ok) and return its byte size."""
        if artifact is None:
            return 0
        size = self._artifact_size(artifact)
        artifact.path.unlink(missing_ok=True)
        return size

    # -- public API --------------------------------------------------------

    def disk_available(self, estimated_new_bytes: int = 0) -> bool:
        """Return ``True`` if *estimated_new_bytes* fit within the disk limit."""
        return self._total_bytes + estimated_new_bytes < self._disk_limit

    def store(self, artifact: Artifact) -> Artifact:
        """Slide the window forward: evict *previous*, promote *current*.

        Returns the newly stored artifact (now ``current``).
        """
        if self._previous is not None:
            old_path = self._previous.path
            self._total_bytes -= self._unlink(self._previous)
            self._workspace.release_bytes(old_path)
            self._previous = None

        self._previous = self._current
        self._current = artifact
        self._total_bytes += self._artifact_size(artifact)
        self._workspace.track_bytes(artifact.path)
        return self._current

    def replace_current(self, artifact: Artifact) -> Artifact:
        """Replace *current* in-place without touching *previous*.

        Returns the replacement artifact (now ``current``).
        """
        if self._current is not None:
            old_path = self._current.path
            self._total_bytes -= self._unlink(self._current)
            self._workspace.release_bytes(old_path)
        self._current = artifact
        self._total_bytes += self._artifact_size(artifact)
        self._workspace.track_bytes(artifact.path)
        return self._current

    def undo(self) -> Artifact | None:
        """Revert to *previous* (or ``None`` if nothing to undo).

        Returns the (new) current artifact after the undo.
        """
        if self._current is None:
            return None
        current_path = self._current.path
        self._total_bytes -= self._unlink(self._current)
        self._workspace.release_bytes(current_path)
        self._current = self._previous
        self._previous = None
        return self._current

    # -- property accessors ------------------------------------------------

    @property
    def current(self) -> Artifact | None:
        return self._current

    @property
    def previous(self) -> Artifact | None:
        return self._previous

    @property
    def can_undo(self) -> bool:
        return self._previous is not None

    @property
    def total_bytes(self) -> int:
        """Total bytes currently tracked in the artifact window."""
        return self._total_bytes

    @property
    def current_bytes(self) -> int:
        """Bytes used by current artifact only.

        Previous will be freed on next store(), so this is the projected
        in-use bytes after the next store() call (excluding the new artifact).
        """
        return self._artifact_size(self._current) if self._current else 0

    @property
    def disk_limit(self) -> int:
        """Maximum disk bytes allowed."""
        return self._disk_limit

    # -- teardown ----------------------------------------------------------

    def free(self) -> None:
        """Unlink all managed artifacts and reset byte tracking."""
        if self._previous is not None:
            self._workspace.release_bytes(self._previous.path)
        if self._current is not None:
            self._workspace.release_bytes(self._current.path)
        self._unlink(self._previous)
        self._unlink(self._current)
        self._previous = None
        self._current = None
        self._total_bytes = 0