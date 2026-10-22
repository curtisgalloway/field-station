# SPDX-FileCopyrightText: 2026 Curtis Galloway
# SPDX-License-Identifier: Apache-2.0
"""The service: WebSocket feed, health endpoints and CLI."""

import asyncio

import aiohttp
from aiohttp.test_utils import TestClient, TestServer
import pytest

from field_station import app as app_mod


class Clock:

    def __init__(self, now: float) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now


async def _until(predicate, timeout: float = 5.0) -> None:
    deadline = asyncio.get_running_loop().time() + timeout
    while not predicate():
        assert asyncio.get_running_loop().time() < deadline, "timed out"
        await asyncio.sleep(0.01)


async def _client(fake_adsb, fake_ais, clock):
    app = app_mod.make_app(
        fake_adsb.url("/data/aircraft.json"),
        fake_ais.url("/data/ships.json"),
        clock=clock,
    )
    client = TestClient(TestServer(app))
    await client.start_server()
    return client, app


async def test_ws_initial_burst_order(fake_adsb, fake_ais):
    client, app = await _client(fake_adsb, fake_ais, Clock(1000.0))
    try:
        await _until(
            lambda: app[app_mod.ADSB_KEY].status()["available"]
            and app[app_mod.AIS_KEY].status()["available"]
        )
        ws = await client.ws_connect("/ws")
        types = [(await ws.receive_json())["type"] for _ in range(4)]
        assert types == ["adsb", "ais", "trails", "log_history"]
        await ws.close()
    finally:
        await client.close()


async def test_trails_follow_adsb(fake_adsb, fake_ais, aircraft_fixture):
    clock = Clock(1000.0)
    client, app = await _client(fake_adsb, fake_ais, clock)
    try:
        poller = app[app_mod.ADSB_KEY]
        await _until(lambda: poller.status()["available"])
        clock.now = 1004.0
        for entry in aircraft_fixture["aircraft"]:
            if entry.get("hex") == "a00001":
                entry["lat"] += 0.01
        fake_adsb.bodies["/data/aircraft.json"] = aircraft_fixture
        async with aiohttp.ClientSession() as session:
            await poller._poll_once(session)  # pylint: disable=protected-access
        ws = await client.ws_connect("/ws")
        trails = None
        for _ in range(4):
            msg = await ws.receive_json()
            if msg["type"] == "trails":
                trails = msg["aircraft"]
        assert len(trails["a00001"]) == 2
        await ws.close()
    finally:
        await client.close()


async def test_healthz_one_source_down(fake_adsb, fake_ais):
    fake_ais.fail("/data/ships.json")
    client, app = await _client(fake_adsb, fake_ais, Clock(1000.0))
    try:
        await _until(lambda: app[app_mod.ADSB_KEY].status()["available"])
        await _until(lambda: fake_ais.hits.get("/data/ships.json", 0) >= 1)
        await asyncio.sleep(0.1)
        resp = await client.get("/healthz")
        assert resp.status == 503
        body = await resp.text()
        assert "ais" in body and "adsb" not in body
        ws = await client.ws_connect("/ws")
        assert (await ws.receive_json())["type"] == "adsb"
        await ws.close()
    finally:
        await client.close()


async def test_healthz_ok_and_statusz(fake_adsb, fake_ais):
    clock = Clock(1000.0)
    client, app = await _client(fake_adsb, fake_ais, clock)
    try:
        await _until(
            lambda: app[app_mod.ADSB_KEY].status()["available"]
            and app[app_mod.AIS_KEY].status()["available"]
        )
        resp = await client.get("/healthz")
        assert resp.status == 200
        assert (await resp.text()).strip() == "ok"
        ws = await client.ws_connect("/ws")
        await ws.receive_json()
        clock.now = 1007.0
        status = await (await client.get("/statusz")).json()
        assert status["clients"] == 1
        assert status["version"]
        assert status["sources"]["adsb"]["available"] is True
        assert status["sources"]["adsb"]["contacts"] > 0
        assert status["sources"]["ais"]["contacts"] > 0
        assert status["sources"]["adsb"]["seconds_since_poll"] == pytest.approx(7.0)
        await ws.close()
    finally:
        await client.close()


async def test_unconfigured_source_not_started(fake_adsb):
    app = app_mod.make_app(fake_adsb.url("/data/aircraft.json"), None)
    client = TestClient(TestServer(app))
    await client.start_server()
    try:
        await _until(lambda: app[app_mod.ADSB_KEY].status()["available"])
        assert app[app_mod.AIS_KEY] is None
        resp = await client.get("/healthz")
        assert resp.status == 200
        status = await (await client.get("/statusz")).json()
        assert list(status["sources"]) == ["adsb"]
        ws = await client.ws_connect("/ws")
        types = [(await ws.receive_json())["type"] for _ in range(3)]
        assert types == ["adsb", "trails", "log_history"]
        await ws.close()
    finally:
        await client.close()


def test_no_sources_refuses_start(monkeypatch):
    monkeypatch.delenv("FIELD_STATION_ADSB_URL", raising=False)
    monkeypatch.delenv("FIELD_STATION_AIS_URL", raising=False)
    with pytest.raises(SystemExit) as exc:
        app_mod.main([])
    assert exc.value.code != 0
