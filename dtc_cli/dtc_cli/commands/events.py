"""``dtc events`` -- event lifecycle, speakers, banners, registrants, reminders."""

from __future__ import annotations

import base64
import csv
import json
import os
import shutil
import subprocess
import tempfile
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import click
import httpx

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


DEFAULT_BANNER_GENERATOR_DIR = Path.home() / "git" / "banner-generator"
BANNER_TEMPLATE = "dtc-event-speaker"


def _sniff_mime(raw: bytes) -> str | None:
    if raw.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if raw.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if len(raw) >= 12 and raw[:4] == b"RIFF" and raw[8:12] == b"WEBP":
        return "image/webp"
    return None


def _picture_labels(start_raw: str, tz_name: str) -> tuple[str, str]:
    """``("Tue 13 Dec 2022", "17:00 CET")`` in the event's own timezone."""
    try:
        zone = ZoneInfo(tz_name)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise click.UsageError(f"Unknown --timezone {tz_name!r}.") from exc
    try:
        start = datetime.fromisoformat(start_raw)
    except ValueError as exc:
        raise click.UsageError("--start-datetime must be ISO 8601.") from exc
    local = start.replace(tzinfo=zone) if start.tzinfo is None else start.astimezone(zone)
    return (
        f"{local:%a} {local.day} {local:%b %Y}",
        f"{local:%H:%M} {local.tzname() or tz_name}",
    )


def _speaker_entries(client: Any, shorts: list[str], workdir: Path) -> list[dict[str, Any]]:
    """Name and local portrait for each speaker short key.

    The name comes from the speakers API (the short key when the speaker is new);
    the portrait is the site's ``/images/authors/<short>.jpg``, left out (the
    template then draws initials) when the site has none.
    """
    entries: list[dict[str, Any]] = []
    base_url = str(getattr(client, "base_url", "") or "").rstrip("/")
    for short in shorts:
        name = short
        try:
            found = client.get(f"{API}/events/speakers", params={"search": short})
            for item in found.get("items") or []:
                if str(item.get("short")) == short:
                    name = str(item.get("name") or short)
                    break
        except Exception:  # noqa: BLE001 - a missing name falls back to the short key
            pass
        entry: dict[str, Any] = {"name": name}
        if base_url:
            try:
                response = httpx.get(
                    f"{base_url}/images/authors/{short}.jpg", timeout=20.0, follow_redirects=True
                )
                if response.status_code == 200 and _sniff_mime(response.content):
                    portrait = workdir / f"{short}.jpg"
                    portrait.write_bytes(response.content)
                    entry["image_url"] = portrait.resolve().as_uri()
            except httpx.HTTPError:
                pass
        entries.append(entry)
    return entries


def render_standard_picture(
    body: dict[str, Any],
    *,
    client: Any,
    generator_dir: Path,
    output: Path,
) -> Path:
    """Render the standard event picture with banner-generator (``dtc-event-speaker``).

    Runs ``uv run banner-generator render`` in the local banner-generator
    checkout; see its README, "DataTalks.Club event pictures".
    """
    missing = [
        flag
        for flag, key in (
            ("--title", "title"),
            ("--type", "type"),
            ("--start-datetime", "start_datetime"),
            ("--speakers", "speakers"),
        )
        if not body.get(key)
    ]
    if missing:
        raise click.UsageError(
            f"Rendering the standard picture needs {', '.join(missing)} (or pass --banner)."
        )
    if not (generator_dir / "pyproject.toml").is_file():
        raise click.UsageError(
            f"banner-generator not found at {generator_dir}. Pass --banner FILE, or point "
            "--banner-generator-dir / DTC_BANNER_GENERATOR_DIR at a checkout."
        )
    uv = shutil.which("uv")
    if uv is None:
        raise click.UsageError("Rendering the standard picture needs `uv` on PATH.")
    date_label, time_label = _picture_labels(
        str(body["start_datetime"]), str(body.get("timezone") or "Europe/Berlin")
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="dtc-event-picture-") as tmp:
        workdir = Path(tmp)
        spec = {
            "template": BANNER_TEMPLATE,
            "format": "png",
            "size": "luma",
            "output": str(output.resolve()),
            "kind": str(body["type"]),
            "title": str(body["title"]),
            "date_label": date_label,
            "time_label": time_label,
            "speakers": _speaker_entries(client, list(body["speakers"]), workdir),
        }
        spec_path = workdir / "event-picture.json"
        spec_path.write_text(json.dumps(spec, indent=2), encoding="utf-8")
        completed = subprocess.run(  # noqa: S603 - fixed argv, local trusted tool
            [
                uv,
                "run",
                "--project",
                str(generator_dir),
                "banner-generator",
                "render",
                str(spec_path),
            ],
            cwd=str(generator_dir),
            capture_output=True,
            text=True,
            check=False,
        )
        if completed.returncode != 0 or not output.is_file():
            detail = (completed.stderr or completed.stdout).strip().splitlines()[-5:]
            raise click.ClickException(
                "banner-generator failed to render the standard picture:\n" + "\n".join(detail)
            )
    return output


