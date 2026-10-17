# Course launch announcements

Studio → **Read course launch announcements** opens `/studio/course-launch/`.
The website owns launch schedules. CMP supplies optional explicit starts at the import
boundary; it never sends website announcements. Shared curriculum definitions have no starts.

Create/edit a template draft, publish an immutable version, and select publications when
creating a Course. Existing courses use assignment updates. A Cohort may select its own
publications or inherit its Course defaults. Publishing never changes existing announcements.
Archive templates instead of deleting their publication history.

Launch a live, current-curriculum Cohort to generate draft schedules for every assigned
cohort/module/project anchor. Generated rows are unarmed. Missing starts display `missing_start`;
past offsets display `past_due`. Neither can trigger automatic catch-up. Self-paced and GitHub
archive cohorts cannot generate, compose or dispatch announcements.

Review the preview, audience and destination, approve the current revision, then explicitly
schedule an absolute future timestamp or send now. Editing copy or changing its start clears
approval and fences old jobs. Unchanged approved/scheduled copy can be rescheduled directly with a current revision and explicit confirmation. Editing copy clears approval; review and approve it again before scheduling.
Cancellation disarms work before dispatch begins. A cancelled row can be edited and explicitly approved/scheduled again; start changes never silently undo cancellation. A dispatching/submitted/ambiguous message cannot
be edited or cancelled because transport may already have accepted it.

Email targets enrolled learners, using the current `email_course_updates` preference and Relay's
suppression rules. Relay sender is `courses`; purpose `course-announcement` carries validated
subject/body context through the existing package mail service. Relay renders the mail and owns
delivery status. No SES or Datamailer fallback exists. `submitted` means handed to the durable mail
boundary; it does not mean delivered. Inspect package EmailDelivery/Studio for suppression/status.

Telegram persists a destination **setting key**, never a bot credential or chat destination.
Course defaults own that reference; cohort overrides select template versions and inherit the
Course destination. A cohort command supplying a destination reference is rejected. Omitting the
Course key preserves it; an explicit empty key clears it. Changing the key disarms pending
Telegram work across every cohort, including template overrides and manual announcements. Those
rows need fresh approval and scheduling; cancelled and settled rows keep their state, and old
jobs are fenced by the new revision. A dispatch claim captures its setting key before commit, so
an assignment edit cannot redirect work already in progress.
The worker resolves `COURSE_TELEGRAM_DESTINATIONS` and `COURSE_TELEGRAM_BOT_TOKEN` at send time.
An uncertainty marker commits before the request. A timeout, malformed acknowledgement or
interrupted dispatch becomes `ambiguous`, never automatically resent. Investigate the transport
before creating a deliberate replacement. Missing configuration gives `blocked`; correct it,
review, approve and explicitly schedule again. Workers receive identifiers/revisions only.

All adapters require `courses.launch.read` / `courses.launch.write` / `courses.launch.send`
scopes as applicable and their matching Django permissions. Course operators manage drafts;
sending requires both course-management and email-send authority. Site administrators have both.
Support operators cannot access message bodies or recipients through this surface. Audit records
contain action/actor metadata, without subjects, bodies, context, destinations or credentials.

## Safe transport defaults

`COURSE_ANNOUNCEMENTS_ENABLED` defaults false. All local, test and development environments stay
in simulation even when this flag is true. Live transport requires both production environment
and the explicit flag. This change performs no deployment or sender configuration.

Controlled test-send requires an explicit operator email destination and confirmation, and always
returns a simulated preview. It never contacts that address. Use the ordinary production dispatch
path only after the separately authorized release/configuration process.

## Operator CLI

Run `uv run --frozen python scripts/course_launch.py --help`. Every command supports `--json`.
The CLI sends the same API commands as Studio; credentials are Bearer headers only.

```bash
uv run --frozen python scripts/course_launch.py login --url http://localhost:8000
uv run --frozen python scripts/course_launch.py whoami --json
uv run --frozen python scripts/course_launch.py templates list --json
uv run --frozen python scripts/course_launch.py templates seed --name 'Module email' \
  --channel email --kind module --subject 'Module {{ module_number }} starts' \
  --body-file ../playbooks/ai-dev-tools-zoomcamp/copy-bank/email/module-email-template.md
uv run --frozen python scripts/course_launch.py templates seed --name 'Module Telegram' \
  --channel telegram --kind module \
  --body-file ../playbooks/ai-dev-tools-zoomcamp/copy-bank/telegram/module-announcement-template.md
uv run --frozen python scripts/course_launch.py templates publish --id TEMPLATE_UUID --expected-revision 1
uv run --frozen python scripts/course_launch.py courses create --slug example-course \
  --title 'Example course' --version-ids VERSION_UUID
uv run --frozen python scripts/course_launch.py assignments save --course-id COURSE_UUID \
  --expected-revision 1 --version-ids VERSION_UUID --telegram-destination-key example
uv run --frozen python scripts/course_launch.py cohorts launch --cohort-id COHORT_UUID
uv run --frozen python scripts/course_launch.py announcements list --cohort-id COHORT_UUID --json
uv run --frozen python scripts/course_launch.py announcements compose --cohort-id COHORT_UUID \
  --version-id VERSION_UUID --target-kind module --target-id MODULE_ID
uv run --frozen python scripts/course_launch.py announcements approve --id ANNOUNCEMENT_UUID --expected-revision 2
uv run --frozen python scripts/course_launch.py announcements schedule --id ANNOUNCEMENT_UUID \
  --expected-revision 3 --scheduled-at 2027-01-11T17:00:00+00:00 --confirmed
uv run --frozen python scripts/course_launch.py announcements cancel --id ANNOUNCEMENT_UUID --expected-revision 4
uv run --frozen python scripts/course_launch.py logout --json
```

