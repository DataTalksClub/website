from __future__ import annotations

import hashlib
import math
import uuid
from collections.abc import Callable

from django.db import DatabaseError, transaction
from django.db.models import Sum
from django.utils import timezone

from core.idempotency import acquire_transaction_lock

from .constants import (
    ADAPTIVE_RATE_LIMIT,
    MAX_RATE_COST,
    MIN_RATE_COST,
    RATE_WINDOW,
    READ_RATE_LIMIT,
    WRITE_RATE_LIMIT,
)
from .models import APIPrincipal, APIRateAdmission


class RateLimitExceeded(RuntimeError):
    def __init__(self, retry_after: int) -> None:
        self.retry_after = max(1, min(60, retry_after))
        super().__init__("management API rate limit exceeded")


class RateLimitUnavailable(RuntimeError):
    pass


def _subject_hash(kind: str, subject: str) -> str:
    digest = hashlib.sha256()
    digest.update(b"dtc:management-rate-subject:v1\0")
    digest.update(kind.encode("ascii"))
    digest.update(b"\0")
    digest.update(subject.encode("ascii"))
    return digest.hexdigest()


def admit(
    *,
    cost_class: str,
    cost: int,
    principal: APIPrincipal | None = None,
    invalid_prefix: str | None = None,
    now=None,
    using: str = "default",
) -> None:
    if not MIN_RATE_COST <= cost <= MAX_RATE_COST:
        raise ValueError("rate cost must be between 1 and 10")
    if (principal is None) == (invalid_prefix is None):
        raise ValueError("exactly one rate subject is required")
    if cost_class == APIRateAdmission.CostClass.READ:
        limit = READ_RATE_LIMIT
    elif cost_class == APIRateAdmission.CostClass.WRITE:
        limit = WRITE_RATE_LIMIT
    elif cost_class == APIRateAdmission.CostClass.ADAPTIVE:
        limit = ADAPTIVE_RATE_LIMIT
    else:
        raise ValueError("unknown rate cost class")

    if principal is not None:
        subject = str(principal.id)
        subject_hash = _subject_hash("principal", subject)
    else:
        subject = invalid_prefix or ""
        subject_hash = _subject_hash("invalid-prefix", subject)
    admitted_at = now or timezone.now()
    cutoff = admitted_at - RATE_WINDOW

    try:
        with transaction.atomic(using=using):
            acquire_transaction_lock(
                "management-rate",
                f"{subject_hash}:{cost_class}",
                using=using,
            )
            current = (
                APIRateAdmission.objects.using(using)
                .filter(
                    subject_hash=subject_hash,
                    cost_class=cost_class,
                    created_at__gt=cutoff,
                )
                .aggregate(total=Sum("cost"))["total"]
                or 0
            )
            if current + cost > limit:
                oldest = (
                    APIRateAdmission.objects.using(using)
                    .filter(
                        subject_hash=subject_hash,
                        cost_class=cost_class,
                        created_at__gt=cutoff,
                    )
                    .order_by("created_at")
                    .values_list("created_at", flat=True)
                    .first()
                )
                retry_after = 60
                if oldest is not None:
                    retry_after = math.ceil((oldest + RATE_WINDOW - admitted_at).total_seconds())
                raise RateLimitExceeded(retry_after)
            APIRateAdmission.objects.using(using).create(
                principal=principal,
                subject_hash=subject_hash,
                cost_class=cost_class,
                cost=cost,
                created_at=admitted_at,
            )
    except RateLimitExceeded:
        raise
    except DatabaseError as error:
        raise RateLimitUnavailable("management API rate storage is unavailable") from error


def principal_subject_hash(principal_id: uuid.UUID) -> str:
    return _subject_hash("principal", str(principal_id))


def verify_with_adaptive_limit(
    *,
    prefix: str,
    verifier: Callable[[], bool],
    now=None,
    using: str = "default",
) -> bool:
    admitted_at = now or timezone.now()
    cutoff = admitted_at - RATE_WINDOW
    subject_hash = _subject_hash("invalid-prefix", prefix)
    try:
        with transaction.atomic(using=using):
            acquire_transaction_lock(
                "management-rate",
                f"{subject_hash}:{APIRateAdmission.CostClass.ADAPTIVE}",
                using=using,
            )
            failures = (
                APIRateAdmission.objects.using(using)
                .filter(
                    subject_hash=subject_hash,
                    cost_class=APIRateAdmission.CostClass.ADAPTIVE,
                    created_at__gt=cutoff,
                )
                .aggregate(total=Sum("cost"))["total"]
                or 0
            )
            if failures >= ADAPTIVE_RATE_LIMIT:
                oldest = (
                    APIRateAdmission.objects.using(using)
                    .filter(
                        subject_hash=subject_hash,
                        cost_class=APIRateAdmission.CostClass.ADAPTIVE,
                        created_at__gt=cutoff,
                    )
                    .order_by("created_at")
                    .values_list("created_at", flat=True)
                    .first()
                )
                retry_after = 60
                if oldest is not None:
                    retry_after = math.ceil((oldest + RATE_WINDOW - admitted_at).total_seconds())
                raise RateLimitExceeded(retry_after)
            verified = verifier()
            if not verified:
                APIRateAdmission.objects.using(using).create(
                    subject_hash=subject_hash,
                    cost_class=APIRateAdmission.CostClass.ADAPTIVE,
                    cost=1,
                    created_at=admitted_at,
                )
            return verified
    except RateLimitExceeded:
        raise
    except DatabaseError as error:
        raise RateLimitUnavailable("management API rate storage is unavailable") from error
