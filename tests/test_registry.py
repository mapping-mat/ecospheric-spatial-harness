"""Tests for ecospheric_harness.registry.

Covers tool discovery, alias resolution, intent overrides, EDD source
disambiguation, diagnostic exclusion, collision handling, and param denylist.
"""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import MagicMock, patch

from ecospheric_harness.registry import (
    INTENT_OVERRIDES,
    PARAM_DENYLIST,
    CatalogIntentEntry,
    ToolRegistry,
    _reconstruct_descriptor,
    _resolve_intent_name,
)
from ecospheric_harness.intents import RegisteredTool

# ---------------------------------------------------------------------------
# Fixtures: realistic EDD + ESE describe/envelope shapes
# ---------------------------------------------------------------------------

EDD_SEARCH_COMMANDS: list[dict[str, Any]] = [
    # 9 search descriptors — one per EDD plugin source.
    # Real EDD describe --all output uses name="search" (not "@ckan search").
    # Source disambiguation is handled by positional pairing with plugins.
    {
        "name": "search",
        "description": "Search CKAN open data portal datasets",
        "category": "discovery",
        "parameters": [
            {"name": "--source", "description": "Source prefix", "type": "string", "required": True},
            {"name": "--bbox", "description": "Bounding box", "type": "string", "required": True},
            {"name": "--output", "description": "Output path", "type": "string", "required": False},
            {"name": "--format", "description": "Output format", "type": "string", "required": False},
            {"name": "--limit", "description": "Max results", "type": "integer", "required": False},
            {"name": "--json", "description": "JSON output", "type": "boolean", "required": False},
            {"name": "--quiet", "description": "Suppress status", "type": "boolean", "required": False},
            {"name": "--instance", "description": "CKAN instance", "type": "string", "required": False},
            {"name": "--q", "description": "Free-text query", "type": "string", "required": False},
        ],
        "input_formats": [],
        "output_formats": ["json"],
        "data_type": "metadata",
        "requires_planar_crs": False,
        "backends": [],
    },
    {
        "name": "search",
        "description": "Search earthquake event feeds",
        "category": "discovery",
        "parameters": [
            {"name": "--source", "description": "Source prefix", "type": "string", "required": True},
            {"name": "--bbox", "description": "Bounding box", "type": "string", "required": True},
            {"name": "--output", "description": "Output path", "type": "string", "required": False},
            {"name": "--format", "description": "Output format", "type": "string", "required": False},
            {"name": "--limit", "description": "Max results", "type": "integer", "required": False},
            {"name": "--json", "description": "JSON output", "type": "boolean", "required": False},
            {"name": "--quiet", "description": "Suppress status", "type": "boolean", "required": False},
            {"name": "--intent", "description": "Search intent", "type": "string", "required": True},
            {"name": "--starttime", "description": "Start time", "type": "string", "required": False},
            {"name": "--minmagnitude", "description": "Min magnitude", "type": "number", "required": False},
        ],
        "input_formats": [],
        "output_formats": ["json"],
        "data_type": "vector",
        "requires_planar_crs": False,
        "backends": [],
    },
    {
        "name": "search",
        "description": "Search FIRMS fire hotspots",
        "category": "discovery",
        "parameters": [
            {"name": "--source", "description": "Source prefix", "type": "string", "required": True},
            {"name": "--bbox", "description": "Bounding box", "type": "string", "required": True},
            {"name": "--output", "description": "Output path", "type": "string", "required": False},
            {"name": "--format", "description": "Output format", "type": "string", "required": False},
            {"name": "--limit", "description": "Max results", "type": "integer", "required": False},
            {"name": "--json", "description": "JSON output", "type": "boolean", "required": False},
            {"name": "--quiet", "description": "Suppress status", "type": "boolean", "required": False},
            {"name": "--sensor", "description": "Sensor type", "type": "string", "required": False},
            {"name": "--timespan", "description": "Time span", "type": "string", "required": False},
            {"name": "--file-format", "description": "File format", "type": "string", "required": False},
        ],
        "input_formats": [],
        "output_formats": ["json"],
        "data_type": "metadata",
        "requires_planar_crs": False,
        "backends": [],
    },
    {
        "name": "search",
        "description": "Search GBIF species occurrence records",
        "category": "discovery",
        "parameters": [
            {"name": "--source", "description": "Source prefix", "type": "string", "required": True},
            {"name": "--bbox", "description": "Bounding box", "type": "string", "required": True},
            {"name": "--output", "description": "Output path", "type": "string", "required": False},
            {"name": "--format", "description": "Output format", "type": "string", "required": False},
            {"name": "--limit", "description": "Max results", "type": "integer", "required": False},
            {"name": "--json", "description": "JSON output", "type": "boolean", "required": False},
            {"name": "--quiet", "description": "Suppress status", "type": "boolean", "required": False},
            {"name": "--taxon-key", "description": "GBIF taxon key", "type": "integer", "required": False},
            {"name": "--scientific-name", "description": "Scientific name", "type": "string", "required": False},
            {"name": "--year", "description": "Year filter", "type": "string", "required": False},
        ],
        "input_formats": [],
        "output_formats": ["json"],
        "data_type": "vector",
        "requires_planar_crs": False,
        "backends": [],
    },
    {
        "name": "search",
        "description": "Search geoBoundaries admin boundaries",
        "category": "discovery",
        "parameters": [
            {"name": "--source", "description": "Source prefix", "type": "string", "required": True},
            {"name": "--bbox", "description": "Bounding box", "type": "string", "required": True},
            {"name": "--output", "description": "Output path", "type": "string", "required": False},
            {"name": "--format", "description": "Output format", "type": "string", "required": False},
            {"name": "--limit", "description": "Max results", "type": "integer", "required": False},
            {"name": "--json", "description": "JSON output", "type": "boolean", "required": False},
            {"name": "--quiet", "description": "Suppress status", "type": "boolean", "required": False},
            {"name": "--iso3", "description": "ISO3 country code", "type": "string", "required": True},
            {"name": "--adm-level", "description": "Admin level", "type": "integer", "required": True},
        ],
        "input_formats": [],
        "output_formats": ["json"],
        "data_type": "vector",
        "requires_planar_crs": False,
        "backends": [],
    },
    {
        "name": "search",
        "description": "Search OpenTopography DEM datasets",
        "category": "discovery",
        "parameters": [
            {"name": "--source", "description": "Source prefix", "type": "string", "required": True},
            {"name": "--bbox", "description": "Bounding box", "type": "string", "required": True},
            {"name": "--output", "description": "Output path", "type": "string", "required": False},
            {"name": "--format", "description": "Output format", "type": "string", "required": False},
            {"name": "--limit", "description": "Max results", "type": "integer", "required": False},
            {"name": "--json", "description": "JSON output", "type": "boolean", "required": False},
            {"name": "--quiet", "description": "Suppress status", "type": "boolean", "required": False},
            {"name": "--demtype", "description": "DEM type", "type": "string", "required": False},
            {"name": "--output-format", "description": "Output format", "type": "string", "required": False},
        ],
        "input_formats": [],
        "output_formats": ["json"],
        "data_type": "metadata",
        "requires_planar_crs": False,
        "backends": [],
    },
    {
        "name": "search",
        "description": "Search OpenStreetMap features",
        "category": "discovery",
        "parameters": [
            {"name": "--source", "description": "Source prefix", "type": "string", "required": True},
            {"name": "--bbox", "description": "Bounding box", "type": "string", "required": True},
            {"name": "--output", "description": "Output path", "type": "string", "required": False},
            {"name": "--format", "description": "Output format", "type": "string", "required": False},
            {"name": "--limit", "description": "Max results", "type": "integer", "required": False},
            {"name": "--json", "description": "JSON output", "type": "boolean", "required": False},
            {"name": "--quiet", "description": "Suppress status", "type": "boolean", "required": False},
            {"name": "--intent", "description": "Search intent", "type": "string", "required": False},
            {"name": "--overpassql", "description": "Overpass QL", "type": "string", "required": False},
        ],
        "input_formats": [],
        "output_formats": ["json"],
        "data_type": "vector",
        "requires_planar_crs": False,
        "backends": [],
    },
    {
        "name": "search",
        "description": "Search Overture Maps features",
        "category": "discovery",
        "parameters": [
            {"name": "--source", "description": "Source prefix", "type": "string", "required": True},
            {"name": "--bbox", "description": "Bounding box", "type": "string", "required": True},
            {"name": "--output", "description": "Output path", "type": "string", "required": False},
            {"name": "--format", "description": "Output format", "type": "string", "required": False},
            {"name": "--limit", "description": "Max results", "type": "integer", "required": False},
            {"name": "--json", "description": "JSON output", "type": "boolean", "required": False},
            {"name": "--quiet", "description": "Suppress status", "type": "boolean", "required": False},
            {"name": "--intent", "description": "Search intent", "type": "string", "required": True},
            {"name": "--release", "description": "Release version", "type": "string", "required": False},
        ],
        "input_formats": [],
        "output_formats": ["json"],
        "data_type": "vector",
        "requires_planar_crs": False,
        "backends": [],
    },
    {
        "name": "search",
        "description": "Search STAC catalogs",
        "category": "discovery",
        "parameters": [
            {"name": "--source", "description": "Source prefix", "type": "string", "required": True},
            {"name": "--bbox", "description": "Bounding box", "type": "string", "required": True},
            {"name": "--output", "description": "Output path", "type": "string", "required": False},
            {"name": "--format", "description": "Output format", "type": "string", "required": False},
            {"name": "--limit", "description": "Max results", "type": "integer", "required": False},
            {"name": "--json", "description": "JSON output", "type": "boolean", "required": False},
            {"name": "--quiet", "description": "Suppress status", "type": "boolean", "required": False},
            {"name": "--catalog", "description": "STAC catalog URL", "type": "string", "required": False},
            {"name": "--collection", "description": "STAC collection", "type": "string", "required": False},
            {"name": "--date", "description": "Date range", "type": "string", "required": False},
        ],
        "input_formats": [],
        "output_formats": ["json"],
        "data_type": "metadata",
        "requires_planar_crs": False,
        "backends": [],
    },
]

