"""Reconcile issued CMP certificates after learner-history import completion.

The learner-history importer is intentionally resumable: completed tables and
already-claimed source rows are skipped.  That makes changing attachment logic
safe for future imports, but it also means the change cannot repair enrollment
rows claimed by an earlier run.  This service is the bounded repair path.

It accepts only the exact CMP export recorded by the learner-history binding,
uses only existing enrollment claims, and updates only non-blank certificate
URLs for one explicitly named source/target cohort pair.  Reports contain
counts only; learner identifiers and URLs never leave the service.
"""

from __future__ import annotations

import hashlib
import sqlite3
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import NoReturn

from django.db import transaction

from courses.models import Cohort, Enrollment
from courses.models.cmp_import import CmpHistoryClaim, CmpHistoryImportBinding
from courses.services.cmp_learner_history_import import BINDING_KIND


class CmpCertificateReconciliationError(RuntimeError):
    """A bounded refusal code safe to include in an operations log."""


@dataclass(frozen=True, slots=True)
class CmpCertificateReconciliationResult:
    source_certificates: int
    claims_matched: int
    targets_matched: int
    already_current: int
    updates_required: int
    updated: int
    applied: bool

    def summary(self) -> dict[str, int | bool]:
        return asdict(self)


def _refuse(code: str) -> NoReturn:
    raise CmpCertificateReconciliationError(code)


def _source_digest(path: Path) -> str:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
    except OSError:
        _refuse("source-unreadable")
    return digest.hexdigest()


def _refuse_source_sidecars(path: Path) -> None:
    """Require one standalone SQLite file, not a live WAL-backed database.

    Hashing only the main file while SQLite reads committed pages from a WAL
    would validate different bytes from the ones supplying certificate URLs.
    An immutable connection is safe only after all journal sidecars are absent.
    """

    sidecars = (
        Path(f"{path}-wal"),
        Path(f"{path}-shm"),
        Path(f"{path}-journal"),
    )
    if any(sidecar.exists() for sidecar in sidecars):
        _refuse("source-not-standalone")


def _bound_source(path: Path) -> tuple[int, str]:
    _refuse_source_sidecars(path)
    digest = _source_digest(path)
    try:
        binding = CmpHistoryImportBinding.objects.get(kind=BINDING_KIND)
    except CmpHistoryImportBinding.DoesNotExist:
        _refuse("learner-history-binding-missing")
    if binding.source_sha256 != digest:
        _refuse("source-digest-mismatch")
    return binding.pk, digest


def _issued_certificates(path: Path, source_course_slug: str) -> dict[int, str]:
    _refuse_source_sidecars(path)
    try:
        connection = sqlite3.connect(f"{path.as_uri()}?mode=ro&immutable=1", uri=True)
    except sqlite3.Error:
        _refuse("source-unreadable")
    try:
        integrity = connection.execute("PRAGMA integrity_check").fetchone()
        if integrity != ("ok",):
            _refuse("source-integrity-invalid")
        courses = connection.execute(
            "SELECT id FROM courses_course WHERE slug = ?", (source_course_slug,)
        ).fetchall()
        if not courses:
            _refuse("source-course-missing")
        if len(courses) != 1:
            _refuse("source-course-ambiguous")
        rows = connection.execute(
            "SELECT id, certificate_url FROM courses_enrollment WHERE course_id = ?",
            (courses[0][0],),
        ).fetchall()
    except sqlite3.Error:
        _refuse("source-schema-invalid")
    finally:
        connection.close()

    issued: dict[int, str] = {}
    max_length = Enrollment._meta.get_field("certificate_url").max_length
    for source_id, raw_url in rows:
        certificate_url = str(raw_url or "").strip()
        if not certificate_url:
            continue
        if max_length is not None and len(certificate_url) > max_length:
            _refuse("source-certificate-url-invalid")
        issued[int(source_id)] = certificate_url
    return issued


def _claimed_targets(issued: dict[int, str], *, for_update: bool = False) -> dict[int, int]:
    queryset = CmpHistoryClaim.objects.filter(
        table="courses_enrollment",
        source_id__in=issued,
    )
    if for_update:
        queryset = queryset.select_for_update()
    claims = dict(queryset.values_list("source_id", "target_id"))
    if len(claims) != len(issued):
        _refuse("enrollment-claim-gap")
    return claims


def _urls_by_target(issued: dict[int, str], claims: dict[int, int]) -> dict[int, str]:
    urls: dict[int, str] = {}
    for source_id, source_url in issued.items():
        target_id = claims[source_id]
        existing = urls.get(target_id)
        if existing is not None and existing != source_url:
            _refuse("conflicting-target-certificates")
        urls[target_id] = source_url
    return urls


