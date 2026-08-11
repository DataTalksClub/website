# 05 - Events, registration, and email

Status: draft

## Event model

Before database Event cutover, the bounded checked projection exposes each legacy public event at
`/events/<event-slug>`. Homepage and hub links enter that internal detail first. Provider and
recording destinations appear only as clearly labelled safe external actions on the detail, and
every checked speaker key resolves to `/people/<short>.html`. This surface is read-only and does
not introduce registration, email, or provider mutations.

### Event

- UUID and immutable internal identity;
- stable public slug with explicit aliases for approved renames;
- title, summary, sanitized body, event type, image, and visibility;
- timezone-aware start/end plus the event's IANA timezone;
- registration open/close timestamps;
- online/in-person location and separately protected join information;
- lifecycle state, publication timestamps, revision, calendar UID, and calendar sequence;
- optional recording, recap, course/cohort, or external-event relationship;
- ordered person relationships for speakers and hosts.

Lifecycle: `draft -> published -> completed -> archived`, with `cancelled` reachable before completion. Registration availability is derived from publication, registration window, event time, and cancellation state.

MVP has no capacity or waitlist. The schema and services must not imply unlimited capacity forever, but capacity workflows are deferred until their product rules are specified.

## Person relationships

- Event speakers and hosts reference the canonical GitHub-backed person `short` key.
- One person can hold multiple roles and can appear in any number of events, podcasts, articles, books, courses, or cohorts.
- Studio selects only resolvable active profiles and shows an edit-on-GitHub link.
- Removing a public person record is blocked while database events/courses still reference the key, unless an alias/replacement is supplied.

## Accountless event registration

### Data

`EventRegistration` stores:

- UUID, event, original email, normalized email, optional display name and timezone;
- status, registration/reactivation version, created/verified/cancelled/attended timestamps;
- privacy-notice version and acknowledgement timestamp;
- separate optional newsletter-consent value, text version, source, and timestamp;
- safe acquisition metadata such as UTM fields;
- abuse metadata with deliberately short retention;
- optional future user relation.

There is one row per `(event, normalized_email)`. Cancellation transitions the row; re-registration reactivates and increments its version. Database constraints, not only form checks, enforce uniqueness.

### Lifecycle

`pending_verification -> confirmed -> cancelled`, with `expired`, `attended`, and `no_show` where applicable.

1. An anonymous visitor submits the registration form.
2. The service validates the event window, normalizes email, applies rate limits, and atomically
   creates/reactivates a pending registration, one logical verification `EmailDelivery` intent, and
   its durable job.
3. The response is deliberately uniform whether the address was new, pending, confirmed, or rate-limited in a non-user-actionable way.
4. A high-entropy, hashed, registration/version-scoped link verifies ownership and confirms the registration.
5. Confirmation atomically creates its logical confirmation delivery intent, durable job, and
   calendar invitation idempotently.
6. A separate high-entropy management link opens a GET confirmation screen; cancellation itself requires POST and rotates the registration version.

Verification and management tokens are never stored in plaintext, written to logs, placed in analytics, or exposed through Studio/API responses.

### Event changes

- Rescheduling increments the calendar sequence, updates public display, and creates one update message per active registration.
- Cancelling an event closes registration, increments calendar sequence, and creates cancellation messages plus cancelled ICS content.
- Bulk notification commands are asynchronous, resumable, rate-aware, idempotent, and produce operator-visible results.
- Editing title/time/location after registrations exist requires a change summary and explicit notification decision.
- Events are archived, not physically deleted, once registrations or messages exist.

## Course/cohort communication

The target Relay-backed purpose catalog includes:

- course/cohort registration confirmation for an already verified durable member account, not an
  anonymous pending-course-registration verification flow;
- enrollment welcome/removal;
- deadline reminders;
- homework/project score notifications;
- peer-review assignment and completion reminders;
- course updates;
- certificate issue/revoke/reissue;
- account verification and password recovery.

Course/cohort message idempotency keys include cohort and relevant enrollment/assignment/submission versions so repeated jobs cannot send the same logical message twice.

The development `courses` sender/purpose is the only currently approved live path. Every other
purpose above fails closed until #22 approves its owner, audience, Relay sender/reply-to,
template/context, idempotency/version inputs, and retention class.

Existing Datamailer audits and external identifiers are imported only as send-disabled, read-only
migration/history/reconciliation evidence. Datamailer receives no new website send, and its
compatibility surfaces cannot dispatch, requeue, or become a rollback sender.

Marketing campaigns and newsletters are out of MVP scope. Marketing consent remains separate even if an external mailing service consumes it later.

## Slack-access transactional delivery

MVP Slack access uses the current shared DataTalks.Club join URL. It has no Slack invitation API,
SCIM/member synchronization, or manual review queue. The join URL exists only in the approved
runtime secret channel. It is never stored in `MemberProfile`, `SlackAccessGrant`, `EmailDelivery`
context or retained rendered bodies, audits, logs, metrics, URLs, OpenAPI examples, screenshots, or
issue evidence. Domain rows carry only a non-secret `invite_version`.

