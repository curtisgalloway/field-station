<!--
SPDX-FileCopyrightText: 2026 Curtis Galloway
SPDX-License-Identifier: Apache-2.0
-->
# field-station

Processes outside-world sensor data (ADS-B and AIS contacts first) and
publishes it for others to consume.

Receiver URLs are supplied with flags or the `FIELD_STATION_ADSB_URL` and
`FIELD_STATION_AIS_URL` environment variables. There are no defaults.

## Feed contract

All contact positions are absolute. The feed never carries bearing or
distance from the receiver, nor the receiver's position.

### `GET /ws`

A WebSocket carrying one JSON object per text message. Inbound messages are
ignored. On connect a client receives, in this order:

1. `adsb` - the current ADS-B status (only if ADS-B is configured)
2. `ais` - the current AIS status (only if AIS is configured)
3. `trails`
4. `log_history`

After that it receives live `adsb`, `ais` and `log` events as they occur.
A client that cannot keep up is disconnected. On shutdown the server closes
every socket with code 1001 ("going away").

`adsb` - `{"type": "adsb", "available": bool, "total": int, "msg_rate":
number or null, "aircraft": [...]}`. When `available` is false, only
`type`, `available` and an empty `aircraft` list are present. `total` is the
number of aircraft the receiver reports; `msg_rate` is messages per second.
Each aircraft, nearest the receiver first, at most 24:

| Field | Meaning |
| --- | --- |
| `hex` | ICAO 24-bit address, lowercase hex string |
| `flight` | Callsign, printable ASCII, at most 32 characters, or null |
| `alt_ft` | Altitude in feet (0 when on the ground) |
| `ground` | True when the aircraft reports being on the ground |
| `gs_kt` | Ground speed in knots |
| `track` | Track over ground in degrees true |
| `lat`, `lon` | Position in decimal degrees (WGS 84) |
| `seen` | Seconds since any message was last heard |
| `kind` | `airliner`, `light`, `rotor` or `other` |

Aircraft without a position, or whose position is more than 60 s old, are
not listed.

`ais` - `{"type": "ais", "available": bool, "total": int, "vessels": [...]}`.
When `available` is false, only `type`, `available` and an empty `vessels`
list are present. `total` counts vessels (not buoys or base stations),
located or not. Each vessel, nearest first, at most 40:

| Field | Meaning |
| --- | --- |
| `mmsi` | Maritime Mobile Service Identity |
| `name` | Vessel name, printable ASCII, at most 32 characters, or null |
| `sog_kt` | Speed over ground in knots |
| `cog` | Course over ground in degrees true |
| `heading` | True heading in degrees |
| `lat`, `lon` | Position in decimal degrees (WGS 84) |
| `seen_s` | Seconds since the last signal |
| `kind` | `commercial`, `working`, `pleasure` or `mark` (a base station, beacon or aid to navigation, not a vessel) |

Vessels with no position are not listed.

`trails` - `{"type": "trails", "aircraft": {hex: [[lat, lon, t_ms], ...]}}`.
Aircraft only, oldest point first, one point per 4 s, at most 32 points and
120 s of history per aircraft. `lat` and `lon` are decimal degrees; `t_ms` is
epoch milliseconds.

`log_history` - `{"type": "log_history", "entries": [...]}`: the most recent
80 log entries, oldest first. `log` - `{"type": "log", "entry": {...}}`: one
new entry. An entry is `{"ts": number, "source": "adsb" or "ais", "text":
string}`, where `ts` is epoch seconds. Text is one of `contact <ident>`,
`<n> new contacts`, `vessel <ident>`, `<n> new vessels` or `receiver
online` / `receiver offline`.

### `GET /healthz`

`200` with body `ok` when every configured source is available; otherwise
`503` with `unavailable: ` and the names of the sources that are down.

### `GET /statusz`

JSON: `{"sources": {"adsb": {"available": bool, "contacts": int,
"seconds_since_poll": number or null}, "ais": {...}}, "clients": int,
"version": string}`. Only configured sources appear. `seconds_since_poll`
is the time since the last successful poll, or null if there has been none.

Licensed under Apache 2.0.

## Spotter API

An optional ranking of every contact heard, with a history, served over a
token-protected HTTP API separate from the feed above (the feed is
unchanged and still carries no distance or bearing). The design, scoring
rules and endpoint fields are in [docs/spotter.md](docs/spotter.md).

- `GET /spotter/current` - the current pick and its change sequence number
- `GET /spotter/history` - past sightings, best-scoring or newest first
- `GET /spotter/counts` - distinct contacts per type group

Every request needs `Authorization: Bearer <token>`, with the token set in
`FIELD_STATION_SPOTTER_TOKEN`. With no token set, `/spotter/*` answers 503;
a wrong or missing token gets 401. History is kept in a SQLite file named
by `--spotter-db` (env `FIELD_STATION_SPOTTER_DB`); without one the spotter
runs in memory and the history endpoints answer 503. In a container, mount
a volume and point the database at it (for example
`FIELD_STATION_SPOTTER_DB=/data/spotter.db`). `--spotter-quiet` (default
`22:00-07:00`) and `--spotter-tz` (default `America/Los_Angeles`) set when
the pick never changes.
