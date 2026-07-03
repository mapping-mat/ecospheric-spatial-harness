"""Tests for the FastAPI application (web/app.py)."""

from __future__ import annotations

import uuid
from unittest.mock import MagicMock, patch

import pytest

# Patch Harness BEFORE importing anything that references it, so
# SessionManager.__init__ / create_session / get_or_create never call
# the real Harness constructor (which needs tool discovery, env vars, etc.).
with patch("ecospheric_harness.session_manager.Harness", autospec=True):
    from fastapi.testclient import TestClient

    from ecospheric_harness.web.app import create_app


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def client() -> TestClient:
    """Return a TestClient backed by a mocked SessionManager."""
    app = create_app(harness_kwargs={"tools": ["edd", "ese"]})
    return TestClient(app)


def _is_uuid(value: str) -> bool:
    """Return True if *value* is a valid UUID4 string."""
    try:
        uuid.UUID(value, version=4)
        return True
    except ValueError:
        return False


# ---------------------------------------------------------------------------
# 1. POST /api/session — create a session
# ---------------------------------------------------------------------------


def test_create_session(client: TestClient):
    """POST /api/session returns 200 with a valid UUID session_id."""
    resp = client.post("/api/session")
    assert resp.status_code == 200

    body = resp.json()
    assert "session_id" in body
    assert _is_uuid(body["session_id"])


# ---------------------------------------------------------------------------
# 2. GET /api/sessions — list sessions
# ---------------------------------------------------------------------------


def test_list_sessions(client: TestClient):
    """After creating two sessions, GET /api/sessions returns both."""
    client.post("/api/session")
    client.post("/api/session")

    resp = client.get("/api/sessions")
    assert resp.status_code == 200

    body = resp.json()
    assert "sessions" in body
    assert len(body["sessions"]) == 2


# ---------------------------------------------------------------------------
# 3. GET /api/session/{id}/artifacts — 404 for unknown session
# ---------------------------------------------------------------------------


def test_list_artifacts_404(client: TestClient):
    """Artifacts endpoint returns 404 for a non-existent session."""
    resp = client.get("/api/session/does-not-exist/artifacts")
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# 4. GET /api/session/{id}/state — 404 for unknown session
# ---------------------------------------------------------------------------


def test_get_state_404(client: TestClient):
    """State endpoint returns 404 for a non-existent session."""
    resp = client.get("/api/session/does-not-exist/state")
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# 5. POST /api/chat — unknown session_id gets created on the fly (get_or_create)
# ---------------------------------------------------------------------------


def test_chat_creates_session_on_the_fly(client: TestClient):
    """POST /api/chat with an unknown session_id should create the session
    via get_or_create and return a streaming response (not 404).

    The chat endpoint calls sm.get_or_create(req.session_id) which creates
    a new Harness if the session doesn't exist yet, so we expect a 200
    with text/event-stream content type.
    """
    fake_id = str(uuid.uuid4())
    resp = client.post(
        "/api/chat",
        json={"session_id": fake_id, "prompt": "hello"},
    )
    # The app uses get_or_create, so it should NOT 404.
    # It returns a StreamingResponse (200) with event-stream media type.
    assert resp.status_code == 200
    assert "text/event-stream" in resp.headers.get("content-type", "")


# ---------------------------------------------------------------------------
# 6. POST /api/chat — 409 when session is busy
# ---------------------------------------------------------------------------


def test_chat_409_when_session_busy(client: TestClient):
    """A second concurrent request to a busy session returns 409."""
    # Create a session
    resp = client.post("/api/session")
    session_id = resp.json()["session_id"]

    # Manually mark it as busy via the SessionManager
    sm = client.app.state.session_manager
    sm.acquire(session_id)

    # Second request to the same session should 409
    resp = client.post(
        "/api/chat",
        json={"session_id": session_id, "prompt": "hello"},
    )
    assert resp.status_code == 409
    assert "busy" in resp.json()["detail"].lower()

    # Cleanup
    sm.release(session_id)
