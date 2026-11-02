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
import functools
import importlib.metadata
import json
import logging
import os
import time
import zoneinfo

from aiohttp import web

from field_station.adsb import AdsbPoller
from field_station.ais import AisPoller
from field_station.eventlog import EventLog
from field_station.spotter import api as spotter_api
from field_station.spotter import schedule as spotter_schedule
from field_station.spotter import service as spotter_service
from field_station.spotter import store as spotter_store
from field_station.trails import TrailStore

log = logging.getLogger("field_station.app")

ADSB_KEY = web.AppKey("adsb", AdsbPoller | None)
AIS_KEY = web.AppKey("ais", AisPoller | None)
TRAILS_KEY = web.AppKey("trails", TrailStore)
LOG_KEY = web.AppKey("log", EventLog)
CLIENTS_KEY = web.AppKey("clients", set)
CLOCK_KEY = web.AppKey("clock", object)
TASKS_KEY = web.AppKey("tasks", list)

# Spotter defaults (docs/spotter.md).
DEFAULT_QUIET = "22:00-07:00"
DEFAULT_TZ = "America/Los_Angeles"

# Event field that holds each source's contact list.
CONTACTS_FIELD = {"adsb": "aircraft", "ais": "vessels"}


def _sources(app: web.Application) -> dict:
    """Returns the configured pollers by source name."""
    found = {"adsb": app[ADSB_KEY], "ais": app[AIS_KEY]}
    return {name: poller for name, poller in found.items() if poller is not None}


CLIENT_QUEUE_SIZE = 64


class Client:
    """One connected consumer: a bounded outbox drained by its own task."""

    def __init__(self, ws: web.WebSocketResponse, registry: set | None = None) -> None:
        self.ws = ws
        self.registry = registry
        self.queue: asyncio.Queue[str] = asyncio.Queue(maxsize=CLIENT_QUEUE_SIZE)
        self.writer: asyncio.Task | None = None

    async def drain(self) -> None:
        try:
            while True:
                await self.ws.send_str(await self.queue.get())
        except (ConnectionError, RuntimeError) as exc:
            log.info("dropping a client whose socket failed: %s", exc)
            if self.registry is not None:
                self.registry.discard(self)
            asyncio.ensure_future(self._close())

    async def _close(self) -> None:
        with contextlib.suppress(ConnectionError, RuntimeError):
            await self.ws.close()

    def drop(self) -> None:
        """Abandons a client that cannot keep up."""
        if self.writer is not None:
            self.writer.cancel()
        asyncio.ensure_future(self.ws.close())


def broadcast(app: web.Application, event: dict) -> None:
    """Queues an event for every client; never waits on the network."""
    payload = json.dumps(event)
    for client in list(app[CLIENTS_KEY]):
        try:
            client.queue.put_nowait(payload)
        except asyncio.QueueFull:
            log.warning("dropping a client that cannot keep up")
            app[CLIENTS_KEY].discard(client)
            client.drop()


async def ws_handler(request: web.Request) -> web.WebSocketResponse:
    ws = web.WebSocketResponse(heartbeat=30)
    await ws.prepare(request)
    app = request.app
    client = Client(ws, app[CLIENTS_KEY])
    # No await between registering and snapshotting, so every later event
    # lands in the queue after the burst and none is lost or duplicated.
    app[CLIENTS_KEY].add(client)
    for poller in _sources(app).values():
        client.queue.put_nowait(json.dumps(poller.status()))
    snapshot = {"type": "trails", "aircraft": app[TRAILS_KEY].snapshot()}
    client.queue.put_nowait(json.dumps(snapshot))
    client.queue.put_nowait(json.dumps(app[LOG_KEY].history()))
    client.writer = asyncio.create_task(client.drain())
    try:
        async for _ in ws:
            pass  # inbound messages are ignored
    finally:
        app[CLIENTS_KEY].discard(client)
        client.writer.cancel()
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
        last_ok = poller.last_ok
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
    for name, poller in _sources(app).items():
        task = asyncio.create_task(poller.run())
        task.add_done_callback(functools.partial(_poller_died, poller, name))
        app[TASKS_KEY].append(task)
    spotter = app[spotter_api.SPOTTER_KEY]
    if spotter is not None:
        await asyncio.to_thread(spotter.load)
        app[TASKS_KEY].append(asyncio.create_task(spotter.run()))


def _poller_died(poller, name: str, task: asyncio.Task) -> None:
    """Reports a poller that crashed as unavailable instead of frozen."""
    if task.cancelled() or task.exception() is None:
        return
    log.error("%s poller crashed", name, exc_info=task.exception())
    asyncio.ensure_future(poller.mark_down())


async def _close_clients(app: web.Application) -> None:
    """Closes every open WebSocket so consumers see the stop."""
    clients = list(app[CLIENTS_KEY])
    app[CLIENTS_KEY].clear()
    for client in clients:
        if client.writer is not None:
            client.writer.cancel()
    await asyncio.gather(
        *(client.ws.close(code=1001, message=b"going away") for client in clients),
        return_exceptions=True,
    )


