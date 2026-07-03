# ESP Roadmap — Ecospheric Spatial Platform

> Revised 2026-07-03. Four review rounds (Gemini 3 Flash, Opus 4.8 ×2, MiniMax M3). Phases 0–3 complete.

## Current State

Four packages built, tested, in private GitHub repos:

| Package | Role | Tests |
|---------|------|-------|
| **ETP** | Shared protocol: envelopes, command descriptors, parameter schemas, error types | 271 |
| **EDD** | Data discovery/fetch — 9 source plugins (OSM, STAC, geoBoundaries, etc), 13 commands | 744 |
| **ESE** | Geospatial processing — 96 commands across raster, vector, pointcloud, hydro, proj | 1,917 |
| **ESH** | Multi-turn LLM orchestration — intent protocol, named artifact registry, undo/redo, preflight, web UI | 608 |

**Total: 3,540 tests. All passing.**

## Phase Completion Summary

| Phase | Status | Date | Key Commits |
|-------|--------|------|-------------|
| 0a — Environment & Installation | ✅ | 2026-07-01 | (initial) |
| 0b — WorkspaceManager | ✅ | 2026-07-02 | `27c916d` |
| 0c — Security Foundation | ✅ | 2026-07-02 | `19aa441` |
| 0.5 — Named Artifact Registry | ✅ | 2026-07-02 | `081ad54`, `c4ffc49` |
| 1a — Eval Harness | ✅ | 2026-07-02 | `fad7cf8` |
| 1b — First Real NL Query | ✅ | 2026-07-02 | `f1f0d96`→`5dc3bf4` |
| 1.5 — Provider Abstraction | ✅ | 2026-07-03 | `e59bab3` |
| 2.1 — Preflight Foundation (Checks 1–8) | ✅ | 2026-07-03 | `ad25ae2` |
| 2.2 — Output Validation | ✅ | 2026-07-03 | `0e73cfc` |
| 2.3 — Memory Budget + Command Classification | ✅ | 2026-07-03 | `a489f01` |
| 2.4 — WorkspaceManager Extensions | ✅ | 2026-07-03 | `a489f01` |
| 2.5 — COG Default + Integration Tests | ✅ | 2026-07-03 | `2d36e70` |
| 2.6 — Post-Review Fixes (Sonnet 5) | ✅ | 2026-07-03 | `5a96960` |
| 3.1a — Registry Persistence Wiring | ✅ | 2026-07-03 | `e043321` |
| 3.1b — SessionManager | ✅ | 2026-07-03 | `1584c21` |
| 3.2 — FastAPI + SSE + rio-tiler Tiles | ✅ | 2026-07-03 | `bad6ecb`, `4c68af3`, `e179436` |
| 3.3 — Frontend SPA | ✅ | 2026-07-03 | `1072e96` |
| 3.3b — E2E Smoke Test + Bug Fixes | ✅ | 2026-07-03 | `00bb09b`, `00eed0d` |
| 3.4 — Provenance, Cancellation, ASK_USER | ⬜ DEFERRED | — | — |
| 4 — Hardening & Polish | ⬜ | — | — |

**E2E verified:** Playwright smoke test — search → reproject → buffer pipeline completes through browser with SSE streaming, 3 artifacts produced, map + artifact panel populated.

---

## Revised Roadmap

> **Note:** Phases 0–3.3 are COMPLETE. Checkboxes below preserve the original design spec.
> For granular per-phase status, commits, and test counts, see `DEVELOPMENT.md`.
> Remaining work: Phase 3.4 (deferred) and Phase 4 (hardening).

### Phase 0 — Environment, Installation, Security & Workspace ✅ COMPLETE (2026-07-02)

**Goal:** Get tools installed and pinned, establish the security envelope, and build the `WorkspaceManager` that all subsequent phases depend on.

#### 0a — Environment & Installation

