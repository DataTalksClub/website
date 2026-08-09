from unittest.mock import Mock, patch

from django.test import TestCase, override_settings
from django.urls import reverse

ALB_READINESS_SECURITY_SETTINGS = {
    "ALLOWED_HOSTS": ["web.dtcdev.click"],
    "SECURE_SSL_REDIRECT": True,
    "SECURE_PROXY_SSL_HEADER": ("HTTP_X_FORWARDED_PROTO", "https"),
    "SECURE_REDIRECT_EXEMPT": [r"^health/ready$"],
}


class HealthTests(TestCase):
    @override_settings(
        ALLOWED_HOSTS=["web.dtcdev.click"],
        NOINDEX=True,
        CANONICAL_ORIGIN="https://datatalks.club",
    )
    def test_development_hostname_is_allowed_noindex_and_uses_explicit_canonical(self) -> None:
        response = self.client.get("/unified/", headers={"host": "web.dtcdev.click"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["X-Robots-Tag"], "noindex, nofollow")
        self.assertContains(response, '<link rel="canonical" href="https://datatalks.club/">')

    def test_liveness_does_not_call_database(self) -> None:
        with patch("core.views.connection.ensure_connection") as ensure_connection:
            response = self.client.get(reverse("health-live"))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json(),
            {
                "status": "ok",
                "version": "local-development-build-version-not-configured",
                "source_sha": None,
                "image_digest": None,
            },
        )
        ensure_connection.assert_not_called()

    def test_readiness_succeeds_when_database_and_migrations_are_healthy(self) -> None:
        response = self.client.get(reverse("health-ready"))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "ready")
        self.assertEqual(
            {name: response.json()[name] for name in ("version", "source_sha", "image_digest")},
            {
                "version": "local-development-build-version-not-configured",
                "source_sha": None,
                "image_digest": None,
            },
        )

    @override_settings(**ALB_READINESS_SECURITY_SETTINGS)
    def test_readiness_accepts_direct_http_alb_probe_with_private_target_host(self) -> None:
        response = self.client.get(
            reverse("health-ready"),
            headers={"host": "10.0.0.10:8000"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "ready")
        self.assertNotIn("Location", response.headers)

    @override_settings(**ALB_READINESS_SECURITY_SETTINGS)
    def test_readiness_alb_probe_preserves_dependency_failure(self) -> None:
        with patch(
            "core.views.connection.ensure_connection",
            side_effect=RuntimeError("secret detail"),
        ):
            response = self.client.get(
                reverse("health-ready"),
                headers={"host": "10.0.0.10:8000"},
            )

        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json()["status"], "not_ready")
        self.assertEqual(response.json()["checks"]["database"]["message"], "database unavailable")
        self.assertNotContains(response, "secret detail", status_code=503)

    @override_settings(**ALB_READINESS_SECURITY_SETTINGS)
    def test_readiness_redirect_exemption_is_exact(self) -> None:
        for path in ("/", reverse("health-live"), f"{reverse('health-ready')}/"):
            with self.subTest(path=path):
                response = self.client.get(
                    path,
                    headers={
                        "host": "web.dtcdev.click",
                        "x-forwarded-proto": "http",
                    },
                )
                self.assertEqual(response.status_code, 301)
                self.assertEqual(response.headers["Location"], f"https://web.dtcdev.click{path}")

    @override_settings(**ALB_READINESS_SECURITY_SETTINGS)
    def test_unrelated_hosts_are_not_allowed_outside_exact_readiness_path(self) -> None:
        for host in ("10.0.0.10:8000", "unrelated.invalid"):
            for path in (
                "/",
                "/ping",
                "/ping-extra",
                reverse("health-live"),
                f"{reverse('health-ready')}/",
            ):
                with self.subTest(host=host, path=path):
                    response = self.client.get(path, headers={"host": host})
                    self.assertEqual(response.status_code, 400)
                    self.assertEqual(response.headers["X-Robots-Tag"], "noindex, nofollow")

    @override_settings(**ALB_READINESS_SECURITY_SETTINGS)
    def test_public_https_health_and_page_requests_do_not_redirect(self) -> None:
        for path in ("/", reverse("health-live"), reverse("health-ready")):
            with self.subTest(path=path):
                response = self.client.get(
                    path,
                    headers={
                        "host": "web.dtcdev.click",
                        "x-forwarded-proto": "https",
                    },
                )
                self.assertEqual(response.status_code, 200)
                self.assertNotIn("Location", response.headers)

    def test_readiness_fails_safely_when_database_is_unavailable(self) -> None:
        with patch(
            "core.views.connection.ensure_connection",
            side_effect=RuntimeError("secret detail"),
        ):
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
