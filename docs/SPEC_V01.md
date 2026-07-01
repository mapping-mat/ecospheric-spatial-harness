# Ecospheric Agent Harness — Specification v0.1 (Draft v1)

## Assumptions

1. **Intent**: Pilgrim wants a harness that sits between an LLM and ETP-compatible tools (EDD, ESE, future tools). The model orchestrates multi-step geospatial pipelines. The harness enforces schema, chains tool I/O, and manages intermediate artifacts.

2. **Existing codebase**: Three ETP-compatible tools exist — `etp` (shared protocol library, v0.1.0), `edd` (data discovery/download, v0.5.0), `ese` (spatial engine, pre-0.1). All expose `--describe` catalogs with `CommandDescriptor` objects, accept input via ETP pipe (Arrow IPC) or CLI flags, and emit ETP success/error envelopes on stdout.

3. **ETP provides the building blocks**:
   - `CommandDescriptor` with `input_formats`, `output_formats`, `data_type`, `requires_planar_crs`
   - `build_function_block()` / `to_openai_tool()` / `to_anthropic_tool()` — LLM tool definitions from descriptors
   - Pipe contract: 16-byte header + Arrow IPC payload, with provenance chain serialization
   - Error envelopes: `error_type`, `message`, `suggestion`, `exit_code`, `retryable`, `retry_after_ms`, `param_path`
   - Success envelopes: `tool`, `tool_version`, `schema_version`, `status`, `command`, `data`, `warnings`

4. **Model role**: Parse natural-language user intent, select tools, specify parameters, decide next action based on previous step's result. The model is the brain; the harness is the hands.

5. **No fine-tuning** (v0.1). Off-the-shelf models with good function-calling support. Fine-tuning is deferred until evidence shows models can't select correct tools from good descriptions.

6. **No web frontend** (v0.1). CLI/library first. Web layer is a separate concern for a later version.

7. **Intermediate data strategy**: Sliding window of one. Only the most recent successful output is retained. Failed steps do not consume or free the current artifact. This forces linear pipelines with retry-at-current-step semantics.

8. **Pipeline linearity**: No branching, no parallel execution, no catalog of intermediates. If the model needs to change an earlier step, it re-runs from the beginning.

9. **"Done" looks like**: A user can type a natural-language geospatial request, the model orchestrates a multi-tool pipeline via the harness, and the harness executes each step with schema validation, manages the intermediate artifact, and returns a final result with full provenance.

---

## A. Objective

**Primary**: Build a multi-turn orchestration harness that lets an LLM execute sequential ETP-compatible tool pipelines by validating, executing, and chaining tool calls with a single-artifact sliding window.

**Secondary goals**:
- Provide a tool registry that auto-discovers ETP-compatible tools via `--describe`
- Validate every tool call against the tool's ETP schema before execution
- Narrow the available tool menu after each step based on output/input format compatibility
- Surface tool results (metadata envelopes, not raw data) back to the model for next-step reasoning
- Stitch provenance across all pipeline steps into a single lineage chain
- Support retry at the current step without losing the previous successful output
- Expose a clean Python API for programmatic use AND a CLI for interactive use

**Out of scope (v0.1)**:
- Fine-tuning or model training
- Web frontend / HTTP API server
- Branching or parallel pipeline execution
- Persistent artifact catalog (beyond the single sliding window)
- Tool installation or dependency management
- Authentication, rate-limiting, or multi-tenant isolation
- Streaming model responses (the harness processes one tool call at a time)

---

## B. Commands

### Tool invocation
The harness does NOT call tools via shell subprocess directly. Instead:
- Each registered tool (EDD, ESE) is a **CLI binary** invoked via `subprocess`
- Input data flows via stdin (ETP pipe) or `--input` file path
- Output data flows via stdout (ETP pipe) or `--output` file path
- The harness captures stdout envelope (JSON) for model feedback and pipes/redirects binary data

### Harness CLI
```
# Interactive mode — model orchestrates a pipeline from user prompt
ecospheric-harness "Download Sentinel-2 scene S2B_MSIL2A and clip to this bbox"

# With explicit model
ecospheric-harness --model openrouter/z-ai/glm-5.2 "..." 

# List registered tools
ecospheric-harness --list-tools

# Dry run — show the planned pipeline without executing
ecospheric-harness --dry-run "..."
```

