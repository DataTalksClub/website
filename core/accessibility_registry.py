from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, dataclass
from datetime import date
from pathlib import PurePosixPath


@dataclass(frozen=True, slots=True)
class CriticalState:
    identifier: str
    group: str
    axe_surface: str | None
    behavior_test: str
    js_required: bool = False
    core_smoke: bool = False
    route_contract: bool = False


@dataclass(frozen=True, slots=True)
class AxeException:
    rule: str
    selector: str
    state: str
    reason: str
    owner: str
    expires: date


def _state(
    identifier: str,
    group: str,
    surface: str | None,
    behavior_test: str,
    *,
    js: bool = False,
    core: bool = False,
    contract: bool = False,
) -> CriticalState:
    return CriticalState(
        identifier=identifier,
        group=group,
        axe_surface=surface,
        behavior_test=behavior_test,
        js_required=js,
        core_smoke=core,
        route_contract=contract,
    )


# Executable scenario keys. The complete Playwright matrix resolves every key, runs the scenario,
# and requires the scenario to report each registered state only after its state-specific assertion
# and accessibility scan have completed.
PUBLIC_TEST = "public-current-states"
IDENTITY_TEST = "account-current-states"
STUDIO_TEST = "management-current-states"
HISTORICAL_TEST = "historical-current-states"
COURSE_TEST = "learner-current-states"
STUDIO_COURSE_TEST = "studio-courses-current-states"

BEHAVIOR_SCENARIOS = frozenset(
    {
        PUBLIC_TEST,
        IDENTITY_TEST,
        STUDIO_TEST,
        HISTORICAL_TEST,
        COURSE_TEST,
        STUDIO_COURSE_TEST,
    }
)

