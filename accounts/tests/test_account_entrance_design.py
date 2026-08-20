"""The account entrance family, rebuilt on design 5a.

`/accounts/signup/` is where the homepage's primary call to action lands, and it
was the last page in the flow still rendering allauth's unstyled default
document — a visitor who decided to join met a page that did not look like the
site they came from.  These tests hold the rebuilt pages to the two things that
can quietly break: the design system's own document contract (one inline
stylesheet, the shared shell, the cream/lavender seam), and the authentication
controls the redesign is not allowed to drop — every field, the CSRF token, the
`next` redirect and the provider destinations.

They also hold the form's accessibility, because the error state is the one a
redesign is most likely to get wrong and least likely to see by accident.
"""

from __future__ import annotations

import re

from allauth.socialaccount.models import SocialApp
from django.conf import settings
from django.contrib.sites.models import Site
from django.core.cache import cache
from django.test import TestCase
from django.urls import reverse

STYLE_ELEMENT = re.compile(r"<style\b")
STYLESHEET_LINK = re.compile(r'<link[^>]+rel="stylesheet"')
BAND = re.compile(r'class="band (band-[a-z]+)')

# The pages in the family that a signed-out visitor can reach with a plain GET.
# `/accounts/password/reset/` is deliberately absent: `accounts/urls.py` answers
# it with a 403 ahead of allauth, which is a routing decision this work did not
# touch.
ENTRANCE_PATHS = {
    "signup": "/accounts/signup/",
    "password reset sent": "/accounts/password/reset/done/",
    "password changed": "/accounts/password/reset/key/done/",
    "spent reset link": "/accounts/password/reset/key/abc-def/",
    "inactive account": "/accounts/inactive/",
}


class AccountEntranceDocumentTests(TestCase):
    """Every entrance page is a design 5a document, like the rest of the site."""

    def rendered_pages(self) -> dict[str, str]:
        bodies = {}
        for name, path in ENTRANCE_PATHS.items():
            response = self.client.get(path)
            self.assertEqual(response.status_code, 200, name)
            bodies[name] = response.content.decode()
        return bodies

    def test_every_entrance_page_carries_one_inline_stylesheet_and_no_external_css(self) -> None:
        for name, body in self.rendered_pages().items():
            with self.subTest(page=name):
                self.assertEqual(len(STYLE_ELEMENT.findall(body)), 1)
                self.assertEqual(STYLESHEET_LINK.findall(body), [])
                # The stylesheet is the shared partial, not a page's own fork.
                self.assertIn("--lavender:", body)

    def test_every_entrance_page_includes_the_shared_site_shell(self) -> None:
        for name, path in ENTRANCE_PATHS.items():
            with self.subTest(page=name):
                response = self.client.get(path)
                self.assertTemplateUsed(response, "core/_site_shell_head.html")
                self.assertTemplateUsed(response, "core/_site_shell_foot.html")

    def test_every_entrance_page_opens_warm_and_reads_on_the_content_ground(self) -> None:
        for name, body in self.rendered_pages().items():
            with self.subTest(page=name):
                grounds = BAND.findall(body)
                self.assertEqual(grounds[0], "band-cream")
                self.assertEqual([g for g in grounds[1:] if g != "band-lavender"], [])

    def test_every_entrance_page_has_exactly_one_first_level_heading(self) -> None:
        for name, body in self.rendered_pages().items():
            with self.subTest(page=name):
                self.assertEqual(len(re.findall(r"<h1\b", body)), 1)


