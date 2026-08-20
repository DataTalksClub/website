from __future__ import annotations

import json

from django.conf import settings
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from content.models import ContentSource
from content_sync.course_repository_webhook import (
    COURSE_REPOSITORY_ADAPTER_TYPE,
    COURSE_REPOSITORY_JOB_HANDLER,
    COURSE_REPOSITORY_PARSER_VERSION,
    COURSE_REPOSITORY_WEBHOOK_NAMESPACE,
    CourseRepositoryWebhookError,
    parse_github_course_push,
)
from content_sync.webhook_delivery import (
    WebhookDeliveryConflict,
    WebhookDeliveryInProgress,
    WebhookDeliveryCommandFailed,
    fence_webhook_delivery,
    verify_webhook_signature,
)
from core.idempotency import hash_idempotency_key
from core.models import IdempotencyRecord
from jobs.dispatch import dispatch_after_commit


def _error(code: str, status: int) -> JsonResponse:
    return JsonResponse({"error": code}, status=status)


def _delivery_record_id(delivery_id: str):
    key_hash = hash_idempotency_key(COURSE_REPOSITORY_WEBHOOK_NAMESPACE, delivery_id)
    return IdempotencyRecord.objects.get(
        scope=COURSE_REPOSITORY_WEBHOOK_NAMESPACE,
        key_hash=key_hash,
    ).id


@csrf_exempt
@require_POST
def github_course_repository_webhook(request):
    secret = getattr(settings, "COURSE_REPOSITORY_WEBHOOK_SECRET", "")
    if not secret:
        return _error("course_repository_webhook_not_configured", 503)

    delivery_id = request.headers.get("X-GitHub-Delivery", "")
    signature = request.headers.get("X-Hub-Signature-256", "")
    event_type = request.headers.get("X-GitHub-Event", "")
    body = request.body
    try:
        body_sha256 = verify_webhook_signature(
            body=body,
            secret=secret,
            signature=signature,
        )
    except ValueError:
        return _error("github_signature_invalid", 401)

    try:
        payload = json.loads(body)
    except (TypeError, json.JSONDecodeError):
        return _error("github_payload_invalid", 400)
    try:
        push = parse_github_course_push(payload, event_type=event_type)
    except CourseRepositoryWebhookError as error:
        return _error(error.code, 400)

    source = (
        ContentSource.objects.filter(
            enabled=True,
            adapter_type=COURSE_REPOSITORY_ADAPTER_TYPE,
            repository_owner=push.owner,
            repository_name=push.repository,
            branch=push.branch,
        )
        .order_by("id")
        .first()
    )
    if source is None:
        return _error("course_repository_source_unregistered", 404)

    def enqueue_job() -> None:
        delivery_record_id = _delivery_record_id(delivery_id)
        dispatch_after_commit(
            handler=COURSE_REPOSITORY_JOB_HANDLER,
            deduplication_key=(
                f"course-source:{source.id}:{push.commit_sha}:{COURSE_REPOSITORY_PARSER_VERSION}"
            ),
            payload={
                "source_uuid": str(source.id),
                "commit_sha": push.commit_sha,
                "delivery_record_id": str(delivery_record_id),
            },
        )

    try:
        result = fence_webhook_delivery(
            namespace=COURSE_REPOSITORY_WEBHOOK_NAMESPACE,
            delivery_id=delivery_id,
            body_sha256=body_sha256,
            command=enqueue_job,
        )
    except WebhookDeliveryConflict:
        return _error("github_delivery_conflict", 409)
    except WebhookDeliveryInProgress:
        return _error("github_delivery_in_progress", 503)
    except WebhookDeliveryCommandFailed:
        return _error("course_repository_enqueue_failed", 503)
    except ValueError:
        return _error("github_delivery_invalid", 400)

    return JsonResponse(
        {
            "outcome": result.outcome,
            "delivery_record_id": str(result.record_id),
        },
        status=202,
    )
