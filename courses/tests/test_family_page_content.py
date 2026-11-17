"""The family landing's pure composers: syllabus rows, stories, project cards.

These compose the page's facts from model-shaped rows without touching the
database, so the tests do either: every input here is a stub that carries the
attributes the composer reads, nothing more.
"""

from datetime import date
from types import SimpleNamespace

from django.test import SimpleTestCase

from courses.course_page_content import (
    family_edition_rows,
    family_project_cards,
    family_story_rows,
    family_syllabus_rows,
    merge_syllabus_rows_with_projects,
)


class FamilySyllabusRowsTests(SimpleTestCase):
    def test_numbers_units_and_strips_the_spoken_module_prefix(self):
        units = [
            SimpleNamespace(title="Module 1: Agentic RAG", summary=""),
            SimpleNamespace(title="Module 2: Vector Search", summary="Embeddings."),
        ]

        rows = family_syllabus_rows(units)

        self.assertEqual([row.index for row in rows], ["01", "02"])
        self.assertEqual([row.title for row in rows], ["Agentic RAG", "Vector Search"])
        self.assertEqual(rows[1].summary, "Embeddings.")

    def test_strips_homework_prefixes_too(self):
        rows = family_syllabus_rows(
            [SimpleNamespace(title="Homework 3: Orchestration", summary="")]
        )

        self.assertEqual(rows[0].title, "Orchestration")

    def test_a_case_variant_is_still_a_prefix(self):
        rows = family_syllabus_rows([SimpleNamespace(title="module 10: Capstone", summary="")])

        self.assertEqual(rows[0].title, "Capstone")

    def test_an_unprefixed_title_passes_through(self):
        rows = family_syllabus_rows([SimpleNamespace(title="Capstone project", summary="")])

        self.assertEqual(rows[0].title, "Capstone project")

    def test_a_summary_that_is_only_a_link_is_not_a_summary(self):
        rows = family_syllabus_rows(
            [
                SimpleNamespace(
                    title="Homework 1: Intro",
                    summary="",
                    description="https://github.com/example/course/blob/main/homework1.md",
                )
            ]
        )

        self.assertEqual(rows[0].summary, "")


class MergeSyllabusRowsWithProjectsTests(SimpleTestCase):
    """The syllabus's real projects, interleaved by chronology (issue: "for ml
    zoomcamp we have midterm project in the middle of the syllabus let's
    include it chronographically" -- not appended after every module).
    """

    def _units(self):
        return family_syllabus_rows(
            [
                SimpleNamespace(title="Module 1: Intro", summary=""),
                SimpleNamespace(title="Module 2: Modeling", summary=""),
                SimpleNamespace(title="Module 3: Deployment", summary=""),
                SimpleNamespace(title="Module 4: Monitoring", summary=""),
            ]
        )

    def test_a_midterm_lands_between_the_modules_it_falls_between(self):
        units = self._units()
        due_dates = [
            date(2026, 1, 5),
            date(2026, 1, 12),
            date(2026, 1, 19),
            date(2026, 1, 26),
        ]
        midterm = SimpleNamespace(title="Midterm project", submission_due_date=date(2026, 1, 15))
        capstone = SimpleNamespace(title="Capstone project", submission_due_date=date(2026, 2, 1))

        merged = merge_syllabus_rows_with_projects(
            units, due_dates, [midterm, capstone], ["/midterm", "/capstone"]
        )

        titles = [row.title for row in merged]
        self.assertEqual(
            titles,
            [
                "Intro",
                "Modeling",
                "Midterm project",
                "Deployment",
                "Monitoring",
                "Capstone project",
            ],
        )
        self.assertEqual(
            [row.is_project for row in merged], [False, False, True, False, False, True]
        )
        # A project row carries no module number, and a single-attempt
        # project links straight to its own page.
        midterm_row = merged[2]
        self.assertEqual(midterm_row.index, "")
        self.assertEqual(midterm_row.url, "/midterm")
        # The plain module rows keep their own original numbering.
        self.assertEqual(
            [row.index for row in merged if not row.is_project], ["01", "02", "03", "04"]
        )

    def test_a_project_due_before_every_dated_unit_leads_the_list(self):
        units = self._units()
        due_dates = [date(2026, 1, 5), date(2026, 1, 12), date(2026, 1, 19), date(2026, 1, 26)]
        early_project = SimpleNamespace(
            title="Warm-up project", submission_due_date=date(2026, 1, 1)
        )

        merged = merge_syllabus_rows_with_projects(units, due_dates, [early_project], ["/early"])

        self.assertEqual(merged[0].title, "Warm-up project")
        self.assertTrue(merged[0].is_project)

    def test_a_project_with_no_dated_units_to_compare_falls_back_to_the_end(self):
        units = self._units()
        due_dates = [None, None, None, None]
        capstone = SimpleNamespace(title="Capstone project", submission_due_date=date(2026, 2, 1))

        merged = merge_syllabus_rows_with_projects(units, due_dates, [capstone], ["/capstone"])

        self.assertEqual(merged[-1].title, "Capstone project")
        self.assertTrue(merged[-1].is_project)

    def test_no_projects_leaves_the_unit_list_untouched(self):
        units = self._units()
        due_dates = [None, None, None, None]

        merged = merge_syllabus_rows_with_projects(units, due_dates, [], [])

        self.assertEqual(merged, units)


