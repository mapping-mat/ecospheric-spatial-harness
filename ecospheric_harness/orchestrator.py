"""Multi-turn orchestration loop for the Ecospheric Agent Harness."""

from __future__ import annotations

import json
import os
import shutil
import time
from pathlib import Path
from typing import Any

import httpx  # kept for backward-compat with test patching (see _call_model)

from ecospheric_harness.artifact_registry import ArtifactRecord, ArtifactRegistry
from ecospheric_harness.output_validator import OutputValidator
from ecospheric_harness.params import normalize_params
from ecospheric_harness.config import HarnessConfig
from ecospheric_harness.corrections import CorrectionHandler
from ecospheric_harness.executor import ToolExecutor
from ecospheric_harness.intents import (
    IntentEntry,
    IntentOption,
    PreflightResult,
    Resolution,
    ResolutionError,
    parse_intent,
)
from ecospheric_harness.menu import available_intents
from ecospheric_harness.preflight import PreflightChecker
from ecospheric_harness.provenance import build_provenance_from_dag
from ecospheric_harness.registry import ToolRegistry
from ecospheric_harness.resolver import IntentResolver
from ecospheric_harness.result import PipelineResult, StepRecord
from ecospheric_harness.validator import SchemaValidator
from ecospheric_harness.workspace import WorkspaceManager
from ecospheric_harness.providers.base import ModelProvider, ModelResponse, ProviderError


# ---------------------------------------------------------------------------
# Format → file extension helper
# ---------------------------------------------------------------------------

_FORMAT_EXTENSIONS: dict[str, str] = {
    "geotiff": ".tif",
    "cog": ".tif",
    "geojson": ".geojson",
    "json": ".json",
    "geoparquet": ".parquet",
    "shp": ".shp",
    "gpkg": ".gpkg",
    "fgb": ".fgb",
    "kml": ".kml",
    "laz": ".laz",
    "las": ".las",
}


def _format_to_extension(fmt: str) -> str:
    """Map a format string to a file extension (with leading dot)."""
    return _FORMAT_EXTENSIONS.get(fmt.lower(), f".{fmt.lower()}" if fmt else ".bin")


# ---------------------------------------------------------------------------
# System prompt
# ---------------------------------------------------------------------------

_RULES = """\
You are a geospatial pipeline orchestrator. You emit intent commands
that a harness resolves to tool invocations. You have ONE function: emit_intent.

Rules:
1. Call emit_intent once per turn.
2. Available intents are listed in the turn state. Use only those.
3. After each execution, you receive results and updated available intents.
4. If a step fails, retry with different params. The current artifact is preserved.
5. To undo the last step: emit_intent(intent="undo")
6. To redo the last step with new params: emit_intent(intent="redo", params={{...}})
   Redo re-runs the SAME operation with different params. To do a DIFFERENT
   operation, use undo first, then emit the new intent.
7. Artifacts are named with stable IDs (e.g. clip_001, slope_002). The turn state
   shows the 2 most recent artifacts in detail plus a compact list of all artifacts.
   To use a specific past artifact as input, include `input_artifact_id` in params.
   If no input_artifact_id is specified, the most recent artifact is used automatically.
   Do NOT include `input` in params — the harness resolves it for you. Include
   `input_artifact_id` only when referencing a specific past artifact.
8. Search results appear in turn state as "search_results". For STAC search,
   results are metadata — pass "results_file" to fetch as the "stac" param.
   For direct-data search (OSM, geoBoundaries), the output IS the artifact
   and is stored in the registry. No fetch needed.
   Search results from STAC are NOT artifacts.
9. When complete: emit_intent(intent="complete", summary="...")
10. If you cannot complete: emit_intent(intent="failed", reason="...")
11. If `can_undo` is false, do not emit `undo` — it will fail.
12. For commands without a standard input parameter (e.g. hydro commands that
    use --d8-pntr instead of --input), include `_input_target` in params to
    specify which parameter receives the artifact path.
    Example: {{"intent": "basins", "params": {{"_input_target": "d8-pntr", "threshold": 500}}}}
13. For commands that need a mask or secondary input file (like `clip --by`),
    provide the file path in params. The harness will not auto-generate mask
    files.
    Example: {{"intent": "clip", "params": {{"by": "/tmp/harness/mask_abc.geojson"}}}}
14. Destructive operations (overwrite, delete) will require confirmation in a
    future phase. For now, the harness blocks path escapes and SSRF attempts
    automatically.
"""


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------


