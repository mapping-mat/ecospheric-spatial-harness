# Ecospheric Agent Harness — Specification v0.2 (Draft v6)

## Changelog (Draft v5 → v6)

| ID | Severity | Change |
|----|----------|--------|
| B1 | BLOCKER | `reproject` uses `--to` (not `--crs`) — replaced all occurrences of `crs` as reproject parameter with `to` |
| B2 | BLOCKER | `clip` uses `--by` (vector mask file), not `--bbox` — input is positional, not `--input`. Replaced all clip-by-bbox examples with clip-by-mask |
| B3 | BLOCKER | Param serialization is now type-driven: `string` type + list value → comma-joined; `array` type + list → space-separated; `string` + string → as-is. EDD `--bbox` is a comma-separated string |
| B4 | BLOCKER | `INTENT_OVERRIDES` keyed on full command name (before stripping), not stripped intent — resolves `proj distance` vs `vector distance` collision |
| M1 | MAJOR | Two search modes: STAC (metadata listings → turn state, needs `fetch`) vs direct-data (OSM/geoBoundaries → writes vector to `--output`, stored as artifact, no `fetch` needed) |
| M2 | MAJOR | Search result schema is source-shape-aware: STAC → items with assets; direct-data → feature_count/crs/bounds (no per-item summary) |
| M3 | MAJOR | `CorrectionHandler.redo()` now passes `workdir` to `execute()`, stores `self._workdir` in `__init__`, uses `target.command_ref` (not `target.command`) |
| M4 | MAJOR | Harness enforces single-asset fetch: `--item` and `--asset` required; multi-item downloads out of scope for v0.2 |
| E1 | MINOR | Added note: tool-specific extra envelope keys (e.g. `ese_version`) are ignored by harness |
| E2 | MINOR | Added note: harness appends `--json` to all tool invocations; universally accepted by ETP-compatible tools |
| E3 | MINOR | Resolver "no artifact" branch checks `input_formats is None or len(input_formats) == 0` explicitly; notes positional input handling |
| E4 | MINOR | `--list-tools` output format: JSON array of `{name, version, binary, command_count}` |
| E5 | MINOR | Catalog JSON files are snapshots; always use `--describe` at runtime (promoted from staleness note) |
| E6 | MINOR | Error handling table: "Preflight: fetch non-singular selection → return error directing model to specify --item and --asset" |
| E7 | MINOR | Model system prompt Rule 13: commands needing mask/secondary input file → provide file path in params |

## Changelog (Draft v4 → v5)

| ID | Severity | Change |
|----|----------|--------|
| B1 | BLOCKER | Executor input routing is now parameter-aware: inspects `ParameterDescriptor` list to route artifact path as positional `input`, `--input` flag, or via `_input_target` for commands like `hydro basins` |
| B2 | BLOCKER | EDD `--describe --all` now wraps output in ETP success envelope (matching ESE pattern) for `ese` format |
| B3 | BLOCKER | Spec documents startup discovery of EDD source prefixes via `<tool> plugins --json`; harness builds `search_*` intents from discovered prefixes |
| B4 | BLOCKER | `StepRecord` now stores `tool_ref` and `command_ref` alongside string fields; `CorrectionHandler.redo()` uses typed references |
| M1 | MAJOR | Replaced hardcoded geographic CRS set with `pyproj.CRS(artifact.crs).is_geographic`; added pyproj to runtime deps |
| M2 | MAJOR | Documented `_input_target` mechanism for multi-input commands (e.g. `hydro basins` with `--d8-pntr`); added example in multi-turn loop |
| M3 | MAJOR | Added `"distance": "geodesic_distance"` to `INTENT_OVERRIDES` — resolves collision between `proj distance` and `vector distance` |
| M4 | MAJOR | `PreflightChecker.check_disk()` now accepts input artifact and computes estimate via `expansion_factor` (default 2.0×); falls back to 500 MB fixed estimate |
| M5 | MAJOR | Documented failed redo state machine: no StepRecord created, `failed_attempts` counter in turn state |
| M6 | MAJOR | Added note that ESE defines its own `CommandDescriptor` separately from ETP; harness reconstructs from JSON (resilient) |
| M7 | MAJOR | Clarified `can_undo()` after post-undo redo returns True (previous exists from store); undo is valid and intentional |
| E1 | MINOR | Added note about search result pagination for future v0.3 |
| E2 | MINOR | Updated AC26 to describe parameter validation and planned argv |
| E3 | MINOR | Added staleness note for catalog JSON files in docs/ |
| E4 | MINOR | Documented kebab-case convention for all CLI flags |
| E5 | MINOR | Documented `--list-intents` output format as JSON array of intent objects |
| E6 | MINOR | Verified `available_intents` example shows fetch with all required params (confirmed correct) |
| E7 | MINOR | Added model system prompt guard: "If `can_undo` is false, do not emit `undo`" |

## Changelog (Draft v3 → v4)

| ID | Severity | Change |
|----|----------|--------|
| B2 | BLOCKER | Fixed `error.error_type` → `error.type` throughout (matches ETP envelope: `error.type`) |
| B4 | BLOCKER | Redesigned redo state machine to handle both post-undo redo and replace-current redo; added `replace_current()` to ArtifactManager |
| B5 | BLOCKER | Added single-word command intent rule (`len(parts) == 1` → intent = command name) |
| B6 | BLOCKER | Search results file path included in turn state; model passes path to `fetch` via `--stac` param |
| M1 | MAJOR | Canonical bbox format: list of 4 floats `[xmin, ymin, xmax, ymax]` — executor serializes to space-separated CLI args |
| M4 | MAJOR | Search result schema defined with cap (default 20 items, configurable); full results on disk |
| M6 | MAJOR | `requires_planar_crs` preflight check — reads field from CommandDescriptor (already exposed by both EDD and ESE) |
| M7 | MAJOR | Disk usage limits — configurable max (default 2 GB), checked before each execution |
| M8 | MAJOR | Configurable subprocess timeout — `HARNESS_SUBPROCESS_TIMEOUT` env var, constructor param, default 300s |
| — | CONVERGENCE | Removed option placement table — ESE accepts `--output` at both global and command level; harness uses uniform `after_command` placement for all tools |
| — | CONVERGENCE | Removed artifact metadata inference section — EDD and ESE now both populate `format`/`data_type` in envelopes directly |
| — | CONVERGENCE | Updated assumption 3 to reflect `requires_planar_crs` field on CommandDescriptor |
| — | CONVERGENCE | Updated assumption 11 — both tools now populate `format`/`data_type`; harness reads directly, no inference needed |
| — | CONVERGENCE | Updated assumption 12 — EDD search commands now all named `"search"` with `--source` param |

---

## Assumptions

1. **Intent**: Build a harness between an LLM and ETP-compatible tools (EDD, ESE). The model orchestrates multi-step geospatial pipelines. The harness enforces schema, resolves intents to tools, chains tool I/O, manages intermediate artifacts, and supports conversational corrections.

2. **Existing codebase**: Three ETP-compatible tools — `etp` (shared protocol, v0.1.0), `edd` (data discovery/download, v0.5.0), `ese` (spatial engine, v0.5.0). All expose `--describe` catalogs with `CommandDescriptor` objects, accept input via CLI file paths, and emit ETP success/error envelopes on stdout.

