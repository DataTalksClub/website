from django.contrib import admin

from .models import Event


@admin.register(Event)
class EventAdmin(admin.ModelAdmin):
    list_display = ("public_id", "title", "lifecycle", "slug", "updated_at")
    list_filter = ("lifecycle",)
    search_fields = ("title", "slug", "source_key")
    readonly_fields = ("id", "public_id", "slug", "created_at", "updated_at")
