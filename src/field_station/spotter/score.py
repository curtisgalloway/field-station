# SPDX-FileCopyrightText: 2026 Curtis Galloway
# SPDX-License-Identifier: Apache-2.0
"""Interest scoring (docs/spotter.md, "Scoring"). All rules are additive."""

from __future__ import annotations

from field_station.spotter import model
from field_station.spotter import names
from field_station.spotter import table as table_lib

RARITY_MAX = 30.0
RARITY_MIN_TOTAL = 20
RARE_SHARE = 0.01
COMMON_SHARE = 0.10
CLOSE_NM = 5.0
CLOSE_MAX = 15.0
LOW_ALT_FT = 5000
SHOWN_PENALTY = -50.0
OVERHEAD_NM = 1.0
OVERHEAD_POINTS = 200
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


def rarity_points(type_id: str | None, history: table_lib.TypeHistory | None) -> float:
    """Returns 0..30; full marks at or below 1% of the 7-day sightings, none at 10%+."""
    if not type_id or history is None or history.total() < RARITY_MIN_TOTAL:
        return 0.0
    share = history.count(type_id) / history.total()
    frac = (COMMON_SHARE - share) / (COMMON_SHARE - RARE_SHARE)
    return RARITY_MAX * min(1.0, max(0.0, frac))


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
    if s.distance_nm is not None and s.distance_nm < OVERHEAD_NM:
        score += OVERHEAD_POINTS
        reasons.append((OVERHEAD_POINTS, "flew right over the house"))
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
        rare = rarity_points(tcode, history)
        if rare > 0:
            score += rare
            reasons.append((rare, f"rare type: {tcode}"))
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
    rare = rarity_points(table_lib.type_key(s), history)
    if rare > 0:
        score += rare
        reasons.append(
            (rare, f"rare type: {names.ship_type_name(shiptype) or shiptype}")
        )
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