async def _stop(app: web.Application) -> None:
    for task in app[TASKS_KEY]:
        task.cancel()
    for task in app[TASKS_KEY]:
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await task
    spotter = app[spotter_api.SPOTTER_KEY]
    if spotter is not None:
        spotter.close()


def make_app(
    adsb_url: str | None,
    ais_url: str | None,
    clock: Callable[[], float] = time.time,
    spotter: spotter_service.Spotter | None = None,
    spotter_token: str | None = None,
) -> web.Application:
    """Builds the service.

    Args:
      adsb_url: URL of the receiver's aircraft.json, or None for no ADS-B.
      ais_url: URL of the receiver's ships.json, or None for no AIS.
      clock: Returns epoch seconds; used for trails, log entries and status.
      spotter: The spotter to feed with the full receiver documents, or None.
      spotter_token: Bearer token for ``/spotter/*``; unset answers 503.
    """
    app = web.Application()
    app[CLIENTS_KEY] = set()
    app[CLOCK_KEY] = clock
    app[TASKS_KEY] = []
    app[TRAILS_KEY] = TrailStore()

    async def publish_log(event: dict) -> None:
        broadcast(app, event)

    app[LOG_KEY] = EventLog(publish_log, clock)

    async def on_log(source: str, text: str) -> None:
        await app[LOG_KEY].add(source, text)

    async def publish(event: dict) -> None:
        broadcast(app, event)

    async def on_adsb_poll(status: dict) -> None:
        # Every cycle, so trails age out during an outage or a quiet sky.
        app[TRAILS_KEY].add(status.get("aircraft", []), clock())

    app[spotter_api.SPOTTER_KEY] = spotter
    app[spotter_api.TOKEN_KEY] = spotter_token or ""
    adsb_tap = spotter.observe_aircraft if spotter is not None else None
    ais_tap = spotter.observe_ships if spotter is not None else None

    app[ADSB_KEY] = (
        AdsbPoller(publish, adsb_url, on_log, clock, on_adsb_poll, adsb_tap)
        if adsb_url
        else None
    )
    app[AIS_KEY] = (
        AisPoller(publish, ais_url, on_log, clock, None, ais_tap) if ais_url else None
    )
    spotter_api.add_routes(app)
    app.router.add_get("/ws", ws_handler)
    app.router.add_get("/healthz", healthz)
    app.router.add_get("/statusz", statusz)
    app.on_startup.append(_start)
    app.on_shutdown.append(_close_clients)
    app.on_cleanup.append(_stop)
    return app


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="field-station", description=__doc__)
    parser.add_argument("--host", default="127.0.0.1")
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
    parser.add_argument(
        "--spotter-db",
        default=os.environ.get("FIELD_STATION_SPOTTER_DB"),
        help="spotter history SQLite file (env FIELD_STATION_SPOTTER_DB); "
        "omit to keep the spotter in memory only",
    )
    parser.add_argument(
        "--spotter-seed",
        default=os.environ.get("FIELD_STATION_SPOTTER_SEED"),
        help="JSON file of estimated past type counts for rarity "
        "(env FIELD_STATION_SPOTTER_SEED); a missing file is skipped",
    )
    parser.add_argument(
        "--spotter-quiet",
        default=os.environ.get("FIELD_STATION_SPOTTER_QUIET", DEFAULT_QUIET),
        help="daily HH:MM-HH:MM window when the pick never changes "
        "(env FIELD_STATION_SPOTTER_QUIET; default %(default)s)",
    )
    parser.add_argument(
        "--spotter-tz",
        default=os.environ.get("FIELD_STATION_SPOTTER_TZ", DEFAULT_TZ),
        help="time zone of the quiet hours (env FIELD_STATION_SPOTTER_TZ; "
        "default %(default)s)",
    )
    args = parser.parse_args(argv)
    if not args.adsb_url and not args.ais_url:
        parser.error("give at least one of --adsb-url / --ais-url")
    token = os.environ.get("FIELD_STATION_SPOTTER_TOKEN") or None
    spotter = None
    if token or args.spotter_db:
        try:
            quiet = spotter_schedule.QuietHours.parse(args.spotter_quiet)
            tz = zoneinfo.ZoneInfo(args.spotter_tz)
        except (ValueError, zoneinfo.ZoneInfoNotFoundError) as err:
            parser.error(str(err))
        store = spotter_store.Store(args.spotter_db) if args.spotter_db else None
        spotter = spotter_service.Spotter(
            store, quiet, tz, seed_path=args.spotter_seed
        )
    logging.basicConfig(level=logging.INFO)
    web.run_app(
        make_app(args.adsb_url, args.ais_url, spotter=spotter, spotter_token=token),
        host=args.host,
        port=args.port,
    )


if __name__ == "__main__":
    main()
