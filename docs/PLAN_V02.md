# Ecospheric Agent Harness — Implementation Plan v0.2

**Spec**: `docs/SPEC_V02.md` (Draft v6, 50 ACs, 5 critique rounds)
**Date**: 2026-06-30
**Status**: Ready for dispatch

---

## Spec Ambiguities to Resolve Before Coding

These are implementation details the spec doesn't fully pin down. Resolutions are proposed here for Pilgrim review — once approved, they become binding constraints on the tasks.

### A1. EDD search param pollution
**Issue**: Each EDD `search` descriptor carries operational flags (`--json`, `--quiet`, `--no-cache`, `--timeout`, `--log-level`) plus `--output` and `--format` that the model shouldn't see in `required_params` or the intent menu.
**Resolution**: Registry filters `ParameterDescriptor` entries by a denylist: `{"--json", "--quiet", "--no-cache", "--timeout", "--log-level", "--output", "--format"}`. These are harness-controlled or irrelevant to model intent. `required_params` in `IntentOption` only includes params that are `required=True` AND not in the denylist.
**Impact**: `registry.py`, `menu.py`

### A2. EDD search data_type varies per source
**Issue**: The spec describes two search modes (STAC metadata vs direct-data vector) but keys the distinction on `source == "@stac"`. In reality, `@ckan`, `@firms`, `@opentopography` also return `data_type="metadata"`, while `@osm`, `@gbif`, `@geoboundaries`, `@overture`, `@earthquakes` return `data_type="vector"`.
**Resolution**: The registry pairs each `search` CommandDescriptor with its source prefix. The `data_type` on the `IntentEntry` comes from the descriptor, not from a hardcoded STAC/non-STAC check. The orchestrator's two-mode branching uses `data_type == "metadata"` → turn-state-only vs `data_type == "vector"` → artifact stored. This is more general than keying on source name.
**Impact**: `registry.py`, `orchestrator.py`

### A3. EDD search descriptors all share the name `"search"`
**Issue**: `edd describe --all` emits 9 entries all named `"search"` with different `data_type` and different required params. The registry must pair each with its source prefix to build `search_<source>` intents.
**Resolution**: At startup, after calling `edd describe --all`, the registry calls `edd plugins --json` to get source prefixes. For each prefix `@X`, it finds the `search` descriptor whose parameters include `--source` with the matching source value. Since all 9 descriptors have `--source` as the first required param, the registry matches by checking which source-specific params are present (e.g., `--iso3` → geoboundaries, `--demtype` → opentopography, `--taxon-key` → gbif). Implementation: the registry pairs by position — the Nth `search` descriptor pairs with the Nth source prefix from `edd plugins --json`. **Wait** — this is fragile. Better approach: call `edd describe search --source @osm` per source to get the source-specific descriptor. But `edd describe` takes a command name, not source. **Actual approach**: the registry uses the `supported_intents` field on the EDD source plugin capabilities (visible in `edd plugins --json` as `supported_intents` — confirmed this field exists on `SourceCapabilities`). Actually, `edd plugins --json` doesn't expose `data_type` per source. The reliable pairing is: all 9 search descriptors have `--source` as required param 1; the registry pairs them by **parameter fingerprint** — the set of param names beyond the common baseline. This is deterministic but brittle if EDD adds sources.

**Final resolution**: The registry calls `edd describe --all` and gets all search descriptors. It then calls `edd plugins --json` for source prefixes. For each source prefix `@X`, it invokes `edd search --source @X --bbox "0,0,0,0" --output /dev/null --json --limit 0` in a **dry discovery mode** to see which descriptor responds — no, this is too expensive.

**Actually simplest**: Parse the 9 search descriptors. Each has the same common params (`--source`, `--bbox`, `--output`, `--format`, `--limit`, `--json`, `--quiet`, `--no-cache`, `--timeout`, `--log-level`). The **unique params** per descriptor fingerprint it: `--catalog`/`--collection`/`--date` → `@stac`, `--demtype`/`--output-format` → `@opentopography`, `--taxon-key`/`--scientific-name`/`--year` → `@gbif`, `--iso3`/`--adm-level` → `@geoboundaries`, `--intent`/`--starttime`/`--endtime`/`--minmagnitude` → `@earthquakes`, `--intent`/`--overpassql` → `@osm`, `--intent`/`--release` → `@overture`, `--sensor`/`--timespan`/`--file-format`/`--region` → `@firms`, remaining → `@ckan`.

This is **brittle**. Better: the EDD `describe --all` output should include the source value in the descriptor. It doesn't currently. **For v0.2, the registry will pair search descriptors with sources by index order**, matching the order from `edd plugins --json` against the order of search descriptors from `edd describe --all`. Both come from the same plugin registration order, so they should match. We add a test that verifies this pairing is correct by checking unique params. If EDD changes, the test breaks loudly.

**Impact**: `registry.py`, `test_registry.py`

### A4. Artifact CRS extraction
**Issue**: The spec's `Artifact` dataclass has a `crs` field, but the ETP envelope `data` block doesn't have a standard `crs` key. ESE enriches `data` with `format` and `data_type` but CRS is only in the `crs_meta` provenance field, not directly in `data`.
**Resolution**: The orchestrator extracts CRS from the envelope `data` block by checking keys in priority order: `data["crs"]`, `data["crs_meta"]["crs"]`, `data["output_crs"]`. If none found, `crs=None`. This is a best-effort extraction — preflight CRS checks gracefully handle `None` (already specified: "no input to check" → ok).
**Impact**: `orchestrator.py` (artifact construction), `test_orchestrator.py`

### A5. Artifact bbox extraction
**Issue**: Similar to CRS — no standard `bbox` key in envelope `data`.
**Resolution**: Best-effort extraction: `data.get("bbox")` or `data.get("bounds")` or `data.get("extent")`. If none, `bbox=None`. Not critical for v0.2 — bbox is display-only in turn state.
**Impact**: `orchestrator.py`

### A6. `_input_target` in intent menu
**Issue**: The spec says `_input_target` is a harness-internal key stripped before serialization. But should the model see it as a required param in the menu for commands like `hydro basins`?
**Resolution**: `_input_target` is NOT shown in `required_params`. The model learns about it from system prompt Rule 12. When the model emits it, the executor handles it. When the model doesn't emit it and the command needs it, the executor raises an error that's returned to the model with a hint. This matches the spec's design.
**Impact**: `menu.py`, `executor.py`

### A7. `--output` and `--json` in executor argv
**Issue**: The executor always appends `--output <tmpfile>` and `--json`. But `--json` appears in some command descriptors as a parameter (e.g., `raster clip` has `--json: type=boolean`). The executor should append it regardless of descriptor — it's a universal flag.
**Resolution**: Executor appends `--json` as a bare flag after all serialized params, before subprocess.run. It's not in the `params` dict — it's harness-controlled. Same for `--output`. Neither appears in serialized params from the model.
**Impact**: `executor.py`

