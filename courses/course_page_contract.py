"""The deployed course-page contract, written once and shared by both gates.

``playwright_tests/test_deployed_smoke.py`` pins these markers against the live
development origin during promotion, and that file only executes at deploy time,
so a course-page change between deploys used to orphan it silently (issue #204:
``846f367`` retired the module accordion the smoke still expected, and the drift
surfaced three deploys later as a failed promotion).  ``courses/tests/
test_course_release_contract.py`` renders the representative course page from
the checked local seed and holds it to exactly these markers — imported from
here, never re-typed — the same lockstep ``core/tests/test_home_release_contract.py``
established for the homepage in #198.  The next course-page redesign that drops
or renames a marker fails Django CI instead of a deploy.

The contract of record is ``courses/templates/courses/course.html`` as restored
by ``846f367``: one lavender assignments band with a Homework table and a
Projects table whose deadlines and states are all visible at once, because the
single module accordion ``257680f`` shipped "hid the assignment list behind a
click on a page whose whole job is to show it".  The accordion's markers are
therefore pinned as retired below: the rendered page must not carry them.

This module is deliberately pure constants with no Django imports so the
deploy-only Playwright file can import it without configured settings.
"""

from __future__ import annotations

# The catalogue record both gates render: the representative course the local
# seed writes from scripts/production_like_course_specs.json (eight homeworks,
# three projects), which is also the course the deployed smoke follows from the
# courses index.
REPRESENTATIVE_COURSE_PATH = "/courses/de-zoomcamp-2026"
REPRESENTATIVE_COURSE_TITLE = "Data Engineering Zoomcamp 2026"

# The assignments band's two tables (846f367).  Each heading is rendered only
# when the course has that kind of work; the representative course has both, so
# both headings are part of the deployed contract.
COURSE_HOMEWORK_HEADING = "Homework"
COURSE_HOMEWORK_HEADING_ID = "homework-heading"
COURSE_PROJECTS_HEADING = "Projects"
COURSE_PROJECTS_HEADING_ID = "projects-heading"

# The retired design-5a accordion surface (257680f), rejected by 846f367.  The
# deployed smoke resolves ``details.module`` as a CSS selector against the live
# DOM and the heading by its accessible name; the shared design system still
# documents the accordion in stylesheet commentary, so neither string is ever
# grepped for in raw rendered HTML — the in-repo guard asserts the retired id
# and the ``<details`` element instead.
RETIRED_MODULES_HEADING = "The modules"
RETIRED_MODULES_HEADING_ID = "modules-heading"
RETIRED_MODULE_ACCORDION_SELECTOR = "details.module"
