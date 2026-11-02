# SPDX-FileCopyrightText: 2026 Curtis Galloway
# SPDX-License-Identifier: Apache-2.0
"""The one-hour sighting table and the history that scoring reads."""

from __future__ import annotations

import dataclasses
import datetime

from field_station.spotter import model

KEEP_SECONDS = 60 * 60
HISTORY_DAYS = 90
SHOWN_SECONDS = 24 * 60 * 60


def sighting_id(sighting: model.Sighting) -> str:
    """Returns the table key; hex and MMSI live in different spaces."""
    return f"{sighting.kind}:{sighting.key}"


def type_key(sighting: model.Sighting) -> str | None:
    """Returns the category rarity is counted over: type code or ship type."""
    if sighting.kind == "aircraft":
        return sighting.raw.get("t") or None
    shiptype = sighting.raw.get("shiptype")
    return None if shiptype is None else f"ship:{shiptype}"


def _day(ts: float) -> str:
    return datetime.datetime.fromtimestamp(ts, datetime.timezone.utc).strftime(
        "%Y-%m-%d"
    )


class TypeHistory:
    """Per-day counts of distinct sightings by type, kept for 90 days."""

    def __init__(self, days: dict[str, dict[str, float]] | None = None) -> None:
        # Counts are whole numbers from our own sightings, and fractional for
        # days filled from the seed file (expected contacts, not observed).
        self.days: dict[str, dict[str, float]] = days or {}

    def record(self, key: str, now: float) -> None:
        day = self.days.setdefault(_day(now), {})
        day[key] = day.get(key, 0) + 1

    def prune(self, now: float) -> None:
        cutoff = _day(now - HISTORY_DAYS * 24 * 3600)
        self.days = {d: c for d, c in self.days.items() if d > cutoff}

    def count(self, key: str) -> float:
        return sum(day.get(key, 0) for day in self.days.values())

    def count_where(self, keep) -> float:
        """Sightings whose type key satisfies `keep(key)`."""
        return sum(
            n for day in self.days.values() for key, n in day.items() if keep(key)
        )

    def days_covered(self) -> int:
        """Days from the first day with data to the last, inclusive (at least 1)."""
        if not self.days:
            return 1
        first = datetime.date.fromisoformat(min(self.days))
        last = datetime.date.fromisoformat(max(self.days))
        return (last - first).days + 1

    def total(self) -> float:
        return sum(sum(day.values()) for day in self.days.values())


@dataclasses.dataclass
class Display:
    """What the spotter currently has picked."""

    shown_id: str | None = None
    refreshed_at: float | None = None


class SightingTable:
    """Sightings of the last hour plus the history around them."""

    def __init__(self) -> None:
        self.sightings: dict[str, model.Sighting] = {}
        self.history = TypeHistory()
        self.shown: dict[str, float] = {}
        self.display = Display()

    def merge(self, observed: list[model.Sighting], now: float) -> None:
        """Folds a poll into the table, keeping the closest record per sighting."""
        for new in observed:
            sid = sighting_id(new)
            old = self.sightings.get(sid)
            if old is None:
                self.sightings[sid] = new
                tkey = type_key(new)
                if tkey:
                    self.history.record(tkey, now)
                continue
            old.first_seen = min(old.first_seen, new.first_seen)
            old.last_seen = max(old.last_seen, new.last_seen)
            old.dirty = True
            closer = new.distance_nm is not None and (
                old.distance_nm is None or new.distance_nm < old.distance_nm
            )
            if closer:
                old.distance_nm = new.distance_nm
                old.bearing_deg = new.bearing_deg
                old.seen_at = new.seen_at
                old.raw = new.raw
        self.prune(now)

    def prune(self, now: float) -> None:
        """Drops sightings unseen for 60 min, and expired history and shown marks."""
        self.sightings = {
            sid: s
            for sid, s in self.sightings.items()
            if now - s.last_seen <= KEEP_SECONDS
        }
        self.history.prune(now)
        self.shown = {
            sid: ts for sid, ts in self.shown.items() if now - ts < SHOWN_SECONDS
        }

    def mark_shown(self, sighting: model.Sighting, now: float) -> None:
        """Records that `sighting` became the pick (for the 24 h no-repeat rule)."""
        sid = sighting_id(sighting)
        self.shown[sid] = now
        self.display = Display(shown_id=sid, refreshed_at=now)

    def shown_recently(self, sighting: model.Sighting, now: float) -> bool:
        ts = self.shown.get(sighting_id(sighting))
        return ts is not None and now - ts < SHOWN_SECONDS