- [ ] Create isolated venv for ESP
- [ ] Pin all dependencies in a lockfile (`uv.lock`) — no unpinned dev installs
- [ ] Install ETP, EDD, ESE in dev mode
- [ ] **Verify single resolved ETP version** — EDD and ESE must resolve to the same ETP install
- [ ] Verify `edd` and `ese` are on PATH
- [ ] **Resolve tool paths explicitly at startup** — `shutil.which()` at init, log absolute paths. Do not trust ambient PATH.
- [ ] Verify `edd describe --all` and `ese describe --all` produce valid ETP envelopes
- [ ] Confirm GDAL system lib consistency (same `libgdal` version)
- [ ] **Confirm PROJ data consistency** — same `proj.db`, same datum grid files. Verify with `projinfo` output comparison.
- [ ] **Check for GDAL Python-binding vs CLI mismatch**

#### 0b — WorkspaceManager (base, before security and registry)

Built here because Phase 0c (security) and Phase 0.5 (registry) both depend on it. Phase 2a extends it with memory accounting and session cleanup.

- [ ] `WorkspaceManager` class: creates and manages `~/.esp/sessions/{session_id}/`
- [ ] **Path confinement enforced** — every read/write path canonicalized via `realpath()` (resolves symlinks, not just rejects `..`/`~`). Hard fail on any path outside session dir. TOCTOU note: confinement check happens at preflight; subprocess writes are not re-checked at syscall level (documented residual risk).
- [ ] Disk accounting: track total bytes, enforce `disk_limit_gb`
- [ ] `--workspace` CLI flag for custom location
- [ ] **Per-session lock** — `flock()` on a session-level lockfile. Prevents concurrent mutation of the same session's workspace (registry, id counter, idempotency cache) when multiple processes or web requests access it. Phase 3's FastAPI backend requires this.

#### 0c — Security Foundation

Threat model: single trusted user on a single host. Prompts and tool outputs are NOT trusted. Model output is treated as untrusted input to a TCB-protected tool executor.

**Attack surface:**
1. Prompt injection via data (OSM `note=...` field, STAC metadata)
2. Path traversal (model emits `../../../etc/cron.d/something`)
3. Resource exhaustion (model emits `buffer_distance: 1e15`)
4. Destructive operations (model overwrites files outside session)
5. Data exfiltration via URL sources
6. API key exposure in tool error messages
7. SSRF (model emits URL to `169.254.169.254` or internal hosts)

**Security controls:**
- [ ] **Path confinement** — via `WorkspaceManager` (0b). `realpath()` canonicalization, reject escapes. Hard fail.
- [ ] **Subprocess hardening:**
  - Wall-clock timeout (configurable, default 300s)
  - Max output bytes (configurable, default 100MB)
  - `RLIMIT_AS` (address space) — the actual kernel-level memory guard. This is what gives the Phase 2d memory budget teeth. Note: `RLIMIT_CPU` sums CPU-seconds across threads — a multithreaded GDAL op hits the ceiling in wall-clock/N time. Use `RLIMIT_CPU` cautiously or not at all for GDAL-heavy ops.
  - `RLIMIT_NPROC` to prevent fork bombs
  - `cwd=workdir`, minimal env (strip API keys, set `GDAL_CACHEMAX` to a sensible fraction of available RAM)
- [ ] **SSRF mitigation** — block link-local (169.254.0.0/16), RFC-1918 (10/8, 172.16/12, 192.168/16), and cloud metadata IPs in any model-emitted URL. Cheap preflight check, belongs with path confinement, not Phase 4.
- [ ] **Destructive op guard** — any command that overwrites a file requires confirmation. CLI: prompt. Web UI: ASK_USER.
- [ ] **Tool output sanitization** — strip env vars, API keys, absolute home paths from subprocess stdout/stderr before feeding to model. Redaction filter (`*_KEY`, `*_TOKEN`, `*_SECRET`, `Bearer *`).
- [ ] **Tool-result role separation** — tool results marked as `role: "tool"` in message history, not `role: "user"`. This is defense-in-depth, NOT the primary injection defense. The real injection defense is that every destructive/egress action routes through preflight ASK_USER. Role separation is a structural signal to the model that tool output is data, not instructions.
- [ ] **Egress allowlist (deferred to Phase 4)** — for v1, SSRF IP blocking (above) is the mitigation. Full egress allowlist is a Phase 4 stretch if needed.

