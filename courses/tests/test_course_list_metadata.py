from courses.tests.course_list_base import CourseListViewTestBase


class CourseListMetadataTest(CourseListViewTestBase):
    def test_course_list_shows_active_course_metadata(self):
        archive_course = self.create_archived_course_fixture()
        self.configure_active_course_metadata()

        response = self.course_list_response()

        self.assertEqual(response.status_code, 200)
        # The duration, dates and next-assignment facts stay real context data the
        # view computes for every cohort (`home_duration_label`,
        # `home_current_assignment`) even though the plain, consistent catalogue
        # card no longer displays them -- the owner's "no special treatment" ask
        # trimmed the card to a title and one outcome line, the whole card
        # itself already the click target; that richer detail lives on the
        # family and cohort pages instead.
        self.assert_active_course_metadata(response)
        self.assertContains(response, "Database-provided course summary.")
        self.assert_active_course_card(response)
        self.assert_archive_course_row(response, archive_course)
        self.assertNotContains(
            response,
            "https://courses.datatalks.club/test-course/register",
        )
        self.assertNotContains(response, "home-stats-grid")
        self.assertNotContains(response, "Course page")
