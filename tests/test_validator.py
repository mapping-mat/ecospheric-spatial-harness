"""Tests for ecospheric_harness.validator — SchemaValidator and ValidationResult."""

from __future__ import annotations

from etp.describe import CommandDescriptor, ParameterDescriptor

from ecospheric_harness.intents import RegisteredTool, ResolvedCall
from ecospheric_harness.validator import SchemaValidator, ValidationResult


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------

_STUB_TOOL = RegisteredTool(
    name="test-tool",
    version="0.0.1",
    binary="test-tool",
    commands=[],  # not used by the validator
)


def _cmd(params: list[ParameterDescriptor]) -> CommandDescriptor:
    return CommandDescriptor(
        name="test-cmd",
        description="a test command",
        category="vector",
        parameters=params,
    )


def _resolve(cmd: CommandDescriptor, params: dict[str, object]) -> ResolvedCall:
    return ResolvedCall(tool=_STUB_TOOL, command=cmd, params=params)


_validator = SchemaValidator()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_valid_params_pass() -> None:
    """A well-formed payload with all required params must pass."""
    cmd = _cmd([
        ParameterDescriptor("--name", "a name", "string", required=True),
        ParameterDescriptor("--count", "a count", "integer", required=False),
    ])
    result = _validator.validate(_resolve(cmd, {"name": "hello", "count": 42}))
    assert result.ok is True
    assert result.errors == []


def test_missing_required_param_error() -> None:
    """Omitting a required param must fail with the param name in the error."""
    cmd = _cmd([
        ParameterDescriptor("--target", "target path", "string", required=True),
    ])
    result = _validator.validate(_resolve(cmd, {}))
    assert result.ok is False
    assert len(result.errors) >= 1
    assert any("target" in e for e in result.errors)


def test_wrong_type_error() -> None:
    """Supplying a string where an integer is expected must fail."""
    cmd = _cmd([
        ParameterDescriptor("--distance", "buffer distance", "integer", required=True),
    ])
    result = _validator.validate(_resolve(cmd, {"distance": "not-a-number"}))
    assert result.ok is False
    assert any("distance" in e for e in result.errors)


def test_extra_unknown_param_error() -> None:
    """An extra key must fail when additionalProperties is false."""
    cmd = _cmd([
        ParameterDescriptor("--x", "x coord", "number", required=True),
    ])
    result = _validator.validate(_resolve(cmd, {"x": 1.0, "bogus": "surprise"}))
    assert result.ok is False
    assert any("bogus" in e for e in result.errors)


def test_input_target_stripped_before_validation() -> None:
    """The internal ``_input_target`` key must be silently removed."""
    cmd = _cmd([
        ParameterDescriptor("--layer", "layer name", "string", required=True),
    ])
    result = _validator.validate(
        _resolve(cmd, {"layer": "roads", "_input_target": "/tmp/out.geojson"})
    )
    assert result.ok is True
    assert result.errors == []


def test_boolean_param_given_as_string_error() -> None:
    """A string value for a boolean param must fail."""
    cmd = _cmd([
        ParameterDescriptor("--verbose", "verbose mode", "boolean", required=True),
    ])
    result = _validator.validate(_resolve(cmd, {"verbose": "true"}))
    assert result.ok is False
    assert any("verbose" in e for e in result.errors)


def test_array_param_given_as_non_list_error() -> None:
    """A non-list value for an array param must fail."""
    cmd = _cmd([
        ParameterDescriptor("--coords", "coordinate list", "array", required=True),
    ])
    result = _validator.validate(_resolve(cmd, {"coords": "not-a-list"}))
    assert result.ok is False
    assert any("coords" in e for e in result.errors)


def test_empty_params_on_optional_only_command() -> None:
    """Empty params on a command with no required params must pass."""
    cmd = _cmd([
        ParameterDescriptor("--format", "output format", "string", required=False),
    ])
    result = _validator.validate(_resolve(cmd, {}))
    assert result.ok is True
    assert result.errors == []


def test_validation_result_dataclass() -> None:
    """ValidationResult is a proper dataclass with correct defaults."""
    ok_result = ValidationResult(ok=True)
    assert ok_result.ok is True
    assert ok_result.errors == []

    err_result = ValidationResult(ok=False, errors=["boom"])
    assert err_result.ok is False
    assert err_result.errors == ["boom"]


# ---------------------------------------------------------------------------
# Dashed-key normalization (params.py integration)
# ---------------------------------------------------------------------------


def test_validate_accepts_dashed_param_keys() -> None:
    """Params with '--'-prefixed keys must be accepted after normalization.

    The validator (via _coerce_params) should strip leading dashes and
    convert hyphens to underscores before matching against the schema.
    """
    cmd = _cmd([
        ParameterDescriptor("--source", "data source", "string", required=True),
        ParameterDescriptor("--bbox", "bounding box", "string", required=True),
    ])
    dashed_params = {"--source": "@osm", "--bbox": "-121.9,39.7,-121.8,39.8"}
    result = _validator.validate(_resolve(cmd, dashed_params))
    assert result.ok is True, f"Expected OK but got errors: {result.errors}"


def test_coerce_params_returns_normalized_keys() -> None:
    """_coerce_params must return keys WITHOUT the '--' prefix."""
    from ecospheric_harness.validator import _coerce_params

    cmd = _cmd([
        ParameterDescriptor("--source", "data source", "string", required=True),
        ParameterDescriptor("--output-crs", "target CRS", "string", required=False),
    ])
    raw = {"--source": "@osm", "--output-crs": "EPSG:4326"}
    coerced = _coerce_params(raw, cmd)
    assert "--source" not in coerced, "Raw dashed key should be replaced"
    assert "--output-crs" not in coerced, "Raw dashed key should be replaced"
    assert "source" in coerced
    assert "output_crs" in coerced
    assert coerced["source"] == "@osm"
    assert coerced["output_crs"] == "EPSG:4326"