3. **ETP provides**:
   - `CommandDescriptor` with `input_formats`, `output_formats`, `data_type` (defaults `"any"`), `category`, `name`, `parameters` (list of `ParameterDescriptor`), `requires_planar_crs` (boolean)
   - `ParameterDescriptor` with `name` (CLI flag including `--`), `description`, `type` (`string`/`number`/`boolean`/`array`/`integer`), `required`, `default`, `pattern`
   - `build_function_block()` / `to_openai_tool()` / `to_anthropic_tool()` — per-command LLM tool defs
   - `build_parameters_schema()` — public function (re-exported from `etp.describe`) that builds a JSON Schema from a descriptor's parameters
   - Pipe contract: 16-byte header + Arrow IPC — **vector-only** (`GeoDataFrame` → WKB → Arrow IPC). No raster payload. (Not used by harness v0.2 — file-path handoff only.)
   - Error envelopes: `error.type`, `error.message`, `error.suggestion`, `error.exit_code`, `error.retryable`, `error.retry_after_ms`, `error.param_path`, `error.details`
   - Success envelopes: `tool`, `tool_version`, `schema_version`, `status`, `command`, `data` (dict with `format`, `data_type`), `warnings`
   - Tool-specific extra envelope keys (e.g. ESE's `ese_version`) may be present and are ignored by the harness.

4. **Model role**: Emit structured intent commands. The model reasons at the **operation level** — it does not name tools or binaries. The harness resolves intents to concrete tool+command invocations.

5. **No fine-tuning** (v0.2). The intent protocol is designed for small-model compatibility.

6. **No web frontend** (v0.2). CLI/library first.

7. **Intermediate data strategy**: Sliding window of **two** — current (most recent successful output) and previous (its input). Supports `undo` and `redo`. Failed steps do not consume or free any artifact.

8. **Pipeline linearity**: No branching, parallel execution, or multi-step undo. Corrections limited to one step back.

9. **Transport convention**: All artifact data flows via **file-path handoff** (`--output <tmpfile>` for writes, input via parameter-aware routing — positional `input`, `--input`, or `_input_target` — for reads). The JSON envelope is always on stdout. No stream is shared between binary data and JSON.

10. **CLI invocation**: Both EDD and ESE accept `--output` as a **command-level option** after the subcommand. The harness uses uniform `after_command` option placement for all tools — no option placement table needed.

11. **Artifact metadata contract**: Both EDD and ESE populate `format` and `data_type` in their success envelope `data` blocks (convergence complete). The harness reads these directly — no inference from command descriptors needed.

12. **Search vs download**: EDD has a single `search` command (with `--source` param for 9 sources) and a `fetch` command (downloads binary assets). There are two search modes:
    - **STAC catalog search** (source="@stac"): Returns metadata listings. Search results are **turn state** (sent to the model, not stored as artifacts). The model must `fetch` to get chainable artifacts.
    - **Direct-data search** (source="@osm", "@geoboundaries", "@overture", etc.): Writes vector features directly to `--output`. The output IS the chainable artifact and is stored in the sliding window. No `fetch` is needed.

13. **"Done" looks like**: User types a natural-language request → model orchestrates a multi-tool pipeline → harness validates and executes → user can correct mid-flight → final result has full provenance.

---

## A. Objective

**Primary**: Build a multi-turn orchestration harness that lets an LLM execute sequential ETP-compatible tool pipelines through an intent-resolution layer with schema validation, a two-artifact sliding window, and conversational correction support.

**Secondary goals**:
- Auto-discover ETP-compatible tools via `--describe`
- Build an intent catalog with alias resolution from real command names
- Resolve model-emitted intents to concrete tool+command invocations, disambiguating by `data_type`
- Validate every resolved tool call against ETP schema before execution
- Narrow the available intent menu after each step based on `data_type` compatibility
- Surface tool results (metadata, not raw data) to the model
- Support `undo` and `redo` as first-class intents with correct, atomic artifact window management
- Stitch provenance across all pipeline steps
- Treat search results as turn state (not artifacts); only file-producing commands create chainable artifacts
- Preflight checks: `requires_planar_crs` validation, disk usage limits, configurable subprocess timeout
- Expose a Python API and CLI
- Keep the model's output space bounded for future small-model compatibility

**Out of scope (v0.2)**:
- Fine-tuning or model training
- Web frontend / HTTP API server
- Branching or parallel pipeline execution
- Persistent artifact catalog
- Tool installation or dependency management
- Authentication, rate-limiting, multi-tenant isolation
- Streaming model responses
- Corrections beyond one step back
- Extending ETP pipe to carry raster data (tracked as TODO)
- Human-in-the-loop confirmation (model decides autonomously in v0.2)

---

## B. Commands

### Transport convention (canonical)

**One rule**: JSON envelope on stdout, artifact data on disk via `--output <tmpfile>`. No stream is shared.

- The harness **always** passes `--output <tmpfile>` to the tool (as a command-level option after the subcommand)
- The harness reads the JSON envelope from stdout
- The harness reads the artifact data from the tmpfile path referenced in the envelope's `data` block
- Vector and raster data both flow via file paths — uniform treatment

### CLI option placement

**Uniform for all tools**: `--output` is placed **after the subcommand** as a command-level option. Input artifact routing is parameter-aware (see Executor section). For most commands, input is a **positional** argument:

```
ese raster clip --output <tmpfile> <step1> --by /tmp/harness/mask_abc.geojson --json
ese hydro basins --output <tmpfile> --d8-pntr <step1> --threshold 500   # _input_target routes to --d8-pntr
edd search --source @stac --bbox "-121.5,38.2,-121.3,38.4" --output <tmpfile> --json
edd fetch --stac <search_results> --asset visual --item "S2B..." --output <tmpfile> --json
```

No option placement table needed.

### Command name tokenization

Real `CommandDescriptor.name` values contain spaces: `"raster clip"`, `"hydro fill-sinks"`, `"search"`, `"fetch"`. When invoking a tool via subprocess, the harness **splits the command name on spaces** into separate argv tokens:

```python
args = [tool.binary]
args.extend(command.name.split())  # "raster clip" → ["raster", "clip"], "fetch" → ["fetch"]
```

### Intent Protocol

The model emits intents via a **single function-calling tool** called `emit_intent`:

```json
{
  "name": "emit_intent",
  "description": "Emit a geospatial pipeline intent. One per turn.",
  "parameters": {
    "type": "object",
    "additionalProperties": false,
    "properties": {
      "intent": {
        "type": "string",
        "description": "Operation from available_intents, or 'undo', 'redo', 'complete', 'failed'",
        "enum": ["<repopulated per turn from available_intents>"]
      },
      "params": {
        "type": "object",
        "description": "Parameters for the operation.",
        "additionalProperties": true
      },
      "summary": { "type": "string" },
      "reason": { "type": "string" }
    },
    "required": ["intent"]
  }
}
```

**The `intent` enum is repopulated every turn** from the current `available_intents` list (≤15 options) plus the correction/terminal intents (`undo`, `redo`, `complete`, `failed`). The tool definition is re-sent to the model each turn. This keeps the enum bounded and ensures the model only sees relevant operations.

`summary` and `reason` are validated by the harness (not JSON Schema) — `summary` required when `intent="complete"`, `reason` required when `intent="failed"`.

**Canonical param formats**:
- `bbox` for EDD search: comma-separated string `"xmin,ymin,xmax,ymax"` — the ParameterDescriptor type is `"string"`, so the value is passed as-is to `--bbox`. Example: `--bbox "-121.5,38.2,-121.3,38.4"`. Note: No ESE command has a `--bbox` parameter.
- `to` for ESE reproject: string EPSG code `"EPSG:3857"` — passed as `--to EPSG:3857`
- `by` for ESE clip: string file path to a vector mask GeoJSON — passed as `--by /path/to/mask.geojson`
- All other params: match the `ParameterDescriptor` type

**Type-driven param serialization**: The executor serializes parameters based on the `ParameterDescriptor.type`, not by parameter name:
- `type == "string"` + value is a list → join with commas (e.g. `bbox: [xmin,ymin,xmax,ymax]` → `--bbox "-121.5,38.2,-121.3,38.4"`)
- `type == "string"` + value is already a string → pass as-is (e.g. `bbox: "-121.5,38.2,-121.3,38.4"` → `--bbox "-121.5,38.2,-121.3,38.4"`)
- `type == "array"` + value is a list → single flag + space-separated values (e.g. `--flag val1 val2 val3`)
- `type == "boolean"` + value is True → bare flag; False → omit
- `type == "number"/"integer"` + value → flag + stringified value

**Operation intents:**
```json
{"intent": "search_stac", "params": {"bbox": "-121.5,38.2,-121.3,38.4"}}
{"intent": "search_osm", "params": {"bbox": "-121.5,38.2,-121.3,38.4"}}
{"intent": "fetch", "params": {"stac": "/tmp/search_results.json", "asset": "visual", "item": "S2B_..."}}
{"intent": "clip", "params": {"by": "/tmp/harness/mask_abc.geojson"}}
{"intent": "reproject", "params": {"to": "EPSG:3857"}}
```

**Correction intents:**
```json
{"intent": "undo"}
{"intent": "redo", "params": {"to": "EPSG:4326"}}
```

**Terminal intents:**
```json
{"intent": "complete", "summary": "Downloaded S2B scene, clipped, reprojected to 3857"}
{"intent": "failed", "reason": "Could not reproject — unsupported CRS combination"}
```

### Search results as turn state

Search has two modes that produce different turn-state shapes:

**Mode A — STAC catalog search** (source="@stac"): Returns metadata listings (item IDs, descriptions, asset keys). Results are **turn state** only — no artifact is stored. The model must `fetch` to get chainable artifacts.

**Mode B — Direct-data search** (source="@osm", "@geoboundaries", "@overture", etc.): Writes vector features directly to `--output` as a GeoJSON/FlatGeobuf FeatureCollection. The output IS the chainable artifact and is stored in the sliding window. No `fetch` is needed.

For both modes:
- The full search results are written to a temp file
- The model reads the results and decides the next intent
- For STAC: `available_intents` after search is unchanged (no artifact to narrow on)
- For direct-data: `available_intents` narrows based on the artifact's `data_type` (typically "vector")

**Search result schema in turn state** (source-shape-aware):

For STAC sources (capped at `search_result_cap` items, default 20, configurable via `HARNESS_SEARCH_CAP`):

```json
{
  "search_results": {
    "source": "@stac",
    "total_count": 47,
    "returned_count": 20,
    "results_file": "/tmp/harness/search_abc123.json",
    "items": [
      {
        "id": "S2B_MSIL2A_20240615T185919",
        "title": "Sentinel-2B L2A scene",
        "assets": ["visual", "B01", "B02", "B03"],
        "bbox": [-121.5, 38.2, -121.3, 38.4],
        "datetime": "2024-06-15T18:59:19Z"
      }
    ]
  }
}
```

For direct-data sources (OSM, geoBoundaries, etc.), no per-item summary (could be thousands of features):

```json
{
  "search_results": {
    "source": "@osm",
    "feature_count": 342,
    "results_file": "/tmp/harness/search_def456.geojson",
    "format": "geojson",
    "data_type": "vector",
    "crs": "EPSG:4326",
    "bounds": [-121.5, 38.2, -121.3, 38.4]
  }
}
```

Items beyond the cap are still in the results file on disk. The model can request a different page or narrower bbox if needed.

If search results exceed the cap, the model can narrow the search bbox or add filters to reduce results. A future v0.3 may add pagination via a `search_page` intent.

### Intent Catalog and Alias Map

Real command names use spaces: `"raster clip"`, `"hydro fill-sinks"`, `"search"`, `"fetch"`, `"convert raster-format"`. See `docs/ese-command-catalog.json` (96 commands) and `docs/edd-command-catalog.json` (13 commands: `search` + `fetch` + `info` + `doctor` + `plugins`).

> **Note (E5)**: Catalog JSON files in docs/ are snapshots for reference only and may lag behind current tool state. Always use `--describe` at runtime for current state.

**Startup source discovery**: At startup, the harness calls `<tool> plugins --json` on each EDD tool to discover registered source prefixes. The `edd plugins --json` response includes a `plugins` array where each entry has a `prefix` field (e.g. `"@osm"`, `"@stac"`, `"@geoboundaries"`). For each source prefix `@X`, the registry creates an intent `search_X` mapped to `search --source @X`. This ensures the harness always has an up-to-date list of search sources rather than maintaining a hardcoded enumeration.

**Alias resolution rules** (applied at registry build time):

1. **Split command name on spaces into parts.**

2. **Single-word commands** (`len(parts) == 1`): The intent IS the command name. No category stripping.
   - `"fetch"` → intent `fetch`
   - `"search"` → intent `search` (but see rule 3 for EDD source disambiguation)
   - `"doctor"` → excluded by diagnostic category filter (rule 6)
   - `"info"` → excluded by info category filter (rule 6)

3. **Multi-word commands** (`len(parts) >= 2`): First token is the category. Join remaining tokens with `_`, then **replace `-` with `_`** to normalize hyphens:
   - `"raster clip"` → intent `clip`
   - `"hydro fill-sinks"` → intent `fill_sinks`
   - `"hydro distance-to-outlet"` → intent `distance_to_outlet`
   - `"convert raster-format"` → intent `raster_format`
   - `"proj transform"` → intent `transform`
   - `"pointcloud filter"` → intent `filter`

4. **EDD source commands**: EDD's `search` command has a `--source` parameter. Each source produces a distinct intent. The alias combines the operation with the source value (stripping `@`):
   - `"search"` with `--source @osm` → intent `search_osm`
   - `"search"` with `--source @stac` → intent `search_stac`
   - `"search"` with `--source @geoboundaries` → intent `search_geoboundaries`
   - The registry builds one intent entry per discovered source value.

5. **Intent overrides** (applied to the **full command name before stripping**):
   ```python
   INTENT_OVERRIDES = {
       "proj transform": "reproject",       # full command name → override intent
       "proj distance": "geodesic_distance", # full command name → override intent
   }
   ```
   The override check is performed on the original command name (e.g. `"proj distance"`) before any category stripping. If no override matches, proceed with stripping as before. This prevents collision between `proj distance` (→ `geodesic_distance`) and `vector distance` (→ `distance` via stripping), which would both collide on the stripped name `"distance"`.

6. **Diagnostic exclusion**: Commands with `category` in `{"diagnostic", "info", "pipe"}` are excluded from the intent catalog entirely. This is an explicit category allowlist, not a format-shape heuristic.

7. **Collision handling**: When two commands produce the same intent name (e.g. `raster clip` and `vector clip` both → `clip`), the catalog stores **both entries**. The resolver disambiguates at runtime by `data_type`.

8. **Menu deduplication**: The intent menu shows each intent once. The `required_params` shown are from the entry that **will actually resolve** given the current artifact's `data_type` — computed at menu-build time, not a union or first-seen.

### Harness CLI
```
ecospheric-harness "Download Sentinel-2 scene S2B_MSIL2A and clip to this area"
ecospheric-harness --model openrouter/z-ai/glm-5.2 "..."
ecospheric-harness --list-tools
ecospheric-harness --list-intents
ecospheric-harness --dry-run "..."
ecospheric-harness --max-turns 20 "..."
ecospheric-harness --subprocess-timeout 600 "..."
ecospheric-harness --disk-limit-gb 2 "..."
ecospheric-harness --search-cap 50 "..."
```

**CLI flag convention**: All CLI flags use kebab-case (e.g., `--subprocess-timeout`, `--disk-limit-gb`, `--search-cap`, `--max-turns`, `--list-intents`, `--list-tools`, `--dry-run`).

**`--list-intents` output format**: Outputs a JSON array of `{intent, description, tool, command, required_params, data_type}` objects, one per deduplicated intent. This provides a machine-readable view of the resolved intent catalog.

### Python API
```python
from ecospheric_harness import Harness

h = Harness(
    tools=["edd", "ese"],
    subprocess_timeout=600,    # seconds (M8)
    disk_limit_gb=2,          # max tmpfile usage (M7)
    search_cap=50,             # max items in turn state (M4)
)
result = h.run("Download Sentinel-2 scene S2B_MSIL2A and clip to this region")

h.undo()
h.redo(params={"to": "EPSG:4326"})
```

### Environment
- `OPENROUTER_API_KEY`
- `EDD_BIN` / `ESE_BIN` — optional tool binary paths
- `HARNESS_WORKDIR` — temp directory (default: system temp)
- `HARNESS_MAX_TURNS` — default 20
- `HARNESS_SUBPROCESS_TIMEOUT` — default 300 (seconds)
- `HARNESS_DISK_LIMIT_GB` — default 2
- `HARNESS_SEARCH_CAP` — default 20

---

## C. Project Structure

```
projects/ecospheric-harness/
├── docs/
│   ├── SPEC_V02.md
│   ├── ese-command-catalog.json
│   └── edd-command-catalog.json
├── ecospheric_harness/
│   ├── __init__.py
│   ├── __main__.py            # CLI entry point
│   ├── registry.py            # Tool discovery, intent catalog, alias map
│   ├── resolver.py            # Intent → tool+command resolution
│   ├── validator.py           # Schema validation of resolved tool calls
│   ├── executor.py            # subprocess invocation, param serialization, timeout
│   ├── artifact.py            # Two-artifact sliding window manager
│   ├── orchestrator.py        # Multi-turn loop: model ↔ harness ↔ tools
│   ├── menu.py                # Intent menu narrowing
│   ├── corrections.py         # Undo/redo (atomic)
│   ├── preflight.py           # requires_planar_crs check, disk usage check
│   ├── provenance.py          # Cross-step provenance chain stitching
│   ├── result.py              # PipelineResult, StepRecord dataclasses
│   ├── intents.py             # Intent type definitions
│   └── config.py              # Model config, tool paths, env vars
├── tests/
│   ├── conftest.py
│   ├── test_registry.py
│   ├── test_resolver.py
│   ├── test_validator.py
│   ├── test_executor.py
│   ├── test_artifact.py
│   ├── test_orchestrator.py
│   ├── test_menu.py
│   ├── test_corrections.py
│   ├── test_preflight.py
│   ├── test_provenance.py
│   ├── test_result.py
│   └── test_intents.py
├── pyproject.toml
├── README.md
└── LICENSE
```

### Integration
- New repo, sibling to `etp`, `edd`, `ese`
- Depends on `etp>=0.1.0` (uses `CommandDescriptor`, `ParameterDescriptor`, `build_parameters_schema` — re-exported as public from `etp.describe`)
- Does NOT depend on `edd` or `ese` — runtime discovery via `--describe`
- Note: ESE currently defines its own `CommandDescriptor` in `ese/core/output.py` rather than importing from ETP. The JSON output shapes match but are maintained separately. Future ESE migration to import from ETP is tracked as tech debt. The harness parses JSON describe output and does not import either Python class directly — it reconstructs descriptors from JSON. This is resilient to both implementations.

---

## D. Code Style

- Python 3.11+, `from __future__ import annotations` in every module
- ruff, mypy --strict, pytest, coverage ≥90%
- uv for deps
- Google-style docstrings
- Runtime deps: `etp`, `pyproj`, `httpx` (OpenRouter), stdlib

---

## E. Testing Strategy

### Unit tests
- **registry**: mock `--describe`, verify catalog build, alias resolution (including single-word names, hyphens, 3-token names, EDD `--source` disambiguation), diagnostic exclusion by category
- **resolver**: intent + artifact → correct tool+command; disambiguation by data_type; no-match and multi-match paths
- **validator**: malformed calls rejected with schema details
- **executor**: uniform after-command option placement; command name tokenization; param serialization (arrays, booleans, strings, integers); envelope capture from stdout; artifact read from `--output` path; subprocess timeout enforcement
- **artifact**: window shifts on success; preserves on failure; undo reverts; `replace_current` for redo; `store` for post-undo redo; disk usage tracking
- **menu**: narrowing by data_type; dedup shows resolved entry's params; search results don't narrow menu
- **corrections**: undo, redo (atomic — fails leave artifacts untouched), undo at step 1, redo without previous, redo after undo, undo after redo
- **preflight**: `requires_planar_crs` blocks geographic CRS; disk limit rejects when full
- **provenance**: excludes undone steps, includes redone steps
- **orchestrator**: mock model + tools, multi-turn loop with corrections, search→fetch→process flow, search result cap
- **intents**: parse all intent types from function-calling format

### Integration tests
- 2-step vector pipeline (search_osm → buffer) — direct-data search, no fetch
- 4-step raster pipeline (search_stac → fetch → clip → reproject) — clip by `--by` mask, reproject by `--to`
- Undo + redo mid-pipeline (both redo paths: replace-current and post-undo)
- Redo after undo (post-undo path)
- Undo after redo (state machine cycle)
- File-path handoff for both raster and vector
- Planar CRS preflight rejection (geographic CRS → error to model)
- Disk limit rejection
- Subprocess timeout handling

### Coverage
- ≥90%, all public API tested, all error paths

---

## F. Boundaries

### Always do
- Resolve intents to concrete tool+command before execution
- Validate every resolved call against ETP schema
- Place CLI options after the subcommand (uniform for all tools)
- Split command names on spaces when building argv
- Serialize params with type-driven logic (string+list → comma-joined, array+list → space-separated, booleans → bare flag, strings/integers → flag + value)
- Reverse-map model property names to CLI flag names using `ParameterDescriptor.name`
- Maintain two-artifact window: shift on success, preserve on failure
- Redo atomically: execute into fresh temp, only swap/free on success
- Read `format`/`data_type` directly from envelope `data` (no inference)
- Preflight: check `requires_planar_crs` against artifact CRS before execution
- Preflight: check disk usage against limit before execution
- Enforce subprocess timeout (configurable, default 300s)
- Stitch provenance (undone steps excluded, redone steps included)
- Re-send `emit_intent` tool definition each turn with repopulated enum
- Cap search results in turn state (default 20, configurable)
- Enforce single-asset fetch: `--item` and `--asset` are required when fetch is invoked through the harness. Multi-item downloads via `--output-dir` are out of scope for v0.2.
- Run linter + type checker + tests

### Ask first
- Adding a new LLM provider
- Expanding artifact window beyond 2
- Adding persistent storage
- New correction patterns
- Modifying ETP/EDD/ESE source

### Never do
- Execute a call that fails schema validation
- Pass raw binary data to the model
- Allow the model to name tool binaries
- Leak tool/command identities in ambiguity resolution (resolve deterministically)
- Share a stream between binary data and JSON
- Import private symbols from ETP (use public exports only)
- Store secrets in code
- Key retry logic on exit code alone (use `error.type` and `error.retryable` from envelope)
- Execute a planar-CRS command on geographic input without surfacing the error to the model

---

## Architecture Detail

### The Multi-Turn Loop (with search→fetch→process + correction)

```
TURN 1: search (STAC catalog search)
  Model: emit_intent(intent="search_stac", params={"bbox": "-121.5,38.2,-121.3,38.4"})
  Harness: resolve → search --source @stac (EDD)
           execute → --output <tmpfile>, envelope on stdout (metadata listing)
  Harness → model: {status: success,
                    search_results: {
                      source: "@stac", total_count: 47, returned_count: 20,
                      results_file: "/tmp/harness/search_abc.json",
                      items: [{id: "S2B...", title: "...", assets: ["visual", ...]}, ...]
                    },
                    available_intents: [search_*, fetch, ...],  # unchanged — no artifact
                    current_artifact: null}
  No artifact stored (STAC search returns metadata only).

  Alternative TURN 1b: search (direct-data search — OSM)
  Model: emit_intent(intent="search_osm", params={"bbox": "-121.5,38.2,-121.3,38.4"})
  Harness: resolve → search --source @osm (EDD)
           execute → --output <tmpfile>, vector features written to file
  Harness → model: {status: success,
                    search_results: {
                      source: "@osm", feature_count: 342,
                      results_file: "/tmp/harness/search_def456.geojson",
                      format: "geojson", data_type: "vector",
                      crs: "EPSG:4326", bounds: [-121.5, 38.2, -121.3, 38.4]
                    },
                    current_artifact: {...},  # stored as artifact!
                    available_intents: [buffer, clip, reproject, ...]}  # narrowed by data_type=vector
  Artifact stored (direct-data search produces chainable output).

TURN 2: fetch (produces first artifact from STAC search)
  Model: emit_intent(intent="fetch", params={"stac": "/tmp/harness/search_abc.json", "asset": "visual", "item": "S2B..."})
  Harness: resolve → fetch (EDD) — single-asset fetch enforced (--item and --asset required)
           execute → --output <tmpfile>, envelope on stdout with {format: "geotiff", data_type: "raster", ...}
  Harness → model: {status: success, data: {format: "geotiff", data_type: "raster", crs: "EPSG:32610", ...},
                    available_intents: [clip, reproject, convert_format, ...]}
  Artifacts: current=step1_output, previous=None

TURN 3: process (clip by vector mask)
  Model: emit_intent(intent="clip", params={"by": "/tmp/harness/mask_abc.geojson"})
  Harness: resolve "clip" + raster → "raster clip" (ESE)
           preflight: requires_planar_crs? No for clip. Disk? OK.
           execute: ese raster clip --output <tmpfile> <step1> --by /tmp/harness/mask_abc.geojson --json
           (input is positional — _route_input produces a positional arg)
           envelope: {data: {output_path: "...", format: "geotiff", data_type: "raster", bands: 4, ...}}
  Harness → model: {status: success, ...}
  Artifacts: current=step2_output, previous=step1_output

TURN 4: CORRECTION (undo + retry with different operation)
  Model: emit_intent(intent="undo")
  Harness: discard step2, revert to step1, mark step2 undone
  Artifacts: current=step1_output, previous=None

  Model: emit_intent(intent="reproject", params={"to": "EPSG:4326"})
  Harness: resolve "reproject" + raster → "raster reproject" (ESE)
           preflight: requires_planar_crs? No for reproject. Disk? OK.
           execute: ese raster reproject --output <tmpfile> <step1> --to EPSG:4326 --json
           (input is positional — _route_input produces a positional arg)
  Artifacts: current=step3_output, previous=step1_output

  Model: emit_intent(intent="complete", summary="Downloaded + reprojected to 4326")

TURN 4b: HYDRO COMMAND (multi-input via _input_target)
  Model: emit_intent(intent="basins", params={"_input_target": "d8-pntr", "threshold": 500})
  Harness: resolve "basins" + raster → "hydro basins" (ESE)
           Input routing: "hydro basins" has no "input"/"--input" param.
           _input_target="d8-pntr" → route artifact to --d8-pntr.
           preflight: requires_planar_crs? Yes. Check CRS via pyproj → OK (planar).
           execute: ese hydro basins --output <tmpfile> --d8-pntr <step1> --threshold 500 --json
  Artifacts: current=step4_output, previous=step3_output

Result:
  final_artifact: step3_output
  provenance: [step1(fetch) → step3(raster reproject)]  (step2 undone, excluded)
  steps: [StepRecord, StepRecord(undone), StepRecord]
```

### Intent Types

```python
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Any


@dataclass
class OperationIntent:
    intent: str
    params: dict[str, Any] = field(default_factory=dict)


@dataclass
class UndoIntent:
    intent: str  # "undo"


@dataclass
class RedoIntent:
    intent: str  # "redo"
    params: dict[str, Any] = field(default_factory=dict)


@dataclass
class CompleteIntent:
    intent: str  # "complete"
    summary: str = ""


@dataclass
class FailedIntent:
    intent: str  # "failed"
    reason: str = ""
```

### Intent Resolver

```python
class IntentResolver:
    def __init__(self, catalog: list[IntentEntry]):
        self._catalog = catalog

    def resolve(
        self,
        intent: str,
        params: dict[str, Any],
        current_artifact: Artifact | None,
    ) -> ResolvedCall | ResolutionError:
        candidates = [e for e in self._catalog if e.intent == intent]
        if not candidates:
            return ResolutionError(f"Unknown intent '{intent}'")

        if current_artifact:
            # Primary filter: data_type match
            compatible = [
                e for e in candidates
                if e.command.data_type == current_artifact.data_type
            ]
            # Fallback: "any" data_type + format-compatible
            if not compatible:
                compatible = [
                    e for e in candidates
                    if e.command.data_type == "any"
                    and (current_artifact.format in e.command.input_formats
                         or not e.command.input_formats)
                ]
            if not compatible:
                return ResolutionError(
                    f"No tool can '{intent}' on {current_artifact.data_type}"
                )
            candidates = compatible
        else:
            # No artifact — only commands that don't need input
            # Note: check explicitly for None or empty list. Commands with
            # input_formats=[] but a positional "input" parameter still accept
            # input — those are handled via the artifact path when present.
            candidates = [
                e for e in candidates
                if e.command.input_formats is None
                or len(e.command.input_formats) == 0
            ]
            if not candidates:
                return ResolutionError(
                    f"Intent '{intent}' requires input data"
                )

        if len(candidates) == 1:
            return ResolvedCall(
                tool=candidates[0].tool,
                command=candidates[0].command,
                params=params,
            )

        # Multiple candidates — resolve by tool precedence (deterministic)
        precedence = {"edd": 0, "ese": 1}
        candidates.sort(key=lambda e: precedence.get(e.tool.name, 99))
        return ResolvedCall(
            tool=candidates[0].tool,
            command=candidates[0].command,
            params=params,
        )
```

### Menu Narrowing (with resolved-params)

```python
def available_intents(
    catalog: list[IntentEntry],
    artifact: Artifact | None,
    resolver: IntentResolver,
) -> list[IntentOption]:
    options = []
    seen = set()

    for entry in catalog:
        if entry.command.category in {"diagnostic", "info", "pipe"}:
            continue

        if artifact is None:
            if entry.command.input_formats:
                continue
        else:
            # Check compatibility
            type_match = (
                entry.command.data_type == artifact.data_type
                or entry.command.data_type == "any"
            )
            format_match = (
                artifact.format in entry.command.input_formats
                or not entry.command.input_formats
            )
            if not (type_match and format_match):
                continue

        # For dedup: show params of the entry that WILL resolve
        # given the current artifact
        if entry.intent in seen:
            continue

        # If artifact exists, verify this entry is the one the resolver picks
        if artifact:
            resolved = resolver.resolve(entry.intent, {}, artifact)
            if isinstance(resolved, ResolvedCall):
                # Find which catalog entry matches the resolution
                for e in catalog:
                    if e.intent == entry.intent and e.command is resolved.command:
                        entry = e
                        break

        seen.add(entry.intent)
        options.append(IntentOption(
            intent=entry.intent,
            description=entry.description,
            required_params=entry.required_params,
        ))

    return options
```

### Artifact Manager (Two-Window, atomic redo support)

```python
@dataclass
class Artifact:
    path: Path
    envelope: dict[str, Any]
    format: str
    data_type: str
    crs: str | None = None
    bbox: list[float] | None = None
    step_number: int = 0


class ArtifactManager:
    def __init__(self, workdir: Path, disk_limit_bytes: int):
        self._workdir = workdir
        self._disk_limit = disk_limit_bytes
        self._current: Artifact | None = None
        self._previous: Artifact | None = None
        self._total_bytes: int = 0

    def _artifact_size(self, artifact: Artifact) -> int:
        try:
            return artifact.path.stat().st_size
        except OSError:
            return 0

    def disk_available(self, estimated_new_bytes: int = 0) -> bool:
        return self._total_bytes + estimated_new_bytes < self._disk_limit

    def store(self, artifact: Artifact) -> Artifact:
        """Shift window on success: previous freed, current→previous, new→current."""
        if self._previous is not None:
            self._total_bytes -= self._artifact_size(self._previous)
            self._previous.path.unlink(missing_ok=True)
            self._previous = None
        self._previous = self._current
        self._current = artifact
        self._total_bytes += self._artifact_size(artifact)
        return self._current

    def replace_current(self, artifact: Artifact) -> Artifact:
        """Replace current artifact, keeping previous intact. Used by redo (replace path)."""
        if self._current is not None:
            self._total_bytes -= self._artifact_size(self._current)
            self._current.path.unlink(missing_ok=True)
        self._current = artifact
        self._total_bytes += self._artifact_size(artifact)
        return self._current

    def undo(self) -> Artifact | None:
        """Discard current, revert to previous. No double-undo."""
        if self._current is None:
            return None
        self._total_bytes -= self._artifact_size(self._current)
        self._current.path.unlink(missing_ok=True)
        self._current = self._previous
        self._previous = None
        return self._current

    def current(self) -> Artifact | None:
        return self._current

    def previous(self) -> Artifact | None:
        return self._previous

    def can_undo(self) -> bool:
        """True only if previous exists (undo will have something to restore).

        After post-undo redo (store path): store() shifts current→previous, so
        can_undo() returns True. Undo is valid here — it reverts to the
        previous artifact and marks the redo step as undone. This is the same
        undo-after-redo cycle documented in the correction traces.
        """
        return self._previous is not None

    def free(self):
        if self._current:
            self._current.path.unlink(missing_ok=True)
        if self._previous:
            self._previous.path.unlink(missing_ok=True)
        self._current = None
        self._previous = None
        self._total_bytes = 0
```

### Correction Handling (Atomic Redo — Two Paths)

Redo has two paths depending on whether an undo was performed first:

1. **Replace-current** (no undo before redo): The target step is the current one. Input = previous. The old current is freed and replaced. Previous stays intact.
2. **Post-undo** (undo was done first): The target step is the last undone step. Input = current (which is the previous artifact after undo). The window shifts: current→previous, new→current.

Both paths execute into a fresh temp file and only mutate state on success.

```python
class CorrectionHandler:
    def __init__(self, artifacts, steps, executor, resolver, workdir):
        self._artifacts = artifacts
        self._steps = steps
        self._executor = executor
        self._resolver = resolver
        self._workdir = workdir

    def undo(self) -> CorrectionResult:
        if not self._artifacts.can_undo():
            return CorrectionResult(
                status="error",
                message="Cannot undo — no previous artifact to revert to"
            )
        # Mark last successful non-undone step
        for step in reversed(self._steps):
            if step.status == "success" and not step.undone:
                step.undone = True
                break
        restored = self._artifacts.undo()
        return CorrectionResult(status="undone", artifact=restored)

    def redo(self, params: dict[str, Any]) -> CorrectionResult:
        """Re-execute the last step with new params. Atomic — fails leave state unchanged."""

        # Find the last successful step (undone or not)
        target = None
        for step in reversed(self._steps):
            if step.status == "success":
                target = step
                break

        if target is None:
            return CorrectionResult(status="error", message="No step to redo")

        # Determine input artifact and mutation strategy
        if target.undone:
            # POST-UNDO path: input is current (which was the previous before undo)
            input_artifact = self._artifacts.current()
            if input_artifact is None:
                return CorrectionResult(status="error", message="No input artifact for redo")
            use_store = True  # shift window: current→previous, new→current
        else:
            # REPLACE-CURRENT path: input is previous
            input_artifact = self._artifacts.previous()
            if input_artifact is None:
                return CorrectionResult(status="error", message="No previous artifact for redo")
            use_store = False  # replace current, keep previous

        # Execute into a FRESH temp path — don't touch current artifacts yet
        result = self._executor.execute(
            tool=target.tool_ref,
            command=target.command_ref,
            params=params,
            input_artifact=input_artifact,
            workdir=self._workdir,
        )

        if result.returncode != 0 or result.envelope.get("status") != "success":
            # FAILURE — artifacts untouched, step state unchanged
            return CorrectionResult(
                status="error",
                message=f"Redo execution failed: {result.envelope.get('error', {}).get('message', 'unknown')}",
            )

        # SUCCESS — atomically mutate state
        if not target.undone:
            # Replace-current: mark old step as undone
            target.undone = True

        new_artifact = self._build_artifact(result, target.command_ref)
        # Note: _build_artifact uses the same artifact builder as the orchestrator
        # (constructs an Artifact from the execution result envelope + output_path)
        if use_store:
            self._artifacts.store(new_artifact)  # shift: current→previous, new→current
        else:
            self._artifacts.replace_current(new_artifact)  # swap current, keep previous

        return CorrectionResult(status="redone", artifact=new_artifact)
```

**Redo trace 1 — replace-current (no undo first):**

```
State: current=step2, previous=step1, steps=[step1✓, step2✓]
Model: emit_intent(intent="redo", params={"to": "EPSG:4326"})

1. Find last successful step → step2 (not undone)
2. path = REPLACE-CURRENT (target not undone)
3. input_artifact = previous = step1
4. Execute step2's command with new params → fresh temp file
   → FAILS? Return error. State unchanged. ✅
   → SUCCEEDS? Continue.
5. Mark step2.undone = True
6. artifacts.replace_current(new_artifact):
   - Free old current (step2) ← replace_current frees the old current
   - new → current
   - previous (step1) stays
7. steps=[step1✓, step2✓(undone), step2'✓]
8. Artifacts: current=step2', previous=step1
9. Provenance: [step1 → step2']
```

**Redo trace 2 — post-undo (undo first, then redo):**

```
State after undo: current=step1, previous=None, steps=[step1✓, step2✓(undone)]
Model: emit_intent(intent="redo", params={"to": "EPSG:3857"})

1. Find last successful step → step2 (undone)
2. path = POST-UNDO (target is undone)
3. input_artifact = current = step1
4. Execute step2's command with new params → fresh temp file
   → FAILS? Return error. State unchanged. ✅
   → SUCCEEDS? Continue.
5. step2 already undone — no marking needed
6. artifacts.store(new_artifact):
   - previous is None, nothing to free
   - current (step1) → previous
   - new → current
7. steps=[step1✓, step2✓(undone), step2'✓]
8. Artifacts: current=step2', previous=step1
9. Provenance: [step1 → step2']
```

**Undo after redo (state machine cycle):**

```
State: current=step2', previous=step1, steps=[step1✓, step2✓(undone), step2'✓]
Model: emit_intent(intent="undo")

1. can_undo? Yes (previous = step1 exists)
2. Find last successful non-undone step → step2'
3. Mark step2'.undone = True
4. artifacts.undo():
   - Free current (step2')
   - current = previous (step1)
   - previous = None
5. State: current=step1, previous=None
6. steps=[step1✓, step2✓(undone), step2'✓(undone)]
7. Provenance: [step1]  (both step2 and step2' undone)
```

**Failed redo — state machine behavior:**

Failed redo attempts do NOT create StepRecord entries. The executor returns an error, artifacts and step state remain unchanged. The model receives the error in turn state and can retry redo with different params. A `failed_attempts` counter is included in turn state when non-zero to help the model track repeated failures:

```json
{
  "current_artifact": { ... },
  "available_intents": [ ... ],
  "can_undo": true,
  "last_result": {
    "status": "error",
    "message": "Redo execution failed: <error details>"
  },
  "failed_attempts": 1
}
```

The `failed_attempts` field is omitted (or 0) when there are no recent failed redo attempts, keeping the turn state clean in the common case.

### Executor (Uniform option placement + param serialization + timeout)

> **Note**: The harness appends `--json` to all tool invocations to ensure envelope output. This flag may not appear in every command's descriptor but is universally accepted by ETP-compatible tools.

```python
class ToolExecutor:
    def __init__(self, subprocess_timeout: int = 300):
        self._timeout = subprocess_timeout

    def execute(
        self,
        tool: RegisteredTool,
        command: CommandDescriptor,
        params: dict[str, Any],
        input_artifact: Artifact | None,
        workdir: Path,
    ) -> ExecuteResult:
        output_path = workdir / f"step_{uuid4().hex[:8]}.bin"

        args = [tool.binary]
        args.extend(command.name.split())  # tokenize "raster clip" → ["raster", "clip"]
        args.extend(["--output", str(output_path)])

        if input_artifact:
            args.extend(self._route_input(input_artifact, command, params))

        # Remove _input_target from params before serialization (harness-internal key)
        serializable_params = {k: v for k, v in params.items() if k != "_input_target"}
        args.extend(self._serialize_params(serializable_params, command))
        args.append("--json")  # ensure envelope output

        proc = subprocess.run(args, capture_output=True, timeout=self._timeout)

        try:
            envelope = json.loads(proc.stdout)
        except json.JSONDecodeError:
            envelope = {
                "status": "error",
                "error": {
                    "type": "internal_error",
                    "message": f"Tool produced invalid JSON output",
                    "exit_code": proc.returncode,
                    "retryable": False,
                }
            }

        return ExecuteResult(
            envelope=envelope,
            returncode=proc.returncode,
            output_path=output_path,
        )

    def _route_input(
        self,
        input_artifact: Artifact,
        command: CommandDescriptor,
        params: dict[str, Any],
    ) -> list[str]:
        """Route the input artifact path to the correct CLI parameter.

        Rules:
        1. If a parameter has name="input" (no -- prefix), append as positional arg.
        2. If a parameter has name="--input", use --input <path>.
        3. If neither exists, check for _input_target in params to determine
           which parameter receives the artifact path.
        4. If no input param and no _input_target, raise an error.
        """
        path = str(input_artifact.path)
        param_names = [p.name for p in command.parameters]

        # Rule 1: positional input (name without -- prefix)
        if "input" in param_names:
            return [path]

        # Rule 2: --input flag
        if "--input" in param_names:
            return ["--input", path]

        # Rule 3: _input_target specified by model
        target_name = params.get("_input_target")
        if target_name:
            # Find the param descriptor to determine how to serialize
            for p in command.parameters:
                flag_name = f"--{target_name}"
                if p.name == flag_name or p.name == target_name:
                    if p.name.startswith("--"):
                        return [p.name, path]
                    else:
                        return [path]  # positional
            # Target name provided but not found in command params
            raise ValueError(
                f"_input_target '{target_name}' not found in command '{command.name}' parameters"
            )

        # Rule 4: no way to route
        raise ValueError(
            f"Command '{command.name}' has no standard input parameter. "
            f"Specify which parameter receives the artifact via _input_target."
        )

    def _serialize_params(self, params: dict, command: CommandDescriptor) -> list[str]:
        """Serialize params with type-driven handling + reverse name mapping.

        Serialization is driven by ParameterDescriptor.type, not by parameter name:
        - type=="string" + list value → comma-join (e.g. bbox → "xmin,ymin,xmax,ymax")
        - type=="string" + string value → pass as-is
        - type=="array" + list value → space-separated (e.g. --flag v1 v2 v3)
        - type=="boolean" → bare flag (True) or omit (False)
        - type=="number"/"integer" → flag + stringified value
        """
        args = []
        # Build reverse map: property_name → ParameterDescriptor
        param_map = {}
        for p in command.parameters:
            prop_name = p.name.lstrip("-").replace("-", "_")
            param_map[prop_name] = p

        for key, value in params.items():
            desc = param_map.get(key)
            flag = desc.name if desc else f"--{key.replace('_', '-')}"
            param_type = desc.type if desc else None

            if isinstance(value, bool):
                if value:
                    args.append(flag)
            elif isinstance(value, list):
                if param_type == "string":
                    # String-typed param with list value → comma-join
                    # e.g. bbox: ["-121.5", "38.2", ...] → --bbox "-121.5,38.2,-121.3,38.4"
                    args.extend([flag, ",".join(str(v) for v in value)])
                else:
                    # Array type or fallback: single flag + space-separated values
                    args.append(flag)
                    args.extend(str(v) for v in value)
            else:
                args.extend([flag, str(value)])

        return args
```

### Preflight Checks

```python
class PreflightChecker:
    def __init__(self, artifacts: ArtifactManager, workdir: Path):
        self._artifacts = artifacts
        self._workdir = workdir

    def check_planar_crs(
        self, command: CommandDescriptor, artifact: Artifact | None
    ) -> PreflightResult:
        """Reject commands requiring planar CRS when input is geographic."""
        if not command.requires_planar_crs:
            return PreflightResult(ok=True)

        if artifact is None:
            return PreflightResult(ok=True)  # no input to check

        if artifact.crs is None:
            return PreflightResult(
                ok=False,
                error=f"Command '{command.name}' requires planar CRS but input CRS is unknown. "
                      f"Reproject to a planar CRS (e.g. EPSG:3857) first.",
            )

        import pyproj
        try:
            crs = pyproj.CRS(artifact.crs)
        except pyproj.exceptions.CRSError:
            return PreflightResult(
                ok=False,
                error=f"Command '{command.name}' received unparseable CRS '{artifact.crs}'.",
            )

        if crs.is_geographic:
            return PreflightResult(
                ok=False,
                error=f"Command '{command.name}' requires planar CRS but input is {artifact.crs} "
                      f"(geographic). Reproject to a planar CRS (e.g. EPSG:3857) first.",
            )

        return PreflightResult(ok=True)

    def check_disk(
        self,
        estimated_bytes: int = 0,
        input_artifact: Artifact | None = None,
        expansion_factor: float = 2.0,
    ) -> PreflightResult:
        """Reject execution if disk limit would be exceeded.

        If input_artifact is provided, estimate output size as
        input_artifact size × expansion_factor (default 2.0).
        Falls back to 500 MB fixed estimate when no input is given
        and estimated_bytes is 0.
        """
        if estimated_bytes == 0 and input_artifact is not None:
            try:
                input_size = input_artifact.path.stat().st_size
            except OSError:
                input_size = 0
            estimated_bytes = int(input_size * expansion_factor)
        elif estimated_bytes == 0:
            estimated_bytes = 500 * 1024 * 1024  # 500 MB default

        if not self._artifacts.disk_available(estimated_bytes):
            return PreflightResult(
                ok=False,
                error=f"Disk usage limit would be exceeded. "
                      f"Current: {self._artifacts._total_bytes >> 20} MB, "
                      f"Limit: {self._artifacts._disk_limit >> 20} MB.",
            )
        return PreflightResult(ok=True)
```

### Canonical Format Vocabulary

The harness normalizes format identifiers to lowercase:

```python
FORMAT_ALIASES = {
    "tif": "geotiff",
    "gtiff": "geotiff",
    "cog": "cog",
    "geoparquet": "geoparquet",
    "parquet": "geoparquet",
    "geojson": "geojson",
    "shp": "shp",
    "gpkg": "gpkg",
    "fgb": "fgb",
    "kml": "kml",
    "laz": "laz",
    "las": "las",
    "ply": "ply",
    "ascii": "ascii",
    "json": "json",
}

def normalize_format(fmt: str) -> str:
    return FORMAT_ALIASES.get(fmt.lower(), fmt.lower())
```

All format comparisons in the resolver and menu use `normalize_format()`.

### Provenance Chain

```python
@dataclass
class StepRecord:
    step_number: int
    tool: str                    # tool name (for display)
    command: str                 # the real CommandDescriptor.name (e.g. "raster clip")
    tool_ref: Any = None         # RegisteredTool reference (for executor)
    command_ref: Any = None      # CommandDescriptor reference (for executor)
    intent: str = ""             # the model's intent (e.g. "clip")
    params: dict[str, Any] = field(default_factory=dict)
    status: str = ""             # "success" | "error" | "rejected"
    undone: bool = False
    envelope: dict[str, Any] | None = None
    duration_ms: int = 0
    is_search: bool = False      # True for search steps (no artifact)

@dataclass
class PipelineResult:
    steps: list[StepRecord]
    final_artifact: Artifact | None
    provenance_chain: list[dict]  # non-undone successful steps only

    def summary(self) -> str:
        ...
```

### Model Communication

**System prompt:**

```
You are a geospatial pipeline orchestrator. You emit intent commands 
that a harness resolves to tool invocations. You have ONE function: emit_intent.

Rules:
1. Call emit_intent once per turn.
2. Available intents are listed in the turn state. Use only those.
3. After each execution, you receive results and updated available intents.
4. If a step fails, retry with different params. The current artifact is preserved.
5. To undo the last step: emit_intent(intent="undo")
6. To redo the last step with new params: emit_intent(intent="redo", params={...})
   Redo re-runs the SAME operation with different params. To do a DIFFERENT 
   operation, use undo first, then emit the new intent.
7. You have TWO artifacts: current and previous. Undo reverts to previous.
   You cannot undo twice.
8. Search results appear in turn state as "search_results". For STAC search,
   results are metadata — pass "results_file" to fetch as the "stac" param.
   For direct-data search (OSM, geoBoundaries), the output IS the artifact
   and is stored in the sliding window. No fetch needed.
   Search results from STAC are NOT artifacts.
9. When complete: emit_intent(intent="complete", summary="...")
10. If you cannot complete: emit_intent(intent="failed", reason="...")
11. If `can_undo` is false, do not emit `undo` — it will fail.
12. For commands without a standard input parameter (e.g. hydro commands that
    use --d8-pntr instead of --input), include `_input_target` in params to
    specify which parameter receives the artifact path.
    Example: {"intent": "basins", "params": {"_input_target": "d8-pntr", "threshold": 500}}
13. For commands that need a mask or secondary input file (like `clip --by`),
    provide the file path in params. The harness will not auto-generate mask
    files.
    Example: {"intent": "clip", "params": {"by": "/tmp/harness/mask_abc.geojson"}}

{turn_state}
```

**Turn state (after a processing step):**

```json
{
  "current_artifact": {
    "format": "geotiff",
    "data_type": "raster",
    "crs": "EPSG:32610",
    "bbox": [-121.5, 38.2, -121.3, 38.4],
    "bands": 4,
    "size_mb": 12.3
  },
  "available_intents": [
    {"intent": "clip", "description": "Clip raster/vector by vector mask", "required_params": ["by"]},
    {"intent": "reproject", "description": "Reproject raster", "required_params": ["to"]}
  ],
  "can_undo": true,
  "last_result": {
    "status": "success",
    "step": 2,
    "intent": "fetch"
  },
  "search_results": null
}
```

**Turn state (after a STAC search step):**

```json
{
  "current_artifact": null,
  "available_intents": [
    {"intent": "search_osm", "description": "Search OSM", "required_params": ["bbox"]},
    {"intent": "search_stac", "description": "Search STAC", "required_params": ["bbox"]},
    {"intent": "fetch", "description": "Download asset", "required_params": ["stac", "asset", "item"]}
  ],
  "can_undo": false,
  "last_result": {
    "status": "success",
    "step": 1,
    "intent": "search_stac"
  },
  "search_results": {
    "source": "@stac",
    "total_count": 47,
    "returned_count": 20,
    "results_file": "/tmp/harness/search_abc123.json",
    "items": [
      {"id": "S2B_MSIL2A_20240615T185919", "title": "Sentinel-2B L2A", "assets": ["visual", "B01", "B02"], "bbox": [-121.5, 38.2, -121.3, 38.4]}
    ]
  }
}
```

**Turn state (after a direct-data search step, e.g. OSM):**

```json
{
  "current_artifact": {
    "format": "geojson",
    "data_type": "vector",
    "crs": "EPSG:4326",
    "bounds": [-121.5, 38.2, -121.3, 38.4],
    "size_mb": 2.1
  },
  "available_intents": [
    {"intent": "search_stac", "description": "Search STAC", "required_params": ["bbox"]},
    {"intent": "buffer", "description": "Buffer vector features", "required_params": ["distance"]},
    {"intent": "clip", "description": "Clip raster/vector by vector mask", "required_params": ["by"]}
  ],
  "can_undo": true,
  "last_result": {
    "status": "success",
    "step": 1,
    "intent": "search_osm"
  },
  "search_results": {
    "source": "@osm",
    "feature_count": 342,
    "results_file": "/tmp/harness/search_def456.geojson",
    "format": "geojson",
    "data_type": "vector",
    "crs": "EPSG:4326",
    "bounds": [-121.5, 38.2, -121.3, 38.4]
  }
}
```

### Error Handling

| Error type | Harness behavior |
|---|---|
| Intent parse failure | Return parse error, model retries |
| Unknown intent | Return error with available intents, model retries |
| Resolution failure | Return error, model retries |
| Schema validation failure | Return rejection, preserve artifacts, model retries |
| Preflight: planar CRS mismatch | Return error suggesting reproject, preserve artifacts, model retries |
| Preflight: disk limit | Return error, preserve artifacts, model retries |
| Preflight: fetch non-singular selection | Return error directing model to specify --item and --asset, preserve artifacts, model retries |
| Tool execution failure | Parse error envelope, return to model, preserve artifacts |
| Tool timeout | Return timeout error, preserve artifacts |
| Redo execution failure | Return error, artifacts UNTOUCHED, step state unchanged |
| Undo with no previous artifact | Return error, pipeline continues |
| Redo with no step to redo | Return error, pipeline continues |
| Model unparseable response | Return error, model retries |
| Model declares failed | End loop, return partial result |
| Max turns reached | End loop, return partial result |

**Retryable detection**: The executor reads `error.retryable` and `error.type` from the ETP error envelope (key is `error.type`, not `error.error_type`). Exit code 6 is shared between `source_error` (retryable) and `backend_error` (not retryable) — the harness keys on `error.type`.

### Max Turns

Default: 20. Correction and search turns count toward the limit.

### Final Output

On `intent="complete"`:
1. Persist final artifact to user path (or default)
2. Write provenance chain (JSON) — non-undone steps only
3. Write human-readable summary (including correction notes)
4. Return `PipelineResult`

Step log retains ALL steps including undone ones.

---

## Acceptance Criteria

1. **AC1**: Harness discovers EDD and ESE tools via `--describe` at startup
2. **AC2**: Harness builds an intent catalog with alias resolution (single-word, space-split, category-strip, hyphen→underscore, EDD `--source` disambiguation, overrides)
3. **AC3**: Model calls `emit_intent` → harness resolves → validates → executes
4. **AC4**: Model emits invalid params → harness rejects with schema error → model retries
5. **AC5**: Successful step shifts the two-artifact window
6. **AC6**: Failed step preserves both artifacts
7. **AC7**: After each step, model receives narrowed intent menu (data_type + format compatible, deduped to resolved entry's params)
8. **AC8**: `emit_intent` enum is repopulated each turn from `available_intents` (≤15 + corrections/terminals)
9. **AC9**: Multi-step vector pipeline (search_osm → buffer) executes end-to-end (direct-data search produces artifact directly, no fetch needed)
10. **AC10**: Multi-step raster pipeline (search_stac → fetch → clip → reproject) executes end-to-end (clip uses `--by` mask file, reproject uses `--to`)
11. **AC11**: Direct-data search (non-STAC) stores output as artifact in sliding window; STAC search stores results as turn state (not artifacts); menu is not narrowed after STAC search; `results_file` path available for fetch
12. **AC12**: Model emits `undo` → harness discards current, reverts to previous, marks step undone
13. **AC13**: Model emits `redo` with new params → harness re-executes same command atomically (fails leave state unchanged)
14. **AC14**: Undo at step 1 (no previous) → error, pipeline continues
15. **AC15**: Redo without previous artifact → error, pipeline continues
16. **AC16**: Redo execution failure → artifacts untouched, step state unchanged
17. **AC17**: Provenance chain excludes undone steps, includes redone steps
18. **AC18**: Model emits `complete` → harness persists artifact + provenance + summary
19. **AC19**: Model emits `failed` → harness returns partial result
20. **AC20**: Max turns exceeded → partial result with "pipeline incomplete"
21. **AC21**: Intent resolution ambiguity resolved deterministically (data_type + tool precedence, no tool names leaked)
22. **AC22**: CLI: `ecospheric-harness "natural language request"` runs a full pipeline
23. **AC23**: Python API: `Harness(tools=["edd","ese"]).run("...")` returns `PipelineResult`
24. **AC24**: `--list-tools` shows all registered tools as JSON array of `{name, version, binary, command_count}` objects
25. **AC25**: `--list-intents` shows all deduplicated intents
26. **AC26**: `--dry-run` shows the resolved tool calls, parameter validation results, and planned argv without executing subprocesses
27. **AC27**: All tools invoked with options after subcommand (uniform `after_command` placement); input artifact routing is parameter-aware: executor inspects `ParameterDescriptor` list to determine positional `input`, `--input` flag, or `_input_target` dispatch — not a blanket `--input`
28. **AC28**: Multi-word command names tokenized correctly (`"raster clip"` → `["raster", "clip"]`)
29. **AC29**: Array params (type="array") serialized as single flag + space-separated values; string params with list values are comma-joined
30. **AC30**: Boolean params serialized as bare flag (True) or omitted (False)
31. **AC31**: Param names reverse-mapped from underscore to hyphen using `ParameterDescriptor.name`
32. **AC32**: `format`/`data_type` read directly from envelope `data` block (no inference)
33. **AC33**: Format identifiers normalized to lowercase canonical forms
34. **AC34**: `can_undo` in turn state reflects previous artifact availability
35. **AC35**: Executor reads `error.retryable` and `error.type` from envelope (not exit code alone)
36. **AC36**: Diagnostic commands excluded by category allowlist (not format heuristic)
37. **AC37**: Undo + redo mid-pipeline produces correct artifact and provenance
38. **AC38**: Single-word commands (`fetch`, `search`) produce intents without category stripping
39. **AC39**: Redo after undo (post-undo path) correctly uses current as input, shifts window via `store()`
40. **AC40**: Undo after redo (state machine cycle) correctly reverts to previous, marks redo step undone
41. **AC41**: `requires_planar_crs` preflight blocks geographic CRS inputs with actionable error
42. **AC42**: Disk usage limit enforced — rejects execution when limit would be exceeded
43. **AC43**: Subprocess timeout configurable (env var, constructor param, CLI flag); timeout produces clean error
44. **AC44**: Search results capped at configurable limit (default 20); `results_file` path in turn state
45. **AC45**: Unit test coverage ≥90%
46. **AC46**: ruff + mypy --strict pass clean
47. **AC47**: Type-driven param serialization: `string` type + list value → comma-joined; `array` type + list → space-separated; `string` + string → as-is
48. **AC48**: Single-asset fetch enforced: `--item` and `--asset` required; fetch without them returns error directing model to narrow selection
49. **AC49**: `INTENT_OVERRIDES` keyed on full command name before stripping; no collision between `proj distance` and `vector distance`
50. **AC50**: Tool-specific extra envelope keys (e.g. `ese_version`) are ignored without error
