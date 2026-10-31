# SPDX-FileCopyrightText: 2026 Curtis Galloway
# SPDX-License-Identifier: Apache-2.0
"""Shared data types."""

from __future__ import annotations

import dataclasses
from typing import Literal

Kind = Literal["aircraft", "vessel"]

WINDOW_SECONDS = 3600


@dataclasses.dataclass
class Sighting:
    """One aircraft (by ICAO hex) or vessel (by MMSI) seen in the last hour.

    `raw` holds the closest observation's full receiver record. `seen_at` is
    when that closest pass happened. `window_start` is the start of the hour
    window the stored row belongs to; `dirty` marks changes not yet stored.
    """

    kind: Kind
    key: str
    first_seen: float
    last_seen: float
    distance_nm: float | None
    bearing_deg: float | None
    raw: dict
    seen_at: float | None = None
    window_start: int | None = None
    dirty: bool = dataclasses.field(default=True, compare=False)

    def __post_init__(self) -> None:
        if self.seen_at is None:
            self.seen_at = self.last_seen
        if self.window_start is None:
            self.window_start = int(self.first_seen // WINDOW_SECONDS) * WINDOW_SECONDS


@dataclasses.dataclass
class Scored:
    """A sighting with its interest score and human-readable reasons."""

    sighting: Sighting
    score: float
    reasons: list[str]
