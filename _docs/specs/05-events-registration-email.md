# 05 - Events, registration, and email

Status: draft

## Event model

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
2. The service validates the event window, normalizes email, applies rate limits, and creates/reactivates a pending registration plus a verification-email outbox row in one transaction.
3. The response is deliberately uniform whether the address was new, pending, confirmed, or rate-limited in a non-user-actionable way.
4. A high-entropy, hashed, registration/version-scoped link verifies ownership and confirms the registration.
5. Confirmation creates its email outbox row and calendar invitation idempotently.
6. A separate high-entropy management link opens a GET confirmation screen; cancellation itself requires POST and rotates the registration version.

Verification and management tokens are never stored in plaintext, written to logs, placed in analytics, or exposed through Studio/API responses.

### Event changes

- Rescheduling increments the calendar sequence, updates public display, and creates one update message per active registration.
- Cancelling an event closes registration, increments calendar sequence, and creates cancellation messages plus cancelled ICS content.
- Bulk notification commands are asynchronous, resumable, rate-aware, idempotent, and produce operator-visible results.
- Editing title/time/location after registrations exist requires a change summary and explicit notification decision.
- Events are archived, not physically deleted, once registrations or messages exist.

## Course/cohort communication

The same email infrastructure supports:

- course/cohort registration verification and confirmation;
- enrollment welcome/removal;
- deadline reminders;
- homework/project score notifications;
- peer-review assignment and completion reminders;
- course updates;
- certificate issue/revoke/reissue;
- account verification and password recovery.

Course/cohort message idempotency keys include cohort and relevant enrollment/assignment/submission versions so repeated jobs cannot send the same logical message twice.

Existing Datamailer audits and external identifiers are migrated for history. New transactional delivery uses the unified outbox and SES provider adapter. Datamailer may remain a temporary compatibility adapter during migration, but it is not the new domain model or source of truth.

Marketing campaigns and newsletters are out of MVP scope. Marketing consent remains separate even if an external mailing service consumes it later.

## Email templates

Email templates are fully managed through Studio and the admin API:

- stable template key and purpose;
- draft/published version lifecycle;
- subject, plain-text body, HTML/Markdown source, sender name/address, reply-to, and required context schema;
- preview contexts, validation, test-send, publication, and rollback;
- immutable template version attached to every queued delivery.

Code provides safe bootstrap templates and required-key/context definitions. Operators edit versioned content, not Python files. Rendering escapes variables by default and sanitizes allowed rich content.

Every transactional email has meaningful plain-text and accessible HTML alternatives. Tracking pixels are disabled in MVP.

## Durable delivery model

### EmailDelivery

- UUID and unique logical `idempotency_key`;
- classification, purpose, template key/version, and related event/cohort/registration/enrollment IDs;
- immutable recipient, subject, sender, and reply-to snapshots;
- minimal render context or immutable rendered bodies according to the retention decision;
- state, attempts, lease owner/expiry, next attempt, provider message ID, safe error summary, and timestamps.

States: `pending`, `leased`, `provider_accepted`, `delivered`, `retryable`, `ambiguous`, `suppressed`, `dead`, `hard_bounced`, and `complained`.

### EmailAttempt and provider events

- Each attempt records start/end, outcome, provider request correlation, and redacted error data.
- SES/SNS event delivery is signature-verified and deduplicated by provider event ID.
- Provider accepted is not displayed as delivered.
- Hard bounce and complaint create suppression state independent of optional marketing preferences.
- Unmatched but valid provider events are retained briefly for reconciliation.

### Worker semantics

- Business state and `EmailDelivery` are committed in the same transaction.
- Workers atomically lease pending/retryable rows and recover expired leases.
- Transient failures use bounded exponential backoff; permanent failures go to operator-visible dead state.
- An uncertain connection loss after provider submission becomes `ambiguous`; it is not blindly retried.
- Manual resend creates a new audited logical delivery related to the original; it is distinct from an automatic retry.
- The chosen bias is at-least-once critical communication: a rare duplicate is preferable to a missed confirmation/update, while idempotency and ambiguous-state reconciliation minimize both.

## Amazon SES

- Development sends from a verified `dtcdev.click` identity in `us-east-1`, where AWS account `817685572750` already has SES production access.
- Application runtime can remain in `eu-west-1`; the SES adapter uses its configured provider region explicitly.
- SPF, DKIM, DMARC, configuration sets, event destinations, quotas, and suppression behavior are Terraform-managed or referenced without taking ownership of unrelated shared identities.
- Development defaults to an allowlist/test recipients for rehearsals. Automated tests use console/in-memory delivery or SES simulator addresses.
- Sender, reply-to, and production domain require owner approval before production rollout.

## Abuse and privacy

- Registration and resend endpoints use CSRF, body limits, distributed rate limits by IP/email/event, and a honeypot; adaptive accessible challenge is added only when evidence warrants it.
- Responses do not disclose whether an email is registered.
- Join links and attendee lists are not public by default.
- Privacy acknowledgement and optional marketing consent are unbundled and versioned.
- CSV exports escape spreadsheet formulas and require a dedicated permission plus audit event.
- Raw IP/user-agent data has a short documented retention and never appears in routine logs.

## Studio and API capabilities

Both interfaces can:

- create/edit/preview/publish/cancel/archive events;
- assign/reorder people as speakers/hosts;
- inspect registration counts and individual records subject to PII permissions;
- cancel/reactivate/correct a registration through explicit reasoned commands;
- export authorized registrations;
- mark attendance individually or through bounded import;
- initiate/resume event update, cancellation, reminder, or follow-up operations;
- manage email template versions, preview, test, publish, and roll back;
- inspect delivery/attempt/provider state and retry or resolve safe failures;
- inspect suppression and provider-health diagnostics.

## Acceptance criteria

- Registration replay, refresh, and concurrency create one logical row and one delivery per purpose/version.
- Verification/cancellation tokens are hashed, scoped, expiring, revocable, redacted, and link-scanner safe.
- Event reschedule/cancellation produces correct calendar sequence and idempotent messages.
- Worker and provider failures produce the specified durable states without losing business data.
- Course reminders and event messages share one auditable delivery model.
- Every event/email management action has Studio/admin API parity and negative authorization tests.
