"""Tests for ecospheric_harness.preflight."""

from __future__ import annotations

from pathlib import Path

import pytest
from etp.describe import CommandDescriptor

from ecospheric_harness.artifact import Artifact, ArtifactManager
from ecospheric_harness.preflight import PreflightChecker


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def checker(tmp_workdir: Path) -> PreflightChecker:
    """Return a PreflightChecker backed by a 1 GB ArtifactManager."""
    am = ArtifactManager(tmp_workdir, disk_limit_bytes=1024 * 1024 * 1024)
    return PreflightChecker(am, tmp_workdir)


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
    """AC41 — planar CRS preflight checks."""

    def test_not_required_ok_regardless_of_crs(
        self,
        checker: PreflightChecker,
        non_planar_cmd: CommandDescriptor,
        tmp_path: Path,
    ) -> None:
        """requires_planar_crs=False → ok even with geographic CRS."""
        art = _make_artifact(tmp_path, crs="EPSG:4326")
        result = checker.check_planar_crs(non_planar_cmd, art)
        assert result.ok is True
        assert result.error == ""

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

    def test_required_geographic_input_fails(
        self,
        checker: PreflightChecker,
        planar_cmd: CommandDescriptor,
        tmp_path: Path,
    ) -> None:
        """Geographic input triggers AC41 actionable error."""
        art = _make_artifact(tmp_path, crs="EPSG:4326")
        result = checker.check_planar_crs(planar_cmd, art)
        assert result.ok is False
        assert "geographic" in result.error.lower()
        assert "EPSG:3857" in result.error
        assert "buffer" in result.error

    def test_required_no_artifact_ok(
        self,
        checker: PreflightChecker,
        planar_cmd: CommandDescriptor,
    ) -> None:
        """No artifact to check → ok."""
        result = checker.check_planar_crs(planar_cmd, None)
        assert result.ok is True

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


# ===================================================================
# Disk checks
# ===================================================================


class TestCheckDisk:
    """AC42 — disk space preflight checks."""

    def test_under_limit_ok(
        self,
        checker: PreflightChecker,
    ) -> None:
        """Estimate well under limit → ok."""
        result = checker.check_disk(estimated_bytes=1024)
        assert result.ok is True

    def test_over_limit_fails_with_mb_message(
        self,
        checker: PreflightChecker,
    ) -> None:
        """Estimate exceeding limit → error showing current/limit MB (AC42)."""
        # Manager has 1 GB limit; request 2 GB
        result = checker.check_disk(estimated_bytes=2 * 1024 * 1024 * 1024)
        assert result.ok is False
        assert "MB" in result.error
        assert "limit" in result.error.lower()

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

    def test_no_input_no_estimate_fallback_500mb(
        self,
        checker: PreflightChecker,
    ) -> None:
        """No estimate and no artifact → 500 MB fallback."""
        result = checker.check_disk()
        # 500 MB < 1 GB limit → ok
        assert result.ok is True

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


# ===================================================================
# ISSUE 3: disk check uses current_bytes not total_bytes
# ===================================================================


class TestDiskCheckUsesCurrentBytes:
    """Disk preflight should use current_bytes (excludes previous),
    not total_bytes (which includes both current and previous)."""

    def test_current_bytes_property(self, tmp_path: Path) -> None:
        """ArtifactManager.current_bytes returns only current artifact size."""
        mgr = ArtifactManager(tmp_path, disk_limit_bytes=10_000_000)

        # No artifacts
        assert mgr.current_bytes == 0

        # One artifact
        p1 = tmp_path / "file1.bin"
        p1.write_bytes(b"x" * 1000)
        a1 = Artifact(path=p1, envelope={}, format="geotiff", data_type="raster")
        mgr.store(a1)
        assert mgr.current_bytes == 1000
        assert mgr.total_bytes == 1000

        # Two artifacts: current=2000, previous=1000, total=3000
        p2 = tmp_path / "file2.bin"
        p2.write_bytes(b"x" * 2000)
        a2 = Artifact(path=p2, envelope={}, format="geotiff", data_type="raster")
        mgr.store(a2)
        assert mgr.current_bytes == 2000  # only current
        assert mgr.total_bytes == 3000  # current + previous
        assert mgr.current_bytes != mgr.total_bytes

    def test_disk_check_passes_when_current_plus_new_fits(
        self, tmp_path: Path,
    ) -> None:
        """Disk check should pass when current_bytes + new fits, even if
        total_bytes + new would NOT fit (because previous will be freed)."""
        # Set up a small disk limit: 5000 bytes
        mgr = ArtifactManager(tmp_path, disk_limit_bytes=5000)

        # Store two artifacts: current=2000, previous=2000, total=4000
        p1 = tmp_path / "file1.bin"
        p1.write_bytes(b"x" * 2000)
        a1 = Artifact(path=p1, envelope={}, format="geotiff", data_type="raster")
        mgr.store(a1)

        p2 = tmp_path / "file2.bin"
        p2.write_bytes(b"x" * 2000)
        a2 = Artifact(path=p2, envelope={}, format="geotiff", data_type="raster")
        mgr.store(a2)

        # total_bytes = 4000; disk_limit = 5000
        # With old logic: total_bytes + 1500 = 5500 > 5000 → FAIL
        # With new logic: current_bytes + 1500 = 3500 < 5000 → PASS
        # (previous=2000 will be freed on next store)
        checker = PreflightChecker(mgr, tmp_path)
        result = checker.check_disk(estimated_bytes=1500)
        assert result.ok is True, (
            "Disk check should pass: current(2000) + new(1500) = 3500 < 5000, "
            f"but got error: {result.error}"
        )

    def test_disk_check_fails_when_current_plus_new_really_exceeds(
        self, tmp_path: Path,
    ) -> None:
        """Disk check should fail when current_bytes + new truly exceeds limit."""
        mgr = ArtifactManager(tmp_path, disk_limit_bytes=5000)

        p1 = tmp_path / "file1.bin"
        p1.write_bytes(b"x" * 4000)
        a1 = Artifact(path=p1, envelope={}, format="geotiff", data_type="raster")
        mgr.store(a1)

        # current=4000, limit=5000, remaining=1000. Request 2000 → should fail.
        checker = PreflightChecker(mgr, tmp_path)
        result = checker.check_disk(estimated_bytes=2000)
        assert result.ok is False