**Key insight:** Preflight is both a correctness layer AND a security layer. The same machinery serves both — designed together from day one.

---

### Phase 0.5 — Named Artifact Registry ✅ COMPLETE (2026-07-02)

**Goal:** Replace the two-artifact sliding window with a named artifact registry before building anything on top of it.

The current implementation physically deletes artifacts when they fall outside the two-slot window. On the third `store()`, the oldest artifact is gone — the file is deleted, not just unreferenced. Re-running means re-executing from scratch.

**Why this is a design defect:** branching, multi-input downstream, named checkpoints, parameter sweeps, multi-output intents — none are expressible when files are deleted after 2 slots.

#### Registry Design

- [ ] Every artifact gets a stable, human-readable id: `clip_001`, `slope_003`, `search_osm_001`
- [ ] The model references any past artifact by id in subsequent intents
- [ ] **System prompt context:** 2 most recent artifacts shown in full detail (format, CRS, extent, bounds) PLUS a compact list of all named artifacts (id, type, one-line summary). **Collections (sweeps/fan-out) collapse to a single group entry** (`buffer_sweep_001 [58 items]`) to prevent unbounded prompt growth.
- [ ] Artifacts are NOT deleted when they fall out of the recent window — they remain on disk and addressable
- [ ] **Disk eviction policy:** when `disk_limit_gb` is hit, evict oldest artifacts that are (a) NOT referenced by the current turn's intent AND (b) NOT reachable in the provenance DAG (no descendant artifact lists them as a parent). Eviction respects provenance ancestry and rollback reachability, not just current-turn references.
- [ ] Undo/redo is per-artifact, not global
- [ ] **Idempotency (scoped):** re-running a step with identical params + identical input artifact id + same tool version is a no-op (return cached output). Only applies to commands flagged `idempotent: true`. EDD fetch/network commands = `false`. Stochastic ESE ops = `false` unless seed is pinned. Idempotency cache keyed on (tool_name, tool_version, command, params_hash, input_artifact_id).
- [ ] Per-command `idempotent: bool` flag in the registry, derived from command metadata (network/fetch/stochastic = false, pure deterministic transforms = true)

#### Registry Persistence

- [ ] Registry state serialized to `~/.esp/sessions/{session_id}/registry.json` (or SQLite) — the id→path map, provenance DAG, idempotency cache hashes, and per-session metadata (tool versions, model version, system prompt version)
- [ ] **Crash recovery:** on startup, if a session dir exists with a registry file, load it. Orphaned files (in session dir but not in registry) are garbage-collected.
- [ ] **Concurrency:** per-session lock (from 0b `flock()`) serializes all registry mutations. Web backend (Phase 3) must acquire the lock before any registry read-modify-write.

#### Id Artifact Determinism for Eval Fixtures

- [ ] Artifact ids are counter-based and deterministic within a session (incrementing from 001). Eval fixtures match on **structural equivalence** (intent sequence + artifact properties), not on raw id strings. If a fixture needs to reference an artifact id, it uses a placeholder (`$ARTIFACT_1`) that the test harness resolves to the actual id after execution.

---

### Phase 1 — First NL Query + Evaluation Harness ✅ COMPLETE (2026-07-02)

**Goal:** Run a real NL geospatial query and prove it works — with automated regression coverage.

#### 1a — Evaluation Harness

