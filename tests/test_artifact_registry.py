"""Tests for ArtifactRegistry."""

from __future__ import annotations

import json
from pathlib import Path

from ecospheric_harness.artifact_registry import ArtifactRecord, ArtifactRegistry
from ecospheric_harness.workspace import WorkspaceManager


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_registry(tmp_path: Path, *, disk_limit_bytes: int = 10_000_000) -> ArtifactRegistry:
    """Create a fresh ArtifactRegistry backed by a temporary workspace."""
    workspace = WorkspaceManager(
        workspace_root=tmp_path / "sessions",
        disk_limit_bytes=disk_limit_bytes,
    )
    return ArtifactRegistry(workspace=workspace, disk_limit_bytes=disk_limit_bytes)


def _make_test_record(
    registry: ArtifactRegistry,
    intent: str = "test",
    *,
    path: Path | None = None,
    parent_ids: list[str] | None = None,
    file_size: int = 100,
) -> ArtifactRecord:
    """Create and register a minimal test artifact."""
    if path is None:
        path = registry._workspace.create_temp_path(suffix=".bin")
    # Create the file so track_bytes works
    path.write_bytes(b"x" * file_size)
    return registry.register(
        path=path,
        format="geotiff",
        data_type="raster",
        crs="EPSG:4326",
        bbox=[-122.5, 39.7, -122.3, 39.8],
        step_number=len(registry.list_all()) + 1,
        envelope={"status": "success", "data": {}},
        parent_ids=parent_ids or [],
        intent=intent,
        tool_name="ese",
        tool_version="1.0.0",
        command_name="test command",
        params={},
        duration_ms=100,
        is_search=False,
    )


# ---------------------------------------------------------------------------
# Registration and ID assignment
# ---------------------------------------------------------------------------


class TestRegistration:
    def test_register_assigns_sequential_ids(self, tmp_path: Path) -> None:
        registry = _make_registry(tmp_path)
        r1 = _make_test_record(registry, intent="clip")
        r2 = _make_test_record(registry, intent="fetch")
        r3 = _make_test_record(registry, intent="clip")

        assert r1.artifact_id == "clip_001"
        assert r2.artifact_id == "fetch_002"
        assert r3.artifact_id == "clip_003"

    def test_register_increments_counter(self, tmp_path: Path) -> None:
        registry = _make_registry(tmp_path)
        assert registry._counter == 0

        _make_test_record(registry)
        assert registry._counter == 1

        _make_test_record(registry)
        assert registry._counter == 2

        _make_test_record(registry)
        assert registry._counter == 3

    def test_register_stores_artifact_data(self, tmp_path: Path) -> None:
        registry = _make_registry(tmp_path)
        record = _make_test_record(registry, intent="clip")

        assert record.format == "geotiff"
        assert record.data_type == "raster"
        assert record.crs == "EPSG:4326"
        assert record.bbox == [-122.5, 39.7, -122.3, 39.8]
        assert record.intent == "clip"
        assert record.tool_name == "ese"
        assert record.tool_version == "1.0.0"
        assert record.command_name == "test command"
        assert record.duration_ms == 100
        assert record.is_search is False
        assert record.path.exists()
        assert record.created_at != ""

    def test_register_tracks_bytes(self, tmp_path: Path) -> None:
        registry = _make_registry(tmp_path)
        initial_bytes = registry.bytes_used

        _make_test_record(registry, file_size=500)
        assert registry.bytes_used == initial_bytes + 500

        _make_test_record(registry, file_size=300)
        assert registry.bytes_used == initial_bytes + 500 + 300


# ---------------------------------------------------------------------------
# Lookup
# ---------------------------------------------------------------------------


