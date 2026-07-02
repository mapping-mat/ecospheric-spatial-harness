"""CLI entry point and public Python API for the Ecospheric Agent Harness.

Implements the ``Harness`` class (public API) and ``python -m ecospheric_harness``
CLI with ``--list-tools``, ``--list-intents``, ``--dry-run``, and prompt execution.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, cast

from ecospheric_harness.artifact import ArtifactManager
from ecospheric_harness.config import HarnessConfig
from ecospheric_harness.corrections import CorrectionHandler
from ecospheric_harness.executor import ToolExecutor
from ecospheric_harness.intents import (
    CorrectionResult,
    IntentEntry,
    IntentOption,
    RegisteredTool,
)
from ecospheric_harness.menu import available_intents
from ecospheric_harness.orchestrator import Orchestrator
from ecospheric_harness.preflight import PreflightChecker
from ecospheric_harness.registry import ToolRegistry
from ecospheric_harness.resolver import IntentResolver
from ecospheric_harness.result import PipelineResult
from ecospheric_harness.security import SubprocessHardener, SubprocessLimits
from ecospheric_harness.validator import SchemaValidator
from ecospheric_harness.workspace import WorkspaceManager


# ---------------------------------------------------------------------------
# Harness — public Python API
# ---------------------------------------------------------------------------


class Harness:
    """Public Python API for the Ecospheric Agent Harness.

    Example::

        h = Harness(tools=["edd", "ese"])
        result = h.run("Download Sentinel-2 scene S2B_MSIL2A and clip to this region")
    """

    def __init__(
        self,
        tools: list[str] | None = None,
        subprocess_timeout: int = 300,
        disk_limit_gb: float = 2.0,
        search_cap: int = 20,
        max_turns: int = 20,
        model: str = "z-ai/glm-5.2",
        workspace_root: str | Path | None = None,
        session_id: str | None = None,
        max_output_mb: int = 100,
        rlimit_as_mb: int | None = None,
        rlimit_nproc: int | None = None,
        gdal_cachemax_mb: int = 256,
    ) -> None:
        tool_names = tools if tools is not None else ["edd", "ese"]

        self._config = HarnessConfig(
            model=model,
            tools=tool_names,
            subprocess_timeout=subprocess_timeout,
            disk_limit_gb=disk_limit_gb,
            search_cap=search_cap,
            max_turns=max_turns,
            workspace_root=Path(workspace_root) if workspace_root is not None else Path.home() / ".esp" / "sessions",
            session_id=session_id,
            subprocess_max_output_mb=max_output_mb,
            rlimit_as_mb=rlimit_as_mb,
            rlimit_nproc=rlimit_nproc,
            gdal_cachemax_mb=gdal_cachemax_mb,
        )

        # Create WorkspaceManager
        disk_limit_bytes = int(disk_limit_gb * 1024 * 1024 * 1024)
        self._workspace = WorkspaceManager(
            self._config.workspace_root,
            disk_limit_bytes=disk_limit_bytes,
            session_id=self._config.session_id,
        )

        # Create SubprocessHardener from config
        limits = SubprocessLimits(
            wall_clock_timeout=subprocess_timeout,
            max_output_bytes=max_output_mb * 1024 * 1024,
            rlimit_as=rlimit_as_mb * 1024 * 1024 if rlimit_as_mb is not None else None,
            rlimit_nproc=rlimit_nproc,
            gdal_cachemax=str(gdal_cachemax_mb),
        )
        hardener = SubprocessHardener(limits)

        # Discover tools
        self._discovered_tools: list[RegisteredTool] = (
            ToolRegistry.discover_tools(tool_names)
        )

        # Discover sources (per tool)
        sources: dict[str, list[str]] = {}
        for t in self._discovered_tools:
            try:
                src_list = ToolRegistry.discover_sources(t)
                if src_list:
                    sources[t.name] = src_list
            except Exception as e:
                import warnings
                warnings.warn(
                    f"Failed to discover sources for tool '{t.name}': {e}. "
                    f"Search intents may be limited.",
                    stacklevel=2,
                )

        # Build catalog (cast to list[IntentEntry] for mypy list invariance)
        self._catalog: list[IntentEntry] = cast(
            "list[IntentEntry]",
            ToolRegistry.build_catalog(self._discovered_tools, sources),
        )

        # Build supporting objects
        self._resolver = IntentResolver(self._catalog)
        self._validator = SchemaValidator()
        self._executor = ToolExecutor(hardener=hardener)
        self._artifacts = ArtifactManager(
            workspace=self._workspace,
            disk_limit_bytes=disk_limit_bytes,
        )
        self._preflight = PreflightChecker(
            artifacts=self._artifacts,
            workspace=self._workspace,
        )
        self._corrections = CorrectionHandler(
            artifacts=self._artifacts,
            steps=[],  # shared mutable list — orchestrator appends to it
            executor=self._executor,
            resolver=self._resolver,
            workspace=self._workspace,
            preflight=self._preflight,
        )
        # Orchestrator shares the corrections' step list
        self._orchestrator = Orchestrator(
            config=self._config,
            registry=ToolRegistry(),
            resolver=self._resolver,
            validator=self._validator,
            executor=self._executor,
            artifacts=self._artifacts,
            preflight=self._preflight,
            corrections=self._corrections,
            catalog=self._catalog,
            workspace=self._workspace,
        )

    # -- public methods ----------------------------------------------------

    def run(self, prompt: str) -> PipelineResult:
        """Run the orchestration pipeline with a natural-language prompt."""
        return self._orchestrator.run(prompt)

    def undo(self) -> CorrectionResult:
        """Undo the last successful step."""
        return self._corrections.undo()

    def redo(self, params: dict[str, Any]) -> CorrectionResult:
        """Redo the last step with new parameters."""
        return self._corrections.redo(params)

    # -- properties --------------------------------------------------------

    @property
    def tools(self) -> list[RegisteredTool]:
        """Return the discovered tools."""
        return self._discovered_tools

    @property
    def intents(self) -> list[IntentOption]:
        """Return the available intent options for the current state."""
        return available_intents(self._catalog, self._artifacts.current, self._resolver)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _build_parser() -> argparse.ArgumentParser:
    """Build the argument parser for the CLI."""
    parser = argparse.ArgumentParser(
        prog="ecospheric-harness",
        description="Ecospheric Agent Harness — geospatial pipeline orchestrator",
    )
    parser.add_argument(
        "prompt",
        nargs="?",
        default=None,
        help="Natural-language request for the pipeline",
    )
    parser.add_argument(
        "--model",
        default="z-ai/glm-5.2",
        help="Model identifier (default: z-ai/glm-5.2)",
    )
    parser.add_argument(
        "--list-tools",
        action="store_true",
        help="List discovered tools as JSON and exit",
    )
    parser.add_argument(
        "--list-intents",
        action="store_true",
        help="List available intents as JSON and exit",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show resolved tool calls without executing",
    )
    parser.add_argument(
        "--max-turns",
        type=int,
        default=20,
        help="Maximum orchestration turns (default: 20)",
    )
    parser.add_argument(
        "--subprocess-timeout",
        type=int,
        default=300,
        help="Subprocess timeout in seconds (default: 300)",
    )
    parser.add_argument(
        "--disk-limit-gb",
        type=float,
        default=2.0,
        help="Disk usage limit in GB (default: 2.0)",
    )
    parser.add_argument(
        "--search-cap",
        type=int,
        default=20,
        help="Max search result items (default: 20)",
    )
    parser.add_argument(
        "--workspace",
        default=None,
        help="Workspace root directory (default: ~/.esp/sessions)",
    )
    parser.add_argument(
        "--session-id",
        default=None,
        help="Session identifier for resuming or naming a session",
    )
    parser.add_argument(
        "--max-output-mb",
        type=int,
        default=100,
        help="Max subprocess output in MB (default: 100)",
    )
    parser.add_argument(
        "--rlimit-as-mb",
        type=int,
        default=None,
        help="Address space RLIMIT_AS in MB (default: no limit)",
    )
    parser.add_argument(
        "--rlimit-nproc",
        type=int,
        default=None,
        help="Max processes RLIMIT_NPROC (default: no limit)",
    )
    parser.add_argument(
        "--gdal-cachemax",
        type=int,
        default=256,
        help="GDAL_CACHEMAX in MB (default: 256)",
    )
    return parser


def _list_tools_json(harness: Harness) -> None:
    """Output --list-tools JSON."""
    items = [
        {
            "name": t.name,
            "version": t.version,
            "binary": t.binary,
            "command_count": len(t.commands),
        }
        for t in harness.tools
    ]
    print(json.dumps(items, indent=2))


def _list_intents_json(harness: Harness) -> None:
    """Output --list-intents JSON."""
    items = [
        {
            "intent": i.intent,
            "description": i.description,
            "tool": i.tool,
            "command": i.command,
            "required_params": i.required_params,
            "data_type": i.data_type,
        }
        for i in harness.intents
    ]
    print(json.dumps(items, indent=2))


def _dry_run(harness: Harness, prompt: str) -> None:
    """Show resolved calls without executing (AC26)."""
    from ecospheric_harness.intents import parse_intent

    # Parse the prompt as an intent (best-effort — prompt may be natural language)
    try:
        raw: dict[str, Any] = json.loads(prompt)
        parsed = parse_intent(raw)
    except (json.JSONDecodeError, ValueError):
        # Natural-language prompt — show available intents as preview
        print(json.dumps({
            "mode": "dry-run",
            "prompt": prompt,
            "available_intents": [
                {"intent": i.intent, "description": i.description}
                for i in harness.intents
            ],
            "note": "Prompt is natural language — pass JSON intent for resolution preview.",
        }, indent=2))
        return

    # If we got a structured intent, try to resolve it
    from ecospheric_harness.intents import OperationIntent

    if isinstance(parsed, OperationIntent):
        resolved = harness._resolver.resolve(
            parsed.intent, parsed.params, harness._artifacts.current,
        )
        from ecospheric_harness.intents import ResolutionError

        if isinstance(resolved, ResolutionError):
            print(json.dumps({
                "mode": "dry-run",
                "intent": parsed.intent,
                "error": resolved.message,
            }, indent=2))
            return

        # Validate
        validation = harness._validator.validate(resolved)
        # Build planned argv using the shared serialize_params function
        from ecospheric_harness.executor import serialize_params

        argv = [resolved.tool.binary]
        argv.extend(resolved.command.name.split())
        # Strip _input_target from params before serialization
        serializable_params = {k: v for k, v in resolved.params.items() if k != "_input_target"}
        argv.extend(serialize_params(serializable_params, resolved.command))
        argv.append("--json")

        print(json.dumps({
            "mode": "dry-run",
            "intent": parsed.intent,
            "tool": resolved.tool.name,
            "command": resolved.command.name,
            "params": resolved.params,
            "validation": {"ok": validation.ok, "errors": validation.errors},
            "planned_argv": argv,
        }, indent=2))
    else:
        print(json.dumps({
            "mode": "dry-run",
            "intent_type": type(parsed).__name__,
            "note": "Non-operation intent — nothing to resolve.",
        }, indent=2))


def main(argv: list[str] | None = None) -> int:
    """CLI entry point."""
    parser = _build_parser()
    args = parser.parse_args(argv)

    # --list-tools and --list-intents don't need a prompt or API key
    if args.list_tools:
        harness = Harness(
            tools=["edd", "ese"],
            model=args.model,
            subprocess_timeout=args.subprocess_timeout,
            disk_limit_gb=args.disk_limit_gb,
            search_cap=args.search_cap,
            max_turns=args.max_turns,
            workspace_root=args.workspace,
            session_id=args.session_id,
            max_output_mb=args.max_output_mb,
            rlimit_as_mb=args.rlimit_as_mb,
            rlimit_nproc=args.rlimit_nproc,
            gdal_cachemax_mb=args.gdal_cachemax,
        )
        _list_tools_json(harness)
        return 0

    if args.list_intents:
        harness = Harness(
            tools=["edd", "ese"],
            model=args.model,
            subprocess_timeout=args.subprocess_timeout,
            disk_limit_gb=args.disk_limit_gb,
            search_cap=args.search_cap,
            max_turns=args.max_turns,
            workspace_root=args.workspace,
            session_id=args.session_id,
            max_output_mb=args.max_output_mb,
            rlimit_as_mb=args.rlimit_as_mb,
            rlimit_nproc=args.rlimit_nproc,
            gdal_cachemax_mb=args.gdal_cachemax,
        )
        _list_intents_json(harness)
        return 0

    # --dry-run
    if args.dry_run:
        if args.prompt is None:
            parser.error("--dry-run requires a prompt")
        harness = Harness(
            tools=["edd", "ese"],
            model=args.model,
            subprocess_timeout=args.subprocess_timeout,
            disk_limit_gb=args.disk_limit_gb,
            search_cap=args.search_cap,
            max_turns=args.max_turns,
            workspace_root=args.workspace,
            session_id=args.session_id,
            max_output_mb=args.max_output_mb,
            rlimit_as_mb=args.rlimit_as_mb,
            rlimit_nproc=args.rlimit_nproc,
            gdal_cachemax_mb=args.gdal_cachemax,
        )
        _dry_run(harness, args.prompt)
        return 0

    # Normal run requires a prompt
    if args.prompt is None:
        parser.error("prompt is required (unless using --list-tools or --list-intents)")

    # Check for API key
    import os

    if not os.environ.get("OPENROUTER_API_KEY"):
        print(
            "Error: OPENROUTER_API_KEY environment variable is not set.",
            file=sys.stderr,
        )
        return 1

    harness = Harness(
        tools=["edd", "ese"],
        model=args.model,
        subprocess_timeout=args.subprocess_timeout,
        disk_limit_gb=args.disk_limit_gb,
        search_cap=args.search_cap,
        max_turns=args.max_turns,
        workspace_root=args.workspace,
        session_id=args.session_id,
        max_output_mb=args.max_output_mb,
        rlimit_as_mb=args.rlimit_as_mb,
        rlimit_nproc=args.rlimit_nproc,
        gdal_cachemax_mb=args.gdal_cachemax,
    )
    result = harness.run(args.prompt)
    print(result.summary())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
