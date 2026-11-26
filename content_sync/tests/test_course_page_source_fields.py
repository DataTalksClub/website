"""Course-page source fields carried by a schema-2 course repository.

The module README parsed at import into structured fields, lesson summaries
and video lengths, the module code link, course instructors and weekly time,
and placement-only project entries for CMP-owned attempts.  Built on the
versioned ``llm_zoomcamp_shared`` fixture, edited in memory.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from community_base.coursework.models import ReviewCriteriaSet
from django.test import SimpleTestCase, TestCase

from content_sync.course_repository import CourseRepositoryValidationError, parse_course_repository
from content_sync.module_overview import parse_module_overview
from courses.models import Cohort, Course, Project, SharedLesson, SharedModule
from courses.services.curriculum_import import import_course_repository_curriculum
from courses.tests.test_shared_curriculum_import import fixture_snapshot, make_command

README = "01-agentic-rag/README.md"
LESSON = "01-agentic-rag/01-lesson.md"
COHORT = "cohorts/2026/cohort.yaml"

LLM_STYLE_README = """# Module 1: Agentic RAG

In this module, we build a RAG pipeline. See [module 2](../02-vector-search/).

Code: [code/](code/)

## Part 1: RAG

The first lessons build the pipeline.

1. [Introduction](01-lesson.md) - What we'll build and why
2. [Practice](02-practice.md) (Optional) - Practising the loop

## Optional

