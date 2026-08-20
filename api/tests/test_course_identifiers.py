import json

from django.test import SimpleTestCase

from api.openapi.spec import build_openapi_spec
from api.tests.course_api_base import CourseAPITestBase
from courses.models import Cohort


class CourseIdentifierAPITestCase(CourseAPITestBase):
    def test_create_course_accepts_non_year_identifier(self):
        payload = self.new_course_payload()
        payload["identifier"] = "spring-2026"

        response = self.client.post(
            "/api/courses/",
            json.dumps(payload),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.json()["identifier"], "spring-2026")
        created = Cohort.objects.get(slug="new-course")
        self.assertEqual(created.identifier, "spring-2026")

    def test_patch_course_accepts_non_year_identifier(self):
        response = self.client.patch(
            "/api/courses/ml-zoomcamp/",
            json.dumps({"identifier": "spring-2026"}),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["identifier"], "spring-2026")
        self.course.refresh_from_db()
        self.assertEqual(self.course.identifier, "spring-2026")

    def test_course_responses_expose_identifier(self):
        response = self.client.get("/api/courses/ml-zoomcamp/")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["identifier"], "2026")


class CourseIdentifierOpenAPITestCase(SimpleTestCase):
    def test_course_schemas_document_identifier(self):
        schemas = build_openapi_spec()["components"]["schemas"]

        for schema_name in ("CourseSummary", "CourseCreate", "CourseDetail"):
            self.assertIn("identifier", schemas[schema_name]["properties"])
            self.assertEqual(
                schemas[schema_name]["properties"]["identifier"]["type"],
                "string",
            )

        patch_properties = schemas["CoursePatch"]["properties"]
        self.assertIn("identifier", patch_properties)
        self.assertNotIn("identifier", schemas["CourseCreate"].get("required", []))
