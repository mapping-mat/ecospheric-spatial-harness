"""Tests for the intent resolver (T3.2).

All tests use mock IntentEntry / CommandDescriptor / RegisteredTool objects
— no real tool subprocesses are invoked.
"""

from __future__ import annotations

from pathlib import Path


from ecospheric_harness.artifact import Artifact
from ecospheric_harness.intents import (
    IntentEntry,
    RegisteredTool,
    ResolvedCall,
    ResolutionError,
)
from ecospheric_harness.resolver import IntentResolver

# We import CommandDescriptor from etp.describe (lightweight dataclass).
from etp.describe import CommandDescriptor


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_tool(name: str) -> RegisteredTool:
    """Create a minimal RegisteredTool with no commands."""
    return RegisteredTool(name=name, version="1.0.0", binary=name, commands=[])


def _make_command(
    *,
    name: str = "cmd",
    data_type: str = "any",
    input_formats: list[str] | None = None,
    description: str = "",
) -> CommandDescriptor:
    """Create a CommandDescriptor with the given overrides."""
    return CommandDescriptor(
        name=name,
        description=description,
        category="vector",
        parameters=[],
        input_formats=input_formats if input_formats is not None else [],
        output_formats=[],
        data_type=data_type,
    )


def _make_entry(
    intent: str,
    tool_name: str = "edd",
    *,
    data_type: str = "any",
    input_formats: list[str] | None = None,
    description: str = "",
) -> IntentEntry:
    """Create an IntentEntry with a single command."""
    return IntentEntry(
        intent=intent,
        description=description,
        tool=_make_tool(tool_name),
        command=_make_command(
            data_type=data_type,
            input_formats=input_formats,
            description=description,
        ),
        required_params=[],
    )


def _make_artifact(
    data_type: str = "raster",
    fmt: str = "geotiff",
) -> Artifact:
    """Create a minimal Artifact (path doesn't need to exist for resolver)."""
    return Artifact(
        path=Path("/tmp/dummy"),
        envelope={},
        format=fmt,
        data_type=data_type,
    )


# ---------------------------------------------------------------------------
# Single candidate
# ---------------------------------------------------------------------------


class TestSingleCandidate:
    """Intent matches exactly one catalog entry → ResolvedCall."""

    def test_single_match(self) -> None:
        entry = _make_entry("buffer", data_type="vector")
        resolver = IntentResolver([entry])
        result = resolver.resolve("buffer", {"distance": 10}, _make_artifact("vector"))
        assert isinstance(result, ResolvedCall)
        assert result.tool.name == "edd"
        assert result.command.data_type == "vector"
        assert result.params == {"distance": 10}


# ---------------------------------------------------------------------------
# Data_type disambiguation
# ---------------------------------------------------------------------------


class TestDataTypeDisambiguation:
    """Same intent, different data_type entries → artifact selects winner."""

    def test_clip_raster_selects_raster_entry(self) -> None:
        raster_clip = _make_entry("clip", data_type="raster", description="raster clip")
        vector_clip = _make_entry("clip", data_type="vector", description="vector clip")
        resolver = IntentResolver([raster_clip, vector_clip])

        result = resolver.resolve("clip", {}, _make_artifact("raster"))
        assert isinstance(result, ResolvedCall)
        assert result.command.data_type == "raster"

    def test_clip_vector_selects_vector_entry(self) -> None:
        raster_clip = _make_entry("clip", data_type="raster", description="raster clip")
        vector_clip = _make_entry("clip", data_type="vector", description="vector clip")
        resolver = IntentResolver([raster_clip, vector_clip])

        result = resolver.resolve("clip", {}, _make_artifact("vector"))
        assert isinstance(result, ResolvedCall)
        assert result.command.data_type == "vector"


# ---------------------------------------------------------------------------
# Fallback to "any" data_type
# ---------------------------------------------------------------------------


class TestFallbackToAny:
    """When no exact data_type match, fall back to data_type="any" + format."""

    def test_fetch_any_with_format_match(self) -> None:
        fetch_entry = _make_entry(
            "fetch",
            data_type="any",
            input_formats=["geotiff", "cog"],
        )
        resolver = IntentResolver([fetch_entry])
        artifact = _make_artifact(data_type="raster", fmt="geotiff")
        result = resolver.resolve(
            "fetch", {"item": "a", "asset": "b"}, artifact
        )
        assert isinstance(result, ResolvedCall)
        assert result.command.data_type == "any"

    def test_fetch_any_with_empty_input_formats(self) -> None:
        """input_formats=[] (empty) counts as accepting anything."""
        fetch_entry = _make_entry("fetch", data_type="any", input_formats=[])
        resolver = IntentResolver([fetch_entry])
        artifact = _make_artifact(data_type="raster", fmt="geotiff")
        result = resolver.resolve(
            "fetch", {"item": "a", "asset": "b"}, artifact
        )
        assert isinstance(result, ResolvedCall)


# ---------------------------------------------------------------------------
# No artifact — no input needed
# ---------------------------------------------------------------------------


class TestNoArtifactNoInput:
    """No artifact + command accepts no input → ResolvedCall."""

    def test_search_with_empty_input_formats(self) -> None:
        search = _make_entry("search", input_formats=[])
        resolver = IntentResolver([search])
        result = resolver.resolve("search", {"query": "rivers"}, None)
        assert isinstance(result, ResolvedCall)
        assert result.tool.name == "edd"

    def test_search_with_none_input_formats(self) -> None:
        search = _make_entry("search", input_formats=None)
        resolver = IntentResolver([search])
        result = resolver.resolve("search", {"query": "rivers"}, None)
        assert isinstance(result, ResolvedCall)


# ---------------------------------------------------------------------------
# No artifact — needs input
# ---------------------------------------------------------------------------


