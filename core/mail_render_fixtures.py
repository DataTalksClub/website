"""Render a committed package mail template for tests and visual evidence.

The renderer is the package's transitional markdown renderer (the same one
the deploy mirror and D1.2a's render tests use); context is synthetic and
type-aware so list-valued template variables iterate.
"""

from __future__ import annotations

from typing import Any

from community_base.mail.backends.ses_local import render_delivery
from community_base.mail.models import EmailDelivery

from core import mail_templates

#: List- and object-valued context keys need real shapes to iterate.
CONTEXT_OVERRIDES = {
    "assigned_reviews": [
        {
            "review_id": 1,
            "eval_url": "https://courses.datatalks.club/eval/1",
            "submission_github_link": "https://github.com/example/project",
        },
        {
            "review_id": 2,
            "eval_url": "https://courses.datatalks.club/eval/2",
            "submission_github_link": "",
        },
    ],
}


def render_package_template(template_key: str):
    template = {t.key: t for t in mail_templates.load_templates()}[template_key]
    context = {
        "user_name": "Q1Z",
        "user_email": "learner@example.com",
        "site_name": "DataTalks.Club",
        "site_url": "https://courses.datatalks.club",
    }
    override: Any
    for position, key in enumerate(template.required_context, start=2):
        context[key] = f"Q{position}Z"
    # Block-tag-only variables (a {% for %} over a list) never appear as
    # {{ }} placeholders, so the overrides are applied unconditionally.
    context.update(CONTEXT_OVERRIDES)
    delivery = EmailDelivery(
        template_key=template_key,
        recipient_email="learner@example.com",
    )
    return render_delivery(delivery, context)
