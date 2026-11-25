"""``dtc events`` -- event lifecycle, speakers, banners, registrants, reminders."""

from __future__ import annotations

import base64
import csv
import json
import uuid
from pathlib import Path
from typing import Any

import click

from dtc_cli.client import get_client
from dtc_cli.output import emit, format_csv

API = "/api/v1/admin"
# Keep each banner JSON request safely under CloudFront WAF's 8,192-byte body limit.
BANNER_CHUNK_RAW_BYTES = 4096

BANNER_MIME = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
}


def _split_csv(value: str | None) -> list[str] | None:
    if value is None:
        return None
    return [item.strip() for item in value.split(",") if item.strip()]


def _load_json_or_file(raw: str | None, *, label: str) -> Any:
    if raw is None:
        return None
    text = Path(raw[1:]).read_text(encoding="utf-8") if raw.startswith("@") else raw
    try:
        return json.loads(text)
    except ValueError as exc:
        raise click.UsageError(
            f"{label} must be valid JSON (or @path/to/file.json): {exc}"
        ) from exc


def _resolve_event_uuid(event_ref: str) -> str:
    """Accept a UUID, numeric public_id, or slug and return the event UUID."""
    ref = event_ref.strip()
    if len(ref) == 36 and ref.count("-") == 4:
        return ref
    params = {"public_id": ref} if ref.isdigit() else {"slug": ref}
    data = get_client().get(f"{API}/events", params=params)
    items = data.get("items") or []
    if not items:
        raise click.ClickException(f"Event '{event_ref}' not found.")
    return str(items[0]["id"])


@click.group()
def events() -> None:
    """Manage DataTalks.Club events."""


@events.command("list")
@click.option(
    "--status",
    type=click.Choice(["draft", "upcoming", "in_progress", "past", "cancelled", "archived"]),
    default=None,
    help="Draft, cancelled and archived are stored; upcoming, in_progress and past "
    "follow from the event's start and end time.",
)
@click.option("--upcoming", is_flag=True, help="Show only upcoming events.")
@click.option("--past", is_flag=True, help="Show only past events.")
@click.option("--format", "fmt", type=click.Choice(["json", "table"]), default="json")
def events_list(status: str | None, upcoming: bool, past: bool, fmt: str) -> None:
    """List events."""
    params: dict[str, str] = {}
    if status:
        params["status"] = status
    if upcoming:
        params["upcoming"] = "true"
    if past:
        params["past"] = "true"
    data = get_client().get(f"{API}/events", params=params or None)
    if fmt == "table":
        emit(
            data.get("items", []),
            "table",
            columns=["public_id", "type", "status", "start_datetime", "title", "public_url"],
        )
    else:
        emit(data, "json")


@events.command("get")
@click.argument("event")
def events_get(event: str) -> None:
    """Get a single event by public_id, slug, or UUID."""
    event_uuid = _resolve_event_uuid(event)
    emit(get_client().get(f"{API}/events/{event_uuid}"))


