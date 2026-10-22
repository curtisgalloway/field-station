# SPDX-FileCopyrightText: 2026 Curtis Galloway
# SPDX-License-Identifier: Apache-2.0
"""This repo is public: no private infrastructure may appear in tracked files.

Two checks. The generic patterns (private network addresses, home-directory
paths) live here and always run. The private terms (machine names, internal
domains) are never stored in the repo: they are read from an untracked file,
`FIELD_STATION_PRIVATE_TERMS` or `~/.config/field-station/private-terms`, one
term per line. Hits are reported as path:line plus a term index, never the term.
"""

import os
import pathlib
import re
import stat
import subprocess

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
DEFAULT_TERMS = "~/.config/field-station/private-terms"

_OCT = r"(?:25[0-5]|2[0-4]\d|1\d\d|[1-9]?\d)"
_PRE = r"(?<![\d.])"
_POST = r"(?!\d)(?!\.\d)"
GENERIC = [
    re.compile(_PRE + rf"10\.{_OCT}\.{_OCT}\.{_OCT}" + _POST),
    re.compile(_PRE + rf"172\.(?:1[6-9]|2\d|3[01])\.{_OCT}\.{_OCT}" + _POST),
    re.compile(_PRE + rf"192\.168\.{_OCT}\.{_OCT}" + _POST),
    re.compile(
        _PRE + rf"100\.(?:6[4-9]|[7-9]\d|1[01]\d|12[0-7])\.{_OCT}\.{_OCT}" + _POST
    ),
    re.compile(r"/(?:Users|home)/[^/\s\"']+/"),
    re.compile(r"[A-Za-z]:\\{1,2}Users\\{1,2}[^\\\s\"']+", re.IGNORECASE),
]


def _tracked():
    """Yields (path, mode) for every tracked entry."""
    raw = subprocess.run(
        ["git", "ls-files", "-s", "-z"], cwd=ROOT, check=True, capture_output=True
    ).stdout.decode("utf-8", "surrogateescape")
    for entry in raw.split("\0"):
        if not entry:
            continue
        meta, _, name = entry.partition("\t")
        yield name, meta.split()[0]


def _scan(patterns):
    """Returns 'path:line pattern#N' hits across names and contents."""
    hits = []
    for name, mode in _tracked():
        if mode in ("120000", "160000"):
            continue
        for index, pattern in enumerate(patterns):
            if pattern.search(name):
                hits.append(f"{name}:<filename> #{index}")
        path = ROOT / name
        try:
            info = path.lstat()
        except OSError as err:
            hits.append(f"{name}: unreadable ({err.__class__.__name__})")
            continue
        if stat.S_ISLNK(info.st_mode):
            continue
        if not stat.S_ISREG(info.st_mode):
            hits.append(f"{name}: tracked but not a regular file")
            continue
        try:
            text = path.read_bytes().decode("utf-8", "replace")
        except OSError as err:
            hits.append(f"{name}: unreadable ({err.__class__.__name__})")
            continue
        for number, line in enumerate(text.splitlines(), 1):
            for index, pattern in enumerate(patterns):
                if pattern.search(line):
                    hits.append(f"{name}:{number} #{index}")
    return hits


def test_no_private_addresses_or_paths():
    hits = _scan(GENERIC)
    assert not hits, f"private addresses or paths in: {hits}"


def test_no_private_terms():
    path = pathlib.Path(
        os.environ.get("FIELD_STATION_PRIVATE_TERMS") or DEFAULT_TERMS
    ).expanduser()
    if not path.is_file():
        pytest.skip(
            "private-terms file not found; set FIELD_STATION_PRIVATE_TERMS or "
            f"create {DEFAULT_TERMS} (one term per line) to run this check"
        )
    terms = [
        line.strip()
        for line in path.read_text().splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]
    if not terms:
        pytest.skip("private-terms file has no terms; nothing to check")
    patterns = [
        re.compile(r"(?<!\w)" + re.escape(term) + r"(?!\w)", re.IGNORECASE)
        for term in terms
    ]
    hits = _scan(patterns)
    assert not hits, f"private terms in (path:line #term-index): {hits}"
