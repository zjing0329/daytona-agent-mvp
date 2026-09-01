from __future__ import annotations

import asyncio
import json
from collections import defaultdict, deque
from datetime import datetime
from typing import Any, AsyncIterator, Deque, Dict, List, Optional

from .models import EventRecord


class EventBus:
    def __init__(self, history_limit: int = 500):
        self._history_limit = history_limit
        self._history: Dict[str, Deque[EventRecord]] = defaultdict(
            lambda: deque(maxlen=self._history_limit)
        )
        self._subscribers: Dict[str, List[asyncio.Queue[EventRecord]]] = defaultdict(list)
        self._lock = asyncio.Lock()

    async def publish(self, session_id: str, event_type: str, data: Optional[Dict[str, Any]] = None) -> EventRecord:
        event = EventRecord(
            type=event_type,
            session_id=session_id,
            timestamp=datetime.utcnow(),
            data=data or {},
        )
        async with self._lock:
            self._history[session_id].append(event)
            subscribers = list(self._subscribers[session_id])
        for queue in subscribers:
            try:
                queue.put_nowait(event)
            except asyncio.QueueFull:
                pass
        return event

    async def history(self, session_id: str) -> List[EventRecord]:
        async with self._lock:
            return list(self._history.get(session_id, []))

    async def stream(self, session_id: str) -> AsyncIterator[str]:
        queue: asyncio.Queue[EventRecord] = asyncio.Queue(maxsize=100)
        async with self._lock:
            self._subscribers[session_id].append(queue)
            history = list(self._history.get(session_id, []))
        try:
            for event in history:
                yield self._format_sse(event)
            while True:
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=15)
                    yield self._format_sse(event)
                except asyncio.TimeoutError:
                    yield ": keep-alive\n\n"
        finally:
            async with self._lock:
                if queue in self._subscribers.get(session_id, []):
                    self._subscribers[session_id].remove(queue)

    @staticmethod
    def _format_sse(event: EventRecord) -> str:
        payload = event.model_dump(mode="json")
        return f"event: {event.type}\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"


event_bus = EventBus()
