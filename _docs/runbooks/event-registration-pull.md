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

4. For a newer export, add \`--refresh\`. Refresh replaces each provider event's
   own rows atomically, so cancellations and changed statuses are reflected rather
   than appended. Dry-run it first: \`registrations_replaced\` is the gross rewrite,
   \`registrations_removed\` the people really lost. A refresh that would lose more
   than 5 people or 10% of one event, or 2% overall, is refused before any write and
   names the events (\`refresh_removal_guard\`); pass \`--allow-large-removal\` only
   after reviewing them (\`data-ingest.md\` §16/17).

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

Public event counts are the distinct people registered for the event: approved
Luma and attending Eventbrite registrations, plus registrations made on the site
itself (\`community_base.events.EventRegistration\`) that are confirmed,
attended or no-show. A site registration whose email or account matches an
eligible provider registration for the same event counts once. Other statuses
(including site registrations pending verification) remain in the database but
are not included in the displayed count.