class FamilyStoryRowsTests(SimpleTestCase):
    def _testimonial(self, **overrides):
        row = SimpleNamespace(
            name="Alex",
            quote="Built a thing.",
            attribution="LLM Zoomcamp graduate",
            source_url="",
            portrait_url="",
            role_before="",
            role_after="",
            elapsed="",
        )
        for name, value in overrides.items():
            setattr(row, name, value)
        return row

    def test_flattens_the_fields_the_band_renders(self):
        story = family_story_rows([self._testimonial()])[0]

        self.assertEqual(story.name, "Alex")
        self.assertEqual(story.quote, "Built a thing.")
        self.assertEqual(story.attribution, "LLM Zoomcamp graduate")

    def test_reads_the_portrait_through_the_model_not_the_key(self):
        story = family_story_rows([self._testimonial(portrait_url="/static/core/x.jpg")])[0]

        self.assertEqual(story.portrait_url, "/static/core/x.jpg")

    def test_carries_a_stated_role_transition_and_elapsed_time(self):
        story = family_story_rows(
            [
                self._testimonial(
                    role_before="Data Analyst",
                    role_after="ML Engineer",
                    elapsed="6 months",
                )
            ]
        )[0]

        self.assertEqual(story.role_before, "Data Analyst")
        self.assertEqual(story.role_after, "ML Engineer")
        self.assertEqual(story.elapsed, "6 months")

    def test_a_quote_with_no_stated_transition_invents_none(self):
        story = family_story_rows([self._testimonial()])[0]

        self.assertEqual(story.role_before, "")
        self.assertEqual(story.role_after, "")
        self.assertEqual(story.elapsed, "")


class FamilyProjectCardsTests(SimpleTestCase):
    def test_takes_the_newest_edition_that_holds_projects(self):
        newer_empty = SimpleNamespace(cohort=SimpleNamespace(identifier="2026"), projects=[])
        older_full = SimpleNamespace(
            cohort=SimpleNamespace(identifier="2025"),
            projects=[SimpleNamespace(slug="capstone-1"), SimpleNamespace(slug="capstone-2")],
        )

        cards = family_project_cards([newer_empty, older_full])

        self.assertEqual(
            [(card.cohort.identifier, card.project.slug) for card in cards],
            [("2025", "capstone-1"), ("2025", "capstone-2")],
        )


class FamilyEditionRowsTests(SimpleTestCase):
    """The campaign that promotes a cohort must not freeze its pill in time.

    A regression: the promoted edition's pill used to read "registration
    open" unconditionally, even long after the cohort itself had started,
    because the promoted-cohort branch was checked before the dates were.
    """

    def _edition(self, **cohort_overrides):
        cohort = SimpleNamespace(
            pk=1,
            start_date=None,
            end_date=None,
            delivery_mode="",
        )
        for name, value in cohort_overrides.items():
            setattr(cohort, name, value)
        return SimpleNamespace(cohort=cohort), cohort

    def test_promoted_cohort_reads_in_progress_once_it_has_started(self):
        today = date(2026, 9, 15)
        edition, cohort = self._edition(start_date=date(2026, 9, 14), end_date=date(2027, 1, 25))

        rows = family_edition_rows([edition], cohort, today)

        self.assertEqual(rows[0].state_words, "in progress")
        self.assertEqual(rows[0].state_pill_class, "status-pill-live")

    def test_promoted_cohort_still_reads_registration_open_before_it_starts(self):
        today = date(2026, 8, 1)
        edition, cohort = self._edition(start_date=date(2026, 9, 14), end_date=date(2027, 1, 25))

        rows = family_edition_rows([edition], cohort, today)

        self.assertEqual(rows[0].state_words, "registration open")
        self.assertEqual(rows[0].state_pill_class, "status-pill-open")

    def test_promoted_cohort_past_its_end_date_reads_finished(self):
        today = date(2027, 2, 1)
        edition, cohort = self._edition(start_date=date(2026, 9, 14), end_date=date(2027, 1, 25))

        rows = family_edition_rows([edition], cohort, today)

        self.assertEqual(rows[0].state_words, "finished")
        self.assertEqual(rows[0].state_pill_class, "status-pill-wait")

    def test_no_edition_holds_projects(self):
        empty = SimpleNamespace(cohort=SimpleNamespace(identifier="2026"), projects=[])

        self.assertEqual(family_project_cards([empty]), ())


