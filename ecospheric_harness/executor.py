"""Subprocess invocation and parameter serialization for the Ecospheric Agent Harness.

Executes ETP-compatible tools via subprocess, handles input artifact routing,
serializes parameters with type-driven logic, and constructs ETP envelopes
from stdout or error conditions.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

from etp.describe import CommandDescriptor, ParameterDescriptor

from ecospheric_harness.artifact import Artifact
from ecospheric_harness.artifact_registry import ArtifactRecord
from ecospheric_harness.intents import ExecuteResult, RegisteredTool
from ecospheric_harness.security import SubprocessHardener, check_ssrf
from ecospheric_harness.workspace import PathConfinementError, WorkspaceManager

# Geo extensions used as heuristic for path-type params
_GEO_EXTENSIONS = frozenset({
    ".geojson", ".shp", ".gpkg", ".tif", ".tiff", ".geotiff",
    ".cog", ".fgb", ".parquet", ".geoparquet", ".kml", ".kmz",
    ".laz", ".las", ".ply", ".gdb", ".json",
})

# Maps ETP/artifact format names to the file extension ESE's _detect_fmt expects.
FORMAT_TO_EXT: dict[str, str] = {
    "geojson": ".geojson",
    "geoparquet": ".parquet",
    "gpkg": ".gpkg",
    "geopackage": ".gpkg",
    "geotiff": ".tif",
    "cog": ".tif",
    "shp": ".shp",
    "kml": ".kml",
}


def serialize_params(
    params: dict[str, Any],
    command: CommandDescriptor,
) -> list[str]:
    """Serialize params with type-driven handling + reverse name mapping.

    Serialization is driven by ParameterDescriptor.type, not by parameter name:
    - type=="string" + list value → comma-join (e.g. bbox → "xmin,ymin,xmax,ymax")
    - type=="string" + string value → pass as-is
    - type=="array" + list value → space-separated (e.g. --flag v1 v2 v3)
    - type=="boolean" → bare flag (True) or omit (False)
    - type=="number"/"integer" → flag + stringified value
    """
    args: list[str] = []
    # Build reverse map: property_name → ParameterDescriptor
    param_map: dict[str, ParameterDescriptor] = {}
    for p in command.parameters:
        prop_name = p.name.lstrip("-").replace("-", "_")
        param_map[prop_name] = p

    for key, value in params.items():
        # Normalize key: strip leading dashes, replace hyphens with underscores
        # so both "distance" and "--distance" and "--output-crs" → "output_crs"
        norm_key = key.lstrip("-").replace("-", "_")
        desc = param_map.get(norm_key)
        flag = desc.name if desc else f"--{norm_key.replace('_', '-')}"
        param_type = desc.type if desc else None

        if isinstance(value, bool):
            if value:
                args.append(flag)
        elif isinstance(value, list):
            if param_type == "string":
                # String-typed param with list value → comma-join
                args.extend([flag, ",".join(str(v) for v in value)])
            else:
                # Array type or fallback: single flag + space-separated values
                args.append(flag)
                args.extend(str(v) for v in value)
        else:
            args.extend([flag, str(value)])

    return args


class ToolExecutor:
    """Executes ETP tool commands as subprocesses with parameter serialization."""

    def __init__(self, hardener: SubprocessHardener | None = None, *, subprocess_timeout: int = 300) -> None:
        self._hardener: SubprocessHardener = hardener or SubprocessHardener()
        # Keep subprocess_timeout for backward compat; hardener.limits overrides.
        self._timeout: int = subprocess_timeout

    def execute(
        self,
        tool: RegisteredTool,
        command: CommandDescriptor,
        params: dict[str, Any],
        input_artifact: Artifact | ArtifactRecord | None,
        workspace: WorkspaceManager,
    ) -> ExecuteResult:
        """Execute a tool command and return the result.

        Steps:
        1. Check path-typed params for confinement
        2. Check model-emitted URLs for SSRF
        3. Generate output path
        4. Build argv with binary + tokenized command + --output
        5. Route input artifact if present
        6. Serialize params (stripping _input_target)
        7. Append --json flag
        8. Run subprocess with hardened env, resource limits, timeout
        9. Sanitize output and parse JSON or construct error envelope
        """
        # Check path-typed params for confinement
        confinement_error = self._check_param_paths(params, workspace)
        if confinement_error is not None:
            envelope: dict[str, Any] = {
                "status": "error",
                "error": {
                    "type": "path_confinement",
                    "message": str(confinement_error),
                    "exit_code": -1,
                    "retryable": False,
                },
            }
            return ExecuteResult(
                envelope=envelope,
                returncode=-1,
                output_path=workspace.create_temp_path(),
            )

        # Check model-emitted URLs for SSRF
        ssrf_error = self._check_ssrf_params(params)
        if ssrf_error is not None:
            return ExecuteResult(
                envelope={
                    "status": "error",
                    "error": {
                        "type": "ssrf_blocked",
                        "message": ssrf_error,
                        "exit_code": -1,
                        "retryable": False,
                    },
                },
                returncode=-1,
                output_path=workspace.create_temp_path(),
            )

        # Determine output file extension from target format or input artifact
        suffix = ".bin"
        # Check if params specify a target output format (e.g. convert command)
        output_fmt = params.get("output_format") or params.get("format")
        if output_fmt:
            suffix = FORMAT_TO_EXT.get(str(output_fmt), ".bin")
        elif input_artifact is not None:
            fmt = getattr(input_artifact, "format", None)
            if fmt:
                suffix = FORMAT_TO_EXT.get(str(fmt), ".bin")
        output_path = workspace.create_temp_path(suffix=suffix)

        args: list[str] = [tool.binary]
        args.extend(command.name.split())  # tokenize "raster clip" → ["raster", "clip"]
        args.extend(["--output", str(output_path)])

        if input_artifact is not None:
            args.extend(self._route_input(input_artifact, command, params))
            # input was routed via _route_input — don't also serialize it as a param
            serializable_params = {k: v for k, v in params.items() if k != "_input_target" and k != "input"}
        else:
            serializable_params = {k: v for k, v in params.items() if k != "_input_target"}
        args.extend(self._serialize_params(serializable_params, command))
        args.append("--json")  # ensure envelope output

        # Build hardened environment and get resource limit function
        env = self._hardener.build_env()
        timeout = self._hardener.limits.wall_clock_timeout
        max_output = self._hardener.limits.max_output_bytes
        preexec = self._hardener.preexec_fn()

        try:
            proc = subprocess.run(
                args,
                capture_output=True,
                text=True,
                timeout=timeout,
                env=env,
                preexec_fn=preexec,
            )
        except subprocess.TimeoutExpired:
            envelope = {
                "status": "error",
                "error": {
                    "type": "timeout",
                    "message": f"Tool execution timed out after {timeout}s",
                    "exit_code": -1,
                    "retryable": False,
                },
            }
            return ExecuteResult(
                envelope=envelope,
                returncode=-1,
                output_path=output_path,
            )

        # Post-hoc output size check (max_output_bytes).
        truncated = False
        # Ensure strings (subprocess.run with text=True returns str, but
        # test mocks may return bytes or unset attributes as MagicMock).
        stdout = proc.stdout
        stderr = proc.stderr
        if isinstance(stdout, bytes):
            stdout = stdout.decode("utf-8", errors="replace")
        elif not isinstance(stdout, str):
            stdout = ""
        if isinstance(stderr, bytes):
            stderr = stderr.decode("utf-8", errors="replace")
        elif not isinstance(stderr, str):
            stderr = ""
        if len(stdout) > max_output:
            stdout = stdout[:max_output]
            truncated = True
        if len(stderr) > max_output:
            stderr = stderr[:max_output]
            truncated = True

        # Sanitize output (redact secrets, API keys, home paths)
        sanitized = self._hardener.sanitize_output(stdout, stderr)
        clean_stdout = sanitized.stdout

        if truncated:
            # Note truncation in stderr if not already erroring
            clean_stdout = clean_stdout  # already truncated above

        try:
            envelope = json.loads(clean_stdout)
        except json.JSONDecodeError:
            # Include stderr and a snippet of stdout so the model can
            # diagnose the actual failure (e.g. wrong args, missing dep).
            stdout_snippet = clean_stdout[:500] if clean_stdout else "(empty)"
            stderr_snippet = stderr[:500] if stderr else ""
            detail = f"exit_code={proc.returncode}, stdout={stdout_snippet}"
            if stderr_snippet:
                detail += f", stderr={stderr_snippet}"
            envelope = {
                "status": "error",
                "error": {
                    "type": "internal_error",
                    "message": f"Tool produced invalid JSON output ({detail})",
                    "exit_code": proc.returncode,
                    "retryable": False,
                },
            }

        # If output was truncated, append metadata to envelope.
        if truncated and envelope.get("status") == "success":
            envelope.setdefault("_warnings", []).append(
                f"Output truncated to {max_output} bytes (max_output_bytes limit)"
            )

        return ExecuteResult(
            envelope=envelope,
            returncode=proc.returncode,
            output_path=output_path,
        )

    @staticmethod
    def _check_ssrf_params(params: dict[str, Any]) -> str | None:
        """Check param values for SSRF-targeted URLs.

        Returns an error message if a URL is blocked, or None if all clear.
        """
        for key, value in params.items():
            if key == "_input_target":
                continue
            if not isinstance(value, str):
                continue
            if value.startswith("http://") or value.startswith("https://"):
                try:
                    check_ssrf(value)
                except ValueError as exc:
                    return f"URL in param '{key}' is blocked: {exc}"
        return None

    def _route_input(
        self,
        input_artifact: Artifact | ArtifactRecord,
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
            target_str = str(target_name)
            # Find the param descriptor to determine how to serialize
            for p in command.parameters:
                flag_name = f"--{target_str}"
                if p.name == flag_name or p.name == target_str:
                    if p.name.startswith("--"):
                        return [p.name, path]
                    else:
                        return [path]  # positional
            # Target name provided but not found in command params
            raise ValueError(
                f"_input_target '{target_str}' not found in command "
                f"'{command.name}' parameters"
            )

        # Rule 4: fallback — append as positional argument
        # Many ESE commands accept INPUT_PATH as a positional arg that isn't
        # declared in CommandDescriptor.parameters (e.g. `convert`).
        # Appending the path as a positional is safe: if the command doesn't
        # accept it, the CLI will error with a clear message.
        return [path]

    def _serialize_params(
        self,
        params: dict[str, Any],
        command: CommandDescriptor,
    ) -> list[str]:
        """Serialize params using the shared serialize_params function.

        Note: The canonical normalization point for param keys is
        ``ecospheric_harness.params.normalize_params``, called by the
        orchestrator before validation/execution. This method's own
        ``norm_key`` logic in ``serialize_params`` is defense-in-depth.
        """
        return serialize_params(params, command)

    @staticmethod
    def _check_param_paths(
        params: dict[str, Any],
        workspace: WorkspaceManager,
    ) -> PathConfinementError | None:
        """Check path-like params for confinement.

        Heuristic: a param value is path-like if:
        - it's a string containing '/' or '\\'
        - its lowercase form ends with a known geo extension

        Returns the first PathConfinementError encountered, or None.
        """
        for key, value in params.items():
            if key == "_input_target":
                continue
            if not isinstance(value, str):
                continue
            looks_like_path = (
                "/" in value
                or "\\" in value
                or any(value.lower().endswith(ext) for ext in _GEO_EXTENSIONS)
            )
            if not looks_like_path:
                continue
            try:
                workspace.check_path(Path(value))
            except PathConfinementError as exc:
                return exc
        return None
