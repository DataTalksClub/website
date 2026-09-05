# The recurring event registration pull

Luma is not frozen history, and every other document in this repository that
calls it a migration input is describing a step, not the steady state. **New
events keep being created there, and people keep registering for events we
already have.** This runbook is the recurring task: take an export while we
still can, work out what changed, land it, and know afterwards exactly what we
are synchronised to.

Companion documents. [`ingest-script-inventory.md`](ingest-script-inventory.md)
§5–6 and §9 say what each script does and where its output lands;
[`data-ingest.md`](data-ingest.md) §14–17 is the narrative. This one is the only
place that says *when to run them, in what order, and what a person has to
decide in between*.

> **The one rule that outranks everything below.** The Luma export contains
> attendee rows. Never print, log, paste or commit a value out of one. Counts,
> event titles and provider event ids are fine — an event id is part of the
> event's public Luma URL. An email address is not, in a log or anywhere else;
> identify a person by user id.

---

## 1. Why this exists, measured

Two prepared exports of the same Luma account sit side by side on this machine
today. Comparing them is the whole argument, and it was measured on 2026-09-05
by counting rows, never by reading one:

| | Export of 2026-08-29 (pinned) | Export of ~2026-09-02 |
| --- | ---: | ---: |
| Events | 166 | **174** |
| Rows | 51,924 | **52,467** |
| Approved / declined | 51,873 / 51 | 52,415 / 52 |
| `tree_sha256` | `5362e8c2…` | `2e18d184…` |

- **8 events exist in the newer export that the older one had never heard of**,
  dated 2026-08-31 to 2026-09-15. Seven of them are events this database has
  never seen at all; the eighth is an event we already hold under its reviewed
  identity.
- **Of the 166 events both exports carry, 99 have different registrant rows.**
  Five gained rows, thirteen *lost* rows, and the rest changed without changing
  their row count.

Read the second bullet twice. **A refreshed provider export is not append-only.**
A registrant who cancels, or whom Luma deletes, is simply gone from the next
export. That is why the refresh in §5 replaces an event's registration facts
wholesale instead of adding to them, and it is why "just import the new file
again" is the one thing that must never happen — `EventRegistration` keeps no
per-attendee natural key, so a second unguarded pass would double every row it
already holds.

---

## 2. The last-synchronised record — where it is and how to read it

There is no new model and no new state file for this. Three records that already
exist answer the two questions between them, and each is authoritative for a
different thing. **Read all three; any one alone will mislead you.**

### 2.1 What snapshot we are pinned to

[`_docs/migration-data/event-registration-sources.json`](../migration-data/event-registration-sources.json)
— checked in, human-reviewed, and the file every full run validates against.

| Field | Means |
| --- | --- |
| `luma.capture_completed_at` | When the export was taken out of Luma. **Maintained by a person; no code reads it.** It is the only place the capture time is recorded at all. |
| `luma.tree_sha256` | The exact prepared directory the pin refers to. |
| `luma.event_total`, `row_total`, `status_totals` | What that directory must contain, to the row. |

This is the *reviewed* watermark: what a human last decided we accept. It moves
only in a commit (§4.3).

### 2.2 What a database has actually ingested

`events.models.HistoricalRegistrationSourceRun` — one row per distinct export,
per provider. Its unique constraint is
`(provider, whole_source_checksum, schema_version, mapping_set_revision, policy_version)`,
so re-running the same export reuses its row and a genuinely different export
creates a new one. `Meta.ordering` is `("-created_at", "-id")`, so **the newest
row for a provider is literally that database's last-synchronised point**, and
it carries the checksum, the event and row totals, and when we ingested it.

### 2.3 Which individual events have had their registrations pulled

`events.models.EventRegistrantImportProgress` — one row per
`(provider, external_event_identifier)`. `completed` says whether that event's
registrant rows have ever been read; `updated_at` says when they were last read.
That is the per-event freshness answer, and it is what §5 acts on.

### 2.4 Reading all three

Write this to `.tmp/watermark.py` and pipe it in. It prints counts and
checksums only.

```python
from events.models import EventRegistrantImportProgress, HistoricalRegistrationSourceRun

for provider in ("luma", "eventbrite"):
    run = HistoricalRegistrationSourceRun.objects.filter(provider=provider).first()
    if run is None:
        print(f"{provider}: no source run — this database has never ingested an export")
        continue
    print(
        f"{provider}: ingested {run.created_at:%Y-%m-%d %H:%M} "
        f"tree={run.whole_source_checksum[:12]} "
        f"events={run.manifest_event_total} rows={run.parsed_row_total} state={run.state}"
    )

pulled = EventRegistrantImportProgress.objects.filter(completed=True)
print(f"events whose registrations have been pulled: {pulled.count()}")
oldest = pulled.order_by("updated_at").first()
newest = pulled.order_by("-updated_at").first()
if oldest is not None and newest is not None:
    print(f"oldest per-event pull: {oldest.updated_at:%Y-%m-%d %H:%M}")
    print(f"newest per-event pull: {newest.updated_at:%Y-%m-%d %H:%M}")
```

