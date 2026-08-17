from accounts.studio_test_support import grant_studio_role
from courses.models import User
from courses.tests.leaderboard_base import LeaderboardTestBase


class LeaderboardScoreBreakdownAdminTestCase(LeaderboardTestBase):
    def test_score_breakdown_admin_button_visible_for_staff(self):
        enrollment = self.create_student("student1")
        admin_user = User.objects.create_user(username="admin", is_staff=True)
        grant_studio_role(admin_user, "course_operator")

        self.client.force_login(admin_user)
        url = self.score_breakdown_url(enrollment)
        response = self.client.get(url)

        self.assertEqual(response.status_code, 200)
        admin_edit_url = self.admin_enrollment_edit_url(enrollment)
        self.assertContains(response, admin_edit_url)
        # The design 5a page loads no icon font, so the repair control is
        # named rather than marked with a glyph (issue #179).
        self.assertContains(response, "Admin: Edit Enrollment")

    def test_score_breakdown_admin_button_hidden_for_regular_user(self):
        enrollment = self.create_student("student1")
        regular_user = User.objects.create_user(
            username="regular",
            is_staff=False,
        )

        self.client.force_login(regular_user)
        url = self.score_breakdown_url(enrollment)
        response = self.client.get(url)

        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "Admin: Edit Enrollment")
        self.assertNotContains(
            response,
            self.admin_enrollment_edit_url(enrollment),
        )

    def test_score_breakdown_admin_button_hidden_for_anonymous(self):
        enrollment = self.create_student("student1")
        url = self.score_breakdown_url(enrollment)

        response = self.client.get(url)

        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "Admin: Edit Enrollment")
        self.assertNotContains(
            response,
            self.admin_enrollment_edit_url(enrollment),
        )
