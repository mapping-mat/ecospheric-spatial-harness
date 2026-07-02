"""Tests for ecospheric_harness.__main__ — CLI entry point and public API.

Covers AC22–AC26: CLI with prompt, Python API run/undo/redo, --list-tools,
--list-intents, --dry-run, --model override, and missing API key handling.
"""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from etp.describe import CommandDescriptor, ParameterDescriptor


# ---------------------------------------------------------------------------
# Helpers: mock tool data
# ---------------------------------------------------------------------------

def _make_command(
    name: str = "raster clip",
    description: str = "Clip a raster to a bounding box",
    category: str = "raster",
    input_formats: list[str] | None = None,
    data_type: str = "raster",
    parameters: list[ParameterDescriptor] | None = None,
) -> CommandDescriptor:
    return CommandDescriptor(
        name=name,
        description=description,
        category=category,
        parameters=parameters or [],
        input_formats=input_formats if input_formats is not None else ["geotiff"],
        output_formats=["geotiff"],
        data_type=data_type,
    )


def _make_edd_command() -> CommandDescriptor:
    return CommandDescriptor(
        name="search",
        description="Search for datasets",
        category="discovery",
        parameters=[],
        input_formats=[],
        output_formats=["json"],
        data_type="metadata",
    )


def _make_registered_tool(
    name: str = "edd",
    version: str = "1.0.0",
    binary: str = "edd",
    commands: list[CommandDescriptor] | None = None,
) -> MagicMock:
    """Create a mock RegisteredTool."""
    tool = MagicMock()
    tool.name = name
    tool.version = version
    tool.binary = binary
    tool.commands = commands or [_make_edd_command()]
    return tool


def _mock_discover_tools(tool_names: list[str]) -> list[MagicMock]:
    """Return mock tools matching the requested names."""
    tools = []
    for name in tool_names:
        if name == "edd":
            tools.append(_make_registered_tool(
                name="edd",
                binary="edd",
                commands=[_make_edd_command()],
            ))
        elif name == "ese":
            tools.append(_make_registered_tool(
                name="ese",
                binary="ese",
                commands=[_make_command()],
            ))
        else:
            tools.append(_make_registered_tool(name=name, binary=name))
    return tools


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def mock_registry() -> Any:
    """Patch ToolRegistry methods to avoid real subprocess calls."""
    with (
        patch(
            "ecospheric_harness.registry.ToolRegistry.discover_tools",
            side_effect=_mock_discover_tools,
        ) as mock_dt,
        patch(
            "ecospheric_harness.registry.ToolRegistry.discover_sources",
            return_value=[],
        ) as mock_ds,
        patch(
            "ecospheric_harness.registry.ToolRegistry.build_catalog",
            return_value=[],
        ) as mock_bc,
    ):
        yield {
            "discover_tools": mock_dt,
            "discover_sources": mock_ds,
            "build_catalog": mock_bc,
        }


# ---------------------------------------------------------------------------
# AC24: --list-tools
# ---------------------------------------------------------------------------


class TestListTools:
    """--list-tools outputs JSON with correct shape."""

    def test_list_tools_json_shape(
        self, mock_registry: Any, capsys: pytest.CaptureFixture[str],
    ) -> None:
        from ecospheric_harness.__main__ import main

        exit_code = main(["--list-tools"])
        assert exit_code == 0

        captured = capsys.readouterr()
        data = json.loads(captured.out)
        assert isinstance(data, list)
        assert len(data) == 2  # edd + ese

        for item in data:
            assert set(item.keys()) == {"name", "version", "binary", "command_count"}
            assert isinstance(item["name"], str)
            assert isinstance(item["version"], str)
            assert isinstance(item["binary"], str)
            assert isinstance(item["command_count"], int)

    def test_list_tools_no_api_key_required(
        self, mock_registry: Any, capsys: pytest.CaptureFixture[str],
    ) -> None:
        """--list-tools should work even without OPENROUTER_API_KEY."""
        from ecospheric_harness.__main__ import main

        with patch.dict("os.environ", {}, clear=False):
            # Remove the key if present
            import os
            os.environ.pop("OPENROUTER_API_KEY", None)
            exit_code = main(["--list-tools"])
            assert exit_code == 0


# ---------------------------------------------------------------------------
# AC25: --list-intents
# ---------------------------------------------------------------------------


