# SPDX-FileCopyrightText: 2026 Curtis Galloway
# SPDX-License-Identifier: Apache-2.0
"""The spotter: reads full receiver documents, ranks contacts, picks one.

It taps each poller's parsed document in process, before the feed's caps
and field reductions, so it sees every contact and every field. Nothing it
computes (distance, bearing) reaches the WebSocket feed.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
import zoneinfo

from field_station import geo
from field_station.spotter import model
from field_station.spotter import names
from field_station.spotter import schedule
from field_station.spotter import score as score_lib
from field_station.spotter import store as store_lib
from field_station.spotter import table as table_lib
from field_station.text import clean_text

log = logging.getLogger("field_station.spotter")

TICK_SECONDS = 60.0


def _number(value) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def parse_aircraft(
    data: dict, receiver: tuple[float, float], now: float
) -> list[model.Sighting]:
    """Converts an aircraft.json document; skips aircraft with no position.

    Distance and bearing are computed from the receiver position.
    """
    rx_lat, rx_lon = receiver
    out = []
    for rec in data.get("aircraft", []):
        hex_id, lat, lon = (
            rec.get("hex"),
            _number(rec.get("lat")),
            _number(rec.get("lon")),
        )
        if not isinstance(hex_id, str) or not hex_id or lat is None or lon is None:
            continue
        seen = _number(rec.get("seen")) or 0.0
        km = geo.haversine_km(rx_lat, rx_lon, lat, lon)
        out.append(
            model.Sighting(
                kind="aircraft",
                key=hex_id.lower(),
                first_seen=now - seen,
                last_seen=now - seen,
                distance_nm=km / geo.KM_PER_NM,
                bearing_deg=geo.bearing_deg(rx_lat, rx_lon, lat, lon),
                raw=rec,
            )
        )
    return out


def parse_ships(data: dict, now: float) -> list[model.Sighting]:
    """Converts a ships.json document; skips ships with no distance.

    Uses AIS-catcher's own ``distance`` (nm) and ``bearing``.
    """
    out = []
    for rec in data.get("ships", []):
        mmsi, distance = rec.get("mmsi"), _number(rec.get("distance"))
        if mmsi is None or distance is None:
            continue
        seen = _number(rec.get("last_signal")) or 0.0
        out.append(
            model.Sighting(
                kind="vessel",
                key=str(mmsi),
                first_seen=now - seen,
                last_seen=now - seen,
                distance_nm=distance,
                bearing_deg=_number(rec.get("bearing")),
                raw=rec,
            )
        )
    return out


def _altitude_ft(raw: dict) -> float | None:
    alt = raw.get("alt_baro", raw.get("alt_geom"))
    if alt == "ground":
        return 0.0
    return _number(alt)


def _text(value) -> str | None:
    return clean_text(value if isinstance(value, str) else None)


def _year(value) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def fields(sighting: model.Sighting, score: float, reasons: list[str]) -> dict:
    """Returns the API fields of a scored sighting (docs/spotter.md)."""
    raw = sighting.raw
    out = {
        "kind": sighting.kind,
        "key": sighting.key,
        "ident": None,
        "registration": None,
        "type_code": None,
        "description": None,
        "operator": None,
        "country": None,
        "year": None,
        "ship_type": None,
        "length_m": None,
        "distance_nm": sighting.distance_nm,
        "bearing_deg": sighting.bearing_deg,
        "altitude_ft": None,
        "seen_at": sighting.seen_at,
        "score": score,
        "reasons": reasons,
    }
    if sighting.kind == "aircraft":
        out.update(
            ident=_text(raw.get("flight")),
            registration=_text(raw.get("r")),
            type_code=_text(raw.get("t")),
            description=_text(raw.get("desc")),
            operator=_text(raw.get("ownOp")),
            year=_year(raw.get("year")),
            altitude_ft=_altitude_ft(raw),
        )
    else:
        ship_type = raw.get("shiptype")
        ship_type = ship_type if isinstance(ship_type, int) else None
        out.update(
            ident=_text(raw.get("shipname")),
            description=names.ship_type_name(ship_type),
            country=_text(raw.get("country")),
            ship_type=ship_type,
            length_m=score_lib.vessel_length(raw),
        )
    return out


class Spotter:
    """Keeps the sighting table, the pick, and the optional history store."""

    def __init__(
        self,
        store: store_lib.Store | None,
        quiet: schedule.QuietHours,
        tz: zoneinfo.ZoneInfo,
        clock=time.time,
        seed_path: str | None = None,
    ) -> None:
        self.store = store
        self.seed_path = seed_path
        self.quiet = quiet
        self.tz = tz
        self._clock = clock
        self.table = table_lib.SightingTable()
        self.seq = 0
        self.changed_at = 0.0
        self.pick: dict | None = None

    def load(self) -> None:
        """Restores the pick, the no-repeat marks and type counts from the store.

        Then adds the seed file's type counts, if one is configured.
        """
        try:
            self._load_store()
        finally:
            self._load_seed()

    def _load_seed(self) -> None:
        """Adds estimated past type counts from the seed file.

        The seed (built from a public ADS-B archive, weighted by how likely
        this receiver was to hear each aircraft) fills days before our own
        history begins. A day the store already has is never overwritten, so
        real sightings always win.
        """
        if not self.seed_path:
            return
        try:
            with open(self.seed_path, encoding="utf-8") as fh:
                seed = json.load(fh)
            days = seed["days"]
            if not isinstance(days, dict):
                raise TypeError("seed 'days' is not an object")
        except FileNotFoundError:
            log.info("no spotter seed at %s", self.seed_path)
            return
        except (OSError, ValueError, KeyError, TypeError):
            log.exception("could not read spotter seed %s; ignoring it", self.seed_path)
            return
        history = self.table.history
        added = 0
        for day, counts in days.items():
            if day in history.days or not isinstance(counts, dict):
                continue
            history.days[day] = {
                str(k): float(v) for k, v in counts.items() if isinstance(v, (int, float))
            }
            added += 1
        history.prune(self._clock())
        log.info("spotter seed: added %d days from %s", added, self.seed_path)

    def _load_store(self) -> None:
        if self.store is None:
            return
        now = self._clock()
        try:
            self.seq = int(self.store.meta("seq") or 0)
            self.changed_at = float(self.store.meta("changed_at") or 0.0)
            pick = self.store.meta("pick")
            self.pick = json.loads(pick) if pick else None
            for kind, key, picked_at in self.store.recent_picks(
                now - table_lib.SHOWN_SECONDS
            ):
                self.table.shown[f"{kind}:{key}"] = picked_at
            if self.pick is not None:
                shown = f"{self.pick["kind"]}:{self.pick["key"]}"
                self.table.display = table_lib.Display(shown, self.changed_at)
            days = self.store.type_counts(now - table_lib.HISTORY_DAYS * 24 * 3600)
            for day, tkey, count in days:
                self.table.history.days.setdefault(day, {})[tkey] = count
        except (ValueError, KeyError, TypeError):
            log.exception("could not restore spotter state; starting empty")

    def observe_aircraft(self, data: dict, receiver: tuple[float, float]) -> None:
        """Takes the full aircraft.json document of one poll."""
        now = self._clock()
        self.table.merge(parse_aircraft(data, receiver, now), now)

    def observe_ships(self, data: dict) -> None:
        """Takes the full ships.json document of one poll."""
        now = self._clock()
        self.table.merge(parse_ships(data, now), now)

    def current(self) -> dict:
        return {"seq": self.seq, "changed_at": self.changed_at, "pick": self.pick}

    def ranked(self, limit: int = 10) -> list[dict]:
        """The best sightings right now, best first, scored as the pick is.

        Lets a display skip a pick it cannot show (no photo) and fall back to
        the next one down.
        """
        now = self._clock()
        scored = score_lib.rank(self.table, now)[:limit]
        return [fields(x.sighting, x.score, x.reasons) for x in scored]

    async def tick(self) -> schedule.Decision:
        """Stores what changed and decides whether the pick changes."""
        now = self._clock()
        self.table.prune(now)
        scored = score_lib.rank(self.table, now)
        top = scored[0] if scored else None
        decision = schedule.decide(now, top, self.table.display, self.quiet, self.tz)
        picked = None
        if decision.change:
            picked = top.sighting
            self.table.mark_shown(picked, now)
            self.seq += 1
            self.changed_at = now
            self.pick = fields(picked, top.score, top.reasons)
        if self.store is None:
            return decision
        dirty = [s for s in self.table.sightings.values() if s.dirty or s is picked]
        rows = []
        for sighting in dirty:
            # Stored scores leave out the repeat penalty: history ranks sightings
            # by what they were, not by whether they were already shown.
            plain = score_lib.score_sighting(sighting, self.table.history)
            row = fields(sighting, plain.score, plain.reasons)
            row.update(
                window_start=sighting.window_start,
                first_seen=sighting.first_seen,
                last_seen=sighting.last_seen,
            )
            rows.append(row)
            sighting.dirty = False
        meta = {
            "seq": str(self.seq),
            "changed_at": str(self.changed_at),
            "pick": json.dumps(self.pick),
        }
        key = None
        if picked is not None:
            key = (picked.kind, picked.key, picked.window_start)
        try:
            await asyncio.to_thread(self.store.commit, rows, now, key, meta)
        except Exception:
            for sighting in dirty:
                sighting.dirty = True
            raise
        return decision

    async def run(self) -> None:
        """Ticks once a minute until cancelled; a failed tick is logged."""
        while True:
            await asyncio.sleep(TICK_SECONDS)
            try:
                await self.tick()
            except Exception:  # pylint: disable=broad-exception-caught
                log.exception("spotter tick failed")

    def close(self) -> None:
        if self.store is not None:
            self.store.close()