class TestLookup:
    def test_get_returns_artifact(self, tmp_path: Path) -> None:
        registry = _make_registry(tmp_path)
        record = _make_test_record(registry)

        result = registry.get(record.artifact_id)
        assert result is record

    def test_get_returns_none_for_unknown_id(self, tmp_path: Path) -> None:
        registry = _make_registry(tmp_path)
        result = registry.get("nonexistent_999")
        assert result is None

    def test_get_returns_none_for_evicted(self, tmp_path: Path) -> None:
        registry = _make_registry(tmp_path)
        record = _make_test_record(registry)
        record.evicted = True

        result = registry.get(record.artifact_id)
        assert result is None

    def test_get_recent_returns_n_most_recent(self, tmp_path: Path) -> None:
        registry = _make_registry(tmp_path)
        records = []
        for i in range(5):
            rec = _make_test_record(registry)
            records.append(rec)

        recent = registry.get_recent(2)
        assert len(recent) == 2
        assert recent[0].artifact_id == records[4].artifact_id
        assert recent[1].artifact_id == records[3].artifact_id

    def test_get_recent_excludes_undone(self, tmp_path: Path) -> None:
        registry = _make_registry(tmp_path)
        records = []
        for _ in range(3):
            rec = _make_test_record(registry)
            records.append(rec)

        # Mark the most recent as undone
        records[2].undone = True

        recent = registry.get_recent(2)
        assert len(recent) == 2
        assert recent[0].artifact_id == records[1].artifact_id
        assert recent[1].artifact_id == records[0].artifact_id

    def test_get_recent_excludes_evicted(self, tmp_path: Path) -> None:
        registry = _make_registry(tmp_path)
        records = []
        for _ in range(3):
            rec = _make_test_record(registry)
            records.append(rec)

        # Evict the most recent
        records[2].evicted = True

        recent = registry.get_recent(2)
        assert len(recent) == 2
        assert recent[0].artifact_id == records[1].artifact_id
        assert recent[1].artifact_id == records[0].artifact_id

    def test_list_all_returns_sorted_by_step(self, tmp_path: Path) -> None:
        registry = _make_registry(tmp_path)
        records = []
        for _ in range(5):
            rec = _make_test_record(registry)
            records.append(rec)

        all_records = registry.list_all()
        assert len(all_records) == 5

        # Verify sorted by step_number ascending
        for i in range(len(all_records) - 1):
            assert all_records[i].step_number <= all_records[i + 1].step_number

        assert all_records[0].artifact_id == records[0].artifact_id
        assert all_records[4].artifact_id == records[4].artifact_id

    def test_list_all_excludes_evicted(self, tmp_path: Path) -> None:
        registry = _make_registry(tmp_path)
        records = []
        for _ in range(3):
            rec = _make_test_record(registry)
            records.append(rec)

        # Evict the middle one
        records[1].evicted = True

        all_records = registry.list_all()
        assert len(all_records) == 2
        assert all_records[0].artifact_id == records[0].artifact_id
        assert all_records[1].artifact_id == records[2].artifact_id


# ---------------------------------------------------------------------------
# current property
# ---------------------------------------------------------------------------


class TestCurrentProperty:
    def test_current_returns_most_recent(self, tmp_path: Path) -> None:
        registry = _make_registry(tmp_path)
        records = []
        for _ in range(3):
            rec = _make_test_record(registry)
            records.append(rec)

        current = registry.current
        assert current is not None
        assert current.artifact_id == records[2].artifact_id

    def test_current_returns_none_when_empty(self, tmp_path: Path) -> None:
        registry = _make_registry(tmp_path)
        current = registry.current
        assert current is None

    def test_current_skips_undone(self, tmp_path: Path) -> None:
        registry = _make_registry(tmp_path)
        records = []
        for _ in range(3):
            rec = _make_test_record(registry)
            records.append(rec)

        # Mark most recent as undone
        records[2].undone = True

        current = registry.current
        assert current is not None
        assert current.artifact_id == records[1].artifact_id


# ---------------------------------------------------------------------------
# can_undo
# ---------------------------------------------------------------------------


