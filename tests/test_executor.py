"""Tests for ecospheric_harness.executor.

Covers subprocess invocation, parameter serialization, input routing,
command tokenization, and error handling (AC27–AC31, AC35, AC43, AC47).
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from etp.describe import CommandDescriptor, ParameterDescriptor

from ecospheric_harness.artifact import Artifact
from ecospheric_harness.executor import ToolExecutor
from ecospheric_harness.workspace import WorkspaceManager
from ecospheric_harness.intents import ExecuteResult, RegisteredTool

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_command(
    name: str,
    parameters: list[ParameterDescriptor] | None = None,
) -> CommandDescriptor:
    return CommandDescriptor(
        name=name,
        description="test command",
        category="test",
        parameters=parameters or [],
    )


def _make_param(name: str, ptype: str = "string", required: bool = False) -> ParameterDescriptor:
    return ParameterDescriptor(
        name=name,
        description=f"param {name}",
        type=ptype,
        required=required,
    )


def _make_tool(binary: str = "ese") -> RegisteredTool:
    return RegisteredTool(
        name="ese",
        version="0.5.0",
        binary=binary,
        commands=[],
    )


def _make_artifact(path: Path) -> Artifact:
    return Artifact(
        path=path,
        envelope={"status": "success"},
        format="geotiff",
        data_type="raster",
    )


def _success_envelope(command: str = "clip") -> dict[str, Any]:
    return {
        "tool": "ese",
        "tool_version": "0.5.0",
        "schema_version": "1.0",
        "status": "success",
        "command": command,
        "data": {"format": "geotiff", "data_type": "raster"},
    }


def _mock_completed(result_envelope: dict[str, Any], returncode: int = 0) -> MagicMock:
    proc = MagicMock()
    proc.stdout = json.dumps(result_envelope).encode()
    proc.returncode = returncode
    return proc


# ---------------------------------------------------------------------------
# AC27: Uniform after-command placement — --output comes after subcommand
# ---------------------------------------------------------------------------


class TestUniformOptionPlacement:
    """AC27: --output is placed after the subcommand in argv."""

    @patch("ecospheric_harness.executor.subprocess.run")
    def test_output_after_subcommand(self, mock_run: MagicMock, tmp_path: Path) -> None:
        mock_run.return_value = _mock_completed(_success_envelope("clip"))

        tool = _make_tool("ese")
        cmd = _make_command("clip", [_make_param("--input", "string", True)])
        artifact = _make_artifact(tmp_path / "input.tif")

        executor = ToolExecutor()
        executor.execute(tool, cmd, {}, artifact, WorkspaceManager(tmp_path, disk_limit_bytes=10_000_000))

        args = mock_run.call_args[0][0]
        # argv: ["ese", "clip", "--output", <path>, "--input", <path>, "--json"]
        assert args[0] == "ese"
        assert args[1] == "clip"
        assert args[2] == "--output"
        # --output appears at index 2, after "ese" and "clip"
        assert args.index("--output") > args.index("clip")


# ---------------------------------------------------------------------------
# Output path extension bug fix — output_path suffix must match a format
# ESE's _detect_fmt recognizes, not the workspace default ".bin".
# ---------------------------------------------------------------------------


class TestOutputPathExtension:
    """Output path suffix is derived from input artifact format or output_format param."""

    @patch("ecospheric_harness.executor.subprocess.run")
    def test_output_path_uses_parquet_extension_for_geoparquet_input(
        self, mock_run: MagicMock, tmp_path: Path
    ) -> None:
        mock_run.return_value = _mock_completed(_success_envelope("transform"))

        tool = _make_tool("ese")
        cmd = _make_command("proj transform", [_make_param("input", "string", True)])
        artifact = Artifact(
            path=tmp_path / "input.parquet",
            envelope={"status": "success"},
            format="geoparquet",
            data_type="vector",
        )

        executor = ToolExecutor()
        executor.execute(tool, cmd, {}, artifact, WorkspaceManager(tmp_path, disk_limit_bytes=10_000_000))

        args = mock_run.call_args[0][0]
        output_path = args[args.index("--output") + 1]
        assert output_path.endswith(".parquet")

    @patch("ecospheric_harness.executor.subprocess.run")
    def test_output_path_uses_geojson_for_convert_output_format(
        self, mock_run: MagicMock, tmp_path: Path
    ) -> None:
        mock_run.return_value = _mock_completed(_success_envelope("convert"))

        tool = _make_tool("ese")
        cmd = _make_command("convert", [_make_param("input", "string", True)])
        artifact = _make_artifact(tmp_path / "input.tif")

        executor = ToolExecutor()
        executor.execute(
            tool,
            cmd,
            {"output_format": "geojson"},
            artifact,
            WorkspaceManager(tmp_path, disk_limit_bytes=10_000_000),
        )

        args = mock_run.call_args[0][0]
        output_path = args[args.index("--output") + 1]
        assert output_path.endswith(".geojson")

    @patch("ecospheric_harness.executor.subprocess.run")
    def test_output_path_defaults_to_bin_when_no_input_artifact(
        self, mock_run: MagicMock, tmp_path: Path
    ) -> None:
        mock_run.return_value = _mock_completed(_success_envelope("search"))

        tool = _make_tool("ese")
        cmd = _make_command("search", [])

        executor = ToolExecutor()
        executor.execute(tool, cmd, {}, None, WorkspaceManager(tmp_path, disk_limit_bytes=10_000_000))

        args = mock_run.call_args[0][0]
        output_path = args[args.index("--output") + 1]
        assert output_path.endswith(".bin")

    @patch("ecospheric_harness.executor.subprocess.run")
    def test_output_path_unknown_format_falls_back_to_bin(
        self, mock_run: MagicMock, tmp_path: Path
    ) -> None:
        mock_run.return_value = _mock_completed(_success_envelope("clip"))

        tool = _make_tool("ese")
        cmd = _make_command("clip", [_make_param("input", "string", True)])
        artifact = Artifact(
            path=tmp_path / "input.xyz",
            envelope={"status": "success"},
            format="unknown_format",
            data_type="raster",
        )

        executor = ToolExecutor()
        executor.execute(tool, cmd, {}, artifact, WorkspaceManager(tmp_path, disk_limit_bytes=10_000_000))

        args = mock_run.call_args[0][0]
        output_path = args[args.index("--output") + 1]
        assert output_path.endswith(".bin")


# ---------------------------------------------------------------------------
# AC28: Command name tokenization
# ---------------------------------------------------------------------------


class TestCommandTokenization:
    """AC28: "raster clip" → ["raster", "clip"] in argv."""

    @patch("ecospheric_harness.executor.subprocess.run")
    def test_multi_word_command_tokenized(self, mock_run: MagicMock, tmp_path: Path) -> None:
        mock_run.return_value = _mock_completed(_success_envelope("raster clip"))

        tool = _make_tool("ese")
        cmd = _make_command("raster clip", [_make_param("input", "string", True)])
        artifact = _make_artifact(tmp_path / "input.tif")

        executor = ToolExecutor()
        executor.execute(tool, cmd, {}, artifact, WorkspaceManager(tmp_path, disk_limit_bytes=10_000_000))

        args = mock_run.call_args[0][0]
        assert args[0] == "ese"
        assert args[1] == "raster"
        assert args[2] == "clip"
        assert args[3] == "--output"


# ---------------------------------------------------------------------------
# AC29 & AC47: Param serialization — type-driven
# ---------------------------------------------------------------------------


class TestParamSerialization:
    """AC29/AC47: Type-driven serialization for string, array, and more."""

    @patch("ecospheric_harness.executor.subprocess.run")
    def test_string_list_comma_join(self, mock_run: MagicMock, tmp_path: Path) -> None:
        """AC29/AC47: bbox ["-121.5","38.2"] → ["--bbox", "-121.5,38.2"]"""
        mock_run.return_value = _mock_completed(_success_envelope())

        tool = _make_tool()
        cmd = _make_command("clip", [_make_param("--bbox", "string")])

        executor = ToolExecutor()
        executor.execute(tool, cmd, {"bbox": ["-121.5", "38.2"]}, None, WorkspaceManager(tmp_path, disk_limit_bytes=10_000_000))

        args = mock_run.call_args[0][0]
        bbox_idx = args.index("--bbox")
        assert args[bbox_idx + 1] == "-121.5,38.2"

    @patch("ecospheric_harness.executor.subprocess.run")
    def test_string_value_as_is(self, mock_run: MagicMock, tmp_path: Path) -> None:
        """String param + string value → passed as-is."""
        mock_run.return_value = _mock_completed(_success_envelope())

        tool = _make_tool()
        cmd = _make_command("clip", [_make_param("--bbox", "string")])

        executor = ToolExecutor()
        executor.execute(tool, cmd, {"bbox": "-121.5,38.2"}, None, WorkspaceManager(tmp_path, disk_limit_bytes=10_000_000))

        args = mock_run.call_args[0][0]
        bbox_idx = args.index("--bbox")
        assert args[bbox_idx + 1] == "-121.5,38.2"

    @patch("ecospheric_harness.executor.subprocess.run")
    def test_array_list_space_separated(self, mock_run: MagicMock, tmp_path: Path) -> None:
        """AC29: array type + list → flag + space-separated values."""
        mock_run.return_value = _mock_completed(_success_envelope())

        tool = _make_tool()
        cmd = _make_command("clip", [_make_param("--flag", "array")])

        executor = ToolExecutor()
        executor.execute(tool, cmd, {"flag": ["v1", "v2"]}, None, WorkspaceManager(tmp_path, disk_limit_bytes=10_000_000))

        args = mock_run.call_args[0][0]
        flag_idx = args.index("--flag")
        assert args[flag_idx + 1] == "v1"
        assert args[flag_idx + 2] == "v2"


# ---------------------------------------------------------------------------
# AC30: Boolean serialization
# ---------------------------------------------------------------------------


class TestBooleanSerialization:
    """AC30: True → bare flag, False → omitted."""

    @patch("ecospheric_harness.executor.subprocess.run")
    def test_bool_true_bare_flag(self, mock_run: MagicMock, tmp_path: Path) -> None:
        mock_run.return_value = _mock_completed(_success_envelope())

        tool = _make_tool()
        cmd = _make_command("clip", [_make_param("--verbose", "boolean")])

        executor = ToolExecutor()
        executor.execute(tool, cmd, {"verbose": True}, None, WorkspaceManager(tmp_path, disk_limit_bytes=10_000_000))

        args = mock_run.call_args[0][0]
        assert "--verbose" in args
        # Bare flag — next arg should NOT be "True"
        vidx = args.index("--verbose")
        assert args[vidx + 1] != "True" if vidx + 1 < len(args) - 1 else True

    @patch("ecospheric_harness.executor.subprocess.run")
    def test_bool_false_omitted(self, mock_run: MagicMock, tmp_path: Path) -> None:
        mock_run.return_value = _mock_completed(_success_envelope())

        tool = _make_tool()
        cmd = _make_command("clip", [_make_param("--verbose", "boolean")])

        executor = ToolExecutor()
        executor.execute(tool, cmd, {"verbose": False}, None, WorkspaceManager(tmp_path, disk_limit_bytes=10_000_000))

        args = mock_run.call_args[0][0]
        assert "--verbose" not in args


# ---------------------------------------------------------------------------
# Integer/number serialization
# ---------------------------------------------------------------------------


class TestNumericSerialization:
    """Integer/number → flag + stringified value."""

    @patch("ecospheric_harness.executor.subprocess.run")
    def test_integer_param(self, mock_run: MagicMock, tmp_path: Path) -> None:
        mock_run.return_value = _mock_completed(_success_envelope())

        tool = _make_tool()
        cmd = _make_command("clip", [_make_param("--threshold", "integer")])

        executor = ToolExecutor()
        executor.execute(tool, cmd, {"threshold": 500}, None, WorkspaceManager(tmp_path, disk_limit_bytes=10_000_000))

        args = mock_run.call_args[0][0]
        tidx = args.index("--threshold")
        assert args[tidx + 1] == "500"


# ---------------------------------------------------------------------------
# AC31: Name reverse-map
# ---------------------------------------------------------------------------


class TestNameReverseMap:
    """AC31: model emits min_area → CLI gets --min-area."""

    @patch("ecospheric_harness.executor.subprocess.run")
    def test_underscore_to_hyphen(self, mock_run: MagicMock, tmp_path: Path) -> None:
        mock_run.return_value = _mock_completed(_success_envelope())

        tool = _make_tool()
        cmd = _make_command("clip", [_make_param("--min-area", "number")])

        executor = ToolExecutor()
        executor.execute(tool, cmd, {"min_area": 100.0}, None, WorkspaceManager(tmp_path, disk_limit_bytes=10_000_000))

        args = mock_run.call_args[0][0]
        assert "--min-area" in args
        midx = args.index("--min-area")
        assert args[midx + 1] == "100.0"

    @patch("ecospheric_harness.executor.subprocess.run")
    def test_unknown_param_fallback(self, mock_run: MagicMock, tmp_path: Path) -> None:
        """Param not in descriptor → fallback to --{key.replace('_','-')}."""
        mock_run.return_value = _mock_completed(_success_envelope())

        tool = _make_tool()
        cmd = _make_command("clip")  # no parameters defined

        executor = ToolExecutor()
        executor.execute(tool, cmd, {"custom_flag": "value"}, None, WorkspaceManager(tmp_path, disk_limit_bytes=10_000_000))

        args = mock_run.call_args[0][0]
        assert "--custom-flag" in args
        cidx = args.index("--custom-flag")
        assert args[cidx + 1] == "value"


# ---------------------------------------------------------------------------
# Input routing
# ---------------------------------------------------------------------------


class TestInputRouting:
    """Input artifact routing rules."""

    @patch("ecospheric_harness.executor.subprocess.run")
    def test_positional_input(self, mock_run: MagicMock, tmp_path: Path) -> None:
        """Rule 1: param name="input" (no --) → positional arg."""
        mock_run.return_value = _mock_completed(_success_envelope())

        tool = _make_tool()
        cmd = _make_command("clip", [_make_param("input", "string", True)])
        artifact = _make_artifact(tmp_path / "in.tif")

        executor = ToolExecutor()
        executor.execute(tool, cmd, {}, artifact, WorkspaceManager(tmp_path, disk_limit_bytes=10_000_000))

        args = mock_run.call_args[0][0]
        # After binary + subcommand + --output, the positional arg should be next
        assert str(artifact.path) in args
        pos_idx = args.index(str(artifact.path))
        # Positional — no preceding -- flag for this value
        assert args[pos_idx - 1] != "--input"

    @patch("ecospheric_harness.executor.subprocess.run")
    def test_input_flag(self, mock_run: MagicMock, tmp_path: Path) -> None:
        """Rule 2: param name="--input" → ["--input", path]."""
        mock_run.return_value = _mock_completed(_success_envelope())

        tool = _make_tool()
        cmd = _make_command("fetch", [_make_param("--input", "string", True)])
        artifact = _make_artifact(tmp_path / "in.tif")

        executor = ToolExecutor()
        executor.execute(tool, cmd, {}, artifact, WorkspaceManager(tmp_path, disk_limit_bytes=10_000_000))

        args = mock_run.call_args[0][0]
        iidx = args.index("--input")
        assert args[iidx + 1] == str(artifact.path)

    @patch("ecospheric_harness.executor.subprocess.run")
    def test_input_target_flag(self, mock_run: MagicMock, tmp_path: Path) -> None:
        """Rule 3: _input_target="d8-pntr" with --d8-pntr param → ["--d8-pntr", path]."""
        mock_run.return_value = _mock_completed(_success_envelope("basins"))

        tool = _make_tool()
        cmd = _make_command(
            "hydro basins",
            [_make_param("--d8-pntr", "string", True), _make_param("--output", "string")],
        )
        artifact = _make_artifact(tmp_path / "pntr.tif")

        executor = ToolExecutor()
        executor.execute(tool, cmd, {"_input_target": "d8-pntr"}, artifact, WorkspaceManager(tmp_path, disk_limit_bytes=10_000_000))

        args = mock_run.call_args[0][0]
        didx = args.index("--d8-pntr")
        assert args[didx + 1] == str(artifact.path)

    def test_no_input_no_target_positional_fallback(self, tmp_path: Path) -> None:
        """Rule 4: no input param, no _input_target → positional arg fallback."""
        cmd = _make_command("process", [_make_param("--threshold", "integer")])
        artifact = _make_artifact(tmp_path / "in.tif")

        executor = ToolExecutor()
        result = executor._route_input(artifact, cmd, {})
        assert result == [str(artifact.path)]

    def test_convert_style_command_positional_fallback(self, tmp_path: Path) -> None:
        """Regression: commands like `convert` with no declared input param
        should fall back to positional arg (simulates ESE convert command)."""
        # Simulate a CommandDescriptor like ESE's `convert` — it takes
        # INPUT_PATH as a positional arg but doesn't declare it in parameters.
        cmd = _make_command("convert", [
            _make_param("--from", "string"),
            _make_param("--to", "string"),
        ])
        artifact = _make_artifact(tmp_path / "data.geojson")

        executor = ToolExecutor()
        result = executor._route_input(artifact, cmd, {})
        assert result == [str(artifact.path)]

    def test_input_target_not_found_raises(self, tmp_path: Path) -> None:
        """_input_target references nonexistent param → ValueError."""
        cmd = _make_command("process", [_make_param("--threshold", "integer")])
        artifact = _make_artifact(tmp_path / "in.tif")

        executor = ToolExecutor()
        with pytest.raises(ValueError, match="not found"):
            executor._route_input(artifact, cmd, {"_input_target": "nonexistent"})

    @patch("ecospheric_harness.executor.subprocess.run")
    def test_input_not_doubled_when_artifact_and_param(self, mock_run: MagicMock, tmp_path: Path) -> None:
        """When input_artifact is routed AND params contains 'input', --input
        must NOT appear a second time from _serialize_params."""
        mock_run.return_value = _mock_completed(_success_envelope())

        tool = _make_tool("ese")
        # command declares "input" as a positional param (name without --)
        cmd = _make_command("clip", [_make_param("input", "string", True)])
        artifact = _make_artifact(tmp_path / "routed.tif")

        executor = ToolExecutor()
        # params contains an "input" key that should be suppressed
        # Use "-" (stdin sentinel) to avoid triggering path confinement checks
        executor.execute(
            tool, cmd, {"input": "-"}, artifact,
            WorkspaceManager(tmp_path, disk_limit_bytes=10_000_000),
        )

        args = mock_run.call_args[0][0]
        # The routed artifact path must be present
        assert str(artifact.path) in args
        # The params "input" value must NOT appear anywhere in args
        assert "-" not in args
        # With positional routing, --input should NOT appear at all
        assert "--input" not in args

    @patch("ecospheric_harness.executor.subprocess.run")
    def test_input_serialized_when_no_artifact(self, mock_run: MagicMock, tmp_path: Path) -> None:
        """When no input_artifact is provided, params['input'] IS serialized
        normally — the model's own input value is used."""
        mock_run.return_value = _mock_completed(_success_envelope())

        tool = _make_tool("ese")
        cmd = _make_command("clip", [_make_param("--input", "string")])

        executor = ToolExecutor()
        # Use a value that won't trigger path confinement heuristic
        executor.execute(
            tool, cmd, {"input": "stdin"}, None,
            WorkspaceManager(tmp_path, disk_limit_bytes=10_000_000),
        )

        args = mock_run.call_args[0][0]
        assert "--input" in args
        iidx = args.index("--input")
        assert args[iidx + 1] == "stdin"


