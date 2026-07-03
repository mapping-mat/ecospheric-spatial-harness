"""Per-session workspace management for the Ecospheric Agent Harness.

Provides path confinement, disk accounting, and per-session locking.
"""

from __future__ import annotations

import fcntl
import os
from pathlib import Path
from uuid import uuid4


class PathConfinementError(Exception):
    """Raised when a path escapes the session workspace directory."""

    def __init__(self, path: Path, session_dir: Path) -> None:
        self.path = path
        self.session_dir = session_dir
        super().__init__(
            f"Path '{path}' is outside session directory '{session_dir}'"
        )


class WorkspaceManager:
    """Creates and manages a per-session workspace directory.

    Provides path confinement, disk accounting, and per-session locking.
    """

    def __init__(
        self,
        workspace_root: Path,
        disk_limit_bytes: int,
        session_id: str | None = None,
    ) -> None:
        """Initialize workspace, create session dir, acquire flock.

        - Creates workspace_root if it doesn't exist (parents=True).
        - Creates session_dir = workspace_root / session_id.
        - Acquires flock(LOCK_EX | LOCK_NB) on session_dir/.lock.
        - For resumed sessions (existing dir), walks files to rebuild disk accounting.
        - Raises FileExistsError if lock cannot be acquired.
        """
        if session_id is None:
            session_id = uuid4().hex[:12]

        self._workspace_root = workspace_root
        self._disk_limit_bytes = disk_limit_bytes
        self._session_id = session_id
        self._session_dir = workspace_root / session_id

        # Create directories
        self._session_dir.mkdir(parents=True, exist_ok=True)

        # Compute realpath once for confinement checks
        self._session_dir_resolved = Path(
            os.path.realpath(str(self._session_dir))
        )

        # Acquire exclusive non-blocking flock on .lock
        self._lockfile_path = self._session_dir / ".lock"
        self._lockfile_fd: int | None = None
        fd = os.open(str(self._lockfile_path), os.O_CREAT | os.O_RDWR)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            os.close(fd)
            raise FileExistsError(
                f"Session '{session_id}' is already locked"
            )
        self._lockfile_fd = fd

        # Disk accounting: walk existing files for resumed sessions
        self._tracked_files: dict[Path, int] = {}
        self._bytes_used: int = 0
        self._rebuild_accounting()

    # -- properties --------------------------------------------------------

    @property
    def session_dir(self) -> Path:
        """Return the session directory path."""
        return self._session_dir

    @property
    def session_id(self) -> str:
        """Return the session identifier."""
        return self._session_id

    @property
    def workspace_root(self) -> Path:
        """Return the workspace root directory."""
        return self._workspace_root

    @property
    def disk_limit_bytes(self) -> int:
        """Return the disk limit in bytes."""
        return self._disk_limit_bytes

    @property
    def bytes_used(self) -> int:
        """Return the total bytes tracked in this session."""
        return self._bytes_used

    @property
    def lockfile_path(self) -> Path:
        """Return the lockfile path."""
        return self._lockfile_path

    # -- path methods ------------------------------------------------------

    def resolve_path(self, relative_or_absolute: str | Path) -> Path:
        """Resolve a path relative to session_dir or absolute.

        Canonicalizes via os.path.realpath(). Checks confinement.
        Returns the canonical path. Raises PathConfinementError if outside.
        """
        p = Path(relative_or_absolute)
        if not p.is_absolute():
            p = self._session_dir / p
        canonical = Path(os.path.realpath(str(p)))
        if not self._is_confined(canonical):
            raise PathConfinementError(canonical, self._session_dir_resolved)
        return canonical

    def check_path(self, path: Path) -> Path:
        """Canonicalize and check an absolute path for confinement.

        Returns the canonical path. Raises PathConfinementError if outside.
        """
        canonical = Path(os.path.realpath(str(path)))
        if not self._is_confined(canonical):
            raise PathConfinementError(canonical, self._session_dir_resolved)
        return canonical

    # -- disk accounting ---------------------------------------------------

    def track_bytes(self, path: Path) -> int:
        """Add file's size to accounting. Idempotent.

        Returns bytes added (0 if already tracked or file missing).
        """
        real = Path(os.path.realpath(str(path)))
        if real in self._tracked_files:
            return 0
        try:
            size = real.stat().st_size
        except OSError:
            return 0
        self._tracked_files[real] = size
        self._bytes_used += size
        return size

    def release_bytes(self, path: Path) -> int:
        """Subtract file's size from accounting.

        Returns bytes released (0 if not tracked).
        """
        real = Path(os.path.realpath(str(path)))
        size = self._tracked_files.pop(real, 0)
        self._bytes_used -= size
        return size

    def cleanup_unregistered(self, path: Path) -> bool:
        """Delete an unregistered temp/output file. Returns True if deleted.

        Used for cleaning up output files from failed validation or
        cancelled steps.  Only deletes the file if it's within the
        workspace root (path confinement).
        """
        try:
            canonical = Path(os.path.realpath(str(path)))
            # Check confinement against workspace_root (broader than session_dir)
            # so that output files created in nested subdirs can still be cleaned.
            try:
                canonical.relative_to(self._workspace_root.resolve())
            except ValueError:
                return False
            if canonical.exists():
                canonical.unlink()
                return True
        except OSError:
            return False
        return False

    def check_disk_available(self, estimated_bytes: int = 0) -> bool:
        """Check if estimated_bytes fit within the disk limit."""
        return self._bytes_used + estimated_bytes < self._disk_limit_bytes

    def create_temp_path(self, suffix: str = ".bin") -> Path:
        """Return a temp path inside session_dir. Does NOT create the file."""
        return self._session_dir / f"step_{uuid4().hex[:8]}{suffix}"

    def cleanup(self) -> None:
        """Release flock, close lockfile fd. Idempotent."""
        if self._lockfile_fd is not None:
            try:
                fcntl.flock(self._lockfile_fd, fcntl.LOCK_UN)
            except OSError:
                pass
            try:
                os.close(self._lockfile_fd)
            except OSError:
                pass
            self._lockfile_fd = None

    # -- internal ----------------------------------------------------------

    def _is_confined(self, canonical_path: Path) -> bool:
        """Check if canonical_path is within _session_dir_resolved."""
        try:
            canonical_path.relative_to(self._session_dir_resolved)
            return True
        except ValueError:
            return False

    def _rebuild_accounting(self) -> None:
        """Walk session_dir recursively and populate disk accounting."""
        for dirpath, _dirnames, filenames in os.walk(str(self._session_dir)):
            for fname in filenames:
                if fname == ".lock":
                    continue
                fpath = Path(dirpath) / fname
                try:
                    real = Path(os.path.realpath(str(fpath)))
                    size = real.stat().st_size
                    self._tracked_files[real] = size
                    self._bytes_used += size
                except OSError:
                    pass
