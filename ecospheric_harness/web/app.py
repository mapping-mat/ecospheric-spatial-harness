"""FastAPI application factory for the Ecospheric Spatial Harness.

Provides session management, artifact inspection, and a chat endpoint
with Server-Sent Event streaming.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import StreamingResponse, Response
from pydantic import BaseModel, Field

from ecospheric_harness.session_manager import SessionManager
from ecospheric_harness.web.sse import format_sse_event, QueueEventRelay
from ecospheric_harness.web.tiles import serve_tile, get_tile_bounds, render_preview_png


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
    # Artifact preview + tiles
    # ------------------------------------------------------------------

    def _find_artifact(artifact_id: str) -> Any:
        """Search all sessions for an artifact by ID."""
        sm: SessionManager = app.state.session_manager
        for session in sm.list_sessions():
            sid = session["session_id"]
            harness = sm.get(sid)
            if harness is None:
                continue
            artifact = harness._artifact_registry.get(artifact_id)
            if artifact is not None:
                return artifact
        return None

    @app.get("/api/artifact/{artifact_id}/preview")
    async def artifact_preview(artifact_id: str) -> Response:
        """Serve vector artifacts as GeoJSON, raster metadata as JSON."""
        artifact = _find_artifact(artifact_id)
        if artifact is None:
            raise HTTPException(status_code=404, detail="Artifact not found")

        path = Path(artifact.path)
        if not path.exists():
            raise HTTPException(status_code=404, detail="Artifact file not found on disk")

        data_type = artifact.data_type

        if data_type == "vector":
            # Read via geopandas and return as GeoJSON
            import geopandas as gpd
            gdf = gpd.read_file(path)
            # Reproject to EPSG:4326 for Leaflet if needed
            if gdf.crs is not None and str(gdf.crs) != "EPSG:4326":
                gdf = gdf.to_crs("EPSG:4326")
            geojson = json.loads(gdf.to_json())
            return Response(
                content=json.dumps(geojson),
                media_type="application/json",
            )
        elif data_type == "raster":
            # Return raster metadata for the frontend
            try:
                meta = get_tile_bounds(path)
                return Response(
                    content=json.dumps(meta),
                    media_type="application/json",
                )
            except Exception as exc:
                raise HTTPException(
                    status_code=500,
                    detail=f"Failed to read raster metadata: {exc}",
                )
        else:
            raise HTTPException(
                status_code=400,
                detail=f"Preview not supported for data_type '{data_type}'",
            )

    @app.get("/api/artifact/{artifact_id}/tiles/{z}/{x}/{y}.png")
    async def artifact_tile(artifact_id: str, z: int, x: int, y: int) -> Response:
        """Serve a single XYZ tile from a raster artifact as PNG."""
        artifact = _find_artifact(artifact_id)
        if artifact is None:
            raise HTTPException(status_code=404, detail="Artifact not found")

        path = Path(artifact.path)
        if not path.exists():
            raise HTTPException(status_code=404, detail="Artifact file not found on disk")

        if artifact.data_type != "raster":
            raise HTTPException(
                status_code=400,
                detail="Tile serving is only available for raster artifacts",
            )

        try:
            png_bytes = serve_tile(path, z, x, y)
            return Response(content=png_bytes, media_type="image/png")
        except FileNotFoundError:
            raise HTTPException(status_code=404, detail="Raster file not found")
        except Exception as exc:
            raise HTTPException(
                status_code=500,
                detail=f"Tile rendering failed: {exc}",
            )

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

            The session lock is released in the ``finally`` block **after
            the orchestrator thread completes**, ensuring no background
            mutation occurs after lock release.
            """
            relay = QueueEventRelay()
            relay.set_loop(asyncio.get_running_loop())

            # Track the executor future so we can wait for it before
            # releasing the lock — prevents the race where a client
            # disconnects, the lock is released, but the orchestrator
            # is still mutating state in the background.
            future: asyncio.Future | None = None

            try:
                def run_orchestrator() -> None:
                    """Thread-pool target: run the sync orchestrator."""
                    try:
                        relay.push(format_sse_event("turn_start", {"prompt": req.prompt}))

                        result = harness.run(req.prompt)

                        # Emit artifact events for each step produced
                        for step in harness._orchestrator._steps:
                            if step.status == "success" and step.envelope:
                                data = step.envelope.get("data", {})
                                relay.push(format_sse_event("artifact", {
                                    "id": step.output_path.stem if step.output_path else "",
                                    "data_type": data.get("data_type", "unknown"),
                                    "format": data.get("format", "unknown"),
                                    "crs": data.get("crs") or data.get("output_crs"),
                                    "bbox": data.get("bbox") or data.get("bounds"),
                                }))

                        relay.push(format_sse_event("turn_end", {
                            "status": "success",
                            "steps": len(harness._orchestrator._steps),
                        }))
                        relay.push(format_sse_event("done", {"status": "complete"}))
                    except Exception as exc:
                        relay.push(format_sse_event("error", {"message": str(exc)}))
                    finally:
                        relay.push(None)  # sentinel

                # Submit to thread pool and track the future.
                future = asyncio.get_running_loop().run_in_executor(
                    None, run_orchestrator,
                )

                async for event in relay:
                    yield event

                # Wait for the executor future to complete before releasing
                # the lock. This ensures the orchestrator thread is fully
                # done mutating state before another request can acquire
                # the session.
                if future is not None:
                    await future
            finally:
                # Always release the session lock — after the orchestrator
                # thread has completed (or errored).
                sm.release(req.session_id)

        return StreamingResponse(
            event_generator(),
            media_type="text/event-stream",
        )

    return app
