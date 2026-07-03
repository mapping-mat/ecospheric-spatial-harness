"""Server-Sent Event helpers for the Ecospheric Spatial Harness web API."""

from __future__ import annotations

import asyncio
import json
from queue import Empty, Queue
from typing import Any


# -- SSE formatting -----------------------------------------------------------

def format_sse_event(event_type: str, data: dict[str, Any]) -> str:
    """Format a Server-Sent Event string.

    Args:
        event_type: The event name (e.g. ``"turn_start"``, ``"done"``).
        data: The payload dict to JSON-encode.

    Returns:
        A fully-terminated SSE string (``event: ...\\ndata: ...\\n\\n``).
    """
    return f"event: {event_type}\ndata: {json.dumps(data)}\n\n"


# -- Sync → async queue bridge -----------------------------------------------

class QueueEventRelay:
    """Bridges sync orchestrator events to an async SSE generator.

    A worker thread pushes formatted SSE strings (or ``None`` to signal
    completion) via :meth:`push`.  An async consumer drains the queue via
    ``async for event in relay``.

    Typical usage::

        relay = QueueEventRelay()
        relay.set_loop(asyncio.get_running_loop())

        def worker():
            relay.push(format_sse_event("turn_start", {...}))
            relay.push(format_sse_event("done", {"status": "complete"}))
            relay.push(None)  # sentinel

        loop.run_in_executor(None, worker)

        async for event in relay:
            yield event
    """

    def __init__(self) -> None:
        self._queue: Queue[str | None] = Queue()
        self._loop: asyncio.AbstractEventLoop | None = None

    def set_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        """Register the event loop (called from async context)."""
        self._loop = loop

    def push(self, event: str | None) -> None:
        """Thread-safe push of a formatted SSE string.

        Args:
            event: A formatted SSE string, or ``None`` to signal completion.
        """
        self._queue.put(event)

    def __aiter__(self) -> QueueEventRelay:
        return self

    async def __anext__(self) -> str:
        while True:
            try:
                item = await self._loop.run_in_executor(
                    None, self._queue.get, True, 0.1,
                )
            except Empty:
                continue
            if item is None:
                raise StopAsyncIteration
            return item