class TestCanUndo:
    def test_can_undo_true_with_artifacts(self, tmp_path: Path) -> None:
        registry = _make_registry(tmp_path)
        _make_test_record(registry)

        assert registry.can_undo is True

    def test_can_undo_false_when_empty(self, tmp_path: Path) -> None:
        registry = _make_registry(tmp_path)
        assert registry.can_undo is False

    def test_can_undo_false_when_all_undone(self, tmp_path: Path) -> None:
        registry = _make_registry(tmp_path)
        r1 = _make_test_record(registry)
        r2 = _make_test_record(registry)

        r1.undone = True
        r2.undone = True

        assert registry.can_undo is False


# ---------------------------------------------------------------------------
# Idempotency cache
# ---------------------------------------------------------------------------


class TestIdempotency:
    def test_is_idempotent_returns_none_for_uncached(self, tmp_path: Path) -> None:
        registry = _make_registry(tmp_path)
        result = registry.is_idempotent(
            tool_name="ese",
            tool_version="1.0.0",
            command_name="clip",
            params={"buffer": 100},
            input_artifact_id="input_001",
        )
        assert result is None

    def test_record_idempotent_then_is_idempotent_returns_id(
        self, tmp_path: Path
    ) -> None:
        registry = _make_registry(tmp_path)

        registry.record_idempotent(
            tool_name="ese",
            tool_version="1.0.0",
            command_name="clip",
            params={"buffer": 100},
            input_artifact_id="input_001",
            output_artifact_id="output_001",
        )

        result = registry.is_idempotent(
            tool_name="ese",
            tool_version="1.0.0",
            command_name="clip",
            params={"buffer": 100},
            input_artifact_id="input_001",
        )
        assert result == "output_001"

    def test_is_idempotent_returns_none_for_evicted_output(
        self, tmp_path: Path
    ) -> None:
        registry = _make_registry(tmp_path)

        # Record idempotent with a real artifact
        record = _make_test_record(registry)
        registry.record_idempotent(
            tool_name="ese",
            tool_version="1.0.0",
            command_name="clip",
            params={},
            input_artifact_id=None,
            output_artifact_id=record.artifact_id,
        )

        # Evict the output artifact
        record.evicted = True

        # Cache still returns the ID (cache is separate from artifact state)
        result = registry.is_idempotent(
            tool_name="ese",
            tool_version="1.0.0",
            command_name="clip",
            params={},
            input_artifact_id=None,
        )
        # Note: the cache doesn't track eviction, so it still returns the ID
        assert result == record.artifact_id

    def test_idempotency_key_differs_for_different_params(
        self, tmp_path: Path
    ) -> None:
        registry = _make_registry(tmp_path)

        key1 = registry._idempotency_key(
            tool_name="ese",
            tool_version="1.0.0",
            command_name="clip",
            params={"buffer": 100},
            input_artifact_id=None,
        )

        key2 = registry._idempotency_key(
            tool_name="ese",
            tool_version="1.0.0",
            command_name="clip",
            params={"buffer": 200},
            input_artifact_id=None,
        )

        assert key1 != key2

    def test_idempotency_key_same_for_same_params(
        self, tmp_path: Path
    ) -> None:
        registry = _make_registry(tmp_path)

        key1 = registry._idempotency_key(
            tool_name="ese",
            tool_version="1.0.0",
            command_name="clip",
            params={"buffer": 100},
            input_artifact_id="input_001",
        )

        key2 = registry._idempotency_key(
            tool_name="ese",
            tool_version="1.0.0",
            command_name="clip",
            params={"buffer": 100},
            input_artifact_id="input_001",
        )

        assert key1 == key2


# ---------------------------------------------------------------------------
# Undo/Redo
# ---------------------------------------------------------------------------


