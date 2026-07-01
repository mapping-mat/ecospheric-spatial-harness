"""Tool discovery, alias resolution, and intent-catalog construction.

Implements the ETP registry that discovers tool capabilities via subprocess,
resolves human-readable intent aliases from multi-word command names, and
builds the intent catalog consumed by the harness resolver.
"""

from __future__ import annotations

import json
import os
import subprocess
from dataclasses import dataclass
from typing import Any

from etp.describe import CommandDescriptor, ParameterDescriptor

from ecospheric_harness.intents import (
    IntentEntry,
    RegisteredTool,
)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

INTENT_OVERRIDES: dict[str, str] = {
    "proj transform": "reproject",
    "proj distance": "geodesic_distance",
}

PARAM_DENYLIST: frozenset[str] = frozenset({
    "--json",
    "--quiet",
    "--no-cache",
    "--timeout",
    "--log-level",
    "--output",
    "--format",
})

_EXCLUDED_CATEGORIES: frozenset[str] = frozenset({
    "diagnostic",
    "info",
    "pipe",
})


# ---------------------------------------------------------------------------
# CatalogIntentEntry — extends IntentEntry with optional source tag
# ---------------------------------------------------------------------------


@dataclass
class CatalogIntentEntry(IntentEntry):
    """An :class:`IntentEntry` augmented with an optional data-source tag.

    The source field is set for EDD search commands that have been paired
    with a specific data-source prefix (e.g. ``@osm``, ``@stac``).
    """

    source: str | None = None


# ---------------------------------------------------------------------------
# Descriptor reconstruction
# ---------------------------------------------------------------------------


def _reconstruct_parameter(data: dict[str, Any]) -> ParameterDescriptor:
    """Rebuild a :class:`ParameterDescriptor` from a JSON dict."""
    return ParameterDescriptor(
        name=data["name"],
        description=data.get("description", ""),
        type=data.get("type", "string"),
        required=data.get("required", False),
        default=data.get("default"),
        pattern=data.get("pattern"),
    )


def _reconstruct_descriptor(data: dict[str, Any]) -> CommandDescriptor:
    """Rebuild a :class:`CommandDescriptor` from a JSON dict.

    Handles the shape produced by ``<binary> describe --all`` output,
    where each command dict contains ``name``, ``description``,
    ``category``, ``parameters``, etc.
    """
    params = [_reconstruct_parameter(p) for p in data.get("parameters", [])]
    return CommandDescriptor(
        name=data["name"],
        description=data.get("description", ""),
        category=data.get("category", ""),
        parameters=params,
        input_formats=data.get("input_formats", []),
        output_formats=data.get("output_formats", []),
        data_type=data.get("data_type", "any"),
        requires_planar_crs=data.get("requires_planar_crs", False),
        backends=data.get("backends", []),
    )


# ---------------------------------------------------------------------------
# Alias resolution
# ---------------------------------------------------------------------------


def _resolve_intent_name(command_name: str) -> str | None:
    """Resolve a command name to a short intent alias.

    Returns ``None`` for excluded (diagnostic/info/pipe) categories — but
    this function does not know the category.  Category filtering happens
    in :meth:`ToolRegistry.build_catalog`.

    Rules (applied in order):
      1. Check ``INTENT_OVERRIDES`` keyed on the full command name.
      2. Single-word command → intent = command name unchanged.
      3. Multi-word → drop first token (category), join remainder with ``_``,
         replacing ``-`` with ``_``.
    """
    if command_name in INTENT_OVERRIDES:
        return INTENT_OVERRIDES[command_name]

    parts = command_name.split()
    if len(parts) == 1:
        return command_name

    # Multi-word: drop category prefix, join rest with underscores
    rest = parts[1:]
    return "_".join(rest).replace("-", "_")


# ---------------------------------------------------------------------------
# ToolRegistry
# ---------------------------------------------------------------------------


