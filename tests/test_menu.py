"""Tests for menu narrowing (T4.1)."""

from __future__ import annotations

from pathlib import Path

from etp.describe import CommandDescriptor, ParameterDescriptor

from ecospheric_harness.artifact import Artifact
from ecospheric_harness.intents import IntentEntry, RegisteredTool
from ecospheric_harness.menu import available_intents
from ecospheric_harness.resolver import IntentResolver


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _param(name: str, required: bool = False) -> ParameterDescriptor:
    return ParameterDescriptor(
        name=name,
        description=f"param {name}",
        type="string",
        required=required,
    )


def _cmd(
    name: str,
    *,
    category: str = "",
    data_type: str = "any",
    input_formats: list[str] | None = None,
    params: list[ParameterDescriptor] | None = None,
) -> CommandDescriptor:
    return CommandDescriptor(
        name=name,
        description=f"command {name}",
        category=category,
        parameters=params or [],
        input_formats=input_formats,
        output_formats=[],
        data_type=data_type,
        requires_planar_crs=False,
        backends=[],
    )


def _tool(name: str) -> RegisteredTool:
    return RegisteredTool(name=name, version="1.0.0", binary=name, commands=[])


def _entry(
    intent: str,
    cmd: CommandDescriptor,
    *,
    tool_name: str = "ese",
    required_params: list[str] | None = None,
) -> IntentEntry:
    rp = required_params if required_params is not None else [
        p.name for p in cmd.parameters if p.required
    ]
    return IntentEntry(
        intent=intent,
        description=f"intent {intent}",
        tool=_tool(tool_name),
        command=cmd,
        required_params=rp,
    )


def _artifact(
    fmt: str = "geotiff",
    data_type: str = "raster",
) -> Artifact:
    return Artifact(
        path=Path("/tmp/test.tif"),
        envelope={},
        format=fmt,
        data_type=data_type,
    )


# ---------------------------------------------------------------------------
# Fixtures: shared catalog pieces
# ---------------------------------------------------------------------------

def _raster_tool() -> RegisteredTool:
    return _tool("ese")


def _build_catalog() -> list[IntentEntry]:
    """Build a realistic multi-entry catalog for tests."""
    _raster_tool()

    # --- no-input commands (search/fetch) ---
    search_stac_cmd = _cmd(
        "search",
        category="search",
        data_type="any",
        input_formats=[],
        params=[_param("--query", required=True)],
    )
    search_stac = _entry("search_stac", search_stac_cmd, tool_name="ese",
                          required_params=["--query"])

    fetch_cmd = _cmd(
        "fetch",
        category="fetch",
        data_type="any",
        input_formats=[],
        params=[_param("--item", required=True), _param("--asset", required=True)],
    )
    fetch = _entry("fetch", fetch_cmd, tool_name="ese",
                    required_params=["--item", "--asset"])

    # --- raster ops ---
    clip_raster_cmd = _cmd(
        "raster clip",
        category="raster",
        data_type="raster",
        input_formats=["geotiff", "cog"],
        params=[_param("--by", required=True), _param("--crop")],
    )
    clip_raster = _entry("clip", clip_raster_cmd, tool_name="ese",
                          required_params=["--by"])

    reproject_raster_cmd = _cmd(
        "raster proj",
        category="raster",
        data_type="raster",
        input_formats=["geotiff"],
        params=[_param("--crs", required=True)],
    )
    reproject_raster = _entry("reproject", reproject_raster_cmd, tool_name="ese",
                               required_params=["--crs"])

    # --- vector ops ---
    clip_vector_cmd = _cmd(
        "vector clip",
        category="vector",
        data_type="vector",
        input_formats=["geojson", "gpkg"],
        params=[_param("--by", required=True), _param("--where")],
    )
    clip_vector = _entry("clip", clip_vector_cmd, tool_name="ese",
                          required_params=["--by", "--where"])

    buffer_cmd = _cmd(
        "vector buffer",
        category="vector",
        data_type="vector",
        input_formats=["geojson"],
        params=[_param("--distance", required=True)],
    )
    buffer = _entry("buffer", buffer_cmd, tool_name="ese",
                     required_params=["--distance"])

    # --- "any" data_type ops (accept any input) ---
    summary_cmd = _cmd(
        "summary",
        category="summary",
        data_type="any",
        input_formats=["geotiff", "geojson", "gpkg"],
        params=[_param("--stats")],
    )
    summary = _entry("summary", summary_cmd, tool_name="ese",
                      required_params=[])

    # --- diagnostic (should be excluded) ---
    doctor_cmd = _cmd("doctor", category="diagnostic")
    doctor = _entry("doctor", doctor_cmd, tool_name="ese", required_params=[])

    info_cmd = _cmd("info", category="info")
    info = _entry("info", info_cmd, tool_name="ese", required_params=[])

    pipe_cmd = _cmd("pipe", category="pipe")
    pipe = _entry("pipe", pipe_cmd, tool_name="ese", required_params=[])

    return [
        search_stac,
        fetch,
        clip_raster,
        reproject_raster,
        clip_vector,
        buffer,
        summary,
        doctor,
        info,
        pipe,
    ]


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestNoArtifact:
    """Only no-input commands shown when artifact is None."""

    def test_only_no_input_commands(self) -> None:
        catalog = _build_catalog()
        resolver = IntentResolver(catalog)
        options = available_intents(catalog, None, resolver)

        intents = [o.intent for o in options]
        # search_stac and fetch have empty input_formats → shown
        assert "search_stac" in intents
        assert "fetch" in intents
        # raster/vector ops require input → not shown
        assert "clip" not in intents
        assert "reproject" not in intents
        assert "buffer" not in intents
        assert "summary" not in intents


