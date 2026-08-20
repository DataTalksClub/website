from django.contrib import admin

from .models import (
    Event,
    EventQnaCohostInvite,
    EventQnaQuestion,
    EventQnaSession,
    EventQnaVote,
)


@admin.register(Event)
class EventAdmin(admin.ModelAdmin):
    list_display = ("public_id", "title", "lifecycle", "slug", "updated_at")
    list_filter = ("lifecycle",)
    search_fields = ("title", "slug", "source_key")
    readonly_fields = ("id", "public_id", "slug", "created_at", "updated_at")


@admin.register(EventQnaSession)
class EventQnaSessionAdmin(admin.ModelAdmin):
    list_display = ("event", "state", "q_total", "q_answered", "revision", "updated_at")
    list_filter = ("state", "backend_key")
    search_fields = ("event__title", "event__id")
    readonly_fields = ("id", "event", "revision", "created_at", "updated_at")


@admin.register(EventQnaQuestion)
class EventQnaQuestionAdmin(admin.ModelAdmin):
    list_display = ("question_id", "session", "status", "score", "pinned", "created_at")
    list_filter = ("status", "pinned")
    search_fields = ("question_id", "text")
    readonly_fields = ("question_id", "session", "participant_digest", "created_at", "answered_at")


@admin.register(EventQnaCohostInvite)
class EventQnaCohostInviteAdmin(admin.ModelAdmin):
    list_display = ("name", "session", "created_at", "revoked_at")
    list_filter = ("revoked_at",)
    search_fields = ("name", "invite_id")
    readonly_fields = (
        "invite_id",
        "session",
        "name",
        "passcode_digest",
        "created_by_ref",
        "created_at",
    )


@admin.register(EventQnaVote)
class EventQnaVoteAdmin(admin.ModelAdmin):
    list_display = ("question", "created_at")
    readonly_fields = ("question", "participant_digest", "created_at")
