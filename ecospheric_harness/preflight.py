"""Pre-flight checks for the Ecospheric Agent Harness."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

import geopandas as gpd
import pyproj
from etp.describe import CommandDescriptor
from pyproj.exceptions import CRSError

from ecospheric_harness.artifact import Artifact
from ecospheric_harness.artifact_registry import ArtifactRecord, ArtifactRegistry
from ecospheric_harness.intents import PreflightResult, Resolution
from ecospheric_harness.security import check_ssrf as _check_ssrf_url
from ecospheric_harness.workspace import WorkspaceManager

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# File extensions recognised as vector formats (case-insensitive suffix).
_VECTOR_EXTENSIONS: set[str] = {
    ".geojson", ".gpkg", ".shp", ".parquet", ".fgb", ".kml",
}

# File extensions recognised as raster formats (case-insensitive suffix).
_RASTER_EXTENSIONS: set[str] = {".tif", ".tiff", ".vrt"}

# Param key stems that indicate a secondary/mask/by input for binary ops.
_BINARY_PARAM_STEMS: tuple[str, ...] = (
    "by", "mask", "overlay", "clip", "second", "compare", "input2", "secondary",
)

# Command name substrings that indicate distance / unit-sensitive operations.
_DISTANCE_COMMAND_STEMS: tuple[str, ...] = ("buffer", "distance", "near", "proximity")

# CRS param keys (for CRS validity check).
_CRS_PARAM_KEYS: tuple[str, ...] = ("output_crs", "target_crs", "crs", "srs")

# Resolution param keys.
_RESOLUTION_PARAM_KEYS: tuple[str, ...] = ("resolution", "pixel_size")


class PreflightChecker:
    """Validates that a command can safely execute on the available resources."""

    def __init__(
        self, registry: ArtifactRegistry, workspace: WorkspaceManager
    ) -> None:
        self._registry = registry
        self._workspace = workspace

    # ------------------------------------------------------------------
    # Pipeline entrypoint
    # ------------------------------------------------------------------

    def run_all_checks(
        self,
        resolved: Any,  # ResolvedCall
        input_artifact: Artifact | ArtifactRecord | None,
        params: dict[str, Any],
    ) -> list[PreflightResult]:
        """Run all applicable preflight checks and return the full result list.

        The caller scans for the first BLOCK resolution; MODEL_DISCRETION
        results are collected as non‑fatal warnings.

        Checks are ordered by risk: CRS agreement and geometry validity
        are cheap early signals, disk and SSRF are safety gates, and
        unit‑awareness / resolution sanity are advisory.
        """
        command: CommandDescriptor = resolved.command
        results: list[PreflightResult] = []

        # 1. CRS agreement (binary ops — two inputs in different CRS)
        if self._is_binary_op(command):
            results.append(self._check_crs_agreement(command, input_artifact, params))

        # 2. Extent intersection (binary ops — non‑overlapping inputs)
        if self._is_binary_op(command):
            results.append(self._check_extent_intersection(command, input_artifact, params))

        # 3. Unit awareness (distance ops on geographic CRS)
        results.append(self._check_unit_awareness(command, input_artifact))

        # 4. Extent containment (requested bounds exceed input)
        results.append(self._check_extent_containment(command, input_artifact, params))

        # 5. CRS validity (target/output CRS parseable)
        results.append(self._check_crs_validity(command, params))

        # 6. Planar CRS requirement
        results.append(self._check_planar_crs(command, input_artifact))

        # 7. Resolution sanity (within 3 orders of magnitude)
        results.append(self._check_resolution_sanity(command, input_artifact, params))

        # 8. Geometry validity (vector input corrupt geometries)
        results.append(self._check_geometry_validity(command, input_artifact))

        # 9. Disk space
        results.append(self.check_disk(input_artifact=input_artifact))

        # 10. SSRF
        results.append(self.check_ssrf(params))

        return results

    # ==================================================================
    # Public method suite (backward-compat with existing callers)
    # ==================================================================

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
            return PreflightResult(check="planar_crs")

        if artifact is None:
            return PreflightResult(check="planar_crs")

        if artifact.crs is None:
            return PreflightResult(
                check="planar_crs",
                resolution=Resolution.BLOCK,
                message=(
                    f"Command '{command.name}' requires planar CRS but input "
                    f"CRS is unknown. Reproject to a planar CRS "
                    f"(e.g. EPSG:3857) first."
                ),
            )

        try:
            crs = pyproj.CRS(artifact.crs)
        except CRSError:
            return PreflightResult(
                check="planar_crs",
                resolution=Resolution.BLOCK,
                message=(
                    f"Command '{command.name}' requires planar CRS but input "
                    f"CRS '{artifact.crs}' could not be parsed. "
                    f"Reproject to a planar CRS (e.g. EPSG:3857) first."
                ),
            )

        if crs.is_geographic:
            return PreflightResult(
                check="planar_crs",
                resolution=Resolution.BLOCK,
                message=(
                    f"Command '{command.name}' requires planar CRS but input "
                    f"is {artifact.crs} (geographic). Reproject to a planar "
                    f"CRS (e.g. EPSG:3857) first."
                ),
            )

        return PreflightResult(check="planar_crs")

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

        projected = self._registry.bytes_used + estimate
        if projected >= self._registry._disk_limit:
            current_mb = self._registry.bytes_used / (1024 * 1024)
            limit_mb = self._registry._disk_limit / (1024 * 1024)
            return PreflightResult(
                check="disk",
                resolution=Resolution.BLOCK,
                message=(
                    f"Insufficient disk space: need {estimate / (1024 * 1024):.1f} MB "
                    f"but only {limit_mb - current_mb:.1f} MB available "
                    f"(limit {limit_mb:.1f} MB)."
                ),
            )

        return PreflightResult(check="disk")

    # ------------------------------------------------------------------
    # SSRF checks
    # ------------------------------------------------------------------

    def check_ssrf(self, params: dict[str, Any]) -> PreflightResult:
        """Scan param values for URLs and check each against blocked ranges."""
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
                    check="ssrf",
                    resolution=Resolution.BLOCK,
                    message=f"URL in param '{key}' is blocked: {exc}",
                )
        return PreflightResult(check="ssrf")

    # ------------------------------------------------------------------
    # Disk availability (delegating to registry)
    # ------------------------------------------------------------------

    def check_disk_available(self, estimated_bytes: int = 0) -> bool:
        """Check if estimated_bytes fit within the disk limit."""
        return self._registry.bytes_used + estimated_bytes < self._registry._disk_limit

    # ==================================================================
    # New spatial checks (Slice 2.1)
    # ==================================================================

    # ------------------------------------------------------------------
    # 1. CRS agreement (binary ops)
    # ------------------------------------------------------------------

    def _check_crs_agreement(
        self,
        command: CommandDescriptor,
        input_artifact: Artifact | ArtifactRecord | None,
        params: dict[str, Any],
    ) -> PreflightResult:
        """For binary operations, verify primary and secondary inputs share a CRS."""
        secondary, sec_error = self._resolve_secondary_input(params)
        if sec_error is not None:
            # Could not read secondary — warn but don't block.
            return PreflightResult(
                check="crs_agreement",
                resolution=Resolution.MODEL_DISCRETION,
                message=sec_error,
            )

        if secondary is None:
            return PreflightResult(check="crs_agreement")

        primary_crs = input_artifact.crs if input_artifact is not None else None
        secondary_crs = secondary.get("crs")

        if primary_crs is None and secondary_crs is None:
            return PreflightResult(check="crs_agreement")

        if primary_crs is None or secondary_crs is None:
            # One has a CRS, the other doesn't — cannot compare meaningfully.
            return PreflightResult(check="crs_agreement")

        if primary_crs != secondary_crs:
            return PreflightResult(
                check="crs_agreement",
                resolution=Resolution.BLOCK,
                message=(
                    f"CRS mismatch for binary operation '{command.name}': "
                    f"primary input is {primary_crs} but secondary is "
                    f"{secondary_crs}. Reproject one input to match."
                ),
            )

        return PreflightResult(check="crs_agreement")

    # ------------------------------------------------------------------
    # 2. Extent intersection (binary ops)
    # ------------------------------------------------------------------

    def _check_extent_intersection(
        self,
        command: CommandDescriptor,
        input_artifact: Artifact | ArtifactRecord | None,
        params: dict[str, Any],
    ) -> PreflightResult:
        """For binary operations, verify primary and secondary extents overlap."""
        secondary, _sec_error = self._resolve_secondary_input(params)
        if secondary is None:
            return PreflightResult(check="extent_intersection")

        primary_bbox = input_artifact.bbox if input_artifact is not None else None
        secondary_bbox: list[float] | None = secondary.get("bbox")  # type: ignore[assignment]

        if primary_bbox is None or secondary_bbox is None:
            return PreflightResult(check="extent_intersection")

        # Shapely-style bbox: [minx, miny, maxx, maxy]
        # Intersection exists if the intervals overlap on both axes.
        x_overlap = not (primary_bbox[2] < secondary_bbox[0] or secondary_bbox[2] < primary_bbox[0])
        y_overlap = not (primary_bbox[3] < secondary_bbox[1] or secondary_bbox[3] < primary_bbox[1])

        if not (x_overlap and y_overlap):
            return PreflightResult(
                check="extent_intersection",
                resolution=Resolution.BLOCK,
                message=(
                    f"Extents do not overlap for binary operation "
                    f"'{command.name}'. Primary bbox {primary_bbox}, "
                    f"secondary bbox {secondary_bbox}."
                ),
            )

        return PreflightResult(check="extent_intersection")

    # ------------------------------------------------------------------
    # 3. Unit awareness
    # ------------------------------------------------------------------

    def _check_unit_awareness(
        self,
        command: CommandDescriptor,
        input_artifact: Artifact | ArtifactRecord | None,
    ) -> PreflightResult:
        """Warn when a distance-sensitive operation runs on a geographic CRS."""
        cmd_lower = command.name.lower()
        is_distance_op = any(stem in cmd_lower for stem in _DISTANCE_COMMAND_STEMS)
        if not is_distance_op:
            return PreflightResult(check="unit_awareness")

        if input_artifact is None or input_artifact.crs is None:
            return PreflightResult(check="unit_awareness")

        try:
            crs = pyproj.CRS(input_artifact.crs)
        except CRSError:
            return PreflightResult(check="unit_awareness")

        if crs.is_geographic:
            return PreflightResult(
                check="unit_awareness",
                resolution=Resolution.AUTO_FIX,
                message=(
                    f"Command '{command.name}' uses distance units but input "
                    f"CRS {input_artifact.crs} is geographic (degree-based). "
                    f"Consider reprojecting to a planar CRS like EPSG:3857 "
                    f"for meaningful distance calculations."
                ),
                diagnostics={
                    "suggested_crs": "EPSG:3857",
                    "input_crs": input_artifact.crs,
                },
            )

        return PreflightResult(check="unit_awareness")

    # ------------------------------------------------------------------
    # 4. Extent containment
    # ------------------------------------------------------------------

    def _check_extent_containment(
        self,
        command: CommandDescriptor,
        input_artifact: Artifact | ArtifactRecord | None,
        params: dict[str, Any],
    ) -> PreflightResult:
        """Verify requested bounds lie within the input extent."""
        requested_bbox: list[float] | None = None
        for key in ("bbox", "bounds"):
            val = params.get(key)
            if isinstance(val, (list, tuple)) and len(val) == 4:
                try:
                    requested_bbox = [float(v) for v in val]
                except (ValueError, TypeError):
                    requested_bbox = None
                break

        if requested_bbox is None:
            return PreflightResult(check="extent_containment")

        if input_artifact is None or input_artifact.bbox is None:
            return PreflightResult(check="extent_containment")

        input_bbox = input_artifact.bbox
        # requested bbox must be within input bbox:
        # requested[minx] >= input[minx], requested[maxx] <= input[maxx], etc.
        within = (
            requested_bbox[0] >= input_bbox[0]
            and requested_bbox[1] >= input_bbox[1]
            and requested_bbox[2] <= input_bbox[2]
            and requested_bbox[3] <= input_bbox[3]
        )

        if not within:
            return PreflightResult(
                check="extent_containment",
                resolution=Resolution.BLOCK,
                message=(
                    f"Requested bounds {requested_bbox} exceed input extent "
                    f"{input_bbox} for command '{command.name}'."
                ),
            )

        return PreflightResult(check="extent_containment")

    # ------------------------------------------------------------------
    # 5. CRS validity
    # ------------------------------------------------------------------

    def _check_crs_validity(
        self,
        command: CommandDescriptor,
        params: dict[str, Any],
    ) -> PreflightResult:
        """Verify any target CRS parameter is parseable."""
        for key in _CRS_PARAM_KEYS:
            value = params.get(key)
            if value is None:
                continue
            if not isinstance(value, str):
                continue
            try:
                pyproj.CRS(value)
            except CRSError:
                return PreflightResult(
                    check="crs_validity",
                    resolution=Resolution.BLOCK,
                    message=(
                        f"Invalid CRS '{value}' in param '{key}' for "
                        f"command '{command.name}': {value} could not "
                        f"be parsed."
                    ),
                )

        return PreflightResult(check="crs_validity")

    # ------------------------------------------------------------------
    # 6. Planar CRS (private — delegates to public)
    # ------------------------------------------------------------------

    def _check_planar_crs(
        self,
        command: CommandDescriptor,
        input_artifact: Artifact | ArtifactRecord | None,
    ) -> PreflightResult:
        """Check planar CRS requirement (delegates to public method)."""
        result = self.check_planar_crs(command, input_artifact)
        # Normalize in case check_planar_crs was mocked with old-style return.
        if not isinstance(result, PreflightResult):
            if hasattr(result, "ok") and not result.ok:
                return PreflightResult(
                    check="planar_crs",
                    resolution=Resolution.BLOCK,
                    message=getattr(result, "error", ""),
                )
            return PreflightResult(check="planar_crs")
        return result

    # ------------------------------------------------------------------
    # 7. Resolution sanity
    # ------------------------------------------------------------------

    def _check_resolution_sanity(
        self,
        command: CommandDescriptor,
        input_artifact: Artifact | ArtifactRecord | None,
        params: dict[str, Any],
    ) -> PreflightResult:
        """Warn when requested resolution differs from input by > 3 orders."""
        param_resolution: float | None = None
        for key in _RESOLUTION_PARAM_KEYS:
            val = params.get(key)
            if isinstance(val, (int, float)):
                param_resolution = float(val)
                break

        if param_resolution is None:
            return PreflightResult(check="resolution_sanity")

        if input_artifact is None:
            return PreflightResult(check="resolution_sanity")

        # Try to get input resolution from envelope data.
        envelope = getattr(input_artifact, "envelope", None)
        data: dict[str, Any] | None = envelope.get("data") if isinstance(envelope, dict) else None
        input_res: float | None = None
        if data is not None:
            for key in ("resolution", "pixel_size"):
                val = data.get(key)
                if isinstance(val, (int, float)):
                    input_res = float(val)
                    break

        if input_res is None:
            return PreflightResult(check="resolution_sanity")

        # Convert degrees → metres for geographic CRS.
        param_m = param_resolution
        input_m = input_res
        if input_artifact.crs is not None:
            try:
                crs = pyproj.CRS(input_artifact.crs)
                if crs.is_geographic:
                    param_m *= 111320.0
                    input_m *= 111320.0
            except CRSError:
                pass

        if input_m == 0:
            return PreflightResult(check="resolution_sanity")

        ratio = max(param_m, input_m) / min(param_m, input_m)
        if ratio > 1000:
            return PreflightResult(
                check="resolution_sanity",
                resolution=Resolution.MODEL_DISCRETION,
                message=(
                    f"Resolution mismatch: param={param_resolution}, "
                    f"input={input_res} (ratio {ratio:.0f}:1). "
                    f"Results may be poor quality or excessive."
                ),
                diagnostics={
                    "param_resolution": param_resolution,
                    "input_resolution": input_res,
                    "ratio": ratio,
                },
            )

        return PreflightResult(check="resolution_sanity")

    # ------------------------------------------------------------------
    # 8. Geometry validity (vector only)
    # ------------------------------------------------------------------

    def _check_geometry_validity(
        self,
        command: CommandDescriptor,
        input_artifact: Artifact | ArtifactRecord | None,
    ) -> PreflightResult:
        """Check vector input for corrupt / invalid geometries."""
        if input_artifact is None:
            return PreflightResult(check="geometry_validity")

        if input_artifact.data_type != "vector":
            return PreflightResult(check="geometry_validity")

        valid_formats: set[str] = {"geojson", "geoparquet", "gpkg", "shp", "fgb"}
        if input_artifact.format not in valid_formats:
            return PreflightResult(check="geometry_validity")

        path = input_artifact.path
        if not path.exists():
            return PreflightResult(check="geometry_validity")

        try:
            gdf = gpd.read_file(path, rows=100)
        except Exception:
            # Can't read the file — don't block, could be a driver issue.
            return PreflightResult(
                check="geometry_validity",
                resolution=Resolution.MODEL_DISCRETION,
                message=(
                    f"Could not read '{path}' for geometry validity check."
                ),
            )

        if len(gdf) == 0:
            return PreflightResult(check="geometry_validity")

        invalid_count = int((~gdf.geometry.is_valid).sum())
        invalid_pct = invalid_count / len(gdf)

        if invalid_pct > 0.10:
            return PreflightResult(
                check="geometry_validity",
                resolution=Resolution.MODEL_DISCRETION,
                message=(
                    f"{invalid_count}/{len(gdf)} geometries ({invalid_pct:.0%}) "
                    f"in '{input_artifact.artifact_id}' are invalid. "
                    f"Consider running a 'fix' or 'make_valid' operation first."
                ),
                diagnostics={
                    "invalid_count": invalid_count,
                    "total_count": len(gdf),
                    "invalid_pct": invalid_pct,
                },
            )

        return PreflightResult(check="geometry_validity")

    # ==================================================================
    # Helpers
    # ==================================================================

    def _is_binary_op(self, command: CommandDescriptor) -> bool:
        """Return True if *command* accepts a secondary/mask/clip input."""
        if command.parameters is None:
            return False
        for param in command.parameters:
            name_lower = param.name.lower() if param.name else ""
            if any(stem in name_lower for stem in _BINARY_PARAM_STEMS):
                return True
        return False

    def _resolve_secondary_input(
        self, params: dict[str, Any],
    ) -> tuple[dict[str, Any] | None, str | None]:
        """Resolve a secondary input (mask / overlay / clip source).

        Scans parameter values for keys indicating a secondary input, then
        looks up the value as an artifact ID or file path.

        Returns:
            A tuple of (metadata_dict, error_message).  When metadata is
            present it contains at least ``"crs"`` and ``"bbox"`` keys.
            ``error_message`` is only populated for non‑fatal issues
            (missing file, read failure) — the caller decides severity.
        """
        # 1. Find the param that carries a secondary input.
        secondary_value: Any = None
        for key, value in params.items():
            key_lower = key.lower()
            if any(stem in key_lower for stem in _BINARY_PARAM_STEMS):
                secondary_value = value
                break

        if secondary_value is None or not isinstance(secondary_value, str):
            return None, None

        sv = secondary_value.strip()
        if not sv:
            return None, None

        # 2. Try artifact registry lookup.
        artifact = self._registry.get(sv)
        if artifact is not None:
            return {
                "crs": artifact.crs,
                "bbox": artifact.bbox,
            }, None

        # 3. Try file-path read.
        path = Path(sv)
        suffix = path.suffix.lower()

        if suffix in _VECTOR_EXTENSIONS:
            return self._read_vector_header(path)

        if suffix in _RASTER_EXTENSIONS:
            return self._read_raster_header(path)

        # Not a recognised file extension — could be a URL or unsupported
        # format.  Don't block but warn.
        return None, None

    # ------------------------------------------------------------------
    # File header readers
    # ------------------------------------------------------------------

    def _read_vector_header(
        self, path: Path,
    ) -> tuple[dict[str, Any] | None, str | None]:
        """Read CRS and bbox from a vector file header."""
        if not path.exists():
            return None, f"Could not read secondary input: {path}"
        try:
            gdf = gpd.read_file(path, rows=100)
        except Exception as exc:
            return None, f"Could not read secondary input: {path} — {exc}"

        return {
            "crs": str(gdf.crs) if gdf.crs else None,
            "bbox": gdf.total_bounds.tolist() if len(gdf) > 0 else None,
        }, None

    def _read_raster_header(
        self, path: Path,
    ) -> tuple[dict[str, Any] | None, str | None]:
        """Read CRS and bbox from a raster file header via ``ese info``."""
        if not path.exists():
            return None, f"Could not read secondary input: {path}"
        try:
            proc = subprocess.run(
                ["ese", "info", "--input", str(path), "--json"],
                capture_output=True, text=True, timeout=10,
            )
        except (subprocess.TimeoutExpired, OSError) as exc:
            return None, f"Could not read secondary input: {path} — {exc}"

        if proc.returncode != 0:
            return None, f"Could not read secondary input: {path} — {proc.stderr.strip()[:200]}"

        try:
            data = json.loads(proc.stdout)
        except json.JSONDecodeError as exc:
            return None, f"Could not read secondary input: {path} — invalid JSON: {exc}"

        envelope: dict[str, Any] | None = data.get("envelope")
        if envelope is None:
            return None, f"Could not read secondary input: {path} — no envelope in ese output"

        crs = envelope.get("crs")
        bbox = envelope.get("bbox")
        return {"crs": crs, "bbox": bbox}, None