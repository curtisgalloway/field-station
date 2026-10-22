# SPDX-FileCopyrightText: 2026 Curtis Galloway
# SPDX-License-Identifier: Apache-2.0
"""Cleaning of over-the-air text.

Callsigns and vessel names are broadcast by whoever is transmitting, so
they are attacker-controlled. Every such string is cleaned before it can
reach the feed or a log line.
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
_CONTROLS = re.compile(r"[\x00-\x1f\x7f-\x9f]")


def clean_text(value) -> str | None:
    """Returns value stripped of escapes and controls, or None if empty.

    Args:
      value: The raw broadcast string (or anything else, which gives None).
    """
    if not isinstance(value, str):
        return None
    text = _ANSI.sub("", value)
    text = _WHITESPACE.sub(" ", text)
    text = _CONTROLS.sub("", text)
    text = _WHITESPACE.sub(" ", text).strip()[:MAX_LEN].strip()
    return text or None
