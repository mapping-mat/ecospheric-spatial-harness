"""Tests for memory budget preflight check (Phase 2.3)."""
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest
from etp.describe import CommandDescriptor

from ecospheric_harness.artifact_registry import ArtifactRegistry
from ecospheric_harness.intents import PreflightResult, Resolution
from ecospheric_harness.preflight import PreflightChecker
from ecospheric_harness.workspace import WorkspaceManager


@pytest.fixture()
def checker_with_limit(tmp_workdir: Path) -> PreflightChecker:
    ws = WorkspaceManager(tmp_workdir, disk_limit_bytes=1024 * 1024 * 1024)
    registry = ArtifactRegistry(workspace=ws, disk_limit_bytes=1024 * 1024 * 1024)
    return PreflightChecker(registry, workspace=ws, memory_limit_mb=100)


@pytest.fixture()
def checker_no_limit(tmp_workdir: Path) -> PreflightChecker:
    ws = WorkspaceManager(tmp_workdir, disk_limit_bytes=1024 * 1024 * 1024)
    registry = ArtifactRegistry(workspace=ws, disk_limit_bytes=1024 * 1024 * 1024)
    return PreflightChecker(registry, workspace=ws)


def _make_raster_artifact(tmp_path, width=10000, height=10000, bands=1, dtype="float32"):
    p = tmp_path / "input.tif"
    p.write_bytes(b"\x00" * 1000)
    return MagicMock(
        data_type="raster",
        crs="EPSG:4326",
        path=p,
        envelope={"data": {"data_type": "raster", "width": width, "height": height, "bands": bands, "dtype": dtype}},
        bbox=[-180, -90, 180, 90],
        artifact_id="test_001",
    )


class TestCheckMemoryBudget:
    def test_no_limit_always_passes(self, checker_no_limit, tmp_path):
        art = _make_raster_artifact(tmp_path)
        cmd = CommandDescriptor(name="reproject", description="reproject", category="raster", requires_planar_crs=False)
        result = checker_no_limit._check_memory_budget(cmd, art, {})
        assert result.resolution == Resolution.PASS

    def test_no_artifact_passes(self, checker_with_limit):
        cmd = CommandDescriptor(name="reproject", description="reproject", category="raster", requires_planar_crs=False)
        result = checker_with_limit._check_memory_budget(cmd, None, {})
        assert result.resolution == Resolution.PASS

    def test_under_limit_passes(self, checker_with_limit, tmp_path):
        # 100×100 raster × 4 bytes × 1 band × 3.0 multiplier = 120KB — well under 100MB
        art = _make_raster_artifact(tmp_path, width=100, height=100)
        cmd = CommandDescriptor(name="reproject", description="reproject", category="raster", requires_planar_crs=False)
        result = checker_with_limit._check_memory_budget(cmd, art, {})
        assert result.resolution == Resolution.PASS

    def test_over_limit_blocks(self, checker_with_limit, tmp_path):
        # 10000×10000 × 4 × 1 × 3.0 = 1.2GB — over 100MB limit
        art = _make_raster_artifact(tmp_path, width=10000, height=10000)
        cmd = CommandDescriptor(name="reproject", description="reproject", category="raster", requires_planar_crs=False)
        result = checker_with_limit._check_memory_budget(cmd, art, {})
        assert result.resolution == Resolution.BLOCK
        assert "memory" in result.message.lower() or "rss" in result.message.lower()
        assert "estimate_mb" in result.diagnostics

    def test_streaming_command_lower_estimate(self, checker_with_limit, tmp_path):
        # slope is streaming with 1.5× multiplier
        # 10000×10000 × 4 × 1 × 1.5 = 600MB — still over 100MB
        art = _make_raster_artifact(tmp_path, width=10000, height=10000)
        cmd = CommandDescriptor(name="slope", description="slope", category="raster", requires_planar_crs=False)
        result = checker_with_limit._check_memory_budget(cmd, art, {})
        assert result.resolution == Resolution.BLOCK
        # But estimate should be lower than full_load
        assert result.diagnostics["multiplier"] == 1.5

    def test_diagnostics_contain_confidence(self, checker_with_limit, tmp_path):
        art = _make_raster_artifact(tmp_path, width=100, height=100, dtype="float32")
        cmd = CommandDescriptor(name="reproject", description="reproject", category="raster", requires_planar_crs=False)
        result = checker_with_limit._check_memory_budget(cmd, art, {})
        assert "confidence" in result.diagnostics
