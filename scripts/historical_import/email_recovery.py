"""Recover the real email behind an upstream ``sha1(email)`` scoring hash.

The graded exports (``processed/hw-*.csv``, ``processed/project-*.csv``,
``leaderboard.csv``) only carry that hash. The plaintext email every learner
actually used is still sitting in the same repository -- in the weekly raw
Google Form exports, the graduate lists, and (for a few editions) a
leaderboard email reveal -- just never joined back to the graded rows before.
This module does that join once per cohort, in memory, and hands back a
``{hash: email}`` map; the plaintext values are never written to disk by this
module, only passed to whatever creates the learner's account.
"""

from __future__ import annotations

import csv
from pathlib import Path

from .identity import sha1_hex

_EMAIL_COLUMN_HINT = "email"


def _read_email_columns(path: Path) -> list[str]:
    try:
        with path.open(newline="", encoding="utf-8", errors="ignore") as handle:
            reader = csv.DictReader(handle)
            fieldnames = reader.fieldnames or []
            columns = [name for name in fieldnames if _EMAIL_COLUMN_HINT in name.lower()]
            if not columns:
                return []
            emails = []
            for row in reader:
                for column in columns:
                    value = (row.get(column) or "").strip()
                    if value:
                        emails.append(value)
            return emails
    except (OSError, csv.Error):
        return []


def build_hash_to_email_map(csv_paths: tuple[Path, ...]) -> dict[str, str]:
    """Scan every given CSV for an ``*email*`` column and hash what it finds."""

    hash_to_email: dict[str, str] = {}
    for path in csv_paths:
        for email in _read_email_columns(path):
            hash_to_email.setdefault(sha1_hex(email), email)
    return hash_to_email


__all__ = ["build_hash_to_email_map"]
