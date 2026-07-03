# Phase 2 — Spatial Validation + Data-Size Strategy

> Scoped 2026-07-03. Builds on ROADMAP.md. Sliced into implementable units.

## Current State

- `PreflightChecker` has 3 checks: `check_planar_crs`, `check_disk`, `check_ssrf`
- `PreflightResult` is a simple dataclass: `ok: bool`, `error: str`
- Orchestrator calls preflight checks inline in `_handle_operation()`
- `ArtifactRecord` has: `crs`, `bbox`, `format`, `data_type`, `envelope` (full ETP envelope)
- ETP envelopes contain `data.crs`, `data.bbox`, `data.bounds`, `data.extent`, `data.data_type`, `data.format`
- ESE has 96 commands across raster/vector/pointcloud/hydro/proj
- 408 tests passing

## Design Decisions

### PreflightResult upgrade (breaking change — required first)

Current `PreflightResult` is `{ok, error}`. ROADMAP calls for `{check, resolution, message, diagnostics}`. This is a structural change that ripples through:
- `intents.py` (PreflightResult dataclass)
- `preflight.py` (PreflightChecker methods)
- `orchestrator.py` (how preflight results are consumed)
- Existing tests that assert on `PreflightResult.ok` / `.error`

**Strategy:** New `PreflightResult` with `Resolution` enum. Old checks migrated. Orchestrator gains a `_run_preflight()` method that runs all applicable checks and collects results. BLOCK and ASK_USER stop execution. AUTO_FIX applies the fix and re-runs. MODEL_DISCRETION and PASS continue.

### What metadata is available for preflight?

From `ArtifactRecord`:
- `crs`: string (e.g. "EPSG:32610") or None
- `bbox`: `[minx, miny, maxx, maxy]` or None
- `format`: "geotiff", "geoparquet", etc.
- `data_type`: "raster", "vector", "pointcloud", "metadata"
- `envelope`: full ETP envelope dict (has `data.crs`, `data.bbox`, `data.bounds`, `data.extent`, `data.resolution`, `data.width`, `data.height`, `data.bands`, etc.)

For binary ops (two inputs), the second input comes from params (e.g. `--by`, `--mask`, `--overlay`). We need to resolve param values that are file paths to artifact metadata. This is tricky — for now, we'll only run binary-op checks when both inputs are registered artifacts. If the second input is a raw file path, we skip binary checks (can't inspect without executing).

### Memory estimation approach

Per ROADMAP: memory behavior class + multiplier per command, runtime RSS estimate from live input metadata.

- **Behavior class:** `streaming`, `full_load`, `depends`
- **Multiplier:** default 3×, overridden for known classes
- **RSS estimate:** `input_dims × dtype × bands × multiplier`
- Input dims from envelope: `data.width × data.height × data.bands` (raster), or `file_size_bytes` (vector/pointcloud)
- **Block check:** if estimate > `rlimit_as_mb` (or a new `memory_limit_mb` config), BLOCK

We need a command classification table. ESE has 96 commands — classify by algorithm family:
- Reproject/warp: `full_load`, 3× (GDAL Warp uses ~2-3× input)
- Buffer/clip/dissolve (vector): `full_load`, 2× (geopandas loads all)
- Slope/aspect/hillshade: `streaming`, 1.5× (windowed)
- Contour: `streaming`, 2×
- Mesh/rasterize: `full_load`, 3×
- Point cloud ops: `full_load`, 3× (PDAL loads all)
- Info/describe: `streaming`, 1×
- Default: `full_load`, 3× (conservative)

### Output validation approach

After execution, inspect the output artifact:
- File exists and non-empty
- Raster: dimensions > 1×1, CRS set, NoData set (if applicable)
- Vector: feature count > 0, valid geometries, CRS set
- Output-vs-intent: extent ⊆ expected (from input extent + operation type), CRS == requested

Tools: `rasterio` (already installed via geopandas/gdal), `geopandas`, `shapely`. All already available.

---

## Slices

### Slice 2.1 — PreflightResult + Resolution enum upgrade

