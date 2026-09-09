"""Body shapes the legacy API parses strictly (audit BE-14).

Every legacy mutation reads the body field-by-field, so an array, scalar, or
null body used to escape the view as an ``AttributeError``.  Single-object
routes now demand one JSON object, bulk routes demand a bounded array of
objects validated before any mutation, and the bare ``NaN``/``Infinity``
constants Python's JSON parser would otherwise accept are rejected with the
same generic ``Invalid JSON`` response.  Rejected values are never reflected.
"""

from __future__ import annotations

import json

from api.tests.project_api_base import PROJECT_INSTRUCTIONS_URL, ProjectAPITestBase
from courses.models import Project

OBJECT_SHAPE_ERROR = "invalid_json_object"
BULK_SHAPE_ERROR = "invalid_json_object_list"


class ProjectCreateBodyShapeTests(ProjectAPITestBase):
    url = "/api/courses/test-course/projects/"

    def post_body(self, raw: bytes | str):
        return self.client.post(
            self.url,
            raw,
            content_type="application/json",
        )

    def test_an_empty_array_keeps_the_legacy_empty_bulk_contract(self) -> None:
        response = self.post_body(json.dumps([]))

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json(), {"created": []})
        self.assertEqual(Project.objects.count(), 0)

    def test_a_mixed_object_scalar_array_is_a_controlled_bulk_error(self) -> None:
        response = self.post_body(json.dumps([{"name": "Project"}, 5]))

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["code"], BULK_SHAPE_ERROR)
        self.assertEqual(Project.objects.count(), 0)

    def test_a_scalar_body_is_rejected_without_reflection(self) -> None:
        for raw in ("7", '"text"', "true", "null"):
            with self.subTest(raw=raw):
                response = self.post_body(raw)

                self.assertEqual(response.status_code, 400)
                body = response.json()
                self.assertEqual(body["code"], BULK_SHAPE_ERROR)
                self.assertNotIn(raw, json.dumps(body))

    def test_a_bare_nan_constant_is_invalid_json(self) -> None:
        response = self.post_body('{"name": "Project", "score": NaN}')

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["error"], "Invalid JSON")
        self.assertEqual(Project.objects.count(), 0)

    def test_an_infinity_constant_is_invalid_json(self) -> None:
        response = self.post_body('{"name": "Project", "score": -Infinity}')

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["error"], "Invalid JSON")

    def test_a_single_object_still_creates_one_item(self) -> None:
        response = self.post_body(
            json.dumps(
                {
                    "name": "Legacy Single",
                    "submission_due_date": "2026-04-01T23:59:59Z",
                    "peer_review_due_date": "2026-04-08T23:59:59Z",
                    "instructions_url": PROJECT_INSTRUCTIONS_URL,
                }
            )
        )

        self.assertEqual(response.status_code, 201)
        self.assertEqual(len(response.json()["created"]), 1)
        self.assertEqual(Project.objects.count(), 1)

    def test_a_malformed_later_item_persists_nothing(self) -> None:
        payload = json.dumps(
            [
                {"name": "First Valid", "instructions_url": PROJECT_INSTRUCTIONS_URL},
                "not an object",
            ]
        )

        response = self.post_body(payload)

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["code"], BULK_SHAPE_ERROR)
        # The shape gate runs before any create call: no partial write.
        self.assertEqual(Project.objects.count(), 0)

    def test_an_oversized_bulk_array_is_refused_before_mutation(self) -> None:
        from api.utils import MAX_BULK_ITEMS

        payload = json.dumps([{"name": f"Project {index}"} for index in range(MAX_BULK_ITEMS + 1)])

        response = self.post_body(payload)

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["code"], "too_many_items")
        self.assertEqual(Project.objects.count(), 0)


class ProjectDetailBodyShapeTests(ProjectAPITestBase):
    def setUp(self) -> None:
        super().setUp()
        self.project = self._create_project()

    def patch_body(self, raw: bytes | str):
        return self.client.patch(
            f"/api/courses/test-course/projects/{self.project.id}/",
            raw,
            content_type="application/json",
        )

    def test_an_array_patch_is_a_controlled_error(self) -> None:
        response = self.patch_body(json.dumps([{"title": "Nope"}]))

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["code"], OBJECT_SHAPE_ERROR)
        self.project.refresh_from_db()
        self.assertEqual(self.project.title, "Project 1")

    def test_a_null_patch_is_a_controlled_error(self) -> None:
        response = self.patch_body("null")

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["code"], OBJECT_SHAPE_ERROR)

    def test_a_string_patch_is_a_controlled_error_without_reflection(self) -> None:
        response = self.patch_body('"just a string"')

        self.assertEqual(response.status_code, 400)
        body = response.json()
        self.assertEqual(body["code"], OBJECT_SHAPE_ERROR)
        self.assertNotIn("just a string", json.dumps(body))

    def test_an_object_patch_still_applies(self) -> None:
        response = self.patch_body(json.dumps({"title": "Renamed"}))

        self.assertEqual(response.status_code, 200)
        self.project.refresh_from_db()
        self.assertEqual(self.project.title, "Renamed")


class CoursePatchBodyShapeTests(ProjectAPITestBase):
    def test_a_scalar_course_patch_is_a_controlled_error(self) -> None:
        response = self.client.patch(
            "/api/courses/test-course/",
            "42",
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["code"], OBJECT_SHAPE_ERROR)
