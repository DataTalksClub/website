#!/usr/bin/env python3
"""Privacy-safe usage measurement for the t1832u follow-up retirement surfaces.

Intake 81995d56 (audit items B12/B13/B14) keeps three compatibility surfaces
behind a measurement gate: staff ``/cadmin/*`` redirects, the legacy content
digests on ``ContentRelease``, and the compatibility token identity API plus
legacy raw-``Token`` API use. This script produces the counts-only evidence
the intake asks for, with no personally identifying information.

Inputs
------
* Gunicorn access-log files in the deployed format from ``entrypoint.sh``::

      %(t)s "%(m)s %(U)s %(H)s" %(s)s %(b)s %(D)s ...

  ``%(U)s`` is the path without the query string; the client address and
  every request header are excluded by design (see
  ``core/gunicorn_logging.py``). The log therefore cannot distinguish raw
  ``Token`` from ``Bearer`` management-credential API calls: that split is
  reported as unavailable and needs an application-level aggregate, not
  access logs.

* The Django database (only when ``--count-releases`` is passed): counts
  ``ContentRelease`` rows grouped by contract-digest class. Configure the
  database through the standard ``DJANGO_SETTINGS_MODULE``/environment
  variables; this script performs no writes.

Output is one JSON object on stdout with counts only: the observed time
range, lines parsed/skipped, ``/cadmin*`` hits,
``/api/account/identity/`` hits, and the release-digest groups. Malformed
lines are counted as skipped, never echoed.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Literal

CADMIN_PREFIX = "/cadmin"
COMPAT_IDENTITY_PATHS = frozenset({"/api/account/identity/", "/api/account/identity"})

DigestClass = Literal["current", "retired_flat_podcast_routes", "legacy", "unknown"]

CURRENT_DIGEST = "a9f435476325cbb0e1bc573d1ec991dda6ea9bc08764fce8c7e27b9d5209f792"
RETIRED_FLAT_PODCAST_ROUTES_DIGEST = (
    "31f505350566bfcde0a30109dadcfb3565042fd395b4c1bd151966f94d361332"
)
LEGACY_DIGEST = "50f875806217865ef35b74f58ed885c4b5c832284391dbea7f84344d3416f66d"

DIGEST_CLASSES: Mapping[str, DigestClass] = {
    CURRENT_DIGEST: "current",
    RETIRED_FLAT_PODCAST_ROUTES_DIGEST: "retired_flat_podcast_routes",
    LEGACY_DIGEST: "legacy",
}

# The deployed Gunicorn request line: timestamp, "METHOD PATH PROTO", status.
# Anything after the duration (request/correlation ids) is ignored; a line
# that does not match is counted as skipped, never echoed back.
ACCESS_LINE_PATTERN = re.compile(
    r"^\[(?P<timestamp>[^\]]+)\]\s+"
    r'"(?P<method>[A-Z]+)\s+(?P<path>\S+)\s+HTTP/[\d.]+"\s+'
    r"(?P<status>\d{3})\s+\S+\s+\S+"
)

TIMESTAMP_FORMAT = "%d/%b/%Y:%H:%M:%S %z"

RAW_TOKEN_SPLIT_NOTE = (
    "unavailable_from_access_logs: the deployed access format excludes "
    "request headers by design, so raw-Token versus Bearer management-"
    "credential use cannot be separated from access logs"
)


def classify_contract_digest(digest: str) -> DigestClass:
    """Return the retirement class of one ``public_contracts_sha256`` value."""

    return DIGEST_CLASSES.get(digest, "unknown")


def is_cadmin_path(path: str) -> bool:
    """Match the staff redirect surface: ``/cadmin`` and everything below it."""

    return path == CADMIN_PREFIX or path.startswith(CADMIN_PREFIX + "/")


def is_compat_identity_path(path: str) -> bool:
    """Match the compatibility token identity endpoint (slashful or not)."""

    return path in COMPAT_IDENTITY_PATHS


@dataclass
class AccessLogCounts:
    lines_total: int = 0
    lines_parsed: int = 0
    lines_skipped: int = 0
    cadmin_hits: int = 0
    compat_identity_hits: int = 0
    first_timestamp: datetime | None = None
    last_timestamp: datetime | None = None

    def record(self, *, path: str, timestamp: datetime | None) -> None:
        if is_cadmin_path(path):
            self.cadmin_hits += 1
        if is_compat_identity_path(path):
            self.compat_identity_hits += 1
        if timestamp is not None and (
            self.first_timestamp is None or timestamp < self.first_timestamp
        ):
            self.first_timestamp = timestamp
        if timestamp is not None and (
            self.last_timestamp is None or timestamp > self.last_timestamp
        ):
            self.last_timestamp = timestamp


def parse_timestamp(raw: str) -> datetime | None:
    """Parse one Gunicorn ``%(t)s`` timestamp, or return None when unreadable."""

    try:
        return datetime.strptime(raw, TIMESTAMP_FORMAT)
    except ValueError:
        return None


def parse_access_log_lines(lines: Iterable[str]) -> AccessLogCounts:
    """Count retirement-surface hits in deployed-format access-log lines.

    Counts only: paths are classified into the two watched prefix classes
    and never retained. Malformed lines increment ``lines_skipped``.
    """

    counts = AccessLogCounts()
    for line in lines:
        counts.lines_total += 1
        match = ACCESS_LINE_PATTERN.match(line.strip())
        if match is None:
            counts.lines_skipped += 1
            continue
        counts.lines_parsed += 1
        counts.record(
            path=match.group("path"),
            timestamp=parse_timestamp(match.group("timestamp")),
        )
    return counts


def summarize_release_digests(digests: Iterable[str]) -> dict[str, int]:
    """Group contract-digest values into retirement classes (counts only)."""

    summary: dict[str, int] = {
        "current": 0,
        "retired_flat_podcast_routes": 0,
        "legacy": 0,
        "unknown": 0,
    }
    for digest in digests:
        summary[classify_contract_digest(digest)] += 1
    return summary


def count_release_digests() -> dict[str, int]:
    """Count ``ContentRelease`` rows per contract-digest class.

    Importing the ORM lazily keeps the pure log-parsing helpers usable
    without Django settings configured.
    """

    from content.models import ContentRelease

    digests = ContentRelease.objects.values_list("public_contracts_sha256", flat=True).iterator()
    return summarize_release_digests(digests)


def build_report(
    log_counts: AccessLogCounts,
    release_digests: dict[str, int] | None,
) -> dict[str, object]:
    """Assemble the counts-only JSON report (no paths, ids, or tokens)."""

    window: dict[str, str] | None = None
    if log_counts.first_timestamp is not None and log_counts.last_timestamp is not None:
        window = {
            "first": log_counts.first_timestamp.isoformat(),
            "last": log_counts.last_timestamp.isoformat(),
        }
    return {
        "window": window,
        "lines_total": log_counts.lines_total,
        "lines_parsed": log_counts.lines_parsed,
        "lines_skipped": log_counts.lines_skipped,
        "cadmin_hits": log_counts.cadmin_hits,
        "compat_identity_hits": log_counts.compat_identity_hits,
        "raw_token_split": RAW_TOKEN_SPLIT_NOTE,
        "release_digests": release_digests,
    }


def read_log_lines(paths: Sequence[str]) -> list[str]:
    """Read access-log files, tolerating undecodable bytes conservatively."""

    lines: list[str] = []
    for raw in paths:
        lines.extend(Path(raw).read_text(encoding="utf-8", errors="replace").splitlines())
    return lines


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Count retirement-surface use (counts only, no PII).",
    )
    parser.add_argument(
        "--access-log",
        action="append",
        default=[],
        help="Gunicorn access-log file in the deployed format (repeatable).",
    )
    parser.add_argument(
        "--count-releases",
        action="store_true",
        help="Also count ContentRelease rows per digest class via Django.",
    )
    args = parser.parse_args(argv)

    log_counts = parse_access_log_lines(read_log_lines(args.access_log))

    release_digests: dict[str, int] | None = None
    if args.count_releases:
        import django

        django.setup()
        release_digests = count_release_digests()

    json.dump(build_report(log_counts, release_digests), sys.stdout, indent=2, sort_keys=True)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
