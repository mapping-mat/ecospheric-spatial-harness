"""Named artifact registry with provenance DAG, idempotency cache,
and disk-eviction policy for the Ecospheric Agent Harness.

Replaces ArtifactManager entirely.
"""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ecospheric_harness.workspace import WorkspaceManager


# ---------------------------------------------------------------------------
# ArtifactRecord dataclass
# ---------------------------------------------------------------------------


@dataclass
class ArtifactRecord:
    """A single geospatial data artifact with provenance metadata."""

    artifact_id: str  # "clip_001"
    path: Path  # absolute path on disk
    format: str  # "geotiff"
    data_type: str  # "raster" | "vector" | "pointcloud" | "metadata"
    crs: str | None = None
    bbox: list[float] | None = None
    step_number: int = 0
    envelope: dict[str, Any] = field(default_factory=dict)
    parent_ids: list[str] = field(default_factory=list)  # DAG parents
    intent: str = ""
    tool_name: str = ""
    tool_version: str = ""
    command_name: str = ""
    params: dict[str, Any] = field(default_factory=dict)
    duration_ms: int = 0
    is_search: bool = False
    created_at: str = ""  # ISO 8601 string
    undone: bool = False
    evicted: bool = False
    file_size_bytes: int = 0

    def summary(self) -> str:
        """One-line summary: 'clip_001 [raster/geotiff] clip'"""
        return f"{self.artifact_id} [{self.data_type}/{self.format}] {self.intent}"


# ---------------------------------------------------------------------------
# ArtifactRegistry class
# ---------------------------------------------------------------------------


