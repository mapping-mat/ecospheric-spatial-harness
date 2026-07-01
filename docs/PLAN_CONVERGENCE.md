# Plan: EDD/ESE/ETP Convergence (Option C)

## Goal
Align EDD, ESE, and ETP so the harness can treat all tools uniformly — no per-tool normalization layer, no option placement table, no special-casing. Three repos, mechanical changes.

---

## Units & Dependencies

```
Unit 1 (ETP) → Units 2,3,4 (parallel)
Unit 2 (EDD describe names) ─┐
Unit 3 (EDD search envelope) ├─→ Unit 5 (verify)
Unit 4 (ESE envelope data)  ─┘
```

---

## Unit 1: ETP — Promote `_build_parameters_schema` to public (S)

**Why**: Harness needs to build JSON Schema from a `CommandDescriptor`. Currently private (`_build_parameters_schema`). The spec references it as public.

### Task 1.1: Re-export `build_parameters_schema` from `etp.describe`
- **Input**: `etp/describe.py` with `_build_parameters_schema`
- **Action**: Add a public alias `build_parameters_schema = _build_parameters_schema` (or rename the function and keep the private name as alias for backward compat)
- **Output**: `etp.describe.build_parameters_schema` callable
- **Verification**: `python -c "from etp.describe import build_parameters_schema; print('ok')"`

### Task 1.2: Add `format` and `data_type` to `build_success_envelope` docstring
- **Input**: `etp/envelope.py`
- **Action**: Update the docstring for `build_success_envelope` to document that `data` should include `format` and `data_type` when the command produces a chainable artifact (convention, not enforcement)
- **Output**: Updated docstring
- **Verification**: Visual check

---

## Unit 2: EDD — Fix describe catalog names (S)

**Why**: Describe reports `"@osm search"` but the actual CLI is `edd search --source @osm`. The `@osm` is a `--source` param value, not part of the command name. This breaks the harness's command-name tokenization and intent aliasing.

### Task 2.1: Change `search_command()` name from `"{prefix} search"` to `"search"`
- **Input**: `edd/core/source.py` line 464: `name=f"{self.identity.prefix} search"`
- **Action**: Change to `name="search"`. The `--source` parameter (already exists as positional `source` in the CLI) carries the `@osm`/`@stac` identity. Add `--source` to the parameter descriptors if not already present.
- **Output**: All 9 EDD search commands report `name="search"` in describe output
- **Verification**: `python -c "from edd.core.registry import load_plugins; loaded,_=load_plugins(); [print(p.search_command().name) for p in loaded]"` → prints `search` 9 times

### Task 2.2: Add `--source` parameter descriptor to `search_command()`
- **Input**: `edd/core/source.py` `search_command()` global params
- **Action**: Add `ParameterDescriptor(name="--source", description="Source prefix (e.g. @osm, @stac)", type="string", required=True)` to the `global_params` list
- **Output**: Search descriptors include `--source` as a required param
- **Verification**: `python -c "from edd.core.registry import load_plugins; loaded,_=load_plugins(); ps=[p for p in loaded[0].search_command().parameters if p.name=='--source']; print(len(ps)==1)"`

### Task 2.3: Disambiguate in the describe output
- **Input**: Task 2.1 result — all 9 search commands now have `name="search"`
- **Action**: The harness distinguishes by source via the `--source` param, not by command name. This is correct — the model emits `search_osm` / `search_stac` as intents (alias = `"search_" + source_without_at`), and the resolver maps back to `edd search --source @osm`.
- **Output**: No further code change needed in EDD — the harness's alias logic handles this
- **Verification**: Alias logic produces `search_osm` from `name="search"` + `--source @osm` — verified in harness spec

---

## Unit 3: EDD — Add `format`/`data_type` to search envelope (S, optional)

**Why**: Search results are metadata, not chainable artifacts. But adding `format` and `data_type` to the envelope makes the contract consistent. Low priority — the harness treats search results as turn state regardless.

### Task 3.1: Add `format` and `data_type` to search envelope `data`
- **Input**: `edd/cli/search.py` — the `build_success_envelope` call
- **Action**: Add `"format": "json"` (or the actual output format) and `"data_type": "metadata"` to the `data` dict
- **Output**: Search envelopes include format/data_type
- **Verification**: Run `edd search --source @osm --bbox ... --json` and check envelope data contains both fields

---

## Unit 4: ESE — Add `format`/`data_type` to success envelopes (M)

**Why**: ESE's envelope `data` blocks currently include command-specific fields (`bands`, `width`, `output_path`, `provenance`) but not `format` or `data_type`. The harness needs these to chain artifacts. The format is already computed (`out_fmt` variable exists in most commands). The data_type is known from the `CommandDescriptor`.

### Task 4.1: Add `format` and `data_type` to all ESE plugin success envelopes

This is mechanical — each `build_success_envelope(command=..., data=result_data)` call gets two new keys in `result_data`. The values are already available in scope.

