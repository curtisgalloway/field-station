# SPDX-FileCopyrightText: 2026 Curtis Galloway
# SPDX-License-Identifier: Apache-2.0
"""SQLite history of sightings (docs/spotter.md, "Sightings and history").

One row per sighting per hour window. The connection is shared between the
event loop's worker threads, so every call takes a lock; calls are short.
"""

from __future__ import annotations

import json
import pathlib
import sqlite3
import threading

from field_station.spotter import names

RETENTION_SECONDS = 400 * 24 * 3600
PRUNE_EVERY_SECONDS = 3600
MAX_LIMIT = 200
DEFAULT_LIMIT = 20

# Columns describing the contact and its closest pass. On a conflict they
# are replaced only when the new row's pass is closer, so a restart that
# re-observes a contact farther away never overwrites the stored closest pass.
CLOSEST_COLUMNS = (
    "ident",
    "registration",
    "type_code",
    "description",
    "operator",
    "country",
    "year",
    "ship_type",
    "length_m",
    "distance_nm",
    "bearing_deg",
    "altitude_ft",
    "seen_at",
    "score",
    "reasons",
)
ROW_COLUMNS = (
    "kind",
    "key",
    "window_start",
    *CLOSEST_COLUMNS,
    "first_seen",
    "last_seen",
)
# What a history row returns: the pick fields plus first/last seen and picked.
PUBLIC_COLUMNS = (
    "kind",
    "key",
    "ident",
    "registration",
    "type_code",
    "description",
    "operator",
    "country",
    "year",
    "ship_type",
    "length_m",
    "distance_nm",
    "bearing_deg",
    "altitude_ft",
    "seen_at",
    "score",
    "reasons",
    "first_seen",
    "last_seen",
    "picked",
)

