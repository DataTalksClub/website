"""The versioned route-cache registry and the shareable-response boundary.

Spec 02's route-cache contract: one code-owned registry classifies every
resolved Django view, unlisted views are private/disabled, and only eligible
clean ``GET``/``HEAD`` responses of registered public routes may keep
shareable caching.  The runtime half is ``core.middleware``'s final veto, so a
future cacheable view cannot reintroduce a public directive the registry never
reviewed.

``/static/`` has no Django view — WhiteNoise answers it before URL resolution —
so the fingerprinted-static class stays owned by the edge policy, not this
registry.  Deployed TTLs remain pinned to zero by the development SEO policy;
this file changes what an origin response may *declare*, not any TTL.
"""

from __future__ import annotations

from urllib.parse import parse_qsl

ROUTE_CACHE_POLICY_VERSION = 1

# Route classes, named after spec 02's "Route cache and canonical contract" table.
STABLE_RELEASE_ASSET = "stable_release_asset"
EDITORIAL_DETAIL = "editorial_detail"
PUBLIC_HUB_FEED = "public_hub_feed_sitemap"
PUBLIC_CATALOG_DETAIL = "public_catalog_detail"
PERMANENT_REDIRECT = "code_owned_permanent_redirect"
PUBLIC_NOT_FOUND = "public_not_found"
PRIVATE_DYNAMIC = "private_dynamic"
OPERATIONAL = "operational"

#: Classes whose clean responses may keep the cache directives their view set.
#: Search surfaces are expressed through the query allowlist below (the wiki
#: hub's ``q`` switch), because search rides a classified hub view rather than
#: its own route.  ``PUBLIC_NOT_FOUND`` has no view members: a 404 response is
#: classified by status so unknown paths and unknown detail slugs share the
#: reviewed clean-404 class.
SHAREABLE_CACHE_CLASSES = frozenset(
    {
        STABLE_RELEASE_ASSET,
        EDITORIAL_DETAIL,
        PUBLIC_HUB_FEED,
        PUBLIC_CATALOG_DETAIL,
        PERMANENT_REDIRECT,
        PUBLIC_NOT_FOUND,
    }
)

#: Per-view query grammars for shareable views — the reviewed selectors only.
#: A shareable view absent from this mapping admits no query at all (its
#: canonical path is the whole contract).  ``PERMANENT_REDIRECT`` views need no
#: entry: their class carries the redirect query contract (ordinary keys are
#: preserved by the redirect itself, credential-shaped keys are refused
#: upstream by the sensitive-query veto).
QUERY_ALLOWLISTS: dict[str, frozenset[str]] = {
    "content.public_views.collection_hub": frozenset({"page"}),
    # `filter=past` is the events hub's single registered selector; the view
    # itself rejects every other combination.
    "content.public_views.events": frozenset({"filter"}),
    "content.public_views.events_past": frozenset({"page"}),
    "content.public_views.podcast_hub": frozenset({"season"}),
    # `q` is deliberately unlisted: a search request is the spec's search
    # surface and must never be shared, while `page` keeps the catalogue.
    "content.public_views.wiki_hub": frozenset({"page"}),
}

