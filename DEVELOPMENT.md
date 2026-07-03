# Ecospheric Spatial Harness — Development Tracker

## Current Phase: 1b — First Real NL Query (IN PROGRESS)

## Phase History

| Phase | Status | Date | Tests | Commits |
|-------|--------|------|-------|---------|
| 0a — Environment & Installation | ✅ | 2026-07-01 | — | (initial) |
| 0b — WorkspaceManager | ✅ | 2026-07-02 | 252 | `27c916d` |
| 0c — Security Foundation | ✅ | 2026-07-02 | 252 | `19aa441` |
| 0.5 — Named Artifact Registry | ✅ | 2026-07-02 | 274 | `081ad54`, `c4ffc49` |
| 0.5 — Validator coercion fix | ✅ | 2026-07-02 | 274 | `315e45a` |
| 1a — Eval Harness | ✅ | 2026-07-02 | 311 | `fad7cf8` |
| 1b — First Real NL Query | 🔄 | 2026-07-02 | 311 | — |
| 1.5 — Provider Abstraction | ⬜ | — | — | — |
| 2 — Spatial Validation | ⬜ | — | — | — |
| 3 — Web UI | ⬜ | — | — | — |
| 4 — Hardening | ⬜ | — | — | — |

## Phase 1b — Current Blocker

**Problem:** Harness orchestrator gets "Tool produced invalid JSON output" with returncode=0.
- Direct `ToolExecutor.execute()` with same params → works (12 features, clean envelope)
- Direct CLI `edd search --json --output <file>` → works
- Raw `subprocess.run()` with same args + hardened env → works
- The executor's subprocess call is identical to the working manual call
- **Next step:** Log the actual stdout content the executor receives when it reports "invalid JSON" — the issue is somewhere between subprocess.run and json.loads inside the executor

**Also fixed today:**
- EDD `_bbox_to_overpass()` missing parentheses → committed `7ecae96` to EDD repo
- Validator `_coerce_params()` for list→string coercion → committed `315e45a`

**ESE source discovery still broken:** `ese plugins --json` returns exit 2 — not blocking but limits ESE intents

## Test Count: 311
## Source Files: 22