def _target_cohort(slug: str) -> Cohort:
    try:
        return Cohort.objects.get(slug=slug)
    except Cohort.DoesNotExist:
        _refuse("target-cohort-missing")
    except Cohort.MultipleObjectsReturned:
        _refuse("target-cohort-ambiguous")


def _locked_apply(
    *,
    expected_binding_id: int,
    expected_digest: str,
    expected_claims: dict[int, int],
    expected_cohort_id: int,
    target_cohort_slug: str,
    issued: dict[int, str],
) -> tuple[int, int, int]:
    try:
        binding = CmpHistoryImportBinding.objects.select_for_update().get(kind=BINDING_KIND)
    except CmpHistoryImportBinding.DoesNotExist:
        _refuse("learner-history-binding-drift")
    if binding.pk != expected_binding_id or binding.source_sha256 != expected_digest:
        _refuse("learner-history-binding-drift")

    try:
        cohort = Cohort.objects.select_for_update().get(slug=target_cohort_slug)
    except Cohort.DoesNotExist:
        _refuse("target-cohort-drift")
    except Cohort.MultipleObjectsReturned:
        _refuse("target-cohort-drift")
    if cohort.pk != expected_cohort_id:
        _refuse("target-cohort-drift")

    claims = _claimed_targets(issued, for_update=True)
    if claims != expected_claims:
        _refuse("enrollment-claim-drift")
    urls_by_target = _urls_by_target(issued, claims)
    return _reconcile_targets(
        cohort=cohort,
        urls_by_target=urls_by_target,
        apply=True,
    )


def _reconcile_targets(
    *,
    cohort: Cohort,
    urls_by_target: dict[int, str],
    apply: bool,
) -> tuple[int, int, int]:
    queryset = Enrollment.objects.filter(pk__in=urls_by_target)
    if apply:
        queryset = queryset.select_for_update()
    targets = {enrollment.pk: enrollment for enrollment in queryset}
    if len(targets) != len(urls_by_target):
        _refuse("enrollment-target-gap")
    if any(enrollment.course_id != cohort.pk for enrollment in targets.values()):
        _refuse("target-cohort-mismatch")

    changed = []
    for target_id, source_url in urls_by_target.items():
        enrollment = targets[target_id]
        if enrollment.certificate_url != source_url:
            enrollment.certificate_url = source_url
            changed.append(enrollment)
    already_current = len(targets) - len(changed)
    if apply and changed:
        Enrollment.objects.bulk_update(changed, ("certificate_url",))
    return len(targets), already_current, len(changed)


def reconcile_cmp_enrollment_certificates(
    source: Path,
    *,
    source_course_slug: str,
    target_cohort_slug: str,
    apply: bool = False,
) -> CmpCertificateReconciliationResult:
    """Reconcile one cohort's non-blank issued certificate URLs through claims.

    Dry-run is the default.  Applying runs target validation and updates under
    one database transaction.  The source is hashed again immediately before
    that transaction so a file changed since initial validation is refused.
    """

    try:
        source = source.expanduser().resolve(strict=True)
    except OSError:
        _refuse("source-unavailable")
    if not source.is_file():
        _refuse("source-unavailable")

    expected_binding_id, expected_digest = _bound_source(source)
    issued = _issued_certificates(source, source_course_slug)
    claims = _claimed_targets(issued)
    urls_by_target = _urls_by_target(issued, claims)
    cohort = _target_cohort(target_cohort_slug)

    if not apply:
        matched, already_current, updates_required = _reconcile_targets(
            cohort=cohort,
            urls_by_target=urls_by_target,
            apply=False,
        )
        return CmpCertificateReconciliationResult(
            source_certificates=len(issued),
            claims_matched=len(claims),
            targets_matched=matched,
            already_current=already_current,
            updates_required=updates_required,
            updated=0,
            applied=False,
        )

    _refuse_source_sidecars(source)
    if _source_digest(source) != expected_digest:
        _refuse("source-changed-during-reconciliation")
    with transaction.atomic():
        matched, already_current, updates_required = _locked_apply(
            expected_binding_id=expected_binding_id,
            expected_digest=expected_digest,
            expected_claims=claims,
            expected_cohort_id=cohort.pk,
            target_cohort_slug=target_cohort_slug,
            issued=issued,
        )
    return CmpCertificateReconciliationResult(
        source_certificates=len(issued),
        claims_matched=len(claims),
        targets_matched=matched,
        already_current=already_current,
        updates_required=updates_required,
        updated=updates_required,
        applied=True,
    )
