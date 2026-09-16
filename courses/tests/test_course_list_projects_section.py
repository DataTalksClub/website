from courses.tests.course_list_base import CourseListViewTestBase


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