class Orchestrator:
    """Multi-turn orchestration loop between model and harness.

    Drives the model ↔ harness ↔ tools conversation until a terminal
    intent is emitted or the turn limit is reached.
    """

    def __init__(
        self,
        config: HarnessConfig,
        registry: ToolRegistry,
        resolver: IntentResolver,
        validator: SchemaValidator,
        executor: ToolExecutor,
        artifact_registry: ArtifactRegistry,
        preflight: PreflightChecker,
        corrections: CorrectionHandler,
        catalog: list[IntentEntry],
        workspace: WorkspaceManager,
        provider: ModelProvider | None = None,
        output_validator: OutputValidator | None = None,
        default_raster_format: str = "cog",
    ) -> None:
        self._config = config
        self._tool_registry = registry
        self._resolver = resolver
        self._validator = validator
        self._executor = executor
        self._artifact_registry = artifact_registry
        self._preflight = preflight
        self._corrections = corrections
        self._catalog = catalog
        self._workspace = workspace
        self._provider = provider
        self._output_validator = output_validator or OutputValidator()
        self._default_raster_format = default_raster_format

        self._steps: list[StepRecord] = []
        self._failed_redo_count: int = 0
        self._pending_warnings: list[dict[str, str]] = []

    # ------------------------------------------------------------------
    # Main loop
    # ------------------------------------------------------------------

    def run(self, prompt: str) -> PipelineResult:
        """Run the multi-turn orchestration loop.

        Args:
            prompt: The user's natural-language request.

        Returns:
            A :class:`PipelineResult` when a terminal intent is emitted
            or the turn limit is exceeded.
        """
        messages: list[dict[str, Any]] = [{"role": "user", "content": prompt}]

        for _ in range(self._config.max_turns):
            # 1-2. Build system prompt and tool definition.
            turn_state = self._build_turn_state("success", 0, "")
            system_prompt = self._build_system_prompt(turn_state)
            current_artifact = self._artifact_registry.current
            intents = available_intents(self._catalog, current_artifact, self._resolver)
            tool_def = self._build_emit_intent_tool(intents)

            # 3. Call model.
            try:
                model_response = self._call_model(system_prompt, tool_def, messages)
            except ValueError:
                # Malformed API response — retry on next turn.
                continue
            messages.append(model_response)

            # 3b. Defensive: answer extra tool_calls (only one per turn supported).
            extra_calls = model_response.get("tool_calls", [])[1:]
            for extra_call in extra_calls:
                extra_id = extra_call.get("id", "unknown")
                messages.append({
                    "role": "tool",
                    "content": json.dumps({
                        "error": "Only one tool call per turn is supported. "
                                 "Please emit one intent at a time.",
                    }),
                    "tool_call_id": extra_id,
                })

            # 4. Parse response.
            try:
                parsed = self._parse_model_response(model_response)
            except ValueError as exc:
                # Extract tool_call_id from raw response for the error message.
                fallback_id = "emit_intent"
                raw_calls = model_response.get("tool_calls")
                if raw_calls and isinstance(raw_calls, list) and len(raw_calls) > 0:
                    fallback_id = raw_calls[0].get("id", "emit_intent") or "emit_intent"
                messages.append({
                    "role": "tool",
                    "content": json.dumps({"error": str(exc)}),
                    "tool_call_id": fallback_id,
                })
                continue

            # 5-6. Dispatch.
            intent = parsed["intent"]
            params = parsed.get("params", {})
            tool_call_id: str = parsed.get("tool_call_id", "emit_intent")

            result, error_turn = self._dispatch(intent, params)
            if result is not None:
                return result
            # Both error and success paths must append a tool response
            # so that every tool_call has a matching tool message.
            if error_turn is not None:
                turn_response = error_turn
            elif self._steps:
                last_step = self._steps[-1]
                turn_response = self._build_turn_state(
                    "success", last_step.step_number, last_step.intent,
                )
            else:
                turn_response = self._build_turn_state("success", 0, "")
            messages.append({
                "role": "tool",
                "content": json.dumps(turn_response),
                "tool_call_id": tool_call_id,
            })

        # 9. Max turns exceeded.
        return self._build_result()

    # ------------------------------------------------------------------
    # Dispatch helpers
    # ------------------------------------------------------------------

    def _dispatch(
        self, intent: str, params: dict[str, Any],
    ) -> tuple[PipelineResult | None, dict[str, Any] | None]:
        """Dispatch a parsed intent.

        Returns (PipelineResult, None) for terminals,
        (None, error_turn) for errors to send back to the model,
        (None, None) for successful non-terminals.
        """
        if intent in ("complete", "failed"):
            return self._handle_terminal(intent, params), None

        if intent in ("undo", "redo"):
            self._handle_correction(intent, params)
            return None, None

        return self._handle_operation(intent, params)

    def _handle_terminal(self, intent: str, params: dict[str, Any]) -> PipelineResult:
        """Handle complete/failed terminal intents.

        Persists provenance JSON, summary JSON, and copies the artifact
        to session_dir/output for downstream consumption.
        """
        result = self._build_result()
        session_dir = self._workspace.session_dir
        session_dir.mkdir(parents=True, exist_ok=True)

        # 1. Write provenance JSON.
        provenance_path = session_dir / "provenance.json"
        provenance_path.write_text(
            json.dumps(result.provenance_chain, indent=2),
            encoding="utf-8",
        )

        # 2. Write summary JSON.
        summary_path = session_dir / "summary.json"
        summary_path.write_text(
            json.dumps({"summary": result.summary()}, indent=2),
            encoding="utf-8",
        )

        # 3. Copy current artifact to session_dir/output with appropriate extension.
        current = self._artifact_registry.current
        if current is not None and current.path.exists():
            ext = _format_to_extension(current.format)
            output_dir = session_dir / "output"
            output_dir.mkdir(parents=True, exist_ok=True)
            dest = output_dir / f"result{ext}"
            shutil.copy2(current.path, dest)

        return result

    def _handle_correction(self, intent: str, params: dict[str, Any]) -> None:
        """Handle undo/redo correction intents."""
        if intent == "undo":
            self._failed_redo_count = 0
            self._corrections.undo()
            return

        # redo
        result = self._corrections.redo(params)
        if result.status == "error":
            self._failed_redo_count += 1
        else:
            self._failed_redo_count = 0

    def _handle_operation(
        self, intent: str, params: dict[str, Any],
    ) -> tuple[PipelineResult | None, dict[str, Any] | None]:
        """Handle an operation intent (resolve → validate → preflight → execute).

        Returns (None, None) on success, (None, error_turn) on failure.
        """
        # a. Resolve input artifact
        input_artifact: ArtifactRecord | None = None

        # Normalize param keys (strip -- prefixes, hyphens → underscores)
        # so downstream validation/execution sees canonical names.
        params = normalize_params(params)

        # If the model passed `input` as an artifact ID, promote it to
        # `input_artifact_id` so the existing resolution handles it.
        input_val = params.get("input")
        if input_val and isinstance(input_val, str):
            artifact = self._artifact_registry.resolve_input(input_val)
            if artifact is not None:
                params.pop("input")
                params["input_artifact_id"] = input_val

        input_artifact_id = params.pop("input_artifact_id", None)

        if input_artifact_id:
            input_artifact = self._artifact_registry.resolve_input(input_artifact_id)
            if input_artifact is None:
                return None, self._make_error_turn(
                    f"Artifact '{input_artifact_id}' not found", intent
                )
        else:
            input_artifact = self._artifact_registry.current

        # Structural input requirement: if the underlying command needs an
        # input artifact and none is available, fail fast with a clear message.
        command_needs_input = self._resolver.command_needs_input(intent, params)
        if command_needs_input and input_artifact is None:
            return None, self._make_error_turn(
                f"'{intent}' requires an input artifact, but none exists yet. "
                "Run a search or fetch step first to produce one.",
                intent,
            )

        # b. Resolve.
        resolved = self._resolver.resolve(intent, params, input_artifact)
        if isinstance(resolved, ResolutionError):
            return None, self._make_error_turn(resolved.message, intent)

        # c. Check idempotency cache (only if command is marked idempotent)
        # For now, default all commands to idempotent=False (Phase 2 will classify)
        # TODO: Check catalog for idempotent flag
        # cached_id = self._artifact_registry.is_idempotent(...)
        # if cached_id:
        #     cached = self._artifact_registry.get(cached_id)
        #     if cached:
        #         return None, None  # Skip execution

        # d. Validate.
        validation = self._validator.validate(resolved)
        if not validation.ok:
            self._steps.append(StepRecord(
                step_number=len(self._steps) + 1,
                tool=resolved.tool.name,
                command=resolved.command.name,
                tool_ref=resolved.tool,
                command_ref=resolved.command,
                intent=intent,
                params=resolved.params,
                status="rejected",
                envelope=None,
            ))
            return None, self._make_error_turn("; ".join(validation.errors), intent)

        # e. Preflight — run all checks via pipeline
        preflight_results = self._preflight.run_all_checks(resolved, input_artifact, resolved.params)

        # Backward-compat fallback: when preflight mocks haven't been updated
        # to return results from run_all_checks (old-style individual mocks).
        if not preflight_results:
            preflight_results = [
                self._preflight.check_planar_crs(resolved.command, input_artifact),
                self._preflight.check_disk(input_artifact=input_artifact),
                self._preflight.check_ssrf(resolved.params),
            ]

        warnings: list[dict[str, str]] = []

        for result in preflight_results:
            # Normalize: handle old-style mocks with ok/error but no resolution.
            if isinstance(result, PreflightResult):
                resolution = result.resolution
                message = result.message
                check = result.check
            elif hasattr(result, "ok") and not result.ok:
                resolution = Resolution.BLOCK
                message = getattr(result, "error", "Preflight check failed")
                check = "preflight"
            else:
                continue

            if resolution == Resolution.BLOCK:
                self._steps.append(StepRecord(
                    step_number=len(self._steps) + 1,
                    tool=resolved.tool.name,
                    command=resolved.command.name,
                    tool_ref=resolved.tool,
                    command_ref=resolved.command,
                    intent=intent,
                    params=resolved.params,
                    status="rejected",
                    envelope=None,
                ))
                return None, self._make_error_turn(message, intent)
            elif resolution == Resolution.MODEL_DISCRETION:
                warnings.append({"check": check, "message": message})
            # AUTO_FIX and ASK_USER treated as BLOCK in Phase 2
            elif resolution in (Resolution.AUTO_FIX, Resolution.ASK_USER):
                self._steps.append(StepRecord(
                    step_number=len(self._steps) + 1,
                    tool=resolved.tool.name,
                    command=resolved.command.name,
                    tool_ref=resolved.tool,
                    command_ref=resolved.command,
                    intent=intent,
                    params=resolved.params,
                    status="rejected",
                    envelope=None,
                ))
                return None, self._make_error_turn(message, intent)

        # Store warnings for turn-state (consumed by _build_turn_state).
        self._pending_warnings = warnings

        # Inject default raster format if not specified
        if self._default_raster_format and not any(
            k in resolved.params for k in ("format", "output_format", "--format", "--output-format")
        ):
            # Check if this command produces raster output
            cmd_name = resolved.command.name.lower()
            raster_producing = any(x in cmd_name for x in (
                "reproject", "warp", "clip", "buffer", "slope", "aspect",
                "hillshade", "contour", "rasterize", "reclassify", "calc",
                "mosaic", "tile",
            ))
            if raster_producing:
                resolved.params["format"] = self._default_raster_format

        # g. Execute.
        t0 = time.monotonic()
        exec_result = self._executor.execute(
            resolved.tool,
            resolved.command,
            resolved.params,
            input_artifact,
            self._workspace,
        )
        duration_ms = int((time.monotonic() - t0) * 1000)

        # h. Error envelope.
        if exec_result.envelope.get("status") != "success":
            error_msg = self._extract_error_message(exec_result.envelope)
            self._steps.append(StepRecord(
                step_number=len(self._steps) + 1,
                tool=resolved.tool.name,
                command=resolved.command.name,
                tool_ref=resolved.tool,
                command_ref=resolved.command,
                intent=intent,
                params=resolved.params,
                status="error",
                envelope=exec_result.envelope,
                output_path=exec_result.output_path,
                duration_ms=duration_ms,
            ))
            return None, self._make_error_turn(error_msg, intent)

        # h.5. Output validation.
        validation = self._output_validator.validate(
            output_path=exec_result.output_path,
            envelope=exec_result.envelope,
            command=resolved.command,
            input_artifact=input_artifact,
            params=resolved.params,
        )
        if not validation.ok:
            # Clean up orphan output file
            self._workspace.cleanup_unregistered(exec_result.output_path)
            self._steps.append(StepRecord(
                step_number=len(self._steps) + 1,
                tool=resolved.tool.name,
                command=resolved.command.name,
                tool_ref=resolved.tool,
                command_ref=resolved.command,
                intent=intent,
                params=resolved.params,
                status="validation_failed",
                envelope=exec_result.envelope,
                output_path=exec_result.output_path,
                duration_ms=duration_ms,
            ))
            return None, self._make_error_turn(
                f"Output validation failed: {validation.error}. Partial output cleaned up.",
                intent,
            )

        # i. Success — register artifact and build step record.
        step_number = len(self._steps) + 1
        data_type = exec_result.envelope.get("data", {}).get("data_type", "unknown")
        fmt = exec_result.envelope.get("data", {}).get("format", "unknown")
        is_search = intent.startswith("search_") or data_type == "metadata"

        # Determine parent IDs
        parent_ids: list[str] = []
        if input_artifact is not None:
            parent_ids = [input_artifact.artifact_id]

        # Artifact registration: skip for pure metadata (STAC search)
        if data_type != "metadata":
            self._artifact_registry.register(
                path=exec_result.output_path,
                format=fmt,
                data_type=data_type,
                crs=exec_result.envelope.get("data", {}).get("crs")
                    or (exec_result.envelope.get("data", {}).get("crs_meta", {}) or {}).get("crs")
                    or exec_result.envelope.get("data", {}).get("output_crs"),
                bbox=exec_result.envelope.get("data", {}).get("bbox")
                    or exec_result.envelope.get("data", {}).get("bounds")
                    or exec_result.envelope.get("data", {}).get("extent"),
                step_number=step_number,
                envelope=exec_result.envelope,
                parent_ids=parent_ids,
                intent=intent,
                tool_name=resolved.tool.name,
                tool_version=resolved.tool.version,
                command_name=resolved.command.name,
                params=resolved.params,
                duration_ms=duration_ms,
                is_search=is_search,
            )
            # Record idempotency (for future use when commands are classified)
            # self._artifact_registry.record_idempotent(
            #     resolved.tool.name, resolved.tool.version,
            #     resolved.command.name, resolved.params,
            #     input_artifact.artifact_id if input_artifact else None,
            #     new_artifact.artifact_id,
            # )

        self._steps.append(StepRecord(
            step_number=step_number,
            tool=resolved.tool.name,
            command=resolved.command.name,
            tool_ref=resolved.tool,
            command_ref=resolved.command,
            intent=intent,
            params=resolved.params,
            status="success",
            envelope=exec_result.envelope,
            is_search=is_search,
            output_path=exec_result.output_path,
            duration_ms=duration_ms,
        ))
        self._failed_redo_count = 0
        return None, None

    def _make_error_turn(self, error: str, intent: str) -> dict[str, Any]:
        """Build a turn state with an error for the model to retry."""
        step_number = self._steps[-1].step_number if self._steps else 0
        turn = self._build_turn_state("error", step_number, intent)
        turn["last_result"]["message"] = error
        return turn

    # ------------------------------------------------------------------
    # Model communication
    # ------------------------------------------------------------------

    def _call_model(
        self,
        system_prompt: str,
        tool_def: dict[str, Any],
        messages: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """Call the model and return the assistant message dict.

        When a provider is configured, delegates to the provider abstraction.
        Otherwise falls back to direct OpenRouter HTTP calls for backward
        compatibility with code that patches ``ecospheric_harness.orchestrator.httpx``
        in tests.
        """
        if self._provider is not None:
            response: ModelResponse = self._provider.generate(
                system_prompt, messages, tool_def,
            )
            return {
                "tool_calls": response.tool_calls,
                "tool_call_id": response.tool_call_id,
                "usage": {
                    "input_tokens": response.usage.input_tokens,
                    "output_tokens": response.usage.output_tokens,
                },
                "finish_reason": response.finish_reason,
            }

        # -- fallback: direct OpenRouter call (backward compat with test patches) --
        api_key = os.environ.get("OPENROUTER_API_KEY", "")
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }
        payload: dict[str, Any] = {
            "model": self._config.model,
            "messages": [
                {"role": "system", "content": system_prompt},
                *messages,
            ],
            "tools": [tool_def],
            "parallel_tool_calls": False,
        }
        resp = httpx.post(
            "https://openrouter.ai/api/v1/chat/completions",
            headers=headers,
            json=payload,
            timeout=60.0,
        )
        resp.raise_for_status()
        try:
            body: dict[str, Any] = resp.json()
        except json.JSONDecodeError as exc:
            raw_text = resp.text[:500]
            raise ValueError(f"Invalid JSON in API response: {raw_text}") from exc
        try:
            message: dict[str, Any] = body["choices"][0]["message"]
        except (KeyError, IndexError) as exc:
            raw_text = resp.text[:500]
            raise ValueError(f"Unexpected API response structure: {raw_text}") from exc
        return message

    def _parse_model_response(self, response: dict[str, Any]) -> dict[str, Any]:
        """Extract and parse emit_intent arguments from the model response.

        Returns a dict with keys ``intent``, ``params``, and ``tool_call_id``.

        Raises:
            ValueError: If the response is not a valid tool call.
        """
        tool_calls = response.get("tool_calls")
        if not tool_calls:
            raise ValueError("Model did not emit a tool call")

        call = tool_calls[0]
        tool_call_id: str = call.get("id", "emit_intent")
        fn = call.get("function", {})
        if fn.get("name") != "emit_intent":
            raise ValueError(f"Unexpected tool call: {fn.get('name')}")

        try:
            args: dict[str, Any] = json.loads(fn.get("arguments", "{}"))
        except json.JSONDecodeError as exc:
            raise ValueError(f"Invalid JSON in tool arguments: {exc}") from exc

        # Validate via parse_intent (raises ValueError on bad input).
        parse_intent(args)
        args["tool_call_id"] = tool_call_id
        return args

    # ------------------------------------------------------------------
    # Turn state construction
    # ------------------------------------------------------------------

    def _build_system_prompt(self, turn_state: dict[str, Any]) -> str:
        """Build the system prompt from rules plus turn state JSON."""
        return f"{_RULES}\n{json.dumps(turn_state, indent=2)}"

    def _build_turn_state(
        self,
        status: str,
        step: int,
        intent: str,
    ) -> dict[str, Any]:
        """Build the turn state dict sent to the model after each iteration."""
        # Recent artifacts (2 most recent, full detail)
        recent = self._artifact_registry.get_recent(2)
        recent_artifacts: list[dict[str, Any]] = []
        for rec in recent:
            try:
                size_mb = round(rec.path.stat().st_size / (1024 * 1024), 2)
            except OSError:
                size_mb = 0.0
            recent_artifacts.append({
                "artifact_id": rec.artifact_id,
                "format": rec.format,
                "data_type": rec.data_type,
                "crs": rec.crs,
                "bbox": rec.bbox,
                "size_mb": size_mb,
                "intent": rec.intent,
                "step_number": rec.step_number,
            })

        # All artifacts (compact list)
        all_artifacts = self._artifact_registry.list_all()
        all_artifacts_compact: list[dict[str, Any]] = [
            {
                "artifact_id": rec.artifact_id,
                "data_type": rec.data_type,
                "format": rec.format,
                "intent": rec.intent,
                "step_number": rec.step_number,
            }
            for rec in all_artifacts
        ]

        current = self._artifact_registry.current
        intents = available_intents(self._catalog, current, self._resolver)
        intent_dicts = [
            {
                "intent": i.intent,
                "description": i.description,
                "required_params": i.required_params,
                "params": i.params,
            }
            for i in intents
        ]

        turn: dict[str, Any] = {
            "recent_artifacts": recent_artifacts,
            "all_artifacts": all_artifacts_compact,
            "available_intents": intent_dicts,
            "can_undo": self._artifact_registry.can_undo,
            "warnings": getattr(self, "_pending_warnings", []),
            "last_result": {"status": status, "step": step, "intent": intent},
        }

        # Add search_results from last step envelope if applicable.
        if self._steps:
            last = self._steps[-1]
            if last.envelope is not None and last.is_search and last.output_path is not None:
                data = last.envelope.get("data", {})
                data_type = data.get("data_type", "")
                turn["search_results"] = self._build_search_turn_state(
                    last.envelope, last.output_path, data_type,
                )

        if self._failed_redo_count > 0:
            turn["failed_attempts"] = self._failed_redo_count

        self._pending_warnings = []
        return turn

    def _build_emit_intent_tool(self, available: list[IntentOption]) -> dict[str, Any]:
        """Build the emit_intent function-calling tool definition.

        The intent enum is repopulated each turn with current available
        intents plus correction/terminal intents.
        """
        names = [i.intent for i in available]
        return {
            "type": "function",
            "function": {
                "name": "emit_intent",
                "description": "Emit a geospatial pipeline intent. One per turn.",
                "parameters": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "intent": {
                            "type": "string",
                            "description": (
                                "Operation from available_intents, or "
                                "'undo', 'redo', 'complete', 'failed'"
                            ),
                            "enum": [*names, "undo", "redo", "complete", "failed"],
                        },
                        "params": {
                            "type": "object",
                            "description": "Parameters for the operation. Include input_artifact_id to use a specific artifact.",
                            "additionalProperties": True,
                        },
                        "summary": {"type": "string"},
                        "reason": {"type": "string"},
                    },
                    "required": ["intent"],
                },
            },
        }

    # ------------------------------------------------------------------
    # Artifact / search result helpers
    # ------------------------------------------------------------------

    def _build_search_turn_state(
        self,
        envelope: dict[str, Any],
        output_path: Path,
        data_type: str,
    ) -> dict[str, Any]:
        """Build search results for turn state (STAC or direct-data shape)."""
        data: dict[str, Any] = envelope.get("data", {})

        if data_type == "metadata":
            # STAC: items with id/title/assets/bbox/datetime, capped at search_cap.
            items_raw: list[dict[str, Any]] = data.get("items", [])
            total = data.get("total_count", len(items_raw))
            capped = items_raw[: self._config.search_cap]
            return {
                "source": data.get("source", ""),
                "total_count": total,
                "returned_count": len(capped),
                "results_file": str(output_path),
                "items": [
                    {
                        "id": it.get("id", ""),
                        "title": it.get("title", ""),
                        "assets": it.get("assets", []),
                        "bbox": it.get("bbox"),
                        "datetime": it.get("datetime"),
                    }
                    for it in capped
                ],
            }

        # Direct-data (vector): feature_count/crs/bounds.
        return {
            "source": data.get("source", ""),
            "feature_count": data.get("feature_count", 0),
            "results_file": str(output_path),
            "format": data.get("format", ""),
            "data_type": data_type,
            "crs": data.get("crs"),
            "bounds": data.get("bounds") or data.get("bbox"),
        }

    # ------------------------------------------------------------------
    # Result construction
    # ------------------------------------------------------------------

    def _build_result(self) -> PipelineResult:
        """Build the final :class:`PipelineResult`."""
        current = self._artifact_registry.current
        
        # Build provenance from DAG
        artifacts_dict: dict[str, ArtifactRecord] = {
            rec.artifact_id: rec
            for rec in self._artifact_registry.list_all()
        }
        provenance_chain = build_provenance_from_dag(
            artifacts_dict,
            current.artifact_id if current else None,
        )

        return PipelineResult(
            steps=list(self._steps),
            final_artifact=current,
            provenance_chain=provenance_chain,
        )

    @staticmethod
    def _extract_error_message(envelope: dict[str, Any]) -> str:
        """Extract a human-readable error message from an error envelope."""
        error: Any = envelope.get("error")
        if isinstance(error, dict):
            msg: Any = error.get("message", "unknown error")
            return str(msg)
        return str(error) if error is not None else "unknown error"
