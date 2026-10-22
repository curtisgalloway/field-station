# SPDX-FileCopyrightText: 2026 Curtis Galloway
# SPDX-License-Identifier: Apache-2.0
"""Running log of interesting events.

A small ring buffer of {ts, source, text} entries. Producers call ``add``;
each entry is broadcast live as {"type": "log", "entry": ...} and the recent
history is replayed to new WebSocket clients as {"type": "log_history"}.
"""

from __future__ import annotations

from collections.abc import Callable
import logging
import time

log = logging.getLogger("field_station.eventlog")

HISTORY = 80


class EventLog:
    """Ring buffer plus broadcast fan-out for log entries."""

    def __init__(self, publish, clock: Callable[[], float] = time.time) -> None:
        self._publish = publish
        self._clock = clock
        self._entries: list[dict] = []

    def history(self) -> dict:
        return {"type": "log_history", "entries": list(self._entries)}

    async def add(self, source: str, text: str) -> None:
        entry = {"ts": self._clock(), "source": source, "text": text}
        self._entries.append(entry)
        del self._entries[:-HISTORY]
        log.info("[%s] %s", source, text)
        await self._publish({"type": "log", "entry": entry})
