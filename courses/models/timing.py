"""Delivery timing is owned by a cohort, never the shared curriculum graph."""

from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone


class TimingOwner(models.Model):
    starts_at = models.DateTimeField(null=True, blank=True)
    timing_revision = models.PositiveIntegerField(default=0, db_default=0)
    timing_provenance = models.CharField(max_length=200, blank=True, db_default="")

    class Meta:
        abstract = True

    def clean(self):
        super().clean()
        if self.starts_at is None:
            return
        if timezone.is_naive(self.starts_at):
            raise ValidationError({"starts_at": "Start must include a timezone offset."})
        from courses.services.launch_timing import TIMING_MODELS, validate_start
        from courses.services.launch_validation import LaunchError

        for kind, model in TIMING_MODELS.items():
            if isinstance(self, model):
                try:
                    validate_start(kind, self, self.starts_at)
                except LaunchError as error:
                    raise ValidationError({"starts_at": str(error)}) from error
                return
