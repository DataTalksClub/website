from html.parser import HTMLParser
from pathlib import Path

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import resolve, reverse

from accounts.studio_test_support import authenticated_studio_client, make_studio_user
from studio_courses.urls import ROUTE_DEFINITIONS
from courses.models import Course, RegistrationCampaign

REPO_ROOT = Path(__file__).resolve().parents[2]
CANONICAL_ROOT = "/studio/courses"
SLASH_ROOT = "/studio/courses/"


class _VisibleTextParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.text: list[str] = []

    def handle_data(self, data: str) -> None:
        value = data.strip()
        if value:
            self.text.append(value)


class StudioCourseRouteTests(TestCase):
    def setUp(self) -> None:
        self.operator = make_studio_user(
            username="course-operator",
            roles=("course_operator",),
        )

    def _route_kwargs(self, route: str) -> dict[str, object]:
        candidates: dict[str, object] = {
            "campaign_slug": "campaign",
            "complaint_id": 7,
            "course_slug": "course",
            "enrollment_id": 11,
            "homework_slug": "homework",
            "project_slug": "project",
            "submission_id": 13,
        }
        return {key: value for key, value in candidates.items() if f":{key}>" in route}

    def test_every_operation_has_a_logical_canonical_reverse_name(self) -> None:
        for route, _view, name in ROUTE_DEFINITIONS:
            kwargs = self._route_kwargs(route)
            canonical = reverse(f"studio_courses_{name}", kwargs=kwargs)

            with self.subTest(name=name):
                if name == "course_list":
                    self.assertEqual(canonical, CANONICAL_ROOT)
                else:
                    self.assertTrue(canonical.startswith(f"{CANONICAL_ROOT}/"))

    def test_literal_canonical_root_uses_safe_auth_boundary_without_404(self) -> None:
        for method in ("get", "head", "post"):
            with self.subTest(method=method):
                response = getattr(self.client, method)(CANONICAL_ROOT, {"probe": "body"})
                self.assertEqual(response.status_code, 302)
                self.assertNotEqual(response.status_code, 404)
                self.assertIn("/accounts/login/", response.headers["Location"])
                self.assertIn("next=/studio/courses", response.headers["Location"])

    def test_literal_canonical_root_renders_the_populated_operator_surface(self) -> None:
        course = Course.objects.create(
            slug="literal-root-course",
            title="Literal Root Course",
            description="Course proving the live canonical root contract",
        )
        self.client.force_login(self.operator)

        response = self.client.get(CANONICAL_ROOT)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, course.title)
        self.assertContains(response, "Studio")
        self.assertContains(response, "Courses")
        self.assertContains(response, 'href="/studio/courses"')
        self.assertEqual(self.client.head(CANONICAL_ROOT).status_code, 200)
        self.assertEqual(
            self.client.post(CANONICAL_ROOT, {"probe": "body"}).status_code,
            200,
        )

    def test_slash_root_redirects_authorized_requests_one_hop_with_exact_semantics(
        self,
    ) -> None:
        self.client.force_login(self.operator)
        query = "source=bookmark&value=a%2Fb+plus"
        destination = f"{CANONICAL_ROOT}?{query}"

        for method in ("get", "head"):
            with self.subTest(method=method):
                response = getattr(self.client, method)(f"{SLASH_ROOT}?{query}")
                self.assertEqual(response.status_code, 301)
                self.assertEqual(response.headers["Location"], destination)

        followed_get = self.client.get(f"{SLASH_ROOT}?{query}", follow=True)
        self.assertEqual(followed_get.redirect_chain, [(destination, 301)])
        self.assertEqual(followed_get.status_code, 200)
        self.assertEqual(followed_get.wsgi_request.path, CANONICAL_ROOT)

        post_response = self.client.post(
            f"{SLASH_ROOT}?{query}",
            {"probe": "preserved-body"},
        )
        self.assertEqual(post_response.status_code, 308)
        self.assertEqual(post_response.headers["Location"], destination)

        followed_post = self.client.post(
            f"{SLASH_ROOT}?{query}",
            {"probe": "preserved-body"},
            follow=True,
        )
        self.assertEqual(followed_post.redirect_chain, [(destination, 308)])
        self.assertEqual(followed_post.status_code, 200)
        self.assertEqual(followed_post.wsgi_request.method, "POST")
        self.assertEqual(followed_post.wsgi_request.POST["probe"], "preserved-body")

    def test_slash_root_keeps_anonymous_requests_at_the_safe_auth_boundary(self) -> None:
        for method in ("get", "head", "post"):
            with self.subTest(method=method):
                response = getattr(self.client, method)(SLASH_ROOT, {"probe": "body"})
                self.assertEqual(response.status_code, 302)
                self.assertIn("/accounts/login/", response.headers["Location"])
                self.assertNotEqual(response.headers["Location"], CANONICAL_ROOT)

    def test_literal_root_contract_is_independent_of_append_slash(self) -> None:
        self.client.force_login(self.operator)

        for append_slash in (False, True):
            with (
                self.subTest(append_slash=append_slash),
                override_settings(APPEND_SLASH=append_slash),
            ):
                self.assertEqual(self.client.get(CANONICAL_ROOT).status_code, 200)
                slash_response = self.client.get(SLASH_ROOT)
                self.assertEqual(slash_response.status_code, 301)
                self.assertEqual(slash_response.headers["Location"], CANONICAL_ROOT)

    def test_every_legacy_get_route_redirects_directly_and_preserves_query(self) -> None:
        self.client.force_login(self.operator)

        for route, _view, name in ROUTE_DEFINITIONS:
            kwargs = self._route_kwargs(route)
            legacy = reverse(f"legacy_studio_courses_{name}", kwargs=kwargs)
            canonical = reverse(f"studio_courses_{name}", kwargs=kwargs)

            with self.subTest(name=name):
                for method in ("get", "head"):
                    response = getattr(self.client, method)(f"{legacy}?source=bookmark")
                    self.assertEqual(response.status_code, 302)
                    self.assertEqual(
                        response.headers["Location"],
                        f"{canonical}?source=bookmark",
                    )

    def test_legacy_mutation_redirects_preserve_the_request_method(self) -> None:
        self.client.force_login(self.operator)
        kwargs = {"course_slug": "course", "homework_slug": "homework"}
        legacy = reverse("legacy_studio_courses_homework_score", kwargs=kwargs)
        canonical = reverse("studio_courses_homework_score", kwargs=kwargs)

        for method in ("post", "put", "patch", "delete"):
            with self.subTest(method=method):
                response = getattr(self.client, method)(
                    f"{legacy}?source=bookmark",
                    {"confirm": "yes"},
                )
                self.assertEqual(response.status_code, 307)
                self.assertEqual(
                    response.headers["Location"],
                    f"{canonical}?source=bookmark",
                )

    def test_legacy_post_replays_its_body_only_for_an_authorized_role(self) -> None:
        course = Course.objects.create(
            slug="replay-course",
            title="Replay Course",
            description="Course for compatibility replay characterization",
        )
        payload = {
            "title": "Replay campaign",
            "slug": "replay-campaign",
            "edition_label": "2026 cohort",
            "current_course": course.pk,
            "is_active": "on",
            "hero_image_url": "",
            "video_url": "",
            "meta_description": "Replay characterization",
            "marketing_markdown": "## Replayed body",
        }
        legacy = reverse("legacy_studio_courses_campaign_create")
        canonical = reverse("studio_courses_campaign_create")
        self.client.force_login(self.operator)

        response = self.client.post(
            f"{legacy}?source=bookmark",
            payload,
            follow=True,
        )

        self.assertEqual(
            response.redirect_chain[0],
            (f"{canonical}?source=bookmark", 307),
        )
        campaign = RegistrationCampaign.objects.get(slug="replay-campaign")
        self.assertEqual(campaign.marketing_markdown, "## Replayed body")
        self.assertEqual(response.status_code, 200)

    def test_legacy_root_without_a_slash_redirects_directly(self) -> None:
        self.client.force_login(self.operator)
        destination = f"{CANONICAL_ROOT}?source=bookmark"

        for legacy_root in ("/cadmin", "/cadmin/"):
            for method in ("get", "head"):
                with self.subTest(legacy_root=legacy_root, method=method):
                    response = getattr(self.client, method)(f"{legacy_root}?source=bookmark")
                    self.assertEqual(response.status_code, 302)
                    self.assertEqual(response.headers["Location"], destination)

            post_response = self.client.post(
                f"{legacy_root}?source=bookmark",
                {"probe": "legacy-body"},
            )
            self.assertEqual(post_response.status_code, 307)
            self.assertEqual(post_response.headers["Location"], destination)

            followed = self.client.get(
                f"{legacy_root}?source=bookmark",
                follow=True,
            )
            self.assertEqual(followed.redirect_chain, [(destination, 302)])
            self.assertEqual(followed.status_code, 200)

    def test_legacy_routes_do_not_expose_canonical_destinations_to_non_staff(self) -> None:
        legacy = reverse("legacy_studio_courses_course_list")
        canonical = reverse("studio_courses_course_list")

        for user in (
            None,
            get_user_model().objects.create_user(username="learner"),
        ):
            with self.subTest(user=user):
                if user is not None:
                    self.client.force_login(user)
                response = self.client.get(legacy)
                self.assertEqual(response.status_code, 302)
                self.assertNotEqual(response.headers["Location"], canonical)
                self.client.logout()

    def test_canonical_courses_require_staff_authentication(self) -> None:
        canonical = reverse("studio_courses_course_list")

        for user in (
            None,
            get_user_model().objects.create_user(username="canonical-learner"),
        ):
            with self.subTest(user=user):
                if user is not None:
                    self.client.force_login(user)
                response = self.client.get(canonical)
                self.assertEqual(response.status_code, 302)
                self.assertIn("/accounts/login/", response.headers["Location"])
                self.client.logout()

    def test_only_course_operator_and_site_admin_roles_can_use_course_operations(
        self,
    ) -> None:
        canonical_list = reverse("studio_courses_course_list")
        legacy_list = reverse("legacy_studio_courses_course_list")
        canonical_mutation = reverse("studio_courses_campaign_create")
        legacy_mutation = reverse("legacy_studio_courses_campaign_create")
        course = Course.objects.create(
            slug="role-matrix-course",
            title="Role Matrix Course",
            description="Course for authorization characterization",
        )

        for role in ("course_operator", "site_admin"):
            user = make_studio_user(username=f"allowed-{role}", roles=(role,))
            client = authenticated_studio_client(user)
            with self.subTest(role=role, access="allowed"):
                self.assertEqual(client.get(canonical_list).status_code, 200)
                for method in ("get", "head"):
                    response = getattr(client, method)(legacy_list)
                    self.assertEqual(response.status_code, 302)
                    self.assertEqual(response.headers["Location"], canonical_list)
                for method in ("post", "put", "patch", "delete"):
                    response = getattr(client, method)(
                        legacy_mutation,
                        {"probe": role},
                    )
                    self.assertEqual(response.status_code, 307)
                    self.assertEqual(
                        response.headers["Location"],
                        canonical_mutation,
                    )

        denied_users = [
            (
                role,
                make_studio_user(username=f"denied-{role}", roles=(role,)),
            )
            for role in (
                "content_operator",
                "event_operator",
                "email_operator",
                "support_operator",
                "auditor",
            )
        ]
        denied_users.extend(
            (
                ("unassigned_staff", make_studio_user(username="unassigned-staff")),
                (
                    "ungrouped_superuser",
                    get_user_model().objects.create_superuser(
                        username="ungrouped-superuser",
                        email="superuser@example.invalid",
                        password="unused-test-password",
                    ),
                ),
            )
        )

        for role, user in denied_users:
            client = authenticated_studio_client(user)
            valid_mutation_payload = {
                "title": f"Denied {role}",
                "slug": f"denied-{role}",
                "edition_label": "2026 cohort",
                "current_course": course.pk,
                "is_active": "on",
                "hero_image_url": "",
                "video_url": "",
                "meta_description": "Must not be created",
                "marketing_markdown": "Must not be stored",
            }
            with self.subTest(role=role, access="denied"):
                for method in ("get", "head", "post", "put", "patch", "delete"):
                    payload = valid_mutation_payload if method == "post" else {"probe": role}
                    canonical_response = getattr(client, method)(
                        canonical_mutation,
                        payload,
                    )
                    self.assertEqual(canonical_response.status_code, 302)
                    self.assertIn(
                        "/accounts/login/",
                        canonical_response.headers["Location"],
                    )

                    legacy_response = getattr(client, method)(
                        legacy_mutation,
                        payload,
                    )
                    self.assertEqual(legacy_response.status_code, 302)
                    self.assertIn(
                        "/accounts/login/",
                        legacy_response.headers["Location"],
                    )
                    self.assertNotIn(
                        "/studio/courses",
                        legacy_response.headers["Location"],
                    )

                self.assertEqual(client.get(canonical_list).status_code, 302)
                legacy_list_response = client.get(legacy_list)
                self.assertEqual(legacy_list_response.status_code, 302)
                self.assertNotIn(
                    "/studio/courses",
                    legacy_list_response.headers["Location"],
                )
                self.assertFalse(
                    RegistrationCampaign.objects.filter(
                        slug=valid_mutation_payload["slug"]
                    ).exists()
                )

        self.assertFalse(RegistrationCampaign.objects.exists())

    def test_wrong_role_cannot_discover_any_canonical_or_legacy_operation(
        self,
    ) -> None:
        wrong_role = make_studio_user(
            username="all-routes-content-operator",
            roles=("content_operator",),
        )
        client = authenticated_studio_client(wrong_role)

        for route, _view, name in ROUTE_DEFINITIONS:
            kwargs = self._route_kwargs(route)
            canonical = reverse(f"studio_courses_{name}", kwargs=kwargs)
            legacy = reverse(f"legacy_studio_courses_{name}", kwargs=kwargs)

            for method in ("get", "head", "post", "put", "patch", "delete"):
                with self.subTest(name=name, method=method):
                    canonical_response = getattr(client, method)(
                        canonical,
                        {"probe": "body"},
                    )
                    self.assertEqual(canonical_response.status_code, 302)
                    self.assertIn(
                        "/accounts/login/",
                        canonical_response.headers["Location"],
                    )

                    legacy_response = getattr(client, method)(
                        legacy,
                        {"probe": "body"},
                    )
                    self.assertEqual(legacy_response.status_code, 302)
                    self.assertIn(
                        "/accounts/login/",
                        legacy_response.headers["Location"],
                    )
                    self.assertNotIn(
                        "/studio/courses",
                        legacy_response.headers["Location"],
                    )

    def test_course_operator_reaches_courses_from_studio(self) -> None:
        operator = make_studio_user(
            username="navigation-course-operator",
            roles=("course_operator",),
        )
        client = authenticated_studio_client(operator)

        studio_response = client.get(reverse("studio:home"))

        self.assertEqual(studio_response.status_code, 200)
        self.assertEqual(reverse("studio_courses_course_list"), CANONICAL_ROOT)
        self.assertContains(
            studio_response,
            'href="/studio/courses"',
        )
        self.assertContains(studio_response, "Courses")

        courses_response = client.get(reverse("studio_courses_course_list"))
        self.assertEqual(courses_response.status_code, 200)
        self.assertContains(courses_response, "Studio")
        self.assertContains(courses_response, "Courses")
        self.assertIn("private", courses_response.headers["Cache-Control"])
        self.assertIn("no-store", courses_response.headers["Cache-Control"])

    def test_course_pages_have_no_user_facing_legacy_name(self) -> None:
        Course.objects.create(
            slug="test-course",
            title="Test Course",
            description="Test course description",
        )
        self.client.force_login(self.operator)

        for route_name, kwargs in (
            ("studio_courses_course_list", {}),
            ("studio_courses_course", {"course_slug": "test-course"}),
        ):
            with self.subTest(route_name=route_name):
                response = self.client.get(reverse(route_name, kwargs=kwargs))
                self.assertEqual(response.status_code, 200)
                parser = _VisibleTextParser()
                parser.feed(response.content.decode())
                visible_text = " ".join(parser.text).casefold()
                self.assertNotIn("cadmin", visible_text)
                self.assertNotIn("course admin", visible_text)

    def test_human_templates_do_not_use_legacy_product_names(self) -> None:
        template_roots = (
            REPO_ROOT / "accounts" / "templates",
            REPO_ROOT / "studio_courses" / "templates",
            REPO_ROOT / "course_platform_templates",
            REPO_ROOT / "courses" / "templates",
        )

        for template_root in template_roots:
            for template in template_root.rglob("*.html"):
                source = template.read_text(encoding="utf-8")
                folded = source.casefold()
                with self.subTest(template=template.relative_to(REPO_ROOT)):
                    self.assertGreater(len(source.splitlines()), 4)
                    self.assertNotIn("course admin", folded)
                    self.assertNotIn("course management", folded)

    def test_django_admin_remains_a_separate_surface(self) -> None:
        admin_path = reverse("admin:index")
        match = resolve(admin_path)

        self.assertEqual(admin_path, "/admin/")
        self.assertEqual(match.namespace, "admin")
        self.assertNotEqual(admin_path, reverse("studio_courses_course_list"))