```bash
DTC_ENVIRONMENT=local DTC_SQLITE_PATH=.tmp/pull-check.sqlite3 \
DJANGO_SETTINGS_MODULE=website.settings.local \
uv run --frozen python manage.py shell < .tmp/watermark.py
```

A database with no source run has never had an export ingested — that is the
normal state of a scratch database and of any database where only
`--discover-new-events-only` has been run, because that mode deliberately skips
the aggregate leg.

**Why no fourth record was added.** The obvious missing field is "when was this
export captured", and it already exists as `capture_completed_at` in the pinned
facts file. Adding a column to a live public model to carry import bookkeeping
is exactly what [`ingest-script-inventory.md`](ingest-script-inventory.md)'s
design principle forbids, and a new script-owned state file would duplicate a
watermark two models already hold with better guarantees — the source-run row's
uniqueness constraint cannot be edited out of agreement with the data it
describes, and a JSON file beside the script can.

---

## 3. Before you start

- **The prepared exports are protected data.** They live in the gitignored
  `.local/migration-data/events/` of the *main* checkout, and the durable copy
  lives outside any worktree at
  `/data/tmp/luma-eventbrite-export/luma-aggregate-v1/` (`chmod 700` directory,
  `600` files). Nothing here is ever copied into a worktree or committed.
- **There is no Luma API client in this repository and there must not be one.**
  Registration data arrives as an owner-provided file export. The seam is a
  reader in `scripts/prod/registration_sources/`, plugged into the
  provider-neutral port in `events/importers.py`. See §7 for why that matters
  more than usual right now.
- **Work on a scratch database first.** Every step below is safe to rehearse
  against a fresh `.tmp/*.sqlite3` built with `manage.py migrate`.

---

## 4. The procedure

### 4.0 Take the export — the only step that needs Luma

Done by the site owner, in Luma, while access lasts. What we need is the same
shape the existing export has, and nothing more:

- one CSV per event, and a matching JSON checkpoint per event under `_json/`,
  whose `event` object carries `id` and a `https://` `url`;
- CSV columns `event_id`, `guest_id`, `approval_status` at minimum; add `email`
  and `registered_at` for the registrant leg, and `event_name` and
  `event_start_at` for identity discovery. All five extra columns are already in
  the existing export;
- optionally `descriptions/*.md` beside `_json/`, one per event — this is what
  gives a newly discovered event a page body instead of a bare title.

**Take the whole account every time, not a delta.** Everything downstream
compares whole snapshots, and §1 shows why a delta would be wrong anyway: rows
disappear as well as appear.

Hand it over as a directory, not a paste. Record the capture time — it becomes
`capture_completed_at` in §4.3.

### 4.1 Prepare it

```bash
uv run --frozen python scripts/prepare_event_registration_sources.py \
    --luma-source <the raw export directory> \
    --destination /home/alexey/git/dtc-website/.local/migration-data/events
```

This validates the pair shape and the required columns, repackages into
`<destination>/luma-aggregate-v1/`, and **prints the numbers you need for
§4.3** — `event_total`, `row_total`, `status_totals`, `tree_sha256`. Keep that
output. It never prints an event name or an attendee field.

Add `--replace` to overwrite an existing prepared directory. **Move the previous
one aside instead, the first time**: having the old and new prepared directories
side by side is what makes §1's comparison possible, and it is the only way to
see that thirteen events lost rows.

### 4.2 Ask what is new — changes nothing

```bash
uv run --frozen python scripts/prod/import_events.py \
    --database <the database you would apply to> \
    --luma-source .../luma-aggregate-v1 \
    --discover-new-events-only --dry-run
```

Read `new_event_identities.luma`:

| Key | Means |
| --- | --- |
| `created_events` | **The answer to "what is new".** Events this database has never seen, each with its title, start and eligible count. |
| `existing_event_total` | Export events that are an event we already hold — same calendar date, same exact normalized title. Nothing to do. |
| `already_tracked_total` | Export events already carrying our own provider identity, or already staged as an aggregate. Nothing to do. |
| `ambiguous_total` | Several of our events share this export event's date *and* exact title. **A person decides**; folding two real events into one is worse than a duplicate. |
| `undated_total` | No readable calendar date, so "do we already have it" is unanswerable. |
| `no_metadata_total` | The export carries no title — a Luma event with zero registrations has no row to read one from. Reported so you know it exists. |