**Raster commands** (`ese/plugins/raster.py`, `raster_bridge.py`, `raster_math.py`, `raster_utils.py`):
- `format`: use the `out_fmt` variable (already computed via `detect_format()` or hardcoded)
- `data_type`: `"raster"`

**Vector commands** (`ese/plugins/vector.py`, `vector_construct.py`, `vector_overlay.py`, `vector_join.py`, `vector_measure.py`, `vector_linear.py`):
- `format`: use the output format (geojson/geoparquet/shp/gpkg — already known from output path extension or `--output-format`)
- `data_type`: `"vector"`

**Hydro commands** (`ese/plugins/hydro_conditioning.py`, `hydro_flow.py`, `hydro_terrain.py`, `hydro_streams.py`, `hydro_watershed.py`, `hydro_advanced.py`):
- `format`: `out_fmt` or `"geotiff"` (most hydro commands output rasters)
- `data_type`: `"raster"` (most hydro), `"vector"` for `hydro flowpath` and `hydro streams-vector`

**Pointcloud commands** (`ese/plugins/pointcloud.py`, `pointcloud_ops.py`):
- `format`: output format (`laz`, `las`, `ply`, `geojson`, `gtiff`)
- `data_type`: `"pointcloud"` (or `"vector"`/`"raster"` for boundary/density)

**Proj commands** (`ese/plugins/proj.py`, `proj_geodesic.py`):
- `format`: output format (geojson/geoparquet/shp/gpkg)
- `data_type`: `"vector"`

**Convert commands** (`ese/plugins/convert.py`, `convert_crosstype.py`):
- `format`: output format
- `data_type`: depends on conversion direction (raster-to-vector → `"vector"`, etc.)

### Approach
Rather than editing 105 call sites manually, create a helper:

```python
# ese/core/output.py — add this helper
def enrich_data(data: dict, command: CommandDescriptor, output_path: str | None) -> dict:
    """Add format and data_type to envelope data if not present."""
    if "format" not in data:
        if output_path:
            data["format"] = detect_format(Path(output_path))
        elif command.output_formats:
            data["format"] = command.output_formats[0]
    if "data_type" not in data:
        data["data_type"] = command.data_type
    return data
```

Then update each call site to:
```python
result_data = enrich_data(result_data, descriptor, output_path)
return build_success_envelope(command="raster clip", data=result_data)
```

This is still ~105 edits but each is a one-line insertion before `return build_success_envelope`. A subagent can do this mechanically.

### Task 4.2: Verify ESE envelopes contain format/data_type
- **Input**: Updated ESE plugins
- **Action**: Run `ese raster clip --input sample.tif --by mask.geojson --output /tmp/out.tif --json` and verify envelope `data` contains `format` and `data_type`
- **Output**: All ESE success envelopes include both fields
- **Verification**: Grep `build_success_envelope` calls and confirm `enrich_data` is called before each

---

## Unit 5: Cross-tool verification (S)

### Task 5.1: Run EDD + ESE end-to-end smoke test
- **Input**: Updated EDD, ESE, ETP
- **Action**: 
  1. `edd search --source @stac --bbox ... --json --output /tmp/search.json`
  2. `edd fetch --stac /tmp/search.json --asset visual --output /tmp/scene.tif --json`
  3. `ese raster clip --input /tmp/scene.tif --by /tmp/mask.geojson --output /tmp/clipped.tif --json`
  4. Verify each envelope contains `format` and `data_type`
- **Output**: All three tools produce consistent envelope data
- **Verification**: Envelopes pass JSON validation, format/data_type present

### Task 5.2: Run existing test suites
- **Input**: Updated repos
- **Action**: `pytest` in each repo
- **Output**: All existing tests pass
- **Verification**: Zero test failures

---

## Summary

| Unit | Repo | Size | Tasks | Est. |
|------|------|------|-------|------|
| 1: Promote `_build_parameters_schema` | ETP | S | 2 | 15 min |
| 2: Fix EDD describe names | EDD | S | 3 | 30 min |
| 3: Add format/data_type to EDD search | EDD | S | 1 | 15 min |
| 4: Add format/data_type to ESE envelopes | ESE | M | 2 | 1-2 hr |
| 5: Cross-tool verification | All | S | 2 | 30 min |

**Total: ~2-3 hours of mechanical work.**

Unit 4 is the bulk (105 call sites) but each edit is identical: insert one `enrich_data()` call before each `return build_success_envelope`. Dispatch as a single coder subagent with the helper function + clear instructions.

After this converges, the harness spec simplifies:
- No option placement table (both tools use command-level `--output`)
- No per-tool normalization layer
- No artifact metadata inference (envelope always has `format`/`data_type`)
- Command names are single tokens (`search`, `fetch`) or space-split groups (`raster clip`) — consistent
- `--source` param carries the EDD source identity, not the command name