**Files:**
- `ecospheric_harness/intents.py` — replace `PreflightResult` dataclass
- `ecospheric_harness/preflight.py` — migrate all check methods to new return type
- `ecospheric_harness/orchestrator.py` — update `_handle_operation()` preflight consumption
- `tests/test_preflight.py` — update existing tests
- `tests/test_orchestrator.py` — update tests that assert on preflight results

**Changes:**

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
        """Backward-compat: True when resolution is PASS or MODEL_DISCRETION."""
        return self.resolution in (Resolution.PASS, Resolution.MODEL_DISCRETION)
```

All existing checks return `PreflightResult(check="...", resolution=Resolution.BLOCK, message="...")` on failure, `PreflightResult(check="...", resolution=Resolution.PASS)` on success.

Orchestrator: any `BLOCK` → make_error_turn with message. `ASK_USER` → (for now) treat as BLOCK (Phase 3 will surface in UI). `AUTO_FIX` → (for now) treat as BLOCK with "auto-fix not yet implemented" (Phase 2b will implement). `MODEL_DISCRETION` → pass through.

**Tests:** Update existing preflight tests to use new API. Verify backward-compat `ok` property works.

**Estimated:** ~3 files changed, ~2 new test assertions. Small slice.

---

### Slice 2.2 — Spatial preflight checks (the 14 checks)

**Files:**
- `ecospheric_harness/preflight.py` — add new check methods
- `ecospheric_harness/intents.py` — (no changes beyond Slice 2.1)
- `tests/test_preflight.py` — new test cases

**New checks (in priority order):**

1. **`check_crs_agreement(command, input_artifact, params)`** — binary ops: both inputs same CRS?
   - Detect binary ops: command has 2+ input params (e.g. `--input` + `--by`, `--overlay`, `--mask`)
   - If second input is an artifact ID → resolve and compare CRS
   - If second input is a file path → skip (can't inspect)
   - Resolution: `BLOCK` if CRS mismatch, `MODEL_DISCRETION` if can't determine

2. **`check_extent_intersection(command, input_artifact, params)`** — binary ops: do inputs overlap?
   - Compare bbox of both inputs
   - Resolution: `BLOCK` if zero intersection

3. **`check_unit_awareness(command, input_artifact)`** — geographic CRS + linear distance = auto-fix
   - If command requires planar CRS (already checked by `check_planar_crs`) AND input is geographic
   - Resolution: `AUTO_FIX` with diagnostics: `{"suggested_crs": "EPSG:3857", "input_crs": "..."}`
   - (For now, this will be BLOCK with a message — auto-fix implementation is Slice 2.4)

4. **`check_extent_containment(command, input_artifact, params)`** — requested bounds within input?
   - If params contain `bbox` or `bounds`, check it's within input bbox
   - Resolution: `BLOCK` if bounds exceed input extent

5. **`check_crs_validity(command, params)`** — target CRS exists?
   - If params contain `--output-crs` or `--target-crs`, validate with `pyproj.CRS()`
   - Resolution: `BLOCK` if invalid

6. **`check_resolution_sanity(command, input_artifact, params)`** — within 3 orders of magnitude?
   - If params contain `--resolution` and input has resolution in envelope
   - Resolution: `MODEL_DISCRETION` if ratio > 1000× (warn but allow)

7. **`check_geometry_validity(command, input_artifact)`** — valid geometries?
   - Only for vector inputs. Use `shapely.is_valid` on a sample (first 100 features)
   - Resolution: `MODEL_DISCRETION` if >10% invalid (warn)

8. **`check_pixel_alignment(command, input_artifact, params)`** — raster algebra alignment
   - Only for raster algebra ops (map algebra, overlay)
   - Check both inputs have same CRS, resolution, origin
   - Resolution: `BLOCK` if misaligned

9-14: Path confinement + SSRF already exist. Band validity, categorical resampling, datum transformation, NoData awareness → `MODEL_DISCRETION` level (warn but don't block — these are subtle and we don't want false positives early).

**Orchestrator integration:**
- New `_run_preflight_checks()` method that runs all applicable checks and collects results
- Runs checks in priority order
- First `BLOCK` stops and returns error to model
- `MODEL_DISCRETION` results are surfaced in turn state as warnings

**Tests:** ~30-40 new test cases covering each check (pass/fail/skip scenarios).

**Estimated:** ~1 file heavily modified, ~40 new tests. Medium-large slice.

---

### Slice 2.3 — Output validation

**Files:**
- `ecospheric_harness/output_validator.py` — NEW
- `ecospheric_harness/orchestrator.py` — call validator after execution
- `tests/test_output_validator.py` — NEW

**Checks after successful execution:**

```python
@dataclass
class OutputValidationResult:
    ok: bool
    checks: list[dict]  # [{check: "...", passed: bool, message: "..."}]
    error: str = ""