Measured 2026-09-05, dry-running the 174-event export against a database holding
the previous pull: **7 created, 145 existing, 20 already tracked, 2 no metadata,
0 ambiguous, 0 undated.**

> **The one way to misread this.** `--dry-run` makes the identity-manifest
> import a dry run too, so against an *empty* database nothing exists to match
> against and every export event looks new — the same command against a freshly
> migrated, empty database reported **172 created**. Point it at the database
> you would actually apply to.

### 4.3 Move the pin — only when the growth is legitimate

The full run in §4.4 refuses any export whose whole-tree checksum and counts
disagree with
[`_docs/migration-data/event-registration-sources.json`](../migration-data/event-registration-sources.json).
That gate exists to stop a public registration *count* drifting silently, and it
is doing its job: it is why the 174-event export currently fails with
`registration_source_validation_failed`. It is not a bug to route around.

Moving it is a reviewed commit, and the review is the point:

1. Confirm the new export is a superset in the way §4.2 says — new events are
   genuinely new, nothing you expected is missing, `ambiguous_total` is 0 or has
   been resolved by a person.
2. Explain the row delta before you accept it. It will not be "old + new
   events": in the measured case the eight new events brought rows *and* the
   pre-existing 166 net lost 360, because registrants cancel. A delta you cannot
   explain is a reason to stop, not a rounding error.
3. Edit the `luma` block: `event_total`, `row_total`, `registration_total`
   (approved), `excluded_registration_total` (declined), `status_totals`,
   `tree_sha256` — all straight from §4.1's output — and
   `capture_completed_at` from §4.0.
4. Commit that edit **on its own**, with the delta in the message. It is the
   record of what a human accepted.

Two things the pin does *not* cover, and both will bite eventually:

- `scripts/prod/registration_sources/luma.py` carries a **second, code-owned
  pin**: `SAFE_SOURCE_FACTS` and `_require_pinned_reconciliation` hardcode 159
  events / 50,505 rows, an export generation older than the JSON's 166. It is
  unreachable today because `HISTORICAL_REGISTRATION_SOURCES` is configured in
  no settings module, so nothing takes the registry path that enforces it. The
  day someone configures that registry, it must be moved in the same commit as
  the JSON or the Studio path will refuse an export the script accepts.
- Eventbrite's pin is untouched by any of this. Eventbrite really is frozen.

### 4.4 Land it

Identities and page content for the new events, then the aggregates:

```bash
# 1. Identities for the newly discovered events (and the reviewed manifest replay).
uv run --frozen python scripts/prod/import_events.py \
    --database <db> --luma-source .../luma-aggregate-v1 \
    --discover-new-events-only

# 2. Staged descriptions for those events — reports by default, --write to land.
uv run --frozen python scripts/build_luma_event_descriptions.py \
    --database <db> --source-root <the raw export root, with descriptions/ and _json/>

# 3. The full run: content, aggregates, resolution, coverage.
uv run --frozen python scripts/prod/import_events.py \
    --database <db> --luma-source .../luma-aggregate-v1 \
    --current-registration-input _docs/migration-data/local-current-registration-input.json
```

Step 2 stops on anything nobody has reviewed and says so rather than guessing:
a description linking to a destination no one has approved is held and the URL
is named (approving it is an edit to
`scripts/projection_build/event_description_link_policy.py`), and an event whose
`type` is not in
[`local-event-type-input.json`](../migration-data/local-event-type-input.json)
is skipped. Clear both, then re-run with `--write`, then re-run step 1 so
`import_new_content` picks the artifact up.

Step 3 is the run that writes the `HistoricalRegistrationSourceRun` row §2.2
reads — **until it succeeds, this database's last-synchronised point has not
moved**, whatever the identity legs did.

### 4.5 Refresh the registrations

The attendee-level leg. New events get their rows on a plain run; **events we
already have need `--refresh`**, because the completed marker that makes an
interrupted run safe to resume is exactly what stops a plain re-run from seeing
a single new sign-up.

```bash
# What the export holds, reading no attendee row at all.
uv run --frozen python scripts/prod/import_event_registrants.py \
    --database <db> --luma-source .../luma-aggregate-v1 --dry-run

# New events only — a completed event is skipped without reopening its file.
uv run --frozen python scripts/prod/import_event_registrants.py \
    --database <db> --luma-source .../luma-aggregate-v1

# Every event, including completed ones: replace each one's facts with this export's.
uv run --frozen python scripts/prod/import_event_registrants.py \
    --database <db> --luma-source .../luma-aggregate-v1 --refresh
```