def _apply_common_flags(func: Any) -> Any:
    options = [
        click.option("--title", default=None, help="Event title (Title Case)."),
        click.option("--slug", default=None, help="URL slug (auto-derived from title if omitted)."),
        click.option("--description", default=None, help="Markdown description."),
        click.option(
            "--description-file",
            type=click.Path(exists=True, dir_okay=False, path_type=Path),
            default=None,
            help="Read Markdown description from UTF-8 file.",
        ),
        click.option(
            "--type",
            "event_type",
            default=None,
            help=(
                "DTC event type (podcast, webinar, workshop, conference, meetup, "
                "office-hours, project-of-the-week)."
            ),
        ),
        click.option(
            "--kind",
            type=click.Choice(["standard", "workshop", "meetup", "q_and_a"]),
            default=None,
        ),
        click.option(
            "--platform",
            type=click.Choice(["zoom", "custom", "in_person"]),
            default=None,
        ),
        click.option("--start-datetime", default=None, help="ISO 8601 datetime."),
        click.option("--end-datetime", default=None, help="ISO 8601 datetime."),
        click.option("--timezone", default=None, help="IANA timezone (default Europe/Berlin)."),
        click.option("--location", default=None, help="Public stream URL or venue."),
        click.option("--join-url", default=None, help="Protected join URL (sent to registrants)."),
        click.option("--recording-url", default=None, help="YouTube/recording URL."),
        click.option("--speakers", default=None, help="Comma-separated speaker short keys."),
        click.option("--tags", default=None, help="Comma-separated display tags."),
        click.option("--season", type=int, default=None, help="Podcast season number."),
        click.option("--episode", type=int, default=None, help="Podcast episode number."),
        click.option(
            "--materials",
            default=None,
            help="JSON array of {label, url} objects (or @file.json).",
        ),
        click.option(
            "--reminder-intervals",
            default=None,
            help="Comma-separated intervals (e.g. 24h,20m or 24h or none).",
        ),
        click.option(
            "--sponsor-sharing/--no-sponsor-sharing",
            "sponsor_sharing_enabled",
            default=None,
            help="Show sponsor email-sharing consent checkbox on registration.",
        ),
        click.option(
            "--collect-company/--no-collect-company",
            default=None,
            help="Collect company name on registration.",
        ),
        click.option("--sponsor-name", default=None, help="Sponsor display name."),
        click.option(
            "--publish/--no-publish",
            default=None,
            help="Publish immediately (create default: true).",
        ),
    ]
    for opt in reversed(options):
        func = opt(func)
    return func


def _build_event_payload(kwargs: dict[str, Any], *, is_create: bool) -> dict[str, Any]:
    body: dict[str, Any] = {}
    desc_file = kwargs.pop("description_file", None)
    if desc_file is not None:
        if kwargs.get("description") is not None:
            raise click.UsageError("Use either --description or --description-file, not both.")
        body["description"] = desc_file.read_text(encoding="utf-8")

    mapping = {
        "title": "title",
        "slug": "slug",
        "description": "description",
        "event_type": "type",
        "kind": "kind",
        "platform": "platform",
        "start_datetime": "start_datetime",
        "end_datetime": "end_datetime",
        "timezone": "timezone",
        "location": "location",
        "join_url": "zoom_join_url",
        "recording_url": "recording_url",
        "season": "season",
        "episode": "episode",
        "reminder_intervals": "reminder_intervals",
        "sponsor_sharing_enabled": "sponsor_sharing_enabled",
        "collect_company": "collect_company",
        "sponsor_name": "sponsor_name",
        "publish": "publish",
    }
    for cli_key, api_key in mapping.items():
        val = kwargs.get(cli_key)
        if val is not None:
            body[api_key] = val

    if kwargs.get("speakers") is not None:
        body["speakers"] = _split_csv(kwargs["speakers"])
    if kwargs.get("tags") is not None:
        body["tags"] = _split_csv(kwargs["tags"])
    if kwargs.get("materials") is not None:
        body["materials"] = _load_json_or_file(kwargs["materials"], label="--materials")
    if is_create:
        body.setdefault("publish", True)
        body.setdefault("timezone", "Europe/Berlin")
    return body


@events.command("create")
@_apply_common_flags
def events_create(**kwargs: Any) -> None:
    """Create an event (published by default; pass --no-publish for draft)."""
    body = _build_event_payload(kwargs, is_create=True)
    emit(get_client().post(f"{API}/events", json_body=body))


@events.command("update")
@click.argument("event")
@_apply_common_flags
def events_update(event: str, **kwargs: Any) -> None:
    """Update an event. Only passed flags are sent."""
    event_uuid = _resolve_event_uuid(event)
    body = _build_event_payload(kwargs, is_create=False)
    emit(get_client().patch(f"{API}/events/{event_uuid}", json_body=body))


@events.command("publish")
@click.argument("event")
def events_publish(event: str) -> None:
    """Publish a draft event."""
    event_uuid = _resolve_event_uuid(event)
    emit(get_client().post(f"{API}/events/{event_uuid}/publish"))


@events.command("cancel")
@click.argument("event")
@click.option("--reason", default="", help="Optional cancellation reason.")
def events_cancel(event: str, reason: str) -> None:
    """Cancel an event and notify active registrants."""
    event_uuid = _resolve_event_uuid(event)
    emit(get_client().post(f"{API}/events/{event_uuid}/cancel", json_body={"reason": reason}))


