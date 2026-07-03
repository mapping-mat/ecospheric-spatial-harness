# Phase 2 — Spatial Validation + Data-Size Strategy (Revised)

> Revised 2026-07-03 after Sonnet 5 review. Addresses: backward-compat hole,
> binary-op file-path blind spot, memory formula gaps, slice coupling,
> checks 9-14 underspecification, output-validation orphan cleanup.

## Current State

- `PreflightChecker` has 3 checks: `check_planar_crs`, `check_disk`, `check_ssrf`
- `PreflightResult` is a simple dataclass: `ok: bool`, `error: str`
- Orchestrator calls preflight checks inline in `_handle_operation()` with 3 duplicated `StepRecord(...)` blocks
- `ArtifactRecord` has: `crs`, `bbox`, `format`, `data_type`, `envelope` (full ETP envelope)
- ETP envelopes contain `data.crs`, `data.bbox`, `data.bounds`, `data.extent`, `data.data_type`, `data.format`
- ESE has 96 commands across raster/vector/pointcloud/hydro/proj
- 408 tests passing

## Design Decisions

### PreflightResult upgrade + pipeline pattern (foundation — must be done together)

Current `PreflightResult` is `{ok, error}`. ROADMAP calls for `{check, resolution, message, diagnostics}`. This is a **whole-file rewrite of `preflight.py`** — every call site uses `PreflightResult(ok=False, error="...")` which won't exist on the new dataclass. The "small slice" framing was wrong; this is structural.

**New PreflightResult:**

```python
class Resolution(Enum):
    PASS = "pass"
    AUTO_FIX = "auto_fix"
    ASK_USER = "ask_user"
    MODEL_DISCRETION = "model_discretion"
    BLOCK = "block"

@dataclass
class PreflightResult:
    check: str = ""
    resolution: Resolution = Resolution.PASS
    message: str = ""
    diagnostics: dict[str, Any] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        """True when resolution is PASS or MODEL_DISCRETION."""
        return self.resolution in (Resolution.PASS, Resolution.MODEL_DISCRETION)
```

**Warnings surfacing:** The orchestrator's turn-state gains a `warnings: list[dict]` field. `MODEL_DISCRETION` results are appended to this list with `{check, message, diagnostics}`. This is wired in the foundation slice, not deferred — the gap between 2.1 and 2.2 was the critical issue.

**Preflight pipeline pattern:** Replace the inline copy-paste `StepRecord` blocks with:

```python
def _run_preflight_checks(
    self, resolved: ResolvedCall, input_artifact: ArtifactRecord | None,
    params: dict[str, Any],
) -> list[PreflightResult]:
    """Run all applicable preflight checks. Returns results in priority order."""
    results: list[PreflightResult] = []
    command = resolved.command

    # 1. CRS agreement (binary ops)
    results.append(self._check_crs_agreement(command, input_artifact, params))
    # 2. Extent intersection (binary ops)
    results.append(self._check_extent_intersection(command, input_artifact, params))
    # 3. Unit awareness (geographic CRS + distance op)
    results.append(self._check_unit_awareness(command, input_artifact))
    # 4. Extent containment
    results.append(self._check_extent_containment(command, input_artifact, params))
    # 5. CRS validity (target CRS)
    results.append(self._check_crs_validity(command, params))
    # 6. Planar CRS (existing, refactored)
    results.append(self._check_planar_crs(command, input_artifact))
    # 7. Resolution sanity
    results.append(self._check_resolution_sanity(command, input_artifact, params))
    # 8. Geometry validity
    results.append(self._check_geometry_validity(command, input_artifact))
    # 9. SSRF (existing, refactored)
    results.append(self._check_ssrf(params))
    # 10. Disk (existing, refactored)
    results.append(self._check_disk(input_artifact=input_artifact))

    return results
```

Orchestrator's `_handle_operation` calls `_run_preflight_checks()` once, scans results:
- First `BLOCK` → append one `StepRecord(status="rejected")`, return error turn
- `MODEL_DISCRETION` results → collect into `warnings` list on the step/turn
- `PASS` → continue

This eliminates the 3× duplicated `StepRecord` blocks and makes adding checks a one-line append.

