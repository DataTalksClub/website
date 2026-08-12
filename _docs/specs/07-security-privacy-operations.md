# 07 - Security, privacy, accessibility, and operations

Status: draft

## Trust boundaries and protected data

Actors include anonymous readers/registrants, learners, content maintainers, course/event/support/email/content operators, site administrators, auditors, API service principals, bots/attackers, and external GitHub/AWS/identity services.

Protected assets include:

- private member-profile values, completion history, Slack eligibility, and the Slack join secret;
- learner, registrant, submission, peer-review, attendance, and certificate data;
- privacy and optional marketing-consent evidence;
- account verification/password reset/registration management tokens;
- staff sessions, API credentials, GitHub/Relay/OIDC credentials, and the separate Relay callback
  signing secret; provider credentials remain outside the website boundary;
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
- Add a honeypot first. CAPTCHA/challenge is deferred from the MVP and requires a later reviewed
  issue with accessibility evidence if traffic eventually warrants it.
- Never synchronously fetch arbitrary learner-submitted links. Any background validation uses DNS/IP revalidation, protocol/port allowlists, redirect limits, response-size limits, and blocks private/link-local/metadata networks.
- Sanitize GitHub Markdown/HTML and prohibit unsafe protocols, handlers, inline scripts, traversal, symlink escapes, and arbitrary remote includes.
- Formula-neutralize CSV exports.
- Do not log or place secrets/tokens in URLs, metrics, traces, analytics, or error reports.

## Shared-cache trust and poisoning boundary

CloudFront positive caching is deny-by-default. A deterministic versioned viewer-request function,
without a key-value store, removes every viewer-supplied internal classification header and labels
the request `anonymous-v1` only when it has no Authorization, signed URL/cookie, session/auth/CSRF
or unknown credential-shaped cookie, preview/management token, or malformed credential-like state.
Classifier error or ambiguity is private. The marker participates in public-HTML cache keys and is
forwarded through the protected edge/origin boundary.

Explicit private paths always use the zero-TTL policy. A credential-bearing request on a mixed public
path reaches origin, where both Django and an edge origin-response guard force private/no-store
before storage. A response is ineligible when it has `Set-Cookie`, private/no-store, `Vary: *`, CSRF,
PII, identity/capability state, an unsafe method, or a disallowed status. If edge-function and policy
ordering cannot prove that isolation, the route remains zero-TTL.

Anonymous-cacheable templates contain no account-sensitive navigation/data and set no cookie or
CSRF token. If a route cannot render one anonymous-stable representation, it remains disabled.

Cache/origin keys and forwarded values use the exact per-route allowlists in specifications 02 and
08. Host, arbitrary headers/cookies/query, raw Accept-Encoding, CloudFront country, and tracking
values cannot create hidden variants. Duplicate/case/encoding/path/Host tests cover cache poisoning.
All cache policies have `min_ttl = 0`; errors are not cached except the clean public-404 class.

`CloudFront-Viewer-Country` is accepted only on zero-TTL onboarding/profile consumers when trusted
edge mode is configured. CloudFront removes a viewer lookalike and supplies its genuine value across
the CloudFront-prefix-list, expected Host, and generated origin-verification boundary. Django accepts
only a known uppercase ISO 3166-1 alpha-2 code; special/unknown/lowercase/malformed values,
local/test/direct-origin requests, and missing headers yield no suggestion. The form labels the
value as an editable suggestion that the member must confirm or replace. It has lower precedence
than account/registration migration data and never enters a public cache key. The raw header and
unconfirmed suggestion are never stored in the profile or ordinary logs/metrics; only the country
code explicitly confirmed by the member becomes profile data. Onboarding remains fully functional
without the suggestion.

## Edge and application abuse controls

One Terraform-managed CloudFront-scope WAF ACL starts managed and rate rules in count mode. The
initial reviewed set covers common web threats, known-bad input, IP reputation, malformed/oversize
requests, disallowed methods/paths, and rate rules with these per-source-IP five-minute starting
thresholds:

- 2,000 ordinary cacheable public `GET`/`HEAD` requests;
- 300 search, unknown-query, or other origin-bound anonymous reads;
- 300 `/api/` reads;
- 60 signup, login, profile, Slack, and course/event registration requests;
- a separately bounded emergency rate/block rule controlled by reviewed Terraform input.