@events.command("complete")
@click.argument("event")
@click.option("--recording-url", default="", help="Optional recording URL.")
def events_complete(event: str, recording_url: str) -> None:
    """Confirm an ended event and attach its recording.

    Whether an event is past follows from its end time; this refuses an event
    that has not ended yet.
    """
    event_uuid = _resolve_event_uuid(event)
    body = {"recording_url": recording_url} if recording_url else {}
    emit(get_client().post(f"{API}/events/{event_uuid}/complete", json_body=body))


@events.command("upload-banner")
@click.argument("event")
@click.option(
    "--file",
    "banner_file",
    required=True,
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    help="Path to 1000x1000 PNG, JPEG, or WebP banner image.",
)
def events_upload_banner(event: str, banner_file: Path) -> None:
    """Upload a 1000x1000 event banner image."""
    mime = BANNER_MIME.get(banner_file.suffix.lower())
    if mime is None:
        raise click.UsageError("Banner file must be .png, .jpg, .jpeg, or .webp.")
    raw_bytes = banner_file.read_bytes()
    if not raw_bytes:
        raise click.UsageError("Banner file is empty.")
    event_uuid = _resolve_event_uuid(event)
    client = get_client()
    path = f"{API}/events/{event_uuid}/banner"
    if len(raw_bytes) <= BANNER_CHUNK_RAW_BYTES:
        encoded = base64.b64encode(raw_bytes).decode("ascii")
        emit(
            client.post(
                path,
                json_body={"content_type": mime, "image_base64": encoded},
            )
        )
        return

    chunks = [
        raw_bytes[offset : offset + BANNER_CHUNK_RAW_BYTES]
        for offset in range(0, len(raw_bytes), BANNER_CHUNK_RAW_BYTES)
    ]
    upload_id = str(uuid.uuid4())
    chunk_count = len(chunks)
    last_response: dict[str, Any] = {}
    for index, chunk in enumerate(chunks):
        extra_headers = {"X-Banner-Upload-Chunk": "1"} if index > 0 else None
        last_response = client.post(
            path,
            json_body={
                "upload_id": upload_id,
                "chunk_index": index,
                "chunk_count": chunk_count,
                "content_type": mime,
                "chunk_base64": base64.b64encode(chunk).decode("ascii"),
            },
            extra_headers=extra_headers,
        )
    emit(last_response)


@events.command("export-registrants")
@click.argument("event")
@click.option("--output", "-o", type=click.Path(dir_okay=False, path_type=Path), default=None)
@click.option("--format", "fmt", type=click.Choice(["csv", "json"]), default="csv")
@click.option(
    "--newsletter-only",
    is_flag=True,
    help="Only registrants with newsletter_consent=true.",
)
@click.option(
    "--sponsor-consented-only",
    is_flag=True,
    help="Only registrants who consented to sponsor sharing.",
)
def events_export_registrants(
    event: str,
    output: Path | None,
    fmt: str,
    newsletter_only: bool,
    sponsor_consented_only: bool,
) -> None:
    """Export event registrants to CSV or JSON."""
    event_uuid = _resolve_event_uuid(event)
    params: dict[str, str] = {}
    if newsletter_only:
        params["newsletter_only"] = "true"
    if sponsor_consented_only:
        params["sponsor_consented_only"] = "true"
    data = get_client().get(f"{API}/events/{event_uuid}/registrants", params=params or None)
    rows = data.get("items", [])
    if fmt == "csv":
        fieldnames = [
            "email",
            "display_name",
            "status",
            "company",
            "newsletter_consent",
            "sponsor_sharing_consent",
            "created_at",
            "verified_at",
            "attended_at",
        ]
        rendered = format_csv(rows, fieldnames)
    else:
        rendered = json.dumps(data, indent=2, ensure_ascii=False) + "\n"
    if output is not None:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(rendered, encoding="utf-8")
        emit({"event_id": event_uuid, "exported": len(rows), "output": str(output)})
    else:
        click.echo(rendered, nl=False)


