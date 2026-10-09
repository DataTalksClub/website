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
from datetime import datetime

from api.tests.project_api_base import PROJECT_INSTRUCTIONS_URL, ProjectAPITestBase
from courses.models import Cohort, Project

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


class ProjectUpsertBodyContract(ProjectAPITestBase):
    def payload(self) -> dict:
        return {
            "name": "Fallback title",
            "submission_due_date": "2026-04-01T18:30:00+02:00",
            "peer_review_due_date": "2026-04-08T12:15:00-03:00",
        }

    def put_project(self, data: dict, course_slug: str = "test-course"):
        return self.client.put(
            f"/api/courses/{course_slug}/projects/by-slug/contract/",
            json.dumps(data),
            content_type="application/json",
        )

    def assert_mapping(self, project: Project, data: dict, body: dict) -> None:
        expected = {
            "slug": "contract",
            "title": data.get("title", data["name"]),
            "description": data.get("description", ""),
            "instructions_url": data.get("instructions_url"),
            "state": data.get("state", "CL"),
        }
        self.assertEqual(project.course_id, self.course.pk)
        self.assertEqual(body["id"], project.pk)
        for field, value in expected.items():
            self.assertEqual(getattr(project, field), value, field)
            self.assertEqual(body[field], value, field)
        for field in ("submission_due_date", "peer_review_due_date"):
            expected_date = datetime.fromisoformat(data[field])
            self.assertEqual(getattr(project, field), expected_date, field)
            self.assertEqual(datetime.fromisoformat(body[field]), expected_date, field)

    def test_create_and_update_preserve_full_mapping_and_identity(self) -> None:
        data = self.payload()
        data.update(
            title="Explicit title",
            description="Literal description",
            instructions_url=PROJECT_INSTRUCTIONS_URL,
            state="CS",
        )
        response = self.put_project(data)
        self.assertEqual(response.status_code, 201)
        project = Project.objects.get(slug="contract", course=self.course)
        self.assert_mapping(project, data, response.json())
        original_pk = project.pk
        update = self.put_project({"title": "Updated title"})
        self.assertEqual(update.status_code, 200)
        project.refresh_from_db()
        data["title"] = "Updated title"
        self.assert_mapping(project, data, update.json())
        self.assertEqual(project.pk, original_pk)
        self.assertEqual(Project.objects.count(), 1)

    def test_defaults_and_explicit_instructions_keep_distinct_values(self) -> None:
        for instructions in ({}, {"instructions_url": None}, {"instructions_url": ""}):
            with self.subTest(instructions=instructions):
                Project.objects.all().delete()
                data = self.payload()
                data.update(instructions)
                response = self.put_project(data)
                self.assertEqual(response.status_code, 201)
                project = Project.objects.get(slug="contract")
                self.assert_mapping(project, data, response.json())

    def test_same_slug_in_another_cohort_keeps_distinct_identity(self) -> None:
        first = self.put_project(self.payload())
        cohort = Cohort.objects.create(title="Other", slug="other", description="")
        second = self.put_project(self.payload(), cohort.slug)
        self.assertEqual(first.status_code, 201)
        self.assertEqual(second.status_code, 201)
        self.assertNotEqual(first.json()["id"], second.json()["id"])
        project = Project.objects.get(pk=second.json()["id"])
        self.assertEqual(project.course_id, cohort.pk)
        self.assertEqual(Project.objects.count(), 2)

    def test_invalid_creates_preserve_error_priority_and_zero_rows(self) -> None:
        cases = (
            ({"title": None, "state": "XX"}, "missing_required_fields"),
            ({"title": ""}, "missing_required_fields"),
            (
                {"instructions_url": "javascript:bad", "submission_due_date": "bad", "state": "XX"},
                "invalid_instructions_url",
            ),
            ({"submission_due_date": "bad", "state": "XX"}, "invalid_date_format"),
            ({"peer_review_due_date": "bad"}, "invalid_date_format"),
            ({"state": "XX"}, "invalid_project_state"),
        )
        for invalid, code in cases:
            with self.subTest(code=code, invalid=invalid):
                data = self.payload()
                data.update(invalid)
                response = self.put_project(data)
                self.assertEqual(response.status_code, 400)
                self.assertEqual(response.json()["code"], code)
                self.assertEqual(Project.objects.count(), 0)

    def test_invalid_update_and_nonstaff_write_preserve_existing_row(self) -> None:
        created = self.put_project(self.payload())
        response = self.put_project({"title": "Must not persist", "state": "XX"})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["code"], "invalid_project_state")
        project = Project.objects.get(pk=created.json()["id"])
        self.assertEqual(project.title, "Fallback title")
        self.client = self._non_staff_client("contract-denied")
        denial = self.put_project({"title": "Unauthorized"})
        self._assert_staff_token_required(denial)
        project.refresh_from_db()
        self.assertEqual(project.title, "Fallback title")
        self.assertEqual(Project.objects.count(), 1)
