# SPDX-FileCopyrightText: 2026 Curtis Galloway
# SPDX-License-Identifier: Apache-2.0
"""Shared fixtures: a fake receiver serving fixture JSON over real HTTP."""

import json
import pathlib

from aiohttp import web
from aiohttp.test_utils import TestServer
import pytest
import pytest_asyncio

FIXTURES = pathlib.Path(__file__).resolve().parent / "fixtures"


class FakeReceiver:
    """Serves JSON files; a route can be switched to answer HTTP 500."""

    def __init__(self, routes: dict[str, dict]) -> None:
        self.bodies = dict(routes)
        self.failing: set[str] = set()
        self.server: TestServer | None = None
        self.hits: dict[str, int] = {}

    def fail(self, path: str) -> None:
        self.failing.add(path)

    def heal(self, path: str) -> None:
        self.failing.discard(path)

    def url(self, path: str) -> str:
        assert self.server is not None
        return str(self.server.make_url(path))

    async def _handle(self, request: web.Request) -> web.Response:
        path = request.path
        self.hits[path] = self.hits.get(path, 0) + 1
        if path in self.failing:
            return web.Response(status=500, text="boom")
        return web.json_response(self.bodies[path])


async def start_fake_receiver(routes: dict[str, dict]) -> FakeReceiver:
    fake = FakeReceiver(routes)
    app = web.Application()
    for path in routes:
        app.router.add_get(path, fake._handle)  # pylint: disable=protected-access
    fake.server = TestServer(app)
    await fake.server.start_server()
    return fake


@pytest.fixture
def aircraft_fixture() -> dict:
    return json.loads((FIXTURES / "aircraft.json").read_text())


@pytest.fixture
def receiver_fixture() -> dict:
    return json.loads((FIXTURES / "receiver.json").read_text())


@pytest_asyncio.fixture
async def fake_adsb(aircraft_fixture, receiver_fixture):
    fake = await start_fake_receiver(
        {
            "/data/aircraft.json": aircraft_fixture,
            "/data/receiver.json": receiver_fixture,
        }
    )
    yield fake
    await fake.server.close()