# ---------------------------------------------------------------------------
# --json appended as last arg
# ---------------------------------------------------------------------------


class TestJsonFlag:
    """--json is always appended as the last argument."""

    @patch("ecospheric_harness.executor.subprocess.run")
    def test_json_last(self, mock_run: MagicMock, tmp_path: Path) -> None:
        mock_run.return_value = _mock_completed(_success_envelope())

        tool = _make_tool()
        cmd = _make_command("clip", [_make_param("--input", "string", True)])
        artifact = _make_artifact(tmp_path / "in.tif")

        executor = ToolExecutor()
        executor.execute(tool, cmd, {"threshold": 10}, artifact, WorkspaceManager(tmp_path, disk_limit_bytes=10_000_000))

        args = mock_run.call_args[0][0]
        assert args[-1] == "--json"


# ---------------------------------------------------------------------------
# Envelope handling
# ---------------------------------------------------------------------------


class TestEnvelopeHandling:
    """Envelope parsing and error construction."""

    @patch("ecospheric_harness.executor.subprocess.run")
    def test_success_envelope_parsed(self, mock_run: MagicMock, tmp_path: Path) -> None:
        """Mock subprocess returns valid JSON → parsed correctly."""
        envelope = _success_envelope("clip")
        mock_run.return_value = _mock_completed(envelope, returncode=0)

        tool = _make_tool()
        cmd = _make_command("clip", [_make_param("--input", "string", True)])
        artifact = _make_artifact(tmp_path / "in.tif")

        executor = ToolExecutor()
        result = executor.execute(tool, cmd, {}, artifact, WorkspaceManager(tmp_path, disk_limit_bytes=10_000_000))

        assert isinstance(result, ExecuteResult)
        assert result.envelope["status"] == "success"
        assert result.returncode == 0
        assert result.output_path.parent.parent == tmp_path

    @patch("ecospheric_harness.executor.subprocess.run")
    def test_invalid_json_error_envelope(self, mock_run: MagicMock, tmp_path: Path) -> None:
        """Mock subprocess returns garbage → error envelope constructed."""
        proc = MagicMock()
        proc.stdout = b"NOT VALID JSON"
        proc.returncode = 1
        mock_run.return_value = proc

        tool = _make_tool()
        cmd = _make_command("clip", [_make_param("--input", "string", True)])
        artifact = _make_artifact(tmp_path / "in.tif")

        executor = ToolExecutor()
        result = executor.execute(tool, cmd, {}, artifact, WorkspaceManager(tmp_path, disk_limit_bytes=10_000_000))

        assert result.envelope["status"] == "error"
        assert result.envelope["error"]["type"] == "internal_error"
        assert result.envelope["error"]["retryable"] is False
        assert result.returncode == 1

    @patch("ecospheric_harness.executor.subprocess.run")
    def test_timeout_error_envelope(self, mock_run: MagicMock, tmp_path: Path) -> None:
        """AC43: subprocess.TimeoutExpired → error envelope with type='timeout'."""
        mock_run.side_effect = subprocess.TimeoutExpired(cmd="ese", timeout=300)

        tool = _make_tool()
        cmd = _make_command("clip", [_make_param("--input", "string", True)])
        artifact = _make_artifact(tmp_path / "in.tif")

        executor = ToolExecutor()
        result = executor.execute(tool, cmd, {}, artifact, WorkspaceManager(tmp_path, disk_limit_bytes=10_000_000))

        assert result.envelope["status"] == "error"
        assert result.envelope["error"]["type"] == "timeout"
        assert result.envelope["error"]["retryable"] is False
        assert result.returncode == -1

    @patch("ecospheric_harness.executor.subprocess.run")
    def test_successful_execution_returns_correct_fields(
        self, mock_run: MagicMock, tmp_path: Path
    ) -> None:
        """Successful execution → ExecuteResult with correct fields."""
        envelope = _success_envelope("clip")
        mock_run.return_value = _mock_completed(envelope, returncode=0)

        tool = _make_tool("ese")
        cmd = _make_command("clip", [_make_param("--input", "string", True)])
        artifact = _make_artifact(tmp_path / "input.tif")

        executor = ToolExecutor(subprocess_timeout=600)
        result = executor.execute(
            tool, cmd, {"bbox": ["-121", "38", "-120", "39"]}, artifact, WorkspaceManager(tmp_path, disk_limit_bytes=10_000_000)
        )

        assert result.envelope == envelope
        assert result.returncode == 0
        assert result.output_path.name.startswith("step_")
        assert result.output_path.name.endswith(".tif")
        assert result.output_path.parent.parent == tmp_path


# ---------------------------------------------------------------------------
# AC35: error.retryable and error.type read from envelope
# ---------------------------------------------------------------------------


class TestErrorEnvelopeFields:
    """AC35: Executor constructs envelopes with error.type and error.retryable."""

    @patch("ecospheric_harness.executor.subprocess.run")
    def test_error_envelope_has_retryable_and_type(
        self, mock_run: MagicMock, tmp_path: Path
    ) -> None:
        """Error envelopes expose error.type and error.retryable for harness consumption."""
        mock_run.return_value = _mock_completed(
            {
                "status": "error",
                "error": {
                    "type": "validation_error",
                    "message": "bad param",
                    "retryable": True,
                },
            },
            returncode=2,
        )

        tool = _make_tool()
        cmd = _make_command("clip")

        executor = ToolExecutor()
        result = executor.execute(tool, cmd, {}, None, WorkspaceManager(tmp_path, disk_limit_bytes=10_000_000))

        assert result.envelope["error"]["type"] == "validation_error"
        assert result.envelope["error"]["retryable"] is True
