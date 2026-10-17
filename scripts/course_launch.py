"""Operator CLI. Examples: templates list; announcements schedule --help."""

import argparse
import hashlib
import json
import sys
import uuid
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.course_launch_client import (
    ClientError,
    configuration,
    device_login_flow,
    origin,
    profile_path,
    request,
    write_profile,
)
from scripts.course_launch_presentation import command_example, emit_error, emit_result

PREFIX = "/api/v1/admin/course-launch"
COMMANDS = {
    "versions": {},
    "plans": {},
    "templates": {
        "save": "template_save",
        "seed": "template_save",
        "publish": "template_publish",
        "archive": "template_archive",
    },
    "courses": {"create": "course_create"},
    "cohorts": {"create": "cohort_create", "launch": "launch"},
    "assignments": {"save": "assignment_save"},
    "starts": {"update": "timing_update"},
    "announcements": {
        "compose": "compose",
        "edit": "announcement_edit",
        "approve": "approve",
        "cancel": "cancel",
        "schedule": "schedule",
        "send": "send",
        "test": "test_send",
    },
    "schedules": {"cancel": "cancel", "schedule": "schedule"},
}
VALUE_FLAGS = (
    "description",
    "destination",
    "id",
    "name",
    "channel",
    "kind",
    "subject",
    "body",
    "body_file",
    "slug",
    "title",
    "course_id",
    "cohort_id",
    "identifier",
    "delivery_mode",
    "curriculum_format",
    "version_id",
    "target_kind",
    "target_id",
    "starts_at",
    "provenance",
    "scheduled_at",
    "telegram_destination_key",
    "context",
)


def parser():
    result = argparse.ArgumentParser(
        description="Course launch operator CLI. Example: templates list --json"
    )
    result.add_argument("--json", action="store_true")
    result.add_argument("--url")
    families = result.add_subparsers(dest="family", required=True)
    for name in ("login", "logout", "whoami"):
        sub = families.add_parser(
            name, epilog=f"Example: uv run --frozen python scripts/course_launch.py {name} --json"
        )
        sub.add_argument("--json", action="store_true", default=argparse.SUPPRESS)
        sub.add_argument("--url", default=argparse.SUPPRESS)
        if name == "login":
            sub.add_argument("--token-stdin", action="store_true")
            sub.add_argument("--label", default=None)
    for family, commands in COMMANDS.items():
        family_commands = families.add_parser(family).add_subparsers(dest="command", required=True)
        for command in ("list", *commands):
            example = command_example(family, command, commands.get(command))
            add_options(family_commands.add_parser(command, epilog=example))
    return result


def add_options(sub):
    sub.add_argument("--json", action="store_true", default=argparse.SUPPRESS)
    sub.add_argument("--url", default=argparse.SUPPRESS)
    sub.add_argument("--idempotency-key")
    for name in VALUE_FLAGS:
        sub.add_argument("--" + name.replace("_", "-"))
    for name in ("expected_revision", "offset_minutes", "year", "page"):
        sub.add_argument("--" + name.replace("_", "-"), type=int)
    sub.add_argument("--version-ids", dest="version_ids", action="append")
    for name in ("confirmed", "archived", "override_defaults"):
        sub.add_argument(
            "--" + name.replace("_", "-"), action=argparse.BooleanOptionalAction, default=None
        )


def authenticate(args):
    if args.family == "logout":
        profile_path().unlink(missing_ok=True)
        return {"signed_in": False}
    if args.family == "whoami":
        return request("GET", PREFIX + "?resource=identity", url=args.url)
    if args.token_stdin:
        token = sys.stdin.readline().strip()
        base, credential = configuration(args.url, token)
        identity = request("GET", PREFIX + "?resource=identity", url=base, token=credential)
        write_profile({"url": base, "token": credential})
        return identity
    base = origin(args.url)
    credential = device_login_flow(base, label=args.label)
    identity = request("GET", PREFIX + "?resource=identity", url=base, token=credential["token"])
    write_profile({"url": base, "token": credential["token"]})
    return identity


def payload_for(args):
    values = vars(args).copy()
    payload: dict[str, Any] = {"action": COMMANDS[args.family][args.command]}
    ignored = {"family", "command", "json", "url", "idempotency_key", "page", "body_file"}
    for name, value in values.items():
        if name not in ignored and value is not None:
            payload[name] = value
    if args.body_file:
        payload["body"] = Path(args.body_file).read_text()
    if args.context:
        payload["context"] = json.loads(args.context)
    if payload.get("starts_at") == "null":
        payload["starts_at"] = None
    return payload


def execute(args):
    if args.family in {"login", "logout", "whoami"}:
        return authenticate(args)
    if args.command == "list":
        resource = args.family
        if resource == "starts":
            resource = "timings"
        query = {"resource": resource}
        for name in ("id", "cohort_id", "target_kind", "page"):
            if getattr(args, name) is not None:
                query[name] = getattr(args, name)
        return request("GET", PREFIX + "?" + urlencode(query), url=args.url)
    payload = payload_for(args)
    if args.command == "seed" and not args.body_file:
        raise ClientError("Seed requires --body-file pointing to reviewed copy-bank content.", 2)
    if args.command == "seed":
        digest = hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()
        payload["seed_key"] = f"copy-bank:{digest}"
    path = "/commands"
    if payload["action"] in {"send", "schedule", "test_send"}:
        path = "/dispatch"
    return request(
        "POST",
        PREFIX + path,
        url=args.url,
        payload=payload,
        key=args.idempotency_key or str(uuid.uuid4()),
        revision=args.expected_revision,
    )


def main(argv=None):
    args = parser().parse_args(argv)
    try:
        result = execute(args)
    except (ClientError, ValueError, OSError) as error:
        code = getattr(error, "exit_code", 2)
        message = "Invalid input or unreadable input file."
        if isinstance(error, ClientError):
            message = str(error)
        emit_error(message, code, args.json)
        return code
    emit_result(result, args.json)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
