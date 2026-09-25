from __future__ import annotations

from datetime import timedelta

from django.db.models import Case, DateTimeField, Value, When
from django.test import Client
from django.utils import timezone

from accounts.models import Token, User
from accounts.studio_roles import set_single_studio_role
from api.openapi.spec import build_openapi_spec
from api.views.registration_campaign_registrations import count_by_company
from courses.models import CourseRegistration, RegistrationCampaign
from management_auth.models import APIPrincipal
from management_auth.services import create_principal

from .registration_campaign_base import RegistrationCampaignAPITestBase
from .test_staff_authority_boundary import make_credential

REGISTRATION_COUNT_REF = {"$ref": "#/components/schemas/RegistrationCount"}
COMPANY_DESCRIPTION_MARKERS = (
    "stripped",
    "case preserved",
    "whole campaign",
)


class _CompanyNames:
    def __init__(self, names):
        self.names = names

    def values_list(self, field, flat):
        self.field = field
        self.flat = flat
        return self

    def iterator(self, chunk_size=None):
        return iter(self.names)


class RegistrationCampaignCompanyStatsTests(RegistrationCampaignAPITestBase):
    def add_registration(self, campaign, email, company_name, **overrides):
        fields = {
            "campaign": campaign,
            "course": self.course,
            "email": email,
            "name": "Student",
            "company_name": company_name,
            "country": "Germany",
            "region": "Europe",
            "role": CourseRegistration.Role.OTHER,
            "accepted_newsletter": False,
        }
        fields.update(overrides)
        return CourseRegistration.objects.create(**fields)

    def stored_companies(self, campaign):
        return list(
            CourseRegistration.objects.filter(campaign=campaign)
            .order_by("id")
            .values_list("company_name", flat=True)
        )

    def test_by_company_shape_normalization_order_and_filters(self):
        campaign = self.create_campaign()
        stored = [
            " Acme ",
            "\tAcme\n",
            "\u00a0Acme\u00a0",
            "acme",
            "ACME",
            "Acme Data",
            "Acme  Data",
            "",
            "   ",
            "\t \n",
        ]
        for index, company_name in enumerate(stored):
            overrides = {}
            if index == 0:
                overrides = {
                    "name": "Needle Person",
                    "role": CourseRegistration.Role.DATA_ENGINEER,
                    "country": "France",
                    "region": "Americas",
                }
            self.add_registration(
                campaign,
                f"student-{index}@example.com",
                company_name,
                **overrides,
            )
        other_campaign = RegistrationCampaign.objects.create(
            slug="other-campaign",
            title="Other Campaign",
            current_course=self.course,
        )
        self.add_registration(
            other_campaign,
            "elsewhere@example.com",
            "Elsewhere Inc",
        )

        response = self.client.get(self.campaign_registrations_url())

        self.assertEqual(response.status_code, 200)
        data = response.json()
        by_company = data["stats"]["by_company"]
        expected = [
            {"value": "", "count": 3},
            {"value": "Acme", "count": 3},
            {"value": "ACME", "count": 1},
            {"value": "Acme  Data", "count": 1},
            {"value": "Acme Data", "count": 1},
            {"value": "acme", "count": 1},
        ]
        self.assertEqual(by_company, expected)
        for entry in by_company:
            self.assertEqual(set(entry), {"value", "count"})
            self.assertIsInstance(entry["value"], str)
            self.assertIsInstance(entry["count"], int)
        self.assertEqual(sum(entry["count"] for entry in by_company), 10)
        self.assertEqual(data["stats"]["total"], 10)
        self.assertEqual(
            CourseRegistration.objects.filter(campaign=campaign).count(),
            10,
        )
        self.assertNotIn("Elsewhere Inc", [entry["value"] for entry in by_company])
        self.assertEqual(self.stored_companies(campaign), stored)
        returned_companies = [row["company_name"] for row in data["registrations"]]
        self.assertCountEqual(returned_companies, stored)

        unfiltered_stats = data["stats"]
        queries = (
            "?q=Acme",
            "?q=Needle",
            "?role=data_engineer",
            "?country=France",
            "?region=Americas",
        )
        expected_row_counts = (0, 1, 1, 1, 1)
        for query, row_count in zip(queries, expected_row_counts, strict=True):
            filtered = self.client.get(self.campaign_registrations_url() + query)
            self.assertEqual(filtered.status_code, 200)
            filtered_data = filtered.json()
            self.assertEqual(len(filtered_data["registrations"]), row_count)
            self.assertEqual(filtered_data["stats"], unfiltered_stats)
        self.assertEqual(self.stored_companies(campaign), stored)

    def test_role_country_and_region_keep_stored_grouping(self):
        campaign = self.create_campaign()
        self.add_registration(
            campaign,
            "spaced@example.com",
            "  Acme  ",
            role=" data_engineer",
            country=" Germany",
            region="Europe ",
        )
        self.add_registration(
            campaign,
            "plain@example.com",
            "Acme",
            role=CourseRegistration.Role.OTHER,
            country="Germany",
            region="Europe",
        )

        response = self.client.get(self.campaign_registrations_url())

        self.assertEqual(response.status_code, 200)
        stats = response.json()["stats"]
        self.assertEqual(stats["by_company"], [{"value": "Acme", "count": 2}])
        self.assertEqual(
            stats["by_role"],
            [
                {"value": " data_engineer", "count": 1},
                {"value": "other", "count": 1},
            ],
        )
        self.assertEqual(
            stats["by_country"],
            [
                {"value": " Germany", "count": 1},
                {"value": "Germany", "count": 1},
            ],
        )
        self.assertEqual(
            stats["by_region"],
            [
                {"value": "Europe", "count": 1},
                {"value": "Europe ", "count": 1},
            ],
        )
        self.assertEqual(
            self.stored_companies(campaign),
            ["  Acme  ", "Acme"],
        )

    def test_company_buckets_include_rows_past_the_registration_cap(self):
        campaign = self.create_campaign()
        total = 501
        rows = []
        for index in range(total):
            email = f"student-{index}@example.com"
            company_name = "Only Oldest" if index == 0 else f"Company {index:04d}"
            rows.append(
                CourseRegistration(
                    campaign=campaign,
                    course=self.course,
                    email=email,
                    email_normalized=email,
                    name="Student",
                    company_name=company_name,
                    country="Germany",
                    region="Europe",
                    role=CourseRegistration.Role.OTHER,
                )
            )
        created = CourseRegistration.objects.bulk_create(rows)
        base = timezone.now()
        created_at = Case(
            *[
                When(
                    pk=registration.pk,
                    then=Value(
                        base - timedelta(seconds=total - index),
                        output_field=DateTimeField(),
                    ),
                )
                for index, registration in enumerate(created)
            ],
            output_field=DateTimeField(),
        )
        CourseRegistration.objects.filter(campaign=campaign).update(created_at=created_at)
        other_campaign = RegistrationCampaign.objects.create(
            slug="other-campaign",
            title="Other Campaign",
            current_course=self.course,
        )
        self.add_registration(
            other_campaign,
            "elsewhere@example.com",
            "Elsewhere Inc",
        )

        default_response = self.client.get(self.campaign_registrations_url())
        capped_response = self.client.get(self.campaign_registrations_url() + "?limit=500")
        above_cap_response = self.client.get(self.campaign_registrations_url() + "?limit=999")

        self.assertEqual(default_response.status_code, 200)
        self.assertEqual(capped_response.status_code, 200)
        self.assertEqual(above_cap_response.status_code, 200)
        for response, row_count in (
            (default_response, 100),
            (capped_response, 500),
            (above_cap_response, 500),
        ):
            data = response.json()
            by_company = data["stats"]["by_company"]
            returned = [row["company_name"] for row in data["registrations"]]
            self.assertEqual(len(data["registrations"]), row_count)
            self.assertEqual(data["stats"]["total"], total)
            self.assertEqual(len(by_company), total)
            self.assertEqual(sum(entry["count"] for entry in by_company), total)
            self.assertEqual(
                CourseRegistration.objects.filter(campaign=campaign).count(),
                total,
            )
            self.assertNotIn("Only Oldest", returned)
            self.assertIn({"value": "Only Oldest", "count": 1}, by_company)
            self.assertNotIn("Elsewhere Inc", [entry["value"] for entry in by_company])
            self.assertEqual(by_company[0]["value"], "Company 0001")
            self.assertEqual(by_company[-1]["value"], "Only Oldest")
            for entry in by_company:
                self.assertEqual(set(entry), {"value", "count"})

    def test_null_company_name_joins_the_blank_bucket(self):
        buckets = count_by_company(_CompanyNames([None, "  ", "\tAcme\n"]))

        self.assertEqual(
            buckets,
            [
                {"value": "", "count": 2},
                {"value": "Acme", "count": 1},
            ],
        )

    def test_invalid_limit_and_unknown_campaign_stay_unchanged(self):
        campaign = self.create_campaign()
        self.add_registration(campaign, "student@example.com", "Acme Data")

        invalid = self.client.get(self.campaign_registrations_url() + "?limit=nope")

        self.assertEqual(invalid.status_code, 400)
        self.assertEqual(
            invalid.json(),
            {
                "error": "limit must be an integer",
                "code": "invalid_limit",
                "details": {"field": "limit"},
            },
        )

        missing = self.client.get("/api/registration-campaigns/missing-campaign/registrations/")

        self.assertEqual(missing.status_code, 404)
        self.assertNotIn("application/json", missing["Content-Type"])
        self.assertNotIn(b"by_company", missing.content)
        self.assertNotIn(b'"code"', missing.content)

    def test_denials_omit_registration_rows_and_company_counts(self):
        campaign = self.create_campaign()
        self.add_registration(
            campaign,
            "student@example.com",
            "Secret Company",
        )
        non_staff = User.objects.create(
            username="campaign-nonstaff",
            email="campaign-nonstaff@example.com",
        )
        non_staff_token = Token.objects.create(user=non_staff)
        scoped_user = User.objects.create(
            username="campaign-no-scope",
            email="campaign-no-scope@example.com",
            is_staff=True,
        )
        set_single_studio_role(scoped_user, "course_operator")
        scoped_principal = create_principal(
            kind=APIPrincipal.Kind.HUMAN,
            name="company-stats:no-scope",
            identity_snapshot="company-stats:no-scope",
            user=scoped_user,
        )
        unscoped_role = make_credential(scoped_principal, scopes=["studio.home.read"])
        no_studio_user = User.objects.create(
            username="campaign-no-studio",
            email="campaign-no-studio@example.com",
            is_staff=True,
        )
        no_studio_principal = create_principal(
            kind=APIPrincipal.Kind.HUMAN,
            name="company-stats:no-studio",
            identity_snapshot="company-stats:no-studio",
            user=no_studio_user,
        )
        no_studio = make_credential(
            no_studio_principal,
            scopes=["compatibility.course_operations"],
        )
        clients = (
            (Client(), 401, {"error": "Authentication token required"}),
            (
                Client(HTTP_AUTHORIZATION="Token not-a-real-token"),
                401,
                {"error": "Invalid token"},
            ),
            (
                Client(HTTP_AUTHORIZATION="Bearer not-a-real-token"),
                401,
                {"error": "Invalid token"},
            ),
            (
                Client(HTTP_AUTHORIZATION=f"Token {non_staff_token.key}"),
                403,
                {"error": "Staff token required", "code": "staff_token_required"},
            ),
            (
                Client(HTTP_AUTHORIZATION=unscoped_role),
                403,
                {"error": "Staff token required", "code": "staff_token_required"},
            ),
            (
                Client(HTTP_AUTHORIZATION=no_studio),
                403,
                {"error": "Staff token required", "code": "staff_token_required"},
            ),
        )
        urls = (
            self.campaign_registrations_url(),
            "/api/registration-campaigns/missing-campaign/registrations/",
        )

        for client, status, body in clients:
            for url in urls:
                response = client.get(url)
                self.assertEqual(response.status_code, status)
                payload = response.json()
                self.assertEqual(payload, body)
                self.assertNotIn("stats", payload)
                self.assertNotIn("registrations", payload)
                self.assertNotIn("by_company", response.content.decode())
                self.assertNotIn("Secret Company", response.content.decode())

    def test_openapi_registration_stats_include_by_company(self):
        spec = build_openapi_spec()
        by_company = spec["components"]["schemas"]["RegistrationStats"]["properties"]["by_company"]

        self.assertEqual(by_company["type"], "array")
        self.assertEqual(by_company["items"], REGISTRATION_COUNT_REF)
        for marker in COMPANY_DESCRIPTION_MARKERS:
            self.assertIn(marker, by_company["description"])
        self.assertNotIn(
            "description",
            spec["components"]["schemas"]["RegistrationStats"]["properties"]["by_role"],
        )

        response = self.client.get("/api/openapi.json")

        self.assertEqual(response.status_code, 200)
        live = response.json()["components"]["schemas"]["RegistrationStats"]["properties"][
            "by_company"
        ]
        self.assertEqual(live, by_company)
        self.assertIn(
            "/api/registration-campaigns/{campaign_slug}/registrations/",
            response.json()["paths"],
        )