Count mode runs for at least seven representative development days and records only aggregate
matches and reviewed false positives before blocking. Known exploit/IP-reputation rules may move
earlier only with deterministic fixtures and no legitimate-user regression. A blocked/rate-limited
request returns a safe non-cacheable response and provably performs no ALB, Django, database, worker,
or email work.

Application services keep stricter business limits: login/verification/resend/profile/registration
use normalized identity plus safe IP class, and admin API uses principal/capability. Edge IP limits
are not authorization and do not solve distributed botnets. User-Agent is never trusted as crawler
identity, there is no unconditional verified-bot bypass, and `robots.txt` expresses crawl preference
rather than enforcement. Production crawlers use ordinary public limits. Baseline high-rate,
cache-busting, and known-reputation controls plus alarms/emergency rule cover MVP; targeted/advanced
bot control, fraud/account-takeover, CAPTCHA, challenge, and guaranteed botnet prevention are out of
scope. Only plan-included common/self-identifying bot analytics or controls may be considered when
the selected tier supports them without changing this contract.

## Identity and authorization

- Use one custom email-based user model from the first migration for learners and staff.
- Human staff login uses OIDC with provider-enforced MFA; local staff passwords are break-glass only.
- Learner authentication supports verified email and may add social/OIDC login without creating duplicate accounts.
- Signup and social return collect no member-profile values before verified ownership. A server-side
  path-only intent resumes Slack, one active course-registration campaign, or account settings
  without placing email, token, invite secret, or an external next URL in a location.
- `MemberProfile` is private and owner-readable/writable except for explicit support capabilities.
  It cannot create, infer, publish, or authorize a GitHub-backed editorial Person.
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

MemberProfile and every onboarding, self, Slack, Studio, and admin-member response are private,
no-store, noindex, absent from sitemap/search/public serializers, and excluded from public caches.
Profile free text is escaped plain text and no analytics event contains profile values. Aggregate
metrics may use only step, completion version, delivery state, and safe failure category. Optional
profile URLs accept only safe HTTP/HTTPS values, are not synchronously fetched, and render with safe
external-link attributes.

Account export contains the member's profile, completion/grant metadata, their course
registrations' minimized shared-profile snapshots, and the separately registration-owned normalized
email, target, comment, privacy-notice, and optional marketing-consent evidence, but never the shared
Slack secret. Correction uses the normal accounts service. The
deletion/anonymization workflow removes or anonymizes profile PII, future Slack eligibility,
`CustomUser` compatibility projections, search/cache/export copies, and queued deliveries, and
prevents reveal/resend for disabled/quarantined/deleted accounts. Historical minimized
shared-profile snapshots and separate registration-owned fields/evidence follow the approved
educational-record retention and are not silently rewritten outside that legal workflow. The
exception leaves non-PII reconciliation evidence and restored backups replay the deletion tombstone.

Recommended initial retention, pending owner/privacy review:

- unverified public registrations and their abuse metadata: 14 days;
- event registration PII: 90 days after the event unless operational/legal need is documented;
- learner enrollment, submissions, grading, certificates, and consent evidence: retained while the educational record is active, then anonymized/deleted according to a published schedule;
- website email rendered bodies/raw provider payloads: never stored; Relay owns its provider-data
  retention under its separately reviewed policy;
- website logical delivery intent and redacted Relay projection metadata: 180 days;
- hard-bounce/complaint suppression: Relay-owned and retained as long as necessary to prevent
  harmful resends; the website keeps only the required redacted projection;
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

Structured logs include request/job/message IDs, route/operation, duration, status, safe actor class, content release, cohort/event, queue age, and delivery identifiers. Raw emails, submission content, Cookie, Authorization, session/CSRF values, complete query, raw IP, country suggestion/header, origin-verification value, preview/management token, Slack link, profile value, and response body are excluded from default logs.

Application events also include the stable non-secret fields `version`, `source_sha`, and
`image_digest`, copied from the sealed runtime identity. They are log/evidence fields, not metric
dimensions. Health, deployment smoke, release/rollback records, and recovery evidence compare the
same triplet; they never substitute a mutable tag for the immutable digest or expose the raw task
environment/provider response.

Metrics and alerts cover:

- public/learner/Studio/API availability, latency, and error rate;
- registration/enrollment success, verification, throttling, and invariant failures;
- content freshness, failed/quarantined releases, active commit, and link/search build failures;
- worker heartbeat, scheduled-job lateness, oldest durable email job, submission replays/conflicts,
  dead/ambiguous Relay projections, callback/reconciliation freshness, and queue depth;
