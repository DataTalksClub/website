from unittest.mock import Mock, patch

from django.test import TestCase, override_settings
from django.urls import reverse


class HealthTests(TestCase):
    @override_settings(
        ALLOWED_HOSTS=["web.dtcdev.click"],
        NOINDEX=True,
        CANONICAL_ORIGIN="https://datatalks.club",
    )
    def test_development_hostname_is_allowed_noindex_and_uses_production_canonical(self) -> None:
        response = self.client.get("/", headers={"host": "web.dtcdev.click"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["X-Robots-Tag"], "noindex, nofollow")
        self.assertContains(response, '<link rel="canonical" href="https://datatalks.club/">')

    def test_liveness_does_not_call_database(self) -> None:
        with patch("core.views.connection.cursor") as cursor:
            response = self.client.get(reverse("health-live"))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "ok")
        cursor.assert_not_called()

    def test_readiness_succeeds_when_database_and_migrations_are_healthy(self) -> None:
        response = self.client.get(reverse("health-ready"))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "ready")

    def test_readiness_fails_safely_when_database_is_unavailable(self) -> None:
        with patch("core.views.connection.cursor", side_effect=RuntimeError("secret detail")):
            response = self.client.get(reverse("health-ready"))
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json()["checks"]["database"]["message"], "database unavailable")
        self.assertNotContains(response, "secret detail", status_code=503)

    def test_readiness_fails_when_migrations_are_unapplied(self) -> None:
        executor = Mock()
        executor.loader.graph.leaf_nodes.return_value = [("accounts", "0001_initial")]
        executor.migration_plan.return_value = [("pending", False)]
        with patch("core.views.MigrationExecutor", return_value=executor):
            response = self.client.get(reverse("health-ready"))
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json()["checks"]["migrations"]["message"], "unapplied migrations")

    @override_settings(REQUIRED_BOOTSTRAP_SETTINGS=("NOT_CONFIGURED",))
    def test_readiness_fails_when_bootstrap_configuration_is_missing(self) -> None:
        response = self.client.get(reverse("health-ready"))
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json()["checks"]["configuration"]["missing"], ["NOT_CONFIGURED"])

    def test_liveness_is_noindex_in_test_environment(self) -> None:
        response = self.client.get(reverse("health-live"))
        self.assertEqual(response.headers["X-Robots-Tag"], "noindex, nofollow")
        self.assertIn("X-Request-ID", response.headers)
