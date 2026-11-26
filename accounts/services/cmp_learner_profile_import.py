"""Carry CMP learner-profile data onto ``LearnerProfile``.

``import_cmp_learners`` created the accounts and copied the CMP user's own
profile columns once.  It never saw the work data CMP keeps per registration
(``courses_courseregistration``: company, years of experience, the role and
name last typed, LinkedIn/GitHub), and CMP has no profile timestamp at all.
This reconciler fills both in, so an imported member neither refills a form CMP
already had nor meets the "still up to date?" review for a profile they
confirmed at a recent CMP registration.

Source facts:

* the CMP user row: certificate name, country, region, registration role,
  GitHub/LinkedIn/website URLs, about me;
* the learner's **latest** CMP registration (by ``updated_at``): company,
  years of experience, role, name, country/region, LinkedIn/GitHub.  On CMP a
  registration prefilled from the profile and wrote back to it, so its time is
  the last moment the learner confirmed those values -- it becomes
  ``profile_confirmed_at``.  A learner with no CMP registration keeps
  ``profile_confirmed_at`` empty (the review step names the old platform).

Rules:

* **Identity only.** A CMP row reaches an account through ``CmpLearnerClaim``
  (CMP user id -> website user id) and nothing else: never by name, never by
  email.  A registration without a CMP user id, or a CMP user without a claim,
  is counted and skipped.
* **The website wins once the learner has used it.**  A profile with
  ``profile_confirmed_at`` newer than the CMP evidence was saved or confirmed
  on this site and is left untouched.
* **Fill blanks only.**  A CMP value lands only where the profile field is
  empty; a stored value is never overwritten and never blanked.
* **Idempotent; dry-run by default.**  A second ``apply`` updates nothing.
  The source is opened read-only and immutable.

The summary carries counts only, never a payload or an id.
"""

from __future__ import annotations

import sqlite3
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

from django.db import transaction
from django.utils.dateparse import parse_datetime

from accounts.models import CmpLearnerClaim
from courses.models.learner_profile import YEARS_EXPERIENCE_CHOICES, LearnerProfile

USER_COLUMNS = (
    "certificate_name",
    "country",
    "region",
    "registration_role",
    "github_url",
    "linkedin_url",
    "personal_website_url",
    "about_me",
)
#: CMP registration column -> LearnerProfile field.
REGISTRATION_COLUMNS = {
    "company_name": "company_name",
    "years_experience": "years_experience",
    "role": "registration_role",
    "name": "certificate_name",
    "country": "country",
    "region": "region",
    "linkedin_url": "linkedin_url",
    "github_url": "github_url",
}
PROFILE_FIELDS = tuple(
    dict.fromkeys((*USER_COLUMNS, *REGISTRATION_COLUMNS.values(), "profile_confirmed_at"))
)
_YEARS = {value for value, _label in YEARS_EXPERIENCE_CHOICES}
_MAX_LENGTHS = {
    name: getattr(LearnerProfile._meta.get_field(name), "max_length", None)
    for name in PROFILE_FIELDS
    if getattr(LearnerProfile._meta.get_field(name), "max_length", None)
}


class CmpLearnerProfileImportError(RuntimeError):
    pass


@dataclass
class CmpLearnerProfileImportResult:
    applied: bool
    source_users: int = 0
    source_registrations: int = 0
    claimed_users: int = 0
    unclaimed_users: int = 0
    registrations_without_user: int = 0
    profiles_created: int = 0
    profiles_updated: int = 0
    profiles_unchanged: int = 0
    profiles_newer_on_site: int = 0
    confirmed_at_set: int = 0
    fields_filled: Counter = field(default_factory=Counter)

    def summary(self) -> dict:
        data = {key: value for key, value in self.__dict__.items() if key != "fields_filled"}
        data["fields_filled"] = dict(sorted(self.fields_filled.items()))
        return data


def _connect(path: Path) -> sqlite3.Connection:
    if not path.is_file():
        raise CmpLearnerProfileImportError("source-missing")
    for suffix in ("-wal", "-shm", "-journal"):
        if Path(f"{path}{suffix}").exists():
            raise CmpLearnerProfileImportError("source-not-standalone")
    connection = sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro&immutable=1", uri=True)
    connection.row_factory = sqlite3.Row
    return connection


def _columns(connection: sqlite3.Connection, table: str) -> set[str]:
    cursor = connection.execute(f'SELECT * FROM "{table}" LIMIT 0')
    return {column[0] for column in cursor.description}


