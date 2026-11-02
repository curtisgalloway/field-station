# SPDX-FileCopyrightText: 2026 Curtis Galloway
# SPDX-License-Identifier: Apache-2.0
"""The spotter service, its HTTP API, and its effect on the public feed."""

import asyncio
import datetime
import json
import zoneinfo

from aiohttp.test_utils import TestClient, TestServer
import pytest

from field_station import app as app_mod
from field_station.spotter import schedule
from field_station.spotter import service
from field_station.spotter import store as store_lib

TZ = zoneinfo.ZoneInfo("America/Los_Angeles")
QUIET = schedule.QuietHours.parse("22:00-07:00")
TOKEN = "fixture-token"
AUTH = {"Authorization": f"Bearer {TOKEN}"}
# A fake receiver position; the fixtures place contacts around it.
RECEIVER = (37.80, -122.40)


class Clock:

    def __init__(self, now: float) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now


def noon() -> float:
    return datetime.datetime(2026, 10, 31, 12, 0, tzinfo=TZ).timestamp()


async def _until(predicate, timeout: float = 5.0) -> None:
    deadline = asyncio.get_running_loop().time() + timeout
    while not predicate():
        assert asyncio.get_running_loop().time() < deadline, "timed out"
        await asyncio.sleep(0.01)


def typed(aircraft_doc: dict, ships_doc: dict | None = None):
    """Returns copies of the fixture documents with database fields filled in."""
    air = json.loads(json.dumps(aircraft_doc))
    for n, rec in enumerate(air["aircraft"]):
        rec.setdefault("t", "B738" if n % 2 else "E175")
    if ships_doc is None:
        return air
    ships = json.loads(json.dumps(ships_doc))
    for n, rec in enumerate(ships["ships"]):
        rec["shiptype"] = 70 + n
    return air, ships


def make_spotter(tmp_path, clock, with_db=True):
    store = store_lib.Store(tmp_path / "spotter.db") if with_db else None
    return service.Spotter(store, QUIET, TZ, clock)


async def started(app):
    client = TestClient(TestServer(app))
    await client.start_server()
    return client


def test_parse_aircraft_computes_distance_and_bearing(aircraft_fixture):
    found = service.parse_aircraft(aircraft_fixture, RECEIVER, 1000.0)
    by_key = {s.key: s for s in found}
    assert "a00006" not in by_key  # no position
    assert "a00005" in by_key  # stale fix: the feed drops it, the spotter keeps it
    north = service.parse_aircraft(
        {"aircraft": [{"hex": "AB", "lat": 37.90, "lon": -122.40}]}, RECEIVER, 0.0
    )[0]
    assert north.key == "ab"
    assert north.distance_nm == pytest.approx(6.0, abs=0.1)
    assert north.bearing_deg == pytest.approx(0.0, abs=0.01)
    east = service.parse_aircraft(
        {"aircraft": [{"hex": "ab", "lat": 37.80, "lon": -122.30}]}, RECEIVER, 0.0
    )[0]
    assert east.bearing_deg == pytest.approx(90.0, abs=0.1)


def test_parse_ships_uses_catcher_distance(ships_fixture):
    found = service.parse_ships(ships_fixture, 1000.0)
    by_key = {s.key: s for s in found}
    assert by_key["366000001"].distance_nm == 1.5
    assert by_key["366000001"].bearing_deg == 300
    assert by_key["366000001"].first_seen == 996.0
    assert "366000005" not in by_key  # static data only: no distance


def test_fields_map_receiver_data():
    raw = {
        "hex": "a1",
        "flight": "SKW1  ",
        "r": "N1AB",
        "t": "E175",
        "desc": "EMBRAER 175",
        "ownOp": "SAMPLE AIR",
        "year": "2010",
        "alt_baro": "ground",
    }
    s = service.parse_aircraft(
        {"aircraft": [dict(raw, lat=37.9, lon=-122.4)]}, RECEIVER, 5.0
    )[0]
    out = service.fields(s, 1.5, ["x"])
    assert (out["ident"], out["registration"], out["type_code"]) == (
        "SKW1",
        "N1AB",
        "E175",
    )
    assert (out["operator"], out["year"], out["altitude_ft"]) == (
        "SAMPLE AIR",
        2010,
        0.0,
    )
    ship = service.parse_ships(
        {
            "ships": [
                {
                    "mmsi": 1,
                    "distance": 2.0,
                    "bearing": 3,
                    "shiptype": 71,
                    "to_bow": 100,
                    "to_stern": 60,
                    "country": "Sampleland",
                    "shipname": "SAMPLE",
                }
            ]
        },
        5.0,
    )[0]
    out = service.fields(ship, 0.0, [])
    assert out["description"] == "cargo" and out["length_m"] == 160
    assert out["country"] == "Sampleland" and out["ship_type"] == 71


