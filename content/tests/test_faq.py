from __future__ import annotations

import re
from html import escape

from django.test import TestCase

from content.faq_data import (
    FAQ_COURSE_ORDER,
    FAQ_PROJECTION_PATH,
    faq_courses,
    faq_questions,
    render_faq_answer,
)


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

    def test_same_course_source_references_render_as_canonical_faq_fragments(self) -> None:
        course = next(course for course in faq_courses() if course["slug"] == "llm-zoomcamp")
        questions = {question["id"]: question for question in faq_questions(course)}
        cases = (
            (
                "830f3d2018",
                "aa310de435",
                "026_aa310de435_can-i-run-the-course-locally-instead-of-codespaces.md",
                "Can I run the course locally instead of Codespaces?",
            ),
            (
                "8e21752415",
                "152af39a53",
                "openai-error-ratelimiterror-error-code-429",
                "OpenAI: Error: RateLimitError: Error code: 429",
            ),
            (
                "d8b28c47a0",
                "649c280e6d",
                "../module-1-rag/023_649c280e6d_how-should-i-prepare-documents-for-rag.md",
                "How should I prepare documents for RAG?",
            ),
        )
        for source_id, target_id, reference, label in cases:
            with self.subTest(source_id=source_id):
                self.assertIn(f"]({reference})", questions[source_id]["answer"])
                rendered = render_faq_answer(questions[source_id])
                self.assertIn(
                    f'<a href="/faq/llm-zoomcamp.html#{target_id}">{label}</a>',
                    rendered,
                )

    def test_faq_reference_rewrite_is_bounded_and_skips_non_question_links(self) -> None:
        course = next(course for course in faq_courses() if course["slug"] == "llm-zoomcamp")
        question = next(
            question for question in faq_questions(course) if question["id"] == "830f3d2018"
        )
        other_course = next(
            course for course in faq_courses() if course["slug"] == "machine-learning-zoomcamp"
        )
        other_question = faq_questions(other_course)[0]
        answer = " ".join(
            (
                "[filename](026_aa310de435_can-i-run-the-course-locally-instead-of-codespaces.md)",
                "[nested](../module-1-rag/023_649c280e6d_how-should-i-prepare-documents-for-rag.md)",
                "[slug](openai-error-ratelimiterror-error-code-429)",
                "[unknown](999_unknown.md)",
                "[cross-course](../machine-learning/"
                + other_question["source_path"].rsplit("/", 1)[-1]
                + ")",
                "[query](026_aa310de435_can-i-run-the-course-locally-instead-of-codespaces.md?x=1)",
                "[fragment](026_aa310de435_can-i-run-the-course-locally-instead-of-codespaces.md#other)",
                "[root](/026_aa310de435_can-i-run-the-course-locally-instead-of-codespaces.md)",
                "[external](https://github.com/DataTalksClub/faq)",
                "`[code](026_aa310de435_can-i-run-the-course-locally-instead-of-codespaces.md)`",
            )
        )
        rendered = render_faq_answer({**question, "answer": answer})
        self.assertIn('href="/faq/llm-zoomcamp.html#aa310de435">filename</a>', rendered)
        self.assertIn('href="/faq/llm-zoomcamp.html#649c280e6d">nested</a>', rendered)
        self.assertIn('href="/faq/llm-zoomcamp.html#152af39a53">slug</a>', rendered)
        for reference in (
            "999_unknown.md",
            other_question["source_path"].rsplit("/", 1)[-1],
            "026_aa310de435_can-i-run-the-course-locally-instead-of-codespaces.md?x=1",
            "026_aa310de435_can-i-run-the-course-locally-instead-of-codespaces.md?",
            "026_aa310de435_can-i-run-the-course-locally-instead-of-codespaces.md#other",
            "026_aa310de435_can-i-run-the-course-locally-instead-of-codespaces.md#",
            "/026_aa310de435_can-i-run-the-course-locally-instead-of-codespaces.md",
        ):
            self.assertIn(reference, rendered)
        self.assertIn('href="https://github.com/DataTalksClub/faq">external</a>', rendered)
        self.assertIn(
            "<code>[code](026_aa310de435_can-i-run-the-course-locally-instead-of-codespaces.md)</code>",
            rendered,
        )

    def test_faq_rendering_does_not_mutate_projection_or_question_source(self) -> None:
        course = next(course for course in faq_courses() if course["slug"] == "llm-zoomcamp")
        question = next(
            question for question in faq_questions(course) if question["id"] == "830f3d2018"
        )
        projection_bytes = FAQ_PROJECTION_PATH.read_bytes()
        source_path = question["source_path"]
        source_answer = question["answer"]

        render_faq_answer(question)

        self.assertEqual(FAQ_PROJECTION_PATH.read_bytes(), projection_bytes)
        self.assertEqual(question["source_path"], source_path)
        self.assertEqual(question["answer"], source_answer)

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