@events.command("import-attendance")
@click.argument("event")
@click.option(
    "--file",
    "attendance_file",
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    default=None,
    help="CSV (with 'email' column) or JSON array/object of emails.",
)
@click.option("--emails", default=None, help="Comma-separated email addresses.")
@click.option("--status", type=click.Choice(["attended", "no_show"]), default="attended")
def events_import_attendance(
    event: str, attendance_file: Path | None, emails: str | None, status: str
) -> None:
    """Mark registrants as attended or no_show."""
    email_list: list[str] = []
    if attendance_file is not None:
        raw = attendance_file.read_text(encoding="utf-8")
        if attendance_file.suffix.lower() == ".json":
            parsed = json.loads(raw)
            if isinstance(parsed, list):
                email_list.extend(str(item) for item in parsed)
            elif isinstance(parsed, dict) and isinstance(parsed.get("emails"), list):
                email_list.extend(str(item) for item in parsed["emails"])
        else:
            reader = csv.DictReader(raw.splitlines())
            for row in reader:
                if row.get("email"):
                    email_list.append(row["email"].strip())
    if emails:
        email_list.extend(_split_csv(emails) or [])
    if not email_list:
        raise click.UsageError("Provide --file or --emails.")
    event_uuid = _resolve_event_uuid(event)
    emit(
        get_client().post(
            f"{API}/events/{event_uuid}/attendance",
            json_body={"emails": email_list, "status": status},
        )
    )


@events.command("message")
@click.argument("event")
@click.option("--subject", required=True)
@click.option("--body", default=None)
@click.option(
    "--body-file",
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    default=None,
)
@click.option(
    "--audience",
    type=click.Choice(["confirmed", "attended", "all_active"]),
    default="confirmed",
)
@click.option("--dry-run", is_flag=True, help="Preview recipient count without sending.")
def events_message(
    event: str,
    subject: str,
    body: str | None,
    body_file: Path | None,
    audience: str,
    dry_run: bool,
) -> None:
    """Send an email message to event registrants."""
    if body_file is not None:
        if body is not None:
            raise click.UsageError("Use either --body or --body-file, not both.")
        body_text = body_file.read_text(encoding="utf-8")
    elif body is not None:
        body_text = body
    else:
        raise click.UsageError("Provide --body or --body-file.")
    event_uuid = _resolve_event_uuid(event)
    emit(
        get_client().post(
            f"{API}/events/{event_uuid}/message",
            json_body={
                "subject": subject,
                "body": body_text,
                "audience": audience,
                "dry_run": dry_run,
            },
        )
    )


@events.command("reminders")
@click.argument("event")
@click.option(
    "--intervals",
    default=None,
    help="Set reminder intervals (e.g. 24h,20m or 24h or none).",
)
def events_reminders(event: str, intervals: str | None) -> None:
    """Inspect or update per-event reminder intervals."""
    event_uuid = _resolve_event_uuid(event)
    body = {"reminder_intervals": intervals} if intervals is not None else {}
    emit(get_client().post(f"{API}/events/{event_uuid}/reminders", json_body=body))


@events.group("speakers")
def speakers() -> None:
    """List or create event speakers."""


@speakers.command("list")
@click.option("--search", default=None)
def speakers_list(search: str | None) -> None:
    """List active speakers."""
    params = {"search": search} if search else None
    emit(get_client().get(f"{API}/events/speakers", params=params))


@speakers.command("create")
@click.option("--short", required=True, help="Person short key (matches _people/<short>.md).")
@click.option("--name", required=True, help="Display name.")
@click.option("--title", default="", help="Role or job title.")
@click.option("--bio", default="", help="Short biography.")
@click.option("--photo-url", default="", help="Optional photo URL.")
def speakers_create(short: str, name: str, title: str, bio: str, photo_url: str) -> None:
    """Create or update an event speaker profile."""
    emit(
        get_client().post(
            f"{API}/events/speakers",
            json_body={
                "short": short,
                "name": name,
                "title": title,
                "bio": bio,
                "photo_url": photo_url,
            },
        )
    )
