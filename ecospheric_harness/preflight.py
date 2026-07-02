"""Pre-flight checks for the Ecospheric Agent Harness."""

from __future__ import annotations

from typing import Any

import pyproj
from etp.describe import CommandDescriptor
from pyproj.exceptions import CRSError

from ecospheric_harness.artifact import Artifact
from ecospheric_harness.artifact_registry import ArtifactRecord, ArtifactRegistry
from ecospheric_harness.intents import PreflightResult
from ecospheric_harness.security import check_ssrf as _check_ssrf_url
from ecospheric_harness.workspace import WorkspaceManager


class PreflightChecker:
    """Validates that a command can safely execute on the available resources."""

    def __init__(
        self, registry: ArtifactRegistry, workspace: WorkspaceManager
    ) -> None:
        self._registry = registry
        self._workspace = workspace

    # ------------------------------------------------------------------
    # CRS checks
    # ------------------------------------------------------------------

    def check_planar_crs(
        self,
        command: CommandDescriptor,
        artifact: Artifact | ArtifactRecord | None,
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
        input_artifact: Artifact | ArtifactRecord | None = None,
        expansion_factor: float = 2.0,
    ) -> PreflightResult:
        """Verify that *estimated_bytes* (or a derived estimate) fit on disk.

        Uses registry's bytes_used and disk_limit for accounting.
        """
        if estimated_bytes == 0 and input_artifact is not None:
            estimate = int(input_artifact.path.stat().st_size * expansion_factor)
        elif estimated_bytes == 0:
            estimate = 500 * 1024 * 1024  # 500 MB fallback
        else:
            estimate = estimated_bytes

        # Projected disk usage: bytes_used + new estimate
        projected = self._registry.bytes_used + estimate
        if projected >= self._registry._disk_limit:
            current_mb = self._registry.bytes_used / (1024 * 1024)
            limit_mb = self._registry._disk_limit / (1024 * 1024)
            return PreflightResult(
                ok=False,
                error=(
                    f"Insufficient disk space: need {estimate / (1024 * 1024):.1f} MB "
                    f"but only {limit_mb - current_mb:.1f} MB available "
                    f"(limit {limit_mb:.1f} MB)."
                ),
            )

        return PreflightResult(ok=True)

    # ------------------------------------------------------------------
    # SSRF checks
    # ------------------------------------------------------------------

    def check_ssrf(self, params: dict[str, Any]) -> PreflightResult:
        """Scan param values for URLs and check each against blocked ranges.

        Returns PreflightResult(ok=False, error=...) if any URL targets an
        internal/metadata address.
        """
        for key, value in params.items():
            if key == "_input_target":
                continue
            if not isinstance(value, str):
                continue
            if not (value.startswith("http://") or value.startswith("https://")):
                continue
            try:
                _check_ssrf_url(value)
            except ValueError as exc:
                return PreflightResult(
                    ok=False,
                    error=f"URL in param '{key}' is blocked: {exc}",
                )
        return PreflightResult(ok=True)

    # ------------------------------------------------------------------
    # Disk availability (delegating to registry)
    # ------------------------------------------------------------------

    def check_disk_available(self, estimated_bytes: int = 0) -> bool:
        """Check if estimated_bytes fit within the disk limit."""
        return self._registry.bytes_used + estimated_bytes < self._registry._disk_limit