EDD_NON_SEARCH_COMMANDS: list[dict[str, Any]] = [
    {
        "name": "fetch",
        "description": "Fetch a dataset by ID or URL",
        "category": "discovery",
        "parameters": [
            {"name": "--source", "description": "Source prefix", "type": "string", "required": True},
            {"name": "--id", "description": "Dataset ID", "type": "string", "required": True},
            {"name": "--output", "description": "Output path", "type": "string", "required": False},
            {"name": "--json", "description": "JSON output", "type": "boolean", "required": False},
        ],
        "input_formats": [],
        "output_formats": ["json", "geoparquet", "geojson"],
        "data_type": "any",
        "requires_planar_crs": False,
        "backends": [],
    },
    {
        "name": "info",
        "description": "Show tool info",
        "category": "info",
        "parameters": [],
        "input_formats": [],
        "output_formats": [],
        "data_type": "any",
        "requires_planar_crs": False,
        "backends": [],
    },
    {
        "name": "doctor",
        "description": "Check tool health",
        "category": "info",
        "parameters": [],
        "input_formats": [],
        "output_formats": [],
        "data_type": "any",
        "requires_planar_crs": False,
        "backends": [],
    },
    {
        "name": "plugins",
        "description": "List installed plugins",
        "category": "info",
        "parameters": [],
        "input_formats": [],
        "output_formats": [],
        "data_type": "any",
        "requires_planar_crs": False,
        "backends": [],
    },
]