`login` runs the browser device flow, which is the preferred sign-in: it prints a
code and a verification URL, an admin approves it in the browser, and the CLI stores
the resulting management credential. Only launch managers approve (the site_admin
role carries `courses.manage_course_launch`); other users get a clear refusal and no
token is issued. `login --token-stdin` keeps the previous hand-carried scoped-token
path. Profiles live at `~/.config/dtc-course-launch/credentials.json` with mode 0600.
`DTC_TOKEN` and `DTC_URL` override the stored profile. Do not put tokens in command
arguments, URLs or reports.
`DTC_PROFILE_PATH` supports an isolated synthetic test profile. Logout deletes the local profile;
revoke the credential in Studio to revoke server-side access.

Exit codes: 0 success; 2 usage/input; 3 authentication/authorization; 4 not found; 5 revision or
idempotency conflict; 6 server validation; 7 network/server failure. Reads are paginated (`--page`).
Reuse `--idempotency-key` when retrying the same mutation. Command idempotency keys contain 1–128 characters. Mutable commands require
`--expected-revision`, sent as both body revision and `If-Match: "rev-N"`. Read current revisions
before changing records. A stale command fails without overwriting another operator's change.

## API contract

`GET /api/v1/admin/course-launch?resource=templates|versions|courses|cohorts|assignments|plans|announcements|schedules|timings`
returns bounded collections. `id`, `cohort_id`, `target_kind` and `page` select records; timings
accept cohort/module/shared_module_placement/project/homework owners. Announcement records include
plain subject/body preview and schedule/status/revision, without recipient data.

`POST /api/v1/admin/course-launch/commands` uses explicit `action` values:
`template_save`, `template_publish`, `template_archive`, `course_create`, `cohort_create`,
`assignment_save`, `timing_update`, `launch`, `compose`, `announcement_edit`, `approve`, `cancel`.
`POST /api/v1/admin/course-launch/dispatch` supports `schedule`, `send`, `test_send`, each with
`confirmed: true`. Commands require `Idempotency-Key`. Unknown fields are rejected. The generated
OpenAPI document describes command fields. Studio and CLI call the same application services.

## Explicit-start backfill

Migration 0024 separately stores only explicitly authored context overrides so editing never reuses stale derived start/deadline values.

Migration 0022 adds nullable `starts_at` and revision/provenance fields to Cohort, Module,
CohortSharedModule, Homework and Project. It leaves existing starts NULL and creates no jobs,
announcements or messages. There is no automatic historical backfill.

The inspected playbook `ai-dev-tools-zoomcamp/course.yaml` names a cohort start date but no
module/project start instants. Its cohort brief and platform-setup playbook describe assignment
deadlines. Those values cannot supply an offset-aware module/project start. They remain NULL
until an operator supplies an approved instant; this implementation applies no production data.

For each known boundary, obtain the approved cohort brief/calendar and record its document path
and revision in `provenance`. Confirm the exact UTC offset (including DST) with the source owner.
Never subtract a week from a deadline or invent a time from a date-only legacy value.

```bash
uv run --frozen python scripts/course_launch.py starts list --target-kind module --json
uv run --frozen python scripts/course_launch.py starts update --target-kind module --id MODULE_ID \
  --expected-revision 0 --starts-at 2027-01-11T17:00:00+00:00 \
  --provenance 'Approved cohort brief, revision <sha>, module 1 boundary'
```

Repeat for each explicitly known owner. Use `--starts-at null` to clear a previously entered start.
Starts after the cohort end (UTC) or assignment deadline are rejected. Start edits never change
deadlines. Validate the module/project mapping by stable identity. Shared-module starts belong to
the CohortSharedModule placement, not the shared graph. Homework starts exist only as a native
assessment/import bridge; module announcements anchor to the explicit placement.

Optional CMP starts initialize only untouched website rows (`timing_revision=0`, starts NULL).
Old SQLite schemas without the field remain valid; missing/null upstream values do nothing.
Locally edited or explicitly cleared starts survive repeated imports. A homework start propagates
only through an existing explicit terminal-homework binding, never an ordinal/title guess.

Implementation cohesion exceptions: the existing large CMP importer retains three small boundary
calls instead of a broad unrelated rewrite; the settings composition root retains the typed
bootstrap call. All new launch behavior lives in small owning modules.
