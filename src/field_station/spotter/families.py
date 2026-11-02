# SPDX-FileCopyrightText: 2026 Curtis Galloway
# SPDX-License-Identifier: Apache-2.0
"""Aircraft families: ICAO type codes that count as one type for rarity.

"Rarely seen here" is judged per family, so a Cessna 182 is not rare just
because most local Cessnas are 172s. A code not listed is its own family.
"""

from __future__ import annotations

_FAMILIES = {
    "Cessna singles": (
        "C120 C140 C150 C152 C162 C170 C172 C175 C177 C180 C182 C185 C188 C195 "
        "C205 C206 C207 C208 C210 C72R C77R C82R P210 T206 T210"
    ),
    "Cessna twins": "C303 C310 C320 C335 C340 C402 C404 C414 C421 C425 C441",
    "Citation jets": (
        "C500 C501 C510 C525 C25A C25B C25C C25M C550 C551 C560 C56X C650 C680 "
        "C68A C700 C750"
    ),
    "Piper singles": (
        "J3 PA12 PA18 PA20 PA22 PA24 PA25 PA28 P28A P28B P28R P28T PA32 P32R "
        "P32T PA46 P46T PA38"
    ),
    "Piper twins": "PA23 PA27 PA30 PA31 PA34 PA44 PAY1 PAY2 PAY3 PAY4",
    "Cirrus": "SR20 SR22 S22T",
    "Beechcraft Bonanza": "BE33 BE35 BE36",
    "Beechcraft twins": "BE55 BE58 BE60 BE76 BE95",
    "King Air": "BE9L BE9T BE10 BE20 BE30 B350",
    "Mooney": "M20P M20T M20J M20K M20R M20S M20V",
    "Diamond": "DA20 DA40 DA42 DA50 DA62",
    "Van's RV": "RV4 RV6 RV7 RV8 RV9 RV10 RV12 RV14",
    "Robinson helicopters": "R22 R44 R66",
    "Daher TBM": "TBM7 TBM8 TBM9",
    "Boeing 737": "B731 B732 B733 B734 B735 B736 B737 B738 B739 B37M B38M B39M B3XM",
    "Boeing 747": "B741 B742 B743 B744 B748 B74S",
    "Boeing 757": "B752 B753",
    "Boeing 767": "B762 B763 B764",
    "Boeing 777": "B772 B773 B77L B77W B778 B779",
    "Boeing 787": "B788 B789 B78X",
    "Airbus A320 family": "A318 A319 A320 A321 A19N A20N A21N",
    "Airbus A330": "A332 A333 A338 A339",
    "Airbus A350": "A359 A35K",
    "Embraer E-Jets": "E170 E175 E75L E75S E190 E195 E290 E295",
    "Bombardier CRJ": "CRJ1 CRJ2 CRJ7 CRJ9 CRJX",
    "Dash 8": "DH8A DH8B DH8C DH8D",
}

_BY_CODE = {
    code: family for family, codes in _FAMILIES.items() for code in codes.split()
}


def family(type_code: str) -> str:
    """The family name for a type code, or the code itself when unlisted."""
    return _BY_CODE.get(type_code.strip().upper(), type_code)
