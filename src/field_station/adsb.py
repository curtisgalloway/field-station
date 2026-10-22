# SPDX-FileCopyrightText: 2026 Curtis Galloway
# SPDX-License-Identifier: Apache-2.0
"""ADS-B poller.

Polls a readsb/tar1090 ``aircraft.json`` and reduces it to classified
contacts with absolute positions (latitude and longitude). The receiver
location from ``receiver.json`` next to the aircraft feed is used only to
rank contacts nearest-first and to word log lines; it is never published,
and neither bearing nor distance from the receiver appears in the feed.

Fail-soft: an unreachable receiver publishes available=False and keeps
retrying. Aircraft without a fresh position are counted but not listed.
"""

from __future__ import annotations

import asyncio
import logging
import time

import aiohttp

from field_station.geo import haversine_km

log = logging.getLogger("field_station.adsb")

POLL_ACTIVE_SECONDS = 2.0  # contacts in view: keep them moving
POLL_IDLE_SECONDS = 10.0  # empty sky
RETRY_SECONDS = 30.0
MAX_CONTACTS = 24
MAX_POSITION_AGE = 60.0  # seconds since last position fix

# ADS-B emitter category (broadcast by the aircraft, DO-260B 2.2.3.2.5.2)
# folded to the marks the scope draws. This is the airframe's own claim
# about its weight class, so it needs no database -- unlike registration
# and operator, which come from readsb's --db-file.
#   A1 light <15,500 lb        A2 small 15,500-75,000 lb
#   A3 large 75,000-300,000    A4 high-vortex large (B757)
#   A5 heavy >300,000 lb       A7 rotorcraft
# Deliberately unmapped, so they fall to "other": A0 (no information),
# A6 (high performance, >5g and >400 kt -- fast military, rare here),
# the B series (glider, balloon, parachutist, ultralight, UAV) and the
# C series (surface vehicles and obstacles). They are uncommon enough
# that giving each its own mark would cost more legibility than it buys.
KIND_BY_CATEGORY = {
    "A1": "light",
    "A2": "light",
    "A3": "airliner",
    "A4": "airliner",
    "A5": "airliner",
    "A7": "rotor",
}
# Not every aircraft broadcasts a category -- 6 of 94 in a sample taken
# 2026-08-10 -- so the fallback is load-bearing, not theoretical.
DEFAULT_AIRCRAFT_KIND = "other"


def reduce_aircraft(data: dict, receiver: tuple[float, float]) -> list[dict]:
    """Reduces an aircraft.json document to at most MAX_CONTACTS contacts.

    Entries without a position, or whose last fix is older than
    MAX_POSITION_AGE, are dropped. The receiver position is used only to
    sort nearest-first.

    Args:
      data: The parsed aircraft.json document.
      receiver: (lat, lon) of the receiver, for ordering only.

    Returns:
      Contacts nearest-first, with absolute positions.
    """
    rx_lat, rx_lon = receiver
    ranked = []
    for entry in data["aircraft"]:
        lat, lon = entry.get("lat"), entry.get("lon")
        if lat is None or lon is None:
            continue
        if float(entry.get("seen_pos", 0.0)) > MAX_POSITION_AGE:
            continue
        alt = entry.get("alt_baro", entry.get("alt_geom"))
        contact = {
            "hex": entry.get("hex"),
            "flight": (entry.get("flight") or "").strip() or None,
            "alt_ft": 0 if alt == "ground" else alt,
            "ground": alt == "ground",
            "gs_kt": entry.get("gs"),
            "track": entry.get("track"),
            "lat": lat,
            "lon": lon,
            "seen": entry.get("seen"),
            "kind": KIND_BY_CATEGORY.get(entry.get("category"), DEFAULT_AIRCRAFT_KIND),
        }
        ranked.append((haversine_km(rx_lat, rx_lon, lat, lon), contact))
    ranked.sort(key=lambda pair: pair[0])
    return [contact for _, contact in ranked[:MAX_CONTACTS]]


class AdsbPoller:
    """Polls aircraft.json and publishes {"type": "adsb", ...} events."""

    def __init__(self, publish, url: str, on_log=None) -> None:
        self._publish = publish
        self._url = url
        self._on_log = on_log
        self._receiver: tuple[float, float] | None = None
        self._status = {"type": "adsb", "available": False, "aircraft": []}
        self._last_messages: tuple[float, float] | None = None  # (time, count)
        self._known: set[str] = set()

    def status(self) -> dict:
        return dict(self._status)

    async def run(self) -> None:
        async with aiohttp.ClientSession() as session:
            while True:
                delay = await self._poll_once(session)
                await asyncio.sleep(delay)

    async def _fetch_receiver(self, session: aiohttp.ClientSession) -> None:
        url = self._url.rsplit("/", 1)[0] + "/receiver.json"
        async with session.get(url, timeout=aiohttp.ClientTimeout(total=10)) as resp:
            resp.raise_for_status()
            data = await resp.json(content_type=None)
        lat, lon = data.get("lat"), data.get("lon")
        if isinstance(lat, (int, float)) and isinstance(lon, (int, float)):
            self._receiver = (float(lat), float(lon))
        else:
            raise ValueError("receiver.json has no lat/lon")

    async def _poll_once(self, session: aiohttp.ClientSession) -> float:
        try:
            if self._receiver is None:
                await self._fetch_receiver(session)
            async with session.get(
                self._url, timeout=aiohttp.ClientTimeout(total=10)
            ) as resp:
                resp.raise_for_status()
                data = await resp.json(content_type=None)
            contacts = reduce_aircraft(data, self._receiver)
        except (
            aiohttp.ClientError,
            asyncio.TimeoutError,
            LookupError,
            ValueError,
        ) as exc:
            if self._status.get("available"):
                log.warning("aircraft fetch failed: %s", exc)
            await self._set_status({"type": "adsb", "available": False, "aircraft": []})
            return RETRY_SECONDS

        await self._log_new_contacts(contacts)
        now = time.monotonic()
        messages = float(data.get("messages", 0))
        rate = None
        if self._last_messages is not None:
            t0, m0 = self._last_messages
            if now > t0 and messages >= m0:
                rate = round((messages - m0) / (now - t0), 1)
        self._last_messages = (now, messages)
        await self._set_status(
            {
                "type": "adsb",
                "available": True,
                "total": len(data["aircraft"]),
                "msg_rate": rate,
                "aircraft": contacts,
            }
        )
        return POLL_ACTIVE_SECONDS if contacts else POLL_IDLE_SECONDS

    async def _log_new_contacts(self, contacts: list[dict]) -> None:
        """Log contacts appearing in view (not every position tick)."""
        current = {c["hex"] for c in contacts if c.get("hex")}
        fresh = [c for c in contacts if c.get("hex") in current - self._known]
        self._known = current
        if self._on_log is None or not fresh:
            return
        if len(fresh) > 4:
            await self._on_log("adsb", f"{len(fresh)} new contacts")
            return
        rx_lat, rx_lon = self._receiver
        for c in fresh:
            ident = c.get("flight") or c.get("hex")
            nm = haversine_km(rx_lat, rx_lon, c["lat"], c["lon"]) / 1.852
            await self._on_log("adsb", f"contact {ident} · {nm:.0f} nm")

    async def _set_status(self, status: dict) -> None:
        was = self._status.get("available")
        if status != self._status:
            self._status = status
            await self._publish(status)
        if self._on_log is not None and was != status["available"]:
            state = "online" if status["available"] else "offline"
            await self._on_log("adsb", f"receiver {state}")