ESE_COMMANDS: list[dict[str, Any]] = [
    {
        "name": "doctor",
        "description": "Check ESE health",
        "category": "diagnostic",
        "parameters": [],
        "input_formats": [],
        "output_formats": [],
        "data_type": "any",
        "requires_planar_crs": False,
        "backends": [],
    },
    {
        "name": "info",
        "description": "Show ESE info",
        "category": "diagnostic",
        "parameters": [],
        "input_formats": [],
        "output_formats": [],
        "data_type": "any",
        "requires_planar_crs": False,
        "backends": [],
    },
    {
        "name": "pipe",
        "description": "Pipe operations",
        "category": "pipe",
        "parameters": [],
        "input_formats": [],
        "output_formats": [],
        "data_type": "any",
        "requires_planar_crs": False,
        "backends": [],
    },
    {
        "name": "tee",
        "description": "Tee output",
        "category": "pipe",
        "parameters": [],
        "input_formats": [],
        "output_formats": [],
        "data_type": "any",
        "requires_planar_crs": False,
        "backends": [],
    },
    {
        "name": "describe",
        "description": "Describe ESE commands",
        "category": "diagnostic",
        "parameters": [],
        "input_formats": [],
        "output_formats": [],
        "data_type": "any",
        "requires_planar_crs": False,
        "backends": [],
    },
    {
        "name": "convert",
        "description": "Convert between formats",
        "category": "conversion",
        "parameters": [
            {"name": "--input", "description": "Input file", "type": "string", "required": True},
            {"name": "--output", "description": "Output file", "type": "string", "required": True},
            {"name": "--format", "description": "Output format", "type": "string", "required": False},
        ],
        "input_formats": ["geojson", "shp", "geoparquet"],
        "output_formats": ["geojson", "shp", "geoparquet"],
        "data_type": "vector",
        "requires_planar_crs": False,
        "backends": ["fiona", "ogr"],
    },
    {
        "name": "convert raster-format",
        "description": "Convert raster format",
        "category": "conversion",
        "parameters": [
            {"name": "--input", "description": "Input raster", "type": "string", "required": True},
            {"name": "--output", "description": "Output raster", "type": "string", "required": True},
            {"name": "--format", "description": "Output format", "type": "string", "required": False},
        ],
        "input_formats": ["geotiff", "cog"],
        "output_formats": ["geotiff", "cog"],
        "data_type": "raster",
        "requires_planar_crs": False,
        "backends": ["gdal"],
    },
    {
        "name": "hydro fill-sinks",
        "description": "Fill sinks in a DEM",
        "category": "hydro",
        "parameters": [
            {"name": "--input", "description": "Input DEM", "type": "string", "required": True},
            {"name": "--output", "description": "Output DEM", "type": "string", "required": True},
        ],
        "input_formats": ["geotiff"],
        "output_formats": ["geotiff"],
        "data_type": "raster",
        "requires_planar_crs": False,
        "backends": ["richdem"],
    },
    {
        "name": "raster clip",
        "description": "Clip raster by vector mask",
        "category": "raster",
        "parameters": [
            {"name": "--input", "description": "Input raster", "type": "string", "required": True},
            {"name": "--mask", "description": "Mask vector", "type": "string", "required": True},
            {"name": "--output", "description": "Output raster", "type": "string", "required": True},
        ],
        "input_formats": ["geotiff"],
        "output_formats": ["geotiff"],
        "data_type": "raster",
        "requires_planar_crs": False,
        "backends": ["gdal"],
    },
    {
        "name": "vector clip",
        "description": "Clip vector by another vector",
        "category": "vector",
        "parameters": [
            {"name": "--input", "description": "Input vector", "type": "string", "required": True},
            {"name": "--mask", "description": "Mask vector", "type": "string", "required": True},
            {"name": "--output", "description": "Output vector", "type": "string", "required": True},
        ],
        "input_formats": ["geojson", "shp"],
        "output_formats": ["geojson", "shp"],
        "data_type": "vector",
        "requires_planar_crs": False,
        "backends": ["fiona"],
    },
    {
        "name": "raster reproject",
        "description": "Reproject a raster to a new CRS",
        "category": "raster",
        "parameters": [
            {"name": "--input", "description": "Input raster", "type": "string", "required": True},
            {"name": "--crs", "description": "Target CRS", "type": "string", "required": True},
            {"name": "--output", "description": "Output raster", "type": "string", "required": True},
        ],
        "input_formats": ["geotiff"],
        "output_formats": ["geotiff"],
        "data_type": "raster",
        "requires_planar_crs": False,
        "backends": ["gdal"],
    },
    {
        "name": "proj transform",
        "description": "Transform coordinates between CRS",
        "category": "proj",
        "parameters": [
            {"name": "--input", "description": "Input file", "type": "string", "required": True},
            {"name": "--crs", "description": "Target CRS", "type": "string", "required": True},
            {"name": "--output", "description": "Output file", "type": "string", "required": True},
        ],
        "input_formats": ["geojson", "shp"],
        "output_formats": ["geojson", "shp"],
        "data_type": "vector",
        "requires_planar_crs": False,
        "backends": ["pyproj"],
    },
    {
        "name": "proj distance",
        "description": "Compute geodesic distance",
        "category": "proj",
        "parameters": [
            {"name": "--input", "description": "Input file", "type": "string", "required": True},
            {"name": "--output", "description": "Output file", "type": "string", "required": True},
        ],
        "input_formats": ["geojson"],
        "output_formats": ["geojson"],
        "data_type": "vector",
        "requires_planar_crs": False,
        "backends": ["pyproj"],
    },
    {
        "name": "vector buffer",
        "description": "Buffer vector features",
        "category": "vector",
        "parameters": [
            {"name": "--input", "description": "Input vector", "type": "string", "required": True},
            {"name": "--distance", "description": "Buffer distance", "type": "number", "required": True},
            {"name": "--output", "description": "Output vector", "type": "string", "required": True},
            {"name": "--json", "description": "JSON output", "type": "boolean", "required": False},
        ],
        "input_formats": ["geojson", "shp"],
        "output_formats": ["geojson", "shp"],
        "data_type": "vector",
        "requires_planar_crs": False,
        "backends": ["fiona"],
    },
]


