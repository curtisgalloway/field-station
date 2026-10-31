# SPDX-FileCopyrightText: 2026 Curtis Galloway
# SPDX-License-Identifier: Apache-2.0
"""Display names for AIS ship types and the tens classes they fold into."""

from __future__ import annotations

_SHIP_TYPE_NAMES = {
    30: "fishing",
    31: "towing",
    32: "towing, large",
    33: "dredging or underwater ops",
    34: "diving ops",
    35: "military",
    36: "sailing",
    37: "pleasure craft",
    50: "pilot vessel",
    51: "search and rescue",
    52: "tug",
    53: "port tender",
    54: "anti-pollution",
    55: "law enforcement",
    58: "medical transport",
    59: "special craft",
}

_SHIP_CLASS_NAMES = {
    0: "unspecified",
    10: "reserved",
    20: "wing in ground",
    30: "fishing, towing and special",
    40: "high-speed craft",
    50: "pilot, rescue and port craft",
    60: "passenger",
    70: "cargo",
    80: "tanker",
    90: "other",
}


def ship_class_name(tens: int) -> str:
    """Returns the name of an AIS ship type class (the type folded into tens)."""
    return _SHIP_CLASS_NAMES.get(tens, "other")


def ship_type_name(ship_type: int | None) -> str | None:
    """Returns the name of an AIS ship type number, or None if unknown."""
    if ship_type is None:
        return None
    if ship_type in _SHIP_TYPE_NAMES:
        return _SHIP_TYPE_NAMES[ship_type]
    if not isinstance(ship_type, int) or ship_type < 0:
        return None
    return ship_class_name(ship_type // 10 * 10)
