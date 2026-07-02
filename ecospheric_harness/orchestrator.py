"""Multi-turn orchestration loop for the Ecospheric Agent Harness."""

from __future__ import annotations

import json
import os
import shutil
import time
from pathlib import Path
from typing import Any, cast

import httpx

from ecospheric_harness.artifact import Artifact, ArtifactManager
from ecospheric_harness.config import HarnessConfig
from ecospheric_harness.corrections import CorrectionHandler
from ecospheric_harness.executor import ToolExecutor
from ecospheric_harness.intents import (
    IntentEntry,
    IntentOption,
    ResolutionError,
    parse_intent,
)
from ecospheric_harness.menu import available_intents
from ecospheric_harness.preflight import PreflightChecker
from ecospheric_harness.provenance import build_provenance_chain
from ecospheric_harness.registry import ToolRegistry
from ecospheric_harness.resolver import IntentResolver
from ecospheric_harness.result import PipelineResult, StepRecord
from ecospheric_harness.validator import SchemaValidator
from ecospheric_harness.workspace import WorkspaceManager


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
    Example: {{"intent": "basins", "params": {{"_input_target": "d8-pntr", "threshold": 500}}}}
13. For commands that need a mask or secondary input file (like `clip --by`),
    provide the file path in params. The harness will not auto-generate mask
    files.
    Example: {{"intent": "clip", "params": {{"by": "/tmp/harness/mask_abc.geojson"}}}}
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
        artifacts: ArtifactManager,
        preflight: PreflightChecker,
        corrections: CorrectionHandler,
        catalog: list[IntentEntry],
        workspace: WorkspaceManager,
    ) -> None:
        self._config = config
        self._registry = registry
        self._resolver = resolver
        self._validator = validator
        self._executor = executor
        self._artifacts = artifacts
        self._preflight = preflight
        self._corrections = corrections
        self._catalog = catalog
        self._workspace = workspace

        self._steps: list[StepRecord] = []
        self._failed_redo_count: int = 0

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
            intents = available_intents(self._catalog, self._artifacts.current, self._resolver)
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
        current = self._artifacts.current
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
        # a. Resolve.
        resolved = self._resolver.resolve(intent, params, self._artifacts.current)
        if isinstance(resolved, ResolutionError):
            return None, self._make_error_turn(resolved.message, intent)

        # b. Validate.
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

        # c. Preflight.
        crs_result = self._preflight.check_planar_crs(resolved.command, self._artifacts.current)
        if not crs_result.ok:
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
            return None, self._make_error_turn(crs_result.error, intent)

        disk_result = self._preflight.check_disk(input_artifact=self._artifacts.current)
        if not disk_result.ok:
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
            return None, self._make_error_turn(disk_result.error, intent)

        # d. Execute.
        t0 = time.monotonic()
        exec_result = self._executor.execute(
            resolved.tool,
            resolved.command,
            resolved.params,
            self._artifacts.current,
            self._workspace,
        )
        duration_ms = int((time.monotonic() - t0) * 1000)

        # e. Error envelope.
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

        # f. Success — build artifact and step record.
        step_number = len(self._steps) + 1
        artifact = self._build_artifact(exec_result, step_number)

        data_type = exec_result.envelope.get("data", {}).get("data_type", "unknown")
        # is_search: any intent starting with "search_" or metadata data_type.
        is_search = intent.startswith("search_") or data_type == "metadata"
        # Artifact storage: store unless it's pure metadata (STAC search).
        if data_type != "metadata":
            self._artifacts.store(artifact)

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
        """Call the model via OpenRouter and return the assistant message dict."""
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
        """Build the system prompt from rules 1-13 plus turn state JSON."""
        return f"{_RULES}\n{json.dumps(turn_state, indent=2)}"

    def _build_turn_state(
        self,
        status: str,
        step: int,
        intent: str,
    ) -> dict[str, Any]:
        """Build the turn state dict sent to the model after each iteration."""
        current = self._artifacts.current
        current_artifact: dict[str, Any] | None = None
        if current is not None:
            try:
                size_mb = round(current.path.stat().st_size / (1024 * 1024), 2)
            except OSError:
                size_mb = 0.0
            current_artifact = {
                "format": current.format,
                "data_type": current.data_type,
                "crs": current.crs,
                "bbox": current.bbox,
                "size_mb": size_mb,
            }

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
            "current_artifact": current_artifact,
            "available_intents": intent_dicts,
            "can_undo": self._artifacts.can_undo,
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
                            "description": "Parameters for the operation.",
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

    @staticmethod
    def _build_artifact(result: Any, step_number: int) -> Artifact:
        """Construct an :class:`Artifact` from an execution result envelope.

        Extracts format, data_type, CRS, and bbox from the envelope data
        block on a best-effort basis.
        """
        data: dict[str, Any] = result.envelope.get("data", {})

        fmt = data.get("format", "unknown")
        data_type = data.get("data_type", "unknown")

        # CRS: best-effort.
        crs: str | None = (
            data.get("crs")
            or (data.get("crs_meta") or {}).get("crs")
            or data.get("output_crs")
        )

        # Bbox: best-effort.
        bbox: list[float] | None = (
            data.get("bbox") or data.get("bounds") or data.get("extent")
        )

        return Artifact(
            path=result.output_path,
            envelope=result.envelope,
            format=fmt,
            data_type=data_type,
            crs=crs,
            bbox=bbox,
            step_number=step_number,
        )

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
        return PipelineResult(
            steps=list(self._steps),
            final_artifact=self._artifacts.current,
            provenance_chain=build_provenance_chain(
                cast("list[Any]", self._steps),
            ),
        )

    @staticmethod
    def _extract_error_message(envelope: dict[str, Any]) -> str:
        """Extract a human-readable error message from an error envelope."""
        error: Any = envelope.get("error")
        if isinstance(error, dict):
            msg: Any = error.get("message", "unknown error")
            return str(msg)
        return str(error) if error is not None else "unknown error"