- Relay submission latency/availability plus redacted provider-accepted, delivered, bounce,
  complaint, rejection, suppression, quota, and cost summaries supplied by Relay;
- course scoring/peer-assignment failures and deadline-job lateness;
- API authentication/authorization failures, rate limiting, high-risk operations, and export volume;
- database/storage health, backups, restore verification, and edge/origin failures;
- aggregate route/viewer class, cache status and age bucket, cache hit ratio, origin-request rate,
  bytes/status, invalidation state/latency, WAF rule label/action, allowance usage, and edge-function
  errors using bounded labels.

Each alert has an owner, threshold, runbook, and escalation path. Optional tracing must fail safely and is configured at process boot, not through a runtime database secret.

Standard CloudFront/WAF logs use encrypted storage, bounded retention, least privilege, and field
omission/redaction. Real-time logs are not required. Development remains noindex/nofollow for cache
hits, misses, redirects, errors, assets, and WAF denials; cacheability cannot change a production
canonical or make preview content indexable.

## Edge cost-plan decision and rollback

Before subscribing or applying, produce a redacted read-only comparison from current AWS
documentation and the latest 30 days of workload-only metrics, or the available shorter window with
an explicit projection. Compare request/transfer, hit/miss, WAF evaluated/blocked, log ingestion,
invalidation, edge compute, ALB origin request/transfer, ECS, RDS, and residual service cost under
normal, 10x viral, cache-busting, and distributed-bot scenarios. Record exact account/distribution
eligibility, behaviors/rules/associations, logging mode, bot feature level, allowances, and every
unsupported association for pay-as-you-go and then-current Free, Pro, Business, and Premium plans.

Use the cheapest sufficient result without weakening security, observability, cache correctness, or
evidence:

1. Free is eligible only when every required behavior, policy, WAF rule, logging control, and usage
   allowance fits.
2. Prefer Pro when the real eligibility check accepts the exact candidate and forecast with
   headroom.
3. If Pro rejects a requirement, compare measured/projected pay-as-you-go and Business, then choose
   the cheaper sufficient option. Business is not selected only for advanced bots outside MVP.
4. Premium or advanced products require a new owner-approved cost issue.
5. If flat-plan lifecycle cannot be reproduced through accepted infrastructure automation, retain
   pay-as-you-go; never create a console-only subscription.

Prices, allowances, included/unsupported features, and treatment of WAF/DDoS-blocked traffic are
rechecked at implementation because they change. Residual ALB/ECS/RDS, non-included edge compute,
and unrelated services remain budgeted. Alarms have named owners/runbooks and cover 50%, 80%, and
100% of selected allowance/forecast; cache hit ratio below 70% after warm-up; origin rate above
twice reviewed normal peak for 15 minutes; WAF block/rate, 4xx/5xx, invalidation failure/age,
edge-function error; and ALB/ECS/RDS load/cost rising despite edge controls.

Emergency action is a reviewed Terraform rate/block toggle or cache-disable/TTL-zero rollback. It
never broadens caching or origin access. Cache/WAF/invalidation alarms and rollback are exercised
without a live secret or production data before production promotion.

## Service targets

Recommended initial production targets, subject to approval:

- 99.9% monthly availability for public reads and registration/enrollment submission;
- 95th percentile cached public response below 500 ms and uncached HTML below 1 second at the edge region under normal load;
- 99% of approved transactional intents accepted by Relay within 5 minutes, excluding Relay outage
  or suppression; provider acceptance and delivery remain separate later states;
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
- Restore holds historical email jobs until Relay reconciliation proves each intent safe, reapplies
  privacy tombstones, and validates active content-release pointers. It never enables Datamailer or
  a second sender.

## Failure behavior

- GitHub failure: serve last known good content and alert on freshness.
- Invalid content commit: quarantine with diagnostics; do not partially publish.
- Database transaction failure: no ghost registration/enrollment or email.
- Worker/Relay failure: keep the logical intent and durable job, expose lag, and retry only safe
  pre-ambiguity work. Uncertain acknowledgement becomes `ambiguous` and is never automatically
  resent.
- Search/graph failure: retain prior projection or degrade search without losing source pages.
- Concurrent course/event edits: reject stale revision.
- Concurrent scoring/registration/enrollment: database invariants win and tasks remain idempotent.
- Deployment regression: hold email jobs, roll back the immutable app image, and reconcile Relay
  without sending an old logical intent twice or re-enabling Datamailer.