- [ ] Build fixture set of ~20-30 cases: `(prompt → expected intent sequence → expected artifact properties)`
  - Simple single-step: "Search OSM for water features near Chico, CA"
  - Multi-step chain: "Search OSM for buildings near Chico, then buffer by 500m"
  - Raster ops: "Reproject this DEM to EPSG:3857"
  - Named artifact reference: "Search OSM for buildings, buffer by 500m, then clip the original buildings to the buffer extent"
  - Negative cases: impossible request, ambiguous request, non-existent artifact id
  - Security cases: prompt injection in tool output, path traversal attempt, resource exhaustion attempt, SSRF attempt
- [ ] Each case: `temperature=0`, assert correct intent sequence + final artifact exists + non-empty + correct CRS + **spatially correct extent**
- [ ] Run N=3 per case for variance
- [ ] Record baseline token cost and latency

#### 1b — First Real Query ✅ COMPLETE (2026-07-02) (already marked)

- [x] Set `OPENROUTER_API_KEY` in env
- [x] `--list-tools` and `--list-intents` verification (2 tools, 11 intents)
- [x] First single-step query (OSM water features near Chico)
- [x] First multi-step query (search buildings → reproject → buffer 500m)
- [x] Verify spatial correctness of output (2,526 features, UTM 10N, 20.6x area expansion)
- [x] Eval harness passes all fixtures (25/25)

**Validation criteria:** 2-3 step pipeline produces valid spatial output in the correct location, eval harness passes all fixtures. ✅

