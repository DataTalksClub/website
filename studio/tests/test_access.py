from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from accounts.studio_test_support import authenticated_studio_client, make_studio_user


class StudioAccessTests(TestCase):
    def test_anonymous_user_is_sent_to_safe_login_flow(self) -> None:
        response = self.client.get(reverse("studio:home"))
        self.assertRedirects(
            response,
            f"{reverse('login')}?next={reverse('studio:home').replace('/', '%2F')}",
            fetch_redirect_response=False,
        )
        self.assertIn("private", response.headers["Cache-Control"])
        self.assertIn("no-store", response.headers["Cache-Control"])

    def test_non_staff_and_unassigned_staff_are_denied(self) -> None:
        for user in (
            get_user_model().objects.create_user(username="learner"),
            get_user_model().objects.create_user(username="staff", is_staff=True),
        ):
            with self.subTest(user=user.username):
                self.client.force_login(user)
                response = self.client.get(reverse("studio:home"))
                self.assertEqual(response.status_code, 403)
                self.assertNotContains(response, "Traceback", status_code=403)
                self.client.logout()

    def test_explicitly_authorized_staff_user_sees_studio_shell(self) -> None:
        user = make_studio_user(username="authorized", roles=("content_operator",))
        client = authenticated_studio_client(user)
        response = client.get(reverse("studio:home"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Studio")
        self.assertIn("private", response.headers["Cache-Control"])
        self.assertIn("no-store", response.headers["Cache-Control"])
        self.assertEqual(response.headers["X-Robots-Tag"], "noindex, nofollow")