def _clean(name: str, value):
    if value is None:
        return ""
    text = str(value).strip()
    if name == "years_experience" and text not in _YEARS:
        return ""
    limit = _MAX_LENGTHS.get(name)
    if limit and len(text) > limit:
        return ""
    return text


def _read_source(path: Path) -> tuple[dict[int, dict], dict[int, dict], int, int]:
    """CMP user facts and each user's latest registration facts."""

    connection = _connect(path)
    try:
        user_columns = _columns(connection, "accounts_customuser")
        wanted_users = [c for c in USER_COLUMNS if c in user_columns]
        users = {
            row["id"]: {name: _clean(name, row[name]) for name in wanted_users}
            for row in connection.execute(
                f"SELECT id, {', '.join(wanted_users)} FROM accounts_customuser"
            )
        }
        registration_columns = _columns(connection, "courses_courseregistration")
        wanted_registrations = [c for c in REGISTRATION_COLUMNS if c in registration_columns]
        latest: dict[int, dict] = {}
        registrations = 0
        without_user = 0
        query = (
            f"SELECT user_id, created_at, updated_at, {', '.join(wanted_registrations)} "
            "FROM courses_courseregistration ORDER BY id"
        )
        for row in connection.execute(query):
            registrations += 1
            if row["user_id"] is None:
                without_user += 1
                continue
            when = parse_datetime(row["updated_at"] or row["created_at"] or "")
            if when is None:
                continue
            current = latest.get(row["user_id"])
            if current is not None and current["at"] >= when:
                continue
            latest[row["user_id"]] = {
                "at": when,
                **{
                    REGISTRATION_COLUMNS[name]: _clean(REGISTRATION_COLUMNS[name], row[name])
                    for name in wanted_registrations
                },
            }
    finally:
        connection.close()
    return users, latest, registrations, without_user


def _desired_values(user_facts: dict, registration: dict | None) -> dict:
    """The CMP view of one learner's profile: registration over user row."""

    values = {name: value for name, value in user_facts.items() if value}
    if registration is not None:
        for name, value in registration.items():
            if name != "at" and value:
                values[name] = value
        values["profile_confirmed_at"] = registration["at"]
    return values


def import_cmp_learner_profiles(
    path: Path, *, apply: bool = False
) -> CmpLearnerProfileImportResult:
    users, latest, registration_count, without_user = _read_source(Path(path))
    result = CmpLearnerProfileImportResult(
        applied=apply,
        source_users=len(users),
        source_registrations=registration_count,
        registrations_without_user=without_user,
    )
    claims = dict(CmpLearnerClaim.objects.values_list("source_id", "user_id"))

    with transaction.atomic():
        profiles = {
            profile.user_id: profile
            for profile in LearnerProfile.objects.filter(user_id__in=list(claims.values()))
        }
        to_create: list[LearnerProfile] = []
        to_update: list[LearnerProfile] = []
        for source_id in sorted(users):
            user_id = claims.get(source_id)
            if user_id is None:
                result.unclaimed_users += 1
                continue
            result.claimed_users += 1
            desired = _desired_values(users[source_id], latest.get(source_id))
            existing = profiles.get(user_id)
            created = existing is None
            profile = existing if existing is not None else LearnerProfile(user_id=user_id)
            evidence_at = desired.get("profile_confirmed_at")
            if profile.profile_confirmed_at is not None and (
                evidence_at is None or profile.profile_confirmed_at > evidence_at
            ):
                result.profiles_newer_on_site += 1
                continue
            changed = False
            for name, value in desired.items():
                current = getattr(profile, name)
                if current == value:
                    continue
                if name != "profile_confirmed_at" and current not in (None, ""):
                    # Fill blanks only: a stored value may be the learner's
                    # own later edit on this site.
                    continue
                if name == "profile_confirmed_at":
                    result.confirmed_at_set += 1
                result.fields_filled[name] += 1
                setattr(profile, name, value)
                changed = True
            if created and changed:
                result.profiles_created += 1
                to_create.append(profile)
            elif changed:
                result.profiles_updated += 1
                to_update.append(profile)
            else:
                result.profiles_unchanged += 1
        if apply:
            LearnerProfile.objects.bulk_create(to_create, batch_size=500)
            LearnerProfile.objects.bulk_update(to_update, PROFILE_FIELDS, batch_size=500)
        else:
            transaction.set_rollback(True)
    return result
