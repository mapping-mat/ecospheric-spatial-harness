# Ecospheric Spatial Harness — Development Tracker

## Current Phase: 3 — Web UI (NEXT)

## Phase History

| Phase | Status | Date | Tests | Commits |
|-------|--------|------|-------|---------|
| 0a — Environment & Installation | ✅ | 2026-07-01 | — | (initial) |
| 0b — WorkspaceManager | ✅ | 2026-07-02 | 252 | `27c916d` |
| 0c — Security Foundation | ✅ | 2026-07-02 | 252 | `19aa441` |
| 0.5 — Named Artifact Registry | ✅ | 2026-07-02 | 274 | `081ad54`, `c4ffc49` |
| 0.5 — Validator coercion fix | ✅ | 2026-07-02 | 274 | `315e45a` |
| 1a — Eval Harness | ✅ | 2026-07-02 | 311 | `fad7cf8` |
| 1b — First Real NL Query | ✅ | 2026-07-02 | 345 | `f1f0d96`→`5dc3bf4` |
| 1.5 — Provider Abstraction | ✅ | 2026-07-03 | 408 | `e59bab3` |
| 2.1 — Preflight Foundation + Checks 1-8 | ✅ | 2026-07-03 | 461 | `ad25ae2` |
| 2.2 — Output Validation | ✅ | 2026-07-03 | 491 | `0e73cfc` |
| 2.3 — Memory Budget + Command Classification | ✅ | 2026-07-03 | 522 | `a489f01` |
| 2.4 — WorkspaceManager Extensions | ✅ | 2026-07-03 | 522 | `a489f01` |
| 2.5 — COG Default + Integration Tests | ✅ | 2026-07-03 | 519 | `2d36e70` |
| 3 — Web UI | ⬜ NEXT | — | — | — |
| 4 — Hardening | ⬜ | — | — | — |

## Test Count: 519
## Source Files: 30+ (see structure below)

## Source Structure

```
ecospheric_harness/
├── __init__.py          — public API exports
├── __main__.py          — Harness class + CLI
├── config.py            — HarnessConfig dataclass
├── orchestrator.py      — multi-turn orchestration loop
├── executor.py          — subprocess invocation + param serialization
├── intents.py           — intent types, PreflightResult, Resolution enum
├── preflight.py         — PreflightChecker with 10-check pipeline
├── output_validator.py  — post-execution output validation
├── command_profile.py   — memory classification table (command, data_type)
├── params.py            — param normalization
├── corrections.py       — undo/redo handler
├── preflight.py         — spatial + security preflight checks
├── provenance.py        — provenance DAG builder
├── registry.py          — tool discovery
├── resolver.py          — intent → tool/command resolution
├── result.py            — PipelineResult, StepRecord
├── security.py          — SubprocessHardener, SSRF, output sanitization
├── validator.py         — SchemaValidator
├── workspace.py         — WorkspaceManager (path confinement, disk, lock, cleanup)
├── artifact.py          — Artifact dataclass
├── artifact_registry.py — ArtifactRegistry (DAG, idempotency, eviction)
├── menu.py              — available_intents()
├── providers/
│   ├── __init__.py      — provider package exports
│   ├── base.py          — ModelProvider Protocol, ModelResponse, TokenUsage, StreamChunk, ProviderError
│   ├── openrouter.py    — OpenRouterProvider (generate + stream via SSE)
│   └── ollama.py        — OllamaProvider (generate + stream via NDJSON, tool_call normalization)
└── eval/
    ├── __init__.py
    ├── cases.py          — 30 eval fixtures
    ├── fixtures.py       — EvalFixture dataclasses
    └── runner.py         — EvalRunner
```

## Phase 1b — COMPLETE ✅ (2026-07-02)

**E2E verification:** Multi-step pipeline (search OSM buildings → reproject EPSG:32610 → buffer 500m) produces 2,526 features, UTM 10N, 20.6× area expansion. Spatial correctness verified.

**10 bugs fixed:**
1. Redaction regex corrupting JSON (`security.py`)
2. Parameter name normalization (`params.py`)
3. Positional input fallback for commands without `--input`
4. ESE GeoParquet read via magic bytes
5. ESE GeoParquet write via `gpd.to_parquet`
6. Artifact ID resolution from `input` param
7. Double-input serialization
8. Structural input auto-resolution
9. Output file extension based on artifact format
10. EDD Overpass bbox parens fix

## Phase 1.5 — Provider Abstraction ✅ (2026-07-03)

- `ModelProvider` Protocol + `ModelResponse`, `TokenUsage`, `StreamChunk`, `ProviderError` types
- `OpenRouterProvider` — wraps OpenRouter calls, SSE streaming
- `OllamaProvider` — normalizes NDJSON to OpenAI wire format, streaming
- `--provider {openrouter,ollama}` + `--ollama-host` CLI flags
- Backward-compatible: orchestrator falls back to direct httpx when no provider configured
- 63 tests (18 base + 18 openrouter + 19 ollama + 8 orchestrator)

## Phase 2 — Spatial Validation + Data-Size Strategy ✅ (2026-07-03)

**Goal:** Prevent "Success" on garbage outputs and prevent OOM on large data.

### 2.1 — Preflight Foundation + Spatial Checks 1-8
- `PreflightResult` upgraded with `Resolution` enum (PASS/AUTO_FIX/ASK_USER/MODEL_DISCRETION/BLOCK)
- Backward-compat `.ok` and `.error` properties
- `run_all_checks()` pipeline method replaces 3 inline blocks
- 8 new checks: CRS agreement (binary ops + file-path header reads), extent intersection, unit awareness, extent containment, CRS validity, planar CRS, resolution sanity, geometry validity
- `_resolve_secondary_input()` — artifact IDs + file paths (vector via geopandas, raster via `ese info`)
- Turn-state gains `warnings` list for MODEL_DISCRETION results
- AUTO_FIX/ASK_USER treated as BLOCK in Phase 2

