# SPDX-FileCopyrightText: 2026 Curtis Galloway
# SPDX-License-Identifier: Apache-2.0
"""A malformed feed or a failing hook never stops a source."""

import asyncio
import copy

import pytest

from field_station import adsb as adsb_mod
from field_station import ais as ais_mod

AIRCRAFT = "/data/aircraft.json"
SHIPS = "/data/ships.json"


async def _until(predicate, timeout: float = 5.0) -> None:
    deadline = asyncio.get_running_loop().time() + timeout
    while not predicate():
        assert asyncio.get_running_loop().time() < deadline, "timed out"
        await asyncio.sleep(0.005)


def _bad_aircraft(good: dict) -> dict:
    return {
        "top-level array": [],
        "null seen_pos": {
            "aircraft": [{"hex": "bad001", "lat": 1.0, "lon": 2.0, "seen_pos": None}]
        },
        "non-dict entry": {"aircraft": ["oops"]},
        "aircraft not a list": {"aircraft": 5},
    }


def _bad_ships() -> dict:
    return {
        "top-level array": [],
        "list shipclass": {
            "ships": [{"mmsi": 1, "shipclass": [2], "lat": 1.0, "lon": 2.0}]
        },
        "non-dict entry": {"ships": [None]},
        "ships not a list": {"ships": 5},
    }


@pytest.fixture(autouse=True)
def fast_polls(monkeypatch):
    for mod in (adsb_mod, ais_mod):
        monkeypatch.setattr(mod, "POLL_ACTIVE_SECONDS", 0.01)
        monkeypatch.setattr(mod, "POLL_IDLE_SECONDS", 0.01)
        monkeypatch.setattr(mod, "RETRY_SECONDS", 0.01)


@pytest.mark.parametrize(
    "case",
    ["top-level array", "null seen_pos", "non-dict entry", "aircraft not a list"],
)
async def test_adsb_survives_malformed_feed(fake_adsb, aircraft_fixture, case):
    good = copy.deepcopy(aircraft_fixture)
    poller = adsb_mod.AdsbPoller(_noop, fake_adsb.url(AIRCRAFT), on_log=None)
    task = asyncio.create_task(poller.run())
    try:
        await _until(lambda: poller.status()["available"])
        fake_adsb.bodies[AIRCRAFT] = _bad_aircraft(good)[case]
        await _until(lambda: not poller.status()["available"])
        hits = fake_adsb.hits[AIRCRAFT]
        await _until(lambda: fake_adsb.hits[AIRCRAFT] > hits + 2)
        assert not task.done()
        fake_adsb.bodies[AIRCRAFT] = good
        await _until(lambda: poller.status()["available"])
    finally:
        task.cancel()


@pytest.mark.parametrize(
    "case", ["top-level array", "list shipclass", "non-dict entry", "ships not a list"]
)
async def test_ais_survives_malformed_feed(fake_ais, ships_fixture, case):
    good = copy.deepcopy(ships_fixture)
    poller = ais_mod.AisPoller(_noop, fake_ais.url(SHIPS))
    task = asyncio.create_task(poller.run())
    try:
        await _until(lambda: poller.status()["available"])
        fake_ais.bodies[SHIPS] = _bad_ships()[case]
        await _until(lambda: not poller.status()["available"])
        hits = fake_ais.hits[SHIPS]
        await _until(lambda: fake_ais.hits[SHIPS] > hits + 2)
        assert not task.done()
        fake_ais.bodies[SHIPS] = good
        await _until(lambda: poller.status()["available"])
    finally:
        task.cancel()


async def test_adsb_survives_failing_poll_hook(fake_adsb):
    calls = {"n": 0}

    async def on_poll(_status):
        calls["n"] += 1
        if calls["n"] <= 3:
            raise RuntimeError("hook failed")

    poller = adsb_mod.AdsbPoller(_noop, fake_adsb.url(AIRCRAFT), on_poll=on_poll)
    task = asyncio.create_task(poller.run())
    try:
        await _until(lambda: calls["n"] > 5)
        assert not task.done()
        await _until(lambda: poller.status()["available"])
    finally:
        task.cancel()


async def test_ais_survives_failing_poll_hook(fake_ais):
    calls = {"n": 0}

    async def on_poll(_status):
        calls["n"] += 1
        if calls["n"] <= 3:
            raise RuntimeError("hook failed")

    poller = ais_mod.AisPoller(_noop, fake_ais.url(SHIPS), on_poll=on_poll)
    task = asyncio.create_task(poller.run())
    try:
        await _until(lambda: calls["n"] > 5)
        assert not task.done()
        await _until(lambda: poller.status()["available"])
    finally:
        task.cancel()


async def _noop(_event):
    pass
