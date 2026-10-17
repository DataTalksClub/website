"""Immutable announcement publications and a deliberately small placeholder language."""

import re

from courses.models import AnnouncementTemplate, AnnouncementTemplateVersion
from courses.services.launch_validation import LaunchError, load_row, require_revision, text

PLACEHOLDERS = frozenset(
    {
        "cohort_start_date",
        "registration_url",
        "pre_course_qna_title",
        "pre_course_qna_url",
        "launch_stream_date",
        "launch_stream_time",
        "launch_stream_title",
        "launch_stream_url",
        "homework_1_url",
        "homework_1_submission_url",
        "homework_1_deadline",
        "homework_2_url",
        "homework_2_submission_url",
        "homework_2_deadline",
        "homework_3_url",
        "homework_3_submission_url",
        "homework_3_deadline",
        "homework_4_url",
        "homework_4_submission_url",
        "homework_4_deadline",
        "homework_5_url",
        "homework_5_submission_url",
        "homework_5_deadline",
        "homework_6_url",
        "homework_6_submission_url",
        "homework_6_deadline",
        "total_modules",
        "course_title",
        "cohort_title",
        "cohort_year",
        "course_platform_url",
        "module_number",
        "module_title",
        "module_url",
        "module_short_description",
        "module_email_headline",
        "module_action",
        "module_intro_paragraph",
        "module_highlight_1",
        "module_highlight_2",
        "module_highlight_3",
        "module_highlight_4",
        "module_highlight_5",
        "project_title",
        "project_url",
        "starts_at",
        "homework_available",
        "homework_number",
        "homework_url",
        "homework_submission_url",
        "homework_deadline",
        "homework_summary",
        "optional_homework_link",
        "optional_submission_link",
        "optional_deadline_line",
        "optional_submission_closes_line",
        "browser_view_url",
        "leaderboard_url",
    }
)
TOKEN = re.compile(r"{{\s*([a-z][a-z0-9_]*)\s*}}")
BLOCK = re.compile(r"{{#(if|unless)\s+(homework_available)}}(.*?){{/\1}}", re.S)


def render_copy(source, context):
    for name in TOKEN.findall(source):
        if name not in PLACEHOLDERS:
            raise LaunchError("Unknown template placeholder.")

    def conditional(match):
        include = bool(context.get(match[2]))
        if match[1] == "unless":
            include = not include
        if include:
            return match[3]
        return ""

    source = BLOCK.sub(conditional, source)

    def replace(match):
        if match[1] not in PLACEHOLDERS:
            raise LaunchError("Unknown template placeholder.")
        return str(context.get(match[1], ""))

    result = TOKEN.sub(replace, source)
    if "{{" in result or "}}" in result or "{%" in result:
        raise LaunchError("Use supported {{ placeholder }} values and homework conditionals only.")
    return result


def template_values(payload):
    channel = payload["channel"]
    kind = payload["kind"]
    if channel not in {"email", "telegram"} or kind not in {"cohort", "module", "project"}:
        raise LaunchError(
            "Choose an email or Telegram template and a cohort/module/project anchor."
        )
    offset = payload.get("offset_minutes", 0)
    if isinstance(offset, bool) or not isinstance(offset, int) or abs(offset) > 525600:
        raise LaunchError("Offset must be integer minutes within one year.")
    subject = text(payload.get("subject", ""), "subject", limit=200, blank=channel == "telegram")
    if "\n" in subject or "\r" in subject:
        raise LaunchError("Email subject must be a single line.")
    body = text(payload["body"], "body")
    if channel == "telegram" and len(body) > 4096:
        raise LaunchError("Telegram templates must fit within 4096 characters.")
    render_copy(subject, {})
    render_copy(body, {})
    return {
        "name": text(payload["name"], "name", limit=150),
        "channel": channel,
        "kind": kind,
        "subject": subject,
        "body": body,
        "offset_minutes": offset,
    }


def save_template(payload):
    values = template_values(payload)
    if not payload.get("id"):
        row = create_template(payload, values)
    else:
        row = load_row(AnnouncementTemplate, payload["id"], lock=True)
        require_revision(row, payload["expected_revision"])
        for name, value in values.items():
            setattr(row, name, value)
        row.revision += 1
        row.save()
    return serialize_template(row)


def create_template(payload, values):
    seed = payload.get("seed_key")
    if seed is None:
        return AnnouncementTemplate.objects.create(**values)
    if not isinstance(seed, str) or re.fullmatch(r"copy-bank:[0-9a-f]{64}", seed) is None:
        raise LaunchError("Invalid copy-bank provenance digest.")
    row, created = AnnouncementTemplate.objects.get_or_create(seed_key=seed, defaults=values)
    if not created:
        for name, value in values.items():
            if getattr(row, name) != value:
                raise LaunchError(
                    "This imported template changed. Edit its draft or import a new source.",
                    status=409,
                )
    return row


def publish_template(payload):
    row = load_row(AnnouncementTemplate, payload["id"], lock=True)
    require_revision(row, payload["expected_revision"])
    if row.archived:
        raise LaunchError("Restore the template before publishing.")
    values = template_values(serialize_template(row))
    values.pop("name")
    version, _ = AnnouncementTemplateVersion.objects.get_or_create(
        template=row, number=row.revision, defaults=values
    )
    return {"id": str(version.id), "template_id": str(row.id), "number": version.number}


def archive_template(payload):
    row = load_row(AnnouncementTemplate, payload["id"], lock=True)
    require_revision(row, payload["expected_revision"])
    if type(payload["archived"]) is not bool:
        raise LaunchError("Archived must be a boolean.")
    row.archived = payload["archived"]
    row.revision += 1
    row.save(update_fields=["archived", "revision"])
    return serialize_template(row)


def serialize_template(row):
    return {
        "id": str(row.id),
        "name": row.name,
        "channel": row.channel,
        "kind": row.kind,
        "subject": row.subject,
        "body": row.body,
        "offset_minutes": row.offset_minutes,
        "revision": row.revision,
        "archived": row.archived,
        "seed_key": row.seed_key,
    }
