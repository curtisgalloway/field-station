# SPDX-FileCopyrightText: 2026 Curtis Galloway
# SPDX-License-Identifier: Apache-2.0
"""Spotter scoring rules (docs/spotter.md, "Scoring")."""

import pytest
import spotter_helpers as helpers

from field_station.spotter import score
from field_station.spotter import table as table_lib


def history(common=99, rare=1):
    return table_lib.TypeHistory({"2026-10-31": {"B738": common, "B52": rare}})


def sc(s, **kw):
    return score.score_sighting(s, **kw)


def test_plain_airliner_scores_zero():
    r = sc(helpers.aircraft())
    assert r.score == 0 and r.reasons == []


def test_emergency_by_field_and_squawk():
    assert sc(helpers.aircraft(emergency="general")).score == 100
    r = sc(helpers.aircraft(squawk="7700"))
    assert r.score == 100 and r.reasons == ["emergency: 7700"]
    assert sc(helpers.aircraft(emergency="none", squawk="1200")).score == 0


def test_military_and_interesting_flags():
    assert sc(helpers.aircraft(dbFlags=1)).reasons == ["military"]
    assert sc(helpers.aircraft(dbFlags=1)).score == 60
    assert sc(helpers.aircraft(dbFlags=2)).score == 40
    assert sc(helpers.aircraft(dbFlags=3)).score == 100
    assert sc(helpers.aircraft(dbFlags=8)).score == 0


def test_categories():
    assert sc(helpers.aircraft(category="B2")).score == 35
    assert sc(helpers.aircraft(category="A7")).score == 25
    assert sc(helpers.aircraft(category="A5")).score == 15
    assert sc(helpers.aircraft(category="A3")).score == 0


def test_old_airframe():
    r = sc(helpers.aircraft(year="1968"))
    assert r.score == 30 and r.reasons == ["built 1968"]
    assert sc(helpers.aircraft(year="1980")).score == 0
    assert sc(helpers.aircraft(year="junk")).score == 0


def test_no_type_info_penalty():
    r = sc(helpers.aircraft(t=None))
    assert r.score == -10 and r.reasons == ["no type info"]


def test_rarity_aircraft():
    r = sc(helpers.aircraft(t="B52"), history=history())
    assert r.score == 30 and r.reasons == ["rarely seen here: B52, 1\u00d7 in 7 days"]
    assert sc(helpers.aircraft(t="B738"), history=history()).score == 0


def test_rarity_is_linear_between_two_and_twenty_contacts():
    hist = table_lib.TypeHistory({"2026-10-31": {"X": 11, "Y": 945}})
    assert score.rarity_points("X", hist) == pytest.approx(15.0)
    hist = table_lib.TypeHistory({"2026-10-31": {"X": 2, "Y": 945}})
    assert score.rarity_points("X", hist) == 30
    hist = table_lib.TypeHistory({"2026-10-31": {"X": 20, "Y": 945}})
    assert score.rarity_points("X", hist) == 0


def test_rarity_counts_the_whole_family():
    hist = table_lib.TypeHistory(
        {"2026-10-31": {"C172": 58, "C182": 1, "C25B": 1, "B738": 100}}
    )
    assert score.rarity_points("C182", hist) == 0  # 59 Cessna singles
    r = sc(helpers.aircraft(t="C25B"), history=hist)
    assert r.reasons == ["rarely seen here: Citation jets, 1\u00d7 in 7 days"]


def test_rarity_needs_enough_history():
    small = table_lib.TypeHistory({"2026-10-31": {"B52": 1, "B738": 5}})
    assert sc(helpers.aircraft(t="B52"), history=small).score == 0
    assert sc(helpers.aircraft(t="B52")).score == 0


def test_closeness_needs_low_altitude_for_aircraft():
    assert sc(helpers.aircraft(dist=2.5, alt_baro=2000)).score == 7.5
    assert sc(helpers.aircraft(dist=2.5, alt_baro=30000)).score == 0
    assert sc(helpers.aircraft(dist=2.5, alt_baro="ground")).score == 7.5
    assert sc(helpers.aircraft(dist=5.0, alt_baro=2000)).score == 0


def test_overhead_earns_no_bonus_of_its_own():
    # Small aircraft cross the house all day; passing overhead is not news.
    assert sc(helpers.aircraft(dist=0.5, alt_baro=35000)).score == 0


def test_reasons_are_most_points_first():
    r = sc(helpers.aircraft(dbFlags=1, category="A5", year="1950"))
    assert r.reasons == ["military", "built 1950", "heavy"]


def test_vessel_types():
    for shiptype, pts in [
        (35, 60),
        (51, 40),
        (55, 35),
        (58, 30),
        (33, 25),
        (34, 25),
        (31, 20),
        (32, 20),
        (60, 20),
        (69, 20),
        (52, 15),
        (70, 15),
        (89, 15),
        (36, 5),
        (37, 5),
        (0, 0),
        (90, 0),
    ]:
        assert sc(helpers.vessel(shiptype=shiptype)).score == pts, shiptype


def test_vessel_length_rarity_closeness():
    assert sc(helpers.vessel(to_bow=100, to_stern=51)).score == 25
    assert sc(helpers.vessel(to_bow=100, to_stern=50)).score == 0
    assert sc(helpers.vessel(to_bow=None, to_stern=None)).score == 0
    assert sc(helpers.vessel(dist=0.0)).score == 15
    hist = table_lib.TypeHistory({"2026-10-31": {"ship:37": 99, "ship:35": 1}})
    r = sc(helpers.vessel(shiptype=35), history=hist)
    assert r.score == 90
    assert "rarely seen here: military, 1\u00d7 in 7 days" in r.reasons


def test_rank_orders_and_penalizes_repeats():
    t = table_lib.SightingTable()
    a = helpers.aircraft("a", dbFlags=1, now=0)
    b = helpers.aircraft("b", dbFlags=2, now=0)
    t.merge([a, b], now=0)
    assert [r.sighting.key for r in score.rank(t, 0)] == ["a", "b"]
    t.mark_shown(a, now=0)
    assert [r.sighting.key for r in score.rank(t, 10)] == ["b", "a"]


def test_shown_penalty():
    r = sc(helpers.aircraft(squawk="7700"), shown_recently=True)
    assert r.score == 50 and r.reasons[-1] == "shown in last 24 h"
