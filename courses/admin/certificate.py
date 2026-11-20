from django.contrib import admin
from unfold.admin import ModelAdmin  # type: ignore[import-untyped]

from courses.models.certificate import CertificateTemplate


@admin.register(CertificateTemplate)
class CertificateTemplateAdmin(ModelAdmin):
    """Per-course certificate rendering and publishing settings."""

    list_display = ("course", "banner_template", "banner_size", "s3_bucket")
    search_fields = ("course__slug", "course__title")