def _stage_picture(client: Any, raw_bytes: bytes, mime: str) -> str:
    """Send the picture in WAF-sized chunks before the create call; return its upload id."""
    upload_id = str(uuid.uuid4())
    chunks = [
        raw_bytes[offset : offset + BANNER_CHUNK_RAW_BYTES]
        for offset in range(0, len(raw_bytes), BANNER_CHUNK_RAW_BYTES)
    ]
    for index, chunk in enumerate(chunks):
        client.post(
            f"{API}/events",
            json_body={
                "picture_chunk": {
                    "upload_id": upload_id,
                    "chunk_index": index,
                    "chunk_count": len(chunks),
                    "content_type": mime,
                    "chunk_base64": base64.b64encode(chunk).decode("ascii"),
                }
            },
            extra_headers={"X-Banner-Upload-Chunk": "1"} if index > 0 else None,
        )
    return upload_id


@events.command("create")
@_apply_common_flags
@click.option(
    "--banner",
    "banner_file",
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    default=None,
    help="The event picture: a 1000x1000 PNG, JPEG or WebP. Without it the standard "
    "picture is rendered with banner-generator from the title, type, speakers and date.",
)
@click.option(
    "--banner-generator-dir",
    type=click.Path(file_okay=False, path_type=Path),
    default=None,
    help="banner-generator checkout used to render the standard picture "
    "(default: $DTC_BANNER_GENERATOR_DIR or ~/git/banner-generator).",
)
@click.option(
    "--banner-output",
    type=click.Path(dir_okay=False, path_type=Path),
    default=None,
    help="Keep the rendered standard picture at this path (default: a temporary file).",
)
def events_create(
    banner_file: Path | None,
    banner_generator_dir: Path | None,
    banner_output: Path | None,
    **kwargs: Any,
) -> None:
    """Create an event with its picture (published by default; --no-publish for draft).

    Every event has a picture from the moment it exists, so the picture is sent
    with the create call: --banner FILE, or else the standard picture rendered
    locally with banner-generator.
    """
    body = _build_event_payload(kwargs, is_create=True)
    if not str(body.get("description") or "").strip():
        raise click.UsageError(
            "Every event needs a description: pass --description or --description-file."
        )
    client = get_client()
    if banner_file is not None:
        raw_bytes = banner_file.read_bytes()
    else:
        generator_dir = banner_generator_dir or Path(
            os.environ.get("DTC_BANNER_GENERATOR_DIR") or DEFAULT_BANNER_GENERATOR_DIR
        )
        with tempfile.TemporaryDirectory(prefix="dtc-event-picture-out-") as out_tmp:
            output = banner_output or Path(out_tmp) / "event-picture.png"
            render_standard_picture(body, client=client, generator_dir=generator_dir, output=output)
            raw_bytes = output.read_bytes()
        click.echo(
            f"Rendered the standard picture ({len(raw_bytes)} bytes)"
            + (f": {banner_output}" if banner_output else "."),
            err=True,
        )
    mime = _sniff_mime(raw_bytes)
    if mime is None:
        raise click.UsageError("The picture must be a PNG, JPEG or WebP image.")
    body["picture"] = {"upload_id": _stage_picture(client, raw_bytes, mime)}
    emit(client.post(f"{API}/events", json_body=body))


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
