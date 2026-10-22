<!--
SPDX-FileCopyrightText: 2026 Curtis Galloway
SPDX-License-Identifier: Apache-2.0
-->
# field-station

Polls radio receivers (ADS-B readsb, AIS-catcher) and publishes classified
contacts with absolute positions.

## Public repo rule

This repo is public. Never commit private infrastructure: no internal
hostnames or domains, no private network addresses, no machine names, no
home-directory paths, and no real receiver coordinates, in code, tests,
fixtures, docs, or commit messages. Receiver URLs come only from flags or
env (`FIELD_STATION_ADSB_URL`, `FIELD_STATION_AIS_URL`); there are no
default URLs in code. `tests/test_privacy.py` scans tracked file names and contents for private addresses and home paths, and for private terms read from an untracked file (`FIELD_STATION_PRIVATE_TERMS`, default `~/.config/field-station/private-terms`; skipped if absent). Never put those terms in the repo.

The feed never carries bearing or distance from the receiver, nor the
receiver's position (`receiver.json` is read for ranking and log text only).

## Polling

- Aircraft: every 2 s with contacts, 10 s when empty.
- Ships: every 5 s with contacts, 20 s when empty.
- 30 s retry after a failure.
- Caps: 24 aircraft, 40 vessels, nearest first. Aircraft with `seen_pos`
  over 60 s are dropped; ships with no position are dropped.
- Trails: aircraft only, one point per 4 s, 120 s lifetime, 32 points max.

## Consumer contract

The contract with consumers is the event shapes defined in Task 5 of the
implementation plan.

## Conventions

Python via uv, Google style, 88 columns, `uvx pyink`; lint with
`uv run --with pylint pylint`. SPDX two-line headers on every source file.
American spellings.
