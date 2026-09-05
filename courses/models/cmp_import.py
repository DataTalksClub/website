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
a replay whose claims file was lost recover instead of refusing on a unique
constraint.

``unresolved`` counts, by named bucket, the source rows this table skipped
because a parent could not be reconciled -- a user, cohort, homework, question,
project, criteria or campaign the target database does not hold.  It is
deliberately a count per bucket and never a source value: the payload is
learner data, and a report may carry totals and bounded codes only.

Which target row this import created for a given CMP source id lives outside
the database, in the importer's own claims files -- see
``courses.services.cmp_learner_history_import.CmpHistoryClaims`` and the
"Claim tracking" reasoning in ``accounts.services.cmp_learner_import``.  This
table stays a database row for the one property no file can replicate: its
watermark advances inside the same transaction as the batch it counts.
"""

from __future__ import annotations

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
