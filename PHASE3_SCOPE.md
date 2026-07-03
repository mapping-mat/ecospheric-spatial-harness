# Phase 3 Scope — Web UI (Thin First Slice)

> Drafted 2026-07-03. Revised after Sonnet 5 review (persistence already built, session lifecycle unresolved, sync→async bridge needs concrete design).

## Design Decisions (from Pilgrim)

1. **rio-tiler** — add as dependency (implies rasterio too). In-process tile serving.
2. **Frontend** — vanilla JS + Vite. No framework.
3. **Registry persistence** — included in Phase 3. Already implemented in `artifact_registry.py` (persist/load/cleanup_orphans/atomic writes/relative paths/crash recovery). Needs wiring into orchestrator only.
4. **Scope** — thin first slice. Chat + map. Provenance sidebar, cancellation, ASK_USER inline deferred to Phase 3.4.

---

## Phase 3.1a — Wire Existing Persistence into Orchestrator

**Status:** Persistence is already implemented in `artifact_registry.py` — `persist()`, `load()`, `cleanup_orphans()`, atomic temp+rename, relative-path portability, 8+ tests including crash recovery. The orchestrator does NOT currently call these methods. This slice is wiring only.

### Scope

- Call `registry.load()` on `Harness.__init__` when session dir + registry.json exists
- Call `registry.persist()` after each successful step (after artifact registration)
- Call `registry.persist()` after undo/redo operations
- Call `registry.cleanup_orphans()` on session startup (after load)
- Ensure `persist()` is NOT called on rejected/cancelled/failed steps (don't persist garbage state)
- `Harness` gains `save_state()` convenience method (delegates to `registry.persist()`)
- Tests: verify persist called after successful step, NOT called after rejected step, load restores state across Harness instances, orphan cleanup runs on startup

### Files (modify only)

- `ecospheric_harness/orchestrator.py` — add persist/load calls
- `ecospheric_harness/__main__.py` — call load on init if session exists
- `tests/test_registry_persistence.py` — new test file for wiring-level tests (existing tests in `test_artifact_registry.py` already cover the persistence mechanism itself)

### Out of scope

- Multi-session management (3.1b)
- Registry schema migration (YAGNI)

---

## Phase 3.1b — Session/Harness Lifecycle Manager

The current `WorkspaceManager` acquires `flock(LOCK_EX | LOCK_NB)` on init and holds it for the object's lifetime. This is a process-level lock, not per-operation. The FastAPI backend needs a session manager that:

1. Caches a `Harness` instance per `session_id` (dict-based, not per-request construction)
2. Serializes concurrent requests to the same session (or rejects with 409 Conflict)
3. Handles session creation (new session dir + WorkspaceManager + ArtifactRegistry)

### Scope

- `SessionManager` class at `ecospheric_harness/session_manager.py`
  - `get_or_create(session_id: str) -> Harness` — returns cached or creates new
  - `create_session() -> str` — generates session_id, creates dir, returns id
  - `get(session_id: str) -> Harness | None` — returns cached or None
  - `_sessions: dict[str, Harness]` — in-memory cache
  - `_locks: dict[str, threading.Lock]` — per-session serialization (NOT the flock — that's for the WorkspaceManager's lifetime)
  - Thread-safe dict access (multiple FastAPI workers may share process)
- Concurrent request to same session → 409 Conflict (don't queue silently)
  - `is_busy(session_id) -> bool` — checks if a request is in-flight
  - If busy, return 409 with clear message
  - If not busy, acquire per-session lock, process request, release
- Session creation endpoint: `POST /api/session` → `{session_id}`
- Session list endpoint: `GET /api/sessions` → `[{session_id, created_at, artifact_count}]`

### Design decision: 409 over queueing

If two requests hit the same session simultaneously, reject the second with 409. Rationale: the orchestrator mutates the registry, runs subprocesses, and writes files — concurrent mutations would corrupt state. Queueing adds complexity for no user benefit in a single-user tool. The frontend can retry on 409.

### Files

- `ecospheric_harness/session_manager.py` — new
- `tests/test_session_manager.py` — new

### Out of scope

- Session eviction/timeout (Phase 4)
- Multi-process session sharing (single-process uvicorn for now)

---

## Phase 3.2 — FastAPI Backend

Wrap the Harness in a FastAPI app. SSE streaming from provider's `stream()`. Artifact serving for map display.

### Sync→Async Execution Model (design note)

The orchestrator loop is entirely synchronous: `httpx.Client` for model calls, `subprocess.run()` for tool execution, synchronous preflight checks, synchronous output validation. Wrapping this in FastAPI's async event loop requires a concrete bridge pattern:

**Pattern: Thread pool + asyncio.Queue relay**

```
FastAPI endpoint (async)
  → anyio.to_thread.run_sync(orchestrator.step, prompt)
    → orchestrator runs sync: preflight → subprocess → validate → register
    → pushes events to a thread-safe queue
  ← SSE generator drains queue as async, yields to client
```

- One worker thread per orchestration step (not per SSE chunk)
- `asyncio.Queue` for event relay (thread-safe via `loop.call_soon_threadsafe`)
- Thread pool sizing: FastAPI/Starlette default is 40 threads. With 300s subprocess timeout, max 40 concurrent blocking requests. For single-user tool, this is fine. Document as a ceiling.
- The provider's `stream()` method stays sync — it's called from within the worker thread

**Alternative considered:** Rewrite provider to use `httpx.AsyncClient`. Rejected — would cascade through orchestrator, executor, preflight (all sync). Not worth the rewrite for a single-user tool.

### Endpoints

- `POST /api/session` — create new session, return `{session_id}`
- `GET /api/sessions` — list active sessions
- `POST /api/chat` — accept `{session_id, prompt}`, stream SSE response
  - SSE events:
    ```
    event: turn_start
    data: {"step": 1, "intent": "search_osm_buildings"}

    event: tool_call
    data: {"tool": "edd", "command": "search_osm", "params": {...}}

    event: artifact
    data: {"id": "search_osm_001", "data_type": "vector", "format": "geojson", "crs": "EPSG:4326", "bbox": [...]}

    event: turn_end
    data: {"step": 1, "status": "success"}

    event: done
    ```
  - If session is busy (in-flight request) → 409 Conflict
  - If session doesn't exist → 404
- `GET /api/session/{id}/artifacts` — list all named artifacts (id, type, format, crs, bbox)
- `GET /api/session/{id}/state` — current turn state, warnings, recent artifacts
- `GET /api/artifact/{id}/preview` — serve vector as GeoJSON, raster metadata JSON
- `GET /api/artifact/{id}/tiles/{z}/{x}/{y}.png` — rio-tiler tile endpoint (raster only)

### Raster serving via rio-tiler

- rio-tiler `Reader` opens COG files directly from session dir via GDAL/rasterio
- Phase 2.5 defaults raster output to COG — good pairing
- GDAL env: rio-tiler/rasterio inherits the process env, so `GDAL_CACHEMAX` from Harness config applies. `RLIMIT_AS` applies to the worker thread's process (same process). Note: rasterio's memory usage during tile generation is additive with any concurrent subprocess execution.
- Vector preview: read via `geopandas.read_file()` → `to_json()` → return as `application/json`

### CLI integration

- `--web` CLI flag to launch FastAPI (uvicorn) instead of CLI loop
- `--port` flag (default 8000)
- `--host` flag (default 127.0.0.1)

### Files

- `ecospheric_harness/web/__init__.py`
- `ecospheric_harness/web/app.py` — FastAPI app, routes, SSE relay
- `ecospheric_harness/web/sse.py` — SSE event formatting, queue relay helpers
- `ecospheric_harness/web/tiles.py` — rio-tiler integration for raster tiles
- `ecospheric_harness/__main__.py` — add `--web` flag, launch uvicorn
- `tests/test_web_app.py` — endpoint tests with TestClient
- `tests/test_web_sse.py` — SSE streaming tests
- `tests/test_web_tiles.py` — tile serving tests

### Dependencies to add

```
# pyproject.toml
fastapi >= 0.115
uvicorn >= 0.30
rio-tiler >= 7.0
# rasterio comes as a dependency of rio-tiler
# anyio is vendored by starlette/fastapi — no new dep
```

### Out of scope (Phase 3.4+)

- Cancellation (SIGTERM/SIGKILL, model loop abort)
- ASK_USER inline prompts
- Provenance sidebar
- WebSocket (SSE is sufficient for unidirectional streaming)
- Multi-user auth

---

## Phase 3.3 — Frontend SPA (Vanilla JS + Vite)

Single-page app. Chat bar + map. Purpose-built.

### Scope

- Vite dev server with proxy to FastAPI backend
- Single `index.html` + `main.js` + `style.css`
- Layout:
  - Main area: Leaflet map (full height)
  - Bottom: chat input bar (text input + send button)
  - Above chat bar: message history (scrollable, collapsible)
- Chat flow:
  - User types prompt → POST /api/chat
  - SSE events stream in: render turn_start, tool_call, artifact, turn_end, done
  - Artifacts appear on map: vector as GeoJSON layer, raster as tile layer
- Artifact listing: compact list below map or in a collapsible panel
  - Click artifact → zoom to extent on map
  - Show id, type, CRS, bbox
- Session creation: on page load, POST /api/session to get session_id
- No provenance sidebar (Phase 3.4)
- No cancellation button (Phase 3.4)
- No ASK_USER inline (Phase 3.4)

### Map rendering

- Vector (GeoJSON): `L.geoJSON()` with default styling, auto-fit bounds
- Raster (COG/GeoTIFF): tile layer via `/api/artifact/{id}/tiles/{z}/{x}/{y}.png`
  - rio-tiler handles reprojection + resampling server-side
  - Leaflet just displays XYZ tiles
- CRS: Leaflet defaults to EPSG:3857. Server returns GeoJSON in EPSG:4326 (reproject server-side if needed). Raster tiles are EPSG:3857 via rio-tiler.

### Files

- `frontend/index.html`
- `frontend/main.js` — app entry, chat logic, SSE handling
- `frontend/map.js` — Leaflet setup, layer management
- `frontend/style.css` — layout, chat bar, map sizing
- `frontend/vite.config.js` — Vite config with API proxy
- `frontend/package.json` — Vite + Leaflet deps

### Out of scope (Phase 3.4+)

- Provenance DAG visualization
- Cancellation button
- ASK_USER inline prompts
- Session switcher
- Dark mode (maybe)

---

## Phase 3.4 — Provenance, Cancellation, ASK_USER (deferred)

Not in this scope. Will be specced after 3.1-3.3 are working.

---

## Slice Plan

| Slice | What | Dispatch | Tests |
|-------|------|----------|-------|
| 3.1a | Wire existing persistence into orchestrator | impl + test + judge | ~8 |
| 3.1b | Session/Harness lifecycle manager | impl + test + judge | ~12 |
| 3.2 | FastAPI backend + SSE + tiles | impl + test + judge | ~25 |
| 3.3 | Frontend SPA + chat + map | impl (manual test) | — |

**Sequencing:** 3.1a → 3.1b → 3.2 → 3.3. All sequential (each depends on the previous).

3.1a is small (wiring only, persistence mechanism already built + tested). 3.1b is new work but bounded (one class, one concern). 3.2 is the largest slice — may split into 3.2a (endpoints + SSE relay) and 3.2b (rio-tiler integration) if needed. 3.3 is frontend, manual verification + maybe Playwright smoke tests.

## Dependencies to Add

```
# pyproject.toml
fastapi >= 0.115
uvicorn >= 0.30
rio-tiler >= 7.0
# rasterio comes as a dependency of rio-tiler
# anyio is vendored by starlette/fastida — no new dep
```

## Key Risks

1. **rasterio installation** — has been intentionally avoided so far. rio-tiler pulls it in. May need GDAL version alignment with system libgdal. Test install early (before 3.2).
2. **Sync→async bridge** — orchestrator is fully sync (httpx.Client, subprocess.run, sync preflight). FastAPI is async. Concrete pattern: `anyio.to_thread.run_sync` for orchestration steps, `asyncio.Queue` for SSE event relay. Worker thread pushes events, async generator drains.
3. **Per-session locking** — `WorkspaceManager` holds `flock(LOCK_EX | LOCK_NB)` for object lifetime. Solution: `SessionManager` caches one `Harness` per `session_id`, concurrent requests to same session → 409 Conflict.
4. **Subprocess timeout vs thread pool** — orchestrator subprocess calls can block for up to 300s (default `--subprocess-timeout`). FastAPI/Starlette default thread pool is 40 threads. For single-user tool, 40 concurrent blocking requests is fine. Document as a ceiling.
5. **Frontend dev workflow** — Vite dev server + FastAPI backend. Need proxy config. Not a build risk, just plumbing.