async def test_tick_picks_stores_and_serves(tmp_path, aircraft_fixture, ships_fixture):
    clock = Clock(noon())
    spotter = make_spotter(tmp_path, clock)
    spotter.observe_aircraft(aircraft_fixture, RECEIVER)
    spotter.observe_ships(ships_fixture)
    decision = await spotter.tick()
    assert decision.change
    cur = spotter.current()
    assert cur["seq"] == 1 and cur["changed_at"] == clock.now
    assert cur["pick"]["key"] and cur["pick"]["reasons"]
    again = await spotter.tick()
    assert not again.change and spotter.seq == 1
    stored = spotter.store.history({"since": "0", "limit": "200"})
    assert len(stored) == len(spotter.table.sightings)
    assert [r["key"] for r in stored if r["picked"]] == [cur["pick"]["key"]]
    spotter.close()


async def test_pick_survives_restart(tmp_path, aircraft_fixture):
    clock = Clock(noon())
    first = make_spotter(tmp_path, clock)
    first.observe_aircraft(typed(aircraft_fixture), RECEIVER)
    await first.tick()
    pick = first.current()
    first.close()
    second = make_spotter(tmp_path, clock)
    second.load()
    assert second.current() == pick
    assert (
        second.table.display.shown_id == f"{pick['pick']['kind']}:{pick['pick']['key']}"
    )
    assert second.table.shown
    assert second.table.history.total() > 0
    second.close()


async def test_quiet_hours_hold_the_pick(tmp_path, aircraft_fixture):
    midnight = datetime.datetime(2026, 10, 31, 23, 30, tzinfo=TZ).timestamp()
    spotter = make_spotter(tmp_path, Clock(midnight))
    spotter.observe_aircraft(aircraft_fixture, RECEIVER)
    assert not (await spotter.tick()).change
    assert spotter.current()["pick"] is None
    spotter.close()


async def test_memory_only_mode(aircraft_fixture):
    spotter = service.Spotter(None, QUIET, TZ, Clock(noon()))
    spotter.observe_aircraft(aircraft_fixture, RECEIVER)
    assert (await spotter.tick()).change
    assert spotter.current()["seq"] == 1


async def test_api_auth(tmp_path):
    for token, headers, expected in [
        (None, {}, 503),
        (None, AUTH, 503),
        (TOKEN, {}, 401),
        (TOKEN, {"Authorization": "Bearer nope"}, 401),
        (TOKEN, {"Authorization": TOKEN}, 401),
        (TOKEN, {"Authorization": "Basic " + TOKEN}, 401),
        (TOKEN, AUTH, 200),
    ]:
        spotter = make_spotter(tmp_path, Clock(noon()))
        app = app_mod.make_app(None, None, spotter=spotter, spotter_token=token)
        client = await started(app)
        try:
            for path in (
                "/spotter/current",
                "/spotter/ranked",
                "/spotter/history?since=0",
                "/spotter/counts?since=0&group=type_code",
            ):
                resp = await client.get(path, headers=headers)
                assert resp.status == expected, (token, headers, path)
        finally:
            await client.close()


async def test_api_history_and_counts(tmp_path, aircraft_fixture, ships_fixture):
    spotter = make_spotter(tmp_path, Clock(noon()))
    air, ships = typed(aircraft_fixture, ships_fixture)
    spotter.observe_aircraft(air, RECEIVER)
    spotter.observe_ships(ships)
    await spotter.tick()
    app = app_mod.make_app(None, None, spotter=spotter, spotter_token=TOKEN)
    client = await started(app)
    try:
        resp = await client.get("/spotter/current", headers=AUTH)
        assert (await resp.json())["pick"] is not None
        resp = await client.get("/spotter/history?since=0&limit=3", headers=AUTH)
        body = await resp.json()
        assert resp.status == 200 and len(body["sightings"]) == 3
        assert "first_seen" in body["sightings"][0] and "picked" in body["sightings"][0]
        resp = await client.get("/spotter/history", headers=AUTH)
        assert resp.status == 400
        resp = await client.get(
            "/spotter/counts?since=0&group=ship_class&kind=vessel", headers=AUTH
        )
        body = await resp.json()
        assert resp.status == 200 and body["counts"]
        assert set(body["counts"][0]) == {"group", "label", "contacts"}
        resp = await client.get("/spotter/counts?since=0", headers=AUTH)
        assert resp.status == 400
    finally:
        await client.close()


