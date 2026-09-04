"""Registering a course repository as a content source.

Which repositories exist is a database question: the enabled ``ContentSource``
rows with the course-repository adapter type are the answer, and both routes
into the curriculum tables -- the signed push webhook and
``scripts/prod/sync_course_repositories.py`` -- read exactly those rows.

A database that has never been registered has no rows, so this module also owns
the pinned registration *input* at ``content_sync/course_repository_sources.json``.
That file is not a second source of truth: it is how a fresh database gets its
first rows, the same relationship ``scripts/production_like_course_specs.json``
has with the seeded course catalogue.  Registering is idempotent and never
rewrites an existing row, because a registered source's repository identity is
something an operator changed on purpose.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from content.models import ContentSource
from content.services import CreateContentSource, create_content_source
from content_sync.course_repository_webhook import COURSE_REPOSITORY_ADAPTER_TYPE
from core.context import new_context_id
from core.services import ServiceContext

REGISTRATION_INPUT_PATH = Path(__file__).resolve().parent / "course_repository_sources.json"
REGISTRATION_SCHEMA_VERSION = 1

#: The paths a course repository may publish to this site.  One list, so a file
#: the push route would admit is a file the pull route admits.
COURSE_REPOSITORY_PATH_ALLOWLIST = (
    "course.yaml",
    "cohorts/**",
    "**/module.yaml",
    "**/*.md",
)

DEFAULT_MAX_FILES = 5_000
DEFAULT_MAX_BYTES = 100_000_000

_RECORD_FIELDS = frozenset(
    {"stable_id", "display_name", "repository_owner", "repository_name", "branch"}
)


class CourseRepositoryRegistrationError(RuntimeError):
    """A bounded refusal to register a course repository."""


@dataclass(frozen=True, slots=True)
class CourseRepositoryRegistration:
    stable_id: str
    display_name: str
    repository_owner: str
    repository_name: str
    branch: str = "main"
    max_files: int = DEFAULT_MAX_FILES
    max_bytes: int = DEFAULT_MAX_BYTES
    enabled: bool = True


def load_registration_input(
    path: Path | None = None,
) -> tuple[CourseRepositoryRegistration, ...]:
    """Read the pinned registration input, refusing anything unexpected."""

    source_path = path or REGISTRATION_INPUT_PATH
    try:
        payload = json.loads(source_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise CourseRepositoryRegistrationError("registration_input_unreadable") from error
    if (
        not isinstance(payload, dict)
        or payload.get("schema_version") != REGISTRATION_SCHEMA_VERSION
        or not isinstance(payload.get("sources"), list)
    ):
        raise CourseRepositoryRegistrationError("registration_input_invalid")
    records: list[CourseRepositoryRegistration] = []
    seen: set[str] = set()
    for raw in payload["sources"]:
        if not isinstance(raw, dict) or set(raw) != _RECORD_FIELDS:
            raise CourseRepositoryRegistrationError("registration_record_invalid")
        if any(not isinstance(value, str) or not value for value in raw.values()):
            raise CourseRepositoryRegistrationError("registration_record_invalid")
        if raw["stable_id"] in seen:
            raise CourseRepositoryRegistrationError("registration_stable_id_repeated")
        seen.add(raw["stable_id"])
        records.append(CourseRepositoryRegistration(**raw))
    if not records:
        raise CourseRepositoryRegistrationError("registration_input_empty")
    return tuple(records)


def register_course_repository(registration: CourseRepositoryRegistration) -> ContentSource:
    """Create one course-repository ``ContentSource`` row."""

    return create_content_source(
        CreateContentSource(
            stable_id=registration.stable_id,
            display_name=registration.display_name,
            repository_owner=registration.repository_owner,
            repository_name=registration.repository_name,
            branch=registration.branch,
            path_allowlist=COURSE_REPOSITORY_PATH_ALLOWLIST,
            adapter_type=COURSE_REPOSITORY_ADAPTER_TYPE,
            mount_path="/",
            enabled=registration.enabled,
            max_files=registration.max_files,
            max_bytes=registration.max_bytes,
        ),
        context=ServiceContext(
            correlation_id=new_context_id(),
            actor_ref="management:course-sync",
        ),
    )


def seed_course_repository_sources(
    registrations: tuple[CourseRepositoryRegistration, ...],
) -> list[dict[str, object]]:
    """Register every missing source and leave every existing one alone."""

    existing = {
        source.stable_id: source
        for source in ContentSource.objects.filter(
            stable_id__in=[registration.stable_id for registration in registrations]
        )
    }
    report: list[dict[str, object]] = []
    for registration in registrations:
        source = existing.get(registration.stable_id)
        created = source is None
        if source is None:
            source = register_course_repository(registration)
        report.append(
            {
                "stable_id": source.stable_id,
                "repository": f"{source.repository_owner}/{source.repository_name}",
                "branch": source.branch,
                "enabled": source.enabled,
                "created": created,
            }
        )
    return report


__all__ = (
    "COURSE_REPOSITORY_PATH_ALLOWLIST",
    "CourseRepositoryRegistration",
    "CourseRepositoryRegistrationError",
    "REGISTRATION_INPUT_PATH",
    "load_registration_input",
    "register_course_repository",
    "seed_course_repository_sources",
)
