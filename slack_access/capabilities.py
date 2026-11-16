"""One management contract for staff HTML and scoped bearer API adapters."""

from core.capabilities import (
    AdapterMetadata,
    Capability,
    ConcurrencyPolicy,
    IdempotencyPolicy,
    ServiceKind,
    staff_or_active_principal,
)

from . import management_services as services


def _factory():
    return {"contract": "slack.v1"}


def _capability(
    key,
    description,
    service,
    studio,
    api,
    schema,
    *,
    command=False,
    request_schema=None,
    fields=(),
    ban=False,
):
    method = "POST" if command else "GET"
    filters = (
        ("state",)
        if key == "slack.applications.list"
        else ("classification",)
        if key == "slack.joins.report"
        else ()
    )
    return Capability(
        key=key,
        description=description,
        service_kind=ServiceKind.COMMAND if command else ServiceKind.QUERY,
        service=service,
        django_permission=f"slack_access.{'ban_members' if ban else 'review_applications'}",
        studio=AdapterMetadata(
            route=studio, method=method, operation_id=f"{key}.html", writable_fields=fields
        ),
        admin_api=AdapterMetadata(
            route=f"/api/v1/admin/slack/{api}",
            method=method,
            operation_id=key,
            scopes=(key,),
            request_schema=request_schema,
            result_schema=schema,
            writable_fields=fields,
            filter_fields=filters,
            paginated=bool(filters),
            rate_class="write" if command else "read",
            rate_cost=5 if command else 1,
        ),
        idempotency=IdempotencyPolicy.REQUIRED if command else IdempotencyPolicy.NONE,
        # Commands compare expected domain state/count under the graph lock. There is
        # no independent mutable resource revision or If-Match projection to declare.
        concurrency=ConcurrencyPolicy.NONE,
        audit_action=key,
        redacted_fields=(
            "authorization",
            "cookie",
            "email",
            "who",
            "work",
            "what",
            "token",
            "redirect_digest",
            "vouch_key",
            "scope_token",
        ),
        test_factory=_factory,
        function_policy=staff_or_active_principal,
        field_policy=(lambda actor, field: field in fields) if command else None,
    )


APPLICATION_LIST = _capability(
    "slack.applications.list",
    "Review bounded Slack applications and their questionnaire",
    services.list_applications,
    "studio:slack-review",
    "applications",
    "SlackApplications",
)
APPLICATION_REVIEW = _capability(
    "slack.applications.review",
    "Approve or decline one pending Slack application",
    services.review_application,
    "studio:slack-review-action",
    "applications/{application_id}/review",
    "SlackReviewResult",
    command=True,
    request_schema="SlackReviewRequest",
    fields=("action", "expected_state"),
)
BAN_PREVIEW = _capability(
    "slack.members.preview",
    "Preview the accountable membership ban cascade",
    services.ban_preview,
    "studio:slack-moderation",
    "members/{member_id}/ban",
    "SlackBanPreview",
    ban=True,
)
MEMBER_BAN = _capability(
    "slack.members.ban",
    "Confirm a reviewed website accountability ban cascade",
    services.ban_member,
    "studio:slack-moderation-action",
    "members/{member_id}/ban",
    "SlackBanResult",
    command=True,
    request_schema="SlackBanRequest",
    fields=("confirmed", "expected_count", "scope_token"),
    ban=True,
)
JOIN_REPORT = _capability(
    "slack.joins.report",
    "Inspect Slack joins outside or unresolved by website approval",
    services.join_report,
    "studio:slack-report",
    "joins",
    "SlackJoinReport",
)
SLACK_CAPABILITIES = (APPLICATION_LIST, APPLICATION_REVIEW, BAN_PREVIEW, MEMBER_BAN, JOIN_REPORT)