async def test_history_endpoints_503_without_database(aircraft_fixture):
    spotter = service.Spotter(None, QUIET, TZ, Clock(noon()))
    app = app_mod.make_app(None, None, spotter=spotter, spotter_token=TOKEN)
    client = await started(app)
    try:
        assert (await client.get("/spotter/current", headers=AUTH)).status == 200
        resp = await client.get("/spotter/history?since=0", headers=AUTH)
        assert resp.status == 503
        resp = await client.get("/spotter/counts?since=0&group=type_code", headers=AUTH)
        assert resp.status == 503
    finally:
        await client.close()


async def test_spotter_sees_full_documents_before_feed_caps(
    tmp_path, fake_adsb, fake_ais
):
    many = {
        "now": 0,
        "messages": 1,
        "aircraft": [
            {
                "hex": f"b{n:05d}",
                "lat": 37.80 + n * 0.001,
                "lon": -122.40,
                "seen_pos": 1.0,
                "seen": 1.0,
                "r": f"N{n}",
                "t": "B738",
                "dbFlags": 1,
            }
            for n in range(30)
        ],
    }
    fake_adsb.bodies["/data/aircraft.json"] = many
    spotter = make_spotter(tmp_path, Clock(noon()))
    app = app_mod.make_app(
        fake_adsb.url("/data/aircraft.json"),
        fake_ais.url("/data/ships.json"),
        clock=Clock(noon()),
        spotter=spotter,
        spotter_token=TOKEN,
    )
    client = await started(app)
    try:
        await _until(
            lambda: app[app_mod.ADSB_KEY].status()["available"]
            and app[app_mod.AIS_KEY].status()["available"]
        )
        assert len(app[app_mod.ADSB_KEY].status()["aircraft"]) == 24
        aircraft = [k for k in spotter.table.sightings if k.startswith("aircraft:")]
        vessels = [k for k in spotter.table.sightings if k.startswith("vessel:")]
        assert len(aircraft) == 30
        assert vessels
        sample = spotter.table.sightings["aircraft:b00000"]
        assert sample.raw["r"] == "N0" and sample.raw["dbFlags"] == 1
    finally:
        await client.close()


async def test_failing_hook_does_not_disturb_the_feed(fake_adsb, fake_ais):
    class Broken(service.Spotter):

        def observe_aircraft(self, data, receiver):
            raise RuntimeError("boom")

    spotter = Broken(None, QUIET, TZ, Clock(noon()))
    app = app_mod.make_app(
        fake_adsb.url("/data/aircraft.json"),
        fake_ais.url("/data/ships.json"),
        clock=Clock(1000.0),
        spotter=spotter,
    )
    client = await started(app)
    try:
        await _until(lambda: app[app_mod.ADSB_KEY].status()["available"])
    finally:
        await client.close()


async def _feed_messages(fake_adsb, fake_ais, spotter, token):
    app = app_mod.make_app(
        fake_adsb.url("/data/aircraft.json"),
        fake_ais.url("/data/ships.json"),
        clock=Clock(1000.0),
        spotter=spotter,
        spotter_token=token,
    )
    client = await started(app)
    try:
        await _until(
            lambda: app[app_mod.ADSB_KEY].status()["available"]
            and app[app_mod.AIS_KEY].status()["available"]
        )
        ws = await client.ws_connect("/ws")
        texts = [json.dumps(await ws.receive_json()) for _ in range(4)]
        await ws.close()
        return texts
    finally:
        await client.close()


async def test_feed_output_unchanged_with_spotter_enabled(
    tmp_path, fake_adsb, fake_ais
):
    plain = await _feed_messages(fake_adsb, fake_ais, None, None)
    spotter = make_spotter(tmp_path, Clock(1000.0))
    tapped = await _feed_messages(fake_adsb, fake_ais, spotter, TOKEN)
    assert spotter.table.sightings
    assert tapped == plain
    for text in tapped:
        for forbidden in ("distance", "bearing", "score", "reasons"):
            assert forbidden not in text


async def test_api_ranked(tmp_path, aircraft_fixture, ships_fixture):
    spotter = make_spotter(tmp_path, Clock(noon()))
    air, ships = typed(aircraft_fixture, ships_fixture)
    spotter.observe_aircraft(air, RECEIVER)
    spotter.observe_ships(ships)
    await spotter.tick()
    app = app_mod.make_app(None, None, spotter=spotter, spotter_token=TOKEN)
    client = await started(app)
    try:
        resp = await client.get("/spotter/ranked?limit=3", headers=AUTH)
        got = (await resp.json())["sightings"]
        assert resp.status == 200 and len(got) == 3
        scores = [s["score"] for s in got]
        assert scores == sorted(scores, reverse=True)
        assert {"kind", "key", "score", "reasons"} <= set(got[0])
        resp = await client.get("/spotter/ranked?limit=x", headers=AUTH)
        assert resp.status == 400
    finally:
        await client.close()