**Bugs found and fixed during Phase 1b:**
- Redaction regex corrupting JSON output (security.py `[/\S]*` → `[A-Za-z0-9_\-./]*`)
- Parameter name normalization (`params.py` — strip `--` prefixes, hyphens→underscores)
- Positional input fallback for commands without `--input` param
- ESE GeoParquet read via magic bytes (`_is_parquet_file` — extension + PAR1 sniff)
- ESE GeoParquet write via `gpd.to_parquet` (bypass GDAL's libduckdb.so dependency)
- Artifact ID resolution from `input` param (promote to `input_artifact_id`)
- Double-input serialization (strip `input` from serialized params when artifact routed)
- Structural input auto-resolution (strip `input` from `required_params` + schema `required`)
- Output file extension based on artifact format (`.parquet` not `.bin`)

**Test count:** 345 (ESH) + ESE io_utils tests

---

### Phase 1.5 — Provider Abstraction ✅ COMPLETE (2026-07-03)

**Goal:** Decouple from OpenRouter before coupling deepens.

#### Protocol

```python
class ModelProvider(Protocol):
    def generate(self, system_prompt: str, messages: list[dict], tool_def: dict) -> ModelResponse: ...
    def stream(self, system_prompt: str, messages: list[dict], tool_def: dict) -> Iterator[StreamChunk]: ...
```

- OpenAI wire format is the internal interface (explicit). Ollama normalizes into it.
- Streaming from day one (Phase 3 needs SSE).
- No LLM frameworks.

#### Types

```python
@dataclass
class ModelResponse:
    tool_calls: list[dict]
    tool_call_id: str
    usage: TokenUsage
    finish_reason: str

@dataclass
class TokenUsage:
    input_tokens: int
    output_tokens: int

@dataclass
class StreamChunk:
    delta: str
    tool_call_delta: dict | None
    finish_reason: str | None

class ProviderError(Exception):
    error_type: str  # "rate_limit", "context_length", "parse_failure", "timeout", "auth", "unknown"
    retryable: bool
    retry_after: float | None
```

#### Implementation

- [ ] Define types
- [ ] `OpenRouterProvider` (wraps existing `_call_model`, adds streaming)
- [ ] `OllamaProvider` (local, normalizes tool calls)
- [ ] Refactor `Orchestrator._call_model` → delegate to `ModelProvider`
- [ ] `--provider` and `--model` CLI flags
- [ ] `Harness.__init__` accepts `provider: ModelProvider`
- [ ] Run eval harness against both providers

---

### Phase 2 — Spatial Validation + Data-Size Strategy ✅ COMPLETE (2026-07-03)

**Goal:** Prevent "Success" on garbage outputs and prevent OOM on large data.

#### 2a — WorkspaceManager Extensions

Extends the base from Phase 0b:

- [ ] **Memory accounting:** track estimated peak RSS per command (see 2d)
- [ ] **Session cleanup:** old sessions purged after configurable TTL (default 7 days)
- [ ] **Cancellation cleanup:** if a step is cancelled mid-execution, delete partial output. Partial/unregistered temp output is NOT protected by Principle #10 — only registered artifacts are protected. Cancelled steps must NOT populate the idempotency cache.

#### 2b — Spatial Preflight Validation

**Preflight checks (priority order):**

1. **Multi-input CRS agreement** — binary ops: both inputs same CRS?
2. **Extent intersection** — binary ops: do inputs actually overlap? Zero intersection = the 1×1 black box cause.
3. **Unit awareness for distance ops** — geographic CRS + linear distance = auto-fix (reproject to projected, continue)
4. **Extent containment** — requested bounds within input extent?
5. **CRS validity** — target CRS exists and is appropriate?
6. **Resolution sanity** — within 3 orders of magnitude of input?
7. **Band validity** — expected bands present?
8. **Geometry validity** — valid (not self-intersecting)?
9. **Categorical resampling guard** — classified raster + bilinear/cubic = warn
10. **Datum transformation check** — flag transforms lacking defined pipeline
11. **Pixel alignment / grid-snapping** — raster algebra: same CRS, same res, aligned origins?
12. **NoData awareness** — does op account for NoData?
13. **Path confinement** (from 0b/0c) — model-emitted paths inside session dir?
14. **SSRF check** (from 0c) — model-emitted URLs don't target internal/metadata IPs?

#### `PreflightResult`

```python
@dataclass
class PreflightResult:
    check: str
    resolution: Resolution
    message: str
    diagnostics: dict  # machine-readable: actual extent, CRS, resolution, etc.

class Resolution(Enum):
    PASS = "pass"
    AUTO_FIX = "auto_fix"
    ASK_USER = "ask_user"
    MODEL_DISCRETION = "model_discretion"
    BLOCK = "block"
```

**The model that hallucinated the bad param is not the arbiter of whether it's acceptable.**

#### 2c — Output Validation

- [ ] Output file exists and is non-empty
- [ ] Rasters: dimensions > 1×1, valid CRS, NoData set
- [ ] Vectors: feature count > 0, valid geometries, CRS set
- [ ] **Output-vs-intent validation:** extent ⊆ expected, CRS == requested, geometry type == expected
- [ ] Failed validation → step marked **failed** (not success), diagnostics surfaced to model

#### 2d — Data-Size Strategy & Memory Safety

**Size regimes:**

| Workflow | Typical size | Current architecture |
|----------|-------------|---------------------|
| City-scale vector | <100 MB | Works |
| County DEM (10m) | 1-5 GB | Painful |
| State Sentinel-2 mosaic | 50-500 GB | Broken — must be tiled |
| State LiDAR | 50-500 GB | Broken |
| Full OSM planet | 80+ GB | Broken |

**Data-size profile per ESE command:**

Not a static "input size range / output size range" — that's not meaningful (a reproject's output depends on target resolution, a clip's on the mask). What IS command-intrinsic is the **memory behavior class** and a **multiplier**.

- [ ] **Memory behavior class per command:** `streaming` (windowed read, low RSS), `full_load` (reads entire array into RAM), `depends` (varies by params)
- [ ] **Memory multiplier per command:** peak RSS ≈ N × input_array_bytes. Default conservative multiplier (e.g. 3×) for all; override for known classes. Most GDAL/PDAL ops have well-known memory behavior — classify by algorithm, not by benchmarking each of 96 commands.
- [ ] **Runtime RSS estimate:** `input_dims × dtype × bands × command_multiplier`, computed at preflight from live input artifact metadata (not from a stored size range)
- [ ] **Memory budget preflight check:** if estimated RSS > `memory_limit_mb`, BLOCK with `check: "memory_budget"`. Surface actual input size and estimated cost in diagnostics.
- [ ] **`RLIMIT_AS` enforcement** (from Phase 0b) — kernel-level backstop if the estimate is wrong. The estimate is the preflight guard; `RLIMIT_AS` is the runtime guard.
- [ ] **COG for intermediate rasters** — default output format for raster-producing commands. Enables efficient windowed reads by downstream commands and rio-tiler in the web UI.
- [ ] **VRT awareness** — document whether ESE commands support virtual rasters as input
- [ ] **`GDAL_CACHEMAX`** — set to ~25% of available RAM by default, configurable per-session
- [ ] **Documented non-goals for v1:** no S3/GCS/Azure Blob (local FS only), no distributed processing, no automatic tiling of oversized rasters

---

### Phase 3 — Web UI ✅ COMPLETE (2026-07-03)

**Goal:** Chat bar + map window. Purpose-built, not notebook-based.

#### Backend

- [ ] FastAPI app wrapping `Harness`
  - `POST /api/chat` — streaming response (SSE via provider's `stream()`)
  - `GET /api/session/{id}/artifacts` — list all named artifacts
  - `GET /api/artifact/{id}/preview` — serve for map display
  - `GET /api/session/{id}/provenance` — provenance chain
  - `POST /api/session/{id}/preflight` — expose preflight results
  - `POST /api/session/{id}/cancel` — cancel in-progress execution
- [ ] **Per-session lock** (from 0b) — all registry mutations serialized via `flock()`. Web requests acquire lock before read-modify-write.

#### Frontend

- [ ] Single-page app
  - Chat bar at bottom, message history above
  - Map window (Leaflet) occupies main area
  - Artifact provenance sidebar (collapsible) — all named artifacts with id, type, CRS, extent
  - Vector outputs render on map, raster via rio-tiler tile layer
  - Preflight ASK_USER prompts surface inline in chat
  - Cancellation button

#### Cancellation

- [ ] **SIGTERM to subprocess** → 5s grace → SIGKILL
- [ ] **Model loop abort** — abort HTTP request to provider
- [ ] **Partial output cleanup** — unregistered temp output deleted. Cancelled step marked `cancelled` in provenance, not `failed`. Does NOT populate idempotency cache.

#### Raster serving

- [ ] **rio-tiler** (in-process) — serves tiles from GeoTIFFs/COGs. Works with COG outputs from 2d.
- TiTiler only if performance demands it. GeoTIFF.js for small thumbnails only.

#### Tech

- Frontend: vanilla JS + Vite or Preact
- Map: Leaflet (vector via GeoJSON, raster via rio-tiler)
- Session persistence: save/resume via registry persistence (from 0.5)

**NOT building:** notebooks, Streamlit, Jupyter, multi-user auth.

---

### Phase 4 — Hardening & Polish

#### Orchestration Loop Circuit Breaker

- [ ] **`max_turns`** — hard cap on orchestration turns per session (already in config, now framed as safety limit). Default 20.
- [ ] **`max_tokens` / `max_cost_usd`** — cumulative token/cost ceiling per session. When exceeded, halt the loop and surface to user. Prevents a confused model from burning unbounded API spend retrying failed intents.

#### Performance

- [ ] **Discovery caching** — cache `describe --all` to `~/.esp/discovery_cache.json`. Version-checked invalidation. `--refresh` flag. Target <500ms startup.
- [ ] Profile and optimize hot paths

#### Error Recovery

- [ ] **Model retry with spatial context** — feed structured `PreflightResult.diagnostics` to the model. "Clip failed because mask extent [...] does not intersect input extent [...]. Input covers: {actual_extent}."
- [ ] **Transaction rollback** — with named registry: discard artifacts created after step N, restore session to that point. Evicted ancestors are not restorable — rollback is best-effort after eviction. State this explicitly.

#### Pipeline Patterns

- [ ] **Batch intents** — model emits a plan (list of intents) in one turn. Harness executes sequentially with proper artifact naming. Reduces round-trips.
- [ ] **Fan-out / parameter sweep** — "run at thresholds 100, 500, 1000" → N artifacts, each numbered (`buffer_001_t100`, etc.). Results surface as a **collection** in the registry (single group entry in prompt context, expandable).
- [ ] **Conditional execution** — if preflight is ASK_USER, model can branch. Documented as a pattern, not a new control-flow primitive.

**NOT adding:** general-purpose loop/while, Turing-complete pipeline language. Model handles iteration via multi-turn conversation.

#### Provenance & Reproducibility

- [ ] **Provenance export** — full chain as reproducible script (CLI or Python)
- [ ] **Reproducibility guarantees:**
  - Pinned random seeds for stochastic ESE ops
  - LLM model version + system prompt version in provenance
  - Tool versions (EDD, ESE, ETP) in provenance
  - GDAL/PROJ/GEOS versions in provenance
- [ ] **Dockerfile** — pinned runtime with specific GDAL/PROJ/GEOS/PROJ-data versions. Enables "reproduce this session in 6 months."
- [ ] **Rollback after eviction** — explicitly documented as best-effort. If an ancestor was evicted, rollback to a pre-eviction state is not possible. Registry records eviction events in provenance.

#### MCP Compatibility

- [ ] **MCP server study** — evaluate ETP as MCP server. Assess mapping cost, value, replace vs complement.

#### Documentation

- [ ] User-facing docs
- [ ] Architecture overview for contributors

---

## What We Rejected (and Why)

| Suggestion | Source | Verdict | Reason |
|-----------|--------|---------|--------|
| Notebooks / IPython | Gemini | ❌ | Pilgrim doesn't use notebooks. |
| Streamlit | Gemini | ❌ | Too constrained for chat + map. |
| `instructor` / `outlines` | Gemini | ❌ | Function calling already gives structured output. |
| LangChain | Gemini | ❌ | Own orchestration loop, framework adds no value. |
| Self-generating intent catalog | Gemini | ⏳ Deferred | Premature at current size. |
| MCP server exposure | Gemini | ⏳ Phase 4 | Not blocking. |
| Discovery caching in Phase 2 | Gemini → Opus | ❌ Phase 4 | Performance concern, not correctness. |
| Two-artifact sliding window | MiniMax | ❌ Replaced | Design defect — physically deletes artifacts. |
| S3/cloud I/O in v1 | MiniMax | ❌ Phase 4 | Documented non-goal. |
| Static per-command size ranges | MiniMax → Opus r2 | ❌ Replaced | Not meaningful — size depends on params. Replaced with memory behavior class + multiplier formula. |
| `RLIMIT_CPU` for GDAL ops | Opus r2 | ⚠️ Cautious | Sums CPU-seconds across threads. Use cautiously or not at all for GDAL-heavy ops. `RLIMIT_AS` is the real memory guard. |

---

## Design Principles (Affirmed)

1. **Intent protocol stays.** Indirection between user language and CLI versioning is worth the mapping tax.
2. **File-path handoff stays.** Raster data can't flow through pipes.
3. **Subprocess execution stays.** EDD and ESE are CLI tools.
4. **One function: `emit_intent`.** Bounded output space. (Phase 4 batch intents extend to a plan, not a new function.)
5. **No LLM frameworks.** ~400 lines of orchestration. Smaller than most framework configs.
6. **Spatial validation before UX.** Pretty map + wrong data < ugly CLI + correct data.
7. **Eval harness before refactoring.** Don't change what you can't measure.
8. **Structured diagnostics, not human strings.** Machine-readable fields for downstream consumption.
9. **The model is not the safety net.** Explicit resolution (auto-fix / ask-user / model-discretion / block).
10. **Named artifacts, not anonymous slots.** Stable ids. No physical deletion while addressable. Sliding window is prompt context, not storage constraint.
11. **Security is preflight.** Same machinery checks spatial correctness AND path confinement AND resource limits AND SSRF. Designed together.
12. **Know your size regime.** Memory behavior class + multiplier per command. Runtime estimate from live input metadata. `RLIMIT_AS` as backstop.
13. **Only registered artifacts are protected.** Pre-registration temp output (cancelled steps, partial writes) is fair game for cleanup.
14. **Loop has a circuit breaker.** `max_turns` + `max_tokens` / `max_cost_usd`. A confused model doesn't burn unbounded spend.
15. **Registry is persistent and locked.** Serialized to disk (crash recovery). Per-session lock (concurrency safety). Not an in-memory afterthought.

---

## Sequencing Summary

```
Phase 0   → Install tools, pin environment, verify GDAL/PROJ
             + WorkspaceManager (session dir, path confinement, disk accounting, per-session lock)
             + security foundation (subprocess hardening incl. RLIMIT_AS, SSRF blocking,
               output sanitization, destructive op guard, threat model)
Phase 0.5 → Named artifact registry (stable ids, no physical deletion, provenance DAG,
             scoped idempotency for deterministic ops, registry persistence + crash recovery)
Phase 1   → Eval harness (20-30 fixtures incl. security + named-artifact cases) + first real NL query
Phase 1.5 → Provider abstraction (streaming + error taxonomy, guardrailed by eval)
Phase 2   → Spatial validation (14 preflight checks, output-vs-intent validation)
             + data-size strategy (memory behavior class + multiplier, runtime RSS estimate,
               COG defaults, RLIMIT_AS backstop)
             + workspace extensions (memory accounting, session cleanup, cancellation cleanup)
Phase 3   → Web UI (chat bar + map window, rio-tiler, cancellation, per-session locking)
Phase 4   → Circuit breaker (max_turns + max_cost), discovery caching, error recovery,
             batch/fan-out intents, reproducibility (Dockerfile, seed pinning), MCP study, docs
```

**Key changes across all review rounds:**
- Provider abstraction moved up (before coupling deepens)
- Spatial validation moved ahead of UX (correctness before prettiness)
- Web UI is custom, not notebook-based
- Eval harness added as Phase 1 prerequisite
- Discovery caching moved to Phase 4
- Provider protocol includes streaming + error taxonomy from day one
- Preflight expanded to 14 checks (multi-input CRS, extent intersection, unit awareness, categorical resampling, datum transformation, pixel alignment, path confinement, SSRF)
- Preflight severity → explicit Resolution enum
- Preflight diagnostics are structured machine-readable fields
- Output validation checks correctness vs. intent
- Phase 0 hardened: PROJ data, lockfile, tool path resolution, ETP version pin
- Phase 0b: Security foundation (path confinement via realpath, subprocess hardening with RLIMIT_AS, SSRF blocking, output sanitization)
- Phase 0.5: Named artifact registry (replaces sliding window, stable ids, provenance-aware eviction, scoped idempotency, persistence + locking)
- Phase 2d: Data-size strategy (memory behavior class + multiplier formula, runtime RSS estimate, COG defaults, RLIMIT_AS backstop)
- Phase 3: Cancellation (SIGTERM→SIGKILL, model loop abort, partial output cleanup, no idempotency cache pollution)
- Phase 4: Pipeline patterns (batch intents, fan-out as collections, conditional execution)
- Phase 4: Reproducibility (Dockerfile, seed pinning, version stamping, rollback-is-best-effort-after-eviction)
- Phase 4: Orchestration loop circuit breaker (max_turns + max_tokens/max_cost_usd)
- Registry persistence + per-session locking (crash recovery, web backend concurrency safety)
- Eviction respects provenance DAG ancestry (no dangling lineage)
- Idempotency scoped to deterministic ops only (fetch/network/stochastic = false)
- Collections collapse in prompt context (sweep = 1 line, not 58)
