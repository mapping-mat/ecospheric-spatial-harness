"""Pre-flight checks for the Ecospheric Agent Harness."""

from __future__ import annotations

from pathlib import Path

import pyproj
from etp.describe import CommandDescriptor
from pyproj.exceptions import CRSError

from ecospheric_harness.artifact import Artifact, ArtifactManager
from ecospheric_harness.intents import PreflightResult


class PreflightChecker:
    """Validates that a command can safely execute on the available resources."""

    def __init__(self, artifacts: ArtifactManager, workdir: Path) -> None:
        self._artifacts = artifacts
        self._workdir = workdir

    # ------------------------------------------------------------------
    # CRS checks
    # ------------------------------------------------------------------

    def check_planar_crs(
        self,
        command: CommandDescriptor,
        artifact: Artifact | None,
    ) -> PreflightResult:
        """Verify that *artifact*'s CRS is planar when the command requires it."""
        if not command.requires_planar_crs:
            return PreflightResult(ok=True)

        if artifact is None:
            return PreflightResult(ok=True)

        if artifact.crs is None:
            return PreflightResult(
                ok=False,
                error=(
                    f"Command '{command.name}' requires planar CRS but input "
                    f"CRS is unknown. Reproject to a planar CRS "
                    f"(e.g. EPSG:3857) first."
                ),
            )

        try:
            crs = pyproj.CRS(artifact.crs)
        except CRSError:
            return PreflightResult(
                ok=False,
                error=(
                    f"Command '{command.name}' requires planar CRS but input "
                    f"CRS '{artifact.crs}' could not be parsed. "
                    f"Reproject to a planar CRS (e.g. EPSG:3857) first."
                ),
            )

        if crs.is_geographic:
            return PreflightResult(
                ok=False,
                error=(
                    f"Command '{command.name}' requires planar CRS but input "
                    f"is {artifact.crs} (geographic). Reproject to a planar "
                    f"CRS (e.g. EPSG:3857) first."
                ),
            )

        return PreflightResult(ok=True)

    # ------------------------------------------------------------------
    # Disk checks
    # ------------------------------------------------------------------

    def check_disk(
        self,
        estimated_bytes: int = 0,
        input_artifact: Artifact | None = None,
        expansion_factor: float = 2.0,
    ) -> PreflightResult:
        """Verify that *estimated_bytes* (or a derived estimate) fit on disk.

        Uses ``current_bytes`` (current artifact only) rather than
        ``total_bytes`` because ``previous`` will be freed on the next
        ``store()`` call.
        """
        if estimated_bytes == 0 and input_artifact is not None:
            estimate = int(input_artifact.path.stat().st_size * expansion_factor)
        elif estimated_bytes == 0:
            estimate = 500 * 1024 * 1024  # 500 MB fallback
        else:
            estimate = estimated_bytes

        # Projected disk usage after next store(): current_bytes + new estimate.
        # (previous will be freed, so total_bytes overcounts.)
        projected = self._artifacts.current_bytes + estimate
        if projected >= self._artifacts.disk_limit:
            current_mb = self._artifacts.current_bytes / (1024 * 1024)
            limit_mb = self._artifacts.disk_limit / (1024 * 1024)
            return PreflightResult(
                ok=False,
                error=(
                    f"Insufficient disk space: need {estimate / (1024 * 1024):.1f} MB "
                    f"but only {limit_mb - current_mb:.1f} MB available "
                    f"(limit {limit_mb:.1f} MB)."
                ),
            )

        return PreflightResult(ok=True)
