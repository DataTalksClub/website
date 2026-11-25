"""Projects and review criteria sets declared in a schema-2 cohort.yaml.

Uses the versioned ``llm_zoomcamp_shared`` fixture and adds the two cohort keys
in memory, so the fixture's known-output contract stays untouched.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from community_base.coursework.models import ReviewCriteriaSet
from django.test import TestCase

from content_sync.course_repository import CourseRepositoryValidationError, parse_course_repository
from courses.models import Project, ProjectState, SharedModule
from courses.services.curriculum_import import import_course_repository_curriculum
from courses.tests.test_shared_curriculum_import import fixture_snapshot, make_command

COHORT = "cohorts/2026/cohort.yaml"
CRITERIA = "cohorts/2026/review_criteria/llm-zoomcamp-project.yaml"
DESCRIPTION = "cohorts/2026/projects/attempt-1.md"

CRITERIA_YAML = b"""slug: llm-zoomcamp-project
title: LLM Zoomcamp project
criteria:
  - id: problem
    title: Problem description
    description: Is the problem described?
    options:
      - {points: 0, text: Not described}
      - {points: 2, text: Clearly described}
"""

PROJECTS_YAML = """review_criteria:
  - cohorts/2026/review_criteria/llm-zoomcamp-project.yaml
projects:
  - slug: project-attempt-1
    title: Project Attempt 1
    module: 01-agentic-rag
    description: cohorts/2026/projects/attempt-1.md
    submission_due: "2026-08-03T23:59:59Z"
    review_due: "2026-08-10T23:59:59Z"
    peers: 3
    criteria_set: llm-zoomcamp-project
"""


def project_snapshot(extra: str = PROJECTS_YAML) -> dict[str, bytes]:
    snapshot = fixture_snapshot()
    snapshot[COHORT] = snapshot[COHORT] + extra.encode()
    snapshot[CRITERIA] = CRITERIA_YAML
    snapshot[DESCRIPTION] = b"# Attempt 1\n\nBuild an end-to-end RAG app.\n"
    return snapshot


def parse_code(snapshot: dict[str, bytes]) -> str:
    with pytest.raises(CourseRepositoryValidationError) as raised:
        parse_course_repository(snapshot)
    return raised.value.diagnostics[0].code


class CohortProjectsParserTests(TestCase):
    def test_parses_criteria_sets_and_projects(self) -> None:
        source = parse_course_repository(project_snapshot())
        cohort = next(c for c in source.cohorts if c.identifier == "2026")
        self.assertEqual([c.slug for c in cohort.criteria_sets], ["llm-zoomcamp-project"])
        self.assertEqual(cohort.criteria_sets[0].criteria[0]["options"][1]["points"], 2)
        project = cohort.projects[0]
        self.assertEqual(project.slug, "project-attempt-1")
        self.assertEqual(project.module, "01-agentic-rag")
        self.assertEqual(project.peers, 3)
        self.assertEqual(project.submission_due, datetime(2026, 8, 3, 23, 59, 59, tzinfo=UTC))
        self.assertIn("end-to-end RAG app", project.description_markdown)

    def test_rejects_unknown_criteria_set_bad_dates_and_outside_paths(self) -> None:
        self.assertEqual(
            parse_code(
                project_snapshot(PROJECTS_YAML.replace("criteria_set: llm", "criteria_set: x"))
            ),
            "project_criteria_set_missing",
        )
        self.assertEqual(
            parse_code(project_snapshot(PROJECTS_YAML.replace("2026-08-10", "2026-08-01"))),
            "project_review_before_submission",
        )
        self.assertEqual(
            parse_code(
                project_snapshot(
                    PROJECTS_YAML.replace("description: cohorts/2026/", "description: ../")
                )
            ),
            "cohort_file_outside_cohort",
        )
        self.assertEqual(
            parse_code(
                project_snapshot(PROJECTS_YAML.replace("module: 01-agentic-rag", "module: 09-x"))
            ),
            "curriculum_source_mismatch",
        )

    def test_rejects_invalid_criteria(self) -> None:
        snapshot = project_snapshot()
        snapshot[CRITERIA] = b"slug: llm-zoomcamp-project\ntitle: X\ncriteria: []\n"
        self.assertEqual(parse_code(snapshot), "review_criteria_invalid")


class CohortProjectsImportTests(TestCase):
    def test_import_upserts_criteria_set_and_project_and_keeps_state_on_reimport(self) -> None:
        import_course_repository_curriculum(make_command(project_snapshot()))

        criteria_set = ReviewCriteriaSet.objects.get(slug="llm-zoomcamp-project")
        project = Project.objects.get(course__identifier="2026", slug="project-attempt-1")
        self.assertEqual(project.criteria_set, criteria_set)
        self.assertEqual(project.number_of_peers_to_evaluate, 3)
        self.assertEqual(project.shared_module, SharedModule.objects.get())
        self.assertEqual(project.state, ProjectState.COLLECTING_SUBMISSIONS.value)

        project.state = ProjectState.PEER_REVIEWING.value
        project.save(update_fields=["state"])
        snapshot = project_snapshot(PROJECTS_YAML.replace("peers: 3", "peers: 2"))
        snapshot[CRITERIA] = CRITERIA_YAML.replace(b"Clearly described", b"Well described")
        import_course_repository_curriculum(make_command(snapshot, commit_sha="e" * 40))

        project.refresh_from_db()
        self.assertEqual(project.state, ProjectState.PEER_REVIEWING.value)
        self.assertEqual(project.number_of_peers_to_evaluate, 2)
        criteria_set.refresh_from_db()
        self.assertEqual(criteria_set.criteria[0]["options"][1]["text"], "Well described")

    def test_project_removed_from_source_is_kept(self) -> None:
        import_course_repository_curriculum(make_command(project_snapshot()))
        snapshot = project_snapshot("review_criteria:\n  - " + CRITERIA + "\nprojects: []\n")
        import_course_repository_curriculum(make_command(snapshot, commit_sha="f" * 40))
        self.assertTrue(Project.objects.filter(slug="project-attempt-1").exists())
