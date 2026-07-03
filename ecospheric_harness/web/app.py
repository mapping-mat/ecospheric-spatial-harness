"""FastAPI application factory for the Ecospheric Spatial Harness.

Provides session management, artifact inspection, and a chat endpoint
with Server-Sent Event streaming.
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from ecospheric_harness.session_manager import SessionManager
from ecospheric_harness.web.sse import format_sse_event, QueueEventRelay


# ---------------------------------------------------------------------------
# Request / response models
# ---------------------------------------------------------------------------


class ChatRequest(BaseModel):
    session_id: str
    prompt: str = Field(min_length=1)


# ---------------------------------------------------------------------------
# App factory
# ---------------------------------------------------------------------------


def create_app(harness_kwargs: dict[str, Any] | None = None) -> FastAPI:
    """Create and return a configured FastAPI application.

    Args:
        harness_kwargs: Optional keyword arguments forwarded to
            :class:`~ecospheric_harness.session_manager.SessionManager`
            (and in turn to :class:`~ecospheric_harness.__main__.Harness`).

    Returns:
        A fully-wired FastAPI instance.
    """
    app = FastAPI(title="Ecospheric Spatial Harness")
    app.state.session_manager = SessionManager(**(harness_kwargs or {}))

    # ------------------------------------------------------------------
    # Session endpoints
    # ------------------------------------------------------------------

    @app.post("/api/session")
    async def create_session() -> dict[str, str]:
        """Create a new session and return its ID."""
        sm: SessionManager = app.state.session_manager
        session_id = sm.create_session()
        return {"session_id": session_id}

    @app.get("/api/sessions")
    async def list_sessions() -> dict[str, Any]:
        """List all active sessions."""
        sm: SessionManager = app.state.session_manager
        return {"sessions": sm.list_sessions()}

    # ------------------------------------------------------------------
    # Artifact / state inspection
    # ------------------------------------------------------------------

    @app.get("/api/session/{session_id}/artifacts")
    async def list_artifacts(session_id: str) -> dict[str, Any]:
        """Return all artifacts registered in a session."""
        sm: SessionManager = app.state.session_manager
        harness = sm.get(session_id)
        if harness is None:
            raise HTTPException(status_code=404, detail="Session not found")

        artifacts = harness._artifact_registry.list_all()
        return {
            "artifacts": [
                {
                    "id": a.artifact_id,
                    "data_type": a.data_type,
                    "format": a.format,
                    "crs": a.crs,
                    "bbox": a.bbox,
                }
                for a in artifacts
            ]
        }

    @app.get("/api/session/{session_id}/state")
    async def get_state(session_id: str) -> dict[str, Any]:
        """Return the current state of a session."""
        sm: SessionManager = app.state.session_manager
        harness = sm.get(session_id)
        if harness is None:
            raise HTTPException(status_code=404, detail="Session not found")

        recent = harness._artifact_registry.get_recent(5)
        return {
            "recent_artifacts": [
                {"id": a.artifact_id, "type": a.data_type, "format": a.format}
                for a in recent
            ],
        }

    # ------------------------------------------------------------------
    # Chat with SSE streaming
    # ------------------------------------------------------------------

    @app.post("/api/chat")
    async def chat(req: ChatRequest) -> StreamingResponse:
        """Execute a prompt in a session and stream events via SSE.

        Returns ``409 Conflict`` if the session is already busy.
        """
        sm: SessionManager = app.state.session_manager
        harness = sm.get_or_create(req.session_id)

        # Try to acquire the session lock — reject if busy.
        if not sm.acquire(req.session_id):
            raise HTTPException(
                status_code=409,
                detail="Session is busy processing another request",
            )

        async def event_generator() -> Any:
            """Async generator that streams SSE events to the client.

            The orchestrator runs in a thread-pool worker.  Events are
            bridged into the async world via :class:`QueueEventRelay`.

            The session lock is released in the ``finally`` block so it
            is guaranteed to be freed after the stream completes or errors.
            """
            try:
                relay = QueueEventRelay()
                relay.set_loop(asyncio.get_running_loop())

                def run_orchestrator() -> None:
                    """Thread-pool target: run the sync orchestrator."""
                    try:
                        # TODO: Full event instrumentation (turn_start, tool_call,
                        #       artifact per step) requires orchestrator hooks.
                        #       For now we run to completion and push a single
                        #       "done" event.
                        result = harness.run(req.prompt)
                        relay.push(
                            format_sse_event("done", {"status": "complete"})
                        )
                    except Exception as exc:
                        relay.push(
                            format_sse_event("error", {"message": str(exc)})
                        )
                    finally:
                        relay.push(None)  # sentinel

                # Fire-and-forget in the default thread-pool executor.
                asyncio.get_running_loop().run_in_executor(
                    None, run_orchestrator,
                )

                async for event in relay:
                    yield event
            finally:
                # Always release the session lock — the stream may have been
                # cancelled by the client or raised an unhandled error.
                sm.release(req.session_id)

        return StreamingResponse(
            event_generator(),
            media_type="text/event-stream",
        )

    return app