import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    """Remove HistoricalEventMapping and its review-state machine.

    The owner's directive: there is no separate "does this provider event map
    to a canonical event" review state.  A HistoricalRegistrationAggregateRevision
    either resolves directly to an Event (nullable FK, set once) or it does not.
    Resolution is now automatic (exact title+date match) or file-driven (the
    current-registration-input JSON), applied at
    events.services._persist_derived_source / resolve_unmatched_aggregates --
    never a persisted "review_required"/"mapped"/"excluded" row.

    Production has never seen this table (confirmed against the running
    deployment), so this is a straightforward structural migration -- no
    historical-data-preservation machinery is needed for a table with zero
    real rows anywhere.
    """

    dependencies = [
        ("events", "0002_registrant_identity"),
    ]

    operations = [
        migrations.RemoveConstraint(
            model_name="historicalregistrationaggregaterevision",
            name="events_hist_aggregate_revision_unique",
        ),
        migrations.RemoveIndex(
            model_name="historicalregistrationaggregaterevision",
            name="events_hist_agg_state",
        ),
        migrations.RemoveField(
            model_name="historicalregistrationaggregaterevision",
            name="mapping",
        ),
        migrations.AddField(
            model_name="historicalregistrationaggregaterevision",
            name="external_event_identifier",
            field=models.CharField(default="", max_length=512),
            preserve_default=False,
        ),
        migrations.AddField(
            model_name="historicalregistrationaggregaterevision",
            name="event",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="historical_registration_aggregate_revisions",
                to="events.event",
            ),
        ),
        migrations.AddIndex(
            model_name="historicalregistrationaggregaterevision",
            index=models.Index(
                fields=["event", "state", "-created_at"], name="events_hist_agg_state"
            ),
        ),
        migrations.AddIndex(
            model_name="historicalregistrationaggregaterevision",
            index=models.Index(
                fields=["source_run", "external_event_identifier"],
                name="events_hist_agg_run_ext_id",
            ),
        ),
        migrations.AddConstraint(
            model_name="historicalregistrationaggregaterevision",
            constraint=models.UniqueConstraint(
                fields=("source_run", "external_event_identifier", "aggregate_checksum"),
                name="events_hist_aggregate_revision_unique",
            ),
        ),
        migrations.AddConstraint(
            model_name="historicalregistrationaggregaterevision",
            constraint=models.CheckConstraint(
                condition=models.Q(("external_event_identifier__gt", "")),
                name="events_hist_aggregate_external_id_nonempty",
            ),
        ),
        migrations.DeleteModel(
            name="HistoricalEventMapping",
        ),
    ]
