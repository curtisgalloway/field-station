# SPDX-FileCopyrightText: 2026 Curtis Galloway
# SPDX-License-Identifier: Apache-2.0
"""Tests for the aircraft trail store."""

from field_station.trails import TrailStore


def contact(hex_id: str, lat: float = 37.0, lon: float = -122.0) -> dict:
    return {"hex": hex_id, "lat": lat, "lon": lon}


def test_samples_every_4s():
    store = TrailStore()
    for t in (0.0, 2.0, 4.0):
        store.add([contact("abc123", lat=37.0 + t / 1000)], t)
    points = store.snapshot()["abc123"]
    assert len(points) == 2
    assert points[0][2] == 0
    assert points[1][2] == 4000


def test_snapshot_point_is_lat_lon_t_ms():
    store = TrailStore()
    store.add([contact("abc123", lat=37.5, lon=-122.25)], 10.0)
    assert store.snapshot() == {"abc123": [[37.5, -122.25, 10000]]}


def test_ages_out_after_120s():
    store = TrailStore()
    store.add([contact("abc123")], 0.0)
    store.add([], 119.0)
    assert len(store.snapshot()["abc123"]) == 1
    store.add([], 121.0)
    assert "abc123" not in store.snapshot()


def test_caps_at_32_points():
    store = TrailStore(life_s=1000.0, max_points=32)
    for i in range(40):
        store.add([contact("abc123")], i * 4.0)
    points = store.snapshot()["abc123"]
    assert len(points) == 32
    assert points[-1][2] == 39 * 4000


def test_departed_contact_keeps_trail_until_aged():
    store = TrailStore()
    store.add([contact("abc123")], 0.0)
    store.add([contact("zzz999")], 60.0)
    assert len(store.snapshot()["abc123"]) == 1
    store.add([], 121.0)
    assert "abc123" not in store.snapshot()


def test_skips_contacts_missing_hex_lat_or_lon():
    store = TrailStore()
    store.add(
        [
            {"lat": 37.0, "lon": -122.0},
            {"hex": "nolat", "lon": -122.0},
            {"hex": "nolon", "lat": 37.0},
        ],
        0.0,
    )
    assert store.snapshot() == {}


def test_heard_contact_keeps_only_recent_points():
    store = TrailStore()
    store.add([contact("abc123")], 0.0)
    store.add([contact("abc123")], 130.0)
    assert store.snapshot()["abc123"][0][2] == 130000