class TestListIntents:
    """--list-intents outputs JSON with deduplicated intents."""

    def test_list_intents_json_shape(
        self, mock_registry: Any, capsys: pytest.CaptureFixture[str],
    ) -> None:
        from ecospheric_harness.__main__ import main

        exit_code = main(["--list-intents"])
        assert exit_code == 0

        captured = capsys.readouterr()
        data = json.loads(captured.out)
        assert isinstance(data, list)

        for item in data:
            assert "intent" in item
            assert "description" in item
            assert "required_params" in item
            assert isinstance(item["required_params"], list)

    def test_list_intents_no_api_key_required(
        self, mock_registry: Any, capsys: pytest.CaptureFixture[str],
    ) -> None:
        from ecospheric_harness.__main__ import main

        import os
        os.environ.pop("OPENROUTER_API_KEY", None)
        exit_code = main(["--list-intents"])
        assert exit_code == 0


# ---------------------------------------------------------------------------
# AC26: --dry-run
# ---------------------------------------------------------------------------


class TestDryRun:
    """--dry-run shows resolved calls without execution."""

    def test_dry_run_natural_language(
        self, mock_registry: Any, capsys: pytest.CaptureFixture[str],
    ) -> None:
        from ecospheric_harness.__main__ import main

        exit_code = main(["--dry-run", "Clip a raster to this bounding box"])
        assert exit_code == 0

        captured = capsys.readouterr()
        data = json.loads(captured.out)
        assert data["mode"] == "dry-run"
        assert "prompt" in data
        assert "available_intents" in data

    def test_dry_run_json_intent(
        self, mock_registry: Any, capsys: pytest.CaptureFixture[str],
    ) -> None:
        from ecospheric_harness.__main__ import main

        # Build a real-ish catalog entry so resolver can find "clip"
        clip_cmd = _make_command(
            name="raster clip",
            description="Clip raster",
            category="raster",
            input_formats=["geotiff"],
            data_type="raster",
        )
        edd_tool = _make_registered_tool(name="edd", binary="edd", commands=[clip_cmd])

        from ecospheric_harness.registry import CatalogIntentEntry

        clip_entry = CatalogIntentEntry(
            intent="clip",
            description="Clip raster",
            tool=edd_tool,
            command=clip_cmd,
            required_params=[],
        )
        mock_registry["build_catalog"].return_value = [clip_entry]

        # Mock the resolver to return a ResolvedCall for the "clip" intent
        from ecospheric_harness.intents import ResolvedCall

        resolved = ResolvedCall(tool=edd_tool, command=clip_cmd, params={"bbox": [-122, 37, -121, 38]})

        with patch(
            "ecospheric_harness.resolver.IntentResolver.resolve",
            return_value=resolved,
        ):
            intent_json = json.dumps({"intent": "clip", "params": {"bbox": [-122, 37, -121, 38]}})
            exit_code = main(["--dry-run", intent_json])
            assert exit_code == 0

        captured = capsys.readouterr()
        data = json.loads(captured.out)
        assert data["mode"] == "dry-run"
        # Should have tool, command, params, validation, planned_argv
        assert "validation" in data
        assert "planned_argv" in data
        assert data["tool"] == "edd"
        assert data["command"] == "raster clip"

    def test_dry_run_no_execution(
        self, mock_registry: Any, capsys: pytest.CaptureFixture[str],
    ) -> None:
        """Ensure no subprocess is called during dry-run."""
        from ecospheric_harness.__main__ import main

        with patch("ecospheric_harness.executor.subprocess.run") as mock_run:
            main(["--dry-run", "do something"])
            mock_run.assert_not_called()


# ---------------------------------------------------------------------------
# AC23: Python API — Harness.run() returns PipelineResult
# ---------------------------------------------------------------------------


