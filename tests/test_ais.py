# SPDX-FileCopyrightText: 2026 Curtis Galloway
# SPDX-License-Identifier: Apache-2.0
"""Tests for the AIS reduction and poller."""

import aiohttp

from field_station import ais


def test_positionless_dropped(ships_fixture):
    contacts, _ = ais.reduce_ships(ships_fixture)
    assert 366000005 not in {c["mmsi"] for c in contacts}
    assert len(contacts) == 5


def test_kinds(ships_fixture):
    contacts, _ = ais.reduce_ships(ships_fixture)
    by_mmsi = {c["mmsi"]: c for c in contacts}
    assert by_mmsi[366000001]["kind"] == "commercial"
    assert by_mmsi[366000002]["kind"] == "pleasure"
    assert by_mmsi[366000003]["kind"] == "working"
    assert by_mmsi[993000004]["kind"] == "mark"
    absent = {"ships": [{"mmsi": 1, "lat": 37.8, "lon": -122.4, "distance": 1}]}
    assert ais.reduce_ships(absent)[0][0]["kind"] == "commercial"


def _entry(**fields) -> dict:
    base = {"mmsi": 7, "shipclass": 2, "lat": 37.8, "lon": -122.4, "distance": 1}
    base.update(fields)
    return base


def test_null_island_dropped():
    contacts, total = ais.reduce_ships({"ships": [_entry(lat=0, lon=0)]})
    assert contacts == []
    assert total == 1


def test_bool_position_dropped():
    contacts, _ = ais.reduce_ships({"ships": [_entry(lat=True), _entry(lon=False)]})
    assert contacts == []


def test_missing_distance_dropped():
    ships = [_entry(distance=None), _entry(mmsi=8, distance=None)]
    del ships[1]["distance"]
    contacts, total = ais.reduce_ships({"ships": ships})
    assert contacts == []
    assert total == 2


def test_total_excludes_marks(ships_fixture):
    _, total = ais.reduce_ships(ships_fixture)
    assert total == 5


def test_absolute_fields(ships_fixture):
    contacts, _ = ais.reduce_ships(ships_fixture)
    first = contacts[0]
    assert first["mmsi"] == 993000004  # nearest by AIS-catcher distance
    assert first["lat"] == 37.805
    assert first["lon"] == -122.41
    assert set(first) == {
        "mmsi",
        "name",
        "sog_kt",
        "cog",
        "heading",
        "lat",
        "lon",
        "seen_s",
        "kind",
    }
    by_mmsi = {c["mmsi"]: c for c in contacts}
    assert by_mmsi[366000002]["name"] == "SAMPLE SLOOP"
    assert by_mmsi[366000006]["name"] is None


def test_cap_40():
    entries = [
        {"mmsi": i, "shipclass": 2, "lat": 37.8, "lon": -122.4, "distance": 50 - i}
        for i in range(1, 46)
    ]
    contacts, total = ais.reduce_ships({"ships": entries})
    assert len(contacts) == 40
    assert total == 45
    assert contacts[0]["mmsi"] == 45


async def test_poller_publishes_and_logs(fake_ais):
    published = []
    logs = []

    async def publish(event):
        published.append(event)

    async def on_log(source, text):
        logs.append((source, text))

    poller = ais.AisPoller(publish, fake_ais.url("/data/ships.json"), on_log=on_log)
    async with aiohttp.ClientSession() as session:
        delay = await poller._poll_once(session)  # pylint: disable=protected-access
        fake_ais.fail("/data/ships.json")
        retry = await poller._poll_once(session)  # pylint: disable=protected-access
    assert delay == ais.POLL_ACTIVE_SECONDS
    assert retry == ais.RETRY_SECONDS
    event = published[0]
    assert event["type"] == "ais"
    assert event["available"] is True
    assert event["total"] == 5
    assert set(event) == {"type", "available", "total", "vessels"}
    assert len(event["vessels"]) == 5
    assert ("ais", "receiver online") in logs
    assert ("ais", "receiver offline") in logs
    assert ("ais", "vessel SAMPLE SLOOP") in logs
    assert poller.status()["available"] is False


def test_shipname_is_sanitized(ships_fixture):
    dirty = ["\x1b[31mRED\x1b[0m", "A\nB\x00C", "x" * 100]
    for entry, name in zip(ships_fixture["ships"], dirty):
        entry["shipname"] = name
    names = [c["name"] for c in ais.reduce_ships(ships_fixture)[0]]
    assert "RED" in names and "A BC" in names and "x" * 32 in names
    assert all(n is None or len(n) <= 32 for n in names)
