"""Optional upstream timing is an ingest boundary, never a runtime fallback."""

from courses.models import CohortSharedModule, Module
from courses.services.launch_timing import validate_start
from courses.services.launch_validation import aware_datetime


def import_explicit_start(row, source, kind):
    if "starts_at" not in source.keys() or not source["starts_at"]:
        return
    if row.timing_revision != 0 or row.starts_at is not None:
        return
    start = aware_datetime(source["starts_at"])
    validate_start(kind, row, start)
    row.starts_at = start
    row.timing_provenance = "Explicit CMP starts_at; date-only source values are not inferred"
    row.save(update_fields=["starts_at", "timing_provenance"])
    if kind == "homework":
        propagate_bound_start(row, start)


def propagate_bound_start(homework, start):
    for model in (Module, CohortSharedModule):
        for placement in model.objects.filter(
            terminal_homework=homework, timing_revision=0, starts_at__isnull=True
        ):
            kind = "module"
            if model is CohortSharedModule:
                kind = "shared_module_placement"
            validate_start(kind, placement, start)
            placement.starts_at = start
            placement.timing_provenance = "Explicit CMP start through terminal_homework binding"
            placement.save(update_fields=["starts_at", "timing_provenance"])