class OutputValidator:
    def validate(self, output_path: Path, envelope: dict, command: CommandDescriptor,
                 input_artifact: ArtifactRecord | None, params: dict) -> OutputValidationResult:
        ...
```

Checks:
1. File exists and non-empty
2. Raster: open with rasterio, check `width > 1 or height > 1`, CRS set, NoData set
3. Vector: open with geopandas, `len(gdf) > 0`, all geometries valid, CRS set
4. Output-vs-intent: 
   - Reproject: output CRS == requested CRS
   - Clip: output extent ⊆ clip bounds
   - Buffer: output extent ⊇ input extent
   - Search: results non-empty (already checked)
5. Failed validation → step marked `failed` (not `success`), diagnostics in step record

**Orchestrator integration:**
- After executor returns success, before registering artifact, run output validator
- If validation fails: step recorded as `validation_failed`, error message to model
- If validation passes: normal artifact registration

**Tests:** ~20 new test cases (mock rasterio/geopandas, test each check, test pass/fail).

**Estimated:** 2 new files, 1 modified, ~20 tests. Medium slice.

---

### Slice 2.4 — Memory budget preflight + command classification

**Files:**
- `ecospheric_harness/command_profile.py` — NEW (command memory classification table)
- `ecospheric_harness/preflight.py` — add `check_memory_budget()`
- `ecospheric_harness/config.py` — add `memory_limit_mb` field
- `ecospheric_harness/__main__.py` — add `--memory-limit-mb` CLI flag
- `tests/test_command_profile.py` — NEW
- `tests/test_preflight.py` — new test cases

**Command classification:**

```python
@dataclass
class CommandProfile:
    memory_class: str  # "streaming", "full_load", "depends"
    memory_multiplier: float  # peak RSS ≈ N × input_bytes

