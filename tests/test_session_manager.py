"""Tests for SessionManager — thread-safe session lifecycle management."""

from __future__ import annotations

import threading
import uuid
from unittest.mock import MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _new_manager(**extra):
    """Create a SessionManager with Harness mocked out."""
    with patch("ecospheric_harness.session_manager.Harness") as mock_harness:
        mock_harness.return_value = MagicMock(name="Harness")
        from ecospheric_harness.session_manager import SessionManager
        mgr = SessionManager(**extra)
    return mgr


# ---------------------------------------------------------------------------
# 1. create_session returns valid UUID
# ---------------------------------------------------------------------------

def test_create_session_returns_uuid():
    mgr = _new_manager()
    sid = mgr.create_session()

    assert isinstance(sid, str)
    assert len(sid) == 36
    assert sid.count("-") == 4  # standard UUID format: 8-4-4-4-12
    # Must be parseable as UUID
    parsed = uuid.UUID(sid)
    assert str(parsed) == sid

    # Session should be retrievable
    harness = mgr.get(sid)
    assert harness is not None


# ---------------------------------------------------------------------------
# 2. get_or_create creates if missing, returns same if exists
# ---------------------------------------------------------------------------

def test_get_or_create_creates_if_missing():
    mgr = _new_manager()

    harness_a = mgr.get_or_create("session-alpha")
    assert harness_a is not None

    # Same session_id returns the *same object* (identity, not just equality)
    harness_b = mgr.get_or_create("session-alpha")
    assert harness_a is harness_b


# ---------------------------------------------------------------------------
# 3. get returns None for unknown session
# ---------------------------------------------------------------------------

def test_get_returns_none_if_missing():
    mgr = _new_manager()
    assert mgr.get("nonexistent-session-id") is None


# ---------------------------------------------------------------------------
# 4. acquire / release lifecycle
# ---------------------------------------------------------------------------

def test_acquire_release():
    mgr = _new_manager()
    sid = mgr.create_session()

    assert mgr.acquire(sid) is True      # first acquire succeeds
    assert mgr.acquire(sid) is False     # already busy
    mgr.release(sid)
    assert mgr.acquire(sid) is True      # can re-acquire after release


# ---------------------------------------------------------------------------
# 5. is_busy reflects lock state
# ---------------------------------------------------------------------------

def test_is_busy():
    mgr = _new_manager()
    sid = mgr.create_session()

    assert mgr.is_busy(sid) is False
    mgr.acquire(sid)
    assert mgr.is_busy(sid) is True
    mgr.release(sid)
    assert mgr.is_busy(sid) is False


# ---------------------------------------------------------------------------
# 6. list_sessions
# ---------------------------------------------------------------------------

def test_list_sessions():
    mgr = _new_manager()
    sid1 = mgr.create_session()
    sid2 = mgr.create_session()

    sessions = mgr.list_sessions()
    assert len(sessions) == 2

    returned_ids = {s["session_id"] for s in sessions}
    assert returned_ids == {sid1, sid2}

    for s in sessions:
        assert "session_id" in s
        assert "created_at" in s
        assert "artifact_count" in s


# ---------------------------------------------------------------------------
# 7. remove
# ---------------------------------------------------------------------------

def test_remove():
    mgr = _new_manager()
    sid = mgr.create_session()
    assert mgr.get(sid) is not None  # confirm cached

    mgr.remove(sid)

    assert mgr.get(sid) is None
    session_ids = [s["session_id"] for s in mgr.list_sessions()]
    assert sid not in session_ids


# ---------------------------------------------------------------------------
# 8. concurrent acquire — only one thread wins
# ---------------------------------------------------------------------------

def test_concurrent_acquire():
    mgr = _new_manager()
    sid = mgr.create_session()

    results: list[bool] = []
    barrier = threading.Barrier(2)

    def _try_acquire():
        barrier.wait()
        results.append(mgr.acquire(sid))

    t1 = threading.Thread(target=_try_acquire)
    t2 = threading.Thread(target=_try_acquire)
    t1.start()
    t2.start()
    t1.join()
    t2.join()

    # Exactly one thread should have acquired, the other failed
    assert sorted(results) == [False, True]
