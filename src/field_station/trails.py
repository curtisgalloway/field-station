# SPDX-FileCopyrightText: 2026 Curtis Galloway
# SPDX-License-Identifier: Apache-2.0
"""Aircraft trail history.

Keeps a short history of absolute positions per aircraft (hex), so the
scope can draw a decaying track behind each contact. Points are sampled
at most once per sample interval, age out after a fixed lifetime, and
are capped in count. Aging runs on every add for every known aircraft,
whether or not it was heard in that call, so a trail fades after its
contact drops off the feed rather than vanishing with it.
"""

from __future__ import annotations

Point = tuple[float, float, float]  # (lat, lon, t_seconds)


class TrailStore:
    """Per-aircraft position history, oldest point first."""

    def __init__(
        self,
        sample_s: float = 4.0,
        life_s: float = 120.0,
        max_points: int = 32,
    ) -> None:
        self._sample_s = sample_s
        self._life_s = life_s
        self._max_points = max_points
        self._trails: dict[str, list[Point]] = {}

    def add(self, contacts: list[dict], now: float) -> None:
        """Records one poll's contacts and ages every trail.

        Args:
          contacts: Aircraft dicts with ``hex``, ``lat`` and ``lon``.
            Entries missing any of the three are skipped.
          now: Current time in epoch seconds.
        """
        heard: set[str] = set()
        for contact in contacts:
            hex_id = contact.get("hex")
            lat = contact.get("lat")
            lon = contact.get("lon")
            if hex_id is None or lat is None or lon is None:
                continue
            heard.add(hex_id)
            points = self._trails.setdefault(hex_id, [])
            if not points or now - points[-1][2] >= self._sample_s:
                points.append((lat, lon, now))

        for hex_id in list(self._trails):
            points = self._trails[hex_id]
            while points and (
                now - points[0][2] > self._life_s or len(points) > self._max_points
            ):
                points.pop(0)
            if not points and hex_id not in heard:
                del self._trails[hex_id]

    def snapshot(self) -> dict[str, list[list[float]]]:
        """Returns every trail as hex -> [[lat, lon, t_ms], ...], oldest first."""
        return {
            hex_id: [[lat, lon, int(t * 1000)] for lat, lon, t in points]
            for hex_id, points in self._trails.items()
        }