### 2.2 — Output Validation
- `OutputValidator` class with 5 checks: file_exists, raster_validity, vector_validity, output_vs_intent, metadata_completeness
- Output-vs-intent: reproject CRS match, clip extent intersection, buffer extent containment
- `WorkspaceManager.cleanup_unregistered()` deletes orphan files on validation failure
- Failed validation → step `validation_failed` status

### 2.3 — Memory Budget + Command Classification
- `command_profile.py` — 25 profiles keyed by `(command_name, data_type)` tuple
- `estimate_rss_bytes()` with high/low confidence flagging
- Raster: `width × height × bands × dtype_size × multiplier`
- Vector: `feature_count × 500 × multiplier` or `file_size × 5` (low confidence)
- Pointcloud: `file_size × 3`
- `_check_memory_budget()` in preflight pipeline — BLOCK when estimate > limit
- `--memory-limit-mb` CLI flag, `HARNESS_MEMORY_LIMIT_MB` env var

### 2.4 — WorkspaceManager Extensions
- `cleanup_old_sessions(ttl_days)` — removes stale session dirs, skips current
- `cleanup_cancelled_step(session_dir, step_number)` — Phase 3 placeholder
- `estimate_rss(artifact, profile)` — convenience wrapper
- `--session-ttl-days` CLI flag, `HARNESS_SESSION_TTL_DAYS` env var

### 2.5 — COG Default + Integration Tests
- Orchestrator injects `format=cog` for raster-producing commands when not specified
- 5 new eval fixtures (30 total): preflight CRS mismatch, geographic buffer block, invalid CRS, valid pipeline, output validation failure
- 17 integration tests

### Checks 11-14 deferred to Phase 4
Band validity, categorical resampling guard, datum transformation, NoData awareness, pixel alignment — need richer ESE command metadata.

## Known Issues (non-blocking)

- `ese plugins --json` returns exit 2 — limits ESE search intents
- Phase 1.5 stream() error handling gaps (4 minor items — deferred to Phase 4)
- Phase 2.2 `file_exists` check is warning-level (could be tightened to hard-fail for empty files)
- Phase 2.2 bare `except Exception` in output_vs_intent could log warnings
- Memory multiplier heuristics uncalibrated (Phase 4 instruments actual peak RSS)

## CLI Flags Summary

```
ecospheric-harness [prompt] [options]
  --model MODEL                    Model identifier (default: z-ai/glm-5.2)
  --provider {openrouter,ollama}   Model provider (default: openrouter)
  --ollama-host HOST               Ollama host (default: http://localhost:11434)
  --max-turns N                    Max orchestration turns (default: 20)
  --subprocess-timeout SECS        Subprocess timeout (default: 300)
  --disk-limit-gb GB               Disk usage limit (default: 2.0)
  --search-cap N                   Max search results (default: 20)
  --workspace DIR                  Workspace root (default: ~/.esp/sessions)
  --session-id ID                  Session identifier
  --max-output-mb MB               Max subprocess output (default: 100)
  --rlimit-as-mb MB                Address space RLIMIT_AS
  --rlimit-nproc N                 Max processes RLIMIT_NPROC
  --gdal-cachemax MB               GDAL_CACHEMAX (default: 256)
  --memory-limit-mb MB             Memory budget limit (default: none)
  --session-ttl-days DAYS          Session TTL for cleanup (default: 7.0)
  --default-raster-format FMT      Default raster format (default: cog)
  --list-tools                     List discovered tools as JSON
  --list-intents                   List available intents as JSON
  --dry-run                        Show resolved calls without executing
  --eval                           Run evaluation fixtures
  --tag TAG                        Filter eval fixtures by tag
  --fixture NAME                   Run single eval fixture by name
```

## Env Vars

| Variable | Maps to | Default |
|----------|---------|---------|
| `OPENROUTER_API_KEY` | OpenRouter auth | (required for openrouter) |
| `HARNESS_PROVIDER` | `--provider` | `openrouter` |
| `HARNESS_OLLAMA_HOST` | `--ollama-host` | `http://localhost:11434` |
| `HARNESS_WORKSPACE_ROOT` | `--workspace` | `~/.esp/sessions` |
| `HARNESS_SESSION_ID` | `--session-id` | (auto-generated) |
| `HARNESS_MAX_TURNS` | `--max-turns` | `20` |
| `HARNESS_SUBPROCESS_TIMEOUT` | `--subprocess-timeout` | `300` |
| `HARNESS_DISK_LIMIT_GB` | `--disk-limit-gb` | `2.0` |
| `HARNESS_SEARCH_CAP` | `--search-cap` | `20` |
| `HARNESS_MAX_OUTPUT_MB` | `--max-output-mb` | `100` |
| `HARNESS_RLIMIT_AS_MB` | `--rlimit-as-mb` | (none) |
| `HARNESS_RLIMIT_NPROC` | `--rlimit-nproc` | (none) |
| `HARNESS_GDAL_CACHEMAX_MB` | `--gdal-cachemax` | `256` |
| `HARNESS_MEMORY_LIMIT_MB` | `--memory-limit-mb` | (none) |
| `HARNESS_SESSION_TTL_DAYS` | `--session-ttl-days` | `7.0` |
| `HARNESS_DEFAULT_RASTER_FORMAT` | `--default-raster-format` | `cog` |
