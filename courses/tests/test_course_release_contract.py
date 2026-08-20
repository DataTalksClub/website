"""Keep the deployed course-page contract in lockstep with the rendered page.

``playwright_tests/test_deployed_smoke.py`` pins these markers against the live
development origin during promotion, and that file only executes at deploy time,
so a course-page change between deploys used to orphan it silently: ``846f367``
retired the module accordion the smoke still expected and the drift surfaced
three deploys later as a failed promotion (issue #204, the third layer of the
class after #198's content pins and #200's token screening).  This guard renders
the representative course page from the checked local seed and holds it to
exactly the markers the deployed smoke pins — imported from
``courses.course_page_contract`` rather than re-typed — so the two contracts
cannot drift apart again.  The next ``846f367``-style revert fails Django CI in
seconds instead of a seventeen-minute deploy.
"""

from __future__ import annotations

from django.test import TestCase

from courses.course_page_contract import (
    COURSE_HOMEWORK_HEADING,
    COURSE_HOMEWORK_HEADING_ID,
    COURSE_PROJECTS_HEADING,
    COURSE_PROJECTS_HEADING_ID,
    REPRESENTATIVE_COURSE_PATH,
    REPRESENTATIVE_COURSE_TITLE,
    RETIRED_MODULES_HEADING,
    RETIRED_MODULES_HEADING_ID,
)
from courses.services.local_course_seed import seed_local_courses


class CoursePageReleaseContractCoherenceTests(TestCase):
    def test_the_seeded_course_page_renders_the_deployed_contract_markers(self) -> None:
        """Both gates render the same seed record; both tables must be on it.

        The deployed smoke reaches this page through the catalogue and asserts
        the course's own heading plus the Homework and Projects table headings
        (846f367's two-table assignments band).  The seed writes eight
        homeworks and three projects for this course, so both conditional
        sections render and the contract is meaningful: if the seed ever stops
        giving the representative course both kinds of work, the deployed leg
        would fail against live data, and this guard fails here first.
        """

        seed_local_courses()

        response = self.client.get(REPRESENTATIVE_COURSE_PATH)

        self.assertEqual(response.status_code, 200)
        html = response.content.decode("utf-8")
        self.assertIn(REPRESENTATIVE_COURSE_TITLE, html)
        self.assertTrue(response.context["homeworks"])
        self.assertTrue(response.context["projects"])
        for heading_id, heading_text in (
            (COURSE_HOMEWORK_HEADING_ID, COURSE_HOMEWORK_HEADING),
            (COURSE_PROJECTS_HEADING_ID, COURSE_PROJECTS_HEADING),
        ):
            with self.subTest(heading=heading_text):
                self.assertIn(f'<h2 id="{heading_id}">', html)
                self.assertIn(f">{heading_text}</h2>", html)

    def test_the_retired_module_accordion_stays_off_the_course_page(self) -> None:
        """The negative half of the deployed contract (issue #204).

        ``257680f`` shipped a numbered module accordion and ``846f367``
        rejected it — it "hid the assignment list behind a click on a page
        whose whole job is to show it".  The deployed smoke asserts the
        accordion's heading and ``details.module`` elements are absent; the
        same regression is pinned here so a revert fails Django CI.  The
        retired markers are asserted against the page's main region, because
        the shared design system's stylesheet commentary still documents the
        accordion vocabulary the rest of the system may draw with.
        """

        seed_local_courses()

        response = self.client.get(REPRESENTATIVE_COURSE_PATH)

        self.assertEqual(response.status_code, 200)
        html = response.content.decode("utf-8")
        main = html[html.index("<main") : html.index("</main>")]
        self.assertNotIn(RETIRED_MODULES_HEADING, main)
        self.assertNotIn(f'id="{RETIRED_MODULES_HEADING_ID}"', main)
        self.assertNotIn("<details", main)