- Deployment identity mismatch: fail before mutation or success recording; rollback/recovery uses
  the exact recorded VERSION, source SHA, digest, task definitions, and service counts without a
  clock or fabricated timestamp.
- Relay client/callback-secret expiry and provider-health degradation: alert before expiry or
  failure and follow tested rotation/reconciliation runbooks; the website owns no provider secret.

## Acceptance criteria

- Threat model and authorization matrix are reviewed before production data is loaded.
- Security headers, CSRF, session/token lifecycle, rate/body limits, SSRF, XSS, CSV injection, and error-redaction tests pass.
- Privacy export/deletion/anonymization and retention jobs include course and event data.
- Member export/correction/deletion includes profiles, grants, compatibility projections, queued
  deliveries, retained minimized shared-profile snapshots, and the separate registration-owned
  email/target/comment/notice/consent evidence without exposing profile values or Slack secrets in
  logs, metrics, audits, or evidence.
- Cache classification, poisoning, country trust, WAF count/block, cheapest-sufficient plan evidence,
  allowance alarms, and TTL-zero/emergency rollback pass local/policy/deployed gates.
- Scoped Relay credentials/callback verification, redacted projections, callback reconciliation,
  accepted-versus-delivered handling, and ambiguity-without-automatic-resend pass without a website
  provider credential, direct Amazon SES/Datamailer path, rendered body, or raw provider event.
- WCAG automated and manual acceptance passes for critical flows.
- Alerts and runbooks exist for every release-critical failure mode.
- A restore drill meets the approved RPO/RTO and does not resend historical email or resurrect deleted data.

## Protected historical registration sources

Historical registration adapters operate only on an explicitly registered protected reference.
They verify whole-source checksum and versioned schema, stream bounded rows, and keep provider
registration deduplication keys only in memory until aggregate derivation finishes. The application
database retains no source path, filename, archive entry, attendee row/identifier/digest, name,
email, answer, timestamp, notice, consent, or raw payload.

Each registered source also names a code-owned reconciliation profile. The pinned Luma profile
requires the exact 64 proposal/95 review partition; the pinned Eventbrite profile requires the exact
200 proposal/9 review/27 source-missing partition. Missing profiles, extra or overlapping bridge
keys, and any profile cardinality drift fail closed. The synthetic profile exists only under the
test settings and cannot bypass reconciliation in development or production.

The adapter rejects hidden/traversal/symlink/duplicate archive entries, unsafe structure,
decompression/size/count excess, malformed encoding/CSV/JSON, checksum drift, mismatched Luma
pairs, duplicate event IDs, unsupported schemas, and unsupported/unknown statuses before
activation. Eventbrite XLSX is recorded only as bounded `unsupported_xlsx` aggregate evidence and
is never opened or converted. Exact ordered Eventbrite CSV headers use the three pinned SHA-256
fingerprints joined with byte `0x1f`; unsupported/reordered/missing/extra/duplicate headers
quarantine the entry.

Only aggregate/schema facts may enter source control, tests, logs, metrics, screenshots, APIs, or
reports. The real protected reconciliation is an authorized HUMAN gate under #64 retention and
disposal rules. Failures preserve the last accepted total and create no email/newsletter/sponsor,
consent, or provider work.

### Protected course registration-count source

The course baseline adapter accepts only an opaque reference from the code/configuration-owned
`COURSE_REGISTRATION_COUNT_SOURCES` registry. Each entry pins the fixed SQLite adapter, exact whole
file checksum and byte size, schema version and schema-contract checksum, capture/freeze/cutoff
times, and native start. It rejects symlinks, non-regular or oversized files, checksum/size/schema
drift, mutable or inconsistent timing, duplicate campaign/course identities, missing or changed
current targets, mixed/null timestamps, post-cutoff rows, and incomplete aggregates before any
pointer changes.

SQLite is opened read-only with bounded execution and trusted schema disabled. Derivation selects
only campaign/course identity, registration count, and minimum/maximum creation time; source PII
columns are schema-checked but never selected. The database, logs, audit, Studio/API, screenshots,
and reports retain only safe aggregate/provenance facts. Public reads never reopen the protected
file and fail closed when its registered checksum, size, adapter, schema, or schema-contract fact
changes. Real-source validation remains an authorized HUMAN gate; repository and browser tests use
synthetic SQLite sources under project-local `.tmp/` only.
