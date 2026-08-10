from __future__ import annotations

import copy
import json
from datetime import timedelta
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urlsplit

from django.conf import settings
from django.contrib.staticfiles import finders
from django.test import SimpleTestCase, TestCase
from django.urls import reverse
from django.utils import timezone

from courses.models import Course, Project
from scripts.verify_course_platform_vendor_assets import (
    MANIFEST_PATH,
    VendorAssetProvenanceError,
    verify,
)
from test_support.runtime import current_worker_id

ROOT = Path(__file__).resolve().parents[2]


class ExternalAssetParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.external_requests: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = dict(attrs)
        candidate = None
        if tag == "script":
            candidate = attributes.get("src")
        elif tag == "img":
            candidate = attributes.get("src")
        elif tag == "link" and "stylesheet" in (attributes.get("rel") or "").split():
            candidate = attributes.get("href")
        if candidate and (urlsplit(candidate).scheme or candidate.startswith("//")):
            self.external_requests.append(candidate)


class CoursePlatformVendorProvenanceTests(SimpleTestCase):
    def test_manifest_assets_fixtures_and_css_references_are_exact(self) -> None:
        self.assertEqual(verify(), (19, 18, 15))

    def test_manifest_rejects_schema_drift_duplicate_targets_and_digest_drift(self) -> None:
        payload = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
        worker = settings.TEST_RUNTIME.worker(current_worker_id())
        scratch = worker.artifacts / "vendor-provenance-negative.json"
        cases = []

        missing_field = copy.deepcopy(payload)
        del missing_field["assets"][0]["license"]
        cases.append(missing_field)

        duplicate_target = copy.deepcopy(payload)
        duplicate_target["assets"][1]["target_path"] = duplicate_target["assets"][0]["target_path"]
        cases.append(duplicate_target)

        digest_drift = copy.deepcopy(payload)
        digest_drift["assets"][0]["target_sha256"] = "0" * 64
        cases.append(digest_drift)

        for case in cases:
            scratch.write_text(json.dumps(case), encoding="utf-8")
            with self.assertRaises(VendorAssetProvenanceError):
                verify(manifest_path=scratch)

    def test_static_finders_resolve_every_recorded_asset(self) -> None:
        payload = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
        for asset in payload["assets"]:
            target = asset["target_path"]
            static_name = target.removeprefix("core/static/")
            with self.subTest(static_name=static_name):
                found = finders.find(static_name)
                if not isinstance(found, str):
                    self.fail(f"static finder did not resolve {static_name}")
                self.assertEqual(Path(found).resolve(), (ROOT / target).resolve())


class CoursePlatformRenderedVendorAssetTests(TestCase):
    def setUp(self) -> None:
        self.course = Course.objects.create(
            slug="vendor-assets-course",
            title="Vendor assets course",
            description="Synthetic course for local vendor rendering.",
        )
        self.project = Project.objects.create(
            course=self.course,
            slug="vendor-assets-project",
            title="Vendor assets project",
            submission_due_date=timezone.now() + timedelta(days=1),
            peer_review_due_date=timezone.now() + timedelta(days=2),
        )

    def test_rendered_copied_pages_use_only_local_request_assets(self) -> None:
        responses = (
            self.client.get(
                reverse(
                    "project",
                    kwargs={
                        "course_slug": self.course.slug,
                        "project_slug": self.project.slug,
                    },
                )
            ),
            self.client.get("/accounts/login/"),
        )
        for response in responses:
            with self.subTest(path=response.request["PATH_INFO"]):
                self.assertEqual(response.status_code, 200)
                rendered = response.content.decode()
                parser = ExternalAssetParser()
                parser.feed(rendered)
                self.assertEqual(parser.external_requests, [])
                self.assertIn(
                    "/static/core/vendor/fontawesome-free-5.15.1/css/all.min.css",
                    rendered,
                )
                self.assertIn(
                    "/static/core/vendor/tailwindcss-3.4.17/tailwindcss-3.4.17.js",
                    rendered,
                )
                self.assertLess(
                    rendered.index("tailwind.config"),
                    rendered.index("/static/core/vendor/tailwindcss-3.4.17/"),
                )

        project_html = responses[0].content.decode()
        self.assertIn(
            "Open the commit on GitHub and copy the first 7 characters shown next to its title.",
            project_html,
        )
        self.assertNotIn("habrastorage.org", project_html)