class ProjectAttemptGroupingTests(SimpleTestCase):
    """The attempts of one project collapse into one syllabus row.

    Owner feedback (2026-11): "Project Attempt 1 / 2 / Last Project Attempt"
    and "Capstone Project (Attempt 1) / (Attempt 2)" were near-empty rows drawn
    by a different component; one row now lists every attempt's deadlines.
    """

    def _units(self):
        return family_syllabus_rows([SimpleNamespace(title="Module 1: Intro", summary="")])

    def _project(self, title, due, review, **extra):
        return SimpleNamespace(
            title=title,
            description=extra.get("description", ""),
            instructions_url=extra.get("instructions_url", ""),
            submission_due_date=due,
            peer_review_due_date=review,
        )

    def test_numbered_and_last_attempts_share_one_row(self):
        projects = [
            self._project(
                "Project Attempt 1",
                date(2026, 3, 30),
                date(2026, 4, 8),
                description="Put everything we learned into practice. (Attempt 1)",
                instructions_url="https://example.com/project.md",
            ),
            self._project("Project Attempt 2", date(2026, 4, 20), date(2026, 4, 27)),
            self._project("Last Project Attempt", date(2026, 5, 4), date(2026, 5, 11)),
        ]

        merged = merge_syllabus_rows_with_projects(
            self._units(), [None], projects, ["/p1", "/p2", "/p3"]
        )

        self.assertEqual(len(merged), 2)
        row = merged[1]
        self.assertTrue(row.is_project)
        self.assertEqual(row.title, "Project")
        self.assertEqual(row.summary, "Put everything we learned into practice.")
        self.assertEqual(row.brief_url, "https://example.com/project.md")
        self.assertEqual(row.url, "")
        self.assertEqual(
            [(attempt.title, attempt.url) for attempt in row.attempts],
            [
                ("Project Attempt 1", "/p1"),
                ("Project Attempt 2", "/p2"),
                ("Last Project Attempt", "/p3"),
            ],
        )
        self.assertEqual(row.attempts[2].peer_review_due, date(2026, 5, 11))

    def test_parenthesised_attempts_keep_the_project_name(self):
        projects = [
            self._project("Capstone Project (Attempt 1)", date(2027, 1, 15), date(2027, 1, 22)),
            self._project("Capstone Project (Attempt 2)", date(2027, 1, 29), date(2027, 2, 5)),
        ]

        merged = merge_syllabus_rows_with_projects(self._units(), [None], projects, ["/a1", "/a2"])

        self.assertEqual([row.title for row in merged], ["Intro", "Capstone Project"])
        self.assertEqual(len(merged[1].attempts), 2)

    def test_different_projects_stay_separate_rows(self):
        projects = [
            self._project("Midterm Project", date(2025, 11, 19), date(2025, 11, 27)),
            self._project("Capstone 1", date(2026, 1, 5), date(2026, 1, 12)),
            self._project("Capstone 2", date(2026, 1, 21), date(2026, 1, 26)),
        ]

        merged = merge_syllabus_rows_with_projects(
            self._units(), [None], projects, ["/m", "/c1", "/c2"]
        )

        self.assertEqual([row.title for row in merged], ["Intro", "Midterm Project", "Capstone"])
        self.assertEqual(merged[1].url, "/m")
        self.assertEqual(len(merged[2].attempts), 2)

    def test_a_link_or_placeholder_description_is_not_shown_as_a_summary(self):
        projects = [
            self._project(
                "Attempt 1",
                date(2025, 7, 14),
                date(2025, 7, 23),
                description="https://github.com/example/course/blob/main/project.md",
            ),
            self._project("Attempt 2", date(2025, 8, 4), date(2025, 8, 11), description="TBA"),
        ]

        merged = merge_syllabus_rows_with_projects(self._units(), [None], projects, ["/1", "/2"])

        row = merged[1]
        self.assertEqual(row.summary, "")
        self.assertEqual(row.brief_url, "https://github.com/example/course/blob/main/project.md")
        # Nothing but attempt words: the row is simply the project.
        self.assertEqual(row.title, "Project")
