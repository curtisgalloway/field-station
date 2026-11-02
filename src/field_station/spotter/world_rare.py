# SPDX-FileCopyrightText: 2026 Curtis Galloway
# SPDX-License-Identifier: Apache-2.0
"""Aircraft types that are rare in the world, not merely rarely seen here.

Hand-kept: low-production types, outsized freighters, and historic aircraft
of which only a few still fly. Separate from the local rarity count in
score.py, which knows only what this receiver has heard.
"""

from __future__ import annotations

WORLD_RARE_POINTS = 40

_TYPES = {
    # Outsized freighters, a handful of airframes each.
    "A124": "Antonov An-124",
    "AN22": "Antonov An-22",
    "A3ST": "Airbus Beluga",
    "A337": "Airbus BelugaXL",
    "BLCF": "Boeing 747 Dreamlifter",
    "SGUP": "Super Guppy",
    # Low-production military types.
    "B1": "Rockwell B-1 Lancer",
    "B2": "Northrop B-2 Spirit",
    "B52": "Boeing B-52",
    "U2": "Lockheed U-2",
    "C5M": "Lockheed C-5 Galaxy",
    "E3TF": "Boeing E-3 Sentry",
    "E3CF": "Boeing E-3 Sentry",
    "V22": "Bell Boeing V-22 Osprey",
    # Historic aircraft still flying.
    "B17": "Boeing B-17 Flying Fortress",
    "B25": "North American B-25 Mitchell",
    "P51": "North American P-51 Mustang",
    "SPIT": "Supermarine Spitfire",
    "CORS": "Vought F4U Corsair",
    "DC3": "Douglas DC-3",
    "CONI": "Lockheed Constellation",
    "JU52": "Junkers Ju 52",
}


def world_rare(type_code: str | None) -> str | None:
    """The type's name when it is rare in the world, else None."""
    return _TYPES.get((type_code or "").strip().upper())
