"""Counts-only measurement for the t1832u follow-up retirement surfaces.

Intake 81995d56. The log fixtures below are synthetic lines in the deployed
Gunicorn format; they carry no client addresses, tokens, or user data, and
the parser under test retains none either.
"""

from __future__ import annotations

from django.test import SimpleTestCase, TestCase
from django.utils import timezone

from content.models import (
    LEGACY_PUBLIC_CONTRACT_DIGEST,
    PUBLIC_CONTRACT_DIGEST,
    RETIRED_FLAT_PODCAST_ROUTES_DIGEST,
    ContentRelease,
    ContentSource,
)
from scripts.measure_retirement_surfaces import (
    classify_contract_digest,
    count_release_digests,
    parse_access_log_lines,
    summarize_release_digests,
)

CADMIN_LINE = (
    '[03/Nov/2026:10:00:00 +0000] "GET /cadmin/cloudwatch/ HTTP/1.1" '
    '302 0 0.012 request_id="-" correlation_id="-"'
)
CADMIN_ROOT_LINE = (
    '[03/Nov/2026:10:01:00 +0000] "POST /cadmin HTTP/1.1" '
    '302 0 0.004 request_id="-" correlation_id="-"'
)
COMPAT_IDENTITY_LINE = (
    '[03/Nov/2026:10:02:00 +0000] "GET /api/account/identity/ HTTP/1.1" '
    '200 48 0.008 request_id="-" correlation_id="-"'
)
COMPAT_IDENTITY_SLASHLESS_LINE = (
    '[03/Nov/2026:10:03:00 +0000] "GET /api/account/identity HTTP/1.1" '
    '301 0 0.002 request_id="-" correlation_id="-"'
)
UNRELATED_LINE = (
    '[03/Nov/2026:10:04:00 +0000] "GET /courses HTTP/1.1" '
    '200 1234 0.020 request_id="-" correlation_id="-"'
)
BAD_TIMESTAMP_LINE = (
    '[not-a-timestamp] "GET /cadmin/ HTTP/1.1" 302 0 0.003 request_id="-" correlation_id="-"'
)


class AccessLogParserTests(SimpleTestCase):
    def test_counts_each_surface_and_ignores_unrelated_paths(self) -> None:
        counts = parse_access_log_lines(
            [CADMIN_LINE, CADMIN_ROOT_LINE, COMPAT_IDENTITY_LINE, UNRELATED_LINE]
        )

        self.assertEqual(counts.lines_total, 4)
        self.assertEqual(counts.lines_parsed, 4)
        self.assertEqual(counts.lines_skipped, 0)
        self.assertEqual(counts.cadmin_hits, 2)
        self.assertEqual(counts.compat_identity_hits, 1)

    def test_counts_slashless_identity_path_with_the_endpoint(self) -> None:
        counts = parse_access_log_lines([COMPAT_IDENTITY_SLASHLESS_LINE])

        self.assertEqual(counts.compat_identity_hits, 1)
        self.assertEqual(counts.cadmin_hits, 0)

    def test_skips_malformed_lines_without_echoing_them(self) -> None:
        counts = parse_access_log_lines(
            [CADMIN_LINE, "this is not a log line", "", COMPAT_IDENTITY_LINE]
        )

        self.assertEqual(counts.lines_total, 4)
        self.assertEqual(counts.lines_parsed, 2)
        self.assertEqual(counts.lines_skipped, 2)
        self.assertEqual(counts.cadmin_hits, 1)
        self.assertEqual(counts.compat_identity_hits, 1)

    def test_reports_the_observed_window_from_parseable_timestamps(self) -> None:
        counts = parse_access_log_lines([CADMIN_LINE, COMPAT_IDENTITY_LINE])

        assert counts.first_timestamp is not None
        assert counts.last_timestamp is not None
        self.assertLess(counts.first_timestamp, counts.last_timestamp)
        self.assertEqual(counts.first_timestamp.day, 3)

    def test_counts_hits_with_unreadable_timestamps_outside_the_window(self) -> None:
        counts = parse_access_log_lines([BAD_TIMESTAMP_LINE, CADMIN_LINE])

        self.assertEqual(counts.cadmin_hits, 2)
        self.assertEqual(counts.lines_skipped, 0)
        assert counts.first_timestamp is not None
        assert counts.last_timestamp is not None
        self.assertEqual(counts.first_timestamp, counts.last_timestamp)

    def test_empty_input_reports_no_window(self) -> None:
        counts = parse_access_log_lines([])

        self.assertEqual(counts.lines_total, 0)
        self.assertEqual(counts.cadmin_hits, 0)
        self.assertEqual(counts.compat_identity_hits, 0)
        self.assertIsNone(counts.first_timestamp)
        self.assertIsNone(counts.last_timestamp)


class DigestClassifierTests(SimpleTestCase):
    def test_classifies_each_supported_digest(self) -> None:
        self.assertEqual(classify_contract_digest(PUBLIC_CONTRACT_DIGEST), "current")
        self.assertEqual(
            classify_contract_digest(RETIRED_FLAT_PODCAST_ROUTES_DIGEST),
            "retired_flat_podcast_routes",
        )
        self.assertEqual(classify_contract_digest(LEGACY_PUBLIC_CONTRACT_DIGEST), "legacy")

    def test_groups_unknown_digests_without_dropping_them(self) -> None:
        summary = summarize_release_digests(
            [
                PUBLIC_CONTRACT_DIGEST,
                LEGACY_PUBLIC_CONTRACT_DIGEST,
                RETIRED_FLAT_PODCAST_ROUTES_DIGEST,
                "0" * 64,
            ]
        )

        self.assertEqual(
            summary,
            {
                "current": 1,
                "retired_flat_podcast_routes": 1,
                "legacy": 1,
                "unknown": 1,
            },
        )


class ReleaseDigestCountTests(TestCase):
    def _create_release(self, source: ContentSource, sequence: int, digest: str) -> None:
        ContentRelease.objects.create(
            source=source,
            sequence=sequence,
            commit_sha=f"{sequence:040x}",
            parser_version="parser-v1",
            rendering_version="renderer-v1",
            requested_at=timezone.now(),
            public_contracts_sha256=digest,
        )

    def test_counts_rows_per_digest_class(self) -> None:
        source = ContentSource.objects.create(
            stable_id="measurement-fixture",
            display_name="Measurement fixture",
            repository_owner="DataTalksClub",
            repository_name="measurement-fixture",
            branch="main",
            adapter_type="fixture",
            mount_path="/",
        )
        self._create_release(source, 1, PUBLIC_CONTRACT_DIGEST)
        self._create_release(source, 2, PUBLIC_CONTRACT_DIGEST)
        self._create_release(source, 3, LEGACY_PUBLIC_CONTRACT_DIGEST)
        self._create_release(source, 4, RETIRED_FLAT_PODCAST_ROUTES_DIGEST)

        self.assertEqual(
            count_release_digests(),
            {
                "current": 2,
                "retired_flat_podcast_routes": 1,
                "legacy": 1,
                "unknown": 0,
            },
        )