class TestDiagnosticExcluded:
    """Diagnostic, info, and pipe entries never appear."""

    def test_doctor_info_pipe_excluded(self) -> None:
        catalog = _build_catalog()
        resolver = IntentResolver(catalog)
        options = available_intents(catalog, None, resolver)

        intents = [o.intent for o in options]
        assert "doctor" not in intents
        assert "info" not in intents
        assert "pipe" not in intents

    def test_excluded_even_with_artifact(self) -> None:
        catalog = _build_catalog()
        resolver = IntentResolver(catalog)
        art = _artifact("geotiff", "raster")
        options = available_intents(catalog, art, resolver)

        intents = [o.intent for o in options]
        assert "doctor" not in intents
        assert "info" not in intents
        assert "pipe" not in intents


class TestRasterArtifact:
    """Raster artifact shows only raster-compatible intents."""

    def test_raster_compatible(self) -> None:
        catalog = _build_catalog()
        resolver = IntentResolver(catalog)
        art = _artifact("geotiff", "raster")
        options = available_intents(catalog, art, resolver)

        intents = [o.intent for o in options]
        # raster ops: clip (raster), reproject
        assert "clip" in intents
        assert "reproject" in intents
        # "any" data_type + format match: summary accepts geotiff
        assert "summary" in intents
        # vector-only: buffer (data_type="vector", no "any") → excluded
        assert "buffer" not in intents
        # no-input commands excluded when artifact present
        assert "search_stac" not in intents
        assert "fetch" not in intents


class TestVectorArtifact:
    """Vector artifact shows only vector-compatible intents."""

    def test_vector_compatible(self) -> None:
        catalog = _build_catalog()
        resolver = IntentResolver(catalog)
        art = _artifact("geojson", "vector")
        options = available_intents(catalog, art, resolver)

        intents = [o.intent for o in options]
        # vector ops: clip (vector), buffer
        assert "clip" in intents
        assert "buffer" in intents
        # "any" data_type + format match: summary accepts geojson
        assert "summary" in intents
        # raster-only: reproject (data_type="raster") → excluded
        assert "reproject" not in intents
        # no-input commands excluded
        assert "search_stac" not in intents
        assert "fetch" not in intents


class TestDedupResolvedParams:
    """Dedup shows params from the entry the resolver actually picks."""

    def test_clip_raster_uses_raster_params(self) -> None:
        catalog = _build_catalog()
        resolver = IntentResolver(catalog)
        art = _artifact("geotiff", "raster")
        options = available_intents(catalog, art, resolver)

        clip_opts = [o for o in options if o.intent == "clip"]
        assert len(clip_opts) == 1
        # Raster clip has required_params=["--by"]
        assert clip_opts[0].required_params == ["--by"]

    def test_clip_vector_uses_vector_params(self) -> None:
        catalog = _build_catalog()
        resolver = IntentResolver(catalog)
        art = _artifact("geojson", "vector")
        options = available_intents(catalog, art, resolver)

        clip_opts = [o for o in options if o.intent == "clip"]
        assert len(clip_opts) == 1
        # Vector clip has required_params=["--by", "--where"]
        assert "--by" in clip_opts[0].required_params
        assert "--where" in clip_opts[0].required_params


