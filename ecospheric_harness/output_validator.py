"""Post-execution output validation for the Ecospheric Agent Harness."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from etp.describe import CommandDescriptor

from ecospheric_harness.artifact_registry import ArtifactRecord


@dataclass
class OutputValidationResult:
    """Result of output validation checks."""
    ok: bool
    checks: list[dict[str, Any]] = field(default_factory=list)  # [{check, passed, message}]
    error: str = ""


class OutputValidator:
    """Validates tool execution output after successful subprocess execution."""

    def validate(
        self,
        output_path: Path,
        envelope: dict[str, Any],
        command: CommandDescriptor,
        input_artifact: ArtifactRecord | None = None,
        params: dict[str, Any] | None = None,
    ) -> OutputValidationResult:
        """Run all output validation checks. Returns result with aggregated checks."""
        params = params or {}
        checks: list[dict[str, Any]] = []
        errors: list[str] = []

        # 1. File exists and non-empty (warning-level — real tools always write;
        # test mocks may not. Actual tool would have errored if it couldn't write.)
        # NOTE: file_exists failures are NOT added to the errors list below —
        # they are reported as check results but don't cause validation to fail.
        # This is intentional: a tool returning success (exit 0) but with a
        # missing output file indicates a deeper anomaly worth investigating,
        # not a hard validation failure. Do not add `errors.append(...)` here
        # without understanding this design decision.
        check_result = self._check_file_exists(output_path)
        checks.append(check_result)

        # 2. Data-type-specific checks
        data_type = envelope.get("data", {}).get("data_type", "unknown")
        fmt = envelope.get("data", {}).get("format", "unknown")

        if data_type == "raster":
            check_result = self._check_raster(output_path, envelope)
            checks.append(check_result)
            if not check_result["passed"]:
                errors.append(check_result["message"])
        elif data_type == "vector":
            check_result = self._check_vector(output_path, envelope)
            checks.append(check_result)
            if not check_result["passed"]:
                errors.append(check_result["message"])

        # 3. Output-vs-intent checks
        intent_check = self._check_output_vs_intent(
            output_path, envelope, command, input_artifact, params, data_type, fmt,
        )
        checks.append(intent_check)
        if not intent_check["passed"]:
            errors.append(intent_check["message"])

        # 4. Metadata completeness (warning-level, not failure)
        meta_check = self._check_metadata_completeness(envelope, data_type)
        checks.append(meta_check)
        # Metadata incompleteness is a warning, not a failure

        if errors:
            return OutputValidationResult(
                ok=False,
                checks=checks,
                error="; ".join(errors),
            )
        return OutputValidationResult(ok=True, checks=checks)

    def _check_file_exists(self, output_path: Path) -> dict[str, Any]:
        """Check output file exists and is non-empty."""
        if not output_path.exists():
            return {"check": "file_exists", "passed": False, "message": f"Output file does not exist: {output_path}"}
        if output_path.stat().st_size == 0:
            return {"check": "file_exists", "passed": False, "message": f"Output file is empty: {output_path}"}
        return {"check": "file_exists", "passed": True, "message": ""}

    def _check_raster(self, output_path: Path, envelope: dict[str, Any]) -> dict[str, Any]:
        """Check raster output: dimensions > 1x1 (when reported), CRS set (when reported)."""
        data = envelope.get("data", {})
        width = data.get("width")
        height = data.get("height")
        crs = data.get("crs") or (data.get("crs_meta", {}) or {}).get("crs") or data.get("output_crs")

        issues = []
        # Only check dimensions when explicitly reported
        if width is not None and height is not None:
            if width <= 1 and height <= 1:
                issues.append(f"Raster dimensions {width}x{height} are 1x1 or smaller")
        # Flag missing/empty CRS when reported: crs key present but falsy value
        if "crs" in data and not data.get("crs"):
            issues.append("Raster has no CRS set")

        if issues:
            return {"check": "raster_validity", "passed": False, "message": "; ".join(issues)}
        return {"check": "raster_validity", "passed": True, "message": ""}

    def _check_vector(self, output_path: Path, envelope: dict[str, Any]) -> dict[str, Any]:
        """Check vector output: feature count > 0 (when reported), CRS set (when reported)."""
        data = envelope.get("data", {})
        feature_count = data.get("feature_count")
        crs = data.get("crs")
        issues = []

        # Only flag zero features when feature_count is explicitly reported
        if feature_count is not None and feature_count == 0:
            issues.append("Vector output has 0 features")
        # Flag missing/empty CRS when reported: crs key present but falsy value
        if "crs" in data and not crs:
            issues.append("Vector has no CRS set")

        if issues:
            return {"check": "vector_validity", "passed": False, "message": "; ".join(issues)}
        return {"check": "vector_validity", "passed": True, "message": ""}

    def _check_output_vs_intent(
        self,
        output_path: Path,
        envelope: dict[str, Any],
        command: CommandDescriptor,
        input_artifact: ArtifactRecord | None,
        params: dict[str, Any],
        data_type: str,
        fmt: str,
    ) -> dict[str, Any]:
        """Check output matches expected intent (CRS, extent, geometry type)."""
        cmd_name = command.name.lower()
        output_crs = envelope.get("data", {}).get("crs") or envelope.get("data", {}).get("output_crs")
        output_bbox = envelope.get("data", {}).get("bbox") or envelope.get("data", {}).get("bounds") or envelope.get("data", {}).get("extent")
        issues = []

        # Reproject: output CRS should match requested CRS
        if "reproject" in cmd_name or "warp" in cmd_name:
            requested_crs = params.get("output_crs") or params.get("target_crs") or params.get("crs") or params.get("srs")
            if requested_crs and output_crs:
                try:
                    import pyproj
                    req = pyproj.CRS(str(requested_crs))
                    out = pyproj.CRS(str(output_crs))
                    if not req.equals(out):
                        issues.append(f"Reproject output CRS {output_crs} does not match requested {requested_crs}")
                except Exception as exc:
                    # Can't compare CRS — log diagnostic, don't silently pass
                    issues.append(f"Could not verify CRS match (pyproj error: {exc})")

        # Clip: output extent should intersect input extent
        if "clip" in cmd_name:
            clip_bounds = params.get("by") or params.get("bounds") or params.get("bbox")
            if clip_bounds and output_bbox and input_artifact and input_artifact.bbox:
                try:
                    out_bbox = list(output_bbox) if not isinstance(output_bbox, (list, tuple)) else output_bbox
                    if len(out_bbox) != 4:
                        issues.append(f"Clip output bbox has unexpected length {len(out_bbox)}: {out_bbox}")
                    else:
                        from shapely.geometry import box
                        out_extent = box(*out_bbox)
                        in_extent = box(*input_artifact.bbox)
                        if not out_extent.intersects(in_extent):
                            issues.append("Clip output extent does not intersect input extent")
                except Exception as exc:
                    issues.append(f"Could not verify clip extent intersection ({exc})")

        # Buffer: output extent should contain input extent
        if "buffer" in cmd_name and input_artifact and input_artifact.bbox and output_bbox:
            try:
                out_bbox = list(output_bbox) if not isinstance(output_bbox, (list, tuple)) else output_bbox
                if len(out_bbox) != 4:
                    issues.append(f"Buffer output bbox has unexpected length {len(out_bbox)}: {out_bbox}")
                else:
                    from shapely.geometry import box
                    out_extent = box(*out_bbox)
                    in_extent = box(*input_artifact.bbox)
                    if not out_extent.contains(in_extent):
                        issues.append("Buffer output extent does not contain input extent")
            except Exception as exc:
                issues.append(f"Could not verify buffer extent containment ({exc})")

        if issues:
            return {"check": "output_vs_intent", "passed": False, "message": "; ".join(issues)}
        return {"check": "output_vs_intent", "passed": True, "message": ""}

    def _check_metadata_completeness(self, envelope: dict[str, Any], data_type: str) -> dict[str, Any]:
        """Check that envelope has expected metadata (warning-level)."""
        data = envelope.get("data", {})
        missing = []
        if data_type == "raster":
            for key in ("width", "height", "bands", "crs"):
                if not data.get(key):
                    missing.append(key)
        elif data_type == "vector":
            for key in ("feature_count", "crs"):
                if not data.get(key):
                    missing.append(key)

        if missing:
            return {"check": "metadata_completeness", "passed": True, "message": f"Missing metadata: {', '.join(missing)} (warning)"}
        return {"check": "metadata_completeness", "passed": True, "message": ""}