# Classification by command name pattern
COMMAND_PROFILES: dict[str, CommandProfile] = {
    "reproject": CommandProfile("full_load", 3.0),
    "warp": CommandProfile("full_load", 3.0),
    "buffer": CommandProfile("full_load", 2.0),
    "clip": CommandProfile("full_load", 2.0),
    "dissolve": CommandProfile("full_load", 2.0),
    "slope": CommandProfile("streaming", 1.5),
    "aspect": CommandProfile("streaming", 1.5),
    "hillshade": CommandProfile("streaming", 1.5),
    "contour": CommandProfile("streaming", 2.0),
    "rasterize": CommandProfile("full_load", 3.0),
    "info": CommandProfile("streaming", 1.0),
    "describe": CommandProfile("streaming", 1.0),
    # ... etc
}
DEFAULT_PROFILE = CommandProfile("full_load", 3.0)
```

**Memory estimate:**
- Raster: `width × height × bands × dtype_size × multiplier` (from envelope `data.width`, `data.height`, `data.bands`, `data.dtype`)
- Vector/pointcloud: `file_size_bytes × multiplier`
- If estimate > `memory_limit_mb * 1024 * 1024` → BLOCK

**Config:**
- `memory_limit_mb: int | None = None` (None = no check, defer to RLIMIT_AS)
- `HARNESS_MEMORY_LIMIT_MB` env var
- `--memory-limit-mb` CLI flag

**Tests:** ~15 new test cases (profile lookup, estimate calculation, block/pass scenarios).

**Estimated:** 2 new files, 3 modified, ~15 tests. Medium slice.

---

### Slice 2.5 — WorkspaceManager extensions

**Files:**
- `ecospheric_harness/workspace.py` — session cleanup, cancellation cleanup
- `ecospheric_harness/config.py` — `session_ttl_days` field
- `ecospheric_harness/__main__.py` — `--session-ttl-days` CLI flag
- `tests/test_workspace.py` — new test cases (extend existing)

**Changes:**

1. **Session cleanup:** `WorkspaceManager.cleanup_old_sessions(ttl_days: int)` — walks `workspace_root`, removes session dirs older than TTL. Called at harness startup. Default 7 days.

2. **Cancellation cleanup:** `WorkspaceManager.cleanup_partial(session_dir: Path)` — removes unregistered temp files from a cancelled step. Called when a step is cancelled (Phase 3 will add cancellation; for now, just the method exists).

3. **Memory accounting:** `WorkspaceManager.estimate_rss(artifact: ArtifactRecord, profile: CommandProfile) -> int` — convenience method wrapping the estimate logic from Slice 2.4. Lives here so both preflight and the executor can use it.

**Tests:** ~10 new test cases (old session cleanup, partial cleanup, RSS estimate).

**Estimated:** 1 file modified, 2 modified, ~10 tests. Small slice.

---

### Slice 2.6 — COG output default + integration test

**Files:**
- `ecospheric_harness/orchestrator.py` — set COG as default for raster-producing commands
- `ecospheric_harness/config.py` — `default_raster_format: str = "cog"`
- `tests/test_integration.py` — new integration test
- `ecospheric_harness/eval/cases.py` — add spatial validation eval fixtures

**Changes:**

1. **COG default:** When a raster-producing command doesn't specify output format, default to COG. The orchestrator injects `--format cog` (or equivalent) into params if not specified.

2. **Integration test:** Full pipeline that exercises preflight checks + output validation:
   - Search OSM → buffer → clip with mismatched CRS (preflight BLOCK)
   - Search OSM → reproject → buffer (preflight AUTO_FIX for geographic CRS)
   - Search OSM → buffer → validate output (output validation passes)

3. **Eval fixtures:** 5-10 new fixtures testing preflight and output validation scenarios.

**Estimated:** 2 files modified, 1 file extended, ~10 new tests/fixtures. Small slice.

---

## Slice Dependency Graph

```
2.1 (PreflightResult upgrade)
 ├── 2.2 (Spatial preflight checks)
 │    └── 2.4 (Memory budget — can run in parallel with 2.3)
 ├── 2.3 (Output validation — independent of 2.2)
 └── 2.5 (WorkspaceManager extensions — independent of 2.2/2.3)
      └── 2.6 (COG + integration — depends on 2.2 + 2.3)
```

**Recommended order:** 2.1 → (2.2 + 2.3 parallel) → 2.4 → 2.5 → 2.6

Or: 2.1 → 2.2 → 2.3 → 2.4 → 2.5 → 2.6 (sequential, simpler)

## Total Estimates

| Slice | New files | Modified files | New tests | Effort |
|-------|-----------|----------------|-----------|--------|
| 2.1 | 0 | 3 | 0 (update existing) | Small |
| 2.2 | 0 | 1 | ~35-40 | Large |
| 2.3 | 2 | 1 | ~20 | Medium |
| 2.4 | 2 | 3 | ~15 | Medium |
| 2.5 | 0 | 3 | ~10 | Small |
| 2.6 | 0 | 3 | ~10 | Small |
| **Total** | **4** | **14** | **~90-95** | |

## Non-goals for Phase 2

- No auto-fix implementation (AUTO_FIX results as BLOCK with message for now — full auto-fix is Phase 4)
- No ASK_USER UI (Phase 3)
- No cancellation wiring (Phase 3 — just the cleanup method)
- No distributed processing or cloud I/O
- No automatic tiling of oversized rasters
- No `RLIMIT_CPU` (per ROADMAP — too risky for GDAL multithreaded ops)