SCHEMA = """
CREATE TABLE IF NOT EXISTS sightings (
    kind TEXT NOT NULL,
    key TEXT NOT NULL,
    window_start INTEGER NOT NULL,
    ident TEXT,
    registration TEXT,
    type_code TEXT,
    description TEXT,
    operator TEXT,
    country TEXT,
    year INTEGER,
    ship_type INTEGER,
    length_m REAL,
    distance_nm REAL NOT NULL,
    bearing_deg REAL,
    altitude_ft REAL,
    seen_at REAL NOT NULL,
    first_seen REAL NOT NULL,
    last_seen REAL NOT NULL,
    score REAL NOT NULL,
    reasons TEXT NOT NULL,
    picked INTEGER NOT NULL DEFAULT 0,
    picked_at REAL,
    PRIMARY KEY (kind, key, window_start)
);
CREATE INDEX IF NOT EXISTS sightings_last_seen ON sightings (last_seen);
CREATE TABLE IF NOT EXISTS meta (
    name TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""

_UPSERT = (
    "INSERT INTO sightings ({cols}) VALUES ({marks}) "
    "ON CONFLICT (kind, key, window_start) DO UPDATE SET {sets}, "
    "first_seen = MIN(sightings.first_seen, excluded.first_seen), "
    "last_seen = MAX(sightings.last_seen, excluded.last_seen)"
)


class QueryError(ValueError):
    """A history or counts query had a bad parameter."""


def _upsert_sql() -> str:
    closer = "excluded.distance_nm <= sightings.distance_nm"
    sets = ", ".join(
        f"{c} = CASE WHEN {closer} THEN excluded.{c} ELSE sightings.{c} END"
        for c in CLOSEST_COLUMNS
    )
    return _UPSERT.format(
        cols=", ".join(ROW_COLUMNS),
        marks=", ".join("?" for _ in ROW_COLUMNS),
        sets=sets,
    )


def _public(row: sqlite3.Row) -> dict:
    out = {name: row[name] for name in PUBLIC_COLUMNS}
    out["reasons"] = json.loads(out["reasons"])
    out["picked"] = bool(out["picked"])
    return out


def _opt_float(params: dict, name: str) -> float | None:
    if params.get(name) in (None, ""):
        return None
    try:
        return float(params[name])
    except ValueError as err:
        raise QueryError(f"{name} must be a number") from err


class Store:
    """The sightings database."""

    def __init__(self, path: str | pathlib.Path) -> None:
        path = pathlib.Path(path)
        if path.parent != pathlib.Path():
            path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._db = sqlite3.connect(str(path), check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        self._last_prune = 0.0
        with self._lock, self._db:
            self._db.executescript(SCHEMA)

    def close(self) -> None:
        with self._lock:
            self._db.close()

    def commit(
        self,
        rows: list[dict],
        now: float,
        picked: tuple[str, str, int] | None = None,
        meta: dict[str, str] | None = None,
    ) -> None:
        """Stores a batch in one transaction.

        Args:
          rows: Sighting rows keyed by ROW_COLUMNS (reasons already a list).
          now: Epoch seconds, for the pick time and the retention prune.
          picked: (kind, key, window_start) of the sighting that just became
            the pick, if any.
          meta: Named values to keep (current pick, sequence number).
        """
        sql = _upsert_sql()
        with self._lock, self._db:
            for row in rows:
                values = [
                    json.dumps(row[c]) if c == "reasons" else row[c]
                    for c in ROW_COLUMNS
                ]
                self._db.execute(sql, values)
            if picked is not None:
                self._db.execute(
                    "UPDATE sightings SET picked = 1, picked_at = ? "
                    "WHERE kind = ? AND key = ? AND window_start = ?",
                    (now, *picked),
                )
            for name, value in (meta or {}).items():
                self._db.execute(
                    "INSERT INTO meta (name, value) VALUES (?, ?) "
                    "ON CONFLICT (name) DO UPDATE SET value = excluded.value",
                    (name, value),
                )
            if now - self._last_prune >= PRUNE_EVERY_SECONDS:
                self._db.execute(
                    "DELETE FROM sightings WHERE last_seen < ?",
                    (now - RETENTION_SECONDS,),
                )
                self._last_prune = now

    def meta(self, name: str) -> str | None:
        with self._lock:
            row = self._db.execute(
                "SELECT value FROM meta WHERE name = ?", (name,)
            ).fetchone()
        return None if row is None else row["value"]

    def recent_picks(self, since: float) -> list[tuple[str, str, float]]:
        """Returns (kind, key, picked_at) for picks made at or after `since`."""
        with self._lock:
            found = self._db.execute(
                "SELECT kind, key, picked_at FROM sightings "
                "WHERE picked = 1 AND picked_at >= ?",
                (since,),
            ).fetchall()
        return [(r["kind"], r["key"], r["picked_at"]) for r in found]

    def type_counts(self, since: float) -> list[tuple[str, str, int]]:
        """Returns (UTC day, type key, sightings) from `since`, for rarity."""
        with self._lock:
            found = self._db.execute(
                "SELECT strftime('%Y-%m-%d', first_seen, 'unixepoch') AS day, "
                "CASE WHEN kind = 'aircraft' THEN type_code "
                "ELSE 'ship:' || ship_type END AS tkey, COUNT(*) AS n "
                "FROM sightings WHERE first_seen >= ? AND tkey IS NOT NULL "
                "GROUP BY day, tkey",
                (since,),
            ).fetchall()
        return [(r["day"], r["tkey"], r["n"]) for r in found]

    def history(self, params: dict) -> list[dict]:
        """Returns sightings overlapping [since, until], best or newest first.

        Args:
          params: Query parameters as strings: since (required), until, kind,
            type_code, ship_type_min, ship_type_max, picked, order, limit.

        Raises:
          QueryError: A parameter is missing or malformed.
        """
        where, args = self._filters(params, need_since=True)
        if params.get("type_code"):
            where.append("type_code = ?")
            args.append(params["type_code"])
        low, high = _opt_float(params, "ship_type_min"), _opt_float(
            params, "ship_type_max"
        )
        if low is not None:
            where.append("ship_type >= ?")
            args.append(low)
        if high is not None:
            where.append("ship_type <= ?")
            args.append(high)
        picked = params.get("picked")
        if picked not in (None, ""):
            if picked.lower() not in ("true", "false", "1", "0"):
                raise QueryError("picked must be true or false")
            where.append("picked = ?")
            args.append(1 if picked.lower() in ("true", "1") else 0)
        order = params.get("order") or "score"
        if order == "score":
            order_sql = "score DESC, distance_nm ASC, key ASC"
        elif order == "time":
            order_sql = "last_seen DESC, key ASC"
        else:
            raise QueryError("order must be score or time")
        try:
            limit = int(params.get("limit") or DEFAULT_LIMIT)
        except ValueError as err:
            raise QueryError("limit must be an integer") from err
        limit = max(1, min(limit, MAX_LIMIT))
        clause = " AND ".join(where)
        sql = f"SELECT * FROM sightings WHERE {clause} " f"ORDER BY {order_sql} LIMIT ?"
        with self._lock:
            found = self._db.execute(sql, [*args, limit]).fetchall()
        return [_public(r) for r in found]

    def counts(self, params: dict) -> list[dict]:
        """Returns distinct contacts per group over [since, until].

        Args:
          params: since (required), until, kind, group (type_code, ship_type
            or ship_class).

        Raises:
          QueryError: A parameter is missing or malformed.
        """
        group = params.get("group")
        if group == "type_code":
            expr, label = "type_code", "MAX(description)"
        elif group == "ship_type":
            expr, label = "ship_type", "NULL"
        elif group == "ship_class":
            expr, label = "CAST(ship_type / 10 AS INTEGER) * 10", "NULL"
        else:
            raise QueryError("group must be type_code, ship_type or ship_class")
        where, args = self._filters(params, need_since=True)
        where.append(f"{expr} IS NOT NULL")
        clause = " AND ".join(where)
        sql = (
            f"SELECT {expr} AS grp, {label} AS label, "
            "COUNT(DISTINCT kind || ':' || key) AS contacts "
            f"FROM sightings WHERE {clause} "
            "GROUP BY grp ORDER BY contacts DESC, grp ASC"
        )
        with self._lock:
            found = self._db.execute(sql, args).fetchall()
        out = []
        for r in found:
            label = r["label"]
            if group == "ship_type":
                label = names.ship_type_name(r["grp"])
            elif group == "ship_class":
                label = names.ship_class_name(r["grp"])
            out.append({"group": r["grp"], "label": label, "contacts": r["contacts"]})
        return out

    @staticmethod
    def _filters(params: dict, need_since: bool) -> tuple[list[str], list]:
        where, args = ["1 = 1"], []
        since = _opt_float(params, "since")
        if since is None and need_since:
            raise QueryError("since is required")
        if since is not None:
            where.append("last_seen >= ?")
            args.append(since)
        until = _opt_float(params, "until")
        if until is not None:
            where.append("first_seen <= ?")
            args.append(until)
        kind = params.get("kind")
        if kind:
            if kind not in ("aircraft", "vessel"):
                raise QueryError("kind must be aircraft or vessel")
            where.append("kind = ?")
            args.append(kind)
        return where, args