class ToolRegistry:
    """Discovers ETP tools and builds an intent catalog."""

    # -- discovery ---------------------------------------------------------

    @staticmethod
    def _resolve_binary(tool_name: str) -> str:
        """Resolve tool binary path from env vars or fall back to name."""
        env_map = {
            "edd": "EDD_BIN",
            "ese": "ESE_BIN",
        }
        env_key = env_map.get(tool_name)
        if env_key:
            env_val = os.environ.get(env_key)
            if env_val:
                return env_val
        return tool_name

    @staticmethod
    def _run_describe(binary: str) -> dict[str, Any]:
        """Run ``<binary> describe --all`` and parse the JSON envelope."""
        result = subprocess.run(
            [binary, "describe", "--all"],
            capture_output=True,
            text=True,
            check=True,
        )
        envelope: dict[str, Any] = json.loads(result.stdout)
        return envelope

    @classmethod
    def discover_tools(cls, tool_names: list[str]) -> list[RegisteredTool]:
        """Discover capabilities for each named tool.

        Runs ``<binary> describe --all`` for each tool, reconstructs
        :class:`CommandDescriptor` objects from the JSON output, and
        returns a list of :class:`RegisteredTool` instances.
        """
        tools: list[RegisteredTool] = []
        for name in tool_names:
            binary = cls._resolve_binary(name)
            envelope = cls._run_describe(binary)
            data = envelope.get("data", {})
            version = envelope.get("tool_version", "0.0.0")
            cmd_dicts = data.get("commands", [])
            commands = [_reconstruct_descriptor(c) for c in cmd_dicts]
            tools.append(
                RegisteredTool(
                    name=name,
                    version=version,
                    binary=binary,
                    commands=commands,
                )
            )
        return tools

    @staticmethod
    def discover_sources(tool: RegisteredTool) -> list[str]:
        """Discover data-source prefixes for an EDD-type tool.

        Runs ``<binary> plugins --json`` and extracts the ``prefix`` field
        from each plugin entry.
        """
        result = subprocess.run(
            [tool.binary, "plugins", "--json"],
            capture_output=True,
            text=True,
            check=True,
        )
        envelope = json.loads(result.stdout)
        plugins = envelope.get("data", {}).get("plugins", [])
        return [p["prefix"] for p in plugins]

    # -- catalog construction ----------------------------------------------

    @staticmethod
    def _compute_required_params(command: CommandDescriptor) -> list[str]:
        """Return required param names not in the denylist."""
        return [
            p.name
            for p in command.parameters
            if p.required and p.name not in PARAM_DENYLIST
        ]

    @classmethod
    def build_catalog(
        cls,
        tools: list[RegisteredTool],
        sources: dict[str, list[str]],
    ) -> list[CatalogIntentEntry]:
        """Build the full intent catalog from discovered tools.

        Applies alias resolution, diagnostic exclusion, EDD source pairing,
        and collision handling (multiple commands may map to the same intent).

        Parameters
        ----------
        tools:
            Discovered tools with their command descriptors.
        sources:
            Mapping of tool name → list of source prefixes (e.g.
            ``{"edd": ["@osm", "@stac", ...]}``).
        """
        catalog: list[CatalogIntentEntry] = []

        for tool in tools:
            # Build a lookup of source prefix → full prefix string
            # e.g. {"@osm": "@osm", "@stac": "@stac", ...}
            tool_sources = sources.get(tool.name, [])
            source_lookup: dict[str, str] = {
                p.lstrip("@"): p for p in tool_sources
            }

            for cmd in tool.commands:
                # Detect search commands with source prefix
                # Real shape: "@osm search", "@stac search", etc.
                if cmd.name.startswith("@") and " " in cmd.name:
                    prefix_raw, op = cmd.name.split(" ", 1)
                    prefix_name = prefix_raw.lstrip("@")

                    if op == "search":
                        # Use source_lookup for full prefix string if available,
                        # otherwise derive directly from the command name.
                        source_prefix = source_lookup.get(prefix_name, f"@{prefix_name}")
                        search_intent = f"search_{prefix_name}"
                        req_params = cls._compute_required_params(cmd)
                        catalog.append(
                            CatalogIntentEntry(
                                intent=search_intent,
                                description=cmd.description,
                                tool=tool,
                                command=cmd,
                                required_params=req_params,
                                source=source_prefix,
                            )
                        )
                        continue

                # Non-search or non-prefixed commands
                catalog_entry = cls._build_non_search_entry(tool, cmd)
                if catalog_entry is not None:
                    catalog.append(catalog_entry)

        return catalog

    @classmethod
    def _build_non_search_entry(
        cls,
        tool: RegisteredTool,
        cmd: CommandDescriptor,
    ) -> CatalogIntentEntry | None:
        """Build a catalog entry for a non-search command.

        Returns ``None`` for excluded (diagnostic/info/pipe) categories
        or commands that cannot be resolved to an intent name.
        """
        # Diagnostic exclusion (AC36)
        if cmd.category in _EXCLUDED_CATEGORIES:
            return None

        resolved = _resolve_intent_name(cmd.name)
        if resolved is None:
            return None

        req_params = cls._compute_required_params(cmd)
        return CatalogIntentEntry(
            intent=resolved,
            description=cmd.description,
            tool=tool,
            command=cmd,
            required_params=req_params,
        )
