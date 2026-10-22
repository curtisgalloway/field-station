# SPDX-FileCopyrightText: 2026 Curtis Galloway
# SPDX-License-Identifier: Apache-2.0
"""The field-station service.

Serves one WebSocket feed (``/ws``) of classified ADS-B and AIS contacts
with absolute positions, plus ``/healthz`` and ``/statusz``. A source with
no URL is not started and is absent from the feed's health reporting.
"""

from __future__ import annotations

import argparse
import asyncio
from collections.abc import Callable
import contextlib
import importlib.metadata
import json
import logging
import os
import time

from aiohttp import web

from field_station.adsb import AdsbPoller
from field_station.ais import AisPoller
from field_station.eventlog import EventLog
from field_station.trails import TrailStore

log = logging.getLogger("field_station.app")

ADSB_KEY = web.AppKey("adsb", AdsbPoller | None)
AIS_KEY = web.AppKey("ais", AisPoller | None)
TRAILS_KEY = web.AppKey("trails", TrailStore)
LOG_KEY = web.AppKey("log", EventLog)
CLIENTS_KEY = web.AppKey("clients", set)
CLOCK_KEY = web.AppKey("clock", object)
LAST_OK_KEY = web.AppKey("last_ok", dict)
TASKS_KEY = web.AppKey("tasks", list)

# Event field that holds each source's contact list.
CONTACTS_FIELD = {"adsb": "aircraft", "ais": "vessels"}


def _sources(app: web.Application) -> dict:
    """Returns the configured pollers by source name."""
    found = {"adsb": app[ADSB_KEY], "ais": app[AIS_KEY]}
    return {name: poller for name, poller in found.items() if poller is not None}


async def broadcast(app: web.Application, event: dict) -> None:
    payload = json.dumps(event)
    for ws in list(app[CLIENTS_KEY]):
        try:
            await ws.send_str(payload)
        except (ConnectionError, RuntimeError):
            app[CLIENTS_KEY].discard(ws)


async def ws_handler(request: web.Request) -> web.WebSocketResponse:
    ws = web.WebSocketResponse(heartbeat=30)
    await ws.prepare(request)
    app = request.app
    # Registered only after the initial burst is sent, so a live event
    # cannot overtake it and arrive out of order.
    for poller in _sources(app).values():
        await ws.send_json(poller.status())
    await ws.send_json({"type": "trails", "aircraft": app[TRAILS_KEY].snapshot()})
    await ws.send_json(app[LOG_KEY].history())
    app[CLIENTS_KEY].add(ws)
    try:
        async for _ in ws:
            pass  # inbound messages are ignored
    finally:
        app[CLIENTS_KEY].discard(ws)
    return ws


async def healthz(request: web.Request) -> web.Response:
    down = [
        name
        for name, poller in _sources(request.app).items()
        if not poller.status().get("available")
    ]
    if down:
        return web.Response(status=503, text="unavailable: " + ", ".join(down) + "\n")
    return web.Response(text="ok\n")


async def statusz(request: web.Request) -> web.Response:
    app = request.app
    now = app[CLOCK_KEY]()
    sources = {}
    for name, poller in _sources(app).items():
        status = poller.status()
        last_ok = app[LAST_OK_KEY].get(name)
        sources[name] = {
            "available": bool(status.get("available")),
            "contacts": len(status.get(CONTACTS_FIELD[name], [])),
            "seconds_since_poll": None if last_ok is None else now - last_ok,
        }
    try:
        version = importlib.metadata.version("field-station")
    except importlib.metadata.PackageNotFoundError:
        version = "unknown"
    return web.json_response(
        {"sources": sources, "clients": len(app[CLIENTS_KEY]), "version": version}
    )


async def _start(app: web.Application) -> None:
    for poller in _sources(app).values():
        app[TASKS_KEY].append(asyncio.create_task(poller.run()))


async def _stop(app: web.Application) -> None:
    for task in app[TASKS_KEY]:
        task.cancel()
    for task in app[TASKS_KEY]:
        with contextlib.suppress(asyncio.CancelledError):
            await task


def make_app(
    adsb_url: str | None,
    ais_url: str | None,
    clock: Callable[[], float] = time.time,
) -> web.Application:
    """Builds the service.

    Args:
      adsb_url: URL of the receiver's aircraft.json, or None for no ADS-B.
      ais_url: URL of the receiver's ships.json, or None for no AIS.
      clock: Returns epoch seconds; used for trails, log entries and status.
    """
    app = web.Application()
    app[CLIENTS_KEY] = set()
    app[CLOCK_KEY] = clock
    app[LAST_OK_KEY] = {}
    app[TASKS_KEY] = []
    app[TRAILS_KEY] = TrailStore()
    app[LOG_KEY] = EventLog(lambda event: broadcast(app, event), clock)

    async def on_log(source: str, text: str) -> None:
        await app[LOG_KEY].add(source, text)

    async def publish_adsb(event: dict) -> None:
        if event.get("available"):
            app[LAST_OK_KEY]["adsb"] = clock()
            app[TRAILS_KEY].add(event["aircraft"], clock())
        await broadcast(app, event)

    async def publish_ais(event: dict) -> None:
        if event.get("available"):
            app[LAST_OK_KEY]["ais"] = clock()
        await broadcast(app, event)

    app[ADSB_KEY] = AdsbPoller(publish_adsb, adsb_url, on_log) if adsb_url else None
    app[AIS_KEY] = AisPoller(publish_ais, ais_url, on_log) if ais_url else None
    app.router.add_get("/ws", ws_handler)
    app.router.add_get("/healthz", healthz)
    app.router.add_get("/statusz", statusz)
    app.on_startup.append(_start)
    app.on_cleanup.append(_stop)
    return app


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="field-station", description=__doc__)
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8090)
    parser.add_argument(
        "--adsb-url",
        default=os.environ.get("FIELD_STATION_ADSB_URL"),
        help="aircraft.json URL (env FIELD_STATION_ADSB_URL); omit to disable",
    )
    parser.add_argument(
        "--ais-url",
        default=os.environ.get("FIELD_STATION_AIS_URL"),
        help="ships.json URL (env FIELD_STATION_AIS_URL); omit to disable",
    )
    args = parser.parse_args(argv)
    if not args.adsb_url and not args.ais_url:
        parser.error("give at least one of --adsb-url / --ais-url")
    logging.basicConfig(level=logging.INFO)
    web.run_app(make_app(args.adsb_url, args.ais_url), host=args.host, port=args.port)


if __name__ == "__main__":
    main()
