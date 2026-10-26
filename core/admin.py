"""Admin visibility for background scoring/sync jobs.

Operators trigger scoring from Studio and Django admin; the request now only
enqueues a durable intent and returns. This read-only changelist shows each
intent's handler, status, attempts and update time so progress is visible
where the operator triggered it.
"""

from community_base.jobs.models import JobIntent
from django.contrib import admin


@admin.register(JobIntent)
class JobIntentAdmin(admin.ModelAdmin):
    list_display = ("short_id", "handler", "status", "attempts", "max_attempts", "updated_at")
    list_filter = ("handler", "status")
    search_fields = ("handler", "id")
    readonly_fields = (
        "id",
        "handler",
        "key_hash",
        "payload",
        "status",
        "attempts",
        "max_attempts",
        "available_at",
        "lease_expires_at",
        "correlation_id",
        "last_error",
        "created_at",
        "updated_at",
    )

    @admin.display(description="ID")
    def short_id(self, obj):
        return str(obj.id)[:8]

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
