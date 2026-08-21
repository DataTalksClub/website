"""The shared content-page parent owns ordinary course-platform layout."""

from __future__ import annotations

from pathlib import Path

from django.test import SimpleTestCase

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]

DIRECT_CHILDREN = (
    "courses/templates/courses/course_list.html",
    "courses/templates/courses/course_family.html",
    "courses/templates/courses/course.html",
    "courses/templates/courses/dashboard.html",
    "courses/templates/courses/enrollment.html",
    "courses/templates/courses/leaderboard.html",
    "courses/templates/courses/leaderboard_complaint.html",
    "courses/templates/courses/leaderboard_score_breakdown.html",
    "courses/templates/courses/module.html",
    "courses/templates/courses/unit.html",
    "courses/templates/courses/register.html",
    "courses/templates/courses/wrapped.html",
    "courses/templates/courses/user_wrapped.html",
    "courses/templates/homework/stats.html",
    "courses/templates/homework/submissions.html",
    "courses/templates/courses/_submission_page.html",
    "courses/templates/projects/_base.html",
)

PROJECT_CHILDREN = (
    "courses/templates/projects/eval.html",
    "courses/templates/projects/list.html",
    "courses/templates/projects/list_all.html",
    "courses/templates/projects/results.html",
    "courses/templates/projects/stats.html",
    "courses/templates/projects/submissions.html",
)

SUBMISSION_PROJECT_CHILDREN = (
    "courses/templates/projects/eval_submit.html",
    "courses/templates/projects/project.html",
)


class ContentPageShellContractTests(SimpleTestCase):
    def read(self, relative_path: str) -> str:
        return (REPOSITORY_ROOT / relative_path).read_text(encoding="utf-8")

    def test_parent_owns_the_surfaces_and_normal_width(self) -> None:
        source = self.read("templates/core/content_page.html")

        self.assertIn('class="band band-cream content-page-header', source)
        self.assertIn('class="band band-lavender content-page-content', source)
        self.assertIn('class="shell content-shell"', source)

    def test_ordinary_course_pages_extend_the_shared_parent(self) -> None:
        for relative_path in DIRECT_CHILDREN:
            with self.subTest(template=relative_path):
                source = self.read(relative_path)
                self.assertIn('{% extends "core/content_page.html" %}', source)
                self.assertNotIn("<!DOCTYPE html>", source)
                self.assertNotIn('<header class="masthead">', source)
                self.assertNotIn('class="band band-cream', source)
                self.assertNotIn('class="band band-lavender', source)

    def test_project_pages_keep_one_shared_ancestor(self) -> None:
        for relative_path in PROJECT_CHILDREN:
            with self.subTest(template=relative_path):
                source = self.read(relative_path)
                self.assertIn("{% extends 'projects/_base.html' %}", source)
                self.assertNotIn("<!DOCTYPE html>", source)

        for relative_path in SUBMISSION_PROJECT_CHILDREN:
            with self.subTest(template=relative_path):
                source = self.read(relative_path)
                self.assertIn('{% extends "courses/_submission_page.html" %}', source)
                self.assertNotIn("<!DOCTYPE html>", source)

    def test_homepage_remains_an_explicit_layout_exception(self) -> None:
        homepage = self.read("templates/core/home.html")
        self.assertNotIn("{% extends \"core/content_page.html\" %}", homepage)

    def test_auth_pages_remain_the_other_explicit_layout_exception(self) -> None:
        auth_parent = self.read("accounts/templates/account/auth_page.html")

        self.assertNotIn('{% extends "core/content_page.html" %}', auth_parent)
        self.assertIn('class="band band-lavender"', auth_parent)
