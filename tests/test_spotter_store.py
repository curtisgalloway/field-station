# SPDX-FileCopyrightText: 2026 Curtis Galloway
# SPDX-License-Identifier: Apache-2.0
"""The spotter's SQLite history and its queries."""

import pytest

from field_station.spotter import store as store_lib

DAY = 86400.0


def row(kind="aircraft", key="a", **over):
    base = {
        "kind": kind,
        "key": key,
        "window_start": 0,
        "ident": None,
        "registration": None,
        "type_code": None,
        "description": None,
        "operator": None,
        "country": None,
        "year": None,
        "ship_type": None,
        "length_m": None,
        "distance_nm": 3.0,
        "bearing_deg": 90.0,
        "altitude_ft": None,
        "seen_at": 1000.0,
        "score": 10.0,
        "reasons": ["why"],
        "first_seen": 1000.0,
        "last_seen": 1100.0,
    }
    base.update(over)
    return base


@pytest.fixture(name="db")
def _db(tmp_path):
    store = store_lib.Store(tmp_path / "sub" / "spotter.db")
    yield store
    store.close()


def test_round_trip(db):
    db.commit(
        [
            row(
                ident="SKW1",
                registration="N1",
                type_code="E175",
                description="EMBRAER 175",
                operator="SAMPLE AIR",
                year=2010,
                altitude_ft=3000.0,
                reasons=["a", "b"],
            )
        ],
        now=2000.0,
    )
    found = db.history({"since": "0"})
    assert len(found) == 1
    assert found[0] == {
        "kind": "aircraft",
        "key": "a",
        "ident": "SKW1",
        "registration": "N1",
        "type_code": "E175",
        "description": "EMBRAER 175",
        "operator": "SAMPLE AIR",
        "country": None,
        "year": 2010,
        "ship_type": None,
        "length_m": None,
        "distance_nm": 3.0,
        "bearing_deg": 90.0,
        "altitude_ft": 3000.0,
        "seen_at": 1000.0,
        "score": 10.0,
        "reasons": ["a", "b"],
        "first_seen": 1000.0,
        "last_seen": 1100.0,
        "picked": False,
    }


def test_upsert_keeps_one_row_per_window_and_closest_pass(db):
    db.commit([row(distance_nm=3.0, score=10.0, seen_at=1000.0)], now=2000.0)
    db.commit(
        [row(distance_nm=5.0, score=1.0, seen_at=1500.0, last_seen=1600.0)],
        now=2001.0,
    )
    (only,) = db.history({"since": "0"})
    assert only["distance_nm"] == 3.0 and only["score"] == 10.0
    assert only["seen_at"] == 1000.0 and only["last_seen"] == 1600.0
    db.commit(
        [row(distance_nm=2.0, score=20.0, seen_at=1700.0, first_seen=900.0)],
        now=2002.0,
    )
    (only,) = db.history({"since": "0"})
    assert only["distance_nm"] == 2.0 and only["score"] == 20.0
    assert only["first_seen"] == 900.0 and only["last_seen"] == 1600.0
    db.commit([row(window_start=3600)], now=2003.0)
    assert len(db.history({"since": "0"})) == 2


def test_picked_survives_later_upserts(db):
    db.commit([row()], now=2000.0)
    db.commit([], now=2001.0, picked=("aircraft", "a", 0))
    db.commit([row(score=50.0)], now=2002.0)
    (only,) = db.history({"since": "0"})
    assert only["picked"] is True
    assert db.recent_picks(0) == [("aircraft", "a", 2001.0)]
    assert db.recent_picks(2002.0) == []


def test_meta_round_trip_and_overwrite(db):
    assert db.meta("seq") is None
    db.commit([], now=1.0, meta={"seq": "1"})
    db.commit([], now=2.0, meta={"seq": "2"})
    assert db.meta("seq") == "2"


def test_retention_prunes_after_400_days(db):
    db.commit([row(last_seen=100.0)], now=100.0)
    db.commit([row(key="b", last_seen=100.0 + 399 * DAY)], now=100.0 + 399 * DAY)
    assert len(db.history({"since": "0"})) == 2
    later = 100.0 + 401 * DAY
    db.commit([], now=later)
    assert [r["key"] for r in db.history({"since": "0"})] == ["b"]


def test_not_null_violation_raises(db):
    bad = row()
    bad["distance_nm"] = None
    with pytest.raises(Exception):
        db.commit([bad], now=1.0)
    assert db.history({"since": "0"}) == []


