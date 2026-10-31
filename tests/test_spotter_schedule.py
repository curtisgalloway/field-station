# SPDX-FileCopyrightText: 2026 Curtis Galloway
# SPDX-License-Identifier: Apache-2.0
"""Spotter change schedule and the sighting table."""

import datetime
import zoneinfo

import pytest
import spotter_helpers as helpers

from field_station.spotter import model
from field_station.spotter import schedule
from field_station.spotter import table as table_lib

TZ = zoneinfo.ZoneInfo("America/Los_Angeles")
QUIET = schedule.QuietHours.parse("22:00-07:00")


def at(hour, minute=0):
    return datetime.datetime(2026, 10, 31, hour, minute, tzinfo=TZ).timestamp()


def top(score, key="a"):
    return model.Scored(helpers.aircraft(key), score, [])


def decide(now, scored, shown=None, refreshed=None):
    disp = table_lib.Display(shown, refreshed)
    return schedule.decide(now, scored, disp, QUIET, TZ)


def test_quiet_hours_wraparound():
    for h, m, quiet in [
        (22, 0, True),
        (23, 59, True),
        (0, 0, True),
        (6, 59, True),
        (7, 0, False),
        (12, 0, False),
        (21, 59, False),
    ]:
        assert schedule.is_quiet(at(h, m), QUIET, TZ) is quiet, (h, m)


def test_quiet_window_not_wrapping_and_empty():
    day = schedule.QuietHours.parse("09:00-17:00")
    assert day.contains(datetime.time(12))
    assert not day.contains(datetime.time(17))
    assert not schedule.QuietHours.parse("08:00-08:00").contains(datetime.time(8))


def test_bad_quiet_hours():
    with pytest.raises(ValueError):
        schedule.QuietHours.parse("nonsense")


def test_quiet_blocks_even_a_great_sighting():
    d = decide(at(23), top(500))
    assert not d.change and d.reason == "quiet hours"


def test_nothing_seen():
    assert not decide(at(12), None).change


def test_same_top_is_not_changed():
    assert not decide(at(12), top(100, "a"), shown="aircraft:a", refreshed=0).change


def test_interesting_waits_for_15_minutes():
    now = at(12)
    assert not decide(now, top(50), shown="aircraft:z", refreshed=now - 899).change
    assert decide(now, top(50), shown="aircraft:z", refreshed=now - 900).change


def test_floor_is_inclusive():
    assert decide(at(12), top(40), shown="aircraft:z", refreshed=0).change


def test_boring_waits_for_60_minutes():
    now = at(12)
    assert not decide(now, top(10), shown="aircraft:z", refreshed=now - 3599).change
    assert decide(now, top(10), shown="aircraft:z", refreshed=now - 3600).change


def test_first_ever_change():
    assert decide(at(12), top(0)).change


def test_merge_keeps_closest_and_first_last_seen():
    t = table_lib.SightingTable()
    t.merge([helpers.aircraft("a", dist=10, now=100, marker="far")], now=100)
    t.merge([helpers.aircraft("a", dist=3, now=160, marker="near")], now=160)
    t.merge([helpers.aircraft("a", dist=8, now=220, marker="mid")], now=220)
    s = t.sightings["aircraft:a"]
    assert s.distance_nm == 3
    assert s.raw["marker"] == "near"
    assert s.seen_at == 160
    assert (s.first_seen, s.last_seen) == (100, 220)


def test_prune_after_60_minutes():
    t = table_lib.SightingTable()
    t.merge([helpers.aircraft("a", now=0)], now=0)
    t.prune(3600)
    assert "aircraft:a" in t.sightings
    t.prune(3601)
    assert not t.sightings


def test_kinds_do_not_collide():
    t = table_lib.SightingTable()
    t.merge([helpers.aircraft("123", now=0), helpers.vessel("123", now=0)], now=0)
    assert len(t.sightings) == 2


def test_history_counts_distinct_sightings_once_and_expires():
    t = table_lib.SightingTable()
    t.merge([helpers.aircraft("a", now=0)], now=0)
    t.merge([helpers.aircraft("a", now=60)], now=60)
    assert t.history.count("B738") == 1
    t.prune(8 * 24 * 3600)
    assert t.history.total() == 0


def test_shown_expires_after_24h():
    t = table_lib.SightingTable()
    s = helpers.aircraft("a", now=0)
    t.mark_shown(s, now=0)
    assert t.shown_recently(s, now=86399)
    assert not t.shown_recently(s, now=86400)
    assert t.display.shown_id == "aircraft:a"
