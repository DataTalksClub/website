"""The registration page shapes a campaign's Markdown into sections.

``courses.views.registration_marketing`` reads the campaign's own copy and
decides only how to present it: a dated list becomes a schedule, a bold-led
list becomes titled rows, the rest stays prose, and a leading heading that
repeats the campaign title is not drawn a second time.
"""

from django.test import SimpleTestCase

from courses.views.course_page_context import faq_question_without_section_label
from courses.views.registration_marketing import registration_marketing

COPY = """\
## Production AI Agents for Generative Media

4 weeks · ~8-10 h/week · free.

> How do you build a production AI system?

### Dates (2026 cohort)

- Welcome: Tue 10 Nov 2026
- Course starts: Mon 17 Nov 2026
- Module 1 (two weeks): 17-30 Nov 2026 (through Sun 30 Nov)

### Program

- **Module 1 - Make it work.** Generative media workloads,
  first GPU generation.
- **Module 2 - Make it intelligent.** Agents that use GPUs.

### Who this is for

- ML / MLOps / data engineers adding image generation.
- Backend engineers turning a prototype into a service.

### Prerequisites

Python, Docker basics, HTTP APIs, command line.
"""


class RegistrationMarketingTests(SimpleTestCase):
    def setUp(self):
        self.marketing = registration_marketing(
            COPY, title="Production AI Agents for Generative Media"
        )

    def test_the_title_heading_is_dropped_and_its_body_becomes_the_intro(self):
        self.assertNotIn("<h2", self.marketing.intro_html)
        self.assertIn("4 weeks", self.marketing.intro_html)
        self.assertIn("<blockquote>", self.marketing.intro_html)
        self.assertEqual(
            [section.heading for section in self.marketing.sections],
            ["Dates (2026 cohort)", "Program", "Who this is for", "Prerequisites"],
        )

    def test_a_dated_list_is_a_schedule(self):
        dates = self.marketing.sections[0]
        self.assertEqual(dates.kind, "schedule")
        self.assertEqual(
            [(str(row.label), str(row.value)) for row in dates.rows],
            [
                ("Welcome", "Tue 10 Nov 2026"),
                ("Course starts", "Mon 17 Nov 2026"),
                ("Module 1 (two weeks)", "17-30 Nov 2026 (through Sun 30 Nov)"),
            ],
        )

    def test_a_bold_led_list_is_titled_rows_with_wrapped_lines_joined(self):
        program = self.marketing.sections[1]
        self.assertEqual(program.kind, "features")
        self.assertEqual(str(program.rows[0].label), "Module 1 - Make it work.")
        self.assertEqual(
            str(program.rows[0].value), "Generative media workloads, first GPU generation."
        )

    def test_other_bodies_stay_sanitised_prose(self):
        audience, prerequisites = self.marketing.sections[2:]
        self.assertEqual(audience.kind, "prose")
        self.assertIn("<li>", audience.html)
        self.assertEqual(prerequisites.kind, "prose")
        self.assertIn("Python, Docker basics", prerequisites.html)

    def test_section_ids_are_unique_and_prefixed(self):
        slugs = [section.slug for section in self.marketing.sections]
        self.assertEqual(len(slugs), len(set(slugs)))
        self.assertTrue(all(slug.startswith("registration-") for slug in slugs))

    def test_markup_is_sanitised(self):
        marketing = registration_marketing(
            "### Dates\n\n- Start: <script>alert(1)</script>Mon\n- End: Fri\n", title="X"
        )
        self.assertNotIn("<script", str(marketing.sections[0].rows[0].value))

    def test_empty_copy_is_falsy(self):
        self.assertFalse(registration_marketing("", title="X"))


class FaqSectionLabelTests(SimpleTestCase):
    def test_a_prefix_its_section_already_names_is_dropped(self):
        self.assertEqual(
            faq_question_without_section_label(
                "Course: When does the course start?", "General Course-Related Questions"
            ),
            "When does the course start?",
        )
        self.assertEqual(
            faq_question_without_section_label(
                "Course - can I still join?", "General Course-Related Questions"
            ),
            "Can I still join?",
        )

    def test_a_prefix_that_adds_information_is_kept(self):
        question = "Leaderboard: I am not on the leaderboard"
        self.assertEqual(
            faq_question_without_section_label(question, "General Course-Related Questions"),
            question,
        )

    def test_a_question_without_a_prefix_passes_through(self):
        question = "Is it going to be live? When?"
        self.assertEqual(
            faq_question_without_section_label(question, "General Course-Related Questions"),
            question,
        )
