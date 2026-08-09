# 07 - Security, privacy, accessibility, and operations

Status: draft

## Trust boundaries and protected data

Actors include anonymous readers/registrants, learners, content maintainers, course/event/support/email/content operators, site administrators, auditors, API service principals, bots/attackers, and external GitHub/AWS/identity services.

Protected assets include:

- learner, registrant, submission, peer-review, attendance, and certificate data;
- privacy and optional marketing-consent evidence;
- account verification/password reset/registration management tokens;
- staff sessions, API credentials, GitHub/SES/OIDC secrets;
- grading, leaderboard, capacity, content publication, redirect, and email integrity;
- sender reputation, audit evidence, backups, and SEO equity.

## Web and application security

- Run a supported Django security release and apply security updates promptly through locked `uv` dependencies.
- Require HTTPS; enable HSTS only after the development/cutover hostname behavior is verified.
- Use secure, HttpOnly, SameSite cookies, exact allowed hosts/trusted origins, and correct forwarded-protocol handling.
- Apply CSRF protection to all state-changing browser requests.
- Use a restrictive Content Security Policy, `frame-ancestors`, MIME-sniffing protection, a deliberate referrer policy, and permissions policy.
- Mark Studio, admin API, learner dashboards, registration management, preview, export, and PII responses `private, no-store`.
- Return safe errors without stack traces, secrets, tokens, provider payloads, raw SQL, or object-existence clues.
- Apply request/body/file/decompression/time limits at edge and application layers.
- Use AWS WAF/edge rate controls for broad abuse and database-backed per-email/event/principal limits for business actions.
- Add a honeypot first; use an accessible adaptive challenge only when traffic is suspicious.
- Never synchronously fetch arbitrary learner-submitted links. Any background validation uses DNS/IP revalidation, protocol/port allowlists, redirect limits, response-size limits, and blocks private/link-local/metadata networks.
- Sanitize GitHub Markdown/HTML and prohibit unsafe protocols, handlers, inline scripts, traversal, symlink escapes, and arbitrary remote includes.
- Formula-neutralize CSV exports.
- Do not log or place secrets/tokens in URLs, metrics, traces, analytics, or error reports.

## Identity and authorization

- Use one custom email-based user model from the first migration for learners and staff.
- Human staff login uses OIDC with provider-enforced MFA; local staff passwords are break-glass only.
- Learner authentication supports verified email and may add social/OIDC login without creating duplicate accounts.
- Account linking requires verified ownership and audited conflict resolution.
- Studio/API authorization is deny-by-default and checks function, object, and sensitive fields.
- Sessions have idle/absolute timeouts and immediate revocation on staff disablement.
- Password recovery and email-change flows invalidate affected tokens/sessions appropriately.
- API principals are scoped, expiring, revocable, independently identifiable, and never share human credentials.
- Role grants, PII exports, content activation, bulk email/cancellation, grading repairs, and certificate changes receive explicit high-risk controls.

## Privacy baseline

Engineering must document and the owner/privacy contact must approve:

- controller and processor roles, regions, subprocessors, transfer mechanism, and incident contacts;
- purpose/lawful basis per data field and integration;
- versioned notices at event and course registration;
- separate optional marketing consent, never inferred from attendance, enrollment, or transactional communication;
- access, correction, deletion/anonymization, restriction, objection, and portable export workflows suitable for users with and without accounts;
- deletion propagation to projections, search, caches, exports, email providers, and restored backups;
- minors policy and recording/public-profile expectations.

Recommended initial retention, pending owner/privacy review:

- unverified public registrations and their abuse metadata: 14 days;
- event registration PII: 90 days after the event unless operational/legal need is documented;
- learner enrollment, submissions, grading, certificates, and consent evidence: retained while the educational record is active, then anonymized/deleted according to a published schedule;
- email rendered bodies/raw provider payloads: 30 days where diagnosis requires them;
- email delivery metadata: 180 days;
- hard-bounce/complaint suppression: retained as long as necessary to prevent harmful resends;
- security/audit events: one year, with PII minimized;
- application logs: 30 days in development and a reviewed production period.

Retention jobs are idempotent, report counts, produce audit events, and are testable against restores. Backups may expire naturally, but a deletion tombstone/replay procedure prevents erased data from being silently restored into live use.

## Accessibility

Target WCAG 2.2 AA for the public site, learner course experience, Studio, and transactional email.

