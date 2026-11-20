"""Per-course certificate template configuration.

One row per course family says how that course's certificates are rendered and
published.  The rendering engine is the Banner Generator
(``AI-Shipping-Labs/banner-generator``); ``banner_template`` names one of its
bundled certificate templates.  Everything that differs between two cohorts of
the same course (title, year, date) is derived from the cohort and its project
deadlines, never typed here.
"""

from __future__ import annotations

from zoneinfo import available_timezones

from django.core.exceptions import ValidationError
from django.db import models

from courses.models.cohort import Course

DEFAULT_CERTIFICATE_URL_BASE = "https://certificate.datatalks.club"
DEFAULT_CERTIFICATE_BUCKET = "certificate.datatalks.club"


class CertificateTemplate(models.Model):
    course = models.OneToOneField(
        Course, on_delete=models.CASCADE, related_name="certificate_template"
    )
    banner_template = models.SlugField(
        max_length=100,
        default="dtc-zoomcamp-certificate",
        help_text="Banner Generator template name, for example dtc-zoomcamp-certificate.",
    )
    banner_size = models.SlugField(
        max_length=50,
        default="zoomcamp-certificate",
        help_text="Banner Generator size key (zoomcamp-certificate is 1920x1080).",
    )
    url_base = models.URLField(
        default=DEFAULT_CERTIFICATE_URL_BASE,
        help_text="Public origin the issued PDFs are served from.",
    )
    s3_bucket = models.CharField(max_length=255, default=DEFAULT_CERTIFICATE_BUCKET)
    cloudfront_distribution_id = models.CharField(max_length=64, blank=True)
    date_timezone = models.CharField(
        max_length=64,
        default="Europe/Berlin",
        help_text="Timezone the last project deadline is converted to for the printed date.",
    )

    def clean(self):
        super().clean()
        if self.date_timezone not in available_timezones():
            raise ValidationError({"date_timezone": "Unknown timezone."})
        if self.url_base.endswith("/"):
            raise ValidationError({"url_base": "Use an origin without a trailing slash."})

    def __str__(self):
        return f"Certificate template for {self.course}"