class SignupPageTests(TestCase):
    """The sign-up page keeps every control it had, and gains real labels."""

    def test_signup_renders_the_rebuilt_template(self) -> None:
        response = self.client.get("/accounts/signup/")
        body = response.content.decode()

        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "account/signup.html")
        self.assertIn("Choose how you sign up", body)
        self.assertNotIn("Every route below creates the same account.", body)

    def test_every_field_has_a_real_label_and_a_stated_required_mark(self) -> None:
        body = self.client.get("/accounts/signup/").content.decode()

        for field_id, label in (
            ("id_email", "Email"),
            ("id_password1", "Password"),
            ("id_password2", "Password (again)"),
        ):
            with self.subTest(field=field_id):
                self.assertRegex(
                    body,
                    rf'<label class="field-label" for="{field_id}">\s*{re.escape(label)}',
                )
        # Required is stated in text, never by the asterisk alone.
        self.assertIn('<span class="sr-only">(required)</span>', body)
        self.assertIn("are required.", body)

    def test_the_password_fields_work_with_a_password_manager(self) -> None:
        body = self.client.get("/accounts/signup/").content.decode()

        self.assertIn('autocomplete="email"', body)
        self.assertEqual(body.count('autocomplete="new-password"'), 2)
        for field_id in ("id_password1", "id_password2"):
            with self.subTest(field=field_id):
                self.assertRegex(body, rf'<input type="password"[^>]*id="{field_id}"')

    def test_the_csrf_token_and_the_post_target_survive_the_redesign(self) -> None:
        body = self.client.get("/accounts/signup/").content.decode()

        self.assertIn('name="csrfmiddlewaretoken"', body)
        self.assertIn(f'action="{reverse("account_signup")}"', body)

    def test_the_next_redirect_survives_in_both_the_form_and_the_sign_in_link(self) -> None:
        body = self.client.get("/accounts/signup/?next=/courses/").content.decode()

        self.assertIn('<input type="hidden" name="next" value="/courses/">', body)
        self.assertIn("/accounts/login/?next=%2Fcourses%2F", body)

    def test_the_signup_form_keeps_the_legal_reassurance_links(self) -> None:
        body = self.client.get("/accounts/signup/").content.decode()

        self.assertIn('href="/terms"', body)
        self.assertIn('href="/privacy"', body)
        self.assertIn("Terms of Service", body)
        self.assertIn("Privacy Policy", body)

    def test_an_invalid_submission_announces_what_to_fix_next_to_each_field(self) -> None:
        response = self.client.post(
            "/accounts/signup/",
            {"email": "not-an-address", "password1": "one-password", "password2": "another"},
        )
        body = response.content.decode()

        self.assertEqual(response.status_code, 200)
        # The summary is reachable, announced and says what to fix.
        self.assertIn('class="a11y-error-summary"', body)
        self.assertIn('role="alert"', body)
        self.assertIn("data-focus-error-summary", body)
        self.assertIn('href="#id_email"', body)
        # The field error sits beside its field and is linked to it.
        self.assertIn('aria-invalid="true"', body)
        self.assertIn('aria-errormessage="id_email-error"', body)
        self.assertIn('id="id_email-error"', body)
        # Colour never carries the message on its own.
        self.assertIn('<span class="sr-only">Error:</span>', body)

    def test_a_mismatched_password_pair_is_reported_on_the_field_it_belongs_to(self) -> None:
        response = self.client.post(
            "/accounts/signup/",
            {
                "email": "new-member@example.invalid",
                "password1": "a-long-enough-password",
                "password2": "a-different-password",
            },
        )
        body = response.content.decode()

        self.assertEqual(response.status_code, 200)
        self.assertIn('aria-errormessage="id_password2-error"', body)
        self.assertIn("You must type the same password each time.", body)


class SignupProviderChoiceTests(TestCase):
    """The provider block is the page's primary path, so it has to be there."""

    @classmethod
    def setUpTestData(cls) -> None:
        site, _ = Site.objects.get_or_create(
            pk=settings.SITE_ID,
            defaults={"domain": "testserver", "name": "testserver"},
        )
        for provider, name in (("google", "Google"), ("github", "GitHub"), ("slack", "Slack")):
            app = SocialApp.objects.create(
                provider=provider,
                name=name,
                client_id=f"test-{provider}-client-id",
                secret="test-not-a-secret",
            )
            app.sites.add(site)

    def setUp(self) -> None:
        cache.delete("available_providers")

    def test_each_configured_provider_is_a_named_control_not_a_bare_logo(self) -> None:
        body = self.client.get("/accounts/signup/").content.decode()

        for name in ("Google", "GitHub", "Slack"):
            with self.subTest(provider=name):
                self.assertIn(f"Continue with {name}", body)
        self.assertIn('class="provider-choices"', body)

    def test_each_provider_button_points_at_that_provider_and_keeps_next(self) -> None:
        body = self.client.get("/accounts/signup/?next=/courses/").content.decode()

        for provider in ("google", "github", "slack"):
            with self.subTest(provider=provider):
                self.assertIn(f"/accounts/{provider}/login/", body)
        self.assertIn("next=%2Fcourses%2F", body)

    def test_the_brand_marks_are_decoration_and_take_the_theme_with_them(self) -> None:
        body = self.client.get("/accounts/signup/").content.decode()

        marks = re.findall(r'<svg class="provider-mark"[^>]*>', body)
        self.assertEqual(len(marks), 3)
        for mark in marks:
            with self.subTest(mark=mark):
                self.assertIn('aria-hidden="true"', mark)
                self.assertIn('focusable="false"', mark)
        # currentColor, never a hex: the mark flips with the control it sits in.
        self.assertEqual(body.count('<path fill="currentColor"'), 3)

    def test_the_email_form_stays_the_named_alternative_beside_the_providers(self) -> None:
        body = self.client.get("/accounts/signup/").content.decode()

        providers_at = body.index('class="provider-choices"')
        divider_at = body.index('class="entrance-or"')
        form_at = body.index('class="entrance-form"')
        self.assertLess(providers_at, divider_at)
        self.assertLess(divider_at, form_at)
        self.assertIn("Sign up with an email address", body)