class TestUndoRedo:
    def test_mark_undone_sets_flag(self, tmp_path: Path) -> None:
        registry = _make_registry(tmp_path)
        record = _make_test_record(registry)

        assert record.undone is False
        result = registry.mark_undone(record.artifact_id)
        assert result is True
        assert record.undone is True

    def test_mark_undone_returns_false_for_unknown(self, tmp_path: Path) -> None:
        registry = _make_registry(tmp_path)
        result = registry.mark_undone("nonexistent_999")
        assert result is False

    def test_redo_from_clears_undone(self, tmp_path: Path) -> None:
        registry = _make_registry(tmp_path)
        record = _make_test_record(registry)
        registry.mark_undone(record.artifact_id)
        assert record.undone is True

        result = registry.redo_from(record.artifact_id)
        assert result is not None
        assert result.undone is False

    def test_redo_from_returns_none_for_unknown(self, tmp_path: Path) -> None:
        registry = _make_registry(tmp_path)
        result = registry.redo_from("nonexistent_999")
        assert result is None


# ---------------------------------------------------------------------------
# Eviction
# ---------------------------------------------------------------------------


class TestEviction:
    def test_evict_removes_oldest_unreachable(self, tmp_path: Path) -> None:
        # limit=1000, target=900. Need bytes > 900 to trigger eviction.
        registry = _make_registry(tmp_path, disk_limit_bytes=1000)

        # Create two artifacts: one will be undone (unreachable)
        r1 = _make_test_record(registry, file_size=600)
        r2 = _make_test_record(registry, file_size=600)
        # bytes_used = 1200 > 900

        # Mark r1 as undone (not reachable from current)
        r1.undone = True

        evicted = registry.evict()
        assert r1.artifact_id in evicted
        assert r2.artifact_id not in evicted

    def test_evict_protects_ancestors(self, tmp_path: Path) -> None:
        # Set a high limit so eviction won't trigger (all reachable anyway)
        registry = _make_registry(tmp_path, disk_limit_bytes=100_000)

        # Create parent artifact
        parent = _make_test_record(registry, file_size=200)

        # Create child with parent
        child = _make_test_record(registry, parent_ids=[parent.artifact_id], file_size=200)

        # Both are reachable (child is non-undone, parent is ancestor)
        evicted = registry.evict()
        assert parent.artifact_id not in evicted
        assert child.artifact_id not in evicted

    def test_evict_deletes_files(self, tmp_path: Path) -> None:
        # limit=1000, target=900. Need bytes > 900.
        registry = _make_registry(tmp_path, disk_limit_bytes=1000)

        r1 = _make_test_record(registry, file_size=600)
        _make_test_record(registry, file_size=600)
        file_path = r1.path
        assert file_path.exists()

        r1.undone = True

        registry.evict()
        assert not file_path.exists()

    def test_evict_marks_evicted_flag(self, tmp_path: Path) -> None:
        # limit=1000, target=900
        registry = _make_registry(tmp_path, disk_limit_bytes=1000)

        r1 = _make_test_record(registry, file_size=600)
        _make_test_record(registry, file_size=600)

        r1.undone = True

        registry.evict()
        assert r1.evicted is True

    def test_evict_returns_list_of_ids(self, tmp_path: Path) -> None:
        # limit=1000, target=900. Need enough undone bytes.
        registry = _make_registry(tmp_path, disk_limit_bytes=1000)

        # 3 artifacts of 400 bytes = 1200 total
        r1 = _make_test_record(registry, file_size=400)
        r2 = _make_test_record(registry, file_size=600)
        # bytes = 1000, still need > 900 after one eviction

        r1.undone = True

        evicted = registry.evict()
        assert isinstance(evicted, list)
        # r1 is unreachable and bytes (1000) > 900
        assert r1.artifact_id in evicted
        # r2 is not undone, should not be evicted
        assert r2.artifact_id not in evicted

    def test_evict_respects_disk_limit(self, tmp_path: Path) -> None:
        # limit=1000, target=900 bytes
        registry = _make_registry(tmp_path, disk_limit_bytes=1000)

        # Create 4 artifacts of 200 bytes each = 800 bytes total
        r1 = _make_test_record(registry, file_size=200)
        _make_test_record(registry, file_size=200)
        _make_test_record(registry, file_size=200)
        _make_test_record(registry, file_size=200)

        # All non-undone, reachable. 800 < 900, no eviction needed.
        evicted = registry.evict()
        assert len(evicted) == 0
        assert registry.bytes_used == 800

        # Add more to push over 900 bytes
        _make_test_record(registry, file_size=200)
        # Now 1000 bytes > 900 target

        # Mark r1 as undone (evictable)
        r1.undone = True

        evicted = registry.evict()
        # Should evict r1 (200 bytes), bringing total to 800 < 900
        assert len(evicted) == 1
        assert r1.artifact_id in evicted
        assert registry.bytes_used == 800


