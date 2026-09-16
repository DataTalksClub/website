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