#: Every resolved Django view, classified.  Views join the registry at review
#: time — a new view without an entry fails ``test_every_resolved_view_is_
#: classified`` — and the private/dynamic block is the explicit, reviewed
#: complement of the shareable classes above, not a default.
ROUTE_CACHE_CLASSES: dict[str, str] = {
    # -- Public: stable release assets (invalidate on activation; PUB-07 owns
    # the durable invalidation intent).
    "content.public_views.media": STABLE_RELEASE_ASSET,
    "content.public_views.wiki_asset": STABLE_RELEASE_ASSET,
    "content.review_views.docs_asset": STABLE_RELEASE_ASSET,
    "content.review_views.faq_asset": STABLE_RELEASE_ASSET,
    "courses.views.shared_course_assets.shared_course_asset": STABLE_RELEASE_ASSET,
    # -- Public: editorial detail (canonical path, no query).
    "content.legal_views.impressum": EDITORIAL_DETAIL,
    "content.legal_views.privacy": EDITORIAL_DETAIL,
    "content.legal_views.terms": EDITORIAL_DETAIL,
    "content.public_views.article_detail": EDITORIAL_DETAIL,
    "content.public_views.book_detail": EDITORIAL_DETAIL,
    "content.public_views.person_detail": EDITORIAL_DETAIL,
    "content.public_views.podcast_detail_by_id": EDITORIAL_DETAIL,
    "content.public_views.podcast_detail_by_id_without_slug": EDITORIAL_DETAIL,
    "content.public_views.wiki_detail": EDITORIAL_DETAIL,
    "content.public_views.wiki_graph": EDITORIAL_DETAIL,
    "content.public_views.wiki_special": EDITORIAL_DETAIL,
    "content.review_views.docs_home": EDITORIAL_DETAIL,
    "content.review_views.docs_page": EDITORIAL_DETAIL,
    "content.review_views.faq_course": EDITORIAL_DETAIL,
    "content.review_views.faq_home": EDITORIAL_DETAIL,
    "content.review_views.slack": EDITORIAL_DETAIL,
    "core.views.sponsors": EDITORIAL_DETAIL,
    "core.views.tour": EDITORIAL_DETAIL,
    "courses.views.course.course_family_view": EDITORIAL_DETAIL,
    # -- Public: hubs, feeds, sitemaps (registered selectors only, via
    # QUERY_ALLOWLISTS).
    "content.public_views.collection_hub": PUBLIC_HUB_FEED,
    "content.public_views.event_detail": PUBLIC_HUB_FEED,
    "content.public_views.event_detail_without_slug": PUBLIC_HUB_FEED,
    "content.public_views.events": PUBLIC_HUB_FEED,
    "content.public_views.events_past": PUBLIC_HUB_FEED,
    "content.public_views.podcast_hub": PUBLIC_HUB_FEED,
    "content.public_views.section_sitemap": PUBLIC_HUB_FEED,
    "content.public_views.wiki_feed": PUBLIC_HUB_FEED,
    "content.public_views.wiki_graph_json": PUBLIC_HUB_FEED,
    "content.public_views.wiki_hub": PUBLIC_HUB_FEED,
    "content.public_views.wiki_robots": PUBLIC_HUB_FEED,
    "content.public_views.wiki_search_json": PUBLIC_HUB_FEED,
    "content.public_views.wiki_sitemap": PUBLIC_HUB_FEED,
    "content.review_views.faq_course_json": PUBLIC_HUB_FEED,
    "content.review_views.faq_courses_json": PUBLIC_HUB_FEED,
    "core.views.home": PUBLIC_HUB_FEED,
    "core.views.robots": PUBLIC_HUB_FEED,
    "core.views.sitemap": PUBLIC_HUB_FEED,
    # -- Public: anonymous-stable catalogue/detail surfaces (cohort-specific
    # paths stay private: shared lessons are canonical at the cohort-free
    # path and `?cohort=` context is a private variant).
    "courses.views.course_list.course_list": PUBLIC_CATALOG_DETAIL,
    "courses.views.shared_course.shared_lesson_view": PUBLIC_CATALOG_DETAIL,
    "courses.views.shared_course.shared_module_view": PUBLIC_CATALOG_DETAIL,
    "courses.views.wrapped.wrapped_view": PUBLIC_CATALOG_DETAIL,
    # -- Public: code-owned permanent redirects (query follows the redirect
    # contract).
    "cadmin.legacy_urls.redirect_to_studio": PERMANENT_REDIRECT,
    "content.public_views.legacy_events_redirect": PERMANENT_REDIRECT,
    "content.public_views.permanent_public_redirect": PERMANENT_REDIRECT,
    "core.views.management_slash_redirect": PERMANENT_REDIRECT,
    "courses.views.course_aliases.legacy_course_redirect": PERMANENT_REDIRECT,
    "django.views.generic.base.RedirectView": PERMANENT_REDIRECT,
    # -- Operational: health, webhooks, jobs/mail ingress, token trackers.
    "api.openapi.spec.openapi_json_view": OPERATIONAL,
    "api.views.course_repository_webhooks.github_course_repository_webhook": OPERATIONAL,
    "api.views.health.health_view": OPERATIONAL,
    "community_base.content_sync.webhooks.github_webhook": OPERATIONAL,
    "community_base.jobs.ingress.run_job": OPERATIONAL,
    "community_base.mail.callback_ingress.receive_callback": OPERATIONAL,
    "community_base.mail.views.tracking_click": OPERATIONAL,
    "community_base.mail.views.tracking_open": OPERATIONAL,
    # -- Private/dynamic: everything else, listed explicitly.  Registration,
    # capability-bearing URLs (unsubscribe tokens), participant/live Q&A,
    # cohort-scoped learner surfaces, accounts, Studio, admin, and APIs.
    "accounts.api.compatibility_account_identity": PRIVATE_DYNAMIC,
    "accounts.api.current_account_identity": PRIVATE_DYNAMIC,
    "accounts.views.account_settings.account_settings": PRIVATE_DYNAMIC,
    "accounts.views.account_toggles.toggle_dark_mode": PRIVATE_DYNAMIC,
    "accounts.views.account_toggles.update_account_toggle": PRIVATE_DYNAMIC,
    "accounts.views.continuity.explicit_reauthentication": PRIVATE_DYNAMIC,
    "accounts.views.disabled.disabled": PRIVATE_DYNAMIC,
    "accounts.views.email_preferences.account_email_preferences": PRIVATE_DYNAMIC,
    "accounts.views.home_dismissals.dismiss_home_item": PRIVATE_DYNAMIC,
    "accounts.views.impersonation.admin_impersonation_exit": PRIVATE_DYNAMIC,
    "accounts.views.impersonation.stop_impersonating": PRIVATE_DYNAMIC,
    "accounts.views.login.social_login_view": PRIVATE_DYNAMIC,
    "accounts.views.social_connections.disconnect_social_account": PRIVATE_DYNAMIC,
    "accounts.views.social_connections.social_connections_moved": PRIVATE_DYNAMIC,
    "accounts.views.timezone.update_timezone_preference": PRIVATE_DYNAMIC,
    "accounts.views.welcome.welcome": PRIVATE_DYNAMIC,
    "allauth.account.views.AccountInactiveView": PRIVATE_DYNAMIC,
    "allauth.account.views.ConfirmEmailView": PRIVATE_DYNAMIC,
    "allauth.account.views.ConfirmLoginCodeView": PRIVATE_DYNAMIC,
    "allauth.account.views.EmailView": PRIVATE_DYNAMIC,
    "allauth.account.views.LoginView": PRIVATE_DYNAMIC,
    "allauth.account.views.LogoutView": PRIVATE_DYNAMIC,
    "allauth.account.views.PasswordChangeView": PRIVATE_DYNAMIC,
    "allauth.account.views.PasswordResetDoneView": PRIVATE_DYNAMIC,
    "allauth.account.views.PasswordResetFromKeyDoneView": PRIVATE_DYNAMIC,
    "allauth.account.views.PasswordResetFromKeyView": PRIVATE_DYNAMIC,
    "allauth.account.views.PasswordResetView": PRIVATE_DYNAMIC,
    "allauth.account.views.PasswordSetView": PRIVATE_DYNAMIC,
    "allauth.account.views.ReauthenticateView": PRIVATE_DYNAMIC,
    "allauth.account.views.SignupView": PRIVATE_DYNAMIC,
    "allauth.account.views.email_verification_sent": PRIVATE_DYNAMIC,
    "allauth.socialaccount.providers.google.views.LoginByTokenView": PRIVATE_DYNAMIC,
    "allauth.socialaccount.providers.oauth2.views.view": PRIVATE_DYNAMIC,
    "allauth.socialaccount.views.ConnectionsView": PRIVATE_DYNAMIC,
    "allauth.socialaccount.views.LoginCancelledView": PRIVATE_DYNAMIC,
    "allauth.socialaccount.views.LoginErrorView": PRIVATE_DYNAMIC,
    "allauth.socialaccount.views.SignupView": PRIVATE_DYNAMIC,
    "api.views.course_exports.course_criteria_yaml_view": PRIVATE_DYNAMIC,
    "api.views.courses.course_detail_view": PRIVATE_DYNAMIC,
    "api.views.courses.courses_list_view": PRIVATE_DYNAMIC,
    "api.views.enrollment_certificates.bulk_update_enrollment_certificates_view": PRIVATE_DYNAMIC,
    "api.views.enrollment_graduates.graduates_data_view": PRIVATE_DYNAMIC,
    "api.views.homework_exports.homework_data_view": PRIVATE_DYNAMIC,
    "api.views.homeworks.homework_detail_by_slug_view": PRIVATE_DYNAMIC,
    "api.views.homeworks.homework_detail_view": PRIVATE_DYNAMIC,
    "api.views.homeworks.homework_score_by_slug_view": PRIVATE_DYNAMIC,
    "api.views.homeworks.homework_score_view": PRIVATE_DYNAMIC,
    "api.views.homeworks.homeworks_view": PRIVATE_DYNAMIC,
    "api.views.leaderboard_exports.leaderboard_data_view": PRIVATE_DYNAMIC,
    "api.views.project_exports.project_data_view": PRIVATE_DYNAMIC,
    "api.views.projects.project_assign_reviews_by_slug_view": PRIVATE_DYNAMIC,
    "api.views.projects.project_assign_reviews_view": PRIVATE_DYNAMIC,
    "api.views.projects.project_detail_by_slug_view": PRIVATE_DYNAMIC,
    "api.views.projects.project_detail_view": PRIVATE_DYNAMIC,
    "api.views.projects.project_score_by_slug_view": PRIVATE_DYNAMIC,
    "api.views.projects.project_score_view": PRIVATE_DYNAMIC,
    "api.views.projects.projects_view": PRIVATE_DYNAMIC,
    "api.views.questions.question_detail_view": PRIVATE_DYNAMIC,
    "api.views.questions.questions_view": PRIVATE_DYNAMIC,
    "api.views.registration_campaigns.registration_campaign_detail_view": PRIVATE_DYNAMIC,
    "api.views.registration_campaigns.registration_campaign_registrations_view": PRIVATE_DYNAMIC,
    "api.views.registration_campaigns.registration_campaigns_view": PRIVATE_DYNAMIC,
    "community_base.content_sync.studio.history": PRIVATE_DYNAMIC,
    "community_base.content_sync.studio.source_edit": PRIVATE_DYNAMIC,
    "community_base.content_sync.studio.source_sync": PRIVATE_DYNAMIC,
    "community_base.content_sync.studio.sources_list": PRIVATE_DYNAMIC,
    "community_base.content_sync.studio.worker": PRIVATE_DYNAMIC,
    "community_base.mail.catalog_studio.template_detail": PRIVATE_DYNAMIC,
    "community_base.mail.catalog_studio.template_list": PRIVATE_DYNAMIC,
    "community_base.mail.studio.deliveries_list": PRIVATE_DYNAMIC,
    "community_base.mail.studio.delivery_detail": PRIVATE_DYNAMIC,
    "community_base.mail.views.public_unsubscribe": PRIVATE_DYNAMIC,
    "core.views.liveness": PRIVATE_DYNAMIC,
    "core.views.readiness": PRIVATE_DYNAMIC,
    "courses.views.course.course_view": PRIVATE_DYNAMIC,
    "courses.views.course_calendar.course_calendar_view": PRIVATE_DYNAMIC,
    "courses.views.course_enrollment.enrollment_view": PRIVATE_DYNAMIC,
    "courses.views.course_enrollment.update_enrollment_toggle": PRIVATE_DYNAMIC,
    "courses.views.course_leaderboard.leaderboard_complaint_view": PRIVATE_DYNAMIC,
    "courses.views.course_leaderboard.leaderboard_score_breakdown_view": PRIVATE_DYNAMIC,
    "courses.views.course_leaderboard.leaderboard_view": PRIVATE_DYNAMIC,
    "courses.views.dashboard.dashboard_view": PRIVATE_DYNAMIC,
    "courses.views.homework.homework_view": PRIVATE_DYNAMIC,
    "courses.views.homework_statistics.homework_statistics": PRIVATE_DYNAMIC,
    "courses.views.homework_submissions.homework_submissions": PRIVATE_DYNAMIC,
    "courses.views.module.module_view": PRIVATE_DYNAMIC,
    "courses.views.module.update_unit_read_state": PRIVATE_DYNAMIC,
    "courses.views.project.project_view": PRIVATE_DYNAMIC,
    "courses.views.project_eval.projects_eval_view": PRIVATE_DYNAMIC,
    "courses.views.project_eval_actions.projects_eval_add": PRIVATE_DYNAMIC,
    "courses.views.project_eval_actions.projects_eval_delete": PRIVATE_DYNAMIC,
    "courses.views.project_eval_submit.projects_eval_submit": PRIVATE_DYNAMIC,
    "courses.views.project_results.project_results": PRIVATE_DYNAMIC,
    "courses.views.project_statistics.project_statistics": PRIVATE_DYNAMIC,
    "courses.views.project_submissions.project_submissions": PRIVATE_DYNAMIC,
    "courses.views.site_project_gallery.project_gallery_view": PRIVATE_DYNAMIC,
    "courses.views.registration.registration_campaign_view": PRIVATE_DYNAMIC,
    "courses.views.unit.unit_view": PRIVATE_DYNAMIC,
    "courses.views.wrapped.user_wrapped_view": PRIVATE_DYNAMIC,
    "django.contrib.admin.options.add_view": PRIVATE_DYNAMIC,
    "django.contrib.admin.options.change_view": PRIVATE_DYNAMIC,
    "django.contrib.admin.options.changelist_view": PRIVATE_DYNAMIC,
    "django.contrib.admin.options.delete_view": PRIVATE_DYNAMIC,
    "django.contrib.admin.options.history_view": PRIVATE_DYNAMIC,
    "django.contrib.admin.sites.app_index": PRIVATE_DYNAMIC,
    "django.contrib.admin.sites.autocomplete_view": PRIVATE_DYNAMIC,
    "django.contrib.admin.sites.catch_all_view": PRIVATE_DYNAMIC,
    "django.contrib.admin.sites.i18n_javascript": PRIVATE_DYNAMIC,
    "django.contrib.admin.sites.login": PRIVATE_DYNAMIC,
    "django.contrib.admin.sites.logout": PRIVATE_DYNAMIC,
    "django.contrib.admin.sites.password_change_done": PRIVATE_DYNAMIC,
    "django.contrib.auth.views.LogoutView": PRIVATE_DYNAMIC,
    "django.contrib.contenttypes.views.shortcut": PRIVATE_DYNAMIC,
    "event_qna.studio_views.event_qna_cohost": PRIVATE_DYNAMIC,
    "event_qna.studio_views.event_qna_cohost_revoke": PRIVATE_DYNAMIC,
    "event_qna.studio_views.event_qna_detail": PRIVATE_DYNAMIC,
    "event_qna.studio_views.event_qna_moderate": PRIVATE_DYNAMIC,
    "event_qna.studio_views.event_qna_retry": PRIVATE_DYNAMIC,
    "event_qna.studio_views.event_qna_update": PRIVATE_DYNAMIC,
    "event_qna.views.public_qna": PRIVATE_DYNAMIC,
    "event_qna.views.qna_cohost_gate": PRIVATE_DYNAMIC,
    "event_qna.views.qna_host": PRIVATE_DYNAMIC,
    "event_qna.views.qna_present": PRIVATE_DYNAMIC,
    "event_qna.views.qna_qr": PRIVATE_DYNAMIC,
    "event_qna.views.qna_question": PRIVATE_DYNAMIC,
    "event_qna.views.qna_questions": PRIVATE_DYNAMIC,
    "event_qna.views.qna_vote": PRIVATE_DYNAMIC,
    "loginas.views.user_login": PRIVATE_DYNAMIC,
    "loginas.views.user_logout": PRIVATE_DYNAMIC,
    "management_api.urls.credential_collection": PRIVATE_DYNAMIC,
    "management_api.urls.event_qna_collection": PRIVATE_DYNAMIC,
    "management_api.urls.historical_import_collection": PRIVATE_DYNAMIC,
    "management_api.urls.operational_settings_collection": PRIVATE_DYNAMIC,
    "management_api.urls.site_navigation_collection": PRIVATE_DYNAMIC,
    "management_api.urls.site_settings_collection": PRIVATE_DYNAMIC,
    "management_api.urls.sponsor_collection": PRIVATE_DYNAMIC,
    "management_api.urls.sponsor_item": PRIVATE_DYNAMIC,
    "management_api.views.admin_health": PRIVATE_DYNAMIC,
    "management_api.views.credential_revoke": PRIVATE_DYNAMIC,
    "management_api.views.credential_rotate": PRIVATE_DYNAMIC,
    "management_api.views.event_identity_detail": PRIVATE_DYNAMIC,
    "management_api.views.event_identity_list": PRIVATE_DYNAMIC,
    "management_api.views.event_qna_cohost_create": PRIVATE_DYNAMIC,
    "management_api.views.event_qna_cohost_revoke": PRIVATE_DYNAMIC,
    "management_api.views.event_qna_moderate": PRIVATE_DYNAMIC,
    "management_api.views.event_qna_retry": PRIVATE_DYNAMIC,
    "management_api.views.historical_import_activate": PRIVATE_DYNAMIC,
    "management_api.views.historical_import_cancel": PRIVATE_DYNAMIC,
    "management_api.views.historical_import_detail": PRIVATE_DYNAMIC,
    "management_api.views.historical_import_dry_run": PRIVATE_DYNAMIC,
    "management_api.views.historical_import_rollback": PRIVATE_DYNAMIC,
    "management_api.views.historical_import_validate": PRIVATE_DYNAMIC,
    "management_api.views.historical_registration_total": PRIVATE_DYNAMIC,
    "management_api.views.oauth_provider_list": PRIVATE_DYNAMIC,
    "management_api.views.oauth_provider_update": PRIVATE_DYNAMIC,
    "management_api.views.sponsor_archive": PRIVATE_DYNAMIC,
    "management_api.views.sponsor_export": PRIVATE_DYNAMIC,
    "management_api.views.sponsor_reactivate": PRIVATE_DYNAMIC,
    "studio.views.audit_detail": PRIVATE_DYNAMIC,
    "studio.views.audit_export": PRIVATE_DYNAMIC,
    "studio.views.audit_list": PRIVATE_DYNAMIC,
    "studio.views.credential_list": PRIVATE_DYNAMIC,
    "studio.views.credential_revoke": PRIVATE_DYNAMIC,
    "studio.views.credential_rotate": PRIVATE_DYNAMIC,
    "studio.views.event_identity_detail": PRIVATE_DYNAMIC,
    "studio.views.event_identity_list": PRIVATE_DYNAMIC,
    "studio.views.historical_registration_action": PRIVATE_DYNAMIC,
    "studio.views.historical_registration_detail": PRIVATE_DYNAMIC,
    "studio.views.historical_registration_list": PRIVATE_DYNAMIC,
    "studio.views.historical_registration_total": PRIVATE_DYNAMIC,
    "studio.views.home": PRIVATE_DYNAMIC,
    "studio.views.site_navigation": PRIVATE_DYNAMIC,
    "studio.views.site_settings": PRIVATE_DYNAMIC,
    "studio.views.sponsor_archive": PRIVATE_DYNAMIC,
    "studio.views.sponsor_detail": PRIVATE_DYNAMIC,
    "studio.views.sponsor_export": PRIVATE_DYNAMIC,
    "studio.views.sponsor_reactivate": PRIVATE_DYNAMIC,
    "studio.views.sponsors": PRIVATE_DYNAMIC,
    "studio_courses.urls.course_list_slash_redirect": PRIVATE_DYNAMIC,
    "studio_courses.views.campaigns.campaign_create": PRIVATE_DYNAMIC,
    "studio_courses.views.campaigns.campaign_edit": PRIVATE_DYNAMIC,
    "studio_courses.views.campaigns.campaign_registrations": PRIVATE_DYNAMIC,
    "studio_courses.views.course_admin.course_admin": PRIVATE_DYNAMIC,
    "studio_courses.views.course_admin.course_list": PRIVATE_DYNAMIC,
    "studio_courses.views.enrollment.enrollment_edit": PRIVATE_DYNAMIC,
    "studio_courses.views.enrollment.enrollments_list": PRIVATE_DYNAMIC,
    "studio_courses.views.enrollment.leaderboard_complaint_resolve": PRIVATE_DYNAMIC,
    "studio_courses.views.enrollment.leaderboard_complaints": PRIVATE_DYNAMIC,
    "studio_courses.views.homework.homework_clear_correct_answers": PRIVATE_DYNAMIC,
    "studio_courses.views.homework.homework_extend_deadline": PRIVATE_DYNAMIC,
    "studio_courses.views.homework.homework_notify_scores": PRIVATE_DYNAMIC,
    "studio_courses.views.homework.homework_rescore": PRIVATE_DYNAMIC,
    "studio_courses.views.homework.homework_save_answers": PRIVATE_DYNAMIC,
    "studio_courses.views.homework.homework_score": PRIVATE_DYNAMIC,
    "studio_courses.views.homework.homework_set_correct_answers": PRIVATE_DYNAMIC,
    "studio_courses.views.homework.homework_submission_edit": PRIVATE_DYNAMIC,
    "studio_courses.views.homework.homework_submissions": PRIVATE_DYNAMIC,
    "studio_courses.views.observability.cloudwatch_dashboard": PRIVATE_DYNAMIC,
    "studio_courses.views.projects.project_assign_reviews": PRIVATE_DYNAMIC,
    "studio_courses.views.projects.project_extend_deadline": PRIVATE_DYNAMIC,
    "studio_courses.views.projects.project_score": PRIVATE_DYNAMIC,
    "studio_courses.views.projects.project_submission_edit": PRIVATE_DYNAMIC,
    "studio_courses.views.projects.project_submissions": PRIVATE_DYNAMIC,
    "unfold.admin.changelist_view": PRIVATE_DYNAMIC,
    "unfold.sites.index": PRIVATE_DYNAMIC,
    "unfold.sites.password_change": PRIVATE_DYNAMIC,
    "unfold.sites.search": PRIVATE_DYNAMIC,
}

