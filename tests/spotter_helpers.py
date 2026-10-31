# SPDX-FileCopyrightText: 2026 Curtis Galloway
# SPDX-License-Identifier: Apache-2.0
"""Shared builders for the spotter tests."""

from field_station.spotter import model


def aircraft(hex_id="abc123", dist=20.0, now=1000.0, **raw) -> model.Sighting:
    raw = {"hex": hex_id, "t": "B738", "category": "A3", "alt_baro": 30000, **raw}
    return model.Sighting("aircraft", hex_id, now, now, dist, 90.0, raw)


def vessel(mmsi="111", dist=20.0, now=1000.0, **raw) -> model.Sighting:
    raw = {"mmsi": int(mmsi), "shiptype": 0, "to_bow": 5, "to_stern": 5, **raw}
    return model.Sighting("vessel", mmsi, now, now, dist, 90.0, raw)
