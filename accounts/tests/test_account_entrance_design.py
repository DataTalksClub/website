"""The account entrance family, rebuilt on the design system.

`/accounts/signup/` is where the homepage's primary call to action lands. It
was once the last page in the flow still rendering allauth's unstyled default
document, and `AccountEntranceDocumentTests` below still holds every entrance
page — signup included — to the design system's own document contract (one
inline stylesheet, the shared shell, the cream/lavender seam).

Since `accounts.auth.ClosedAccountAdapter` closed plain email/password signup
(see `accounts/tests/test_plain_signup_closed.py`, which pins that with real
HTTP assertions), `/accounts/signup/` no longer renders an interactive form:
every GET and POST renders `account/signup_closed.html` instead of
`account/signup.html`. Most of what `SignupPageTests` and
`SignupProviderChoiceTests` used to check — field labels, autocomplete,
CSRF/`next` plumbing on the live form, provider buttons, the error-state
accessibility wiring — checked controls that do not exist any more, and those
tests were retired rather than bent to pass against a page that has neither a
form nor providers. What is left in those two classes is what is still
genuinely theirs: the one template-sharing assertion that survives as a
login-only check, and the provider-partial test that never depended on the
signup route being open in the first place.
"""

from __future__ import annotations

import re

from django.template import Context, Template
from django.test import TestCase

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
    """Every entrance page is a design system document, like the rest of the site."""

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
    """What is still this class's to check, now that signup itself is closed.

    Every other test that used to live here — the rebuilt-template check, field
    labels and autocomplete, the CSRF token and `action` target, the `next`
    hidden input, the legal-reassurance links, both error-state accessibility
    tests — checked an interactive form (`account/signup.html`) that
    `accounts.auth.ClosedAccountAdapter` no longer lets a signed-out visitor
    reach. They were retired outright rather than pointed at
    `account/signup_closed.html`, which has none of those controls to check;
    `accounts/tests/test_plain_signup_closed.py` already pins what that page
    renders instead.

    One assertion survives, changed rather than deleted: `signup` and `login`
    used to be shown extending the same `account/auth_page.html` template.
    Signup no longer does — `signup_closed.html` is its own standalone
    document — so only login's half of that claim is still true, and is kept
    here as a login-only check rather than dropped along with the rest.
    """

    def test_login_still_extends_the_shared_auth_document(self) -> None:
        login = self.client.get("/accounts/login/")

        self.assertTemplateUsed(login, "account/auth_page.html")


class SignupProviderChoiceTests(TestCase):
    """What is still this class's to check, now that signup itself is closed.

    This class used to prove the provider block actually rendered on
    `/accounts/signup/`: named controls, correct destinations and `next`
    propagation, decorative brand marks, ordering ahead of the email form.
    None of that renders any more — `account/signup_closed.html` has no
    provider block at all — so those four tests, and the `SocialApp`/`Site`
    fixtures (`setUpTestData`/`setUp`) they alone needed, were retired
    outright rather than pointed at a page with nothing left to find.

    `test_the_provider_partial_accepts_links_from_another_template` below
    never depended on the signup route being open — it renders
    `account/_social_provider_choices.html` directly from a template string
    with a hand-built provider list, exercising the reusable partial on its
    own terms. It is untouched.
    """

    def test_the_provider_partial_accepts_links_from_another_template(self) -> None:
        body = Template(
            '{% include "account/_social_provider_choices.html" '
            'with provider_list=providers provider_url_mode="provided" %}'
        ).render(
            Context(
                {
                    "providers": [
                        {
                            "id": "github",
                            "name": "GitHub",
                            "login_url": "/accounts/github/login/?next=%2Fcommunity%2F",
                        }
                    ]
                }
            )
        )

        self.assertIn("Continue with GitHub", body)
        self.assertIn(
            'href="/accounts/github/login/?next=%2Fcommunity%2F"',
            body,
        )
        self.assertIn('<svg class="provider-mark"', body)
