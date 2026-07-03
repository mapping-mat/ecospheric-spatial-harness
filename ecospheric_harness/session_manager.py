"""Session manager for Harness instances.

Caches one :class:`Harness` per ``session_id`` and serializes concurrent access
so that a single session cannot be used for overlapping requests.  Concurrent
requests to the same session are rejected with a "busy" signal so the caller
(e.g. a FastAPI endpoint) can return HTTP 409.
"""

from __future__ import annotations

import threading
import uuid
from datetime import datetime, timezone

from ecospheric_harness.__main__ import Harness


class SessionManager:
    """Caches Harness instances per session_id.  Serializes concurrent access.

    *Thread-safe.*  Uses ``threading.Lock()`` to guard internal data structures
    so it works correctly with FastAPI's thread-pool model.
    """

    def __init__(self, **harness_kwargs) -> None:
        """Store default keyword arguments for :class:`Harness` construction.

        Any kwargs accepted by ``Harness.__init__`` may be passed here; they
        are forwarded to every Harness created by this manager, with the
        exception of ``session_id`` which is set per-session.
        """
        self._harness_kwargs: dict = harness_kwargs
        self._sessions: dict[str, Harness] = {}
        self._busy: set[str] = set()
        self._created_at: dict[str, str] = {}
        self._lock = threading.Lock()

    # -- public API --------------------------------------------------------

    def create_session(self) -> str:
        """Generate a new ``session_id``, create a Harness for it, cache it.

        Returns the new session id (a UUID4 string).
        """
        session_id = str(uuid.uuid4())
        harness = Harness(session_id=session_id, **self._harness_kwargs)

        with self._lock:
            self._sessions[session_id] = harness
            self._created_at[session_id] = datetime.now(timezone.utc).isoformat()

        return session_id

    def get_or_create(self, session_id: str) -> Harness:
        """Return the cached Harness for *session_id*, or create + cache one.

        Raises :class:`ValueError` if the ``session_id`` is empty.
        """
        if not session_id:
            raise ValueError("session_id must not be empty")

        with self._lock:
            existing = self._sessions.get(session_id)
            if existing is not None:
                return existing

        # Create outside the lock — Harness.__init__ is expensive (flocks, tool
        # discovery, registry loading).  Only guard the dict mutation.
        harness = Harness(session_id=session_id, **self._harness_kwargs)

        with self._lock:
            # Double-check: another thread may have created it while we were
            # constructing.
            if session_id in self._sessions:
                return self._sessions[session_id]

            self._sessions[session_id] = harness
            if session_id not in self._created_at:
                self._created_at[session_id] = datetime.now(timezone.utc).isoformat()

        return harness

    def get(self, session_id: str) -> Harness | None:
        """Return the cached Harness for *session_id*, or ``None``."""
        with self._lock:
            return self._sessions.get(session_id)

    def is_busy(self, session_id: str) -> bool:
        """Return ``True`` if a request is currently in-flight for *session_id*."""
        with self._lock:
            return session_id in self._busy

    def acquire(self, session_id: str) -> bool:
        """Try to mark *session_id* as busy.

        Returns ``True`` if the session was successfully acquired,
        ``False`` if it is already busy.
        """
        with self._lock:
            if session_id in self._busy:
                return False
            self._busy.add(session_id)
            return True

    def release(self, session_id: str) -> None:
        """Mark *session_id* as not busy (idempotent)."""
        with self._lock:
            self._busy.discard(session_id)

    def list_sessions(self) -> list[dict]:
        """Return a list of dicts for each cached session.

        Each dict contains: ``session_id``, ``created_at`` (ISO timestamp),
        and ``artifact_count`` (int).
        """
        result: list[dict] = []
        with self._lock:
            for sid, harness in self._sessions.items():
                try:
                    count = len(harness._artifact_registry.list_all())
                except Exception:
                    count = 0
                result.append({
                    "session_id": sid,
                    "created_at": self._created_at.get(sid, ""),
                    "artifact_count": count,
                })
        return result

    def remove(self, session_id: str) -> None:
        """Remove *session_id* from the cache without deleting files.

        The Harness object is dropped from the cache.  The process-level
        ``flock`` it holds is released when the Harness is garbage-collected.
        """
        with self._lock:
            self._sessions.pop(session_id, None)
            self._busy.discard(session_id)
            self._created_at.pop(session_id, None)