- Semantic landmarks, logical headings, skip links, and keyboard-complete navigation.
- Visible unobscured focus, adequate contrast, minimum target sizes, reduced-motion support, and 200% zoom/reflow.
- Explicit labels, autocomplete purpose, instructions, error summary, linked field errors, and preserved input.
- Success/failure and asynchronous status updates announced to assistive technology.
- Human-readable dates plus machine-readable values and explicit timezone.
- Alt text policy for meaningful images and caption/transcript links for audio/video.
- No color-only status or inaccessible anti-bot challenge.
- Automated checks plus keyboard, screen-reader, zoom/reflow, and reduced-motion manual acceptance.

## Observability

Structured logs include request/job/message IDs, route/operation, duration, status, safe actor class, content release, cohort/event, queue age, and delivery identifiers. Raw emails and submission content are excluded from default logs.

Application events also include the stable non-secret fields `version`, `source_sha`, and
`image_digest`, copied from the sealed runtime identity. They are log/evidence fields, not metric
dimensions. Health, deployment smoke, release/rollback records, and recovery evidence compare the
same triplet; they never substitute a mutable tag for the immutable digest or expose the raw task
environment/provider response.

Metrics and alerts cover:

- public/learner/Studio/API availability, latency, and error rate;
- registration/enrollment success, verification, throttling, and invariant failures;
- content freshness, failed/quarantined releases, active commit, and link/search build failures;
- worker heartbeat, scheduled-job lateness, oldest outbox row, retries, dead/ambiguous email, and queue depth;
- SES acceptance latency, bounce, complaint, rejection, suppression, quota, and cost;
- course scoring/peer-assignment failures and deadline-job lateness;
- API authentication/authorization failures, rate limiting, high-risk operations, and export volume;
- database/storage health, backups, restore verification, and edge/origin failures.

Each alert has an owner, threshold, runbook, and escalation path. Optional tracing must fail safely and is configured at process boot, not through a runtime database secret.

## Service targets

Recommended initial production targets, subject to approval:

- 99.9% monthly availability for public reads and registration/enrollment submission;
- 95th percentile cached public response below 500 ms and uncached HTML below 1 second at the edge region under normal load;
- 99% of transactional emails submitted to SES within 5 minutes, excluding provider outage/suppression;
- GitHub content freshness below 15 minutes after an accepted main-branch commit;
- production database RPO at most 15 minutes and service RTO at most 4 hours;
- development RPO 24 hours and RTO one business day.

## Backups and recovery

- Encrypted automated RDS backups with point-in-time recovery and documented retention.
- Final snapshots and deletion protection for persistent production data.
- Versioned S3 content assets with public-access block and least-privilege access.
- Git repositories are content provenance, not backups for database-owned data.
- Secrets have independent recovery/rotation procedures and are not assumed recoverable from database backups.
- Quarterly production restore drills and routine automated backup verification.
- Restore suppresses historical outbox work until reconciliation proves it is safe, reapplies privacy tombstones, and validates active content-release pointers.

## Failure behavior

- GitHub failure: serve last known good content and alert on freshness.
- Invalid content commit: quarantine with diagnostics; do not partially publish.
- Database transaction failure: no ghost registration/enrollment or email.
- Worker/SES failure: keep durable pending/retry state and expose lag to operators.
- Search/graph failure: retain prior projection or degrade search without losing source pages.
- Concurrent course/event edits: reject stale revision.
- Concurrent scoring/registration/enrollment: database invariants win and tasks remain idempotent.
- Deployment regression: roll back immutable app image without sending old outbox messages twice.
- Deployment identity mismatch: fail before mutation or success recording; rollback/recovery uses
  the exact recorded VERSION, source SHA, digest, task definitions, and service counts without a
  clock or fabricated timestamp.
- Secret/provider expiry: alert before expiry and follow a tested rotation runbook.

## Acceptance criteria

- Threat model and authorization matrix are reviewed before production data is loaded.
- Security headers, CSRF, session/token lifecycle, rate/body limits, SSRF, XSS, CSV injection, and error-redaction tests pass.
- Privacy export/deletion/anonymization and retention jobs include course and event data.
- WCAG automated and manual acceptance passes for critical flows.
- Alerts and runbooks exist for every release-critical failure mode.
- A restore drill meets the approved RPO/RTO and does not resend historical email or resurrect deleted data.