class TestPythonAPI:
    """Python API tests for Harness class."""

    def test_run_returns_pipeline_result(
        self, mock_registry: Any,
    ) -> None:
        from ecospheric_harness.__main__ import Harness
        from ecospheric_harness.result import PipelineResult

        mock_result = PipelineResult(steps=[], final_artifact=None, provenance_chain=[])

        with patch(
            "ecospheric_harness.orchestrator.Orchestrator.run",
            return_value=mock_result,
        ):
            h = Harness(tools=["edd", "ese"])
            result = h.run("do something")
            assert isinstance(result, PipelineResult)
            assert result.steps == []

    def test_undo_delegates(
        self, mock_registry: Any,
    ) -> None:
        from ecospheric_harness.__main__ import Harness
        from ecospheric_harness.intents import CorrectionResult

        mock_correction = CorrectionResult(status="undone", artifact=None, message="")

        with patch(
            "ecospheric_harness.corrections.CorrectionHandler.undo",
            return_value=mock_correction,
        ):
            h = Harness(tools=["edd", "ese"])
            result = h.undo()
            assert result.status == "undone"

    def test_redo_delegates(
        self, mock_registry: Any,
    ) -> None:
        from ecospheric_harness.__main__ import Harness
        from ecospheric_harness.intents import CorrectionResult

        mock_correction = CorrectionResult(status="redone", artifact=None, message="")

        with patch(
            "ecospheric_harness.corrections.CorrectionHandler.redo",
            return_value=mock_correction,
        ):
            h = Harness(tools=["edd", "ese"])
            result = h.redo({"param": "value"})
            assert result.status == "redone"

    def test_tools_property(
        self, mock_registry: Any,
    ) -> None:
        from ecospheric_harness.__main__ import Harness

        h = Harness(tools=["edd", "ese"])
        assert len(h.tools) == 2
        assert h.tools[0].name == "edd"
        assert h.tools[1].name == "ese"

    def test_intents_property(
        self, mock_registry: Any,
    ) -> None:
        from ecospheric_harness.__main__ import Harness

        h = Harness(tools=["edd", "ese"])
        # Intents come from available_intents; with empty catalog, should be empty
        intents = h.intents
        assert isinstance(intents, list)

    def test_construction_defaults(
        self, mock_registry: Any,
    ) -> None:
        from ecospheric_harness.__main__ import Harness

        h = Harness()
        assert len(h.tools) == 2  # default edd + ese
        assert h._config.model == "z-ai/glm-5.2"
        assert h._config.max_turns == 20
        assert h._config.subprocess_timeout == 300


# ---------------------------------------------------------------------------
# CLI: prompt passes through to run
# ---------------------------------------------------------------------------


class TestCLIPrompt:
    """CLI with prompt passes it to run."""

    def test_prompt_calls_run(
        self, mock_registry: Any, capsys: pytest.CaptureFixture[str],
    ) -> None:
        from ecospheric_harness.__main__ import main

        mock_result = MagicMock()
        mock_result.summary.return_value = "Pipeline: 0 step(s), 0 successful, 0 failed"

        import os
        os.environ["OPENROUTER_API_KEY"] = "test-key"

        with patch(
            "ecospheric_harness.orchestrator.Orchestrator.run",
            return_value=mock_result,
        ) as mock_run:
            exit_code = main(["hello world"])
            assert exit_code == 0
            mock_run.assert_called_once_with("hello world")

        captured = capsys.readouterr()
        assert "Pipeline" in captured.out

        os.environ.pop("OPENROUTER_API_KEY", None)


# ---------------------------------------------------------------------------
# --model override
# ---------------------------------------------------------------------------


class TestModelOverride:
    """--model passes model to config."""

    def test_model_override(
        self, mock_registry: Any, capsys: pytest.CaptureFixture[str],
    ) -> None:
        from ecospheric_harness.__main__ import main

        mock_result = MagicMock()
        mock_result.summary.return_value = "done"

        import os
        os.environ["OPENROUTER_API_KEY"] = "test-key"

        with patch(
            "ecospheric_harness.orchestrator.Orchestrator.run",
            return_value=mock_result,
        ):
            exit_code = main(["--model", "custom/model", "test prompt"])
            assert exit_code == 0

        os.environ.pop("OPENROUTER_API_KEY", None)

    def test_model_default(
        self, mock_registry: Any,
    ) -> None:
        from ecospheric_harness.__main__ import Harness

        h = Harness(tools=["edd"])
        assert h._config.model == "z-ai/glm-5.2"


# ---------------------------------------------------------------------------
# Missing OPENROUTER_API_KEY
# ---------------------------------------------------------------------------


class TestMissingAPIKey:
    """Missing OPENROUTER_API_KEY shows error (only for actual run)."""

    def test_missing_key_error(
        self, mock_registry: Any, capsys: pytest.CaptureFixture[str],
    ) -> None:
        from ecospheric_harness.__main__ import main

        import os
        os.environ.pop("OPENROUTER_API_KEY", None)

        exit_code = main(["some prompt"])
        assert exit_code == 1

        captured = capsys.readouterr()
        assert "OPENROUTER_API_KEY" in captured.err

    def test_list_tools_works_without_key(
        self, mock_registry: Any, capsys: pytest.CaptureFixture[str],
    ) -> None:
        from ecospheric_harness.__main__ import main

        import os
        os.environ.pop("OPENROUTER_API_KEY", None)

        exit_code = main(["--list-tools"])
        assert exit_code == 0
