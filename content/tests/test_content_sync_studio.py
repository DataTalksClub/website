from __future__ import annotations

from community_base.content_sync.models import ContentSource
from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from accounts.studio_test_support import make_studio_user


class ContentSyncStudioViewTests(TestCase):
    """The D2.2a package studio surfaces render inside the site Studio shell.

    The package content-sync templates extend ``community_base/studio/base.html``
    while the package studio shell adoption is D2.1, so the site carries the
    sanctioned template override at ``templates/community_base/studio/base.html``
    (architecture extension point: template override by path). These views
    exercise every ``studio/content-sync/*`` route directly so a missing
    override or a broken mount fails this suite, not the browser gate.
    """

    # Declared at class level so ``setUpTestData`` assignment typechecks.
    content_source: ContentSource
    main_site_source: ContentSource

    @classmethod
    def setUpTestData(cls) -> None:
        # Fixture-specific slugs: the reference data seeds the real declared
        # sources for the catalogue's synced reads, and both the slug and the
        # repository name are unique.
        cls.content_source = ContentSource.objects.create(
            slug="studio-fixture-content",
            repo_name="DataTalksClub/studio-fixture-content",
            webhook_secret="test-webhook-secret",
            is_enabled=False,
        )
        cls.main_site_source = ContentSource.objects.create(
            slug="studio-fixture-main-site",
            repo_name="DataTalksClub/studio-fixture-main-site",
            webhook_secret="test-webhook-secret",
            is_enabled=False,
        )

    def _staff_client(self):
        user = make_studio_user(
            username="studio-staff",
            roles=("content_operator",),
        )
        self.client.force_login(user)
        return user

    def _studio_routes(self) -> dict[str, str]:
        return {
            "sources": reverse("community_base_content_sources"),
            "history": reverse("community_base_content_sync_history"),
            "worker": reverse("community_base_content_sync_worker"),
            "edit-content": reverse(
                "community_base_content_source_edit", args=(self.content_source.id,)
            ),
            "edit-main-site": reverse(
                "community_base_content_source_edit", args=(self.main_site_source.id,)
            ),
        }

    def test_sources_list_renders_in_site_studio_shell(self) -> None:
        self._staff_client()
        response = self.client.get(reverse("community_base_content_sources"))
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "community_base/content_sync/sources.html")
        self.assertTemplateUsed(response, "community_base/studio/base.html")
        self.assertContains(response, 'class="no-js"')
        self.assertContains(response, "Content sources")
        self.assertContains(response, "DataTalksClub/studio-fixture-content")
        self.assertContains(response, "DataTalksClub/studio-fixture-main-site")

    def test_history_renders(self) -> None:
        self._staff_client()
        response = self.client.get(reverse("community_base_content_sync_history"))
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "community_base/content_sync/history.html")
        self.assertContains(response, "Content sync history")
        self.assertContains(response, 'class="no-js"')

    def test_worker_renders(self) -> None:
        self._staff_client()
        response = self.client.get(reverse("community_base_content_sync_worker"))
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "community_base/content_sync/worker.html")
        self.assertContains(response, "Content sync worker")
        self.assertContains(response, 'class="no-js"')

    def test_both_source_edit_pages_render(self) -> None:
        self._staff_client()
        for source in (self.content_source, self.main_site_source):
            with self.subTest(source=source.slug):
                response = self.client.get(
                    reverse("community_base_content_source_edit", args=(source.id,))
                )
                self.assertEqual(response.status_code, 200)
                self.assertTemplateUsed(response, "community_base/content_sync/source_form.html")
                self.assertContains(response, f"Edit {source.slug}")
                self.assertContains(response, 'class="no-js"')

    def test_source_sync_without_enable_redirects_without_queueing(self) -> None:
        self._staff_client()
        response = self.client.post(
            reverse("community_base_content_source_sync", args=(self.content_source.id,)),
            {},
        )
        self.assertRedirects(response, reverse("community_base_content_sources"))
        self.content_source.refresh_from_db()
        self.assertEqual(self.content_source.last_sync_status, "")

    def test_anonymous_is_redirected_to_login(self) -> None:
        for name, path in self._studio_routes().items():
            with self.subTest(route=name):
                response = self.client.get(path)
                self.assertEqual(response.status_code, 302)

    def test_non_staff_is_forbidden(self) -> None:
        user = get_user_model().objects.create_user(username="learner")
        self.client.force_login(user)
        for name, path in self._studio_routes().items():
            with self.subTest(route=name):
                response = self.client.get(path)
                self.assertEqual(response.status_code, 403)
