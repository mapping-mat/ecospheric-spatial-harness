"""Tests for SSE formatting helpers (web/sse.py)."""

from __future__ import annotations

import asyncio
import json

import pytest

from ecospheric_harness.web.sse import format_sse_event, QueueEventRelay


# ---------------------------------------------------------------------------
# 1. format_sse_event — basic structure
# ---------------------------------------------------------------------------


def test_format_sse_event_basic():
    """SSE string starts with 'event: {type}\\ndata:' and ends with '\\n\\n'."""
    result = format_sse_event("turn_start", {"step": 1})

    assert result.startswith("event: turn_start\ndata:")
    assert result.endswith("\n\n")


# ---------------------------------------------------------------------------
# 2. format_sse_event — data field is valid JSON
# ---------------------------------------------------------------------------


def test_format_sse_event_data_is_json():
    """The data payload line must be parseable JSON."""
    payload = {"step": 1, "tool": "edd", "nested": {"key": "value"}}
    result = format_sse_event("turn_start", payload)

    # Extract the data line (second line)
    lines = result.split("\n")
    data_line = lines[1]
    assert data_line.startswith("data: ")

    parsed = json.loads(data_line[len("data: "):])
    assert parsed == payload


# ---------------------------------------------------------------------------
# 3. format_sse_event — different event types
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "event_type, payload",
    [
        ("tool_call", {"tool": "edd", "args": {"query": "sentinel-2"}}),
        ("artifact", {"id": "art-001", "path": "/tmp/output.tif"}),
        ("done", {"status": "complete", "steps": 3}),
        ("error", {"message": "Timeout waiting for tile server"}),
    ],
)
def test_format_sse_event_different_types(event_type: str, payload: dict):
    """Each event type produces the correct header and valid JSON body."""
    result = format_sse_event(event_type, payload)

    assert result.startswith(f"event: {event_type}\ndata:")
    assert result.endswith("\n\n")

    # JSON must round-trip
    lines = result.split("\n")
    parsed = json.loads(lines[1][len("data: "):])
    assert parsed == payload


# ---------------------------------------------------------------------------
# 4. QueueEventRelay — basic push/drain
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_queue_relay_push_drain():
    """Push an event, drain it via async iteration."""
    relay = QueueEventRelay()
    relay.set_loop(asyncio.get_running_loop())

    relay.push("hello")
    relay.push(None)  # sentinel

    events = []
    async for event in relay:
        events.append(event)

    assert events == ["hello"]


# ---------------------------------------------------------------------------
# 5. QueueEventRelay — multiple pushes in order
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_queue_relay_multiple_pushes_ordered():
    """Multiple pushes are drained in FIFO order."""
    relay = QueueEventRelay()
    relay.set_loop(asyncio.get_running_loop())

    for msg in ["first", "second", "third"]:
        relay.push(msg)
    relay.push(None)

    events = []
    async for event in relay:
        events.append(event)

    assert events == ["first", "second", "third"]


# ---------------------------------------------------------------------------
# 6. QueueEventRelay — sentinel stops iteration
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_queue_relay_sentinel_stops():
    """None sentinel raises StopAsyncIteration."""
    relay = QueueEventRelay()
    relay.set_loop(asyncio.get_running_loop())

    relay.push(None)

    events = []
    async for event in relay:
        events.append(event)

    assert events == []


# ---------------------------------------------------------------------------
# 7. QueueEventRelay — thread-safe push from another thread
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_queue_relay_thread_safe_push():
    """Push from a worker thread, drain from async context."""
    import threading

    relay = QueueEventRelay()
    relay.set_loop(asyncio.get_running_loop())

    def worker():
        relay.push("from-thread")
        relay.push(None)

    t = threading.Thread(target=worker)
    t.start()

    events = []
    async for event in relay:
        events.append(event)

    t.join()
    assert events == ["from-thread"]