CRITICAL_STATES = (
    _state("public.home", "public", "home", PUBLIC_TEST, core=True),
    _state("public.blog-hub", "public", "blog", PUBLIC_TEST),
    _state("public.podcast-hub", "public", "podcast", PUBLIC_TEST),
    _state("public.books-hub", "public", "books", PUBLIC_TEST),
    _state("public.events-hub", "public", "events", PUBLIC_TEST, core=True),
    _state("public.courses-hub", "public", "courses", PUBLIC_TEST, core=True),
    _state("public.wiki-hub", "public", "wiki", PUBLIC_TEST),
    _state("public.docs-hub", "public", "docs", PUBLIC_TEST),
    _state("public.faq-hub", "public", "faq", PUBLIC_TEST),
    _state("public.slack", "public", "slack", PUBLIC_TEST),
    _state("public.article-detail", "public", "article", PUBLIC_TEST),
    _state("public.podcast-transcript-media", "public", "podcast-detail", PUBLIC_TEST, core=True),
    _state("public.book-detail", "public", "book", PUBLIC_TEST),
    _state("public.event-aggregate-speaker", "public", "event", PUBLIC_TEST, core=True),
    _state("public.person-detail", "public", "person", PUBLIC_TEST),
    _state("public.course-detail", "public", "course", PUBLIC_TEST),
    _state("public.wiki-detail", "public", "wiki-detail", PUBLIC_TEST),
    _state("public.docs-nested", "public", "docs-detail", PUBLIC_TEST),
    _state("public.faq-anchor", "public", "faq-anchor", PUBLIC_TEST),
    _state("public.wiki-results", "public", "wiki-results", PUBLIC_TEST),
    _state("public.wiki-zero-results", "public", "wiki-zero", PUBLIC_TEST),
    _state("public.wiki-graph", "public", "wiki-graph", PUBLIC_TEST),
    _state("public.wiki-special-pages", "public", "wiki-special", PUBLIC_TEST),
    _state("public.missing-media", "public", "missing-media", PUBLIC_TEST),
    _state("public.empty-state", "public", "wiki-zero", PUBLIC_TEST),
    _state("public.application-404", "public", "missing", PUBLIC_TEST),
    _state("public.people-removed", "public", None, PUBLIC_TEST, contract=True),
    _state("public.people-slash-removed", "public", None, PUBLIC_TEST, contract=True),
    _state("public.people-html-removed", "public", None, PUBLIC_TEST, contract=True),
    _state("public.approved-redirect-destination", "public", "blog", PUBLIC_TEST, contract=True),
    _state("account.signed-out-login", "account", "login", IDENTITY_TEST, core=True),
    _state("account.invalid-login", "account", "login-error", IDENTITY_TEST, js=True),
    _state("account.unavailable-login", "account", "login", IDENTITY_TEST),
    _state(
        "account.authenticated-navigation", "account", "account-settings", IDENTITY_TEST, js=True
    ),
    _state("account.settings", "account", "account-settings", IDENTITY_TEST, js=True, core=True),
    _state("account.identity-conflict", "account", "identity-conflict", IDENTITY_TEST),
    _state("account.logout-return", "account", "login", IDENTITY_TEST),
    _state("account.revoked-session-return", "account", None, IDENTITY_TEST, contract=True),
    _state("account.safe-denial", "account", None, IDENTITY_TEST, contract=True),
    _state("management.admin-entry", "management", "admin-login", STUDIO_TEST),
    _state("management.studio-entry", "management", "studio-home", STUDIO_TEST, core=True),
    _state("studio.home", "management", "studio-home", STUDIO_TEST),
    _state("studio.credential-empty", "management", "credentials", STUDIO_TEST),
    _state("studio.credential-list", "management", "credentials", STUDIO_TEST),
    _state(
        "studio.credential-copy", "management", "credential-copy", STUDIO_TEST, js=True, core=True
    ),
    _state("studio.credential-rotate", "management", "credential-copy", STUDIO_TEST, js=True),
    _state("studio.credential-revoke", "management", "credentials", STUDIO_TEST),
    _state("studio.credential-denied", "management", None, STUDIO_TEST, contract=True),
    _state("studio.audit-empty", "management", "audit-list", STUDIO_TEST),
    _state("studio.audit-filter", "management", "audit-list", STUDIO_TEST),
    _state("studio.audit-detail", "management", "audit-detail", STUDIO_TEST),
    _state("studio.audit-denied", "management", None, STUDIO_TEST, contract=True),
    _state("historical.list", "historical", "historical-list", HISTORICAL_TEST, core=True),
    _state("historical.detail", "historical", "historical-detail", HISTORICAL_TEST),
    _state("historical.mapping", "historical", "historical-mappings", HISTORICAL_TEST),
    _state("historical.validation-success", "historical", "historical-detail", HISTORICAL_TEST),
    _state("historical.exclusion", "historical", "historical-detail", HISTORICAL_TEST),
    _state("historical.source-missing", "historical", "historical-mappings", HISTORICAL_TEST),
    _state(
        "historical.unsupported-quarantined", "historical", "historical-detail", HISTORICAL_TEST
    ),
    _state("historical.stale-revision", "historical", "historical-detail", HISTORICAL_TEST),
    _state("historical.overlap-conflict", "historical", "historical-detail", HISTORICAL_TEST),
    _state("historical.activation-preview", "historical", "historical-detail", HISTORICAL_TEST),
    _state("historical.rollback", "historical", "historical-detail", HISTORICAL_TEST),
    _state("historical.empty", "historical", "historical-list", HISTORICAL_TEST),
    _state("historical.denied", "historical", None, HISTORICAL_TEST, contract=True),
    _state("learner.signed-out-registration", "learner", "registration", COURSE_TEST, core=True),
    _state("learner.dashboard", "learner", "dashboard", COURSE_TEST, js=True),
    _state("learner.enrollment", "learner", "enrollment", COURSE_TEST),
    _state("learner.homework", "learner", "homework", COURSE_TEST, js=True),
    _state("learner.project", "learner", "project", COURSE_TEST),
    _state("learner.peer-review", "learner", "peer-review", COURSE_TEST),
    _state("learner.score", "learner", "score", COURSE_TEST),
    _state("learner.leaderboard", "learner", "leaderboard", COURSE_TEST, core=True),
    _state("learner.complaint", "learner", "complaint", COURSE_TEST),
    _state("learner.certificate-graduation", "learner", "enrollment", COURSE_TEST),
    _state("learner.empty", "learner", "course-empty", COURSE_TEST),
    _state("learner.success", "learner", "registration-success", COURSE_TEST),
    _state("learner.invalid-form", "learner", "registration-error", COURSE_TEST, js=True),
    _state("learner.stale-denied", "learner", "login", COURSE_TEST),
    _state("learner.long-content", "learner", "homework", COURSE_TEST),
    _state(
        "studio-courses.list", "studio-courses", "studio-courses", STUDIO_COURSE_TEST, core=True
    ),
    _state("studio-courses.form", "studio-courses", "studio-course-form", STUDIO_COURSE_TEST),
    _state("studio-courses.table", "studio-courses", "studio-course-table", STUDIO_COURSE_TEST),
    _state(
        "studio-courses.confirmation", "studio-courses", "studio-course-form", STUDIO_COURSE_TEST
    ),
    _state("studio-courses.error", "studio-courses", "studio-course-form", STUDIO_COURSE_TEST),
    _state("studio-courses.denied", "studio-courses", None, STUDIO_COURSE_TEST, contract=True),
    _state(
        "studio-courses.legacy-redirect", "studio-courses", None, STUDIO_COURSE_TEST, contract=True
    ),
)

