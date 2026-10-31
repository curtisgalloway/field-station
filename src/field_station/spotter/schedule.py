# SPDX-FileCopyrightText: 2026 Curtis Galloway
# SPDX-License-Identifier: Apache-2.0
"""When the pick changes (docs/spotter.md, "Choosing when the pick changes")."""

from __future__ import annotations

import dataclasses
import datetime
import zoneinfo

from field_station.spotter import model
from field_station.spotter import table as table_lib

INTERESTING_FLOOR = 40.0
MIN_INTERVAL = 15 * 60
IDLE_INTERVAL = 60 * 60


@dataclasses.dataclass(frozen=True)
class QuietHours:
    """A daily window, in local time, during which the pick never changes."""

    start: datetime.time
    end: datetime.time

    @classmethod
    def parse(cls, text: str) -> QuietHours:
        """Parses "HH:MM-HH:MM"."""
        try:
            start, end = text.split("-")
            return cls(
                datetime.time.fromisoformat(start.strip()),
                datetime.time.fromisoformat(end.strip()),
            )
        except ValueError as err:
            raise ValueError(f"bad quiet hours {text!r}; want HH:MM-HH:MM") from err

    def contains(self, local: datetime.time) -> bool:
        """Returns True if `local` is inside the window; it may wrap midnight."""
        if self.start == self.end:
            return False
        if self.start < self.end:
            return self.start <= local < self.end
        return local >= self.start or local < self.end


@dataclasses.dataclass(frozen=True)
class Decision:
    """Whether to change the pick, and a short reason for the log."""

    change: bool
    reason: str


def is_quiet(now: float, quiet: QuietHours, tz: zoneinfo.ZoneInfo) -> bool:
    local = datetime.datetime.fromtimestamp(now, tz).time()
    return quiet.contains(local)


def decide(
    now: float,
    top: model.Scored | None,
    display: table_lib.Display,
    quiet: QuietHours,
    tz: zoneinfo.ZoneInfo,
    floor: float = INTERESTING_FLOOR,
) -> Decision:
    """Decides whether to change the pick now; `top` is the best sighting."""
    if is_quiet(now, quiet, tz):
        return Decision(False, "quiet hours")
    if top is None:
        return Decision(False, "nothing seen")
    if table_lib.sighting_id(top.sighting) == display.shown_id:
        return Decision(False, "top sighting already picked")
    elapsed = (
        float("inf") if display.refreshed_at is None else now - display.refreshed_at
    )
    if top.score >= floor:
        if elapsed >= MIN_INTERVAL:
            return Decision(True, "interesting sighting")
        return Decision(False, "interesting, but under 15 min since last change")
    if elapsed >= IDLE_INTERVAL:
        return Decision(True, "new top sighting, hourly change")
    return Decision(False, "not interesting, under 60 min since last change")