class ArtifactRegistry:
    """Named artifact registry with provenance DAG, idempotency cache,
    and disk-eviction policy. Replaces ArtifactManager entirely."""

    def __init__(self, workspace: WorkspaceManager, disk_limit_bytes: int) -> None:
        """Initialize registry, attempt crash recovery from registry.json."""
        self._workspace = workspace
        self._disk_limit = disk_limit_bytes
        self._artifacts: dict[str, ArtifactRecord] = {}
        self._counter: int = 0
        self._idempotency_cache: dict[str, str] = {}
        self._registry_path = workspace.session_dir / "registry.json"

        # Attempt crash recovery
        if self._registry_path.exists():
            self.load()

    # -- ID generation -----------------------------------------------------

    def _next_id(self, intent: str) -> str:
        """Generate `{sanitized_intent}_{counter:03d}`. Increment counter."""
        # Sanitize intent: lowercase, replace non-alphanumeric with underscore
        sanitized = "".join(c if c.isalnum() else "_" for c in intent.lower())
        self._counter += 1
        return f"{sanitized}_{self._counter:03d}"

    # -- Registration ------------------------------------------------------

    def register(
        self,
        *,
        path: Path,
        format: str,
        data_type: str,
        crs: str | None = None,
        bbox: list[float] | None = None,
        step_number: int = 0,
        envelope: dict[str, Any] | None = None,
        parent_ids: list[str] | None = None,
        intent: str = "",
        tool_name: str = "",
        tool_version: str = "",
        command_name: str = "",
        params: dict[str, Any] | None = None,
        duration_ms: int = 0,
        is_search: bool = False,
    ) -> ArtifactRecord:
        """Create record, assign ID, track bytes via workspace, persist."""
        # Generate ID
        artifact_id = self._next_id(intent)

        # Create timestamp
        from datetime import datetime, timezone

        created_at = datetime.now(timezone.utc).isoformat()

        # Get file size
        file_size = self._safe_file_size(path)

        # Create record
        record = ArtifactRecord(
            artifact_id=artifact_id,
            path=path,
            format=format,
            data_type=data_type,
            crs=crs,
            bbox=bbox,
            step_number=step_number,
            envelope=envelope or {},
            parent_ids=parent_ids or [],
            intent=intent,
            tool_name=tool_name,
            tool_version=tool_version,
            command_name=command_name,
            params=params or {},
            duration_ms=duration_ms,
            is_search=is_search,
            created_at=created_at,
            file_size_bytes=file_size,
        )

        # Store in registry
        self._artifacts[artifact_id] = record

        # Track bytes via workspace
        self._workspace.track_bytes(path)

        # Persist
        self.persist()

        return record

    # -- Lookup ------------------------------------------------------------

    def get(self, artifact_id: str) -> ArtifactRecord | None:
        """Lookup. Return None if not found or evicted."""
        record = self._artifacts.get(artifact_id)
        if record is None or record.evicted:
            return None
        return record

    def get_recent(self, n: int = 2) -> list[ArtifactRecord]:
        """N most recent non-evicted, non-undone, sorted by step_number descending."""
        active = [
            r for r in self._artifacts.values() if not r.evicted and not r.undone
        ]
        active.sort(key=lambda r: r.step_number, reverse=True)
        return active[:n]

    def list_all(self) -> list[ArtifactRecord]:
        """All non-evicted artifacts, sorted by step_number ascending."""
        active = [r for r in self._artifacts.values() if not r.evicted]
        active.sort(key=lambda r: r.step_number)
        return active

    def resolve_input(self, artifact_id: str) -> ArtifactRecord | None:
        """Alias for get(), semantic for orchestrator."""
        return self.get(artifact_id)

    # -- Idempotency -------------------------------------------------------

    def _idempotency_key(
        self,
        tool_name: str,
        tool_version: str,
        command_name: str,
        params: dict[str, Any],
        input_artifact_id: str | None,
    ) -> str:
        """Build idempotency cache key."""
        params_hash = hashlib.sha256(
            json.dumps(params, sort_keys=True).encode()
        ).hexdigest()[:16]
        input_id = input_artifact_id or "none"
        return f"{tool_name}:{tool_version}:{command_name}:{params_hash}:{input_id}"

    def is_idempotent(
        self,
        tool_name: str,
        tool_version: str,
        command_name: str,
        params: dict[str, Any],
        input_artifact_id: str | None,
    ) -> str | None:
        """Check cache, return cached output_id or None."""
        key = self._idempotency_key(
            tool_name, tool_version, command_name, params, input_artifact_id
        )
        return self._idempotency_cache.get(key)

    def record_idempotent(
        self,
        tool_name: str,
        tool_version: str,
        command_name: str,
        params: dict[str, Any],
        input_artifact_id: str | None,
        output_artifact_id: str,
    ) -> None:
        """Cache result, persist."""
        key = self._idempotency_key(
            tool_name, tool_version, command_name, params, input_artifact_id
        )
        self._idempotency_cache[key] = output_artifact_id
        self.persist()

    # -- Undo/Redo ---------------------------------------------------------

    def mark_undone(self, artifact_id: str) -> bool:
        """Mark artifact as undone. Return True if found."""
        record = self._artifacts.get(artifact_id)
        if record is None:
            return False
        record.undone = True
        self.persist()
        return True

    def redo_from(self, artifact_id: str) -> ArtifactRecord | None:
        """Clear undone flag, return record. For redo."""
        record = self._artifacts.get(artifact_id)
        if record is None:
            return None
        record.undone = False
        self.persist()
        return record

    # -- Eviction ----------------------------------------------------------

    def evict(self) -> list[str]:
        """Run eviction policy. Evict oldest non-current-turn artifacts not
        reachable in DAG from current artifacts. Delete files, mark evicted.
        Return list of evicted IDs."""
        # Get all non-evicted artifacts
        all_artifacts = {
            aid: rec for aid, rec in self._artifacts.items() if not rec.evicted
        }

        # Find "reachable" set: start from all non-undone artifacts,
        # walk parent_ids backward to find all ancestors.
        reachable: set[str] = set()
        for aid, rec in all_artifacts.items():
            if not rec.undone:
                self._mark_reachable(aid, all_artifacts, reachable)

        # Evictable = non-evicted artifacts NOT in reachable set
        evictable = [
            rec for aid, rec in all_artifacts.items() if aid not in reachable
        ]

        # Sort by step_number ascending (oldest first)
        evictable.sort(key=lambda r: r.step_number)

        # Evict while bytes_used > disk_limit * 0.9
        evicted_ids: list[str] = []
        target_bytes = int(self._disk_limit * 0.9)

        while self.bytes_used > target_bytes and evictable:
            rec = evictable.pop(0)
            # Delete file
            if rec.path.exists():
                rec.path.unlink()
                self._workspace.release_bytes(rec.path)
            rec.evicted = True
            evicted_ids.append(rec.artifact_id)

        if evicted_ids:
            self.persist()

        return evicted_ids

    def _mark_reachable(
        self,
        artifact_id: str,
        all_artifacts: dict[str, ArtifactRecord],
        reachable: set[str],
    ) -> None:
        """Recursively mark an artifact and its ancestors as reachable."""
        if artifact_id in reachable:
            return
        reachable.add(artifact_id)
        rec = all_artifacts.get(artifact_id)
        if rec is None:
            return
        for parent_id in rec.parent_ids:
            self._mark_reachable(parent_id, all_artifacts, reachable)

    # -- Orphan cleanup ----------------------------------------------------

    def cleanup_orphans(self) -> list[Path]:
        """Find files in session_dir not in registry. Delete them.
        Return list of deleted paths."""
        # Collect all registered paths
        registered_paths: set[Path] = set()
        for rec in self._artifacts.values():
            registered_paths.add(rec.path)

        # Walk session_dir
        session_dir = self._workspace.session_dir
        deleted: list[Path] = []

        for dirpath, _dirnames, filenames in os.walk(str(session_dir)):
            for fname in filenames:
                if fname in (".lock", "registry.json", "registry.json.tmp"):
                    continue
                fpath = Path(dirpath) / fname
                if fpath not in registered_paths:
                    fpath.unlink(missing_ok=True)
                    self._workspace.release_bytes(fpath)
                    deleted.append(fpath)

        return deleted

    # -- Persistence -------------------------------------------------------

    def persist(self) -> None:
        """Atomic write (temp + rename) to registry.json."""
        self._registry_path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = self._registry_path.with_suffix(".json.tmp")

        # Convert to JSON-serializable dict
        data: dict[str, Any] = {
            "version": 1,
            "session_id": self._workspace.session_id,
            "next_counter": self._counter,
            "artifacts": {},
            "idempotency_cache": self._idempotency_cache,
        }

        for aid, rec in self._artifacts.items():
            # Store paths relative to session_dir for portability
            try:
                rel_path = rec.path.relative_to(self._workspace.session_dir)
            except ValueError:
                rel_path = rec.path
            rec_dict = {
                "artifact_id": rec.artifact_id,
                "path": str(rel_path),
                "format": rec.format,
                "data_type": rec.data_type,
                "crs": rec.crs,
                "bbox": rec.bbox,
                "step_number": rec.step_number,
                "envelope": rec.envelope,
                "parent_ids": rec.parent_ids,
                "intent": rec.intent,
                "tool_name": rec.tool_name,
                "tool_version": rec.tool_version,
                "command_name": rec.command_name,
                "params": rec.params,
                "duration_ms": rec.duration_ms,
                "is_search": rec.is_search,
                "created_at": rec.created_at,
                "undone": rec.undone,
                "evicted": rec.evicted,
                "file_size_bytes": rec.file_size_bytes,
            }
            data["artifacts"][aid] = rec_dict

        # Write to temp file then rename
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)

        os.replace(tmp_path, self._registry_path)

    def load(self) -> None:
        """Load from registry.json, rebuild _artifacts, _counter, _idempotency_cache."""
        if not self._registry_path.exists():
            return

        with open(self._registry_path, "r", encoding="utf-8") as f:
            data = json.load(f)

        self._counter = data.get("next_counter", 0)
        self._idempotency_cache = data.get("idempotency_cache", {})

        # Rebuild artifacts with absolute paths
        self._artifacts.clear()
        for aid, rec_dict in data.get("artifacts", {}).items():
            # Convert relative path back to absolute
            path_str = rec_dict["path"]
            if not os.path.isabs(path_str):
                abs_path = self._workspace.session_dir / path_str
            else:
                abs_path = Path(path_str)
            rec_dict["path"] = abs_path

            # Rebuild ArtifactRecord
            record = ArtifactRecord(
                artifact_id=rec_dict["artifact_id"],
                path=abs_path,
                format=rec_dict["format"],
                data_type=rec_dict["data_type"],
                crs=rec_dict.get("crs"),
                bbox=rec_dict.get("bbox"),
                step_number=rec_dict.get("step_number", 0),
                envelope=rec_dict.get("envelope", {}),
                parent_ids=rec_dict.get("parent_ids", []),
                intent=rec_dict.get("intent", ""),
                tool_name=rec_dict.get("tool_name", ""),
                tool_version=rec_dict.get("tool_version", ""),
                command_name=rec_dict.get("command_name", ""),
                params=rec_dict.get("params", {}),
                duration_ms=rec_dict.get("duration_ms", 0),
                is_search=rec_dict.get("is_search", False),
                created_at=rec_dict.get("created_at", ""),
                undone=rec_dict.get("undone", False),
                evicted=rec_dict.get("evicted", False),
                file_size_bytes=rec_dict.get("file_size_bytes", 0),
            )
            self._artifacts[aid] = record

    # -- Properties --------------------------------------------------------

    @property
    def current(self) -> ArtifactRecord | None:
        """Most recent non-evicted, non-undone artifact (for backward compat)."""
        recent = self.get_recent(1)
        return recent[0] if recent else None

    @property
    def can_undo(self) -> bool:
        """True if any non-undone artifact exists."""
        return any(not rec.undone and not rec.evicted for rec in self._artifacts.values())

    @property
    def bytes_used(self) -> int:
        """Total bytes of non-evicted artifacts."""
        return sum(
            rec.file_size_bytes for rec in self._artifacts.values() if not rec.evicted
        )

    @property
    def disk_limit_bytes(self) -> int:
        """Disk usage limit in bytes (public accessor)."""
        return self._disk_limit

    # -- Helpers -----------------------------------------------------------

    def _safe_file_size(self, path: Path) -> int:
        """Try stat, return 0 on OSError."""
        try:
            return path.stat().st_size
        except OSError:
            return 0
