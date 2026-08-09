from django.test import TestCase
from django.urls import reverse


class AdoptedAPIHealthTests(TestCase):
    def test_health_returns_the_canonical_local_runtime_identity(self) -> None:
        response = self.client.get(reverse("api_health"))

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
