"""Console presentation is separate from JSON API transport."""

import json
import sys

EXAMPLES = {
    "template_save": "--name Module --channel email --kind module --subject "
    "'Module starts' --body-file message.md",
    "template_publish": "--id TEMPLATE_UUID --expected-revision 1",
    "template_archive": "--id TEMPLATE_UUID --expected-revision 1 --archived",
    "course_create": "--slug example --title Example --version-ids VERSION_UUID",
    "cohort_create": "--course-id COURSE_UUID --slug example-2027 --identifier "
    "2027 --title Example --description 'Live cohort' --year "
    "2027",
    "assignment_save": "--course-id COURSE_UUID --expected-revision 1 --version-ids VERSION_UUID",
    "timing_update": "--target-kind module --id MODULE_ID --expected-revision 0 "
    "--starts-at 2027-01-11T17:00:00+00:00 --provenance "
    "'Approved schedule'",
    "launch": "--cohort-id COHORT_UUID",
    "compose": "--cohort-id COHORT_UUID --version-id VERSION_UUID --target-kind "
    "module --target-id MODULE_ID",
    "announcement_edit": "--id ANNOUNCEMENT_UUID --expected-revision 1 "
    "--subject Updated --body-file message.md",
    "approve": "--id ANNOUNCEMENT_UUID --expected-revision 1",
    "cancel": "--id ANNOUNCEMENT_UUID --expected-revision 1",
    "schedule": "--id ANNOUNCEMENT_UUID --expected-revision 2 --scheduled-at "
    "2027-01-11T17:00:00+00:00 --confirmed",
    "send": "--id ANNOUNCEMENT_UUID --expected-revision 2 --confirmed",
    "test_send": "--id ANNOUNCEMENT_UUID --destination operator@example.invalid --confirmed",
    "batch_preview": "--cohort-id COHORT_UUID",
    "launch_and_schedule": "--cohort-id COHORT_UUID --confirmed --preview-token PREVIEW_TOKEN",
}


def command_example(family, command, action):
    flags = "--json"
    if command != "list":
        flags = EXAMPLES[action]
    return f"Example: uv run --frozen python scripts/course_launch.py {family} {command} {flags}"


def emit_result(result, as_json):
    if as_json:
        print(json.dumps(result))
        return
    if "preview_fingerprint" in result and "rows" in result:
        print(f"Preview fingerprint: {result['preview_fingerprint']}")
        print(f"Eligible: {result.get('eligible_count', 0)}")
        print(f"Skipped: {result.get('skipped_count', 0)}")
        if result.get("guidance"):
            print(result["guidance"])
        for item in result["rows"]:
            print(f"{item.get('generated_key', '')}  {item.get('reason', '')}")
        return
    if "enqueued_count" in result and "skipped_count" in result:
        print(f"Enqueued: {result['enqueued_count']}")
        print(f"Skipped: {result['skipped_count']}")
        for item in result.get("enqueued", []):
            print(f"{item.get('generated_key', '')}  scheduled {item.get('scheduled_at', '')}")
        for item in result.get("skipped", []):
            print(f"{item.get('generated_key', '')}  {item.get('reason', '')}")
        return
    if "items" in result:
        print(
            f"{result.get('total_count', len(result['items']))} records; "
            f"page {result.get('page', 1)}"
        )
        for item in result["items"]:
            label = item.get("name") or item.get("title") or item.get("kind") or "record"
            print(f"{item.get('id', '')}  {label}  {item.get('status', '')}")
        return
    for key in (
        "id",
        "principal_id",
        "status",
        "mode",
        "generated",
        "armed",
        "signed_in",
        "guidance",
    ):
        if key in result:
            print(f"{key.replace('_', ' ').title()}: {result[key]}")
    if "preview" in result:
        print(result["preview"].get("subject", ""))
        print(result["preview"].get("body", ""))


def emit_error(message, exit_code, as_json):
    if as_json:
        print(json.dumps({"error": {"message": message, "exitCode": exit_code}}), file=sys.stderr)
        return
    print(f"dtc: {message}", file=sys.stderr)
