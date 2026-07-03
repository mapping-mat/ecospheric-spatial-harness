"""Tests for command memory profiling (Phase 2.3)."""
from __future__ import annotations

from ecospheric_harness.command_profile import (
    CommandProfile,
    COMMAND_PROFILES,
    DEFAULT_PROFILE,
    get_profile,
    dtype_size,
    estimate_rss_bytes,
)


class TestGetProfile:
    def test_known_raster_command(self):
        p = get_profile("reproject", "raster")
        assert p.memory_class == "full_load"
        assert p.memory_multiplier == 3.0

    def test_known_vector_command(self):
        p = get_profile("buffer", "vector")
        assert p.memory_class == "full_load"
        assert p.memory_multiplier == 2.0

    def test_streaming_command(self):
        p = get_profile("slope", "raster")
        assert p.memory_class == "streaming"

    def test_unknown_command_returns_default(self):
        p = get_profile("nonexistent_cmd", "raster")
        assert p is DEFAULT_PROFILE
        assert p.memory_class == "full_load"
        assert p.memory_multiplier == 3.0

    def test_same_name_different_data_type(self):
        # clip raster is streaming, clip vector is full_load
        r = get_profile("clip", "raster")
        v = get_profile("clip", "vector")
        assert r.memory_class == "streaming"
        assert v.memory_class == "full_load"

    def test_case_insensitive(self):
        p1 = get_profile("Reproject", "Raster")
        p2 = get_profile("reproject", "raster")
        assert p1 is p2


class TestDtypeSize:
    def test_known_dtypes(self):
        assert dtype_size("float32") == 4
        assert dtype_size("float64") == 8
        assert dtype_size("int16") == 2
        assert dtype_size("uint8") == 1
        assert dtype_size("int64") == 8

    def test_none_defaults_to_4(self):
        assert dtype_size(None) == 4

    def test_unknown_defaults_to_4(self):
        assert dtype_size("custom_type") == 4


class TestEstimateRss:
    def test_raster_high_confidence(self):
        profile = CommandProfile("full_load", 3.0)
        envelope = {"data": {"data_type": "raster", "width": 1000, "height": 1000, "bands": 1, "dtype": "float32"}}
        estimate, confidence = estimate_rss_bytes(profile, envelope, file_size_bytes=4000000)
        assert estimate == 1000 * 1000 * 1 * 4 * 3  # 12MB
        assert confidence == "high"

    def test_raster_low_confidence_no_dtype(self):
        profile = CommandProfile("full_load", 3.0)
        envelope = {"data": {"data_type": "raster", "width": 1000, "height": 1000, "bands": 1}}
        estimate, confidence = estimate_rss_bytes(profile, envelope)
        assert estimate == 1000 * 1000 * 1 * 4 * 3  # uses default 4
        assert confidence == "low"

    def test_raster_missing_dims_fallback(self):
        profile = CommandProfile("full_load", 3.0)
        envelope = {"data": {"data_type": "raster"}}
        estimate, confidence = estimate_rss_bytes(profile, envelope, file_size_bytes=1000000)
        assert estimate == 3000000  # 1MB × 3
        assert confidence == "low"

    def test_vector_high_confidence(self):
        profile = CommandProfile("full_load", 2.0)
        envelope = {"data": {"data_type": "vector", "feature_count": 10000}}
        estimate, confidence = estimate_rss_bytes(profile, envelope)
        assert estimate == 10000 * 500 * 2  # 10MB
        assert confidence == "high"

    def test_vector_low_confidence_no_count(self):
        profile = CommandProfile("full_load", 2.0)
        envelope = {"data": {"data_type": "vector"}}
        estimate, confidence = estimate_rss_bytes(profile, envelope, file_size_bytes=2000000)
        assert estimate == 10000000  # 2MB × 5
        assert confidence == "low"

    def test_pointcloud(self):
        profile = CommandProfile("full_load", 3.0)
        envelope = {"data": {"data_type": "pointcloud"}}
        estimate, confidence = estimate_rss_bytes(profile, envelope, file_size_bytes=5000000)
        assert estimate == 15000000  # 5MB × 3
        assert confidence == "low"

    def test_unknown_data_type(self):
        profile = CommandProfile("full_load", 3.0)
        envelope = {"data": {"data_type": "unknown"}}
        estimate, confidence = estimate_rss_bytes(profile, envelope, file_size_bytes=1000000)
        assert estimate == 3000000
        assert confidence == "low"