class TestNoArtifactNeedsInput:
    """No artifact + command requires input → ResolutionError."""

    def test_clip_without_artifact(self) -> None:
        clip = _make_entry("clip", data_type="raster", input_formats=["geotiff"])
        resolver = IntentResolver([clip])
        result = resolver.resolve("clip", {}, None)
        assert isinstance(result, ResolutionError)
        assert "requires input data" in result.message


# ---------------------------------------------------------------------------
# Unknown intent
# ---------------------------------------------------------------------------


class TestUnknownIntent:
    """Intent not in catalog → ResolutionError."""

    def test_unknown_intent(self) -> None:
        resolver = IntentResolver([_make_entry("buffer")])
        result = resolver.resolve("explode", {}, None)
        assert isinstance(result, ResolutionError)
        assert "Unknown intent 'explode'" in result.message


# ---------------------------------------------------------------------------
# No compatible tool
# ---------------------------------------------------------------------------


class TestNoCompatibleTool:
    """Artifact data_type matches no candidate → ResolutionError."""

    def test_pointcloud_no_clip(self) -> None:
        clip = _make_entry("clip", data_type="raster", input_formats=["geotiff"])
        resolver = IntentResolver([clip])
        artifact = _make_artifact(data_type="pointcloud", fmt="laz")
        result = resolver.resolve("clip", {}, artifact)
        assert isinstance(result, ResolutionError)
        assert "No tool can 'clip' on pointcloud" in result.message


# ---------------------------------------------------------------------------
# Tool precedence
# ---------------------------------------------------------------------------


class TestToolPrecedence:
    """Two candidates with same data_type → edd wins (precedence 0)."""

    def test_edd_beats_ese(self) -> None:
        edd_entry = _make_entry("clip", tool_name="edd", data_type="raster")
        ese_entry = _make_entry("clip", tool_name="ese", data_type="raster")
        resolver = IntentResolver([edd_entry, ese_entry])
        result = resolver.resolve("clip", {}, _make_artifact("raster"))
        assert isinstance(result, ResolvedCall)
        assert result.tool.name == "edd"

    def test_ese_only(self) -> None:
        """If only ese is present, it wins (only candidate)."""
        ese_entry = _make_entry("clip", tool_name="ese", data_type="raster")
        resolver = IntentResolver([ese_entry])
        result = resolver.resolve("clip", {}, _make_artifact("raster"))
        assert isinstance(result, ResolvedCall)
        assert result.tool.name == "ese"


# ---------------------------------------------------------------------------
# AC21 — No tool names in error messages
# ---------------------------------------------------------------------------


class TestNoToolNamesInErrors:
    """Error messages must not leak internal tool names (AC21)."""

    def _assert_no_tool_names(self, error: ResolutionError) -> None:
        msg = error.message.lower()
        for name in ("edd", "ese"):
            assert name not in msg, (
                f"Error message must not contain tool name '{name}': {error.message}"
            )

    def test_unknown_intent_no_names(self) -> None:
        resolver = IntentResolver([_make_entry("buffer", tool_name="edd")])
        result = resolver.resolve("explode", {}, None)
        assert isinstance(result, ResolutionError)
        self._assert_no_tool_names(result)

    def test_no_compatible_tool_no_names(self) -> None:
        clip = _make_entry("clip", tool_name="ese", data_type="raster")
        resolver = IntentResolver([clip])
        result = resolver.resolve("clip", {}, _make_artifact("pointcloud"))
        assert isinstance(result, ResolutionError)
        self._assert_no_tool_names(result)

    def test_requires_input_no_names(self) -> None:
        clip = _make_entry("clip", tool_name="edd", data_type="raster", input_formats=["geotiff"])
        resolver = IntentResolver([clip])
        result = resolver.resolve("clip", {}, None)
        assert isinstance(result, ResolutionError)
        self._assert_no_tool_names(result)


# ---------------------------------------------------------------------------
# AC48 — Fetch without item/asset
# ---------------------------------------------------------------------------


class TestFetchEnforcement:
    """Fetch intent requires both 'item' and 'asset' params (AC48)."""

    def test_fetch_missing_item(self) -> None:
        fetch = _make_entry("fetch", input_formats=[])
        resolver = IntentResolver([fetch])
        result = resolver.resolve("fetch", {"asset": "b"}, None)
        assert isinstance(result, ResolutionError)
        assert "item" in result.message
        assert "asset" in result.message

    def test_fetch_missing_asset(self) -> None:
        fetch = _make_entry("fetch", input_formats=[])
        resolver = IntentResolver([fetch])
        result = resolver.resolve("fetch", {"item": "a"}, None)
        assert isinstance(result, ResolutionError)
        assert "item" in result.message
        assert "asset" in result.message

    def test_fetch_missing_both(self) -> None:
        fetch = _make_entry("fetch", input_formats=[])
        resolver = IntentResolver([fetch])
        result = resolver.resolve("fetch", {}, None)
        assert isinstance(result, ResolutionError)

    def test_fetch_with_item_and_asset(self) -> None:
        fetch = _make_entry("fetch", input_formats=[])
        resolver = IntentResolver([fetch])
        result = resolver.resolve("fetch", {"item": "a", "asset": "b"}, None)
        assert isinstance(result, ResolvedCall)
        assert result.params == {"item": "a", "asset": "b"}

    def test_fetch_actionable_message(self) -> None:
        """Error should suggest --list-assets (AC48)."""
        fetch = _make_entry("fetch", input_formats=[])
        resolver = IntentResolver([fetch])
        result = resolver.resolve("fetch", {"item": "a"}, None)
        assert isinstance(result, ResolutionError)
        assert "--list-assets" in result.message
