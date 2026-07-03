# Ecospheric Spatial Harness — Development Tracker

## Current Phase: 2 — Spatial Validation (NEXT)

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
| 2 — Spatial Validation | ⬜ | — | — | — |
| 3 — Web UI | ⬜ | — | — | — |
| 4 — Hardening | ⬜ | — | — | — |

## Phase 1b — COMPLETE ✅ (2026-07-02)

**E2E verification:** Multi-step pipeline (search OSM buildings → reproject EPSG:32610 → buffer 500m) produces 2,526 features, UTM 10N, 20.6× area expansion. Spatial correctness verified.

**10 bugs fixed:**
1. Redaction regex corrupting JSON (`security.py` — `[/\S]*` → `[A-Za-z0-9_\-./]*`)
2. Parameter name normalization (`params.py` — strip `--`, hyphens→underscores)
3. Positional input fallback for commands without `--input`
4. ESE GeoParquet read via magic bytes (`_is_parquet_file`)
5. ESE GeoParquet write via `gpd.to_parquet`
6. Artifact ID resolution from `input` param → `input_artifact_id`
7. Double-input serialization (strip `input` from serialized params)
8. Structural input auto-resolution (strip from `required_params` + schema)
9. Output file extension based on artifact format (`.parquet` not `.bin``)
10. EDD Overpass bbox parens fix (`7ecae96` in EDD repo)

## Known Issues (non-blocking)

- `ese plugins --json` returns exit 2 — limits ESE search intents
- DEVELOPMENT.md previously stale (now updated)

## Test Count: 345
## Source Files: 22

---

## Phase 1.5 — Provider Abstraction (SCOPE)

**Goal:** Decouple from OpenRouter before coupling deepens.

### Protocol

```python
class ModelProvider(Protocol):
    def generate(self, system_prompt: str, messages: list[dict], tool_def: dict) -> ModelResponse: ...
    def stream(self, system_prompt: str, messages: list[dict], tool_def: dict) -> Iterator[StreamChunk]: ...
```

- OpenAI wire format is the internal interface (explicit). Ollama normalizes into it.
- Streaming from day one (Phase 3 needs SSE).
- No LLM frameworks.

### Types

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

### Tasks

- [ ] Define `ModelProvider` protocol + types (`providers/base.py`)
- [ ] `OpenRouterProvider` — wraps existing `_call_model`, adds streaming (`providers/openrouter.py`)
- [ ] `OllamaProvider` — local, normalizes tool calls to OpenAI format (`providers/ollama.py`)
- [ ] Refactor `Orchestrator._call_model` → delegate to `ModelProvider`
- [ ] `--provider` and `--model` CLI flags
- [ ] `Harness.__init__` accepts `provider: ModelProvider`
- [ ] Run eval harness against both providers (regression check)
- [ ] Provider error handling: retry on `retryable=True`, surface on non-retryable

### Estimation

- ~6-8 source files (base, openrouter, ollama, types, error handling, config)
- ~30-40 tests (mock both providers, error scenarios, streaming)
- Dependencies: `httpx` (already available), `ollama` Python client optional (can use raw HTTP)
