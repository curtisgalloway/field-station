# SPDX-FileCopyrightText: 2026 Curtis Galloway
# SPDX-License-Identifier: Apache-2.0
"""The token-protected ``/spotter/*`` HTTP API (docs/spotter.md)."""

from __future__ import annotations

import asyncio
import hmac

from aiohttp import web

from field_station.spotter import service
from field_station.spotter import store as store_lib

SPOTTER_KEY = web.AppKey("spotter", service.Spotter | None)
TOKEN_KEY = web.AppKey("spotter_token", str)


def _unavailable(reason: str) -> web.Response:
    return web.json_response({"error": reason}, status=503)


def authorize(request: web.Request) -> web.Response | None:
    """Returns the refusal for a request that may not proceed, else None.

    503 when no token is configured, 401 for a missing or wrong one. The
    compare is constant-time.
    """
    token = request.app[TOKEN_KEY]
    if not token:
        return _unavailable("spotter token not configured")
    header = request.headers.get("Authorization", "")
    scheme, _, supplied = header.partition(" ")
    if scheme.lower() != "bearer" or not hmac.compare_digest(
        supplied.strip().encode("utf-8"), token.encode("utf-8")
    ):
        return web.json_response(
            {"error": "unauthorized"},
            status=401,
            headers={"WWW-Authenticate": "Bearer"},
        )
    return None


def _spotter(request: web.Request) -> service.Spotter | None:
    return request.app[SPOTTER_KEY]


async def current(request: web.Request) -> web.Response:
    refusal = authorize(request)
    if refusal is not None:
        return refusal
    spotter = _spotter(request)
    if spotter is None:
        return _unavailable("spotter not running")
    return web.json_response(spotter.current())


RANKED_MAX = 25


async def ranked(request: web.Request) -> web.Response:
    refusal = authorize(request)
    if refusal is not None:
        return refusal
    spotter = _spotter(request)
    if spotter is None:
        return _unavailable("spotter not running")
    try:
        limit = int(request.query.get("limit", "10"))
    except ValueError:
        return web.json_response({"error": "limit must be an integer"}, status=400)
    limit = max(1, min(limit, RANKED_MAX))
    return web.json_response({"sightings": spotter.ranked(limit)})


async def _query(request: web.Request, name: str, key: str) -> web.Response:
    refusal = authorize(request)
    if refusal is not None:
        return refusal
    spotter = _spotter(request)
    if spotter is None or spotter.store is None:
        return _unavailable("no spotter database configured")
    try:
        found = await asyncio.to_thread(
            getattr(spotter.store, name), dict(request.query)
        )
    except store_lib.QueryError as err:
        return web.json_response({"error": str(err)}, status=400)
    return web.json_response({key: found})


async def history(request: web.Request) -> web.Response:
    return await _query(request, "history", "sightings")


async def counts(request: web.Request) -> web.Response:
    return await _query(request, "counts", "counts")


def add_routes(app: web.Application) -> None:
    app.router.add_get("/spotter/current", current)
    app.router.add_get("/spotter/ranked", ranked)
    app.router.add_get("/spotter/history", history)
    app.router.add_get("/spotter/counts", counts)