### Python API
```python
from ecospheric_harness import Harness

h = Harness(tools=["edd", "ese"])
result = h.run("Download Sentinel-2 scene S2B_MSIL2A and clip to [xmin,ymin,xmax,ymax]")
# result = PipelineResult with final artifact, provenance chain, step log
```

### Environment
- `OPENROUTER_API_KEY` — for model access (or other provider keys as configured)
- `EDD_BIN` / `ESE_BIN` — optional paths to tool binaries (default: discovered on PATH)
- Tools must be installed and callable (EDD, ESE on PATH or explicitly configured)

---

## C. Project Structure

```
projects/ecospheric-harness/
├── docs/
│   └── SPEC_V01.md            # this file
├── ecospheric_harness/
│   ├── __init__.py
│   ├── __main__.py            # CLI entry point
│   ├── registry.py            # Tool discovery & registration via --describe
│   ├── validator.py           # Schema validation of model-proposed tool calls
│   ├── executor.py            # subprocess invocation, pipe management, envelope capture
│   ├── artifact.py            # Sliding-window artifact manager (one intermediate)
│   ├── orchestrator.py        # Multi-turn loop: model ↔ harness ↔ tools
│   ├── menu.py                # Tool menu narrowing based on format compatibility
│   ├── provenance.py          # Cross-step provenance chain stitching
│   ├── result.py              # PipelineResult, StepRecord dataclasses
│   └── config.py              # Model config, tool paths, defaults
├── tests/
│   ├── conftest.py
│   ├── test_registry.py
│   ├── test_validator.py
│   ├── test_executor.py
│   ├── test_artifact.py
│   ├── test_orchestrator.py
│   ├── test_menu.py
│   ├── test_provenance.py
│   └── test_result.py
├── pyproject.toml
├── README.md
└── LICENSE
```

### Integration with existing repos
- `ecospheric-harness` is a **new repo**, sibling to `etp`, `edd`, `ese`
- Depends on `etp` (for `CommandDescriptor`, `build_function_block`, envelope types)
- Does NOT depend on `edd` or `ese` directly — discovers them at runtime via `--describe`
- Installed via `pip install -e .` with `etp>=0.1.0` as dependency

---

## D. Code Style

- **Python 3.11+** (matches ETP/EDD/ESE)
- **ruff** for linting, **mypy --strict** for type checking (matches ETP conventions)
- **pytest** for testing, **coverage** target ≥90%
- **uv** for dependency management (`uv.lock`, `pyproject.toml`)
- Docstrings on all public functions/classes (Google style, matching ETP)
- No external runtime deps beyond: `etp`, an LLM client library (`openai` or `anthropic` or `httpx` for OpenRouter), and stdlib
- Type hints everywhere, `from __future__ import annotations` at top of every module

---

## E. Testing Strategy

### Unit tests (per module)
- **registry**: mock `--describe` output, verify tool registration, descriptor parsing
- **validator**: feed malformed tool calls, verify rejection with schema error details
- **executor**: mock subprocess, verify stdin/stdout wiring, envelope capture, error handling
- **artifact**: verify sliding window — successful step replaces, failed step preserves
- **menu**: given an output descriptor, verify tool filtering by format compatibility
- **provenance**: given N step envelopes, verify chain stitching
- **orchestrator**: mock model responses and tool execution, verify multi-turn loop

### Integration tests
- Use real `edd` and `ese` binaries (installed locally) with small sample data
- Run a 2-3 step pipeline end-to-end (e.g., download → clip → reproject)
- Verify final artifact exists, provenance chain is complete, all steps recorded

### Test fixtures
- Mock `CommandDescriptor` objects covering vector, raster, pointcloud data types
- Mock ETP envelopes (success + error) for each exit code
- Sample Arrow IPC buffers for pipe testing

### Coverage
- ≥90% line coverage, enforced in CI
- All public API functions have at least one test
- Error paths tested (invalid params, tool failure, pipe corruption, model gives up)

---

## F. Boundaries

