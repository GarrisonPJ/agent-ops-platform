"""Simple pub/sub event bus using asyncio.Queue.

Provides a lightweight publish/subscribe mechanism for streaming agent steps
from the background runner to the SSE endpoint (and potentially other
consumers) within the same process.
"""

from __future__ import annotations

import asyncio
import json
from collections import defaultdict


class EventBus:
    """In-process pub/sub bus keyed by trajectory ID.

    Each subscriber gets its own ``asyncio.Queue``.  Published events are
    broadcast to *all* current subscribers for the given trajectory.
    """

    def __init__(self) -> None:
        self._queues: dict[str, list[asyncio.Queue]] = defaultdict(list)

    def subscribe(self, trajectory_id: str) -> asyncio.Queue:
        """Register a new subscriber and return a queue to receive events on."""
        queue: asyncio.Queue = asyncio.Queue()
        self._queues[trajectory_id].append(queue)
        return queue

    def unsubscribe(self, trajectory_id: str, queue: asyncio.Queue) -> None:
        """Remove a subscriber queue."""
        try:
            self._queues[trajectory_id].remove(queue)
        except ValueError:
            return
        if not self._queues[trajectory_id]:
            del self._queues[trajectory_id]

    async def publish(self, trajectory_id: str, event: dict) -> None:
        """Publish an event to every subscriber of the given trajectory."""
        for queue in list(self._queues[trajectory_id]):
            await queue.put(event)


# Module-level singleton — imported by ``main.py`` and accessed from tests.
event_bus = EventBus()


async def stream_events(trajectory_id: str):
    """Async generator that yields SSE-formatted events for a trajectory.

    Subscribes to the event bus, yields each event as ``data: <json>\n\n``,
    and stops when a terminal event (done / error) is received.
    """
    queue = event_bus.subscribe(trajectory_id)
    try:
        while True:
            event = await queue.get()
            yield f"data: {json.dumps(event)}\n\n"
            if event.get("type") in ("done", "error"):
                break
    finally:
        event_bus.unsubscribe(trajectory_id, queue)
