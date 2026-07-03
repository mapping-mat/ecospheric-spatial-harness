"""Tests for ecospheric_harness.preflight.

Tests the three migrated checks (planar_crs, disk, ssrf) using the new
PreflightResult API with Resolution enum. Also verifies backward-compat
.ok and .error properties still work.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from etp.describe import CommandDescriptor

from ecospheric_harness.artifact import Artifact
from ecospheric_harness.artifact_registry import ArtifactRegistry
from ecospheric_harness.intents import PreflightResult, Resolution
from ecospheric_harness.preflight import PreflightChecker
from ecospheric_harness.workspace import WorkspaceManager


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def checker(tmp_path: Path) -> PreflightChecker:
    """Return a PreflightChecker backed by a 1 GB ArtifactRegistry."""
    ws = WorkspaceManager(tmp_path, disk_limit_bytes=1024 * 1024 * 1024)
    registry = ArtifactRegistry(workspace=ws, disk_limit_bytes=1024 * 1024 * 1024)
    return PreflightChecker(registry, workspace=ws)


@pytest.fixture()
def planar_cmd() -> CommandDescriptor:
    return CommandDescriptor(
        name="buffer",
        description="Buffer geometries",
        category="vector",
        requires_planar_crs=True,
    )


@pytest.fixture()
def non_planar_cmd() -> CommandDescriptor:
    return CommandDescriptor(
        name="search",
        description="Search for datasets",
        category="vector",
        requires_planar_crs=False,
    )


def _make_artifact(
    tmp_path: Path,
    *,
    crs: str | None = "EPSG:3857",
    size_bytes: int = 1024,
) -> Artifact:
    """Create a real file on disk and wrap it in an Artifact."""
    p = tmp_path / "input.bin"
    p.write_bytes(b"\x00" * size_bytes)
    return Artifact(
        path=p,
        envelope={},
        format="geotiff",
        data_type="raster",
        crs=crs,
    )


# ===================================================================
# CRS checks
# ===================================================================


class TestCheckPlanarCrs:
    """AC41 — planar CRS preflight checks.

    Tests both the legacy .ok/.error API and the new .resolution/.check API.
    """

    def test_not_required_ok_regardless_of_crs(
        self,
        checker: PreflightChecker,
        non_planar_cmd: CommandDescriptor,
        tmp_path: Path,
    ) -> None:
        """requires_planar_crs=False → ok even with geographic CRS."""
        art = _make_artifact(tmp_path, crs="EPSG:4326")
        result = checker.check_planar_crs(non_planar_cmd, art)
        # Legacy API
        assert result.ok is True
        assert result.error == ""
        # New API
        assert result.resolution == Resolution.PASS
        assert result.check == "planar_crs"

    def test_required_planar_input_ok(
        self,
        checker: PreflightChecker,
        planar_cmd: CommandDescriptor,
        tmp_path: Path,
    ) -> None:
        """Planar input (EPSG:3857) satisfies planar requirement."""
        art = _make_artifact(tmp_path, crs="EPSG:3857")
        result = checker.check_planar_crs(planar_cmd, art)
        assert result.ok is True
        assert result.resolution == Resolution.PASS

    def test_required_geographic_input_fails(
        self,
        checker: PreflightChecker,
        planar_cmd: CommandDescriptor,
        tmp_path: Path,
    ) -> None:
        """Geographic input triggers AC41 actionable error."""
        art = _make_artifact(tmp_path, crs="EPSG:4326")
        result = checker.check_planar_crs(planar_cmd, art)
        # Legacy API
        assert result.ok is False
        assert "geographic" in result.error.lower()
        assert "EPSG:3857" in result.error
        assert "buffer" in result.error
        # New API
        assert result.resolution == Resolution.BLOCK
        assert result.check == "planar_crs"
        assert "geographic" in result.message.lower()

    def test_required_no_artifact_ok(
        self,
        checker: PreflightChecker,
        planar_cmd: CommandDescriptor,
    ) -> None:
        """No artifact to check → ok."""
        result = checker.check_planar_crs(planar_cmd, None)
        assert result.ok is True
        assert result.resolution == Resolution.PASS

    def test_required_unknown_crs_fails(
        self,
        checker: PreflightChecker,
        planar_cmd: CommandDescriptor,
        tmp_path: Path,
    ) -> None:
        """artifact.crs=None → error about unknown CRS."""
        art = _make_artifact(tmp_path, crs=None)
        result = checker.check_planar_crs(planar_cmd, art)
        assert result.ok is False
        assert "unknown" in result.error.lower()
        assert result.resolution == Resolution.BLOCK

    def test_required_unparseable_crs_fails(
        self,
        checker: PreflightChecker,
        planar_cmd: CommandDescriptor,
        tmp_path: Path,
    ) -> None:
        """artifact.crs='INVALID' → error (CRSError)."""
        art = _make_artifact(tmp_path, crs="INVALID")
        result = checker.check_planar_crs(planar_cmd, art)
        assert result.ok is False
        assert "could not be parsed" in result.error
        assert result.resolution == Resolution.BLOCK


# ===================================================================
# Disk checks
# ===================================================================


class TestCheckDisk:
    """AC42 — disk space preflight checks.

    Tests both legacy and new Resolution API.
    """

    def test_under_limit_ok(
        self,
        checker: PreflightChecker,
    ) -> None:
        """Estimate well under limit → ok."""
        result = checker.check_disk(estimated_bytes=1024)
        assert result.ok is True
        assert result.resolution == Resolution.PASS

    def test_over_limit_fails_with_mb_message(
        self,
        checker: PreflightChecker,
    ) -> None:
        """Estimate exceeding limit → error showing current/limit MB (AC42)."""
        # Manager has 1 GB limit; request 2 GB
        result = checker.check_disk(estimated_bytes=2 * 1024 * 1024 * 1024)
        # Legacy API
        assert result.ok is False
        assert "MB" in result.error
        assert "limit" in result.error.lower()
        # New API
        assert result.resolution == Resolution.BLOCK
        assert result.check == "disk"

    def test_with_input_artifact_estimates_expansion(
        self,
        checker: PreflightChecker,
        tmp_path: Path,
    ) -> None:
        """Disk estimate = file_size × expansion_factor."""
        art = _make_artifact(tmp_path, size_bytes=100_000)
        # expansion_factor=30 → estimate = 3 MB, within 1 GB limit
        result = checker.check_disk(
            input_artifact=art,
            expansion_factor=30.0,
        )
        assert result.ok is True
        assert result.resolution == Resolution.PASS

    def test_no_input_no_estimate_fallback_500mb(
        self,
        checker: PreflightChecker,
    ) -> None:
        """No estimate and no artifact → 500 MB fallback."""
        result = checker.check_disk()
        # 500 MB < 1 GB limit → ok
        assert result.ok is True
        assert result.resolution == Resolution.PASS

    def test_expansion_exceeds_limit(
        self,
        checker: PreflightChecker,
        tmp_path: Path,
    ) -> None:
        """Large expansion factor can push over the limit."""
        art = _make_artifact(tmp_path, size_bytes=600_000_000)
        # 600 MB × 3 = 1.8 GB > 1 GB limit
        result = checker.check_disk(
            input_artifact=art,
            expansion_factor=3.0,
        )
        assert result.ok is False
        assert "MB" in result.error
        assert result.resolution == Resolution.BLOCK


# ===================================================================
# SSRF checks
# ===================================================================


class TestCheckSsrf:
    """SSRF preflight check — migrated check.

    Tests both legacy and new Resolution API.
    """

    def test_safe_url_passes(
        self,
        checker: PreflightChecker,
    ) -> None:
        """Safe external URL → PASS."""
        result = checker.check_ssrf({"url": "https://example.com/data.tif"})
        assert result.ok is True
        assert result.resolution == Resolution.PASS
        assert result.check == "ssrf"

    def test_localhost_blocked(
        self,
        checker: PreflightChecker,
    ) -> None:
        """localhost URL → BLOCK."""
        result = checker.check_ssrf({"url": "http://127.0.0.1/metadata"})
        assert result.ok is False
        assert result.resolution == Resolution.BLOCK
        assert "blocked" in result.message.lower() or "ssrf" in result.message.lower()

    def test_metadata_ip_blocked(
        self,
        checker: PreflightChecker,
    ) -> None:
        """AWS metadata IP → BLOCK."""
        result = checker.check_ssrf({"url": "http://169.254.169.254/latest/meta-data/"})
        assert result.ok is False
        assert result.resolution == Resolution.BLOCK

    def test_non_url_params_pass(
        self,
        checker: PreflightChecker,
    ) -> None:
        """Non-URL string params → PASS."""
        result = checker.check_ssrf({"distance": "500", "format": "geotiff"})
        assert result.ok is True
        assert result.resolution == Resolution.PASS

    def test_skip_input_target_key(
        self,
        checker: PreflightChecker,
    ) -> None:
        """_input_target key is skipped in SSRF check."""
        result = checker.check_ssrf({
            "_input_target": "http://127.0.0.1/internal",
            "distance": "500",
        })
        assert result.ok is True
        assert result.resolution == Resolution.PASS
