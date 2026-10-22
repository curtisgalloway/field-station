# SPDX-FileCopyrightText: 2026 Curtis Galloway
# SPDX-License-Identifier: Apache-2.0
"""Tests for the ADS-B reduction and poller."""

import aiohttp

from field_station import adsb

RECEIVER = (37.80, -122.40)


def test_reduce_drops_stale_and_positionless(aircraft_fixture):
    contacts = adsb.reduce_aircraft(aircraft_fixture, RECEIVER)
    assert len(contacts) == 4
    assert "a00005" not in {c["hex"] for c in contacts}
    assert "a00006" not in {c["hex"] for c in contacts}


def test_reduce_absolute_fields(aircraft_fixture):
    first = adsb.reduce_aircraft(aircraft_fixture, RECEIVER)[0]
    assert first["lat"] == 37.79
    assert first["lon"] == -122.41
    assert "dist_km" not in first
    assert "bearing" not in first
    assert set(first) == {
      "hex", "flight", "alt_ft", "ground", "gs_kt", "track",
      "lat", "lon", "seen", "kind",
  }  # fmt: skip


def test_kinds(aircraft_fixture):
    by_hex = {c["hex"]: c for c in adsb.reduce_aircraft(aircraft_fixture, RECEIVER)}
    assert by_hex["a00001"]["kind"] == "airliner"
    assert by_hex["a00003"]["kind"] == "rotor"
    assert by_hex["a00002"]["kind"] == "other"


def test_ground(aircraft_fixture):
    by_hex = {c["hex"]: c for c in adsb.reduce_aircraft(aircraft_fixture, RECEIVER)}
    assert by_hex["a00004"]["ground"] is True
    assert by_hex["a00004"]["alt_ft"] == 0
    assert by_hex["a00001"]["ground"] is False


def test_cap_and_order():
    entries = [
        {"hex": f"{i:06x}", "lat": 37.80 + i * 0.01, "lon": -122.40, "seen_pos": 0}
        for i in range(30, 0, -1)
    ]
    contacts = adsb.reduce_aircraft({"aircraft": entries}, RECEIVER)
    assert len(contacts) == 24
    lats = [c["lat"] for c in contacts]
    assert lats == sorted(lats)
    assert contacts[0]["hex"] == "000001"


async def test_status_has_no_receiver(fake_adsb):
    published = []

    async def publish(event):
        published.append(event)

    poller = adsb.AdsbPoller(publish, fake_adsb.url("/data/aircraft.json"))
    async with aiohttp.ClientSession() as session:
        delay = await poller._poll_once(session)  # pylint: disable=protected-access
    assert delay == adsb.POLL_ACTIVE_SECONDS
    assert len(published) == 1
    event = published[0]
    assert event["type"] == "adsb"
    assert event["available"] is True
    assert event["total"] == 6
    assert "receiver" not in event
    assert len(event["aircraft"]) == 4
    assert poller.status() == event


async def test_logs_and_failure(fake_adsb):
    logs = []

    async def publish(event):
        del event

    async def on_log(source, text):
        logs.append((source, text))

    poller = adsb.AdsbPoller(
        publish, fake_adsb.url("/data/aircraft.json"), on_log=on_log
    )
    async with aiohttp.ClientSession() as session:
        await poller._poll_once(session)  # pylint: disable=protected-access
        fake_adsb.fail("/data/aircraft.json")
        delay = await poller._poll_once(session)  # pylint: disable=protected-access
    assert delay == adsb.RETRY_SECONDS
    assert ("adsb", "receiver online") in logs
    assert ("adsb", "receiver offline") in logs
    assert ("adsb", "contact SKW5502 · 2 nm") in logs
    assert poller.status()["available"] is False
