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
from ecospheric_harness.intents import ExecuteResult, RegisteredTool
from ecospheric_harness.workspace import PathConfinementError, WorkspaceManager

# Geo extensions used as heuristic for path-type params
_GEO_EXTENSIONS = frozenset({
    ".geojson", ".shp", ".gpkg", ".tif", ".tiff", ".geotiff",
    ".cog", ".fgb", ".parquet", ".geoparquet", ".kml", ".kmz",
    ".laz", ".las", ".ply", ".gdb", ".json",
})


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

    def __init__(self, subprocess_timeout: int = 300) -> None:
        self._timeout: int = subprocess_timeout

    def execute(
        self,
        tool: RegisteredTool,
        command: CommandDescriptor,
        params: dict[str, Any],
        input_artifact: Artifact | None,
        workspace: WorkspaceManager,
    ) -> ExecuteResult:
        """Execute a tool command and return the result.

        Steps:
        1. Generate output path
        2. Build argv with binary + tokenized command + --output
        3. Route input artifact if present
        4. Serialize params (stripping _input_target)
        5. Append --json flag
        6. Run subprocess
        7. Parse stdout JSON or construct error envelope
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

        output_path = workspace.create_temp_path()

        args: list[str] = [tool.binary]
        args.extend(command.name.split())  # tokenize "raster clip" → ["raster", "clip"]
        args.extend(["--output", str(output_path)])

        if input_artifact is not None:
            args.extend(self._route_input(input_artifact, command, params))

        # Strip _input_target from params before serialization (harness-internal key)
        serializable_params = {k: v for k, v in params.items() if k != "_input_target"}
        args.extend(self._serialize_params(serializable_params, command))
        args.append("--json")  # ensure envelope output

        try:
            proc = subprocess.run(args, capture_output=True, text=True, timeout=self._timeout)
        except subprocess.TimeoutExpired:
            envelope = {
                "status": "error",
                "error": {
                    "type": "timeout",
                    "message": f"Tool execution timed out after {self._timeout}s",
                    "exit_code": -1,
                    "retryable": False,
                },
            }
            return ExecuteResult(
                envelope=envelope,
                returncode=-1,
                output_path=output_path,
            )

        try:
            envelope = json.loads(proc.stdout)
        except json.JSONDecodeError:
            envelope = {
                "status": "error",
                "error": {
                    "type": "internal_error",
                    "message": "Tool produced invalid JSON output",
                    "exit_code": proc.returncode,
                    "retryable": False,
                },
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

        # Rule 4: no way to route
        raise ValueError(
            f"Command '{command.name}' has no standard input parameter. "
            f"Specify which parameter receives the artifact via _input_target."
        )

    def _serialize_params(
        self,
        params: dict[str, Any],
        command: CommandDescriptor,
    ) -> list[str]:
        """Serialize params using the shared serialize_params function."""
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
