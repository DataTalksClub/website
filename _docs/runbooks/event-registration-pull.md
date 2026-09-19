# Event registration ingest

Event registration data comes from provider exports and is stored as current
database rows. Runtime code reads \`event_registrants.EventRegistration\`
directly; there is no aggregate, projection, activation state, fallback, or
parallel historical model.

Never print, log, paste, screenshot, or commit attendee values. Raw and prepared
exports live under \`~/prod\`, outside the repository. Logs and reports may contain
provider event identifiers and counts, but never email addresses.

## Order

1. Import Event identities and content:

   \`\`\`bash
   uv run --frozen python scripts/prod/import_events.py \\
       --database <db> \\
       --luma-source ~/prod/dtc-data/luma-eventbrite-export/luma-aggregate-v1
   \`\`\`

2. Preview the attendee import. The preview reads and validates the real export
   without writing rows:

   \`\`\`bash
   uv run --frozen python scripts/prod/import_event_registrants.py \\
       --database <db> \\
       --luma-source ~/prod/dtc-data/luma-eventbrite-export/luma-aggregate-v1 \\
       --luma-identities ~/prod/dtc-data/luma-event-identities.json \\
       --eventbrite-source .local/migration-data/events/eventbrite/aggregate-v1.zip \\
       --eventbrite-identities ~/prod/dtc-data/eventbrite-event-identities.json \\
       --dry-run
   \`\`\`

3. Run the same command without \`--dry-run\`.

4. For a newer export, add \`--refresh\`. Refresh replaces each event's provider
   rows atomically, so cancellations and changed statuses are reflected rather
   than appended.

The importer requires existing Event identities. Events it cannot resolve are
reported under \`awaiting_identity_events\` and skipped. Resolve the identity in
the provider identity input, rerun the Event import if needed, and then rerun the
registrant ingest.

## Verification

Use database counts only:

\`\`\`bash
DTC_ENVIRONMENT=local DTC_SQLITE_PATH=<db> \\
DJANGO_SETTINGS_MODULE=website.settings.local \\
uv run --frozen python manage.py shell -c \\
  'from event_registrants.models import EventRegistration; print(EventRegistration.objects.count())'
\`\`\`

Public event counts include approved Luma registrations and attending
Eventbrite registrations. Other statuses remain in the database but are not
included in the displayed count.