### Always do
- Validate every model-proposed tool call against ETP schema before execution
- Capture and return tool envelopes (success or error) to the model after each step
- Free intermediate artifacts when replaced by a new successful output
- Preserve the current artifact on step failure (for retry)
- Stitch provenance across all executed steps
- Run linter + type checker before committing
- Write tests for every module

### Ask first
- Adding support for a new LLM provider (beyond the initial choice)
- Changing the intermediate data strategy (e.g., supporting N artifacts instead of 1)
- Adding persistent artifact storage
- Modifying any ETP/EDD/ESE source code (harness should be self-contained)

### Never do
- Execute a tool call that fails schema validation (always reject, never "best-effort" execute)
- Pass raw binary data to the model (only envelopes/metadata)
- Allow the model to invoke non-registered tools
- Store secrets or API keys in code
- Bypass the ETP pipe contract (no direct in-process function calls to tools)

---

## Architecture Detail

### The Multi-Turn Loop

```
┌──────────────────────────────────────────────────────────┐
│  User: "Download S2B scene, clip to bbox, reproject"     │
└──────────────────┬───────────────────────────────────────┘
                   ▼
┌──────────────────────────────────────────────────────────┐
│  Orchestrator initializes:                               │
│  1. Load tool registry (edd --describe, ese --describe)  │
│  2. Build function-calling tool list for model           │
│  3. System prompt: "You have these tools. Plan and       │
│     execute step by step. One tool per turn."            │
└──────────────────┬───────────────────────────────────────┘
                   ▼
┌──────────────────────────────────────────────────────────┐
│  TURN 1                                                  │
│  Model → harness: "call edd_download({item: 'S2B...',    │
│     asset: 'visual'})"                                   │
│  Harness: validate ✓ → execute edd → capture envelope    │
│  Harness → model: {status: success, data: {format:       │
│     'geotiff', crs: 'EPSG:32610', bbox: [...], ...},     │
│     available_tools: [ese_raster_clip, ese_proj_...]}    │
│  Artifact: step1_output (persisted)                     │
└──────────────────┬───────────────────────────────────────┘
                   ▼
┌──────────────────────────────────────────────────────────┐
│  TURN 2                                                  │
│  Model → harness: "call ese_raster_clip({bbox: [...]})"  │
│  Harness: validate ✓ → execute ese (stdin = step1)       │
│  Harness → model: {status: success, data: {...}}        │
│  Artifact: step2_output (step1 freed)                    │
└──────────────────┬───────────────────────────────────────┘
                   ▼
┌──────────────────────────────────────────────────────────┐
│  TURN 3                                                  │
│  Model → harness: "call ese_proj_transform(             │
│     {target_crs: 'EPSG:3857'})"                          │
│  Harness: validate ✗ → reject: "param 'target_crs' not   │
│     in schema, did you mean 'crs'?"                      │
│  Harness → model: {status: rejected, schema_error: ...} │
│  Artifact: step2_output (preserved — step failed)        │
└──────────────────┬───────────────────────────────────────┘
                   ▼
┌──────────────────────────────────────────────────────────┐
│  TURN 3 (RETRY)                                          │
│  Model → harness: "call ese_proj_transform(              │
│     {crs: 'EPSG:3857'})"                                 │
│  Harness: validate ✓ → execute ese (stdin = step2)       │
│  Harness → model: {status: success, data: {...}}        │
│  Artifact: step3_output (step2 freed)                    │
│  Model: "Pipeline complete. Final result ready."        │
└──────────────────┬───────────────────────────────────────┘
                   ▼
┌──────────────────────────────────────────────────────────┐
│  Result: PipelineResult                                  │
│    final_artifact: path to step3_output                  │
│    provenance: [step1 → step2 → step3]                   │
│    steps: [StepRecord, StepRecord, StepRecord]           │
└──────────────────────────────────────────────────────────┘
```

### Tool Registry

At startup, the harness discovers tools by running `<tool> --describe --format <native>` for each registered tool binary. The output is parsed into `CommandDescriptor` objects (from `etp.describe`).

```python
@dataclass
class RegisteredTool:
    name: str               # "edd", "ese"
    version: str            # "0.5.0", "0.0.1"
    binary: str             # path to CLI binary
    commands: list[CommandDescriptor]
    
    def function_blocks(self) -> list[dict]:
        """OpenAI/Anthropic function definitions for all commands."""
        return [build_function_block(cmd, self.name) for cmd in self.commands]
```