The first valid member-profile completion atomically creates or confirms one access grant, one
unique delivery intent keyed by account, completion schema version, and invite version, and its
durable job. After commit, the leased job resolves the current secret at send time and submits only
the permitted scalar context to Relay for the verified account email. Relay validates and renders
the immutable template version without returning or requiring the website to retain a
secret-bearing body. Worker/Relay/secret failure leaves profile completion and eligibility
committed. Live submission for this purpose remains disabled until #22 approves it.

A safe Relay-owned bootstrap template may seed this purpose. Relay remains the canonical template
and rendering owner throughout; the website stores only the immutable template key/version and
safe scalar context needed to preserve the logical-delivery and secret-at-send/no-secret-retention
contract.

An eligible member can reveal the current link at the authenticated private Slack surface. If the
secret is missing, it shows a delayed/support state rather than rolling back completion. Duplicate
submit, refresh, retry, and restart do not create another logical delivery. Invite rotation advances
`invite_version`; an eligible member may reveal/receive the new version without re-entering profile
data. Ordinary profile edits do not revoke access. Account quarantine, disablement, or deletion
denies future reveal/resend and cancels safe unsent work, but the site does not claim to revoke an
already used external Slack membership.

MVP has no member-facing email-resend action because the reveal page remains available. An
authorized staff resend is a separate audited logical delivery with confirmation, reason,
idempotency key, and rate limits. It never discloses the raw join URL through Studio/admin API.

## Relay-owned email templates

Relay is the canonical template catalog, validator, renderer, and sender-policy owner. It owns one
editable draft, immutable published versions, subject/plain/HTML content, required typed context,
safe escaping/sanitization, accessible plain/HTML parity, sender resolution, and the rendered
snapshot queued for transport. Tracking pixels are disabled in MVP.

Studio and the admin API proxy the same website application services to Relay for catalog, draft,
preview, publish/republish, and controlled test-send operations. Every real or test delivery names
an immutable Relay template key/version. The website stores no second mutable template body,
renderer, resolved sender, or retained rendered message. Relay unavailability fails safely with no
local rendering or direct-send fallback.

## Durable delivery model

### EmailDelivery

- UUID and unique logical `idempotency_key`;
- classification, purpose, immutable Relay template key/version, and related
  account/profile/grant/event/cohort/registration/enrollment scalar IDs;
- only recipient/reference data allowed by retention policy, minimal scalar context or its canonical
  hash, the Relay sender ID, and safe correlation/idempotency values;
- durable-job reference plus the redacted Relay message ID, projected state, callback/reconciliation
  freshness, safe reason code, and timestamps.

Projected states: `pending`, `queued`, `leased`, `provider_accepted`, `delivered`, `retryable`,
`ambiguous`, `suppressed`, `dead`, `hard_bounced`, and `complained`. Relay is authoritative for
transport state; the website projection exists only for product behavior and operations.

The Slack-link purpose follows the strict secret-at-send contract: its durable website row stores
only the fixed purpose/template identifiers and safe scalar context needed to resolve the current
invite version. The website never retains any rendered body or raw provider payload; Relay's queued
snapshot remains under its own reviewed retention contract. The Slack secret and rendered
secret-bearing body are never returned to or retained by the website.

### Relay callbacks and reconciliation

- Relay owns provider attempts/events, leases/backoff, suppression, and authoritative transition
  history. The website does not create a second provider-attempt or provider-event store.
- Relay callbacks use a tenant-scoped, timestamped HMAC over the raw body. The website deduplicates
  stable Relay event IDs, enforces the replay window, and applies reordered events through guarded
  monotonic projection transitions.
- Callback payloads and the website projection contain safe Relay/correlation/template identifiers,
  timestamps, state, and bounded reason codes only—not bodies, credentials, full recipient data, or
  raw provider payloads.
- Scheduled bounded reconciliation recovers missed callbacks and rechecks recent terminal messages;
  one-delivery manual reconciliation uses the same service.
- Provider acceptance is not displayed as delivered. Hard bounce, complaint, and suppression
  remain Relay-authoritative and independent of optional marketing preferences.

### Worker semantics

- Business state, one logical `EmailDelivery`, and one durable website job are committed in the same
  transaction; rollback creates none.
- A website worker leases/fences the durable job and calls Relay only after commit. Domain services,
  request transactions, model hooks, and callbacks never perform the Relay network request.
- Exact Relay submission replay uses the same idempotency key and request hash. A changed request
  conflicts rather than sending different work under the same key.
- Relay owns provider leases, attempts, bounded backoff, claim-time suppression, and terminal state.
- An uncertain Relay/provider acknowledgement becomes `ambiguous`; neither the website job nor
  Relay automatically resends it. Reconciliation or an audited operator action resolves it.
- Manual resend creates a new audited logical delivery related to the original; it is distinct from an automatic retry.
- Datamailer and direct Amazon SES are never fallback senders, including during rollback.

## Relay sender and provider boundary

- The website calls Relay only through scoped, expiring tenant credentials and identifies senders by
  approved Relay sender ID. It has no provider credential, direct Amazon SES permission, sender
  resolution logic, or provider-event ingress.
