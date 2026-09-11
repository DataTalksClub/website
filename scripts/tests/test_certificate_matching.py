"""Certificate matching never guesses on an ambiguous display name (REL-17).

A display name is the only field shared by the graduate CSVs and the
certificate JSON, and two different graduates can share one.  A URL is
attached only when the normalized name is unique on both sides; an ambiguous
name leaves every enrollment untouched and is counted for a reviewed explicit
mapping instead of handing out the last-seen URL, as the old last-wins
dictionary did.
"""

from __future__ import annotations

import csv
import json
import shutil
import tempfile
from pathlib import Path

from django.test import TestCase

import scripts.prod
from courses.models import Cohort, Course, Enrollment
from scripts.prod.legacy_zoomcamp.certificate_import import (
    import_edition_certificates,
)
from scripts.prod.legacy_zoomcamp.editions import EditionSource
from scripts.prod.legacy_zoomcamp.identity import sha1_hex

PROD_ROOT = Path(scripts.prod.__file__).resolve().parent

GRADUATE_ONE = "graduate-one@example.invalid"
GRADUATE_TWO = "graduate-two@example.invalid"
SHARED_NAME = "Alexa Doe"
UNIQUE_NAME = "Solo Graduate"
CERTIFICATE_A = "https://certificates.invalid/alexa-a"
CERTIFICATE_B = "https://certificates.invalid/alexa-b"
CERTIFICATE_SOLO = "https://certificates.invalid/solo"


class CertificateMatchingTests(TestCase):
    def setUp(self) -> None:
        super().setUp()
        # Scratch data belongs in the project-local .tmp/, never a system temp dir.
        scratch_root = PROD_ROOT.parents[1] / ".tmp"
        scratch_root.mkdir(parents=True, exist_ok=True)
        self.root = Path(tempfile.mkdtemp(prefix="certificate-matching-test-", dir=scratch_root))
        self.addCleanup(shutil.rmtree, self.root, True)

        family = Course.objects.create(slug="mlops-zoomcamp", title="MLOps Zoomcamp")
        self.cohort = Cohort.objects.create(
            course=family,
            slug="mlops-zoomcamp-2022",
            identifier="2022",
            year=2022,
            title="MLOps Zoomcamp 2022",
        )

        self.data = self.root / "old" / "mlops-zoomcamp-2022"

    def _write_graduates(self, rows: list[tuple[str, str]]) -> Path:
        path = self.data / "graduates.csv"
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.writer(handle)
            writer.writerow(["email", "name"])
            writer.writerows(rows)
        return path

    def _write_certificates(self, entries: list[tuple[str, str]]) -> Path:
        path = self.data / "certificates" / "graduates.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(
                [
                    {
                        "variables": {
                            "text": {"name": name},
                            "links": {"certificate-id": url},
                        }
                    }
                    for name, url in entries
                ]
            ),
            encoding="utf-8",
        )
        return path

    def _edition(self, *, csv_path: Path, json_path: Path) -> EditionSource:
        return EditionSource(
            cohort_slug="mlops-zoomcamp-2022",
            course_slug="mlops-zoomcamp",
            course_title="MLOps Zoomcamp",
            year=2022,
            start_month=1,
            homeworks=(),
            projects=(),
            certificate_csvs=(csv_path,),
            certificates_json=(json_path,),
            email_source_csvs=(),
        )

    def _import(self, *, csv_path: Path, json_path: Path):
        return import_edition_certificates(
            self.cohort, self._edition(csv_path=csv_path, json_path=json_path)
        )

    def enrollment_urls(self) -> list[str | None]:
        return list(
            Enrollment.objects.order_by("student__username").values_list(
                "certificate_url", flat=True
            )
        )

    def test_two_graduates_sharing_a_name_match_nothing(self) -> None:
        csv_path = self._write_graduates([(GRADUATE_ONE, SHARED_NAME), (GRADUATE_TWO, SHARED_NAME)])
        json_path = self._write_certificates([(SHARED_NAME, CERTIFICATE_A)])

        result = self._import(csv_path=csv_path, json_path=json_path)

        self.assertEqual(result.graduates_seen, 2)
        self.assertEqual(result.certificate_urls_matched, 0)
        self.assertEqual(result.graduates_blocked_ambiguous_name, 2)
        # The last-wins URL reached nobody: both enrollments stay empty.
        self.assertEqual(self.enrollment_urls(), [None, None])

    def test_a_shared_certificate_source_name_never_uses_last_wins(self) -> None:
        csv_path = self._write_graduates([(GRADUATE_ONE, SHARED_NAME)])
        json_path = self._write_certificates(
            [(SHARED_NAME, CERTIFICATE_A), (SHARED_NAME.lower(), CERTIFICATE_B)]
        )

        result = self._import(csv_path=csv_path, json_path=json_path)

        self.assertEqual(result.certificate_urls_matched, 0)
        self.assertEqual(result.graduates_blocked_ambiguous_name, 1)
        # The old dictionary silently kept CERTIFICATE_B here.
        self.assertEqual(result.source_names_with_multiple_certificates, 1)
        self.assertEqual(self.enrollment_urls(), [None])

    def test_a_unique_exact_match_still_attaches_and_replays_stably(self) -> None:
        csv_path = self._write_graduates([(GRADUATE_ONE, UNIQUE_NAME)])
        json_path = self._write_certificates([(UNIQUE_NAME, CERTIFICATE_SOLO)])

        first = self._import(csv_path=csv_path, json_path=json_path)
        self.assertEqual(first.certificate_urls_matched, 1)
        self.assertEqual(first.graduates_blocked_ambiguous_name, 0)
        self.assertEqual(self.enrollment_urls(), [CERTIFICATE_SOLO])

        # Repeated imports are stable: no duplicate rows, same decision.
        replay = self._import(csv_path=csv_path, json_path=json_path)
        self.assertEqual(replay.graduates_seen, 1)
        self.assertEqual(replay.certificate_urls_matched, 1)
        self.assertEqual(self.enrollment_urls(), [CERTIFICATE_SOLO])
        self.assertEqual(Enrollment.objects.count(), 1)

    def test_a_missing_certificate_is_unmatched_not_blocked(self) -> None:
        csv_path = self._write_graduates([(GRADUATE_ONE, UNIQUE_NAME)])
        json_path = self._write_certificates([(SHARED_NAME, CERTIFICATE_A)])

        result = self._import(csv_path=csv_path, json_path=json_path)

        self.assertEqual(result.certificate_urls_matched, 0)
        self.assertEqual(result.graduates_blocked_ambiguous_name, 0)
        self.assertEqual(self.enrollment_urls(), [None])

    def test_one_graduate_of_a_shared_name_cannot_claim_a_unique_certificate(
        self,
    ) -> None:
        # Two graduates share "Alexa Doe"; only one is listed with the unique
        # name in the certificate sources.  Name uniqueness on the source side
        # cannot rescue an ambiguous graduate side.
        csv_path = self._write_graduates([(GRADUATE_ONE, SHARED_NAME), (GRADUATE_TWO, SHARED_NAME)])
        json_path = self._write_certificates(
            [(SHARED_NAME, CERTIFICATE_A), (UNIQUE_NAME, CERTIFICATE_SOLO)]
        )

        result = self._import(csv_path=csv_path, json_path=json_path)

        self.assertEqual(result.certificate_urls_matched, 0)
        self.assertEqual(result.graduates_blocked_ambiguous_name, 2)
        self.assertEqual(self.enrollment_urls(), [None, None])


