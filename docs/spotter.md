<!--
SPDX-FileCopyrightText: 2026 Curtis Galloway
SPDX-License-Identifier: Apache-2.0
-->
# Spotter: the most interesting contact

**Terms**
- **Contact**: one aircraft (ICAO hex) or vessel (MMSI).
- **Sighting**: a contact seen in a time window, with its closest pass.
- **Pick**: the single highest-scoring sighting of the last hour, chosen for an
  ambient picture display.
- **Renderer**: a separate program (not in this repo) that turns the pick
  into a picture and puts it on a display.

## What it adds

A `spotter` module that ranks every contact heard by how interesting it is,
keeps a history, and serves both through a token-protected HTTP API. The
public WebSocket feed and its contract are unchanged: it still never carries
bearing, distance or the receiver position.

## Data path

The pollers already fetch the full receiver documents. The spotter reads
those full documents in process, **before** the feed's caps (24 aircraft,
40 vessels) and field reductions, so it sees every contact and every field,
including readsb's database fields (`r`, `t`, `desc`, `ownOp`, `year`,
`dbFlags`) and AIS `shiptype`, `to_bow`, `to_stern`, `country`.

Distance and bearing are computed from the receiver position the ADS-B poller
already reads (and, for AIS, from AIS-catcher's own `distance`/`bearing`).

## Sightings and history

- An in-memory one-hour table, keyed `aircraft:<hex>` / `vessel:<mmsi>`,
  holding each contact's closest observation, first and last seen.
- A SQLite file (`--spotter-db`, env `FIELD_STATION_SPOTTER_DB`) with one row
  per sighting per hour window: kind, key, ident, registration, type code,
  description, operator, country, year, ship type, length, closest distance,
  bearing, altitude at closest pass, first/last seen, score, reasons (JSON),
  and whether it became the pick. Rows are kept 400 days.
- No database path configured: the spotter runs in memory only and the
  history endpoints answer 503.

## Scoring

Additive; every rule adds a short reason string.

Aircraft: emergency or squawk 7500/7600/7700 +100; `dbFlags`
bit 1 (military) +60; bit 2 (interesting) +40; category B* +35; A7 +25;
`year` before 1980 +30; A5 +15; rare in the world +40 (the hand-kept list in
`spotter/world_rare.py`: outsized freighters, low-production military types,
historic aircraft still flying); type rarity up to +60; closeness up to +15
(under 5 nm and at or below 5000 ft); no type information −10.

Vessels: ship type 35 military +60; 51 search and rescue +40; 55 law
enforcement +35; 58 medical +30; 33/34 +25; 31/32 +20; 60–69 +20; 52 +15;
70–89 +15; length over 150 m +25; 36/37 +5; type rarity up to +60;
closeness up to +15.

Any contact that was the pick in the last 24 h: −50.

Type rarity is the base of the score and means "rarely seen here", not rare
in the world (that is the separate +40 list). It is the family's typical rate
at this receiver, per week, from up to 90 days of history, worth up to +60 on
a log scale: none at weekly or more, about half at monthly, full at about
once in four months or less, or never. The sighting being scored is left out
of its own count, and the rate divides by the days the history covers. Off
until the history holds 20 contacts. Aircraft are counted by family
(`spotter/families.py`: all Cessna singles, all 737s, all Cirrus and so on;
an unlisted type code is its own family), vessels by `ship:<shiptype>`. The
history itself stays keyed by exact type code. The reason reads "rarely seen
here: <family>, about 2 a month" or "none in the last N days".

Seed history: `--spotter-seed` / `FIELD_STATION_SPOTTER_SEED` names a JSON
file `{"days": {"YYYY-MM-DD": {"<type code>": count}}}` of estimated past
contacts, loaded at startup into the type history for days the database does
not have (our own sightings always win; days older than 90 are dropped). A
missing file is skipped. The spotter renderer project builds it from the
adsb.lol daily archive (ODbL), weighting each aircraft by how likely this
receiver was to hear it, so rarity has a full 90 days from the first day.

## Choosing when the pick changes

Checked every minute:
- Never during quiet hours (`--spotter-quiet`, default `22:00-07:00`, in
  `--spotter-tz`, default `America/Los_Angeles`).
- A new pick when the top sighting differs from the current pick, scores at
  least 40, and 15 min have passed since the last change.
- Otherwise at most every 60 min, and only if the top sighting changed.
- Nothing seen: no change.

Each change increments a sequence number.

## HTTP API

All endpoints require `Authorization: Bearer <token>`, token from env
`FIELD_STATION_SPOTTER_TOKEN`. Token unset: every `/spotter/*` endpoint
answers 503. Wrong or missing token: 401.

### `GET /spotter/current`

`{"seq": int, "changed_at": epoch_s, "pick": {...} | null}`. The pick:

| Field | Meaning |
| --- | --- |
| `kind` | `aircraft` or `vessel` |
| `key` | hex or MMSI, as a string |
| `ident` | callsign or vessel name, or null |
| `registration` | aircraft registration, or null |
| `type_code` | ICAO type designator, or null |
| `description` | type description, or AIS ship type name |
| `operator` | aircraft operator, or null |
| `country` | vessel flag country, or null |
| `year` | aircraft build year, or null |
| `ship_type` | AIS ship type number, or null |
| `length_m` | vessel length, or null |
| `distance_nm`, `bearing_deg` | closest pass, from the receiver |
| `altitude_ft` | altitude at closest pass, or null |
| `seen_at` | epoch seconds of the closest pass |
| `score` | number |
| `reasons` | list of strings, most points first |

### `GET /spotter/ranked`

`{"sightings": [{...}, ...]}`: the best sightings in the table right now,
best first, as the pick is scored (repeat penalty included). Each entry has
the pick's fields. `limit` (default 10, at most 25). A display uses it to
fall back to the next contact when it cannot show the pick.

### `GET /spotter/history`

Query: `since` (epoch s, required), `until`, `kind`, `type_code`,
`ship_type_min`, `ship_type_max`, `picked` (bool), `order` (`score` default,
or `time`), `limit` (default 20, max 200). Returns `{"sightings": [...]}`,
each with the pick fields above plus `first_seen`, `last_seen`, `picked`.

### `GET /spotter/counts`

Query: `since`, `until`, `kind`, `group` (`type_code`, `ship_type`, or
`ship_class`, which folds AIS types into tens: 30s fishing/towing/special,
60s passenger, 70s cargo, 80s tanker…). Returns `{"counts": [{"group":
..., "label": ..., "contacts": int}]}` with distinct contacts per group.
