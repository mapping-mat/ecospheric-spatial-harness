"""Tests for WorkspaceManager extensions (Phase 2.4)."""
from __future__ import annotations

import os
import time
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from ecospheric_harness.workspace import WorkspaceManager


@pytest.fixture()
def ws_root(tmp_path: Path) -> Path:
    """Create a workspace root with multiple session dirs."""
    root = tmp_path / "esp_sessions"
    root.mkdir()
    return root


@pytest.fixture()
def active_ws(ws_root: Path) -> WorkspaceManager:
    """Create a WorkspaceManager with an active session."""
    return WorkspaceManager(ws_root, disk_limit_bytes=1024 * 1024 * 1024, session_id="active_session")


class TestCleanupOldSessions:
    def test_removes_old_sessions(self, ws_root: Path, active_ws: WorkspaceManager):
        # Create an old session with an old file
        old_dir = ws_root / "old_session"
        old_dir.mkdir()
        old_file = old_dir / "data.tif"
        old_file.write_bytes(b"\x00" * 100)
        # Set mtime to 10 days ago
        old_time = time.time() - (10 * 86400)
        os.utime(str(old_file), (old_time, old_time))

        removed = active_ws.cleanup_old_sessions(ttl_days=7.0)
        assert removed == 1
        assert not old_dir.exists()

    def test_keeps_recent_sessions(self, ws_root: Path, active_ws: WorkspaceManager):
        recent_dir = ws_root / "recent_session"
        recent_dir.mkdir()
        recent_file = recent_dir / "data.tif"
        recent_file.write_bytes(b"\x00" * 100)
        # File is fresh (current time)

        removed = active_ws.cleanup_old_sessions(ttl_days=7.0)
        assert removed == 0
        assert recent_dir.exists()

    def test_does_not_remove_current_session(self, ws_root: Path, active_ws: WorkspaceManager):
        # Even if current session is old, don't remove it
        current_dir = active_ws.session_dir
        old_file = current_dir / "data.tif"
        old_file.write_bytes(b"\x00" * 100)
        old_time = time.time() - (30 * 86400)
        os.utime(str(old_file), (old_time, old_time))

        removed = active_ws.cleanup_old_sessions(ttl_days=7.0)
        assert removed == 0
        assert current_dir.exists()

    def test_removes_multiple_old_sessions(self, ws_root: Path, active_ws: WorkspaceManager):
        for i in range(3):
            d = ws_root / f"old_{i}"
            d.mkdir()
            f = d / "data.bin"
            f.write_bytes(b"\x00" * 10)
            old_time = time.time() - (15 * 86400)
            os.utime(str(f), (old_time, old_time))

        removed = active_ws.cleanup_old_sessions(ttl_days=7.0)
        assert removed == 3

    def test_empty_session_dir_removed_by_dir_mtime(self, ws_root: Path, active_ws: WorkspaceManager):
        empty_dir = ws_root / "empty_session"
        empty_dir.mkdir()
        old_time = time.time() - (20 * 86400)
        os.utime(str(empty_dir), (old_time, old_time))

        removed = active_ws.cleanup_old_sessions(ttl_days=7.0)
        assert removed == 1
        assert not empty_dir.exists()


class TestCleanupCancelledStep:
    def test_removes_matching_files(self, tmp_path: Path):
        ws = WorkspaceManager(tmp_path, disk_limit_bytes=1024 * 1024)
        # Create files with step pattern
        (ws.session_dir / "step_003_aaa.tif").write_bytes(b"\x00")
        (ws.session_dir / "step_003_bbb.geojson").write_bytes(b"\x00")
        (ws.session_dir / "step_004_ccc.tif").write_bytes(b"\x00")
        (ws.session_dir / "other_file.txt").write_bytes(b"\x00")

        removed = ws.cleanup_cancelled_step(ws.session_dir, 3)
        assert removed == 2
        assert not (ws.session_dir / "step_003_aaa.tif").exists()
        assert not (ws.session_dir / "step_003_bbb.geojson").exists()
        assert (ws.session_dir / "step_004_ccc.tif").exists()
        assert (ws.session_dir / "other_file.txt").exists()

    def test_no_matching_files(self, tmp_path: Path):
        ws = WorkspaceManager(tmp_path, disk_limit_bytes=1024 * 1024)
        removed = ws.cleanup_cancelled_step(ws.session_dir, 999)
        assert removed == 0


class TestEstimateRss:
    def test_raster_estimate(self, tmp_path: Path):
        from ecospheric_harness.command_profile import CommandProfile
        ws = WorkspaceManager(tmp_path, disk_limit_bytes=1024 * 1024)

        p = tmp_path / "input.tif"
        p.write_bytes(b"\x00" * 1000)
        artifact = MagicMock(
            path=p,
            envelope={"data": {"data_type": "raster", "width": 1000, "height": 1000, "bands": 1, "dtype": "float32"}},
        )
        profile = CommandProfile("full_load", 3.0)

        estimate = ws.estimate_rss(artifact, profile)
        assert estimate == 1000 * 1000 * 1 * 4 * 3  # 12MB

    def test_missing_path_returns_estimate_from_envelope(self, tmp_path: Path):
        from ecospheric_harness.command_profile import CommandProfile
        ws = WorkspaceManager(tmp_path, disk_limit_bytes=1024 * 1024)

        artifact = MagicMock(
            path=tmp_path / "nonexistent.tif",
            envelope={"data": {"data_type": "raster", "width": 100, "height": 100, "bands": 1, "dtype": "float32"}},
        )
        profile = CommandProfile("full_load", 3.0)

        estimate = ws.estimate_rss(artifact, profile)
        assert estimate > 0  # Still gets estimate from envelope dimensions