- Relay owns provider credentials/identities, SPF/DKIM/DMARC/provider configuration, submission,
  queues/workers, attempts/events, suppression, quotas, and provider diagnostics.
- The only approved development sender ID is `courses`, mapped by Relay to
  `DataTalks.Club Courses <courses@dtcdev.click>`. It remains recipient-allowlisted/simulated except
  for one separately approved controlled canary.
- Unknown purposes/senders, non-course purposes still open in #22, broad recipients, and all
  production sender/domain configuration fail closed.

## Abuse and privacy

- Registration and resend endpoints use CSRF, body limits, distributed rate limits by IP/email/event, and a honeypot; adaptive accessible challenge is added only when evidence warrants it.
- Responses do not disclose whether an email is registered.
- Join links and attendee lists are not public by default.
- Privacy acknowledgement and optional marketing consent are unbundled and versioned.
- CSV exports escape spreadsheet formulas and require a dedicated permission plus audit event.
- Raw IP/user-agent data has a short documented retention and never appears in routine logs.

Published anonymous-stable event catalog/detail responses may use the 60-second public edge class
with stale-if-error at most 5 minutes. Event registration forms and outcomes, verification and
management links, attendance, exports, provider endpoints, Slack/profile/onboarding, and any
credentialed response remain zero-TTL, private/no-store. WAF starts in count mode with per-source-IP
five-minute thresholds of 2,000 for ordinary public cacheable reads and 60 for signup, login,
profile, Slack, and course/event registration paths. Application services retain stricter limits by
normalized identity plus safe IP class; edge rate limits are neither account authorization nor a
complete distributed-bot defense.

## Studio and API capabilities

Both interfaces can:

- create/edit/preview/publish/cancel/archive events;
- assign/reorder people as speakers/hosts;
- inspect registration counts and individual records subject to PII permissions;
- cancel/reactivate/correct a registration through explicit reasoned commands;
- export authorized registrations;
- mark attendance individually or through bounded import;
- initiate/resume event update, cancellation, reminder, or follow-up operations;
- proxy Relay template catalog/draft/version preview, test, publish, and republish operations;
- inspect the website intent plus redacted Relay status projection and invoke guarded
  reconcile/safe-retry/ambiguity-resolution/manual-resend commands;
- inspect redacted Relay suppression/provider-health summaries without storing provider events or
  exposing raw payloads.

## Acceptance criteria

- Registration replay, refresh, and concurrency create one logical row and one delivery per purpose/version.
- Verification/cancellation tokens are hashed, scoped, expiring, revocable, redacted, and link-scanner safe.
- Event reschedule/cancellation produces correct calendar sequence and idempotent messages.
- Worker and provider failures produce the specified durable states without losing business data.
- Business state, one logical delivery intent, and one durable job commit atomically; only a leased
  after-commit job calls Relay, and uncertainty is never automatically resent.
- Course reminders and event messages share one auditable delivery model.
- Slack profile completion commits one durable secret-free logical delivery; reveal, send, retry,
  rotation, resend, suppression, outage, quarantine, and deletion never leak or retain the join URL.
- Public event catalog/detail caching cannot store a registration, management, provider, profile,
  Slack, or credentialed response.
- Every event/email management action has Studio/admin API parity and negative authorization tests.
- New website code has no direct Amazon SES or Datamailer send path, no canonical mutable template
  store, and no provider-attempt/event stack; only approved development `courses` delivery may
  progress while #22 purposes fail closed.

## Aggregate-only historical registration overlay

The `events` app may derive reviewed historical totals from protected Luma and Eventbrite sources.
It persists source-run provenance, exact provider-event mappings, immutable per-event aggregate
revisions, coverage/combination policy, active pointers, and safe audits only. It never creates a
legacy registration row or stores a legacy name, email, guest/attendee ID or digest, answer,
consent, privacy acknowledgement, timestamp, filename/path, or provider payload. Derivation causes
no verification, transactional email, newsletter, sponsor, consent, or provider side effect.

Status policy version 1 counts Luma `approved` and Eventbrite `Attending` as registrations.
Declined, cancelled, rejected, duplicate, malformed, unknown, excluded, or quarantined records do
not contribute; unknown status quarantines its provider-event aggregate. No source status is
attendance/check-in evidence.

Each accepted contribution occupies one canonical-event/provider/coverage slot. Multiple providers
are not additive without reviewed `additive_disjoint` coverage. `replacement` atomically changes
the winning pointer; `exclude` contributes nothing; ambiguous overlap fails closed. Future native
confirmed/attended/no-show rows use a separately reviewed cutover slot, and a later row-level
replacement atomically supersedes the aggregate so the query can never count both. Rollback changes
pointers and revisions without deleting history. A replacement records an immutable displacement
for every prior active aggregate or row-projection pointer, so rollback restores the complete prior
accepted contribution set rather than selecting one superseded revision heuristically.
Activation preflights the complete source run before changing any pointer. Two candidates in one
run may not target the same canonical-event/provider/coverage slot; that collision fails closed
before activation, so candidates from the run cannot displace one another or obscure the exact
pre-run contribution set required by rollback.