### A8. Search results file format
**Issue**: For STAC search, results are metadata written to a JSON file. For direct-data search, results are vector features written to GeoJSON. The orchestrator needs to know which to build turn state vs artifact.
**Resolution**: Branch on `envelope.data.data_type`: `"metadata"` → turn state only (parse JSON for items), `"vector"` → artifact stored (envelope has output_path). The orchestrator reads the `data_type` from the execution result envelope, not from a hardcoded source list.
**Impact**: `orchestrator.py`

---

## Units & Dependencies

### Unit Summary

| # | Unit | Size | Depends On | Parallelizable With |
|---|------|------|------------|---------------------|
| U1 | Project scaffold & config | S | — | — |
| U2 | Result & intent types | S | U1 | U3 |
| U3 | Artifact manager | M | U1 | U2 |
| U4 | Registry (tool discovery + intent catalog) | L | U1, U2 | U5 |
| U5 | Preflight checks | S | U3 | U4 |
| U6 | Validator (schema validation) | M | U1, U2 | U4, U5 |
| U7 | Executor (subprocess + param serialization) | L | U1, U2, U3 | U4, U5, U6 |
| U8 | Resolver (intent → tool+command) | M | U2, U4 | U5, U6 |
| U9 | Menu (intent narrowing) | S | U4, U8 | U5, U6 |
| U10 | Corrections (undo/redo) | M | U3, U7, U8 | U9 |
| U11 | Provenance chain | S | U2 | U3-U10 |
| U12 | Orchestrator (multi-turn loop) | L | U4, U7, U8, U9, U10, U11 | — |
| U13 | CLI + Python API | M | U12 | — |
| U14 | Integration tests | L | U12, U13 | — |
| U15 | Quality gates (lint, type, coverage) | S | U14 | — |

### Dependency Graph

```
U1 (scaffold)
├── U2 (types) ──────────────────────────┐
├── U3 (artifact) ─────────────┐         │
│                              │         │
├── U4 (registry) ◄── U2       │         │
│   │                          │         │
├── U5 (preflight) ◄── U3      │         │
│                              │         │
├── U6 (validator) ◄── U2      │         │
│                              │         │
├── U7 (executor) ◄── U2,U3    │         │
│   │                          │         │
├── U8 (resolver) ◄── U4,U2    │         │
│   │                          │         │
├── U9 (menu) ◄── U4,U8        │         │
│   │                          │         │
├── U10 (corrections) ◄── U3,U7,U8       │
│   │                          │         │
├── U11 (provenance) ◄── U2    │         │
│   │                          │         │
├── U12 (orchestrator) ◄────── U4,U7,U8,U9,U10,U11
│   │
├── U13 (CLI/API) ◄── U12
│   │
├── U14 (integration) ◄── U12,U13
│   │
└── U15 (quality) ◄── U14
```

### Critical Path

`U1 → U2 → U4 → U8 → U12 → U13 → U14 → U15`

The registry (U4) and orchestrator (U12) are the two largest units. Everything else can be parallelized around them.

---

## Dispatch Waves

### Wave 1 — Foundation (parallel, 4 tasks)

Build the scaffold, core types, artifact manager, and result/intent dataclasses. These have minimal interdependencies and can be built simultaneously.

| Task | Unit | Model | Files |
|------|------|-------|-------|
| T1.1 | U1 | Qwen 3.7 Plus | `pyproject.toml`, `__init__.py`, `__main__.py` (stub), `config.py`, `conftest.py` |
| T1.2 | U2 | Qwen 3.7 Plus | `intents.py`, `result.py` |
| T1.3 | U3 | Qwen 3.7 Plus | `artifact.py` |
| T1.4 | U11 | Qwen 3.7 Plus | `provenance.py` |

### Wave 2 — Discovery & Validation (parallel, 3 tasks)

Registry, validator, and preflight can all be built once Wave 1 types exist. Registry is the largest — it needs the alias resolution logic, EDD source pairing, and diagnostic filtering.

| Task | Unit | Model | Files |
|------|------|-------|-------|
| T2.1 | U4 | Qwen 3.7 Plus | `registry.py` |
| T2.2 | U6 | Qwen 3.7 Plus | `validator.py` |
| T2.3 | U5 | Qwen 3.7 Plus | `preflight.py` |

### Wave 3 — Execution & Resolution (parallel, 2 tasks)

Executor and resolver both depend on Wave 1+2. Executor is the second-largest unit (param serialization, input routing, subprocess management). Resolver depends on registry output.

| Task | Unit | Model | Files |
|------|------|-------|-------|
| T3.1 | U7 | Qwen 3.7 Plus | `executor.py` |
| T3.2 | U8 | Qwen 3.7 Plus | `resolver.py` |

### Wave 4 — Menu & Corrections (parallel, 2 tasks)

Menu depends on registry + resolver. Corrections depends on artifact, executor, and resolver.

| Task | Unit | Model | Files |
|------|------|-------|-------|
| T4.1 | U9 | Qwen 3.7 Plus | `menu.py` |
| T4.2 | U10 | Qwen 3.7 Plus | `corrections.py` |

### Wave 5 — Orchestrator (single, 1 task)

The orchestrator ties everything together. It's the largest integration point — model communication, turn state construction, search mode branching, artifact lifecycle.

| Task | Unit | Model | Files |
|------|------|-------|-------|
| T5.1 | U12 | Qwen 3.7 Plus | `orchestrator.py` |

### Wave 6 — CLI & API (single, 1 task)

CLI entry point, Python API surface, `--list-tools`, `--list-intents`, `--dry-run`.

| Task | Unit | Model | Files |
|------|------|-------|-------|
| T6.1 | U13 | Qwen 3.7 Plus | `__main__.py`, `__init__.py` (public API) |

### Wave 7 — Integration Tests (single, 1 task)

End-to-end pipeline tests using mocked tools. The integration test suite validates ACs 9-10, 12-17, 37-40, 41-44.

| Task | Unit | Model | MiMo v2.5 Pro | Files |
|------|------|-------|----------------|-------|
| T7.1 | U14 | — | MiMo v2.5 Pro | `tests/test_integration.py` |

### Wave 8 — Judge & Quality (single, 1 task)

Full test run, ruff, mypy --strict, coverage check. Fix any issues found. Final review against all 50 ACs.

| Task | Unit | Model | Files |
|------|------|-------|-------|
| T8.1 | U15 | GLM 5.2 | All files |

---

## Atomic Task Breakdown

### Wave 1: Foundation

#### T1.1 — Project scaffold & config (U1, S)

**Input**: Spec section C (project structure), section B (environment/config)
**Action**:
- Create `pyproject.toml` with: `python >=3.11`, deps `etp>=0.1.0`, `pyproj`, `httpx`, dev deps `pytest`, `pytest-cov`, `ruff`, `mypy`
- `ecospheric_harness/__init__.py` (empty, will be filled in U13)
- `ecospheric_harness/__main__.py` (stub with `if __name__ == "__main__": pass`)
- `ecospheric_harness/config.py`:
  - `HarnessConfig` dataclass: `model`, `tools` (list[str]), `subprocess_timeout` (default 300), `disk_limit_gb` (default 2), `search_cap` (default 20), `max_turns` (default 20), `workdir` (Path)
  - `from_env()` classmethod reading `HARNESS_*` env vars
  - `from_cli()` classmethod for CLI flag overrides