class TestSTACSearch:
    """STAC search (no artifact) — menu shows only no-input commands."""

    def test_search_shown_without_artifact(self) -> None:
        catalog = _build_catalog()
        resolver = IntentResolver(catalog)
        options = available_intents(catalog, None, resolver)

        intents = [o.intent for o in options]
        assert "search_stac" in intents
        assert "fetch" in intents

    def test_search_hidden_with_artifact(self) -> None:
        catalog = _build_catalog()
        resolver = IntentResolver(catalog)
        art = _artifact("geotiff", "raster")
        options = available_intents(catalog, art, resolver)

        intents = [o.intent for o in options]
        assert "search_stac" not in intents


class TestDirectDataSearch:
    """Direct-data search (vector artifact) narrows to vector ops."""

    def test_vector_narrows_correctly(self) -> None:
        catalog = _build_catalog()
        resolver = IntentResolver(catalog)
        art = _artifact("geojson", "vector")
        options = available_intents(catalog, art, resolver)

        intents = [o.intent for o in options]
        assert "clip" in intents
        assert "buffer" in intents
        assert "reproject" not in intents
        assert "search_stac" not in intents
        assert "fetch" not in intents


class TestCap15:
    """Menu returns at most 15 options."""

    def test_cap_at_15(self) -> None:
        # Build a catalog with >15 compatible no-input commands
        catalog: list[IntentEntry] = []
        for i in range(20):
            cmd = _cmd(
                f"op{i}",
                category="op",
                data_type="any",
                input_formats=[],
            )
            catalog.append(_entry(f"op{i}", cmd, tool_name="ese",
                                   required_params=[]))

        resolver = IntentResolver(catalog)
        options = available_intents(catalog, None, resolver)
        assert len(options) == 15

    def test_cap_preserves_order(self) -> None:
        catalog: list[IntentEntry] = []
        for i in range(20):
            cmd = _cmd(
                f"op{i}",
                category="op",
                data_type="any",
                input_formats=[],
            )
            catalog.append(_entry(f"op{i}", cmd, tool_name="ese",
                                   required_params=[]))

        resolver = IntentResolver(catalog)
        options = available_intents(catalog, None, resolver)
        assert [o.intent for o in options] == [f"op{i}" for i in range(15)]


class TestFormatNormalization:
    """Format aliases (e.g. tif → geotiff) are normalized for matching."""

    def test_tif_matches_geotiff(self) -> None:
        catalog = _build_catalog()
        resolver = IntentResolver(catalog)
        # Artifact format "tif" should normalize to "geotiff"
        art = _artifact(fmt="tif", data_type="raster")
        options = available_intents(catalog, art, resolver)

        intents = [o.intent for o in options]
        # clip raster accepts "geotiff" — should match "tif"
        assert "clip" in intents
        assert "reproject" in intents

    def test_gtiff_matches_geotiff(self) -> None:
        catalog = _build_catalog()
        resolver = IntentResolver(catalog)
        art = _artifact(fmt="gtiff", data_type="raster")
        options = available_intents(catalog, art, resolver)

        intents = [o.intent for o in options]
        assert "clip" in intents

    def test_no_match_wrong_format(self) -> None:
        catalog = _build_catalog()
        resolver = IntentResolver(catalog)
        # "laz" is a point cloud format, not accepted by raster ops
        art = _artifact(fmt="laz", data_type="raster")
        options = available_intents(catalog, art, resolver)

        intents = [o.intent for o in options]
        assert "clip" not in intents
        assert "reproject" not in intents


class TestParamDenylist:
    """Required params in PARAM_DENYLIST are excluded from menu options."""

    def test_denylist_params_filtered(self) -> None:
        cmd = _cmd(
            "test op",
            category="test",
            data_type="any",
            input_formats=[],
            params=[
                _param("--query", required=True),
                _param("--json", required=True),  # in denylist
                _param("--output", required=True),  # in denylist
            ],
        )
        entry = _entry("test_op", cmd, tool_name="ese",
                        required_params=["--query", "--json", "--output"])
        catalog = [entry]
        resolver = IntentResolver(catalog)
        options = available_intents(catalog, None, resolver)

        assert len(options) == 1
        assert "--query" in options[0].required_params
        assert "--json" not in options[0].required_params
        assert "--output" not in options[0].required_params