`--refresh` re-reads every event and, per event inside one transaction, deletes
that provider's `EventRegistration` rows for it and writes the ones this export
carries. Read `rows_replaced` against `rows_written` in the report: **replaced
higher than written means registrants left the export**, which is normal and is
precisely what an append would have hidden.

Identities are never deleted. A person we have already consolidated onto an
account stays consolidated whether or not they are still on this event's list,
and the account-first lookup means someone who took a course and registered for
an event is still one account, never two.

Run `--refresh` after §4.4, never before: an event with no identity yet is
reported under `awaiting_identity_events` and skipped, never created here.

### 4.6 What a person must review before anything is public

Landing an export makes **nothing** public on its own. Four gates, all human,
none of which this procedure moves:

1. **Resolution.** An aggregate resolves to a canonical `Event` only when
   `--current-registration-input` names the exact pair, or when exactly one of
   our events shares the provider event's date and its exact normalized title.
   Anything else stays `event=null` and renders no count. Resolving an ambiguous
   one means a person adding the pair to
   [`local-current-registration-input.json`](../migration-data/local-current-registration-input.json)
   and re-running.
2. **Activation.** A *resolved* aggregate still shows nothing until it is
   activated — Studio's dry-run/validate/activate flow, or the narrower
   `activate_explicit_current_source` the script calls for exactly the pairs that
   input file names. Resolution and activation are two gates on purpose.
3. **Link review** for any newly staged description (§4.4 step 2).
4. **Event type** for any newly discovered event — from the reviewed input file,
   never inferred.

Read `activation_coverage` in the §4.4 step 3 report before you call the pull
done. It states the ratio plainly ("N of M provider events resolved"), and an
unresolved event renders no registration count at all.

### 4.7 Then say so

Update, in one commit: `capture_completed_at` and the counts in the pinned facts
(§4.3, if not already done), and the measured figures in
[`ingest-script-inventory.md`](ingest-script-inventory.md) §6 and
[`data-ingest.md`](data-ingest.md) §16/17. Those documents quote real numbers
with the date they were measured; a pull that does not move them leaves the next
person reading a stale figure as a current one.

---

## 5. Failure modes

| Symptom | What it is | What to do |
| --- | --- | --- |
| `registration_source_validation_failed` on the full run | The export disagrees with the pinned checksum or counts. The normal cause is a legitimately grown export. | §4.3. Do not point the run at the old directory to make it pass. |
| `luma_registration_facts_mismatch` | Same, but the counts specifically. | §4.3, and explain the delta before editing. |
| `registration_source_unavailable` | `--luma-source` is not a directory, or `--eventbrite-source` not a file. The default `--luma-source` is in the *main* checkout's `.local/`, so it is normally absent from a worktree. | Pass the path explicitly, or point at `/data/tmp/luma-eventbrite-export/luma-aggregate-v1/`. |
| `luma_discovery_failed` | The export directory failed a structural check — a symlink, a hidden entry, a CSV with no JSON partner, a missing required column. | Re-prepare from the raw export (§4.1); the preparer reports the specific refusal. |
| Discovery reports far more `created_events` than you expect | Almost always `--dry-run` against a database with no identities. | §4.2's warning. Point it at the real database. |
| A second `Event` appears for an event we already had | The pre-2026-09-05 unguarded discovery did this — 144 duplicates against a 166-event export. | `--report-duplicate-identities` names them; `--remove-duplicate-identities` deletes only the ones carrying no alias, registration, aggregate revision, question or invite. Anything else is a merge and needs a person. |
| `mismatched_luma_pair` / `unsupported_luma_schema` from the registrant leg | The export's shape or columns changed. | Compare against §4.0's list. A changed provider schema is a reader change in `scripts/prod/registration_sources/`, never a change in `events/`. |
| `awaiting_identity_events` is non-empty | Those events have no identity yet. | Run §4.4 step 1 first. Never create an event from the registrant leg. |
| Registration counts doubled | Someone cleared `completed` and re-ran instead of using `--refresh`. `EventRegistration` has no natural key to deduplicate on. | Delete that event's `EventRegistration` rows for the provider and re-run with `--refresh`. |
| `activation_coverage` says 0 resolved | Normal, and not a failure. No `--current-registration-input` named a pair, and no export event matched one of ours exactly. | §4.6. Nothing renders a count until a person resolves and activates it. |

---

