"""Tests for the two-artifact sliding-window manager."""

from __future__ import annotations

from pathlib import Path


from ecospheric_harness.artifact import (
    FORMAT_ALIASES,
    Artifact,
    ArtifactManager,
    normalize_format,
)
from ecospheric_harness.workspace import WorkspaceManager


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_artifact(tmp_path: Path, name: str, body: bytes = b"x", **kw: object) -> Artifact:
    """Create a real temp file and return an Artifact pointing at it."""
    p = tmp_path / name
    p.write_bytes(body)
    defaults: dict[str, object] = {
        "path": p,
        "envelope": {},
        "format": "json",
        "data_type": "vector",
    }
    defaults.update(kw)
    return Artifact(**defaults)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Window-shift on success
# ---------------------------------------------------------------------------


class TestWindowShift:
    def test_store_sequence(self, tmp_path: Path) -> None:
        mgr = ArtifactManager(workspace=WorkspaceManager(tmp_path, disk_limit_bytes=1_000_000), disk_limit_bytes=1_000_000)
        a = _make_artifact(tmp_path, "a.bin", b"aaa")
        b = _make_artifact(tmp_path, "b.bin", b"bb")
        c = _make_artifact(tmp_path, "c.bin", b"c")

        # Step 1: store A
        mgr.store(a)
        assert mgr.current is a
        assert mgr.previous is None

        # Step 2: store B — A becomes previous
        mgr.store(b)
        assert mgr.current is b
        assert mgr.previous is a
        assert a.path.exists(), "A's file should still exist after second store"

        # Step 3: store C — A is deleted, B becomes previous
        mgr.store(c)
        assert mgr.current is c
        assert mgr.previous is b
        assert not a.path.exists(), "A's file should be deleted after third store"
        assert b.path.exists(), "B's file should still exist"


# ---------------------------------------------------------------------------
# Undo
# ---------------------------------------------------------------------------


class TestUndo:
    def test_undo_at_step_2(self, tmp_path: Path) -> None:
        mgr = ArtifactManager(workspace=WorkspaceManager(tmp_path, disk_limit_bytes=1_000_000), disk_limit_bytes=1_000_000)
        a = _make_artifact(tmp_path, "a.bin", b"a")
        b = _make_artifact(tmp_path, "b.bin", b"bb")

        mgr.store(a)
        mgr.store(b)

        result = mgr.undo()
        assert result is a
        assert mgr.current is a
        assert mgr.previous is None
        assert not b.path.exists(), "B's file should be deleted after undo"

    def test_undo_at_step_1(self, tmp_path: Path) -> None:
        mgr = ArtifactManager(workspace=WorkspaceManager(tmp_path, disk_limit_bytes=1_000_000), disk_limit_bytes=1_000_000)
        a = _make_artifact(tmp_path, "a.bin", b"a")

        mgr.store(a)
        result = mgr.undo()
        assert result is None
        assert mgr.current is None
        assert mgr.previous is None
        assert not a.path.exists(), "A's file should be deleted after undo"


# ---------------------------------------------------------------------------
# replace_current
# ---------------------------------------------------------------------------


class TestReplaceCurrent:
    def test_replace_current(self, tmp_path: Path) -> None:
        mgr = ArtifactManager(workspace=WorkspaceManager(tmp_path, disk_limit_bytes=1_000_000), disk_limit_bytes=1_000_000)
        a = _make_artifact(tmp_path, "a.bin", b"a")
        b = _make_artifact(tmp_path, "b.bin", b"bb")
        c = _make_artifact(tmp_path, "c.bin", b"ccc")

        mgr.store(a)
        mgr.store(b)

        mgr.replace_current(c)
        assert mgr.current is c
        assert mgr.previous is a, "Previous should stay intact"
        assert not b.path.exists(), "B's file should be deleted after replace_current"
        assert a.path.exists(), "A's file should still exist"


# ---------------------------------------------------------------------------
# Post-undo store
# ---------------------------------------------------------------------------


class TestPostUndoStore:
    def test_post_undo_store(self, tmp_path: Path) -> None:
        mgr = ArtifactManager(workspace=WorkspaceManager(tmp_path, disk_limit_bytes=1_000_000), disk_limit_bytes=1_000_000)
        a = _make_artifact(tmp_path, "a.bin", b"a")
        b = _make_artifact(tmp_path, "b.bin", b"bb")
        c = _make_artifact(tmp_path, "c.bin", b"ccc")

        mgr.store(a)
        mgr.store(b)
        mgr.undo()  # current=A, previous=None

        mgr.store(c)
        assert mgr.current is c
        assert mgr.previous is a


