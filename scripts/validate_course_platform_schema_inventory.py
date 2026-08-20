#!/usr/bin/env python3
"""Validate the static phase-1 course-platform schema/data inventory.

The audit is intentionally Markdown so it can be reviewed as a normal repository
artifact.  This validator only reads checked-in files and the captured audit text;
it never imports Django, opens a database, calls GitHub, or updates the source pin.
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import unquote, urlsplit

REPOSITORY = "DataTalksClub/website"
AUDIT_PATH = Path("_docs/audits/2026-08-20-course-platform-schema-data-inventory-phase-1.md")
SOURCE_PIN_PATH = Path("_docs/adoption/course-platform/source-pin.json")
COPIED_MANIFEST_PATH = Path("_docs/adoption/course-platform/copied-files.tsv")
CURRENT_MAIN_SHA = "4825aa38f27903518b941a251520c60a6845f61a"
SOURCE_PIN_COMMIT = "98a235283904b4ef9ad29e196298540756cf1bcc"
EXPECTED_REF = "refs/heads/main"

CLASSIFICATIONS = frozenset(
    {"identity", "definition", "operational", "learner", "history", "side-effect"}
)
PROVENANCES = frozenset({"pinned-cmp", "target-overlay"})
RELATIONSHIP_KINDS = frozenset({"ForeignKey", "OneToOneField", "ManyToManyField"})
ISSUE_HANDOFFS = frozenset({14, 15, 16, 21, 22, 23, 30, 100, 133})
UTC_PATTERN = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")
SHA_PATTERN = re.compile(r"^[0-9a-f]{40}$")
LINK_PATTERN = re.compile(r"\[[^\]]+\]\((?P<target>[^)]+)\)")
ISSUE_REFERENCE_PATTERN = re.compile(r"#(?P<number>\d+)")
ISSUE_URL_PATTERN = re.compile(
    rf"^https://github\.com/{re.escape(REPOSITORY)}/issues/(?P<number>\d+)$"
)


class ValidationError(ValueError):
    """Raised when the captured inventory violates its static contract."""


@dataclass(frozen=True)
class ModelSpec:
    table: str
    source_path: str
    provenance: str
    owner: str
    classification: str


MODEL_SPECS: dict[str, ModelSpec] = {
    "accounts.CustomUser": ModelSpec(
        "accounts_customuser", "accounts/models.py", "pinned-cmp", "#30", "identity"
    ),
    "accounts.Token": ModelSpec(
        "accounts_token", "accounts/models.py", "pinned-cmp", "#30", "identity"
    ),
    "accounts.AccountIdentityAlias": ModelSpec(
        "accounts_accountidentityalias", "accounts/models.py", "target-overlay", "#100", "identity"
    ),
    "accounts.AccountIdentityQuarantine": ModelSpec(
        "accounts_accountidentityquarantine",
        "accounts/models.py",
        "target-overlay",
        "#100",
        "history",
    ),
    "accounts.AccountReconciliationRun": ModelSpec(
        "accounts_accountreconciliationrun",
        "accounts/models.py",
        "target-overlay",
        "#100",
        "history",
    ),
    "courses.Cohort": ModelSpec(
        "courses_course", "courses/models/cohort.py", "pinned-cmp", "#30", "definition"
    ),
    "courses.RegistrationCampaign": ModelSpec(
        "courses_registrationcampaign",
        "courses/models/cohort.py",
        "pinned-cmp",
        "#30",
        "definition",
    ),
    "courses.CourseRegistration": ModelSpec(
        "courses_courseregistration", "courses/models/cohort.py", "pinned-cmp", "#30", "operational"
    ),
    "courses.Enrollment": ModelSpec(
        "courses_enrollment", "courses/models/cohort.py", "pinned-cmp", "#30", "learner"
    ),
    "courses.LeaderboardComplaint": ModelSpec(
        "courses_leaderboardcomplaint",
        "courses/models/cohort.py",
        "pinned-cmp",
        "#30",
        "operational",
    ),
    "courses.Homework": ModelSpec(
        "courses_homework", "courses/models/homework.py", "pinned-cmp", "#30", "definition"
    ),
    "courses.Question": ModelSpec(
        "courses_question", "courses/models/homework.py", "pinned-cmp", "#30", "definition"
    ),
    "courses.Submission": ModelSpec(
        "courses_submission", "courses/models/homework.py", "pinned-cmp", "#30", "learner"
    ),
    "courses.Answer": ModelSpec(
        "courses_answer", "courses/models/homework.py", "pinned-cmp", "#30", "learner"
    ),
    "courses.HomeworkStatistics": ModelSpec(
        "courses_homeworkstatistics", "courses/models/homework.py", "pinned-cmp", "#30", "history"
    ),
    "courses.WrappedStatistics": ModelSpec(
        "courses_wrappedstatistics", "courses/models/wrapped.py", "pinned-cmp", "#30", "history"
    ),
    "courses.UserWrappedStatistics": ModelSpec(
        "courses_userwrappedstatistics", "courses/models/wrapped.py", "pinned-cmp", "#30", "history"
    ),
    "courses.Project": ModelSpec(
        "courses_project", "courses/models/project.py", "pinned-cmp", "#30", "definition"
    ),
    "courses.ProjectSubmission": ModelSpec(
        "courses_projectsubmission", "courses/models/project.py", "pinned-cmp", "#30", "learner"
    ),
    "courses.ProjectVote": ModelSpec(
        "courses_projectvote", "courses/models/project.py", "pinned-cmp", "#30", "learner"
    ),
    "courses.ReviewCriteria": ModelSpec(
        "courses_reviewcriteria", "courses/models/project.py", "pinned-cmp", "#30", "definition"
    ),
    "courses.PeerReview": ModelSpec(
        "courses_peerreview", "courses/models/project.py", "pinned-cmp", "#30", "learner"
    ),
    "courses.CriteriaResponse": ModelSpec(
        "courses_criteriaresponse", "courses/models/project.py", "pinned-cmp", "#30", "learner"
    ),
    "courses.ProjectEvaluationScore": ModelSpec(
        "courses_projectevaluationscore",
        "courses/models/project.py",
        "pinned-cmp",
        "#30",
        "learner",
    ),
    "courses.ProjectStatistics": ModelSpec(
        "courses_projectstatistics", "courses/models/project.py", "pinned-cmp", "#30", "history"
    ),
    "courses.CourseRegistrationCountSourceRun": ModelSpec(
        "courses_courseregistrationcountsourcerun",
        "courses/models/registration_counts.py",
        "target-overlay",
        "#133",
        "history",
    ),
    "courses.CourseRegistrationCountRevision": ModelSpec(
        "courses_courseregistrationcountrevision",
        "courses/models/registration_counts.py",
        "target-overlay",
        "#133",
        "history",
    ),
    "courses.CourseRegistrationCountSlot": ModelSpec(
        "courses_courseregistrationcountslot",
        "courses/models/registration_counts.py",
        "target-overlay",
        "#133",
        "operational",
    ),
    "data.DatamailerContactEvent": ModelSpec(
        "data_datamailercontactevent", "data/models.py", "pinned-cmp", "#30", "side-effect"
    ),
    "data.DatamailerOutboxEvent": ModelSpec(
        "data_datamaileroutboxevent", "data/models.py", "pinned-cmp", "#30", "side-effect"
    ),
    "data.DatamailerOutboxDispatchRun": ModelSpec(
        "data_datamaileroutboxdispatchrun", "data/models.py", "pinned-cmp", "#30", "side-effect"
    ),
    "data.DatamailerSendAudit": ModelSpec(
        "data_datamailersendaudit", "data/models.py", "pinned-cmp", "#30", "side-effect"
    ),
}

RELATIONSHIP_KEYS = frozenset(
    {
        "accounts.Token.user",
        "accounts.AccountIdentityAlias.survivor",
        "courses.Cohort.students",
        "courses.RegistrationCampaign.current_course",
        "courses.CourseRegistration.campaign",
        "courses.CourseRegistration.course",
        "courses.CourseRegistration.user",
        "courses.Enrollment.student",
        "courses.Enrollment.course",
        "courses.LeaderboardComplaint.enrollment",
        "courses.LeaderboardComplaint.reporter",
        "courses.LeaderboardComplaint.resolved_by",
        "courses.Homework.course",
        "courses.Question.homework",
        "courses.Submission.homework",
        "courses.Submission.student",
        "courses.Submission.enrollment",
        "courses.Answer.submission",
        "courses.Answer.question",
        "courses.HomeworkStatistics.homework",
        "courses.Project.course",
        "courses.ProjectSubmission.project",
        "courses.ProjectSubmission.student",
        "courses.ProjectSubmission.enrollment",
        "courses.ProjectVote.submission",
        "courses.ProjectVote.voter",
        "courses.ReviewCriteria.course",
        "courses.PeerReview.submission_under_evaluation",
        "courses.PeerReview.reviewer",
        "courses.CriteriaResponse.review",
        "courses.CriteriaResponse.criteria",
        "courses.ProjectEvaluationScore.submission",
        "courses.ProjectEvaluationScore.review_criteria",
        "courses.ProjectStatistics.project",
        "courses.CourseRegistrationCountSourceRun.actor",
        "courses.CourseRegistrationCountRevision.source_run",
        "courses.CourseRegistrationCountRevision.campaign",
        "courses.CourseRegistrationCountRevision.cohort",
        "courses.CourseRegistrationCountSlot.campaign",
        "courses.CourseRegistrationCountSlot.cohort",
        "courses.CourseRegistrationCountSlot.active_baseline_revision",
        "courses.CourseRegistrationCountSlot.prior_baseline_revision",
        "courses.UserWrappedStatistics.wrapped",
        "courses.UserWrappedStatistics.user",
    }
)

BOUNDARY_KEYS = frozenset(
    {
        "routes.accounts",
        "routes.compatibility-api",
        "routes.studio-courses",
        "routes.public-courses",
        "export.api-course-criteria",
        "export.api-leaderboard",
        "export.api-homework-submissions",
        "export.api-project-submissions",
        "export.api-graduates",
        "export.api-certificates",
        "export.public-calendar-ics",
        "compat.cadmin-legacy",
        "compat.namespaced-admin-api",
        "compat.datamailer-callback",
        "compat.datamailer-send-audits",
        "commands.pinned-cmp",
        "commands.target-owned",
    }
)

HANDOFF_KEYS = frozenset({14, 15, 16, 21, 22, 23})
MODEL_COLUMNS = (
    "Key",
    "App",
    "Model class",
    "Table",
    "Source path",
    "Migration provenance",
    "Provenance",
    "Owner issue",
    "Classification",
    "Evidence",
)
MIGRATION_COLUMNS = (
    "App",
    "Current numbered migrations",
    "Pinned CMP numbered migrations",
    "Current evidence",
    "Pinned evidence",
    "Distinction",
)
RELATIONSHIP_COLUMNS = (
    "Key",
    "Declaring field",
    "Kind",
    "Target",
    "Classification",
    "Owner / provenance",
    "Evidence",
    "Hand-off",
)
BOUNDARY_COLUMNS = (
    "Key",
    "Boundary",
    "Classification",
    "Owner / provenance",
    "Evidence",
    "Hand-off",
)
HANDOFF_COLUMNS = ("Issue", "State", "Unresolved contract / hand-off", "Evidence")
SCHEMA_ASSERTION_COLUMNS = (
    "Model",
    "Field",
    "Declaration evidence",
    "Migration evidence",
)


def _split_table_row(line: str) -> list[str]:
    value = line.strip()
    if not value.startswith("|"):
        raise ValidationError(f"table row must start with '|': {line!r}")
    value = value[1:]
    if value.endswith("|"):
        value = value[:-1]
    cells = [cell.strip() for cell in value.split("|")]
    if any("|" in cell for cell in cells):
        raise ValidationError(f"unescaped pipe in table row: {line!r}")
    return cells


def _table(lines: list[str], heading: str, columns: tuple[str, ...]) -> list[tuple[str, ...]]:
    try:
        heading_index = lines.index(heading)
    except ValueError as exc:
        raise ValidationError(f"missing {heading!r} section") from exc
    header_index = next(
        (index for index in range(heading_index + 1, len(lines)) if lines[index].startswith("|")),
        None,
    )
    if header_index is None:
        raise ValidationError(f"{heading} has no Markdown table")
    if tuple(_split_table_row(lines[header_index])) != columns:
        raise ValidationError(f"{heading} has unexpected columns")
    if header_index + 1 >= len(lines) or not lines[header_index + 1].startswith("|"):
        raise ValidationError(f"{heading} has no separator row")
    rows: list[tuple[str, ...]] = []
    for line in lines[header_index + 2 :]:
        if not line.startswith("|"):
            break
        cells = _split_table_row(line)
        if len(cells) != len(columns):
            raise ValidationError(
                f"{heading} row has {len(cells)} columns, expected {len(columns)}"
            )
        rows.append(tuple(cells))
    return rows


def _metadata_value(lines: list[str], label: str) -> str:
    prefix = f"- {label}: `"
    for line in lines:
        if line.startswith(prefix) and line.endswith("`"):
            return line[len(prefix) : -1]
    raise ValidationError(f"missing metadata: {label}")


def _metadata_int(lines: list[str], label: str) -> int:
    value = _metadata_value(lines, label)
    if not value.isdigit():
        raise ValidationError(f"{label} must be an integer: {value!r}")
    return int(value)


def _validate_metadata(lines: list[str]) -> None:
    expected = {
        "Snapshot repository": REPOSITORY,
        "Snapshot ref": EXPECTED_REF,
        "Snapshot SHA": CURRENT_MAIN_SHA,
        "Source pin commit": SOURCE_PIN_COMMIT,
        "Models (current adopted target)": "32",
        "Models (pinned CMP)": "26",
        "Models (target overlays)": "6",
        "Adopted routes": "89",
        "Pinned CMP commands": "13",
        "Target-owned commands": "5",
        "Current command registry": "18",
    }
    for label, expected_value in expected.items():
        value = _metadata_value(lines, label)
        if value != expected_value:
            raise ValidationError(f"{label} must be {expected_value!r}, got {value!r}")
    sha = _metadata_value(lines, "Snapshot SHA")
    if not SHA_PATTERN.fullmatch(sha):
        raise ValidationError("snapshot SHA must be a full lowercase hexadecimal SHA")
    source_pin = _metadata_value(lines, "Source pin commit")
    if not SHA_PATTERN.fullmatch(source_pin):
        raise ValidationError("source pin commit must be a full lowercase hexadecimal SHA")
    for label in ("Snapshot at (UTC)", "Repository provenance observed at (UTC)"):
        value = _metadata_value(lines, label)
        if not UTC_PATTERN.fullmatch(value):
            raise ValidationError(f"{label} must be an explicit UTC timestamp ending in Z")
    migration_values = {
        "Current migrations": {"accounts": 12, "courses": 1, "data": 5},
        "Pinned CMP migrations": {"accounts": 10, "courses": 40, "data": 5},
    }
    for label, expected_counts in migration_values.items():
        value = _metadata_value(lines, label)
        actual: dict[str, int] = {}
        for item in value.split(";"):
            app, separator, count = item.strip().partition("=")
            if not separator or not count.isdigit():
                raise ValidationError(f"{label} must use app=count pairs: {value!r}")
            actual[app] = int(count)
        if actual != expected_counts:
            raise ValidationError(f"{label} mismatch: expected {expected_counts}, got {actual}")


def _repository_path_from_link(target: str, audit_path: Path) -> Path:
    parsed = urlsplit(target)
    if parsed.scheme or parsed.netloc:
        raise ValidationError(f"evidence link must be a repository path: {target!r}")
    relative = unquote(parsed.path)
    if not relative:
        raise ValidationError(f"evidence link has no path: {target!r}")
    root = Path.cwd().resolve()
    resolved = (audit_path.parent / relative).resolve()
    try:
        resolved.relative_to(root)
    except ValueError as exc:
        raise ValidationError(f"evidence link escapes the repository: {target!r}") from exc
    return resolved


def _validate_evidence(cell: str, audit_path: Path, *, minimum: int = 1) -> None:
    links = [match.group("target") for match in LINK_PATTERN.finditer(cell)]
    if len(links) < minimum:
        raise ValidationError(f"evidence cell needs at least {minimum} repository links")
    for target in links:
        path = _repository_path_from_link(target, audit_path)
        if not path.is_file():
            raise ValidationError(f"evidence path does not exist: {target!r}")


def _validate_handoff(cell: str, *, required: bool = True) -> None:
    references = {int(match.group("number")) for match in ISSUE_REFERENCE_PATTERN.finditer(cell)}
    if required and not references:
        raise ValidationError("every boundary/model relationship needs an explicit hand-off")
    unknown = references - ISSUE_HANDOFFS
    if unknown:
        raise ValidationError(f"unknown hand-off issue(s): {sorted(unknown)}")


def _validate_model_rows(lines: list[str], audit_path: Path) -> None:
    rows = _table(lines, "## Model/table inventory", MODEL_COLUMNS)
    if len(rows) != len(MODEL_SPECS):
        raise ValidationError(
            f"model inventory must contain {len(MODEL_SPECS)} rows, found {len(rows)}"
        )
    keys = [row[0] for row in rows]
    if len(keys) != len(set(keys)):
        raise ValidationError("model inventory contains duplicate keys")
    if set(keys) != set(MODEL_SPECS):
        raise ValidationError("model inventory has unknown or missing model keys")
    for row in rows:
        (
            key,
            app,
            model,
            table,
            source_path,
            migration,
            provenance,
            owner,
            classification,
            evidence,
        ) = row
        spec = MODEL_SPECS[key]
        if key != f"{app}.{model}":
            raise ValidationError(f"model key does not match app/class: {key!r}")
        if table != spec.table or source_path != spec.source_path:
            raise ValidationError(f"model declaration drift for {key}")
        if provenance != spec.provenance or owner != spec.owner:
            raise ValidationError(f"model provenance/owner drift for {key}")
        if classification != spec.classification or classification not in CLASSIFICATIONS:
            raise ValidationError(f"invalid model classification for {key}")
        if not migration or not re.search(
            r"(?:accounts|courses|data)/migrations/\d{4}_[^ ]+\.py", migration
        ):
            raise ValidationError(f"missing migration provenance for {key}")
        if key.startswith("courses.") and "courses/migrations/0001_initial.py" not in migration:
            raise ValidationError(f"phase-1 courses model must use the squashed migration: {key}")
        _validate_evidence(evidence, audit_path, minimum=2)
        _validate_handoff(evidence, required=False)


def _validate_schema_assertions(lines: list[str], audit_path: Path) -> None:
    rows = _table(lines, "## Phase-1 schema assertions", SCHEMA_ASSERTION_COLUMNS)
    if len(rows) != 1:
        raise ValidationError("phase-1 schema assertions must contain exactly one row")
    model, field, declaration_evidence, migration_evidence = rows[0]
    if (model, field) != ("courses.Cohort", "outcome"):
        raise ValidationError("phase-1 schema assertion must record courses.Cohort.outcome")
    _validate_evidence(declaration_evidence, audit_path)
    _validate_evidence(migration_evidence, audit_path)


def _validate_phase1_schema_source() -> None:
    root = Path.cwd()
    try:
        cohort_source = (root / "courses/models/cohort.py").read_text(encoding="utf-8")
        migration_source = (root / "courses/migrations/0001_initial.py").read_text(
            encoding="utf-8"
        )
    except OSError as exc:
        raise ValidationError("phase-1 Cohort schema evidence is missing") from exc
    if not re.search(r"^class Cohort\(models\.Model\):", cohort_source, re.MULTILINE):
        raise ValidationError("phase-1 Cohort model declaration is missing")
    if not re.search(
        r"^\s+outcome\s*=\s*models\.TextField\(blank=True\)",
        cohort_source,
        re.MULTILINE,
    ):
        raise ValidationError("phase-1 Cohort.outcome declaration is missing")
    if not re.search(r"name=['\"]Cohort['\"]", migration_source):
        raise ValidationError("phase-1 Cohort migration operation is missing")
    if not re.search(r"\(['\"]outcome['\"],\s*models\.TextField\(blank=True\)", migration_source):
        raise ValidationError("phase-1 Cohort.outcome migration field is missing")


def _validate_migration_rows(lines: list[str], audit_path: Path) -> None:
    rows = _table(lines, "## Migration baselines", MIGRATION_COLUMNS)
    expected = {"accounts": (12, 10), "courses": (1, 40), "data": (5, 5)}
    if len(rows) != len(expected):
        raise ValidationError(
            "migration baseline must contain exactly accounts, courses, and data rows"
        )
    seen: set[str] = set()
    for app, current, pinned, current_evidence, pinned_evidence, distinction in rows:
        if app in seen or app not in expected:
            raise ValidationError(f"unknown or duplicate migration app: {app!r}")
        seen.add(app)
        expected_current, expected_pinned = expected[app]
        if (current, pinned) != (str(expected_current), str(expected_pinned)):
            raise ValidationError(f"migration count drift for {app}")
        _validate_evidence(current_evidence, audit_path)
        _validate_evidence(pinned_evidence, audit_path)
        if not distinction:
            raise ValidationError(f"missing migration distinction for {app}")
    if seen != set(expected):
        raise ValidationError("migration baseline app coverage mismatch")


def _validate_relationship_rows(lines: list[str], audit_path: Path) -> None:
    rows = _table(lines, "## Relationship edges", RELATIONSHIP_COLUMNS)
    if len(rows) != len(RELATIONSHIP_KEYS):
        raise ValidationError(f"relationship inventory must contain {len(RELATIONSHIP_KEYS)} rows")
    keys = [row[0] for row in rows]
    if len(keys) != len(set(keys)):
        raise ValidationError("relationship inventory contains duplicate keys")
    if set(keys) != set(RELATIONSHIP_KEYS):
        raise ValidationError("relationship inventory has unknown or missing edges")
    for key, declaring_field, kind, target, classification, owner, evidence, handoff in rows:
        if declaring_field != key:
            raise ValidationError(f"relationship declaring field must equal its key: {key}")
        if kind not in RELATIONSHIP_KINDS or not target:
            raise ValidationError(f"invalid relationship kind/target for {key}")
        if classification not in CLASSIFICATIONS:
            raise ValidationError(f"invalid relationship classification for {key}")
        if not owner or not any(value in owner for value in PROVENANCES):
            raise ValidationError(f"relationship owner/provenance missing for {key}")
        _validate_evidence(evidence, audit_path)
        _validate_handoff(handoff)


def _validate_boundary_rows(lines: list[str], audit_path: Path) -> None:
    rows = _table(lines, "## Export and compatibility boundaries", BOUNDARY_COLUMNS)
    if len(rows) != len(BOUNDARY_KEYS):
        raise ValidationError(f"boundary inventory must contain {len(BOUNDARY_KEYS)} rows")
    keys = [row[0] for row in rows]
    if len(keys) != len(set(keys)):
        raise ValidationError("boundary inventory contains duplicate keys")
    if set(keys) != set(BOUNDARY_KEYS):
        raise ValidationError("boundary inventory has unknown or missing boundaries")
    for key, boundary, classification, owner, evidence, handoff in rows:
        if not boundary or classification not in CLASSIFICATIONS:
            raise ValidationError(f"invalid boundary classification/description for {key}")
        if not owner or not any(value in owner for value in PROVENANCES):
            raise ValidationError(f"boundary owner/provenance missing for {key}")
        _validate_evidence(evidence, audit_path)
        _validate_handoff(handoff)


def _validate_handoff_rows(lines: list[str], audit_path: Path) -> None:
    rows = _table(lines, "## Unresolved policy hand-offs", HANDOFF_COLUMNS)
    if len(rows) != len(HANDOFF_KEYS):
        raise ValidationError("unresolved hand-off table must contain exactly six rows")
    seen: set[int] = set()
    for issue, state, contract, evidence in rows:
        issue_links = [match.group("target") for match in LINK_PATTERN.finditer(issue)]
        match = ISSUE_URL_PATTERN.fullmatch(issue_links[0]) if len(issue_links) == 1 else None
        if match is None:
            raise ValidationError(f"invalid hand-off issue link: {issue!r}")
        number = int(match.group("number"))
        if number in seen or number not in HANDOFF_KEYS:
            raise ValidationError(f"unknown or duplicate unresolved hand-off: #{number}")
        seen.add(number)
        if state != "OPEN" or not contract:
            raise ValidationError(f"unresolved hand-off #{number} must remain OPEN and explicit")
        _validate_evidence(evidence, audit_path)
    if seen != HANDOFF_KEYS:
        raise ValidationError("unresolved hand-off coverage mismatch")


def _validate_related_links(text: str) -> None:
    for number in (30, 34, 150):
        if f"https://github.com/{REPOSITORY}/issues/{number}" not in text:
            raise ValidationError(f"related evidence link for #{number} is missing")


def _validate_repository_baselines() -> None:
    root = Path.cwd()
    if not (root / SOURCE_PIN_PATH).is_file():
        raise ValidationError(f"source pin is missing: {SOURCE_PIN_PATH}")
    if not (root / COPIED_MANIFEST_PATH).is_file():
        raise ValidationError(f"copied manifest is missing: {COPIED_MANIFEST_PATH}")
    try:
        source_pin = json.loads((root / SOURCE_PIN_PATH).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValidationError(f"source pin is not valid JSON: {SOURCE_PIN_PATH}") from exc
    if source_pin.get("source_commit") != SOURCE_PIN_COMMIT:
        raise ValidationError("checked-in source pin commit does not match the captured source")
    expected_current = {"accounts": 12, "courses": 1, "data": 5}
    for app, expected in expected_current.items():
        actual = len(list((root / app / "migrations").glob("[0-9]*.py")))
        if actual != expected:
            raise ValidationError(f"current {app} migration count is {actual}, expected {expected}")
    manifest_counts = {app: 0 for app in expected_current}
    with (root / COPIED_MANIFEST_PATH).open(newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle, delimiter="\t"):
            source_path = row.get("source_path", "")
            match = re.match(r"^(accounts|courses|data)/migrations/\d{4}_[^/]+\.py$", source_path)
            if match:
                manifest_counts[match.group(1)] += 1
    expected_pinned = {"accounts": 10, "courses": 40, "data": 5}
    if manifest_counts != expected_pinned:
        raise ValidationError(f"pinned migration manifest counts drifted: {manifest_counts}")


def validate_text(text: str, path: Path = AUDIT_PATH) -> None:
    """Validate inventory *text* using *path* as the evidence anchor."""

    lines = text.splitlines()
    _validate_metadata(lines)
    _validate_repository_baselines()
    _validate_phase1_schema_source()
    _validate_model_rows(lines, path)
    _validate_migration_rows(lines, path)
    _validate_schema_assertions(lines, path)
    _validate_relationship_rows(lines, path)
    _validate_boundary_rows(lines, path)
    _validate_handoff_rows(lines, path)
    _validate_related_links(text)


def validate(path: Path = AUDIT_PATH) -> None:
    """Validate the checked-in inventory."""

    if not path.is_file():
        raise ValidationError(f"audit file does not exist: {path}")
    validate_text(path.read_text(encoding="utf-8"), path)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("path", nargs="?", type=Path, default=AUDIT_PATH)
    args = parser.parse_args(argv)
    try:
        validate(args.path)
    except ValidationError as exc:
        print(f"course-platform schema inventory validation failed: {exc}", file=sys.stderr)
        return 1
    print(f"validated {args.path}: 32 models, Cohort.outcome, 44 relationships, 17 boundaries")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
