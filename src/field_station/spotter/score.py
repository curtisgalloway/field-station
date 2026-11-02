# SPDX-FileCopyrightText: 2026 Curtis Galloway
# SPDX-License-Identifier: Apache-2.0
"""Interest scoring (docs/spotter.md, "Scoring"). All rules are additive."""

from __future__ import annotations

import math

from field_station.spotter import families
from field_station.spotter import model
from field_station.spotter import names
from field_station.spotter import table as table_lib
from field_station.spotter import world_rare

RARITY_MAX = 60.0
RARITY_MIN_TOTAL = 20
COMMON_PER_WEEK = 1.0  # seen this often or more: no points
RARE_PER_WEEK = 1 / 18  # about once in four months or less: full points
CLOSE_NM = 5.0
CLOSE_MAX = 15.0
LOW_ALT_FT = 5000
SHOWN_PENALTY = -50.0
EMERGENCY_SQUAWKS = ("7500", "7600", "7700")

# shiptype -> (points, reason); a vessel has one type, so these never stack.
_SHIP_TYPES = {
    35: (60, "military vessel"),
    51: (40, "search and rescue"),
    55: (35, "law enforcement"),
    58: (30, "medical transport"),
    33: (25, "dredging"),
    34: (25, "diving ops"),
    31: (20, "towing"),
    32: (20, "towing"),
    52: (15, "tug"),
    36: (5, "sailing"),
    37: (5, "pleasure craft"),
}

Reasons = list[tuple[float, str]]


def seen_count(type_id: str, history: table_lib.TypeHistory) -> int:
    """Other contacts of this type's family (or ship type) in the history.

    The history already counts the sighting being scored; it is left out, so
    a first visit counts 0.
    """
    if type_id.startswith("ship:"):
        n = history.count(type_id)
    else:
        fam = families.family(type_id)
        n = history.count_where(
            lambda key: not key.startswith("ship:") and families.family(key) == fam
        )
    return max(0, n - 1)


def per_week(type_id: str, history: table_lib.TypeHistory) -> float:
    """How often this family typically turns up here, per week."""
    return seen_count(type_id, history) * 7 / history.days_covered()


def rarity_points(type_id: str | None, history: table_lib.TypeHistory | None) -> float:
    """Returns 0..60 from how often this family is typically seen here.

    This is the base of the score: how unexpected the contact is at this
    receiver, estimated from up to 90 days of history. Rare in the world is a
    separate rule (world_rare). Log scale, since rates run from dozens a day to
    a few a year: none at weekly or more, about half at monthly, full at about
    once in four months or less (or never). Off until the history holds 20
    contacts.
    """
    if not type_id or history is None or history.total() < RARITY_MIN_TOTAL:
        return 0.0
    rate = per_week(type_id, history)
    if rate <= RARE_PER_WEEK:
        return RARITY_MAX
    frac = math.log(COMMON_PER_WEEK / rate) / math.log(COMMON_PER_WEEK / RARE_PER_WEEK)
    return RARITY_MAX * min(1.0, max(0.0, frac))


def how_often(type_id: str, history: table_lib.TypeHistory) -> str:
    """Plain words for the typical rate: "about 2 a month"."""
    if seen_count(type_id, history) == 0:
        days = history.days_covered()
        return "none before today" if days == 1 else f"none in the last {days} days"
    rate = per_week(type_id, history)
    if rate >= 1:
        return f"about {round(rate)} a week"
    if rate * 52 / 12 >= 1:
        return f"about {round(rate * 52 / 12)} a month"
    return f"about {max(1, round(rate * 52))} a year"


def rarity_reason(label: str, type_id: str, history: table_lib.TypeHistory) -> str:
    return f"rarely seen here: {label}, {how_often(type_id, history)}"


def closeness_points(distance_nm: float | None) -> float:
    """Returns 0..15, linear from 15 at 0 nm down to 0 at 5 nm."""
    if distance_nm is None or distance_nm >= CLOSE_NM:
        return 0.0
    return CLOSE_MAX * (1.0 - distance_nm / CLOSE_NM)


def _aircraft_year(raw: dict) -> int | None:
    try:
        return int(raw.get("year"))
    except (TypeError, ValueError):
        return None


def _is_low(raw: dict) -> bool:
    alt = raw.get("alt_baro")
    if alt == "ground":
        return True
    return isinstance(alt, (int, float)) and alt <= LOW_ALT_FT