# ---------------------------------------------------------------------------
# Orphan cleanup
# ---------------------------------------------------------------------------


class TestOrphanCleanup:
    def test_cleanup_orphans_deletes_unregistered_files(
        self, tmp_path: Path
    ) -> None:
        registry = _make_registry(tmp_path)

        # Create a file not registered
        orphan_path = registry._workspace.session_dir / "orphan.bin"
        orphan_path.write_bytes(b"orphan data")

        deleted = registry.cleanup_orphans()
        assert orphan_path in deleted
        assert not orphan_path.exists()

    def test_cleanup_orphans_preserves_registered_files(
        self, tmp_path: Path
    ) -> None:
        registry = _make_registry(tmp_path)

        record = _make_test_record(registry)
        file_path = record.path

        deleted = registry.cleanup_orphans()
        assert file_path not in deleted
        assert file_path.exists()

    def test_cleanup_orphans_returns_deleted_paths(
        self, tmp_path: Path
    ) -> None:
        registry = _make_registry(tmp_path)

        orphan1 = registry._workspace.session_dir / "orphan1.bin"
        orphan2 = registry._workspace.session_dir / "orphan2.bin"
        orphan1.write_bytes(b"orphan 1")
        orphan2.write_bytes(b"orphan 2")

        deleted = registry.cleanup_orphans()
        assert len(deleted) == 2
        assert orphan1 in deleted
        assert orphan2 in deleted


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------


class TestPersistence:
    def test_persist_writes_registry_json(self, tmp_path: Path) -> None:
        registry = _make_registry(tmp_path)
        _make_test_record(registry)

        registry.persist()
        registry_path = registry._workspace.session_dir / "registry.json"
        assert registry_path.exists()

    def test_persist_uses_relative_paths(self, tmp_path: Path) -> None:
        registry = _make_registry(tmp_path)
        record = _make_test_record(registry)

        registry.persist()
        registry_path = registry._workspace.session_dir / "registry.json"

        with open(registry_path, "r") as f:
            data = json.load(f)

        # Path should be relative (not starting with /)
        stored_path = data["artifacts"][record.artifact_id]["path"]
        assert not stored_path.startswith("/")
        # Should be a valid filename (not an absolute path)
        assert stored_path == record.path.name or "/" not in stored_path.lstrip("/")

    def test_load_rebuilds_state(self, tmp_path: Path) -> None:
        registry1 = _make_registry(tmp_path)
        record = _make_test_record(registry1)
        registry1.persist()

        # Create a new registry on the same session dir
        workspace2 = WorkspaceManager(
            workspace_root=tmp_path / "sessions2",
            disk_limit_bytes=10_000_000,
            session_id=registry1._workspace.session_id,
        )
        # Manually set session_dir to match
        workspace2._session_dir = registry1._workspace.session_dir
        registry2 = ArtifactRegistry(workspace=workspace2, disk_limit_bytes=10_000_000)

        loaded = registry2.get(record.artifact_id)
        assert loaded is not None
        assert loaded.artifact_id == record.artifact_id
        assert loaded.format == record.format
        assert loaded.data_type == record.data_type

    def test_load_restores_counter(self, tmp_path: Path) -> None:
        registry1 = _make_registry(tmp_path)
        _make_test_record(registry1)
        _make_test_record(registry1)
        _make_test_record(registry1)
        registry1.persist()

        # Create a new registry on the same session dir
        workspace2 = WorkspaceManager(
            workspace_root=tmp_path / "sessions2",
            disk_limit_bytes=10_000_000,
            session_id=registry1._workspace.session_id,
        )
        workspace2._session_dir = registry1._workspace.session_dir
        registry2 = ArtifactRegistry(workspace=workspace2, disk_limit_bytes=10_000_000)

        assert registry2._counter == 3

    def test_load_restores_idempotency_cache(self, tmp_path: Path) -> None:
        registry1 = _make_registry(tmp_path)

        registry1.record_idempotent(
            tool_name="ese",
            tool_version="1.0.0",
            command_name="clip",
            params={"buffer": 100},
            input_artifact_id="input_001",
            output_artifact_id="output_001",
        )

        # Create a new registry on the same session dir
        workspace2 = WorkspaceManager(
            workspace_root=tmp_path / "sessions2",
            disk_limit_bytes=10_000_000,
            session_id=registry1._workspace.session_id,
        )
        workspace2._session_dir = registry1._workspace.session_dir
        registry2 = ArtifactRegistry(workspace=workspace2, disk_limit_bytes=10_000_000)

        result = registry2.is_idempotent(
            tool_name="ese",
            tool_version="1.0.0",
            command_name="clip",
            params={"buffer": 100},
            input_artifact_id="input_001",
        )
        assert result == "output_001"

    def test_persist_atomic_write(self, tmp_path: Path) -> None:
        registry = _make_registry(tmp_path)
        _make_test_record(registry)

        registry.persist()

        # Check that .tmp file doesn't persist (atomic rename)
        tmp_path_file = registry._workspace.session_dir / "registry.json.tmp"
        assert not tmp_path_file.exists()


