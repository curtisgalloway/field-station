# SPDX-FileCopyrightText: 2026 Curtis Galloway
# SPDX-License-Identifier: Apache-2.0
"""Cleaning of over-the-air text."""

import pytest

from field_station.text import clean_text


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("EVIL‮FDP", "EVILFDP"),
        ("ZERO​WIDTH", "ZEROWIDTH"),
        ("﻿BOM", "BOM"),
        ("José", "Jos"),
        ("\x1b[31mRED\x1b[0m", "RED"),
        ("A\nB\x00C", "A BC"),
        ("  two   words ", "two words"),
        ("​‮", None),
        (None, None),
        (5, None),
    ],
)
def test_clean_text(raw, expected):
    assert clean_text(raw) == expected


def test_clamped_to_32():
    assert clean_text("x" * 100) == "x" * 32
