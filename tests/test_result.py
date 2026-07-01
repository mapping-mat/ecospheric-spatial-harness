"""Tests for ecospheric_harness.result."""

from __future__ import annotations

from ecospheric_harness.result import PipelineResult, StepRecord


# ---------------------------------------------------------------------------
# StepRecord
# ---------------------------------------------------------------------------


class TestStepRecord:
    """StepRecord should support full construction and sensible defaults."""

    def test_construction_all_fields(self) -> None:
        record = StepRecord(
            step_number=1,
            tool="ogr",
            command="buffer",
            tool_ref="ogr_tool_obj",
            command_ref="buffer_cmd_obj",
            intent="buffer",
            params={"distance": 50},
            status="success",
            undone=False,
            envelope={"format": "geoparquet"},
            duration_ms=1200,
            is_search=False,
        )
        assert record.step_number == 1
        assert record.tool == "ogr"
        assert record.command == "buffer"
        assert record.tool_ref == "ogr_tool_obj"
        assert record.command_ref == "buffer_cmd_obj"
        assert record.intent == "buffer"
        assert record.params == {"distance": 50}
        assert record.status == "success"
        assert record.undone is False
        assert record.envelope == {"format": "geoparquet"}
        assert record.duration_ms == 1200
        assert record.is_search is False

    def test_construction_defaults(self) -> None:
        record = StepRecord(step_number=1, tool="ogr", command="buffer")
        assert record.tool_ref is None
        assert record.command_ref is None
        assert record.intent == ""
        assert record.params == {}
        assert record.status == ""
        assert record.undone is False
        assert record.envelope is None
        assert record.duration_ms == 0
        assert record.is_search is False

    def test_undone_toggling(self) -> None:
        record = StepRecord(step_number=1, tool="ogr", command="buffer")
        assert record.undone is False
        record.undone = True
        assert record.undone is True
        record.undone = False
        assert record.undone is False


# ---------------------------------------------------------------------------
# PipelineResult
# ---------------------------------------------------------------------------


class TestPipelineResult:
    """PipelineResult should aggregate steps and produce summaries."""

    def test_summary_with_3_steps(self) -> None:
        steps = [
            StepRecord(
                step_number=1, tool="ogr", command="buffer", status="success"
            ),
            StepRecord(
                step_number=2, tool="ogr", command="clip", status="success"
            ),
            StepRecord(
                step_number=3, tool="ogr", command="dissolve", status="error"
            ),
        ]
        pipeline = PipelineResult(
            steps=steps,
            final_artifact=None,
            provenance_chain=[
                {"correction": "re-ran clip with updated params"}
            ],
        )
        summary = pipeline.summary()
        assert "3 step(s)" in summary
        assert "2 successful" in summary
        assert "1 failed" in summary
        assert "Corrections applied: 1" in summary
        # No final artifact line
        assert "Final artifact" not in summary

    def test_summary_empty_steps(self) -> None:
        pipeline = PipelineResult(
            steps=[], final_artifact=None, provenance_chain=[]
        )
        summary = pipeline.summary()
        assert "0 step(s)" in summary
        assert "0 successful" in summary
        assert "0 failed" in summary
        assert "Corrections" not in summary
        assert "Final artifact" not in summary

    def test_summary_all_failed(self) -> None:
        steps = [
            StepRecord(
                step_number=1, tool="ogr", command="buffer", status="error"
            ),
            StepRecord(
                step_number=2, tool="ogr", command="clip", status="error"
            ),
        ]
        pipeline = PipelineResult(
            steps=steps, final_artifact=None, provenance_chain=[]
        )
        summary = pipeline.summary()
        assert "2 step(s)" in summary
        assert "0 successful" in summary
        assert "2 failed" in summary

    def test_summary_with_final_artifact_dict(self) -> None:
        steps = [
            StepRecord(
                step_number=1,
                tool="ogr",
                command="buffer",
                status="success",
            ),
        ]
        artifact = {"format": "geoparquet", "data_type": "polygon"}
        pipeline = PipelineResult(
            steps=steps,
            final_artifact=artifact,
            provenance_chain=[],
        )
        summary = pipeline.summary()
        assert (
            "Final artifact: format=geoparquet, data_type=polygon" in summary
        )

    def test_summary_with_final_artifact_object(self) -> None:
        """summary() should use getattr for non-dict artifacts."""

        class FakeArtifact:
            format = "geojson"
            data_type = "point"

        steps = [
            StepRecord(
                step_number=1,
                tool="ogr",
                command="centroid",
                status="success",
            ),
        ]
        pipeline = PipelineResult(
            steps=steps,
            final_artifact=FakeArtifact(),
            provenance_chain=[],
        )
        summary = pipeline.summary()
        assert (
            "Final artifact: format=geojson, data_type=point" in summary
        )
