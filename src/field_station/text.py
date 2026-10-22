# SPDX-FileCopyrightText: 2026 Curtis Galloway
# SPDX-License-Identifier: Apache-2.0
"""Cleaning of over-the-air text.

Callsigns and vessel names are broadcast by whoever is transmitting, so
they are attacker-controlled. Every such string is cleaned before it can
reach the feed or a log line. ADS-B callsigns and AIS names are 6-bit ASCII
by protocol, so the cleaner is an allowlist: only printable ASCII
(0x20-0x7E) survives, which drops controls, bidirectional overrides,
zero-width and other invisible characters, and accented letters alike.
"""

from __future__ import annotations

import re

MAX_LEN = 32

_ANSI = re.compile(
    r"(?:\x1b|\x9b)\[[0-?]*[ -/]*[@-~]"  # CSI sequences
    r"|\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)?"  # OSC sequences
    r"|\x1b[@-Z\\-_]"  # two-character escapes
)
_WHITESPACE = re.compile(r"\s+")
_NOT_PRINTABLE_ASCII = re.compile(r"[^\x20-\x7e]")


def clean_text(value) -> str | None:
    """Returns value reduced to printable ASCII, or None if empty.

    Args:
      value: The raw broadcast string (or anything else, which gives None).
    """
    if not isinstance(value, str):
        return None
    text = _ANSI.sub("", value)
    text = _WHITESPACE.sub(" ", text)
    text = _NOT_PRINTABLE_ASCII.sub("", text)
    text = _WHITESPACE.sub(" ", text).strip()[:MAX_LEN].strip()
    return text or None
