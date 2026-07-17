"""Best-effort low-latency notifications for already-persisted events."""

from __future__ import annotations

import asyncio
from collections import defaultdict


class EventNotifier:
    def __init__(self) -> None:
        self._subscribers: dict[str, set[asyncio.Queue[dict]]] = defaultdict(set)

    def subscribe(self, run_id: str) -> asyncio.Queue[dict]:
        queue: asyncio.Queue[dict] = asyncio.Queue()
        self._subscribers[run_id].add(queue)
        return queue

    def unsubscribe(self, run_id: str, queue: asyncio.Queue[dict]) -> None:
        subscribers = self._subscribers.get(run_id)
        if not subscribers:
            return
        subscribers.discard(queue)
        if not subscribers:
            self._subscribers.pop(run_id, None)

    async def publish(self, run_id: str, events: list[dict]) -> None:
        for queue in tuple(self._subscribers.get(run_id, ())):
            for event in events:
                await queue.put(event)


event_notifier = EventNotifier()
