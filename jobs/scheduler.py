from __future__ import annotations

import importlib
import uuid
from dataclasses import dataclass
from datetime import timedelta

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.db import DEFAULT_DB_ALIAS, transaction
from django_q.models import Schedule  # type: ignore[import-untyped]

from jobs.clock import database_now
from jobs.execution import _validate_lease_seconds, _validate_worker_id
from jobs.models import SchedulerLease
from jobs.registry import registered_schedules


@dataclass(frozen=True, slots=True)
class SchedulerClaim:
    owner_id: str
    lease_token: uuid.UUID


def require_non_owner_scheduler_disabled() -> None:
    if settings.Q_CLUSTER.get("scheduler") is not False:
        raise ImproperlyConfigured(
            "ordinary Django-Q clusters must set Q_CLUSTER scheduler to False"
        )


def acquire_scheduler_lease(
    owner_id: str,
    *,
    ttl_seconds: int = 90,
    using: str = DEFAULT_DB_ALIAS,
) -> SchedulerClaim | None:
    owner_id = _validate_worker_id(owner_id)
    ttl_seconds = _validate_lease_seconds(ttl_seconds)
    now = database_now(using=using)
    with transaction.atomic(using=using):
        SchedulerLease.objects.using(using).get_or_create(key=SchedulerLease.SINGLETON_KEY)
        lease = (
            SchedulerLease.objects.using(using)
            .select_for_update()
            .get(key=SchedulerLease.SINGLETON_KEY)
        )
        if (
            lease.lease_token is not None
            and lease.expires_at is not None
            and lease.expires_at > now
        ):
            return None
        token = uuid.uuid4()
        lease.owner_id = owner_id
        lease.lease_token = token
        lease.acquired_at = now
        lease.heartbeat_at = now
        lease.expires_at = now + timedelta(seconds=ttl_seconds)
        lease.save(
            update_fields=(
                "owner_id",
                "lease_token",
                "acquired_at",
                "heartbeat_at",
                "expires_at",
            )
        )
    return SchedulerClaim(owner_id=owner_id, lease_token=token)


def renew_scheduler_lease(
    claim: SchedulerClaim,
    *,
    ttl_seconds: int = 90,
    using: str = DEFAULT_DB_ALIAS,
) -> bool:
    ttl_seconds = _validate_lease_seconds(ttl_seconds)
    now = database_now(using=using)
    updated = (
        SchedulerLease.objects.using(using)
        .filter(
            key=SchedulerLease.SINGLETON_KEY,
            owner_id=claim.owner_id,
            lease_token=claim.lease_token,
            expires_at__gt=now,
        )
        .update(
            heartbeat_at=now,
            expires_at=now + timedelta(seconds=ttl_seconds),
        )
    )
    return updated == 1


def release_scheduler_lease(claim: SchedulerClaim, *, using: str = DEFAULT_DB_ALIAS) -> bool:
    updated = (
        SchedulerLease.objects.using(using)
        .filter(
            key=SchedulerLease.SINGLETON_KEY,
            owner_id=claim.owner_id,
            lease_token=claim.lease_token,
        )
        .update(
            owner_id="",
            lease_token=None,
            acquired_at=None,
            heartbeat_at=None,
            expires_at=None,
        )
    )
    return updated == 1


def register_code_schedules(claim: SchedulerClaim, *, using: str = DEFAULT_DB_ALIAS) -> int:
    importlib.import_module("jobs.schedules")
    now = database_now(using=using)
    with transaction.atomic(using=using):
        try:
            lease = (
                SchedulerLease.objects.using(using)
                .select_for_update()
                .get(
                    key=SchedulerLease.SINGLETON_KEY,
                    owner_id=claim.owner_id,
                    lease_token=claim.lease_token,
                    expires_at__gt=now,
                )
            )
        except SchedulerLease.DoesNotExist:
            return 0
        del lease
        count = 0
        for definition in registered_schedules():
            Schedule.objects.using(using).update_or_create(
                name=definition.key,
                defaults={
                    "func": definition.func,
                    "hook": None,
                    "args": repr(tuple(definition.args)),
                    "kwargs": repr(dict(definition.kwargs or {})),
                    "schedule_type": definition.schedule_type,
                    "minutes": definition.minutes,
                    "repeats": definition.repeats,
                    "cron": definition.cron,
                    "cluster": None,
                    "intended_date_kwarg": None,
                },
            )
            count += 1
    return count
