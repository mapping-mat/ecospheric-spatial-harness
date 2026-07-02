# Ecospheric Agent Harness

Multi-turn LLM orchestration harness for ETP-compatible geospatial tools (EDD, ESE). The model orchestrates multi-step geospatial pipelines — search, fetch, clip, reproject, analyze — while the harness enforces schema, chains tool I/O, manages intermediate artifacts, and supports conversational corrections (undo/redo).

## Architecture

```
Model ←→ Orchestrator ←→ Resolver → Executor → EDD/ESE (subprocess)
                ↓
         ArtifactManager (two-slot sliding window)
                ↓
         ProvenanceChain (full audit trail)
```

**Core loop:**
1. Model emits an intent (e.g. `search_osm`, `fetch`, `clip`, `reproject`)
2. Resolver maps intent → tool + command, routes input artifact
3. Preflight checks (CRS, disk usage)
4. Executor runs the tool via subprocess, captures ETP envelope
5. Result stored as artifact in two-slot window (current + previous)
6. Turn state (artifact metadata, search results, available intents) returned to model
7. Model emits next intent or `complete`

**Key design decisions:**
- File-path handoff between tools (not ETP pipe — vector-only)
- Two-artifact sliding window enables undo/redo
- `parallel_tool_calls: false` — one intent per turn
- Type-driven parameter serialization (string+list → comma-join, array+list → space-separated)
- Prefix-based search intent disambiguation (`@osm search` → `search_osm`)

## Modules

| Module | Responsibility |
|--------|---------------|
| `orchestrator.py` | Main loop, model communication, turn state |
| `registry.py` | Tool discovery, catalog construction, intent building |
| `resolver.py` | Intent → tool+command resolution, input routing |
| `executor.py` | Subprocess execution, param serialization |
| `artifact.py` | Two-slot sliding window, disk tracking |
| `corrections.py` | Undo/redo state machine |
| `preflight.py` | CRS and disk checks |
| `validator.py` | Intent param schema validation |
| `menu.py` | Available intents per turn (artifact-aware) |
| `provenance.py` | Step chain, duration tracking |
| `result.py` | PipelineResult, StepRecord |
| `intents.py` | Shared types (IntentOption, CatalogIntentEntry) |
| `config.py` | HarnessConfig, env vars |
| `__main__.py` | CLI + Harness public API |

## Quick Start

```bash
# Install
pip install -e .

# Run with EDD + ESE installed
python -m ecospheric_harness "Download OSM buildings for Butte County and clip to parcels"

# Inspect available intents
python -m ecospheric_harness --list-intents

# Dry-run (show planned argv without executing)
python -m ecospheric_harness --dry-run "Reproject DEM to EPSG:4326"
```

## Python API

```python
from ecospheric_harness import Harness, HarnessConfig

harness = Harness(tools=["edd", "ese"])
result = harness.run("Search for Sentinel-2 scenes over Butte County, fetch the first result, and clip to a mask")

print(result.summary())
# → {"total_steps": 3, "completed": 3, "failed": 0, ...}

for step in result.steps:
    print(f"  {step.step_number}. {step.intent} → {step.status} ({step.duration_ms}ms)")
```

## CLI

```
ecospheric-harness [-h] [--model MODEL] [--list-tools] [--list-intents]
                    [--dry-run] [--max-turns N] [--subprocess-timeout SECS]
                    [--disk-limit-gb GB] [--search-cap N]
                    [prompt]
```

## Dependencies

- Python ≥3.11
- `etp` ≥0.1.0 (shared ETP protocol library)
- `pyproj` (CRS checking)
- `httpx` (model API calls)
- EDD and ESE installed and on PATH (or via `EDD_BIN`/`ESE_BIN` env vars)

## Testing

```bash
pytest tests/ -q          # 252 tests
ruff check ecospheric_harness/ tests/
mypy ecospheric_harness/
```

## Review History

Four critique rounds before initial commit:
1. **Opus 4.8** — found 3 blockers, 6 majors, 6 minors (15 issues)
2. **Opus 4.8** — verified fixes, found 5 new issues (tool_call_id, step collision, etc.)
3. **Opus 4.8** — verified fixes, found 1 remaining (parallel tool_calls)
4. **MiniMax M3** — fresh review, found 1 bug Opus missed (hardcoded last_result) + 4 more

All 28 issues fixed. 252 tests passing, ruff + mypy clean.

## Specs

- [SPEC_V02.md](docs/SPEC_V02.md) — 50 acceptance criteria, 5 critique rounds
- [PLAN_V02.md](docs/PLAN_V02.md) — implementation plan and task breakdown
- [edd-command-catalog.json](docs/edd-command-catalog.json) — EDD command snapshot
- [ese-command-catalog.json](docs/ese-command-catalog.json) — ESE command snapshot

## License

Private.
