"""Tests for security module — SubprocessHardener output redaction."""

from __future__ import annotations

import json
from pathlib import Path

from ecospheric_harness.security import SubprocessHardener


class TestSubprocessHardenerRedaction:
    """Tests for home-path redaction not corrupting structured output."""

    def test_home_path_redaction_preserves_json(self) -> None:
        """Regression: the home-path regex must not consume JSON structural chars."""
        home = str(Path.home())
        raw_json = json.dumps({
            "status": "success",
            "output_path": f"{home}/tmp/output.parquet",
            "count": 12,
        })

        hardener = SubprocessHardener()
        result = hardener.sanitize_output(raw_json, "")

        # JSON must still parse after redaction
        parsed = json.loads(result.stdout)

        # Home path IS redacted
        assert "~[REDACTED]" in result.stdout
        assert home not in result.stdout

        # Other fields survive intact
        assert parsed["status"] == "success"
        assert parsed["count"] == 12
        assert "output_path" in parsed

    def test_home_path_redaction_still_redacts(self) -> None:
        """Plain-text home paths are still redacted."""
        hardener = SubprocessHardener()
        result = hardener.sanitize_output(f"Writing to {Path.home()}/data/file.tif", "")
        assert str(Path.home()) not in result.stdout
        assert "~[REDACTED]" in result.stdout

    def test_stderr_redaction_preserves_json(self) -> None:
        """Same regression check for stderr."""
        home = str(Path.home())
        raw_json = json.dumps({
            "error": f"{home}/cache/bad",
            "code": 42,
        })

        hardener = SubprocessHardener()
        result = hardener.sanitize_output("", raw_json)

        parsed = json.loads(result.stderr)
        assert "~[REDACTED]" in result.stderr
        assert parsed["code"] == 42