## 6. What this deliberately does not do

- **No Luma API client.** File exports only. §7 says why building one now would
  be worse than not having one.
- **No automatic resolution or activation.** Both stay human. A wrong public
  registration count is worse than a missing one.
- **No scheduled job.** The pull is owner-initiated because §4.0 is
  owner-initiated. Automating the six steps after it would be automating the
  cheap part.
- **No rename of `scripts/prod/import_events.py`.** Its `SYNC_MODEL` is still
  `one-time`, which the filename prefix convention in `scripts/prod/__init__.py`
  ties to `import_`. The name now understates what the script does; renaming it
  would break every runbook, inventory entry and Make target that names it, so
  the module docstring carries the correction instead. If a third sync model is
  ever added to that convention, this is the first module that wants it.

---

## 7. The API expiry risk

**Luma access expires soon, and the owner may move to another platform before it
does.** That changes what is urgent: capture beats integration.

### 7.1 What becomes unobtainable when access ends

Everything in this pipeline arrives as a file export from Luma. When access
ends, no further export can be taken, so whatever the last export did not
contain is gone for good:

| Data | Lost how completely |
| --- | --- |
| **Registrant rows for events after the last export** — guest id, email, approval status, registered-at | **Unobtainable.** Nothing else in this codebase holds them. There is no second copy, no API we could call later, no backup outside the export files. |
| **Registrations that arrived after the last export, for events we already have** | **Unobtainable**, and the easiest thing to lose by accident, because a plain re-run picks up nothing (§4.5) and an old export looks like a valid one. |
| **Event identity for events created after the last export** — provider id, title, start | Unobtainable from Luma. Partially reconstructable from a public listing page by hand, at low fidelity and with no ids. |
| **Event descriptions** (`descriptions/*.md`) | Unobtainable. This is the only source of a page body for a discovered event; the reviewed 421-record artifact cannot grow to cover one. |
| **Historical counts already ingested** | **Safe.** They are in `HistoricalRegistrationAggregateRevision` rows in our database, with the source-run provenance beside them. |
| **Registrant identities and facts already imported** | **Safe.** `EventRegistrantIdentity` and `EventRegistration` are ours. |
| **The prepared exports themselves** | Safe as long as `/data/tmp/luma-eventbrite-export/` survives — it is outside every worktree and gitignored everywhere, so nothing in this repository protects it. It is a single copy on one machine. |

### 7.2 What to capture before then, in priority order

1. **One final full export, as late as possible**, in the §4.0 shape —
   CSV + `_json/` checkpoint + `descriptions/`. This is the whole game. Every
   other item on this list is a subset of it.
2. **Land it end to end** through §4.1–4.5, including `--refresh`. An export
   sitting on disk unlanded is one disk failure from being nothing.
3. **Make sure `descriptions/` is in it.** It is optional in the export and
   mandatory for any event we have not already described.
4. **Copy the durable export off this machine.** It is currently one copy in one
   directory, and it is the only artifact that can be re-derived from.
5. **Record the capture time** in `capture_completed_at`. After access ends, that
   field is the permanent statement of how far our registration history reaches.

### 7.3 What a move to another platform needs

The seam is already the right shape, and keeping it that way is worth more than
any Luma convenience:

- `events/importers.py` is a provider-neutral port — a `SourceReader` contract, a
  registry, aggregate-only result types, bounded failure codes. **It knows no
  provider's file format and must not learn one.**
- `events/registrant_import.py` is provider-generic in the same way: the provider
  is an argument, not a constant, and it takes already-parsed `RegistrantRow`
  values.
- Provider file formats live in `scripts/prod/registration_sources/`, one module
  per provider, registered explicitly by the ingest entry point.

So a third platform is **one new reader module** — a `discover_*` and a
`read_*`, plus a `source_reader()` registration — and one new value in
`HistoricalRegistrationSourceRun.Provider` and `EventRegistration.Provider`. Not
a model change, not a matching-logic change, not a change in `events/`. What it
also needs, and what no code can supply:

- a reviewed pin for the new source's counts and checksum, the way §4.3 works;
- a way to tie the new platform's event ids to our canonical events. Every
  automatic match we have leans on the export carrying a title and a start
  timestamp — Eventbrite's does not, which is exactly why every unresolved
  Eventbrite aggregate is reported `provider_event_metadata_unavailable` and why
  none of them will ever resolve on their own. **When evaluating a replacement
  platform, whether its export carries an event title and start time per row is
  a real selection criterion.**

The thing to avoid is a Luma-shaped integration that a migration would throw
away. There is no Luma API client in this repository today. Keep it that way.
