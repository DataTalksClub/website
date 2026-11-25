from allauth.socialaccount.adapter import get_adapter
from allauth.socialaccount.models import SocialApp
from django.contrib.sites.models import Site
from django.core.cache import cache
from django.core.exceptions import MultipleObjectsReturned
from django.http import Http404
from django.test import RequestFactory, TestCase


class UnconfiguredProviderTests(TestCase):
    def setUp(self):
        cache.clear()
        SocialApp.objects.all().delete()

    def test_direct_login_and_callback_return_404_without_an_app(self):
        for provider in ("google", "github", "slack"):
            for suffix in ("login/", "login/callback/"):
                with self.subTest(provider=provider, suffix=suffix):
                    response = self.client.get(f"/accounts/{provider}/{suffix}")
                    self.assertEqual(response.status_code, 404)

    def test_login_does_not_link_to_unconfigured_providers(self):
        response = self.client.get("/accounts/login/")
        self.assertEqual(response.status_code, 200)
        for provider in ("google", "github", "slack"):
            self.assertNotContains(response, f"/accounts/{provider}/login/")

    def test_configured_app_is_still_resolved(self):
        app = SocialApp.objects.create(provider="github", name="GitHub", client_id="example")
        app.sites.add(Site.objects.get_current())
        self.assertEqual(get_adapter().get_app(RequestFactory().get("/"), "github"), app)

    def test_app_bound_only_to_another_site_is_unavailable(self):
        app = SocialApp.objects.create(provider="github", name="GitHub", client_id="example")
        app.sites.add(Site.objects.create(domain="other.invalid", name="Other"))
        with self.assertRaises(Http404):
            get_adapter().get_app(RequestFactory().get("/"), "github")

    def test_ambiguous_configuration_is_not_hidden_as_a_missing_route(self):
        for client_id in ("first", "second"):
            app = SocialApp.objects.create(provider="github", name="GitHub", client_id=client_id)
            app.sites.add(Site.objects.get_current())
        with self.assertRaises(MultipleObjectsReturned):
            get_adapter().get_app(RequestFactory().get("/"), "github")