def _make_edd_envelope() -> dict[str, Any]:
    """Build a realistic EDD describe envelope."""
    return {
        "tool": "edd",
        "tool_version": "0.5.0",
        "schema_version": "1.0",
        "status": "success",
        "command": "describe",
        "data": {
            "commands": EDD_SEARCH_COMMANDS + EDD_NON_SEARCH_COMMANDS,
        },
    }


def _make_ese_envelope() -> dict[str, Any]:
    """Build a realistic ESE describe envelope."""
    return {
        "tool": "ese",
        "tool_version": "0.8.0",
        "schema_version": "1.0",
        "status": "success",
        "command": "describe",
        "data": {
            "commands": ESE_COMMANDS,
        },
    }


EDD_PLUGINS: dict[str, Any] = {
    "tool": "edd",
    "tool_version": "0.5.0",
    "schema_version": "1.0",
    "status": "success",
    "command": "plugins",
    "data": {
        "plugins": [
            {"prefix": "@ckan"},
            {"prefix": "@earthquakes"},
            {"prefix": "@firms"},
            {"prefix": "@gbif"},
            {"prefix": "@geoboundaries"},
            {"prefix": "@opentopography"},
            {"prefix": "@osm"},
            {"prefix": "@overture"},
            {"prefix": "@stac"},
        ],
    },
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _mock_subprocess_run(*args: Any, **kwargs: Any) -> MagicMock:
    """Route subprocess.run calls to the right mock envelope."""
    cmd = args[0] if args else kwargs.get("args", [])
    if "plugins" in cmd:
        output = json.dumps(EDD_PLUGINS)
    elif cmd[0] == "edd":
        output = json.dumps(_make_edd_envelope())
    else:
        output = json.dumps(_make_ese_envelope())
    result = MagicMock()
    result.stdout = output
    result.returncode = 0
    return result


# ---------------------------------------------------------------------------
# _reconstruct_descriptor
# ---------------------------------------------------------------------------


class TestReconstructDescriptor:
    """_reconstruct_descriptor rebuilds a CommandDescriptor from JSON."""

    def test_basic_fields(self) -> None:
        data = {
            "name": "buffer",
            "description": "Buffer vector features",
            "category": "vector",
            "parameters": [
                {"name": "--distance", "description": "Buffer distance", "type": "number", "required": True},
            ],
            "input_formats": ["geojson"],
            "output_formats": ["geojson"],
            "data_type": "vector",
            "requires_planar_crs": False,
            "backends": ["fiona"],
        }
        desc = _reconstruct_descriptor(data)
        assert desc.name == "buffer"
        assert desc.description == "Buffer vector features"
        assert desc.category == "vector"
        assert len(desc.parameters) == 1
        assert desc.parameters[0].name == "--distance"
        assert desc.parameters[0].required is True
        assert desc.input_formats == ["geojson"]
        assert desc.output_formats == ["geojson"]
        assert desc.data_type == "vector"
        assert desc.requires_planar_crs is False
        assert desc.backends == ["fiona"]

    def test_defaults(self) -> None:
        """Missing optional fields get sensible defaults."""
        data = {
            "name": "test",
            "description": "Test command",
            "category": "test",
        }
        desc = _reconstruct_descriptor(data)
        assert desc.parameters == []
        assert desc.input_formats == []
        assert desc.output_formats == []
        assert desc.data_type == "any"
        assert desc.requires_planar_crs is False
        assert desc.backends == []

    def test_parameter_with_pattern(self) -> None:
        data = {
            "name": "test",
            "description": "Test",
            "category": "test",
            "parameters": [
                {"name": "--crs", "description": "CRS string", "type": "string", "required": True, "pattern": "^(EPSG|epsg):\\d+$"},
            ],
        }
        desc = _reconstruct_descriptor(data)
        assert desc.parameters[0].pattern == "^(EPSG|epsg):\\d+$"


# ---------------------------------------------------------------------------
# _resolve_intent_name (alias resolution)
# ---------------------------------------------------------------------------


class TestResolveIntentName:
    """Alias resolution rules produce the correct intent string."""

    def test_single_word_unchanged(self) -> None:
        """AC38: Single-word commands keep their name."""
        assert _resolve_intent_name("fetch") == "fetch"
        assert _resolve_intent_name("buffer") == "buffer"

    def test_two_word_space_split(self) -> None:
        """Multi-word: drop category, join remainder with underscore."""
        assert _resolve_intent_name("raster clip") == "clip"
        assert _resolve_intent_name("vector buffer") == "buffer"

    def test_hyphen_replacement(self) -> None:
        """Hyphens in the operation become underscores."""
        assert _resolve_intent_name("hydro fill-sinks") == "fill_sinks"
        assert _resolve_intent_name("hydro flow-dir") == "flow_dir"

    def test_three_token(self) -> None:
        """Three tokens: drop first, join rest."""
        assert _resolve_intent_name("convert raster-format") == "raster_format"
        assert _resolve_intent_name("raster bridge rasterize") == "bridge_rasterize"

    def test_intent_overrides(self) -> None:
        """AC49: INTENT_OVERRIDES take precedence."""
        assert _resolve_intent_name("proj transform") == "reproject"
        assert _resolve_intent_name("proj distance") == "geodesic_distance"

    def test_override_key_exact_match(self) -> None:
        """Override keys must match the full command name exactly."""
        # "proj info" is not in overrides, so normal rules apply
        assert _resolve_intent_name("proj info") == "info"


# ---------------------------------------------------------------------------
# PARAM_DENYLIST
# ---------------------------------------------------------------------------


class TestParamDenylist:
    """Params in the denylist are excluded from required_params."""

    def test_denylist_contents(self) -> None:
        expected = {"--json", "--quiet", "--no-cache", "--timeout", "--log-level", "--output", "--format"}
        assert PARAM_DENYLIST == expected

    def test_denylist_not_in_required(self) -> None:
        """Even if marked required in descriptor, denylist params are excluded."""
        data = {
            "name": "test",
            "description": "Test",
            "category": "test",
            "parameters": [
                {"name": "--input", "description": "Input", "type": "string", "required": True},
                {"name": "--output", "description": "Output", "type": "string", "required": True},
                {"name": "--json", "description": "JSON flag", "type": "boolean", "required": True},
                {"name": "--format", "description": "Format", "type": "string", "required": True},
                {"name": "--quiet", "description": "Quiet", "type": "boolean", "required": True},
            ],
        }
        desc = _reconstruct_descriptor(data)
        tool = RegisteredTool(name="test", version="1.0", binary="test", commands=[desc])
        catalog = ToolRegistry.build_catalog([tool], {})
        assert len(catalog) == 1
        assert "--input" in catalog[0].required_params
        assert "--output" not in catalog[0].required_params
        assert "--json" not in catalog[0].required_params
        assert "--format" not in catalog[0].required_params
        assert "--quiet" not in catalog[0].required_params


# ---------------------------------------------------------------------------
# Diagnostic exclusion (AC36)
# ---------------------------------------------------------------------------


class TestDiagnosticExclusion:
    """Commands in diagnostic/info/pipe categories are excluded."""

    @patch("ecospheric_harness.registry.subprocess.run")
    def test_ese_diagnostics_excluded(self, mock_run: MagicMock) -> None:
        """doctor, info, pipe, tee, describe from ESE are all excluded."""
        mock_run.side_effect = _mock_subprocess_run
        tools = ToolRegistry.discover_tools(["ese"])
        catalog = ToolRegistry.build_catalog(tools, {})
        intents = [e.intent for e in catalog]
        # These should NOT appear
        for excluded in ["doctor", "info", "pipe", "tee", "describe"]:
            assert excluded not in intents, f"{excluded!r} should be excluded"

    @patch("ecospheric_harness.registry.subprocess.run")
    def test_edd_info_excluded(self, mock_run: MagicMock) -> None:
        """info, doctor, plugins from EDD are excluded (category 'info')."""
        mock_run.side_effect = _mock_subprocess_run
        tools = ToolRegistry.discover_tools(["edd"])
        catalog = ToolRegistry.build_catalog(tools, {})
        intents = [e.intent for e in catalog]
        assert "info" not in intents
        assert "doctor" not in intents
        assert "plugins" not in intents


# ---------------------------------------------------------------------------
# Single-word rule (AC38)
# ---------------------------------------------------------------------------


class TestSingleWordRule:
    """AC38: Single-word commands keep their name as intent."""

    @patch("ecospheric_harness.registry.subprocess.run")
    def test_fetch_stays_fetch(self, mock_run: MagicMock) -> None:
        mock_run.side_effect = _mock_subprocess_run
        tools = ToolRegistry.discover_tools(["edd"])
        catalog = ToolRegistry.build_catalog(tools, {})
        fetch_entries = [e for e in catalog if e.intent == "fetch"]
        assert len(fetch_entries) == 1
        assert fetch_entries[0].command.name == "fetch"

    @patch("ecospheric_harness.registry.subprocess.run")
    def test_convert_stays_convert(self, mock_run: MagicMock) -> None:
        """'convert' (single-word) keeps its name."""
        mock_run.side_effect = _mock_subprocess_run
        tools = ToolRegistry.discover_tools(["ese"])
        catalog = ToolRegistry.build_catalog(tools, {})
        convert_entries = [e for e in catalog if e.intent == "convert"]
        assert len(convert_entries) == 1
        assert convert_entries[0].command.name == "convert"


# ---------------------------------------------------------------------------
# Collision: same intent from multiple commands
# ---------------------------------------------------------------------------


class TestCollision:
    """When two commands produce the same intent, both entries are stored."""

    @patch("ecospheric_harness.registry.subprocess.run")
    def test_raster_clip_and_vector_clip_both_kept(self, mock_run: MagicMock) -> None:
        """Both 'raster clip' and 'vector clip' resolve to 'clip' — both stored."""
        mock_run.side_effect = _mock_subprocess_run
        tools = ToolRegistry.discover_tools(["ese"])
        catalog = ToolRegistry.build_catalog(tools, {})
        clip_entries = [e for e in catalog if e.intent == "clip"]
        assert len(clip_entries) == 2
        names = {e.command.name for e in clip_entries}
        assert names == {"raster clip", "vector clip"}


# ---------------------------------------------------------------------------
# EDD source disambiguation
# ---------------------------------------------------------------------------


class TestEDDSourceDisambiguation:
    """9 EDD search descriptors are paired with 9 source prefixes."""

    @patch("ecospheric_harness.registry.subprocess.run")
    def test_nine_search_intents(self, mock_run: MagicMock) -> None:
        mock_run.side_effect = _mock_subprocess_run
        tools = ToolRegistry.discover_tools(["edd"])
        sources = {"edd": [p["prefix"] for p in EDD_PLUGINS["data"]["plugins"]]}
        catalog = ToolRegistry.build_catalog(tools, sources)
        search_entries = [e for e in catalog if e.intent.startswith("search_")]
        assert len(search_entries) == 9

    @patch("ecospheric_harness.registry.subprocess.run")
    def test_search_intent_names(self, mock_run: MagicMock) -> None:
        mock_run.side_effect = _mock_subprocess_run
        tools = ToolRegistry.discover_tools(["edd"])
        sources = {"edd": [p["prefix"] for p in EDD_PLUGINS["data"]["plugins"]]}
        catalog = ToolRegistry.build_catalog(tools, sources)
        search_intents = sorted(e.intent for e in catalog if e.intent.startswith("search_"))
        expected = [
            "search_ckan",
            "search_earthquakes",
            "search_firms",
            "search_gbif",
            "search_geoboundaries",
            "search_opentopography",
            "search_osm",
            "search_overture",
            "search_stac",
        ]
        assert search_intents == expected

    @patch("ecospheric_harness.registry.subprocess.run")
    def test_search_source_field(self, mock_run: MagicMock) -> None:
        """Each search entry carries its source prefix."""
        mock_run.side_effect = _mock_subprocess_run
        tools = ToolRegistry.discover_tools(["edd"])
        sources = {"edd": [p["prefix"] for p in EDD_PLUGINS["data"]["plugins"]]}
        catalog = ToolRegistry.build_catalog(tools, sources)
        for entry in catalog:
            if entry.intent.startswith("search_"):
                assert entry.source is not None
                assert entry.source.startswith("@")


# ---------------------------------------------------------------------------
# Source fingerprint test
# ---------------------------------------------------------------------------


class TestSourceFingerprint:
    """Each paired search descriptor has unique params identifying its source."""

    @patch("ecospheric_harness.registry.subprocess.run")
    def test_ckan_has_instance_param(self, mock_run: MagicMock) -> None:
        mock_run.side_effect = _mock_subprocess_run
        tools = ToolRegistry.discover_tools(["edd"])
        sources = {"edd": [p["prefix"] for p in EDD_PLUGINS["data"]["plugins"]]}
        catalog = ToolRegistry.build_catalog(tools, sources)
        ckan = [e for e in catalog if e.intent == "search_ckan"]
        assert len(ckan) == 1
        param_names = {p.name for p in ckan[0].command.parameters}
        assert "--instance" in param_names
        assert "--q" in param_names

    @patch("ecospheric_harness.registry.subprocess.run")
    def test_earthquakes_has_intent_and_magnitude(self, mock_run: MagicMock) -> None:
        mock_run.side_effect = _mock_subprocess_run
        tools = ToolRegistry.discover_tools(["edd"])
        sources = {"edd": [p["prefix"] for p in EDD_PLUGINS["data"]["plugins"]]}
        catalog = ToolRegistry.build_catalog(tools, sources)
        eq = [e for e in catalog if e.intent == "search_earthquakes"]
        assert len(eq) == 1
        param_names = {p.name for p in eq[0].command.parameters}
        assert "--intent" in param_names
        assert "--minmagnitude" in param_names

    @patch("ecospheric_harness.registry.subprocess.run")
    def test_geoboundaries_has_iso3_and_adm_level(self, mock_run: MagicMock) -> None:
        mock_run.side_effect = _mock_subprocess_run
        tools = ToolRegistry.discover_tools(["edd"])
        sources = {"edd": [p["prefix"] for p in EDD_PLUGINS["data"]["plugins"]]}
        catalog = ToolRegistry.build_catalog(tools, sources)
        gb = [e for e in catalog if e.intent == "search_geoboundaries"]
        assert len(gb) == 1
        param_names = {p.name for p in gb[0].command.parameters}
        assert "--iso3" in param_names
        assert "--adm-level" in param_names
        # Verify required params for geoboundaries include --iso3 and --adm-level
        assert "--iso3" in gb[0].required_params
        assert "--adm-level" in gb[0].required_params

    @patch("ecospheric_harness.registry.subprocess.run")
    def test_stac_has_catalog_and_collection(self, mock_run: MagicMock) -> None:
        mock_run.side_effect = _mock_subprocess_run
        tools = ToolRegistry.discover_tools(["edd"])
        sources = {"edd": [p["prefix"] for p in EDD_PLUGINS["data"]["plugins"]]}
        catalog = ToolRegistry.build_catalog(tools, sources)
        stac = [e for e in catalog if e.intent == "search_stac"]
        assert len(stac) == 1
        param_names = {p.name for p in stac[0].command.parameters}
        assert "--catalog" in param_names
        assert "--collection" in param_names
        assert "--date" in param_names

    @patch("ecospheric_harness.registry.subprocess.run")
    def test_osm_has_overpassql(self, mock_run: MagicMock) -> None:
        mock_run.side_effect = _mock_subprocess_run
        tools = ToolRegistry.discover_tools(["edd"])
        sources = {"edd": [p["prefix"] for p in EDD_PLUGINS["data"]["plugins"]]}
        catalog = ToolRegistry.build_catalog(tools, sources)
        osm = [e for e in catalog if e.intent == "search_osm"]
        assert len(osm) == 1
        param_names = {p.name for p in osm[0].command.parameters}
        assert "--overpassql" in param_names

    @patch("ecospheric_harness.registry.subprocess.run")
    def test_overture_has_intent_and_release(self, mock_run: MagicMock) -> None:
        mock_run.side_effect = _mock_subprocess_run
        tools = ToolRegistry.discover_tools(["edd"])
        sources = {"edd": [p["prefix"] for p in EDD_PLUGINS["data"]["plugins"]]}
        catalog = ToolRegistry.build_catalog(tools, sources)
        ov = [e for e in catalog if e.intent == "search_overture"]
        assert len(ov) == 1
        param_names = {p.name for p in ov[0].command.parameters}
        assert "--intent" in param_names
        assert "--release" in param_names


# ---------------------------------------------------------------------------
# discover_tools integration
# ---------------------------------------------------------------------------


class TestDiscoverTools:
    """discover_tools invokes subprocess and builds RegisteredTool objects."""

    @patch("ecospheric_harness.registry.subprocess.run")
    def test_edd_discovery(self, mock_run: MagicMock) -> None:
        mock_run.side_effect = _mock_subprocess_run
        tools = ToolRegistry.discover_tools(["edd"])
        assert len(tools) == 1
        tool = tools[0]
        assert tool.name == "edd"
        assert tool.version == "0.5.0"
        assert tool.binary == "edd"
        assert len(tool.commands) == len(EDD_SEARCH_COMMANDS) + len(EDD_NON_SEARCH_COMMANDS)

    @patch("ecospheric_harness.registry.subprocess.run")
    def test_ese_discovery(self, mock_run: MagicMock) -> None:
        mock_run.side_effect = _mock_subprocess_run
        tools = ToolRegistry.discover_tools(["ese"])
        assert len(tools) == 1
        tool = tools[0]
        assert tool.name == "ese"
        assert tool.version == "0.8.0"
        assert len(tool.commands) == len(ESE_COMMANDS)

    @patch("ecospheric_harness.registry.subprocess.run")
    def test_binary_resolution_env_override(self, mock_run: MagicMock) -> None:
        """EDD_BIN env var overrides default binary name."""
        mock_run.side_effect = _mock_subprocess_run
        with patch.dict("os.environ", {"EDD_BIN": "/custom/edd"}):
            tools = ToolRegistry.discover_tools(["edd"])
            assert tools[0].binary == "/custom/edd"


# ---------------------------------------------------------------------------
# discover_sources
# ---------------------------------------------------------------------------


class TestDiscoverSources:
    """discover_sources parses plugin prefixes from EDD."""

    @patch("ecospheric_harness.registry.subprocess.run")
    def test_edd_sources(self, mock_run: MagicMock) -> None:
        mock_run.side_effect = _mock_subprocess_run
        tool = RegisteredTool(name="edd", version="0.5.0", binary="edd", commands=[])
        sources = ToolRegistry.discover_sources(tool)
        assert len(sources) == 9
        assert sources[0] == "@ckan"
        assert sources[6] == "@osm"
        assert sources[8] == "@stac"


# ---------------------------------------------------------------------------
# build_catalog: multi-tool integration
# ---------------------------------------------------------------------------


class TestBuildCatalogMultiTool:
    """Full catalog from both EDD + ESE."""

    @patch("ecospheric_harness.registry.subprocess.run")
    def test_full_catalog(self, mock_run: MagicMock) -> None:
        mock_run.side_effect = _mock_subprocess_run
        tools = ToolRegistry.discover_tools(["edd", "ese"])
        sources = {"edd": [p["prefix"] for p in EDD_PLUGINS["data"]["plugins"]]}
        catalog = ToolRegistry.build_catalog(tools, sources)
        intents = [e.intent for e in catalog]
        # Should have search intents from EDD
        assert "search_osm" in intents
        assert "search_stac" in intents
        # Should have ESE operations
        assert "clip" in intents
        assert "buffer" in intents
        assert "fill_sinks" in intents
        assert "raster_format" in intents
        # Should have EDD fetch
        assert "fetch" in intents
        # Should NOT have diagnostics
        assert "doctor" not in intents
        assert "info" not in intents
        assert "pipe" not in intents

    @patch("ecospheric_harness.registry.subprocess.run")
    def test_catalog_entry_types(self, mock_run: MagicMock) -> None:
        """All catalog entries are CatalogIntentEntry instances."""
        mock_run.side_effect = _mock_subprocess_run
        tools = ToolRegistry.discover_tools(["edd", "ese"])
        sources = {"edd": [p["prefix"] for p in EDD_PLUGINS["data"]["plugins"]]}
        catalog = ToolRegistry.build_catalog(tools, sources)
        for entry in catalog:
            assert isinstance(entry, CatalogIntentEntry)
            assert entry.intent
            assert entry.description
            assert entry.tool is not None
            assert entry.command is not None


# ---------------------------------------------------------------------------
# Intent overrides (AC49)
# ---------------------------------------------------------------------------


class TestIntentOverrides:
    """AC49: INTENT_OVERRIDES produce the correct catalog entries."""

    @patch("ecospheric_harness.registry.subprocess.run")
    def test_proj_transform_becomes_reproject(self, mock_run: MagicMock) -> None:
        """proj transform → reproject via override. raster reproject also →
        reproject via normal alias resolution.  Both are stored (collision)."""
        mock_run.side_effect = _mock_subprocess_run
        tools = ToolRegistry.discover_tools(["ese"])
        catalog = ToolRegistry.build_catalog(tools, {})
        reproject = [e for e in catalog if e.intent == "reproject"]
        assert len(reproject) == 2
        names = {e.command.name for e in reproject}
        assert "proj transform" in names
        assert "raster reproject" in names
        # The override entry specifically comes from proj transform
        override_entry = [e for e in reproject if e.command.name == "proj transform"]
        assert len(override_entry) == 1

    @patch("ecospheric_harness.registry.subprocess.run")
    def test_proj_distance_becomes_geodesic_distance(self, mock_run: MagicMock) -> None:
        mock_run.side_effect = _mock_subprocess_run
        tools = ToolRegistry.discover_tools(["ese"])
        catalog = ToolRegistry.build_catalog(tools, {})
        geodesic = [e for e in catalog if e.intent == "geodesic_distance"]
        assert len(geodesic) == 1
        assert geodesic[0].command.name == "proj distance"

    def test_override_constants(self) -> None:
        """Verify INTENT_OVERRIDES has the expected entries."""
        assert INTENT_OVERRIDES["proj transform"] == "reproject"
        assert INTENT_OVERRIDES["proj distance"] == "geodesic_distance"


# ---------------------------------------------------------------------------
# Issue 5: search intents built even when source_lookup is empty
# ---------------------------------------------------------------------------


class TestSearchFallbackWithoutSources:
    """Search intents should be buildable even when discover_sources fails."""

    @patch("ecospheric_harness.registry.subprocess.run")
    def test_search_intents_built_without_sources(self, mock_run: MagicMock) -> None:
        """When sources={} (discover_sources failed), search commands still
        appear in the catalog.  Without source prefixes to disambiguate,
        they all collide on intent='search'."""
        mock_run.side_effect = _mock_subprocess_run
        tools = ToolRegistry.discover_tools(["edd"])
        # Empty sources — simulates discover_sources failure
        catalog = ToolRegistry.build_catalog(tools, {})
        search_entries = [e for e in catalog if e.intent == "search"]
        assert len(search_entries) == 9

    @patch("ecospheric_harness.registry.subprocess.run")
    def test_search_source_derived_from_prefix(self, mock_run: MagicMock) -> None:
        """Source field is set from the sources dict (not command name)."""
        mock_run.side_effect = _mock_subprocess_run
        tools = ToolRegistry.discover_tools(["edd"])

        # Without sources, search commands collide on intent='search'
        # and have no source tag.
        catalog_no_src = ToolRegistry.build_catalog(tools, {})
        search_collisions = [e for e in catalog_no_src if e.intent == "search"]
        assert len(search_collisions) == 9
        for entry in search_collisions:
            assert entry.source is None

        # With sources provided, each search gets its unique intent + source.
        sources = {"edd": [p["prefix"] for p in EDD_PLUGINS["data"]["plugins"]]}
        catalog = ToolRegistry.build_catalog(tools, sources)
        search_entries = [e for e in catalog if e.intent.startswith("search_")]
        assert len(search_entries) == 9
        for entry in search_entries:
            assert entry.source is not None
            assert entry.source.startswith("@")
            expected_prefix = entry.intent.replace("search_", "")
            assert entry.source == f"@{expected_prefix}"