### Menu Narrowing

After each successful step, the harness examines the output envelope's `data` block (which contains `format`, `data_type`, `crs`, etc.) and filters the available tool menu to only those commands whose `input_formats` are compatible with the current artifact's `output_formats`.

```python
def narrow_menu(
    all_tools: list[RegisteredTool],
    artifact: Artifact,
) -> list[CommandDescriptor]:
    """Filter tool commands to those that can consume the current artifact."""
    compatible = []
    for tool in all_tools:
        for cmd in tool.commands:
            if artifact.format in cmd.input_formats or cmd.data_type == "any":
                compatible.append(cmd)
    return compatible
```

Compatibility rules:
- Exact format match: artifact `geotiff` → command accepts `geotiff`
- `data_type` match: artifact is `raster` → command `data_type` is `raster` or `any`
- If no compatible tools remain, the model is told "no further tools can process this output" and the pipeline ends

### Artifact Manager (Sliding Window)

```python
class ArtifactManager:
    """Manages a single intermediate artifact — the most recent successful output."""
    
    def __init__(self, workdir: Path):
        self._workdir = workdir
        self._current: Artifact | None = None
    
    def store(self, data: bytes, envelope: dict) -> Artifact:
        """Persist new artifact, free previous if successful."""
        if self._current is not None:
            self._current.free()  # delete temp file
        path = self._workdir / f"step_{uuid4().hex[:8]}.bin"
        path.write_bytes(data)
        self._current = Artifact(path=path, envelope=envelope)
        return self._current
    
    def current(self) -> Artifact | None:
        """The most recent successful output, or None if pipeline hasn't started."""
        return self._current
    
    def free(self):
        """Clean up the current artifact (pipeline complete or aborted)."""
        if self._current:
            self._current.free()
            self._current = None
```

Key invariant: `store()` is only called after a **successful** step. Failed steps leave `self._current` untouched. The model can retry the current step any number of times — the input is still there.

### Validator

Before any tool execution, the harness validates the model's proposed call:

1. **Tool exists** in the registry
2. **Command exists** for that tool
3. **All required parameters** are present
4. **Parameter types** match the schema (string, number, boolean, array)
5. **Pattern constraints** (if any) are satisfied
6. **No unknown parameters** (additionalProperties: false)

If validation fails, the harness returns a rejection envelope to the model — WITHOUT executing the tool — so the model can correct and retry:

```python
{
    "status": "rejected",
    "error": {
        "type": "schema_validation_error",
        "message": "Parameter 'crs' is required but not provided",
        "param_path": "crs",
        "suggestion": "Required parameters: crs, bbox. See schema: ...",
    },
    "available_tools": [...],  # still the same menu — nothing executed
    "current_artifact": {...},  # still available
}
```

### Executor

```python
class ToolExecutor:
    """Execute a validated tool call via subprocess."""
    
    def execute(
        self,
        tool: RegisteredTool,
        command: CommandDescriptor,
        params: dict[str, Any],
        artifact: Artifact | None,
    ) -> ExecuteResult:
        """Spawn the tool process, wire stdin/stdout, capture envelope."""
        # Build CLI args from command name + params
        args = [tool.binary, command.name]
        for key, value in params.items():
            args.extend([f"--{key}", str(value)])
        
        # Wire stdin from current artifact (if pipe-eligible)
        stdin_data = None
        if artifact and self._is_pipe_eligible(artifact, command):
            stdin_data = artifact.path.read_bytes()
        
        # Execute
        proc = subprocess.run(
            args, input=stdin_data, capture_output=True, timeout=300
        )
        
        # Parse stdout envelope
        envelope = json.loads(proc.stdout)
        return ExecuteResult(envelope=envelope, returncode=proc.returncode)
```

### Provenance Chain

Each step's ETP envelope carries provenance metadata. The harness stitches these together:

```python
@dataclass
class StepRecord:
    step_number: int
    tool: str
    command: str
    params: dict[str, Any]
    status: str  # "success" | "error" | "rejected"
    envelope: dict[str, Any]
    duration_ms: int

@dataclass
class PipelineResult:
    steps: list[StepRecord]
    final_artifact: Artifact | None
    provenance_chain: list[dict]  # stitched from each step's envelope
    
    def summary(self) -> str:
        """Human-readable pipeline summary for the user."""
        ...
```