- `tests/conftest.py` with shared fixtures: `tmp_workdir`, `mock_tool_describe`
- `tests/test_config.py`: test `from_env()`, `from_cli()`, defaults
**Output**: Installable package, config module, test infrastructure
**Verification**: `pip install -e .` succeeds, `pytest tests/test_config.py` passes, `ruff check .` clean

#### T1.2 — Intent & result types (U2, S)

**Input**: Spec "Intent Types" section, "StepRecord" and "PipelineResult" sections
**Action**:
- `ecospheric_harness/intents.py`:
  - `OperationIntent`, `UndoIntent`, `RedoIntent`, `CompleteIntent`, `FailedIntent` dataclasses (exact fields from spec)
  - `parse_intent(raw: dict) -> OperationIntent | UndoIntent | RedoIntent | CompleteIntent | FailedIntent` — parse from function-calling response
  - Validation: `CompleteIntent` requires `summary`, `FailedIntent` requires `reason`
  - `IntentEntry` dataclass: `intent: str`, `description: str`, `tool: RegisteredTool`, `command: CommandDescriptor`, `required_params: list[str]`
  - `IntentOption` dataclass: `intent: str`, `description: str`, `required_params: list[str]`
  - `RegisteredTool` dataclass: `name: str`, `version: str`, `binary: str`, `commands: list[CommandDescriptor]`
  - `ResolvedCall` dataclass: `tool: RegisteredTool`, `command: CommandDescriptor`, `params: dict[str, Any]`
  - `ResolutionError` dataclass: `message: str`
  - `CorrectionResult` dataclass: `status: str`, `artifact: Artifact | None`, `message: str`
  - `PreflightResult` dataclass: `ok: bool`, `error: str`
  - `ExecuteResult` dataclass: `envelope: dict`, `returncode: int`, `output_path: Path`
- `ecospheric_harness/result.py`:
  - `StepRecord` dataclass (fields from spec: `step_number`, `tool`, `command`, `tool_ref`, `command_ref`, `intent`, `params`, `status`, `undone`, `envelope`, `duration_ms`, `is_search`)
  - `PipelineResult` dataclass: `steps`, `final_artifact`, `provenance_chain`, `summary()` method
- `tests/test_intents.py`:
  - Parse each intent type from raw dict
  - Validation errors for missing `summary`/`reason`
  - Round-trip: construct → serialize → parse
- `tests/test_result.py`:
  - `StepRecord` construction and `undone` toggling
  - `PipelineResult.summary()` with various step states
  - Provenance chain filtering (undone excluded)
**Output**: All type definitions used by other modules
**Verification**: `pytest tests/test_intents.py tests/test_result.py` passes, mypy --strict clean

#### T1.3 — Artifact manager (U3, M)

**Input**: Spec "Artifact Manager" section (full code provided)
**Action**:
- `ecospheric_harness/artifact.py`:
  - `Artifact` dataclass: `path`, `envelope`, `format`, `data_type`, `crs`, `bbox`, `step_number`
  - `ArtifactManager` class (exact API from spec):
    - `__init__(workdir, disk_limit_bytes)`
    - `store(artifact)` — shift window: free previous, current→previous, new→current
    - `replace_current(artifact)` — replace current, keep previous (for redo)
    - `undo()` — discard current, revert to previous
    - `current()`, `previous()`, `can_undo()`
    - `disk_available(estimated_new_bytes)`
    - `free()` — cleanup all
    - `_artifact_size()` helper
  - `normalize_format()` function + `FORMAT_ALIASES` dict (from spec)
- `tests/test_artifact.py`:
  - **Window shift on success**: store A → current=A, previous=None; store B → current=B, previous=A; store C → current=C, previous=B, A freed
  - **Preserve on failure**: (test via mock — store A, store B, then verify state unchanged after simulated failure)
  - **Undo**: store A, store B → undo → current=A, previous=None, B file deleted
  - **Undo at step 1**: store A → undo → current=None, previous=None
  - **Replace_current**: store A, store B → replace_current(C) → current=C, previous=A, B file deleted
  - **Post-undo store**: store A, store B → undo → store C → current=C, previous=A (store shifts A→previous... wait, no: after undo, current=A, previous=None. store(C) → previous=None freed (no-op), current(A)→previous, C→current. So current=C, previous=A.)
  - **can_undo**: False initially, True after 2 stores, False after undo, True after post-undo store
  - **Disk tracking**: total_bytes tracks correctly through store/undo/replace_current
  - **disk_available**: True under limit, False over limit
  - **Format normalization**: all aliases map correctly
**Output**: Working two-artifact sliding window with disk tracking
**Verification**: `pytest tests/test_artifact.py` passes, all state transitions match spec traces

#### T1.4 — Provenance chain (U11, S)

**Input**: Spec "Provenance Chain" section, AC17
**Action**:
- `ecospheric_harness/provenance.py`:
  - `build_provenance_chain(steps: list[StepRecord]) -> list[dict]` — filters to non-undone successful steps, builds chain dict with `step`, `tool`, `command`, `intent`, `params`, `duration_ms`
  - Excludes undone steps (AC17)
  - Includes redone steps (they have their own StepRecord with `undone=False`)
- `tests/test_provenance.py`:
  - Simple 2-step chain
  - Chain with undone step (excluded)
  - Chain with undo + redo (original excluded, redo included)
  - Chain with undo + redo + undo-after-redo (both excluded)
  - Empty steps list
  - All-failed steps list
**Output**: Provenance chain builder
**Verification**: `pytest tests/test_provenance.py` passes, AC17 verified

---

### Wave 2: Discovery & Validation

#### T2.1 — Registry: tool discovery & intent catalog (U4, L)