class DirectCertificateUrlTests(TestCase):
    """2021's ML Zoomcamp has a real graduate roster but no ``graduates.json``
    (issue #15): its certificate URL is computed directly, reproducing
    zoomcamp-scoring's own historical hash formula
    (``certificates/mlzoomcamp-2021-batch.py``, commit 8654144) --
    ``sha1_hex(email + certificate_hash_suffix)`` -- rather than matched by
    name. This can never be ambiguous, unlike the name-matched path.
    """

    def setUp(self) -> None:
        super().setUp()
        scratch_root = PROD_ROOT.parents[1] / ".tmp"
        scratch_root.mkdir(parents=True, exist_ok=True)
        self.root = Path(tempfile.mkdtemp(prefix="direct-certificate-url-test-", dir=scratch_root))
        self.addCleanup(shutil.rmtree, self.root, True)

        family = Course.objects.create(slug="ml-zoomcamp", title="Machine Learning Zoomcamp")
        self.cohort = Cohort.objects.create(
            course=family,
            slug="ml-zoomcamp-2021",
            identifier="2021",
            year=2021,
            title="Machine Learning Zoomcamp 2021",
        )

    def _write_roster(self, rows: list[tuple[str, str]]) -> Path:
        path = self.root / "mlzoomcamp-2021-names.csv"
        with path.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.writer(handle)
            writer.writerow(["email", "name"])
            writer.writerows(rows)
        return path

    def _edition(self, *, csv_path: Path) -> EditionSource:
        return EditionSource(
            cohort_slug="ml-zoomcamp-2021",
            course_slug="ml-zoomcamp",
            course_title="Machine Learning Zoomcamp",
            year=2021,
            start_month=9,
            homeworks=(),
            projects=(),
            certificate_csvs=(csv_path,),
            certificates_json=(),
            email_source_csvs=(),
            certificate_hash_suffix="_",
        )

    def test_the_url_matches_the_historical_hash_formula(self) -> None:
        csv_path = self._write_roster([(GRADUATE_ONE, UNIQUE_NAME)])

        result = import_edition_certificates(self.cohort, self._edition(csv_path=csv_path))

        self.assertEqual(result.graduates_seen, 1)
        self.assertEqual(result.certificate_urls_matched, 1)
        self.assertEqual(result.graduates_blocked_ambiguous_name, 0)
        expected_hash = sha1_hex(GRADUATE_ONE + "_")
        self.assertEqual(
            self.enrollment_urls(),
            [f"https://certificate.datatalks.club/mlzoomcamp/2021/{expected_hash}.pdf"],
        )

    def test_two_graduates_sharing_a_name_are_never_ambiguous(self) -> None:
        # Unlike name-matched editions, a shared display name cannot block a
        # direct per-email URL -- there is no name lookup in this path at all.
        csv_path = self._write_roster([(GRADUATE_ONE, SHARED_NAME), (GRADUATE_TWO, SHARED_NAME)])

        result = import_edition_certificates(self.cohort, self._edition(csv_path=csv_path))

        self.assertEqual(result.graduates_seen, 2)
        self.assertEqual(result.certificate_urls_matched, 2)
        self.assertEqual(result.graduates_blocked_ambiguous_name, 0)

    def test_replay_is_stable(self) -> None:
        csv_path = self._write_roster([(GRADUATE_ONE, UNIQUE_NAME)])
        edition = self._edition(csv_path=csv_path)

        import_edition_certificates(self.cohort, edition)
        replay = import_edition_certificates(self.cohort, edition)

        self.assertEqual(replay.graduates_seen, 1)
        self.assertEqual(replay.certificate_urls_matched, 1)
        self.assertEqual(Enrollment.objects.count(), 1)

    def enrollment_urls(self) -> list[str | None]:
        return list(
            Enrollment.objects.order_by("student__username").values_list(
                "certificate_url", flat=True
            )
        )