@pytest.fixture(name="filled")
def _filled(db):
    db.commit(
        [
            row(key="a", type_code="B738", score=5.0, last_seen=1000.0),
            row(key="b", type_code="B738", score=50.0, last_seen=2000.0),
            row(key="c", type_code="E175", score=30.0, last_seen=3000.0),
            row(
                key="a",
                window_start=3600,
                type_code="B738",
                score=7.0,
                last_seen=4000.0,
                first_seen=3700.0,
            ),
            row(
                "vessel",
                "100",
                ship_type=35,
                score=60.0,
                last_seen=2500.0,
                description="military",
            ),
            row("vessel", "200", ship_type=71, score=15.0, last_seen=2600.0),
            row("vessel", "300", ship_type=79, score=15.0, last_seen=2700.0),
            row(
                "vessel",
                "400",
                ship_type=30,
                score=0.0,
                last_seen=500.0,
                first_seen=400.0,
            ),
        ],
        now=5000.0,
    )
    db.commit([], now=5000.0, picked=("aircraft", "c", 0))
    return db


def test_history_order_and_limit(filled):
    by_score = filled.history({"since": "0"})
    assert [r["score"] for r in by_score] == sorted(
        [r["score"] for r in by_score], reverse=True
    )
    assert by_score[0]["key"] == "100"
    by_time = filled.history({"since": "0", "order": "time"})
    assert by_time[0]["last_seen"] == 4000.0
    assert len(filled.history({"since": "0", "limit": "2"})) == 2
    assert len(filled.history({"since": "0", "limit": "9999"})) == 8
    assert len(filled.history({"since": "0", "limit": "0"})) == 1


def test_history_filters(filled):
    keys = lambda **p: sorted(r["key"] for r in filled.history({"since": "0", **p}))
    assert keys(kind="vessel") == ["100", "200", "300", "400"]
    assert keys(type_code="E175") == ["c"]
    assert keys(ship_type_min="70", ship_type_max="79") == ["200", "300"]
    assert keys(ship_type_min="70") == ["200", "300"]
    assert keys(picked="true") == ["c"]
    assert len(keys(picked="false")) == 7
    assert keys(kind="aircraft", since="2500") == ["a", "c"]
    assert filled.history({"since": "2500", "until": "3000"})
    assert all(
        r["first_seen"] <= 3000 for r in filled.history({"since": "0", "until": "3000"})
    )


@pytest.mark.parametrize(
    "params",
    [
        {},
        {"since": "x"},
        {"since": "0", "kind": "boat"},
        {"since": "0", "order": "random"},
        {"since": "0", "limit": "many"},
        {"since": "0", "picked": "maybe"},
        {"since": "0", "ship_type_min": "x"},
    ],
)
def test_history_rejects_bad_parameters(filled, params):
    with pytest.raises(store_lib.QueryError):
        filled.history(params)


def test_counts_by_type_code_counts_distinct_contacts(filled):
    found = filled.counts({"since": "0", "group": "type_code"})
    assert found == [
        {"group": "B738", "label": None, "contacts": 2},
        {"group": "E175", "label": None, "contacts": 1},
    ]


def test_counts_ship_type_and_class(filled):
    by_type = {
        c["group"]: c for c in filled.counts({"since": "0", "group": "ship_type"})
    }
    assert by_type[35]["label"] == "military"
    assert by_type[71]["label"] == "cargo"
    by_class = {
        c["group"]: c for c in filled.counts({"since": "0", "group": "ship_class"})
    }
    assert by_class[70] == {"group": 70, "label": "cargo", "contacts": 2}
    assert by_class[30]["contacts"] == 2
    assert filled.counts({"since": "600", "group": "ship_class", "kind": "vessel"})[
        0
    ] == {"group": 70, "label": "cargo", "contacts": 2}
    assert filled.counts({"since": "0", "group": "ship_type", "kind": "aircraft"}) == []


def test_counts_rejects_bad_group(filled):
    with pytest.raises(store_lib.QueryError):
        filled.counts({"since": "0", "group": "colour"})
    with pytest.raises(store_lib.QueryError):
        filled.counts({"group": "type_code"})


def test_type_counts_for_rarity(db):
    db.commit(
        [
            row(key="a", type_code="B738", first_seen=100.0),
            row("vessel", "9", ship_type=37, first_seen=100.0),
            row(key="old", type_code="X", first_seen=-10 * DAY),
        ],
        now=200.0,
    )
    got = sorted(db.type_counts(0.0))
    assert got == [("1970-01-01", "B738", 1), ("1970-01-01", "ship:37", 1)]