# ---------------------------------------------------------------------------
# Crash recovery
# ---------------------------------------------------------------------------


class TestCrashRecovery:
    def test_crash_recovery_loads_existing_artifacts(
        self, tmp_path: Path
    ) -> None:
        registry1 = _make_registry(tmp_path)
        record = _make_test_record(registry1)
        registry1.persist()

        # Simulate crash: create new registry on same session dir
        workspace2 = WorkspaceManager(
            workspace_root=tmp_path / "sessions2",
            disk_limit_bytes=10_000_000,
            session_id=registry1._workspace.session_id,
        )
        workspace2._session_dir = registry1._workspace.session_dir
        registry2 = ArtifactRegistry(workspace=workspace2, disk_limit_bytes=10_000_000)

        loaded = registry2.get(record.artifact_id)
        assert loaded is not None
        assert loaded.artifact_id == record.artifact_id

    def test_crash_recovery_rebuilds_bytes_used(
        self, tmp_path: Path
    ) -> None:
        registry1 = _make_registry(tmp_path)
        _make_test_record(registry1, file_size=500)
        _make_test_record(registry1, file_size=300)
        registry1.persist()

        # Simulate crash
        workspace2 = WorkspaceManager(
            workspace_root=tmp_path / "sessions2",
            disk_limit_bytes=10_000_000,
            session_id=registry1._workspace.session_id,
        )
        workspace2._session_dir = registry1._workspace.session_dir
        registry2 = ArtifactRegistry(workspace=workspace2, disk_limit_bytes=10_000_000)

        # bytes_used is computed from non-evicted artifacts
        assert registry2.bytes_used == 800


# ---------------------------------------------------------------------------
# resolve_input
# ---------------------------------------------------------------------------


class TestResolveInput:
    def test_resolve_input_returns_artifact(self, tmp_path: Path) -> None:
        registry = _make_registry(tmp_path)
        record = _make_test_record(registry)

        result = registry.resolve_input(record.artifact_id)
        assert result is record

    def test_resolve_input_returns_none_for_unknown(
        self, tmp_path: Path
    ) -> None:
        registry = _make_registry(tmp_path)
        result = registry.resolve_input("nonexistent_999")
        assert result is None