**Input**: Spec "Intent Catalog and Alias Map" section (rules 1-8), spec "Startup source discovery", AC1, AC2, AC36, AC38, AC49
**Action**:
- `ecospheric_harness/registry.py`:
  - `ToolRegistry` class:
    - `discover_tools(tool_names: list[str]) -> list[RegisteredTool]` — for each tool, run `<binary> describe --all` (without `--json` flag since ESE doesn't support it — parse stdout JSON directly), parse envelope, reconstruct `CommandDescriptor` objects from JSON
    - `discover_sources(tool: RegisteredTool) -> list[str]` — run `<binary> plugins --json` for EDD tools, parse `data.plugins[].prefix`
    - `build_catalog(tools: list[RegisteredTool], sources: dict[str, list[str]]) -> list[IntentEntry]` — apply alias resolution rules:
      1. Split command name on spaces
      2. Single-word: intent = command name (no stripping) — AC38
      3. Multi-word: first token = category, join remaining with `_`, replace `-` with `_`
      4. EDD source commands: pair each `search` descriptor with source prefix → `search_<source_without_@>`
      5. `INTENT_OVERRIDES` keyed on full command name before stripping — AC49
      6. Diagnostic exclusion: `category in {"diagnostic", "info", "pipe"}` — AC36
      7. Collision: store both entries (resolver disambiguates later)
    - `PARAM_DENYLIST`: `{"--json", "--quiet", "--no-cache", "--timeout", "--log-level", "--output", "--format"}` — filtered from `required_params` (A1)
    - `required_params` computation: params with `required=True` and not in denylist
  - `_reconstruct_descriptor(data: dict) -> CommandDescriptor` — rebuild from JSON (resilient to ESE/ETP separate classes, per spec assumption 6)
  - EDD search-source pairing (A3): match search descriptors with source prefixes by index order from `edd plugins --json`. Add a `_fingerprint` check in tests to verify pairing correctness.
- `tests/test_registry.py`:
  - **Mock describe output** (fixture with realistic EDD+ESE descriptors)
  - **Alias resolution**: single-word (`fetch`→`fetch`), space-split (`raster clip`→`clip`), hyphen (`hydro fill-sinks`→`fill_sinks`), 3-token (`convert raster-format`→`raster_format`)
  - **EDD source disambiguation**: 9 `search` descriptors → `search_osm`, `search_stac`, `search_geoboundaries`, etc.
  - **Intent overrides**: `proj transform`→`reproject`, `proj distance`→`geodesic_distance` (AC49)
  - **Collision**: `raster clip` and `vector clip` both → `clip`, both entries stored
  - **Diagnostic exclusion**: `doctor`, `info`, `pipe`, `tee` excluded (AC36)
  - **Single-word rule**: `fetch` stays `fetch` (AC38)
  - **Source fingerprint test**: verify each paired search descriptor has the expected unique params for its source
  - **Param denylist**: `--json`, `--output` etc. not in `required_params`
  - **Reconstruct descriptor**: from JSON dict → correct `CommandDescriptor` fields
**Output**: Working registry that discovers tools and builds intent catalog
**Verification**: `pytest tests/test_registry.py` passes, AC1, AC2, AC36, AC38, AC49 verified

#### T2.2 — Validator: schema validation (U6, M)

**Input**: Spec AC4, AC26, AC47, ETP `build_parameters_schema()` function
**Action**:
- `ecospheric_harness/validator.py`:
  - `SchemaValidator` class:
    - `validate(resolved: ResolvedCall) -> ValidationResult`
    - Uses `etp.describe.build_parameters_schema(command)` to get JSON Schema for the command's params
    - Validates `resolved.params` against the schema
    - Returns `ValidationResult(ok=True)` or `ValidationResult(ok=False, errors=[...])` with schema details
    - Strips `_input_target` from params before validation (harness-internal key, not in schema)
    - Handles `additionalProperties` correctly — schema may set this to False
  - `ValidationResult` dataclass: `ok: bool`, `errors: list[str]`
- `tests/test_validator.py`:
  - Valid params pass
  - Missing required param → error with param name
  - Wrong type (string for integer param) → error
  - Extra unknown param → error (if additionalProperties=False)
  - `_input_target` stripped before validation
  - Boolean param given as string → error
  - Array param given as non-list → error
  - Empty params on command with no required params → ok
**Output**: Schema validator using ETP's build_parameters_schema
**Verification**: `pytest tests/test_validator.py` passes, AC4 verified

#### T2.3 — Preflight checks (U5, S)

**Input**: Spec "Preflight Checks" section (full code provided), AC41, AC42, AC43
**Action**:
- `ecospheric_harness/preflight.py`:
  - `PreflightChecker` class (exact API from spec):
    - `__init__(artifacts: ArtifactManager, workdir: Path)`
    - `check_planar_crs(command, artifact) -> PreflightResult` — uses `pyproj.CRS(artifact.crs).is_geographic`
    - `check_disk(estimated_bytes, input_artifact, expansion_factor) -> PreflightResult`
  - `PreflightResult` imported from `intents.py`
- `tests/test_preflight.py`:
  - **Planar CRS not required**: command with `requires_planar_crs=False` → ok regardless of artifact CRS
  - **Planar CRS required, planar input**: `requires_planar_crs=True`, artifact CRS = EPSG:3857 → ok
  - **Planar CRS required, geographic input**: `requires_planar_crs=True`, artifact CRS = EPSG:4326 → error with actionable message (AC41)
  - **Planar CRS required, no artifact**: → ok (no input to check)
  - **Planar CRS required, unknown CRS**: artifact.crs = None → error
  - **Planar CRS required, unparseable CRS**: artifact.crs = "INVALID" → error
  - **Disk check under limit**: → ok
  - **Disk check over limit**: → error with current/limit MB (AC42)
  - **Disk check with input_artifact**: estimates input_size × expansion_factor
  - **Disk check with no input, no estimate**: falls back to 500 MB
**Output**: Working preflight checker
**Verification**: `pytest tests/test_preflight.py` passes, AC41, AC42 verified

---

### Wave 3: Execution & Resolution

#### T3.1 — Executor: subprocess invocation & param serialization (U7, L)

**Input**: Spec "Executor" section (full code provided), AC27, AC28, AC29, AC30, AC31, AC32, AC35, AC43, AC47, A7
**Action**:
- `ecospheric_harness/executor.py`:
  - `ToolExecutor` class (API from spec):
    - `__init__(subprocess_timeout: int = 300)`
    - `execute(tool, command, params, input_artifact, workdir) -> ExecuteResult`
    - `_route_input(input_artifact, command, params) -> list[str]` — parameter-aware routing:
      1. Positional `input` (name without `--`) → append path as positional arg
      2. `--input` flag → `--input <path>`
      3. `_input_target` in params → find matching param, route to it
      4. None of above → raise ValueError
    - `_serialize_params(params, command) -> list[str]` — type-driven serialization:
      - `type=="string"` + list value → comma-join (A3/B3)
      - `type=="string"` + string → as-is
      - `type=="array"` + list → single flag + space-separated values
      - `type=="boolean"` True → bare flag; False → omit
      - `type=="number"/"integer"` → flag + stringified value
      - Reverse-map: property name (underscore) → `ParameterDescriptor.name` (hyphen)
    - Appends `--json` as bare flag after all params (A7)
    - `subprocess.run` with timeout → `ExecuteResult`
    - JSON parse stdout → envelope; on parse failure, construct error envelope
    - Timeout → `subprocess.TimeoutExpired` → return error envelope with `error.type="timeout"`
  - `ExecuteResult` imported from `intents.py`
- `tests/test_executor.py`:
  - **Uniform after-command placement**: `--output` after subcommand (AC27)
  - **Command name tokenization**: `"raster clip"` → `["raster", "clip"]` (AC28)
  - **Param serialization — string+list (comma-join)**: bbox `["-121.5","38.2","-121.3","38.4"]` → `--bbox "-121.5,38.2,-121.3,38.4"` (AC29, AC47)
  - **Param serialization — string+string (as-is)**: bbox `"-121.5,38.2,-121.3,38.4"` → `--bbox "-121.5,38.2,-121.3,38.4"`
  - **Param serialization — array+list (space-separated)**: `--flag val1 val2 val3` (AC29)
  - **Boolean True → bare flag**: `--overwrite` (AC30)
  - **Boolean False → omitted**: no flag
  - **Integer/number → flag + value**: `--threshold 500`
  - **Name reverse-map**: model emits `min_area` → CLI gets `--min-area` (AC31)
  - **Input routing — positional**: command with `input` param → path as positional arg
  - **Input routing --input flag**: command with `--input` param → `--input <path>`
  - **Input routing — _input_target**: `hydro basins` with `_input_target="d8-pntr"` → `--d8-pntr <path>`
  - **Input routing — no match**: no input param, no `_input_target` → ValueError
  - **--json appended**: always last arg
  - **Envelope capture**: mock subprocess returns JSON → parsed correctly
  - **Invalid JSON**: mock subprocess returns garbage → error envelope constructed
  - **Timeout**: mock subprocess raises TimeoutExpired → error envelope with type="timeout" (AC43)
  - **Successful execution**: mock subprocess returns success envelope → ExecuteResult with correct fields
**Output**: Working executor with type-driven serialization and parameter-aware input routing
**Verification**: `pytest tests/test_executor.py` passes, AC27-AC31, AC35, AC43, AC47 verified

#### T3.2 — Intent resolver (U8, M)

**Input**: Spec "Intent Resolver" section (full code provided), AC3, AC21, AC48
**Action**:
- `ecospheric_harness/resolver.py`:
  - `IntentResolver` class (API from spec):
    - `__init__(catalog: list[IntentEntry])`
    - `resolve(intent, params, current_artifact) -> ResolvedCall | ResolutionError`
    - Primary filter: data_type match
    - Fallback: `data_type == "any"` + format-compatible
    - No artifact: only commands with `input_formats` None or empty
    - Multiple candidates: deterministic by tool precedence (`edd: 0, ese: 1`)
    - No tool names leaked in error messages (AC21)
  - Single-asset fetch enforcement (AC48): if intent is `fetch` and params missing `item` or `asset`, return `ResolutionError` with actionable message
- `tests/test_resolver.py`:
  - **Single candidate**: intent matches one entry → ResolvedCall
  - **Data_type disambiguation**: `clip` with raster artifact → `raster clip`; `clip` with vector artifact → `vector clip`
  - **Fallback to "any"**: `fetch` with `data_type="any"` + format match → ResolvedCall
  - **No artifact, no input needed**: `search` (input_formats=[]) → ResolvedCall
  - **No artifact, needs input**: `clip` with no artifact → ResolutionError "requires input data"
  - **No match**: unknown intent → ResolutionError "Unknown intent"
  - **No compatible tool**: `clip` with pointcloud artifact, no pointcloud clip command → ResolutionError
  - **Tool precedence**: two candidates, same data_type → edd wins (deterministic)
  - **No tool names in errors**: verify error messages don't contain "edd" or "ese" (AC21)
  - **Fetch without item/asset**: → ResolutionError directing to specify both (AC48)
  - **Fetch with item+asset**: → ResolvedCall
**Output**: Working intent resolver with data_type disambiguation
**Verification**: `pytest tests/test_resolver.py` passes, AC3, AC21, AC48 verified

---

### Wave 4: Menu & Corrections

#### T4.1 — Menu narrowing (U9, S)

**Input**: Spec "Menu Narrowing" section (full code provided), AC7, AC8, AC11
**Action**:
- `ecospheric_harness/menu.py`:
  - `available_intents(catalog, artifact, resolver) -> list[IntentOption]` (API from spec)
  - Filter by data_type + format compatibility
  - Dedup by intent name — show resolved entry's params (not union)
  - Exclude diagnostic/info/pipe categories
  - No artifact: only entries with `input_formats` None or empty
  - STAC search (metadata result, no artifact): menu unchanged (AC11)
  - Direct-data search (vector artifact): menu narrows by data_type (AC11)
  - Cap at 15 options (spec says ≤15) — if more, prioritize by category diversity
- `tests/test_menu.py`:
  - **No artifact**: only no-input commands shown
  - **Raster artifact**: only raster-compatible intents shown
  - **Vector artifact**: only vector-compatible intents shown
  - **Dedup shows resolved params**: `clip` with raster → shows `raster clip` params, not `vector clip` params
  - **Diagnostic excluded**: `doctor`, `info` never appear
  - **STAC search (no artifact)**: menu unchanged from initial
  - **Direct-data search (vector artifact)**: menu narrows to vector ops
  - **≤15 options**: cap enforced
**Output**: Working menu narrowing with dedup
**Verification**: `pytest tests/test_menu.py` passes, AC7, AC8, AC11 verified

#### T4.2 — Corrections: undo/redo (U10, M)

**Input**: Spec "Correction Handling" section (full code + traces provided), AC12-AC17, AC37, AC39, AC40
**Action**:
- `ecospheric_harness/corrections.py`:
  - `CorrectionHandler` class (API from spec):
    - `__init__(artifacts, steps, executor, resolver, workdir)`
    - `undo() -> CorrectionResult` — find last successful non-undone step, mark undone, `artifacts.undo()`
    - `redo(params) -> CorrectionResult` — two paths:
      1. Replace-current (target not undone): input=previous, `replace_current()`, mark old step undone
      2. Post-undo (target undone): input=current, `store()`
    - Both paths: execute into fresh temp, only mutate on success (atomic)
    - Failed redo: no StepRecord, no state change (AC16)
    - `_build_artifact(result, command) -> Artifact` — construct from execution result
  - All traces from spec must be verifiable:
    - Redo trace 1 (replace-current)
    - Redo trace 2 (post-undo)
    - Undo after redo (state machine cycle)
    - Failed redo
- `tests/test_corrections.py`:
  - **Undo at step 2**: step2 undone, current=step1, previous=None (AC12)
  - **Undo at step 1**: error, pipeline continues (AC14)
  - **Redo replace-current**: step2→step2', current=step2', previous=step1 (AC13)
  - **Redo post-undo**: after undo, redo → current=step2', previous=step1 (AC39)
  - **Undo after redo**: step2' undone, current=step1 (AC40)
  - **Redo failure**: artifacts untouched, step state unchanged (AC16)
  - **Redo no previous (replace path)**: error (AC15)
  - **Redo no step to redo**: error
  - **Provenance after corrections**: undone excluded, redone included (AC17, AC37)
  - **Atomicity**: redo execution fails → verify artifacts.current, artifacts.previous, step states all unchanged
**Output**: Working undo/redo with atomic redo and correct state machine
**Verification**: `pytest tests/test_corrections.py` passes, AC12-AC17, AC37, AC39, AC40 verified

---

### Wave 5: Orchestrator

#### T5.1 — Multi-turn orchestration loop (U12, L)

**Input**: Spec "Multi-Turn Loop" section, "Model Communication" section, "Search results as turn state" section, "Error Handling" table, AC3, AC5-AC11, AC18-AC20, AC22, AC33, AC34, AC44, AC50
**Action**:
- `ecospheric_harness/orchestrator.py`:
  - `Orchestrator` class:
    - `__init__(config, registry, resolver, validator, executor, artifacts, preflight, menu, corrections, provenance)`
    - `run(prompt: str) -> PipelineResult`
    - Multi-turn loop:
      1. Build system prompt with turn state
      2. Build `emit_intent` tool definition with repopulated enum
      3. Call model (httpx to OpenRouter)
      4. Parse `emit_intent` response → intent object
      5. Handle terminal intents: `complete` (persist + provenance + summary), `failed` (partial result)
      6. Handle correction intents: `undo` → corrections.undo(), `redo` → corrections.redo()
      7. Handle operation intents:
         a. Resolve intent → ResolvedCall
         b. Validate params against schema
         c. Preflight checks (planar CRS, disk)
         d. Execute
         e. Parse envelope: success → build artifact, store; error → return to model
         f. Build turn state for next turn
      8. Search mode branching (A8): `data_type=="metadata"` → turn state only; `data_type=="vector"` → artifact stored
      9. Turn state construction:
         - `current_artifact` (format, data_type, crs, bbox, size_mb) — from envelope data (A4, A5)
         - `available_intents` — from menu module
         - `can_undo` — from artifacts.can_undo()
         - `last_result` — status, step, intent
         - `search_results` — source-shape-aware (STAC: items list; direct-data: feature_count/crs/bounds)
         - `failed_attempts` — counter for failed redos (omitted when 0). Orchestrator maintains `_failed_redo_count: int`, resets to 0 on any successful operation/undo/redo, increments on failed redo only. Included in turn state dict when > 0.
      10. Max turns check (AC20)
    - `_build_system_prompt(turn_state) -> str` — rules 1-13 + turn state JSON
    - `_build_emit_intent_tool(available_intents) -> dict` — function-calling tool def with repopulated enum
    - `_call_model(system_prompt, tool_def, user_prompt) -> dict` — httpx POST to OpenRouter
    - `_build_artifact(envelope, output_path, step_number) -> Artifact` — extract format, data_type, crs (A4), bbox (A5)
    - `_build_search_turn_state(envelope, output_path) -> dict` — STAC vs direct-data shapes
    - `_parse_search_results(envelope, output_path) -> dict` — capped items for STAC, feature_count for direct-data
    - Extra envelope keys ignored without error (AC50)
- `tests/test_orchestrator.py` (unit tests with mocked model + mocked tools):
  - **Single-step success**: model emits one intent → resolved → executed → complete
  - **Two-step pipeline**: model emits step1, step2, complete → both stored, provenance correct (AC5)
  - **Failed step preserves artifacts**: step2 fails → artifacts unchanged, model retries (AC6)
  - **Search STAC**: search_stac → turn state with items, no artifact stored (AC11)
  - **Search OSM**: search_osm → artifact stored, menu narrowed (AC11)
  - **Search→fetch→process**: 3-step STAC pipeline (AC10)
  - **Direct-data→process**: 2-step OSM pipeline (AC9)
  - **Complete intent**: persists artifact, writes provenance, returns PipelineResult (AC18)
  - **Failed intent**: returns partial result (AC19)
  - **Max turns**: reaches limit → partial result with "pipeline incomplete" (AC20)
  - **Turn state construction**: verify all fields present and correct
  - **can_undo in turn state**: False initially, True after 2 steps (AC34)
  - **Search result cap**: STAC results capped at search_cap, results_file path present (AC44)
  - **Extra envelope keys ignored**: ESE envelope with `ese_version` → no error (AC50)
  - **Invalid model response**: unparseable → error returned to model, retries
  - **Schema validation failure**: invalid params → rejection, artifacts preserved, model retries (AC4)
  - **Preflight rejection**: planar CRS mismatch → error to model, artifacts preserved
  - **emit_intent enum repopulated**: each turn gets different enum based on available_intents (AC8)
  - **failed_attempts counter**: failed redo increments counter, counter in turn state when > 0, resets to 0 on successful operation/undo/redo
**Output**: Working multi-turn orchestration loop
**Verification**: `pytest tests/test_orchestrator.py` passes, AC3, AC5-AC11, AC18-AC20, AC34, AC44, AC50 verified

---

### Wave 6: CLI & Python API

#### T6.1 — CLI entry point & public API (U13, M)

**Input**: Spec "Harness CLI" section, "Python API" section, AC22-AC26
**Action**:
- `ecospheric_harness/__init__.py`:
  - Export `Harness` class (public API)
- `ecospheric_harness/__main__.py`:
  - CLI parsing with `argparse`:
    - Positional: `prompt` (optional, can be omitted for `--list-*` modes)
    - `--model` (default: `openrouter/z-ai/glm-5.2`)
    - `--list-tools` → JSON array of `{name, version, binary, command_count}` (AC24)
    - `--list-intents` → JSON array of `{intent, description, tool, command, required_params, data_type}` (AC25)
    - `--dry-run` → show resolved tool calls, validation, planned argv without executing (AC26)
    - `--max-turns` (default: 20)
    - `--subprocess-timeout` (default: 300)
    - `--disk-limit-gb` (default: 2)
    - `--search-cap` (default: 20)
  - `Harness` class (public API):
    - `__init__(tools, subprocess_timeout, disk_limit_gb, search_cap, max_turns, model)`
    - `run(prompt) -> PipelineResult`
    - `undo() -> CorrectionResult`
    - `redo(params) -> CorrectionResult`
    - Properties: `tools`, `intents` (for `--list-*`)
  - All CLI flags kebab-case (spec convention)
- `tests/test_cli.py`:
  - **--list-tools**: JSON output with correct shape (AC24)
  - **--list-intents**: JSON output with deduplicated intents (AC25)
  - **--dry-run**: shows resolved calls without execution (AC26)
  - **Default prompt**: runs pipeline
  - **--model override**: passes model to config
  - **Missing OPENROUTER_API_KEY**: error message
  - **Python API**: `Harness(tools=["edd","ese"]).run("...")` returns PipelineResult (AC23)
  - **Python API undo/redo**: `h.undo()`, `h.redo(params)` work (AC22)
**Output**: Working CLI and Python API
**Verification**: `pytest tests/test_cli.py` passes, AC22-AC26 verified

---

### Wave 7: Integration Tests

#### T7.1 — End-to-end pipeline integration tests (U14, L)

**Input**: All ACs, spec integration test section
**Action**:
- `tests/test_integration.py`:
  - Mock tool binaries (subprocess mocks returning canned envelopes)
  - Mock model responses (scripted emit_intent sequences)
  - **2-step vector pipeline** (search_osm → buffer): AC9
  - **4-step raster pipeline** (search_stac → fetch → clip → reproject): AC10
  - **Undo + redo mid-pipeline** (both redo paths): AC37, AC39
  - **Undo after redo** (state machine cycle): AC40
  - **File-path handoff** for both raster and vector
  - **Planar CRS preflight rejection**: geographic CRS → error to model → model reprojects → success (AC41)
  - **Disk limit rejection**: small limit → error → pipeline handles (AC42)
  - **Subprocess timeout**: mock timeout → clean error (AC43)
  - **Format normalization**: artifact with "tif" → matched as "geotiff" (AC33)
  - **Full provenance chain**: verify after corrections (AC17)
  - **Search result cap**: STAC returns 47, cap at 20, results_file has all (AC44)
  - **Single-asset fetch enforcement**: fetch without item/asset → error (AC48)
  - **INTENT_OVERRIDES**: `proj distance` → `geodesic_distance`, `vector distance` → `distance` (AC49)
  - **Extra envelope keys**: ESE `ese_version` ignored (AC50)
  - **Type-driven serialization**: string+list → comma-join; array+list → space-separated (AC47)
  - **Param name reverse-map**: underscore → hyphen (AC31)
  - **Boolean serialization**: True → bare flag, False → omitted (AC30)
  - **Diagnostic exclusion**: no `doctor`/`info`/`pipe` intents in menu (AC36)
  - **Single-word intents**: `fetch`, `search` not stripped (AC38)
**Output**: Comprehensive integration test suite
**Verification**: `pytest tests/test_integration.py` passes, all 50 ACs covered

---

### Wave 8: Judge & Quality

#### T8.1 — Quality gates: lint, types, coverage, AC checklist (U15, S)

**Input**: All files, all ACs
**Action**:
- Run `ruff check .` — fix any issues
- Run `mypy --strict ecospheric_harness/` — fix any type errors
- Run `pytest --cov=ecospheric_harness --cov-report=term-missing` — verify ≥90% coverage (AC45)
- Manual AC checklist: verify each of the 50 ACs is tested
- Verify `pip install -e .` works clean
- Verify `uv lock` produces a valid lockfile
- Check all public API exports from `__init__.py`
- Verify no imports from ETP private symbols (spec boundary)
- Verify no imports from EDD or ESE (runtime discovery only)
**Output**: Clean lint, types, coverage, AC coverage report
**Verification**: `ruff check . && mypy --strict ecospheric_harness/ && pytest --cov-fail-under=90` all pass (AC45, AC46)

---

## Test Strategy Summary

### Unit Tests (per module)

| Module | Test File | Key Test Cases | ACs |
|--------|-----------|----------------|-----|
| `config.py` | `test_config.py` | env vars, CLI overrides, defaults | — |
| `intents.py` | `test_intents.py` | parse all types, validation, round-trip | — |
| `result.py` | `test_result.py` | StepRecord, PipelineResult.summary, provenance filter | AC17 |
| `artifact.py` | `test_artifact.py` | window shift, preserve on fail, undo, replace_current, disk tracking, format normalize | AC5, AC6 |
| `registry.py` | `test_registry.py` | alias rules, source pairing, overrides, diagnostic exclusion, param denylist | AC1, AC2, AC36, AC38, AC49 |
| `validator.py` | `test_validator.py` | valid/invalid params, _input_target strip, type mismatches | AC4 |
| `preflight.py` | `test_preflight.py` | planar CRS, disk limit, CRS parsing | AC41, AC42 |
| `executor.py` | `test_executor.py` | serialization, input routing, tokenization, timeout, envelope capture | AC27-AC31, AC35, AC43, AC47 |
| `resolver.py` | `test_resolver.py` | disambiguation, fallback, no-leak, fetch enforcement | AC3, AC21, AC48 |
| `menu.py` | `test_menu.py` | narrowing, dedup, cap, diagnostic exclusion | AC7, AC8, AC11 |
| `corrections.py` | `test_corrections.py` | undo, redo both paths, atomicity, undo-after-redo | AC12-AC16, AC37, AC39, AC40 |
| `provenance.py` | `test_provenance.py` | chain building, undone excluded, redone included | AC17 |
| `orchestrator.py` | `test_orchestrator.py` | full loop, search modes, turn state, complete/failed, max turns | AC3, AC5-AC11, AC18-AC20, AC34, AC44, AC50 |
| `__main__.py` | `test_cli.py` | list-tools, list-intents, dry-run, API | AC22-AC26 |

### Integration Tests

| Test | ACs |
|------|-----|
| 2-step vector pipeline (search_osm → buffer) | AC9 |
| 4-step raster pipeline (search_stac → fetch → clip → reproject) | AC10 |
| Undo + redo (both paths) | AC13, AC37, AC39 |
| Undo after redo (cycle) | AC40 |
| Planar CRS preflight rejection | AC41 |
| Disk limit rejection | AC42 |
| Subprocess timeout | AC43 |
| Format normalization | AC33 |
| Search result cap | AC44 |
| Single-asset fetch enforcement | AC48 |
| INTENT_OVERRIDES collision avoidance | AC49 |
| Extra envelope keys ignored | AC50 |
| Type-driven serialization | AC47 |
| Param name reverse-map | AC31 |
| Boolean serialization | AC30 |
| Diagnostic exclusion | AC36 |
| Single-word intent rule | AC38 |

### Coverage Target

≥90% (AC45). All public API tested. All error paths tested. Integration tests cover all 50 ACs.

---

## Module Independence Matrix

| Module | Can build independently? | Ordering constraint |
|--------|-------------------------|---------------------|
| `config.py` | ✅ Yes | Wave 1 (no deps) |
| `intents.py` | ✅ Yes (types only) | Wave 1 (no deps) |
| `result.py` | ✅ Yes (types only) | Wave 1 (no deps) |
| `artifact.py` | ⚠️ Depends on `intents.py` for `Artifact` (actually `Artifact` lives in `artifact.py` — no dep) | Wave 1 |
| `provenance.py` | ⚠️ Depends on `result.py` for `StepRecord` | Wave 1 |
| `registry.py` | ⚠️ Depends on `intents.py` for `IntentEntry`, `RegisteredTool` | Wave 2 |
| `validator.py` | ⚠️ Depends on `intents.py` for `ResolvedCall`, `ValidationResult` | Wave 2 |
| `preflight.py` | ⚠️ Depends on `artifact.py` for `ArtifactManager` | Wave 2 |
| `executor.py` | ⚠️ Depends on `intents.py`, `artifact.py` | Wave 3 |
| `resolver.py` | ⚠️ Depends on `intents.py`, `registry.py` (for `IntentEntry`) | Wave 3 |
| `menu.py` | ⚠️ Depends on `registry.py`, `resolver.py` | Wave 4 |
| `corrections.py` | ⚠️ Depends on `artifact.py`, `executor.py`, `resolver.py` | Wave 4 |
| `orchestrator.py` | ❌ Depends on everything | Wave 5 |
| `__main__.py` | ❌ Depends on `orchestrator.py` | Wave 6 |
| Integration tests | ❌ Depends on everything | Wave 7 |

---

## Risk Assessment

### High-risk areas

1. **Registry — EDD search-source pairing (A3)**: No clean API to match search descriptors with source prefixes. Index-order pairing is fragile. **Mitigation**: Fingerprint test that breaks loudly if pairing changes. Future EDD enhancement: add source to descriptor metadata. Reviewer flagged as problematic-but-acceptable for v0.2.

2. **Orchestrator — `failed_attempts` counter**: The spec requires a `failed_attempts` counter in turn state when non-zero (failed redo attempts). The orchestrator must explicitly track this outside the StepRecord chain — failed redos don't create StepRecords but the counter must still increment and be included in turn state. **Mitigation**: Orchestrator maintains `_failed_redo_count: int` as instance state, resets to 0 on successful operation/redo/undo, includes in turn state when > 0. Test case explicitly verifies counter increments and resets.

3. **Orchestrator — model communication**: The OpenRouter API call shape (function-calling format, system prompt, tool def) needs to match the model's expectations. GLM 5.2 supports OpenAI-compatible function-calling. **Mitigation**: Test with mocked httpx responses first; do a live smoke test after unit tests pass.

4. **Corrections — state machine correctness**: The undo/redo state machine has 6+ traced scenarios. Getting any wrong corrupts the pipeline. **Mitigation**: TDD — write tests for all 6 traces first, then implement. Each trace is an atomic test case.

5. **Executor — param serialization edge cases**: Type-driven serialization has many branches. Model may emit params in unexpected shapes (e.g., `bbox` as string vs list). **Mitigation**: Exhaustive unit test matrix covering all type+value combinations.

### Medium-risk areas

5. **Artifact CRS extraction (A4)**: No standard envelope field for CRS. Best-effort extraction may miss some tools' output. **Mitigation**: Graceful None handling in preflight. Test with real ESE envelope shapes.

6. **Menu dedup with resolver**: Showing the resolved entry's params requires calling the resolver during menu build. If resolver has side effects or is expensive, this could be slow. **Mitigation**: Resolver is pure (no I/O). Verify in tests.

### Low-risk areas

7. **Config, types, provenance**: Straightforward dataclasses and pure functions. Standard TDD.

8. **CLI**: argparse + JSON output. Well-understood territory.

---

## Dispatch Summary

| Wave | Tasks | Models | Est. Time | Parallelism |
|------|-------|--------|-----------|-------------|
| 1 | T1.1-T1.4 | Qwen 3.7 Plus ×4 | ~30 min | 4-way parallel |
| 2 | T2.1-T2.3 | Qwen 3.7 Plus ×3 | ~45 min | 3-way parallel |
| 3 | T3.1-T3.2 | Qwen 3.7 Plus ×2 | ~45 min | 2-way parallel |
| 4 | T4.1-T4.2 | Qwen 3.7 Plus ×2 | ~30 min | 2-way parallel |
| 5 | T5.1 | Qwen 3.7 Plus ×1 | ~60 min | Single |
| 6 | T6.1 | Qwen 3.7 Plus ×1 | ~30 min | Single |
| 7 | T7.1 | MiMo v2.5 Pro ×1 | ~45 min | Single |
| 8 | T8.1 | GLM 5.2 ×1 | ~20 min | Single |

**Total**: 15 tasks, 8 waves, ~5 hours elapsed if waves are sequential with parallel tasks within each wave.

**Fusion panels**: Per Pilgrim's config:
- Code: DeepSeek V4 Pro + Qwen 3.7 Plus (judge: DeepSeek V4 Pro)
- The plan uses Qwen 3.7 Plus as primary implementer. Judge runs after each wave.

---

## AC Coverage Matrix

| AC | Wave | Task | Test |
|----|------|------|------|
| AC1 | 2 | T2.1 | test_registry: discover tools |
| AC2 | 2 | T2.1 | test_registry: build catalog with aliases |
| AC3 | 3 | T3.2 | test_resolver: resolve intent |
| AC4 | 2 | T2.2 | test_validator: invalid params rejected |
| AC5 | 1 | T1.3 | test_artifact: window shift on success |
| AC6 | 1 | T1.3 | test_artifact: preserve on failure |
| AC7 | 4 | T4.1 | test_menu: narrowed intents |
| AC8 | 5 | T5.1 | test_orchestrator: enum repopulated |
| AC9 | 7 | T7.1 | integration: 2-step vector |
| AC10 | 7 | T7.1 | integration: 4-step raster |
| AC11 | 4 | T4.1 | test_menu: search modes |
| AC12 | 4 | T4.2 | test_corrections: undo |
| AC13 | 4 | T4.2 | test_corrections: redo |
| AC14 | 4 | T4.2 | test_corrections: undo at step 1 |
| AC15 | 4 | T4.2 | test_corrections: redo no previous |
| AC16 | 4 | T4.2 | test_corrections: redo failure |
| AC17 | 1 | T1.4 | test_provenance: undone excluded |
| AC18 | 5 | T5.1 | test_orchestrator: complete |
| AC19 | 5 | T5.1 | test_orchestrator: failed |
| AC20 | 5 | T5.1 | test_orchestrator: max turns |
| AC21 | 3 | T3.2 | test_resolver: no tool name leak |
| AC22 | 6 | T6.1 | test_cli: run pipeline |
| AC23 | 6 | T6.1 | test_cli: Python API |
| AC24 | 6 | T6.1 | test_cli: --list-tools |
| AC25 | 6 | T6.1 | test_cli: --list-intents |
| AC26 | 6 | T6.1 | test_cli: --dry-run |
| AC27 | 3 | T3.1 | test_executor: option placement |
| AC28 | 3 | T3.1 | test_executor: command tokenization |
| AC29 | 3 | T3.1 | test_executor: array serialization |
| AC30 | 3 | T3.1 | test_executor: boolean serialization |
| AC31 | 3 | T3.1 | test_executor: name reverse-map |
| AC32 | 5 | T5.1 | test_orchestrator: read format/data_type |
| AC33 | 7 | T7.1 | integration: format normalization |
| AC34 | 5 | T5.1 | test_orchestrator: can_undo |
| AC35 | 3 | T3.1 | test_executor: error.retryable |
| AC36 | 2 | T2.1 | test_registry: diagnostic exclusion |
| AC37 | 4 | T4.2 | test_corrections: undo+redo |
| AC38 | 2 | T2.1 | test_registry: single-word |
| AC39 | 4 | T4.2 | test_corrections: post-undo redo |
| AC40 | 4 | T4.2 | test_corrections: undo after redo |
| AC41 | 2 | T2.3 | test_preflight: planar CRS |
| AC42 | 2 | T2.3 | test_preflight: disk limit |
| AC43 | 3 | T3.1 | test_executor: timeout |
| AC44 | 5 | T5.1 | test_orchestrator: search cap |
| AC45 | 8 | T8.1 | quality: coverage ≥90% |
| AC46 | 8 | T8.1 | quality: ruff + mypy |
| AC47 | 3 | T3.1 | test_executor: type-driven serialization |
| AC48 | 3 | T3.2 | test_resolver: fetch enforcement |
| AC49 | 2 | T2.1 | test_registry: overrides |
| AC50 | 5 | T5.1 | test_orchestrator: extra keys |

All 50 ACs mapped to tasks and tests. No gaps.