#: View paths registered by reviewed development-only URLconfs
#: (``core.tests.seo_fixture_urls``).  The registry-completeness test allows
#: exactly these to exist without appearing in the production resolver.
TEST_SURFACE_VIEWS: set[str] = set()


def register_test_surface_views(classes: dict[str, str]) -> None:
    """Register fixture views from a test-only URLconf into the registry.

    Production code must not call this; it exists so the mounted middleware
    matrix in ``core.tests`` exercises the real registry lookup instead of a
    mock, while the completeness test keeps these entries out of the
    production classification contract.
    """

    for view_path, cache_class in classes.items():
        if cache_class not in SHAREABLE_CACHE_CLASSES:
            raise ValueError(f"test-surface views must map to a shareable class: {view_path}")
        ROUTE_CACHE_CLASSES[view_path] = cache_class
        TEST_SURFACE_VIEWS.add(view_path)


def request_query_keys(raw_query_string: str) -> frozenset[str]:
    """Decode the raw query string's keys without ever raising.

    The raw environment string is parsed, never ``request.GET``, which raises
    on non-ASCII raw bytes.  Undecodable bytes become replacement characters,
    which can only make a key *fail* the allowlist — the fail-closed direction.
    """

    return frozenset(key for key, _value in parse_qsl(raw_query_string, keep_blank_values=True))
