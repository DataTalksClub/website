"""Resumability bookkeeping for the CMP learner-history import.

``scripts/prod/import_cmp_learner_history.py`` moves nine CMP export tables --
about half a million learner rows -- into this app.  A failure at 400,000 rows
has to be a re-run, not a rebuild, so every table is walked in ascending
source-id order in fixed-size batches and its high-water mark is stored here.

Each batch's writes and the advance of ``last_source_id`` share one database
transaction, so a process killed mid-batch leaves that batch fully rolled back:
there is nothing for the stored watermark and the rows to disagree about.  A
re-run resumes with ``id > last_source_id``, so it never re-reads what it
already committed.

``rows_attached`` counts source rows that found a target row already carrying
their natural key -- an enrollment another importer wrote for the same student
and cohort, say.  Those are claimed rather than duplicated, which is what makes
a replay whose claims were lost recover instead of refusing on a unique
constraint.

``unresolved`` counts, by named bucket, the source rows this table skipped
because a parent could not be reconciled -- a user, cohort, homework, question,
project, criteria or campaign the target database does not hold.  It is
deliberately a count per bucket and never a source value: the payload is
learner data, and a report may carry totals and bounded codes only.

Which target row this import created for a given CMP source id lives in
:class:`CmpHistoryClaim`, in this same database, written inside the batch
transaction (audit REL-04) -- and the whole run is bound to its export and
database by :class:`CmpHistoryImportBinding` (audit REL-03).  This progress
table stays a database row for the one property no file can replicate: its
watermark advances inside the same transaction as the batch it counts.
"""

from __future__ import annotations

import uuid

from django.db import models


class CmpHistoryImportProgress(models.Model):
    table = models.CharField(max_length=64, unique=True)
    last_source_id = models.BigIntegerField(default=0)
    rows_created = models.PositiveBigIntegerField(default=0)
    rows_attached = models.PositiveBigIntegerField(default=0)
    rows_skipped = models.PositiveBigIntegerField(default=0)
    unresolved = models.JSONField(default=dict, blank=True)
    completed = models.BooleanField(default=False)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("table",)
        verbose_name = "CMP history import progress"
        verbose_name_plural = "CMP history import progress"

    def __str__(self) -> str:
        return f"cmp-history-import-progress:{self.table}"


class CmpHistoryImportBinding(models.Model):
    """Script-owned binding of the resumable CMP learner-history import to
    its exact inputs.

    One singleton row (``kind`` is unique): the first run records the export's
    SHA-256 digest plus the schema and importer versions it is running; every
    later run -- including a kill-and-resume -- must present the same triple
    or the service refuses before any write (audit REL-03).  A rebuilt target
    database starts with no row, so it can only begin a fresh import; the
    user claims this importer reconciles against are read from the same
    database (``accounts.models.CmpLearnerClaim``), so they cannot describe
    a different one.
    """

    kind = models.CharField(max_length=32, unique=True)
    schema_version = models.IntegerField()
    importer_version = models.CharField(max_length=64)
    source_sha256 = models.CharField(max_length=64)
    target_uuid = models.UUIDField(default=uuid.uuid4, editable=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "CMP history import binding"
        verbose_name_plural = "CMP history import bindings"

    def __str__(self) -> str:
        return f"cmp-history-import-binding:{self.kind}"


class CmpHistoryClaim(models.Model):
    """One "CMP source row id -> target pk" mapping, per imported table.

    The durable claims store for the learner-history import; it replaced the
    earlier per-table JSON files so a claim commits inside the same
    transaction as the batch that created or attached the target row (audit
    REL-04).  A claim that survives is always backed by committed rows; a
    rolled-back batch leaves no claim behind.
    """

    table = models.CharField(max_length=64)
    source_id = models.BigIntegerField()
    target_id = models.BigIntegerField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ("table", "source_id")
        constraints = [
            models.UniqueConstraint(
                fields=("table", "source_id"),
                name="courses_cmphistoryclaim_table_source_unique",
            ),
        ]

    def __str__(self) -> str:
        return f"cmp-history-claim:{self.table}:{self.source_id}"