- [Build a Search Engine](https://www.youtube.com/watch?v=nMrGK5QgPVE)

## Original workshop recordings

- Part 1 – RAG: [Build Your First RAG Application](https://www.youtube.com/watch?v=KSItlTAsMsk)

## Notes

- [Notes from Ada](https://example.com/ada)
- Add your notes above this line
"""


def lesson_slugs() -> dict[str, str]:
    return {"01-lesson.md": "01-lesson", "02-practice.md": "02-practice"}


class ModuleOverviewParserTests(SimpleTestCase):
    def test_list_style_readme(self) -> None:
        overview = parse_module_overview(LLM_STYLE_README, lesson_slugs=lesson_slugs())

        self.assertEqual(
            overview.intro_markdown,
            "In this module, we build a RAG pipeline. See [module 2](../02-vector-search/).",
        )
        self.assertEqual(overview.code_target, "code/")
        self.assertEqual(len(overview.parts), 1)
        part = overview.parts[0]
        self.assertEqual(part.heading, "Part 1: RAG")
        self.assertEqual(part.intro_markdown, "The first lessons build the pipeline.")
        self.assertEqual(
            [(lesson.slug, lesson.title, lesson.description) for lesson in part.lessons],
            [
                ("01-lesson", "Introduction", "What we'll build and why"),
                ("02-practice", "Practice", "Practising the loop"),
            ],
        )
        self.assertEqual(
            [(r.label, r.title, r.url) for r in overview.recordings],
            [
                (
                    "Part 1 – RAG",
                    "Build Your First RAG Application",
                    "https://www.youtube.com/watch?v=KSItlTAsMsk",
                )
            ],
        )
        self.assertEqual([n.url for n in overview.notes], ["https://example.com/ada"])
        self.assertEqual([s.heading for s in overview.sections], ["Optional"])
        self.assertEqual(
            overview.lesson_descriptions(),
            {"01-lesson": "What we'll build and why", "02-practice": "Practising the loop"},
        )

    def test_heading_style_readme_and_ordinals(self) -> None:
        markdown = (
            "﻿# Module 1: Introduction\n\nIntro text.\n\n"
            "## 1.1 [Introduction](01-lesson.md)\n\nMLOps connects design and operation.\n\n"
            "## 1.2 [Practice](02-practice.md)\n\nSet up a workspace.\n\n"
            "## Community Notes\n\n* [Notes](https://example.com/n)\n"
        )
        overview = parse_module_overview(markdown, lesson_slugs=lesson_slugs())

        self.assertEqual(overview.intro_markdown, "Intro text.")
        self.assertEqual(
            overview.lesson_descriptions(),
            {
                "01-lesson": "MLOps connects design and operation.",
                "02-practice": "Set up a workspace.",
            },
        )
        self.assertEqual(len(overview.notes), 1)

    def test_bulleted_ordinals_and_status_notes(self) -> None:
        markdown = (
            "## 9. Serverless\n\n"
            "- 9.1 [Introduction](01-lesson.md)\n"
            "- (Outdated) 9.2 [Practice](02-practice.md)\n"
        )
        overview = parse_module_overview(markdown, lesson_slugs=lesson_slugs())

        self.assertEqual(
            [lesson.slug for lesson in overview.parts[0].lessons], ["01-lesson", "02-practice"]
        )
        self.assertEqual(overview.lesson_descriptions(), {})
        self.assertIsNone(overview.code_target)

    def test_links_to_other_files_are_not_lessons(self) -> None:
        markdown = "# M\n\n## Setup\n\n* [Guide](setup/guide.md) - how to install\n"
        overview = parse_module_overview(markdown, lesson_slugs=lesson_slugs())

        self.assertEqual(overview.parts, ())
        self.assertEqual([s.heading for s in overview.sections], ["Setup"])


def with_readme(snapshot: dict[str, bytes], markdown: str = LLM_STYLE_README) -> dict[str, bytes]:
    snapshot[README] = markdown.encode()
    return snapshot


def with_duration(snapshot: dict[str, bytes], value: str = "754") -> dict[str, bytes]:
    snapshot[LESSON] = snapshot[LESSON].replace(
        b"video_url: https://www.youtube.com/watch?v=fixture-intro\n",
        b"video_url: https://www.youtube.com/watch?v=fixture-intro\n"
        + f"video_duration_seconds: {value}\n".encode(),
    )
    return snapshot


def parse_code(snapshot: dict[str, bytes]) -> str:
    with pytest.raises(CourseRepositoryValidationError) as raised:
        parse_course_repository(snapshot)
    return raised.value.diagnostics[0].code


class ModuleOverviewImportTests(TestCase):
    def test_readme_fields_lesson_summaries_and_durations_are_imported(self) -> None:
        import_course_repository_curriculum(
            make_command(with_duration(with_readme(fixture_snapshot())))
        )

        module = SharedModule.objects.get()
        tree = "https://github.com/DataTalksClub/llm-zoomcamp/tree/main"
        self.assertEqual(module.code_url, f"{tree}/01-agentic-rag/code")
        # A link outside the module that does not exist in the snapshot stays as written.
        self.assertIn("(../02-vector-search/)", module.overview_intro_markdown)
        self.assertEqual(module.overview_parts[0]["heading"], "Part 1: RAG")
        self.assertEqual(
            module.overview_parts[0]["lessons"][0],
            {
                "slug": "01-lesson",
                "title": "Introduction",
                "description": "What we'll build and why",
            },
        )
        self.assertEqual(
            module.workshop_recordings,
            [
                {
                    "label": "Part 1 – RAG",
                    "title": "Build Your First RAG Application",
                    "url": "https://www.youtube.com/watch?v=KSItlTAsMsk",
                }
            ],
        )
        self.assertEqual(module.community_notes[0]["url"], "https://example.com/ada")
        lessons = {lesson.slug: lesson for lesson in SharedLesson.objects.all()}
        self.assertEqual(lessons["01-lesson"].summary, "What we'll build and why")
        self.assertEqual(lessons["01-lesson"].video_duration_seconds, 754)
        self.assertEqual(lessons["02-practice"].summary, "Practising the loop")
        self.assertIsNone(lessons["02-practice"].video_duration_seconds)

    def test_code_directory_is_the_code_link_without_a_code_line(self) -> None:
        import_course_repository_curriculum(make_command(fixture_snapshot()))

        module = SharedModule.objects.get()
        self.assertEqual(
            module.code_url,
            "https://github.com/DataTalksClub/llm-zoomcamp/tree/main/01-agentic-rag/code",
        )
        self.assertEqual(module.overview_parts, [])
        self.assertEqual(
            module.overview_intro_markdown,
            "Module index for GitHub readers and the website module overview.",
        )

    def test_relative_repository_links_become_github_urls(self) -> None:
        markdown = (
            "# M\n\nRead the [notebook](code/notebook.ipynb) and "
            "![diagram](images/architecture.svg).\n"
        )
        import_course_repository_curriculum(make_command(with_readme(fixture_snapshot(), markdown)))

        intro = SharedModule.objects.get().overview_intro_markdown
        self.assertIn(
            "(https://github.com/DataTalksClub/llm-zoomcamp/blob/main/"
            "01-agentic-rag/code/notebook.ipynb)",
            intro,
        )
        self.assertIn(
            "(https://raw.githubusercontent.com/DataTalksClub/llm-zoomcamp/main/"
            "01-agentic-rag/images/architecture.svg)",
            intro,
        )

    def test_invalid_durations_are_rejected(self) -> None:
        self.assertEqual(
            parse_code(with_duration(fixture_snapshot(), "0")), "lesson_video_duration_invalid"
        )
        self.assertEqual(
            parse_code(with_duration(fixture_snapshot(), '"12:34"')),
            "lesson_video_duration_invalid",
        )
        snapshot = fixture_snapshot()
        snapshot["01-agentic-rag/02-practice.md"] = (
            b"---\nvideo_duration_seconds: 60\n---\n" + snapshot["01-agentic-rag/02-practice.md"]
        )
        self.assertEqual(parse_code(snapshot), "lesson_video_duration_without_video")


COURSE_EXTRA = b"""instructors:
  - name: Alexey Grigorev
    url: https://www.linkedin.com/in/agrigorev
  - name: Bhavani Ravi
weekly_commitment: Free. About 10 hours a week.
"""


class CoursePeopleAndTimeTests(TestCase):
    def test_instructors_and_weekly_commitment_are_imported(self) -> None:
        snapshot = fixture_snapshot()
        snapshot["course.yaml"] += COURSE_EXTRA
        import_course_repository_curriculum(make_command(snapshot))

        course = Course.objects.get(slug="llm-zoomcamp")
        self.assertEqual(
            course.instructors,
            [
                {"name": "Alexey Grigorev", "url": "https://www.linkedin.com/in/agrigorev"},
                {"name": "Bhavani Ravi", "url": ""},
            ],
        )
        self.assertEqual(course.weekly_commitment, "Free. About 10 hours a week.")

    def test_absent_keys_keep_stored_values(self) -> None:
        import_course_repository_curriculum(make_command(fixture_snapshot()))
        course = Course.objects.get(slug="llm-zoomcamp")
        course.weekly_commitment = "Curated."
        course.instructors = [{"name": "Curated", "url": ""}]
        course.save()

        import_course_repository_curriculum(make_command(fixture_snapshot()))
        course.refresh_from_db()
        self.assertEqual(course.weekly_commitment, "Curated.")
        self.assertEqual(course.instructors, [{"name": "Curated", "url": ""}])

    def test_invalid_instructors_are_rejected(self) -> None:
        snapshot = fixture_snapshot()
        snapshot["course.yaml"] += b"instructors:\n  - name: A\n  - name: a\n"
        self.assertEqual(parse_code(snapshot), "duplicate_instructor")
        snapshot = fixture_snapshot()
        snapshot["course.yaml"] += b"instructors:\n  - name: A\n    url: http://x.example\n"
        self.assertEqual(parse_code(snapshot), "invalid_https_url")


PLACEMENT = b"""projects:
  - slug: project1
    module: 01-agentic-rag
  - slug: project3
    module: 01-agentic-rag
"""


class ProjectPlacementTests(TestCase):
    def test_placement_only_entry_links_an_existing_cmp_project(self) -> None:
        import_course_repository_curriculum(make_command(fixture_snapshot()))
        cohort = Cohort.objects.get(identifier="2026")
        criteria = ReviewCriteriaSet.objects.create(
            slug="cmp-criteria", title="CMP", criteria=[{"id": "x"}]
        )
        project = Project.objects.create(
            course=cohort,
            slug="project1",
            title="Project Attempt 1",
            submission_due_date=datetime(2026, 8, 3, 23, tzinfo=UTC),
            peer_review_due_date=datetime(2026, 8, 11, 21, tzinfo=UTC),
            criteria_set=criteria,
        )

        snapshot = fixture_snapshot()
        snapshot[COHORT] += PLACEMENT
        import_course_repository_curriculum(make_command(snapshot))

        project.refresh_from_db()
        self.assertEqual(project.shared_module, SharedModule.objects.get())
        # CMP-owned fields are untouched.
        self.assertEqual(project.title, "Project Attempt 1")
        self.assertEqual(project.criteria_set, criteria)
        self.assertEqual(project.submission_due_date, datetime(2026, 8, 3, 23, tzinfo=UTC))
        # project3 does not exist yet: nothing is created.
        self.assertFalse(Project.objects.filter(slug="project3").exists())

    def test_placement_entries_need_a_known_module(self) -> None:
        snapshot = fixture_snapshot()
        snapshot[COHORT] += b"projects:\n  - slug: project1\n"
        self.assertEqual(parse_code(snapshot), "required_key_missing")
        snapshot = fixture_snapshot()
        snapshot[COHORT] += b"projects:\n  - slug: project1\n    module: 09-nope\n"
        self.assertEqual(parse_code(snapshot), "curriculum_source_mismatch")