### Model Communication

The harness communicates with the model via the provider's tool-calling API (OpenAI, Anthropic, OpenRouter). Each turn:

1. Harness sends: system prompt (tool list + constraints) + conversation history + current state (artifact metadata, available tools)
2. Model responds: either a tool call (name + params) or a terminal message ("pipeline complete" / "I can't do this")
3. Harness executes or rejects, appends result to conversation history
4. Loop until model sends a terminal message or max turns reached

**System prompt (initial draft):**

```
You are a geospatial pipeline orchestrator. You have access to ETP-compatible 
tools that operate on geospatial data. 

Rules:
1. Call exactly ONE tool per turn.
2. Each tool call must specify all required parameters.
3. After each execution, you will receive the result envelope and a list 
   of tools compatible with the current output.
4. If a step fails, you may retry with different parameters. The previous 
   successful output is still available.
5. You have ONE intermediate artifact at any time — the most recent 
   successful output. You cannot reference outputs from earlier steps.
6. When the pipeline is complete, say "PIPELINE_COMPLETE" with a summary.
7. If you cannot complete the request, say "PIPELINE_FAILED" with the reason.

Available tools:
{tool_list_json}

Current artifact: None (pipeline not started)
```

**Turn state (injected each turn):**

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
  "available_tools": [
    {"name": "ese_raster_clip", "description": "Clip raster to bbox or polygon"},
    {"name": "ese_proj_transform", "description": "Transform CRS of spatial data"}
  ],
  "last_result": {
    "status": "success",
    "step": 1,
    "tool": "edd",
    "command": "download"
  }
}
```

### Max Turns

Default: 15 turns. Configurable. If the model hits the limit without completing, the harness returns a partial result with the steps executed so far and a "pipeline incomplete" status.

### Error Handling

| Error type | Harness behavior |
|---|---|
| Schema validation failure | Return rejection to model, preserve current artifact, let model retry |
| Tool execution failure (non-zero exit) | Parse error envelope, return to model, preserve current artifact |
| Tool timeout | Return timeout error to model, preserve current artifact |
| Model sends unparseable response | Return "invalid response" error, let model retry |
| Model gives up ("PIPELINE_FAILED") | End loop, return partial result with steps so far |
| Max turns reached | End loop, return partial result |

### Final Output

When the model declares the pipeline complete, the harness:
1. Persists the final artifact to a user-specified path (or a default location)
2. Writes the full provenance chain (JSON)
3. Writes a human-readable pipeline summary
4. Returns a `PipelineResult` object

---

## Acceptance Criteria

1. **AC1**: Harness discovers EDD and ESE tools via `--describe` at startup
2. **AC2**: Model receives tool list as function-calling definitions
3. **AC3**: Model proposes a tool call → harness validates against schema → executes if valid
4. **AC4**: Model proposes invalid tool call → harness rejects with schema error → model can retry
5. **AC5**: Successful step stores intermediate artifact, frees previous
6. **AC6**: Failed step preserves current intermediate artifact for retry
7. **AC7**: After each step, model receives narrowed tool menu based on output format
8. **AC8**: Multi-step pipeline (≥3 tools) executes end-to-end with correct data flow
9. **AC9**: Provenance chain captures all steps with tool, version, command, params, duration
10. **AC10**: Model declares "PIPELINE_COMPLETE" → harness persists final artifact + provenance
11. **AC11**: Model declares "PIPELINE_FAILED" → harness returns partial result with steps so far
12. **AC12**: Max turns exceeded → harness returns partial result with "pipeline incomplete" status
13. **AC13**: CLI: `ecospheric-harness "natural language request"` runs a full pipeline
14. **AC14**: Python API: `Harness(tools=["edd","ese"]).run("...")` returns `PipelineResult`
15. **AC15**: `--list-tools` shows all registered tools and their commands
16. **AC16**: `--dry-run` shows the model's planned pipeline without executing
17. **AC17**: Unit test coverage ≥90%
18. **AC18**: ruff + mypy --strict pass clean
