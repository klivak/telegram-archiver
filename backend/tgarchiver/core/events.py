"""In-process event bus feeding the /ws/events WebSocket."""

from __future__ import annotations

import asyncio
import time
from typing import Any


class EventBus:
    def __init__(self, throttle_s: float = 0.25) -> None:
        self._subs: set[asyncio.Queue[dict[str, Any]]] = set()
        self._last: dict[str, float] = {}
        self.throttle_s = throttle_s

    def subscribe(self) -> asyncio.Queue[dict[str, Any]]:
        q: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=1000)
        self._subs.add(q)
        return q

    def unsubscribe(self, q: asyncio.Queue[dict[str, Any]]) -> None:
        self._subs.discard(q)

    def emit(self, type_: str, data: Any = None, *, throttle_key: str | None = None) -> None:
        """Publish an event. With throttle_key, drops events more frequent than throttle_s (progress spam)."""
        if throttle_key is not None:
            now = time.monotonic()
            if now - self._last.get(throttle_key, 0.0) < self.throttle_s:
                return
            self._last[throttle_key] = now
        ev = {"type": type_, "data": data, "ts": time.time()}
        for q in list(self._subs):
            try:
                q.put_nowait(ev)
            except asyncio.QueueFull:
                pass  # slow consumer; UI re-fetches state on reconnect
