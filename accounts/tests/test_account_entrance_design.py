"""The account entrance family, rebuilt on the design system.

`/accounts/signup/` is where the homepage's primary call to action lands.
`AccountEntranceDocumentTests` below holds every entrance page — signup
included — to the design system's own document contract (one inline
stylesheet, the shared shell, the cream/lavender seam).

Signup is open (`accounts.auth.AccountAdapter`): a GET renders the real
interactive form (`account/signup.html`), same as before the account
takeover fix that briefly closed it (`accounts/tests/test_plain_signup_closed.py`
pins the current, open behavior, including how a matching email is now
refused with a plain error rather than a blanket closed page). Field-level
coverage of the live form (labels, autocomplete, CSRF/`next` plumbing,
provider ordering, error-state accessibility) is not rebuilt in this class —
that was retired when signup closed and is not restored here; the one
template-sharing assertion below is what has stayed continuously true
throughout, and the provider-partial test never depended on the signup route
being open in the first place.
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
                # The site-wide tour stripe adds one closing band-ink section
                # above the footer; every other band stays on the content ground.
                self.assertEqual(
                    [g for g in grounds[1:] if g not in {"band-lavender", "band-ink"}],
                    [],
                )
                self.assertEqual(grounds.count("band-ink"), 1)

    def test_every_entrance_page_has_exactly_one_first_level_heading(self) -> None:
        for name, body in self.rendered_pages().items():
            with self.subTest(page=name):
                self.assertEqual(len(re.findall(r"<h1\b", body)), 1)


class SignupPageTests(TestCase):
    """What is still this class's to check.

    Deeper field-level coverage that used to live here — labels and
    autocomplete, the CSRF token and `action` target, the `next` hidden
    input, the legal-reassurance links, both error-state accessibility tests
    — was retired when signup briefly closed and is not rebuilt here now that
    it is open again; `accounts/tests/test_plain_signup_closed.py` covers the
    current open/refused behavior at the HTTP level instead.

    `signup` and `login` both extend `account/auth_page.html` again now that
    signup renders the real form.
    """

    def test_signup_and_login_extend_the_shared_auth_document(self) -> None:
        signup = self.client.get("/accounts/signup/")
        login = self.client.get("/accounts/login/")

        self.assertTemplateUsed(signup, "account/auth_page.html")
        self.assertTemplateUsed(login, "account/auth_page.html")


class SignupProviderChoiceTests(TestCase):
    """What is still this class's to check.

    Deeper coverage that used to live here — named controls, correct
    destinations and `next` propagation, decorative brand marks, ordering
    ahead of the email form, and the `SocialApp`/`Site` fixtures they alone
    needed — was retired when signup briefly closed and is not rebuilt here
    now that it is open again.

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
