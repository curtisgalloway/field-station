# SPDX-FileCopyrightText: 2026 Curtis Galloway
# SPDX-License-Identifier: Apache-2.0
"""AIS poller.

Polls the ``ships.json`` served by AIS-catcher's built-in web server and
reduces it to classified vessel contacts with absolute positions (latitude
and longitude). AIS-catcher's own ``distance`` (nautical miles from the
receiver) is used only to rank contacts nearest-first and to word log
lines; neither it nor ``bearing`` is published, and neither is the
receiver's position.

Field check against a real capture (2026-10-21, 12 entries): every entry
carried numeric ``lat`` and ``lon`` along with ``distance`` and
``bearing``. The capture held no positionless entry, so how a ship heard
only from static data appears is unverified; this code drops an entry
whose ``lat`` or ``lon`` is absent, ``null`` or out of range (AIS "not
available" is 91 / 181), whichever form it takes.

Fail-soft: an unreachable receiver publishes available=False and keeps
retrying. Ships known only from static data carry no position and are
skipped.
"""

from __future__ import annotations

import asyncio
import logging
import time

import aiohttp

from field_station.text import clean_text

log = logging.getLogger("field_station.ais")

POLL_ACTIVE_SECONDS = 5.0  # ships on the scope; they move slowly
POLL_IDLE_SECONDS = 20.0  # empty sea
RETRY_SECONDS = 30.0
MAX_CONTACTS = 40

# AIS-catcher's ShippingClass enum (Source/Library/Common.h), folded to
# the four marks the scope draws. It derives shipclass itself from the
# transponder type and then overrides it from the vessel's declared AIS
# shiptype -- cargo 70-79, tanker 80-89, passenger 60-69, high-speed
# 40-49, special 50-59, fishing 30 -- which is why the terminal does not
# need the raw shiptype bands here. Two of its fallbacks are worth
# knowing: a Class B transponder with nothing better lands on CLASS_B,
# which is where sail (36) and pleasure craft (37) end up because
# AIS-catcher maps neither; and a Class A transponder with no static
# message yet lands on CLASS_UNKNOWN, which in practice means a ship
# whose name has not arrived rather than a genuinely unknown contact.
KIND_BY_SHIPCLASS = {
    0: "commercial",  # OTHER -- only reached via the ERI inland-waterway table
    1: "commercial",  # UNKNOWN -- Class A transponder, static message pending
    2: "commercial",  # CARGO
    4: "commercial",  # PASSENGER
    6: "commercial",  # TANKER
    7: "commercial",  # HIGHSPEED
    5: "working",  # SPECIAL -- pilot, tug, SAR, law enforcement, dredger
    8: "working",  # FISHING
    9: "working",  # PLANE -- SAR aircraft, heard on the sea band
    10: "working",  # HELICOPTER -- likewise
    3: "pleasure",  # CLASS_B -- the recreational fleet
    11: "mark",  # base station
    12: "mark",  # aid to navigation: a buoy is not a boat
    13: "mark",  # SART / EPIRB beacon
}
# Unrecognized or absent: draw it as a ship. Calling a freighter a yacht
# is the worse of the two mistakes on a screen people read as traffic.
DEFAULT_KIND = "commercial"
# The kinds that are actually vessels, and so belong in the counts.
VESSEL_KINDS = frozenset({"commercial", "working", "pleasure"})


def _located(entry: dict) -> tuple[float, float] | None:
    """Returns (lat, lon) when the entry carries a usable position."""
    lat, lon = entry.get("lat"), entry.get("lon")
    for value in (lat, lon):
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return None
    if lat == 0 and lon == 0:
        return None  # a transponder with no fix reports null island
    if not -90.0 <= lat <= 90.0 or not -180.0 <= lon <= 180.0:
        return None
    return lat, lon