# No exception is accepted by this implementation. Keeping the typed registry here makes any
# future exception reviewable and forces all required ownership/expiry fields to be supplied.
AXE_EXCEPTIONS: tuple[AxeException, ...] = ()

ACCESSIBILITY_AUTHORED_TEMPLATES = (
    "accounts/templates/accounts/login.html",
    "studio_courses/templates/studio_courses/campaign_form.html",
    "studio_courses/templates/studio_courses/include/campaign_field.html",
    "core/templates/accessibility/email.html",
    "core/templates/accessibility/error_summary.html",
    "course_platform_templates/accounts/account_settings.html",
    "course_platform_templates/base.html",
    "courses/templates/courses/enrollment.html",
    "courses/templates/courses/register.html",
    "templates/management_api/credential_fixture.html",
    "templates/core/_site_footer.html",
    "templates/public/_event_meta.html",
    "templates/public/article_detail.html",
    "templates/public/collection_hub.html",
    "templates/public/course_detail.html",
    "templates/public/legal/base.html",
    "templates/public/legal/impressum.html",
    "templates/public/legal/privacy.html",
    "templates/public/legal/terms.html",
    "templates/public/podcast_hub.html",
    "templates/studio/base.html",
    "templates/studio/credentials.html",
    "templates/unfold/layouts/unauthenticated.html",
)

_DJANGO_STRUCTURAL_TAG = re.compile(
    r"{%\s*(?:block|endblock|if|elif|else|endif|for|empty|endfor|include)\b"
)
_HTML_TAG = re.compile(r"</?[A-Za-z][^>]*>")
_HTML_OPENING_TAG = re.compile(r"<(?!/)[A-Za-z][^>]*>")


def template_readability_issues(source: str) -> list[str]:
    issues: list[str] = []
    for line_number, line in enumerate(source.splitlines(), start=1):
        structural_tags = _DJANGO_STRUCTURAL_TAG.findall(line)
        if len(structural_tags) > 1:
            issues.append(f"line {line_number}: multiple Django structural tags")
        if structural_tags and _HTML_TAG.search(line):
            issues.append(f"line {line_number}: Django structural tag shares HTML markup")
        if len(_HTML_OPENING_TAG.findall(line)) > 1:
            issues.append(f"line {line_number}: adjacent nested opening tags")
    return issues


AUTHORED_TEMPLATE_ROOTS = {
    "course_platform_templates": "account-learner-shared",
    "templates/base.html": "foundation-fallback",
    "templates/404.html": "public",
    "templates/core": "public",
    "templates/public": "public",
    "templates/review": "public-review",
    "templates/studio": "studio",
    "templates/registration": "account",
    "templates/courses": "public-courses",
    "templates/management_api": "management-fixture",
    "templates/unfold": "django-admin",
    "accounts/templates": "account",
    "courses/templates": "learner",
    "studio_courses/templates": "studio-courses",
}


def template_surface(path: str) -> str | None:
    normalized = PurePosixPath(path).as_posix().removeprefix("./")
    matches = [
        surface
        for prefix, surface in AUTHORED_TEMPLATE_ROOTS.items()
        if normalized == prefix or normalized.startswith(f"{prefix}/")
    ]
    if len(matches) > 1 and normalized != "course_platform_templates/base.html":
        raise ValueError(f"template has overlapping accessibility ownership: {normalized}")
    return matches[0] if matches else None


def registry_fingerprint() -> str:
    payload = [asdict(state) for state in CRITICAL_STATES]
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()
