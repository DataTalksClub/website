"""Checked target expectations owned by the compatibility adapters.

Source and production captures remain immutable evidence.  This module contains the small,
independently reviewed target contract for the public Slack URL cutover; the manifest builder
uses it to regenerate the digest-bound expectation sidecar without modifying those captures.
"""

from __future__ import annotations

from compatibility.expectations import (
    ApprovedExpectation,
    ApprovedExpectationSet,
    ExpectedResponse,
    QueryPolicy,
)
from compatibility.models import PageMetadata, Reference, ReferenceKind, SitemapState

SLACK_LEGACY_URL = "https://datatalks.club/slack.html"
SLACK_CANONICAL_URL = "https://datatalks.club/slack"


def slack_target_expectation_set(
    *,
    manifest_sha256: str,
    differences_sha256: str,
    public_contracts_sha256: str,
) -> ApprovedExpectationSet:
    """Build the reviewed one-hop Slack alias expectation bound to artifact digests."""

    response = ExpectedResponse(
        status=200,
        final_url=SLACK_CANONICAL_URL,
        content_type="text/html",
        response_robots=("nofollow", "noindex"),
        metadata=PageMetadata(
            title="Join our Slack — DataTalks.Club",
            description=(
                "Join our community of data science professionals, machine learning engineers, "
                "and AI practitioners on Slack."
            ),
            first_heading="Join our Slack",
            language="en",
            canonical_url=SLACK_CANONICAL_URL,
            social_metadata=(
                (
                    "og:description",
                    "Join our community of data science professionals, machine learning engineers, "
                    "and AI practitioners on Slack.",
                ),
                ("og:site_name", "DataTalks.Club"),
                ("og:title", "Join our Slack — DataTalks.Club"),
                ("og:type", "website"),
                ("og:url", SLACK_CANONICAL_URL),
                ("twitter:card", "summary"),
                (
                    "twitter:description",
                    "Join our community of data science professionals, machine learning engineers, "
                    "and AI practitioners on Slack.",
                ),
                ("twitter:site", "@DataTalksClub"),
                ("twitter:title", "Join our Slack — DataTalks.Club"),
            ),
            fragments=(
                "analytics-preferences-description",
                "analytics-preferences-dialog",
                "analytics-preferences-status",
                "analytics-preferences-title",
                "content",
                "dark-mode-toggle",
                "header-container",
                "main-content",
                "site-navigation-links",
            ),
            references=(
                Reference(ReferenceKind.ASSET, "https://datatalks.club/static/alerts.js"),
                Reference(
                    ReferenceKind.ASSET,
                    "https://datatalks.club/static/core/accessibility.css",
                ),
                Reference(
                    ReferenceKind.ASSET,
                    "https://datatalks.club/static/core/accessibility.js",
                ),
                Reference(
                    ReferenceKind.ASSET,
                    "https://datatalks.club/static/core/analytics_preferences.js",
                ),
                Reference(
                    ReferenceKind.ASSET,
                    "https://datatalks.club/static/core/site_navigation.js",
                ),
                Reference(
                    ReferenceKind.ASSET,
                    "https://datatalks.club/static/core/site_shell.css",
                ),
                Reference(
                    ReferenceKind.ASSET,
                    "https://datatalks.club/static/core/vendor/fontawesome-free-5.15.1/css/all.min.css",
                ),
                Reference(
                    ReferenceKind.ASSET,
                    "https://datatalks.club/static/core/vendor/tailwindcss-3.4.17/tailwindcss-3.4.17.js",
                ),
                Reference(ReferenceKind.ASSET, "https://datatalks.club/static/courses.css"),
                Reference(ReferenceKind.ASSET, "https://datatalks.club/static/dark_mode.js"),
                Reference(ReferenceKind.ASSET, "https://datatalks.club/static/local_date.js"),
                Reference(ReferenceKind.ASSET, "https://datatalks.club/static/time_left.js"),
                Reference(
                    ReferenceKind.ASSET,
                    "https://datatalks.club/static/timezone_preference.js",
                ),
                Reference(ReferenceKind.ASSET, "https://datatalks.club/static/user_menu.js"),
                Reference(
                    ReferenceKind.EXTERNAL_LINK,
                    "https://airtable.com/shrhUBN51Jy10fjJq",
                ),
                Reference(
                    ReferenceKind.EXTERNAL_LINK,
                    "https://github.com/DataTalksClub/website",
                ),
                Reference(ReferenceKind.INTERNAL_LINK, "https://datatalks.club/"),
                Reference(
                    ReferenceKind.INTERNAL_LINK,
                    "https://datatalks.club/accounts/login/?next=%2Fslack",
                ),
                Reference(ReferenceKind.INTERNAL_LINK, "https://datatalks.club/blog"),
                Reference(ReferenceKind.INTERNAL_LINK, "https://datatalks.club/books"),
                Reference(ReferenceKind.INTERNAL_LINK, "https://datatalks.club/courses"),
                Reference(ReferenceKind.INTERNAL_LINK, "https://datatalks.club/docs"),
                Reference(ReferenceKind.INTERNAL_LINK, "https://datatalks.club/events"),
                Reference(ReferenceKind.INTERNAL_LINK, "https://datatalks.club/faq"),
                Reference(ReferenceKind.INTERNAL_LINK, "https://datatalks.club/impressum"),
                Reference(ReferenceKind.INTERNAL_LINK, "https://datatalks.club/podcast"),
                Reference(ReferenceKind.INTERNAL_LINK, "https://datatalks.club/privacy"),
                Reference(ReferenceKind.INTERNAL_LINK, SLACK_CANONICAL_URL),
                Reference(ReferenceKind.INTERNAL_LINK, f"{SLACK_CANONICAL_URL}#main-content"),
                Reference(ReferenceKind.INTERNAL_LINK, "https://datatalks.club/terms"),
                Reference(ReferenceKind.INTERNAL_LINK, "https://datatalks.club/wiki"),
            ),
            main_content_fingerprint=(
                "6f66a809801951fc32ebe111ee3a955c2275d9b223c8ac2d4c4bbf97550846fa"
            ),
        ),
        sitemap=SitemapState(),
    )
    expectation = ApprovedExpectation.redirect(
        source_scope="main",
        public_url=SLACK_LEGACY_URL,
        redirect_status=301,
        redirect_target=SLACK_CANONICAL_URL,
        query_policy=QueryPolicy.PRESERVE_RAW,
        expected_response=response,
        owner="content",
        reason="The canonical public Slack page is /slack; /slack.html is a legacy alias.",
        test_reference=(
            "content/tests/test_canonical_routes.py::"
            "CanonicalRouteTests::test_slack_route_and_alias_have_exact_method_and_query_contracts"
        ),
    )
    return ApprovedExpectationSet.create(
        manifest_sha256=manifest_sha256,
        differences_sha256=differences_sha256,
        public_contracts_sha256=public_contracts_sha256,
        expectations=(expectation,),
    )