def _rank_ships(data: dict) -> tuple[list[tuple[float, dict]], int]:
    """Returns ((distance_nm, contact) nearest-first, vessel-kind total)."""
    ranked = []
    total = 0
    for entry in data["ships"]:
        kind = KIND_BY_SHIPCLASS.get(entry.get("shipclass"), DEFAULT_KIND)
        if kind in VESSEL_KINDS:
            total += 1
        position = _located(entry)
        if position is None:
            continue  # static data only -- heard, but never located
        distance = entry.get("distance")
        if isinstance(distance, bool) or not isinstance(distance, (int, float)):
            continue  # unranked entries are dropped, as the terminal did
        contact = {
            "mmsi": entry.get("mmsi"),
            "name": clean_text(entry.get("shipname")),
            "sog_kt": entry.get("speed"),
            "cog": entry.get("cog"),
            "heading": entry.get("heading"),
            "lat": position[0],
            "lon": position[1],
            "seen_s": entry.get("last_signal"),
            "kind": kind,
        }
        ranked.append((distance, contact))
    ranked.sort(key=lambda pair: pair[0])
    # AIS-catcher's own "count" spans a longer-lived vessel database than
    # the list it serves here; the list is what the screen can actually
    # account for. Buoys and base stations are heard on the same band but
    # are not traffic, so they stay out of the "n of m vessels" readout.
    return ranked[:MAX_CONTACTS], total


def reduce_ships(data: dict) -> tuple[list[dict], int]:
    """Reduces a ships.json document to at most MAX_CONTACTS contacts.

    Entries without a position are dropped from the list but still count
    toward the total when they are vessels. AIS-catcher's ``distance`` is
    used only to rank nearest-first.

    Args:
      data: The parsed ships.json document.

    Returns:
      (contacts nearest-first with absolute positions, number of entries
      of a vessel kind, located or not).
    """
    ranked, total = _rank_ships(data)
    return [contact for _, contact in ranked], total


class AisPoller:
    """Polls ships.json and publishes {"type": "ais", ...} events."""

    def __init__(
        self, publish, url: str, on_log=None, clock=time.time, on_poll=None
    ) -> None:
        self._clock = clock
        self._on_poll = on_poll
        self.last_ok: float | None = None  # clock time of last good poll
        self._publish = publish
        self._url = url
        self._on_log = on_log
        self._status = {"type": "ais", "available": False, "vessels": []}
        self._known: set[int] = set()

    def status(self) -> dict:
        return dict(self._status)

    async def run(self) -> None:
        async with aiohttp.ClientSession() as session:
            while True:
                delay = await self._poll_once(session)
                await asyncio.sleep(delay)

    async def _poll_once(self, session: aiohttp.ClientSession) -> float:
        delay = await self._poll_inner(session)
        if self._on_poll is not None:
            await self._on_poll(self._status)
        return delay

    async def _poll_inner(self, session: aiohttp.ClientSession) -> float:
        try:
            async with session.get(
                self._url, timeout=aiohttp.ClientTimeout(total=10)
            ) as resp:
                resp.raise_for_status()
                data = await resp.json(content_type=None)
            ranked, total = _rank_ships(data)
        except (
            aiohttp.ClientError,
            asyncio.TimeoutError,
            LookupError,
            ValueError,
        ) as exc:
            if self._status.get("available"):
                log.warning("ships fetch failed: %s", exc)
            await self._set_status({"type": "ais", "available": False, "vessels": []})
            return RETRY_SECONDS

        contacts = [contact for _, contact in ranked]
        self.last_ok = self._clock()
        await self._log_new_vessels(ranked)
        await self._set_status(
            {
                "type": "ais",
                "available": True,
                "total": total,
                "vessels": contacts,
            }
        )
        return POLL_ACTIVE_SECONDS if contacts else POLL_IDLE_SECONDS

    async def _log_new_vessels(self, ranked: list[tuple[float, dict]]) -> None:
        """Log vessels appearing on the scope (not every position tick).

        Aids to navigation never move, so an arriving buoy is not news.
        """
        vessels = [(d, c) for d, c in ranked if c["kind"] in VESSEL_KINDS]
        current = {c["mmsi"] for _, c in vessels if c.get("mmsi")}
        fresh = [(d, c) for d, c in vessels if c.get("mmsi") in current - self._known]
        self._known = current
        if self._on_log is None or not fresh:
            return
        if len(fresh) > 4:
            await self._on_log("ais", f"{len(fresh)} new vessels")
            return
        for _, c in fresh:
            ident = c.get("name") or c["mmsi"]
            await self._on_log("ais", f"vessel {ident}")

    async def mark_down(self) -> None:
        """Publishes the source as unavailable (used when the poll loop dies)."""
        await self._set_status({"type": "ais", "available": False, "vessels": []})

    async def _set_status(self, status: dict) -> None:
        was = self._status.get("available")
        if status != self._status:
            self._status = status
            await self._publish(status)
        if self._on_log is not None and was != status["available"]:
            state = "online" if status["available"] else "offline"
            await self._on_log("ais", f"receiver {state}")
