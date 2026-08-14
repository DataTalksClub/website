from __future__ import annotations

import re
from html import escape

from django.test import TestCase

from content.faq_data import FAQ_COURSE_ORDER, faq_courses, faq_questions


class FaqRoutesTests(TestCase):
    def test_projection_has_the_pinned_inventory_and_stable_question_ids(self) -> None:
        courses = faq_courses()
        self.assertEqual(tuple(course["slug"] for course in courses), FAQ_COURSE_ORDER)
        self.assertEqual(sum(course["question_count"] for course in courses), 1401)
        self.assertEqual(sum(course["section_count"] for course in courses), 70)
        questions = [question for course in courses for question in faq_questions(course)]
        self.assertEqual(len({question["id"] for question in questions}), 1401)
        self.assertTrue(
            all(re.fullmatch(r"[A-Za-z0-9]{10}", question["id"]) for question in questions)
        )

    def test_faq_hub_lists_every_course(self) -> None:
        response = self.client.get("/faq/")
        self.assertEqual(response.status_code, 200)
        self.assertNotIn("Location", response.headers)
        self.assertContains(response, '<link rel="canonical" href="https://datatalks.club/faq/">')
        self.assertContains(
            response, '<meta property="og:url" content="https://datatalks.club/faq/">'
        )
        for course in faq_courses():
            self.assertContains(response, course["name"])
            self.assertContains(response, f'href="{course["public_path"]}"')

    def test_course_pages_render_every_question_with_canonical_and_faq_json_ld(self) -> None:
        for course in faq_courses():
            with self.subTest(course=course["slug"]):
                response = self.client.get(course["public_path"])
                self.assertEqual(response.status_code, 200)
                body = response.content.decode()
                self.assertContains(
                    response,
                    f'<link rel="canonical" href="https://datatalks.club{course["public_path"]}">',
                )
                self.assertContains(response, '"@type": "FAQPage"')
                self.assertNotIn("c8da1deea9e24945922702994de101dd90a5380a", body)
                for question in faq_questions(course):
                    self.assertIn(f'id="{question["id"]}"', body)
                    self.assertIn(escape(question["question"]), body)
                self.assertEqual(self.client.head(course["public_path"]).status_code, 200)
                self.assertEqual(self.client.post(course["public_path"]).status_code, 405)

    def test_course_json_feeds_match_the_legacy_schema(self) -> None:
        courses_response = self.client.get("/faq/json/courses.json")
        self.assertEqual(courses_response.status_code, 200)
        self.assertEqual(courses_response.headers["Content-Type"], "application/json")
        courses_payload = courses_response.json()
        self.assertEqual(
            courses_payload,
            [
                {
                    "course": course["slug"],
                    "course_name": course["name"],
                    "path": f"/json/{course['slug']}.json",
                    "questions_count": course["question_count"],
                }
                for course in faq_courses()
            ],
        )
        for course in faq_courses():
            with self.subTest(course=course["slug"]):
                response = self.client.get(f"/faq/json/{course['slug']}.json")
                self.assertEqual(response.status_code, 200)
                payload = response.json()
                self.assertEqual(len(payload), course["question_count"])
                self.assertEqual(set(payload[0]), {"id", "course", "section", "question", "answer"})
                expected = [
                    {
                        "id": question["id"],
                        "course": question["course"],
                        "section": question["section"],
                        "question": question["question"],
                        "answer": question["answer"],
                    }
                    for question in faq_questions(course)
                ]
                self.assertEqual(payload, expected)

    def test_literal_template_syntax_is_not_evaluated(self) -> None:
        course = next(
            course for course in faq_courses() if course["slug"] == "data-engineering-zoomcamp"
        )
        question = next(
            question for question in faq_questions(course) if question["id"] == "58586a16a6"
        )
        self.assertIn("{% macro", question["answer"])
        response = self.client.get(course["public_path"])
        body = response.content.decode()
        self.assertIn("{% macro", body)
        self.assertIn("{%- endmacro %}", body)

    def test_referenced_image_assets_are_served_and_unknown_paths_are_not(self) -> None:
        course = next(course for course in faq_courses() if course["slug"] == "mlops-zoomcamp")
        question = next(question for question in faq_questions(course) if question["images"])
        image = question["images"][0]
        response = self.client.get(image["public_path"])
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "image/png")
        page = self.client.get(course["public_path"])
        self.assertContains(page, image["public_path"])
        self.assertEqual(
            self.client.get("/faq/images/mlops-zoomcamp/not-referenced.png").status_code,
            404,
        )
        self.assertEqual(
            self.client.get("/faq/assets/css/main.css")["Content-Type"],
            "text/css",
        )

    def test_unknown_faq_records_are_404(self) -> None:
        for path in (
            "/faq/missing-course.html",
            "/faq/json/missing-course.json",
            "/faq/images/missing-course/asset.png",
            "/faq/assets/missing-course/asset.png",
        ):
            with self.subTest(path=path):
                self.assertEqual(self.client.get(path).status_code, 404)
