from django.urls import reverse

from courses.tests.course_list_base import CourseListViewTestBase


class CourseListProjectsSectionTest(CourseListViewTestBase):
    """The promoted "Learn by building" section (issue: real section,

    one position higher, illustration-left/text-right), replacing the old
    bare `courses-projects-link` closing paragraph.
    """

    def test_projects_section_has_heading_copy_and_link(self):
        response = self.course_list_response()
        content = response.content.decode()

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Learn by building")
        self.assertContains(response, "project")
        self.assertContains(response, "Browse every learner project, from every course")
        all_projects_url = reverse("all_projects")
        self.assertIn(f'href="{all_projects_url}"', content)

    def test_projects_section_sits_between_steps_and_faq(self):
        # Moved up one position: it used to close the page after the FAQ; it
        # now sits right after "How our zoomcamp works" and right before
        # "Frequent questions".
        response = self.course_list_response()
        content = response.content.decode()

        steps_index = content.index("How our zoomcamp works")
        projects_index = content.index("Learn by building")
        faq_index = content.index("Frequent questions")

        self.assertLess(steps_index, projects_index)
        self.assertLess(projects_index, faq_index)

    def test_projects_section_has_an_illustration(self):
        response = self.course_list_response()
        content = response.content.decode()

        projects_index = content.index('class="courses-projects"')
        faq_index = content.index('<section class="courses-faq', projects_index)
        section_html = content[projects_index:faq_index]

        self.assertIn('class="courses-projects-art"', section_html)
        self.assertIn("doodle", section_html)

    def test_projects_section_hidden_without_a_resolvable_gallery_url(self):
        # Same gating as the old link: courses/views/course_list.py always
        # passes ``optional_all_projects_url()``, which only returns None
        # under a URLConf that never registers ``all_projects`` (the
        # studio/course-management deployment target). The full test
        # URLConf always resolves it, so this only re-asserts the template's
        # own {% if all_projects_url %} guard stays intact.
        response = self.course_list_response()
        self.assertIsNotNone(response.context["all_projects_url"])
        self.assertContains(response, "courses-projects")


class CourseListCatalogCardLinkTest(CourseListViewTestBase):
    """The catalogue card dropped its own "View course" line -- the whole

    card is already `role="link"` and the click target, so the line was a
    redundant second affordance.
    """

    def test_catalog_card_has_no_redundant_view_course_link(self):
        response = self.course_list_response()
        content = response.content.decode()
        course_card = self.course_card_html(content, self.course)

        self.assertNotIn("View course", course_card)
        self.assertIn('role="link"', course_card)


class CourseListStatsLabelTest(CourseListViewTestBase):
    def test_materials_stat_drops_the_github_framing(self):
        response = self.course_list_response()
        content = response.content.decode()

        # The big number ("100%") already carries the "100%"; the label
        # below it just names what it is, without repeating the number or
        # naming GitHub specifically.
        stat_index = content.index("<strong>100%</strong>")
        stat_html = content[stat_index : stat_index + 200]

        self.assertIn("<span>open</span>", stat_html)
        self.assertNotContains(response, "materials public on GitHub")