**Interaction with existing `check_planar_crs`:** The new `check_unit_awareness` (check #3) and existing `check_planar_crs` (check #6) overlap — both detect geographic CRS. Resolution:
- `check_unit_awareness`: For distance/buffer ops on geographic CRS → `AUTO_FIX` (suggest reproject). In Phase 2, AUTO_FIX is treated as BLOCK with a "suggested reproject to {CRS}" message.
- `check_planar_crs`: For any command requiring planar CRS on geographic input → `BLOCK` with "reproject first" message.
- These run in sequence. If `check_unit_awareness` fires (AUTO_FIX/BLOCK), `check_planar_crs` is redundant but harmless (also BLOCK). The model sees the more specific message first.

### Binary-op file-path header reads

**Problem:** The model frequently passes raw file paths for `--mask`/`--by`/`--overlay` instead of artifact IDs. The scope originally skipped binary-op checks for file-path inputs.

**Fix:** Add `_resolve_secondary_input(params)` that:
1. Checks if the param value matches a registered artifact ID → use artifact metadata
2. If it's a file path → do a header-only read:
   - Raster: `rasterio.open(path)` → `.crs`, `.bounds` (cheap, no data load)
   - Vector: `fiona.open(path)` or `gpd.read_file(path, rows=1)` → CRS, total_bounds
   - If file doesn't exist or can't be opened → skip with `MODEL_DISCRETION` warning
3. Returns `(ArtifactRecord-like metadata | None, warning_message)`

This is a required deliverable in Slice 2.1, not a deferred limitation. It directly addresses the ROADMAP's primary failure mode (CRS mismatch / zero-intersection → 1×1 black box).

### Command profile keying

**Problem:** Command names like `"clip"` exist in both raster and vector namespaces with different memory profiles.

**Fix:** Key the classification table by `(command_name, data_type)` tuple:

```python
COMMAND_PROFILES: dict[tuple[str, str], CommandProfile] = {
    ("reproject", "raster"): CommandProfile("full_load", 3.0),
    ("reproject", "vector"): CommandProfile("full_load", 2.0),
    ("clip", "raster"): CommandProfile("streaming", 1.5),   # GDAL windowed
    ("clip", "vector"): CommandProfile("full_load", 2.0),   # geopandas
    ("buffer", "vector"): CommandProfile("full_load", 2.0),
    ("slope", "raster"): CommandProfile("streaming", 1.5),
    # ... etc
}
DEFAULT_PROFILE = CommandProfile("full_load", 3.0)
```

### Memory estimation calibration

**Problem:** `file_size_bytes × multiplier` is a poor proxy for compressed formats (GeoParquet is columnar + compressed; in-memory GeoDataFrame is 3-10× disk size). `dtype` may not be present in all envelopes.

**Fix:**
- Raster: `width × height × bands × dtype_size × multiplier` — `dtype_size` defaults to 4 bytes (Float32) if `data.dtype` is absent from envelope. Documented assumption.
- Vector: Use `feature_count × avg_bytes_per_feature × multiplier` where `avg_bytes_per_feature` defaults to 500 bytes (empirical average for polygon geometries). If `feature_count` is absent, fall back to `file_size_bytes × 5` (compression factor estimate for GeoParquet — conservative). Flag as "low-confidence estimate" in diagnostics.
- Pointcloud: `file_size_bytes × 3` (pointcloud formats are typically uncompressed or lightly compressed).
- All estimates include a `confidence: "high"|"low"` field in diagnostics based on whether the input metadata was complete.
- **Calibration note:** These multipliers are initial heuristics. Phase 4 should instrument actual peak RSS per command and calibrate. The preflight check is still valuable as a first-pass guard even with imperfect estimates — RLIMIT_AS is the backstop.

### Output-validation orphan cleanup

**Problem:** If output validation fails post-execution, the file exists on disk but was never registered. The scope didn't wire this into cleanup.

**Fix:** In the orchestrator, after output validation fails:
1. Step recorded as `validation_failed`
2. Call `self._workspace.cleanup_unregistered(output_path)` — deletes the orphan file
3. Error message to model includes: "Output validation failed: {message}. Partial output cleaned up."
4. This is distinct from Slice 2.5's cancellation cleanup (which handles mid-execution interruption), but both call the same `WorkspaceManager.cleanup_unregistered()` method.

---

## Slices (Revised)

### Slice 2.1 — Preflight Foundation + Spatial Checks 1-8

**Merged from old 2.1 + 2.2.** This is the structural foundation. No parallelization — single coherent change.

**Files:**
- `ecospheric_harness/intents.py` — replace `PreflightResult` dataclass with new `Resolution` enum + structured result
- `ecospheric_harness/preflight.py` — whole-file rewrite: migrate 3 existing checks, add 8 new checks, add `_resolve_secondary_input()` for file-path header reads, add pipeline `run_all_checks()` method
- `ecospheric_harness/orchestrator.py` — replace inline preflight blocks with `_run_preflight_checks()` call, add `warnings` list to turn-state, wire `MODEL_DISCRETION` warnings into turn-state
- `tests/test_preflight.py` — update all existing tests for new API, add ~35-40 new test cases
- `tests/test_orchestrator.py` — update tests that assert on preflight results

**Checks implemented (8 fully specified):**

1. **CRS agreement** (binary ops) — both inputs same CRS. File-path header reads via rasterio/fiona. BLOCK on mismatch.
2. **Extent intersection** (binary ops) — inputs overlap. BLOCK on zero intersection.
3. **Unit awareness** — geographic CRS + distance op. AUTO_FIX (treated as BLOCK with "suggested reproject" message in Phase 2).
4. **Extent containment** — requested bounds within input. BLOCK if bounds exceed input.
5. **CRS validity** — target CRS exists. BLOCK if `pyproj.CRS()` raises.
6. **Planar CRS** (existing, refactored) — BLOCK if geographic CRS on planar-requiring command.
7. **Resolution sanity** — within 3 orders of magnitude. MODEL_DISCRETION if ratio > 1000×. Handles unit normalization (both resolutions converted to meters before comparison).
8. **Geometry validity** (vector only) — `shapely.is_valid` on sample (first 100 features). MODEL_DISCRETION if >10% invalid.

Plus existing checks migrated to new API:
9. **SSRF** (existing, refactored) — BLOCK on internal/metadata IP.
10. **Disk** (existing, refactored) — BLOCK on insufficient disk.

**Checks 11-14 deferred to Phase 4** (see Slice 2.5 below):
- Band validity, categorical resampling guard, datum transformation check, NoData awareness, pixel alignment

These are explicitly **cut from Phase 2**, not bundled as an afterthought. They require deeper integration with ESE command semantics and are better suited to Phase 4 hardening where command metadata is richer.

**Turn-state changes:**

```python
turn: dict[str, Any] = {
    # ... existing fields ...
    "warnings": [],  # NEW: list of {check, message} for MODEL_DISCRETION results
}
```

**Tests:**
- Update ~15 existing preflight tests for new API (PreflightResult fields changed)
- ~35-40 new tests: each check (pass/fail/skip), file-path header reads, pipeline ordering, warnings surfacing
- ~5 orchestrator tests updated for warnings in turn-state

**Estimated:** 3 files rewritten, 2 test files updated/extended, ~40-45 new/updated tests. Large slice.

---

### Slice 2.2 — Output Validation

**Sequential after 2.1.** Both touch `_handle_operation`, so no parallelism.

**Files:**
- `ecospheric_harness/output_validator.py` — NEW
- `ecospheric_harness/orchestrator.py` — add post-execution validation call, add orphan cleanup on failure
- `ecospheric_harness/workspace.py` — add `cleanup_unregistered(path)` method
- `tests/test_output_validator.py` — NEW
- `tests/test_workspace.py` — new test cases for cleanup_unregistered

**Checks:**
1. File exists and non-empty
2. Raster: `rasterio.open()` → dimensions > 1×1, CRS set, NoData set (if applicable)
3. Vector: `gpd.read_file()` → feature count > 0, all geometries valid, CRS set
4. Output-vs-intent:
   - Reproject: output CRS == requested CRS
   - Clip: output extent ⊆ clip bounds
   - Buffer: output extent ⊇ input extent
   - Search: results non-empty (already checked)
5. Failed validation → step marked `validation_failed`, orphan file cleaned up via `workspace.cleanup_unregistered()`

**Orchestrator integration:**
```python
# After executor returns success, before artifact registration:
validation = self._output_validator.validate(
    output_path=exec_result.output_path,
    envelope=exec_result.envelope,
    command=resolved.command,
    input_artifact=input_artifact,
    params=resolved.params,
)
if not validation.ok:
    self._workspace.cleanup_unregistered(exec_result.output_path)
    # record step as validation_failed, return error turn
```

**Tests:** ~20 new test cases (mock rasterio/geopandas, each check pass/fail, orphan cleanup verification).

**Estimated:** 2 new files, 2 modified, ~20 new tests. Medium slice.

---

### Slice 2.3 — Memory Budget + Command Classification

**Sequential after 2.2.** Can potentially overlap with 2.4 if needed.

**Files:**
- `ecospheric_harness/command_profile.py` — NEW (classification table keyed by `(command_name, data_type)`)
- `ecospheric_harness/preflight.py` — add `check_memory_budget()` to the pipeline
- `ecospheric_harness/config.py` — add `memory_limit_mb: int | None` field + env var
- `ecospheric_harness/__main__.py` — add `--memory-limit-mb` CLI flag
- `tests/test_command_profile.py` — NEW
- `tests/test_preflight.py` — new test cases for memory budget check

**Command classification:**

```python
@dataclass
class CommandProfile:
    memory_class: str  # "streaming", "full_load", "depends"
    memory_multiplier: float

# Keyed by (command_name, data_type) — avoids clip-raster vs clip-vector confusion
COMMAND_PROFILES: dict[tuple[str, str], CommandProfile] = {
    ("reproject", "raster"): CommandProfile("full_load", 3.0),
    ("reproject", "vector"): CommandProfile("full_load", 2.0),
    ("clip", "raster"): CommandProfile("streaming", 1.5),
    ("clip", "vector"): CommandProfile("full_load", 2.0),
    ("buffer", "vector"): CommandProfile("full_load", 2.0),
    ("dissolve", "vector"): CommandProfile("full_load", 2.0),
    ("slope", "raster"): CommandProfile("streaming", 1.5),
    ("aspect", "raster"): CommandProfile("streaming", 1.5),
    ("hillshade", "raster"): CommandProfile("streaming", 1.5),
    ("contour", "raster"): CommandProfile("streaming", 2.0),
    ("rasterize", "raster"): CommandProfile("full_load", 3.0),
    ("info", "raster"): CommandProfile("streaming", 1.0),
    ("info", "vector"): CommandProfile("streaming", 1.0),
    ("describe", "raster"): CommandProfile("streaming", 1.0),
    # ... to be enumerated against ESE's 96 commands during implementation
}
DEFAULT_PROFILE = CommandProfile("full_load", 3.0)
```

**Memory estimate:**
- Raster: `width × height × bands × dtype_size × multiplier`
  - `dtype_size` from `data.dtype` in envelope (e.g. "float32" → 4 bytes). Default 4 if absent.
  - `width`, `height`, `bands` from envelope `data.width`, `data.height`, `data.bands`
- Vector: `feature_count × 500 × multiplier` (500 bytes/feature empirical default for polygons)
  - If `feature_count` absent: `file_size_bytes × 5` (GeoParquet compression factor)
  - Flagged as `confidence: "low"` in diagnostics
- Pointcloud: `file_size_bytes × 3`
- If estimate > `memory_limit_mb * 1024 * 1024` → BLOCK

**Config:**
- `memory_limit_mb: int | None = None` (None = no check, defer to RLIMIT_AS)
- `HARNESS_MEMORY_LIMIT_MB` env var
- `--memory-limit-mb` CLI flag

**Tests:** ~15 new test cases (profile lookup by tuple, estimate calculation for raster/vector/pointcloud, block/pass, missing metadata fallback, confidence flagging).

**Estimated:** 2 new files, 3 modified, ~15 tests. Medium slice.

---

### Slice 2.4 — WorkspaceManager Extensions

**Independent of 2.2/2.3** (touches workspace.py only, no orchestrator changes).

**Files:**
- `ecospheric_harness/workspace.py` — session cleanup, cancellation cleanup
- `ecospheric_harness/config.py` — `session_ttl_days: float = 7.0` field
- `ecospheric_harness/__main__.py` — `--session-ttl-days` CLI flag
- `tests/test_workspace.py` — new test cases

**Changes:**

1. **Session cleanup:** `WorkspaceManager.cleanup_old_sessions(ttl_days: float)` — walks `workspace_root`, removes session dirs whose newest file mtime > ttl_days old. Called at harness startup. Default 7 days.

2. **Cancellation cleanup:** `WorkspaceManager.cleanup_unregistered(path: Path)` — already added in Slice 2.2 for output validation. This slice adds `cleanup_cancelled_step(session_dir: Path, step_number: int)` for Phase 3's cancellation flow. For now, just the method exists — not wired to any caller.

3. **Memory accounting:** `WorkspaceManager.estimate_rss(artifact: ArtifactRecord, profile: CommandProfile) -> int` — convenience method wrapping the estimate logic from 2.3. Lives here so both preflight and executor can use it.

**Tests:** ~10 new test cases (old session cleanup, partial cleanup, RSS estimate, TTL boundary).

**Estimated:** 1 file modified, 2 modified, ~10 tests. Small slice.

---

### Slice 2.5 — COG Default + Integration Tests + Eval Fixtures

**Depends on 2.1 + 2.2.** Final integration slice.

**Files:**
- `ecospheric_harness/orchestrator.py` — set COG as default for raster-producing commands
- `ecospheric_harness/config.py` — `default_raster_format: str = "cog"`
- `tests/test_integration.py` — new integration tests
- `ecospheric_harness/eval/cases.py` — new eval fixtures

**Changes:**

1. **COG default:** When a raster-producing command doesn't specify output format, orchestrator injects `--format cog` (or equivalent) into params if not specified.

2. **Integration tests:**
   - Search OSM → buffer → clip with mismatched CRS (preflight BLOCK)
   - Search OSM → reproject → buffer (preflight passes, output validation passes)
   - Search OSM → buffer with file-path mask that doesn't exist (header read fails → MODEL_DISCRETION warning)
   - Search OSM → reproject to invalid CRS (preflight BLOCK on CRS validity)
   - Output validation failure: mock produces 1×1 raster → step marked validation_failed, orphan cleaned up

3. **Eval fixtures:** 5 new fixtures testing preflight and output validation scenarios.

4. **Checks 11-14 explicitly deferred:** Band validity, categorical resampling, datum transformation, NoData awareness, pixel alignment → Phase 4. Documented in non-goals.

**Tests:** ~10 new tests + 5 eval fixtures. Small slice.

---

## Revised Slice Dependency Graph

```
2.1 (Preflight foundation + checks 1-8)
 └── 2.2 (Output validation — sequential, shared orchestrator region)
      ├── 2.3 (Memory budget — can overlap with 2.4)
      └── 2.4 (WorkspaceManager extensions — independent of 2.3)
           └── 2.5 (COG + integration — depends on 2.1 + 2.2)
```

**Strict sequential:** 2.1 → 2.2 → (2.3 ∥ 2.4) → 2.5

No parallelism on orchestrator-touching slices. 2.3 and 2.4 can overlap since 2.3 touches preflight/config/CLI and 2.4 touches workspace/config/CLI (config.py merge risk is low — different fields).

## Total Estimates (Revised)

| Slice | New files | Modified files | New/updated tests | Effort |
|-------|-----------|----------------|-------------------|--------|
| 2.1 | 0 | 3 (+2 test) | ~40-45 | Large |
| 2.2 | 2 | 2 (+1 test) | ~20 | Medium |
| 2.3 | 2 | 3 (+1 test) | ~15 | Medium |
| 2.4 | 0 | 3 (+1 test) | ~10 | Small |
| 2.5 | 0 | 3 (+1 test) | ~10 + 5 fixtures | Small |
| **Total** | **4** | **13** | **~95-100** | |

## Non-goals for Phase 2

- **No auto-fix implementation.** AUTO_FIX results are BLOCK with "suggested reproject" message. Full auto-fix is Phase 4.
- **No ASK_USER UI.** ASK_USER treated as BLOCK. UI is Phase 3.
- **No cancellation wiring.** `cleanup_cancelled_step()` method exists but no caller. Phase 3 wires it.
- **No checks 11-14.** Band validity, categorical resampling guard, datum transformation, NoData awareness, pixel alignment → explicitly deferred to Phase 4. These need richer ESE command metadata than what's currently available.
- **No distributed processing or cloud I/O.**
- **No automatic tiling of oversized rasters.**
- **No `RLIMIT_CPU`.** Per ROADMAP — too risky for GDAL multithreaded ops.
- **No memory multiplier calibration.** Initial heuristics only. Phase 4 instruments actual peak RSS and calibrates.
- **No `ArtifactRecord` field for preflight warnings.** Warnings surface in turn-state only. Storing against artifacts for provenance is a future enhancement.