# ---------------------------------------------------------------------------
# can_undo
# ---------------------------------------------------------------------------


class TestCanUndo:
    def test_can_undo_sequence(self, tmp_path: Path) -> None:
        mgr = ArtifactManager(workspace=WorkspaceManager(tmp_path, disk_limit_bytes=1_000_000), disk_limit_bytes=1_000_000)
        a = _make_artifact(tmp_path, "a.bin", b"a")
        b = _make_artifact(tmp_path, "b.bin", b"bb")
        c = _make_artifact(tmp_path, "c.bin", b"ccc")

        assert not mgr.can_undo, "False initially"

        mgr.store(a)
        assert not mgr.can_undo, "False after 1 store (no previous)"

        mgr.store(b)
        assert mgr.can_undo, "True after 2 stores"

        mgr.undo()
        assert not mgr.can_undo, "False after undo (previous consumed)"

        mgr.store(c)
        assert mgr.can_undo, "True after post-undo store"


# ---------------------------------------------------------------------------
# Disk tracking
# ---------------------------------------------------------------------------


class TestDiskTracking:
    def test_total_bytes_through_sequence(self, tmp_path: Path) -> None:
        limit = 1_000_000
        mgr = ArtifactManager(workspace=WorkspaceManager(tmp_path, disk_limit_bytes=limit), disk_limit_bytes=limit)

        a = _make_artifact(tmp_path, "a.bin", b"a" * 100)  # 100 bytes
        b = _make_artifact(tmp_path, "b.bin", b"b" * 200)  # 200 bytes
        c = _make_artifact(tmp_path, "c.bin", b"c" * 300)  # 300 bytes
        d = _make_artifact(tmp_path, "d.bin", b"d" * 50)   # 50 bytes

        assert mgr._total_bytes == 0

        mgr.store(a)
        assert mgr._total_bytes == 100

        mgr.store(b)
        assert mgr._total_bytes == 300  # a(100) + b(200)

        mgr.store(c)
        assert mgr._total_bytes == 500  # a evicted → b(200) + c(300)

        mgr.undo()
        assert mgr._total_bytes == 200  # c evicted → b(200)

        mgr.store(d)
        assert mgr._total_bytes == 250  # b(200) + d(50)


# ---------------------------------------------------------------------------
# disk_available
# ---------------------------------------------------------------------------


class TestDiskAvailable:
    def test_under_and_over_limit(self, tmp_path: Path) -> None:
        mgr = ArtifactManager(workspace=WorkspaceManager(tmp_path, disk_limit_bytes=500), disk_limit_bytes=500)

        a = _make_artifact(tmp_path, "a.bin", b"a" * 200)
        mgr.store(a)
        assert mgr.disk_available(100), "Should fit: 200 + 100 < 500"
        assert not mgr.disk_available(400), "Should not fit: 200 + 400 >= 500"


# ---------------------------------------------------------------------------
# Format normalization
# ---------------------------------------------------------------------------


class TestNormalizeFormat:
    def test_all_aliases(self) -> None:
        for alias, canonical in FORMAT_ALIASES.items():
            assert normalize_format(alias) == canonical

    def test_uppercase(self) -> None:
        assert normalize_format("TIF") == "geotiff"
        assert normalize_format("GeoJSON") == "geojson"

    def test_unknown_passthrough(self) -> None:
        assert normalize_format("weirdfmt") == "weirdfmt"
        assert normalize_format("WEIRDFMT") == "weirdfmt"


# ---------------------------------------------------------------------------
# free()
# ---------------------------------------------------------------------------


class TestFree:
    def test_free(self, tmp_path: Path) -> None:
        mgr = ArtifactManager(workspace=WorkspaceManager(tmp_path, disk_limit_bytes=1_000_000), disk_limit_bytes=1_000_000)
        a = _make_artifact(tmp_path, "a.bin", b"a")
        b = _make_artifact(tmp_path, "b.bin", b"bb")

        mgr.store(a)
        mgr.store(b)

        assert a.path.exists()
        assert b.path.exists()

        mgr.free()

        assert not a.path.exists()
        assert not b.path.exists()
        assert mgr.current is None
        assert mgr.previous is None
        assert mgr._total_bytes == 0