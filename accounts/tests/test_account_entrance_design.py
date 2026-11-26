"""The account entrance family, rebuilt on the design system.

`/accounts/signup/` is where the homepage's primary call to action lands.
`AccountEntranceDocumentTests` below holds every entrance page — signup
included — to the design system's own document contract (one inline
stylesheet, the shared shell, one lavender band holding the entrance card).

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
from urllib.parse import urlencode

from django.conf import settings
from django.middleware.csrf import get_token
from django.template import Context, Template
from django.test import RequestFactory, TestCase

from accounts.services.local_provider_seed import seed_local_social_providers

STYLE_ELEMENT = re.compile(r"<style\b")
STYLESHEET_LINK = re.compile(r'<link[^>]+rel="stylesheet"')
BAND = re.compile(r'class="band (band-[a-z]+)')

# The pages in the family that a signed-out visitor can reach with a plain GET.
ENTRANCE_PATHS = {
    "login": "/accounts/login/",
    "signup": "/accounts/signup/",
    "password reset": "/accounts/password/reset/",
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

    def test_every_entrance_page_is_one_card_on_the_content_ground(self) -> None:
        # One lavender band holding one centred card, with the page's heading
        # inside the card: no cream header over a lone panel.
        for name, body in self.rendered_pages().items():
            with self.subTest(page=name):
                self.assertEqual(BAND.findall(body), ["band-lavender"])
                self.assertEqual(body.count('class="card auth-panel"'), 1)
                card_at = body.index('class="card auth-panel"')
                self.assertGreater(body.index("<h1"), card_at)

    def test_every_entrance_page_extends_the_shared_auth_document(self) -> None:
        for name, path in ENTRANCE_PATHS.items():
            with self.subTest(page=name):
                self.assertTemplateUsed(self.client.get(path), "account/auth_page.html")

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


class SignInCardTests(TestCase):
    """The sign-in card reads top to bottom as one decision."""

    def test_the_card_holds_everything_in_reading_order(self) -> None:
        seed_local_social_providers()

        body = self.client.get("/accounts/login/").content.decode()

        order = [
            'class="card auth-panel"',
            'id="auth-heading"',
            'class="auth-choices"',
            'class="auth-or"',
            'class="auth-form"',
            'class="cta cta-primary interactive-lift auth-submit"',
            "Forgot password?",
            "New here?",
            "We never see your social media passwords.",
        ]
        positions = [body.index(marker) for marker in order]
        self.assertEqual(positions, sorted(positions))
        # One primary action: the providers are secondary controls.
        card = body[body.index('class="card auth-panel"') :]
        self.assertEqual(card.count("cta-primary"), 1)

    def test_no_provider_hides_the_social_block_and_its_lede(self) -> None:
        body = self.client.get("/accounts/login/").content.decode()

        self.assertNotIn('class="auth-choices"', body)
        self.assertNotIn('class="auth-or"', body)
        self.assertNotIn("Continue with a social account", body)
        self.assertNotIn("We never see your social media passwords.", body)
        self.assertIn('class="auth-form"', body)


class SignupProviderChoiceTests(TestCase):
    """Provider choices submit safely while retaining the selected flow URL."""

    def test_allauth_social_login_requires_post(self) -> None:
        self.assertFalse(settings.SOCIALACCOUNT_LOGIN_ON_GET)

    def test_the_provider_partial_posts_with_csrf_and_preserves_next(self) -> None:
        request = RequestFactory().get("/accounts/login/?next=%2Fcommunity%2F")
        body = Template(
            '{% include "account/_social_provider_choices.html" '
            'with provider_list=providers provider_url_mode="provided" %}'
        ).render(
            Context(
                {
                    "request": request,
                    "csrf_token": get_token(request),
                    "providers": [
                        {
                            "id": "github",
                            "name": "GitHub",
                            "login_url": "/accounts/github/login/?next=%2Fcommunity%2F",
                        }
                    ],
                }
            )
        )

        self.assertIn("Continue with GitHub", body)
        self.assertIn(
            'method="post"',
            body,
        )
        self.assertIn(
            'action="/accounts/github/login/?next=%2Fcommunity%2F"',
            body,
        )
        self.assertRegex(
            body,
            r'<input type="hidden" name="csrfmiddlewaretoken" value="[^"]+">',
        )
        self.assertIn('type="submit"', body)
        self.assertIn('<svg class="provider-mark"', body)

    def test_allauth_provider_flow_is_kept_in_the_post_action(self) -> None:
        class Provider:
            id = "github"
            name = "GitHub"

            def get_login_url(self, _request, **params) -> str:
                return f"/accounts/github/login/?{urlencode(params)}"

        request = RequestFactory().get("/accounts/login/?next=%2Fcommunity%2F")
        body = Template(
            '{% include "account/_social_provider_choices.html" '
            'with provider_list=providers provider_url_mode="allauth" '
            'provider_process="connect" provider_action="Connect" %}'
        ).render(
            Context(
                {
                    "request": request,
                    "csrf_token": get_token(request),
                    "providers": [Provider()],
                }
            )
        )

        self.assertIn(
            'action="/accounts/github/login/?process=connect&amp;next=%2Fcommunity%2F"',
            body,
        )
        self.assertIn("Connect GitHub", body)