def _score_aircraft(
    s: model.Sighting, history: table_lib.TypeHistory | None
) -> tuple[float, Reasons]:
    raw = s.raw
    score = 0.0
    reasons: Reasons = []
    emergency = raw.get("emergency")
    squawk = str(raw.get("squawk", ""))
    if (emergency and emergency != "none") or squawk in EMERGENCY_SQUAWKS:
        score += 100
        what = emergency if emergency not in (None, "none") else squawk
        reasons.append((100, f"emergency: {what}"))
    flags = raw.get("dbFlags") or 0
    if not isinstance(flags, int):
        flags = 0
    if flags & 1:
        score += 60
        reasons.append((60, "military"))
    if flags & 2:
        score += 40
        reasons.append((40, "interesting airframe"))
    category = raw.get("category")
    category = category if isinstance(category, str) else ""
    if category.startswith("B"):
        score += 35
        reasons.append((35, "glider, balloon or drone"))
    elif category == "A7":
        score += 25
        reasons.append((25, "helicopter"))
    elif category == "A5":
        score += 15
        reasons.append((15, "heavy"))
    year = _aircraft_year(raw)
    if year is not None and year < 1980:
        score += 30
        reasons.append((30, f"built {year}"))
    tcode = raw.get("t")
    if not tcode:
        score -= 10
        reasons.append((-10, "no type info"))
    else:
        world = world_rare.world_rare(tcode)
        if world:
            score += world_rare.WORLD_RARE_POINTS
            reasons.append(
                (world_rare.WORLD_RARE_POINTS, f"rare in the world: {world}")
            )
        rare = rarity_points(tcode, history)
        if rare > 0:
            score += rare
            reasons.append(
                (rare, rarity_reason(families.family(tcode), tcode, history))
            )
    if _is_low(raw):
        close = closeness_points(s.distance_nm)
        if close > 0:
            score += close
            reasons.append((close, f"close: {s.distance_nm:.1f} nm"))
    return score, reasons


def _score_vessel(
    s: model.Sighting, history: table_lib.TypeHistory | None
) -> tuple[float, Reasons]:
    raw = s.raw
    score = 0.0
    reasons: Reasons = []
    shiptype = raw.get("shiptype")
    if shiptype in _SHIP_TYPES:
        points, reason = _SHIP_TYPES[shiptype]
        score += points
        reasons.append((points, reason))
    elif isinstance(shiptype, int) and 60 <= shiptype <= 69:
        score += 20
        reasons.append((20, "passenger vessel"))
    elif isinstance(shiptype, int) and 70 <= shiptype <= 89:
        score += 15
        reasons.append((15, "cargo or tanker"))
    length = vessel_length(raw)
    if length is not None and length > 150:
        score += 25
        reasons.append((25, f"large: {length} m"))
    tkey = table_lib.type_key(s)
    rare = rarity_points(tkey, history)
    if rare > 0:
        score += rare
        label = names.ship_type_name(shiptype) or str(shiptype)
        reasons.append((rare, rarity_reason(label, tkey, history)))
    close = closeness_points(s.distance_nm)
    if close > 0:
        score += close
        reasons.append((close, f"close: {s.distance_nm:.1f} nm"))
    return score, reasons


def vessel_length(raw: dict) -> int | None:
    """Returns the vessel length in meters from its AIS dimensions, if known."""
    parts = [raw.get("to_bow"), raw.get("to_stern")]
    length = int(sum(p for p in parts if isinstance(p, (int, float))))
    return length if length > 0 else None


def score_sighting(
    sighting: model.Sighting,
    history: table_lib.TypeHistory | None = None,
    shown_recently: bool = False,
) -> model.Scored:
    """Scores one sighting; `shown_recently` applies the 24 h no-repeat penalty.

    Reasons come back most points first.
    """
    if sighting.kind == "aircraft":
        score, reasons = _score_aircraft(sighting, history)
    else:
        score, reasons = _score_vessel(sighting, history)
    if shown_recently:
        score += SHOWN_PENALTY
        reasons.append((SHOWN_PENALTY, "shown in last 24 h"))
    reasons.sort(key=lambda pair: -pair[0])
    return model.Scored(
        sighting=sighting, score=score, reasons=[text for _, text in reasons]
    )


def rank(table: table_lib.SightingTable, now: float) -> list[model.Scored]:
    """Scores every sighting in the table, best first (ties: closest, then key)."""
    scored = [
        score_sighting(s, table.history, table.shown_recently(s, now))
        for s in table.sightings.values()
    ]
    scored.sort(
        key=lambda x: (
            -x.score,
            x.sighting.distance_nm if x.sighting.distance_nm is not None else 1e9,
            x.sighting.key,
        )
    )
    return scored
