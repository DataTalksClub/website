# Open decisions

Status: owner review required

The specs use the recommendations below so implementation can be concrete. Approving the specification index approves these defaults unless an item is changed explicitly.

## 1. GitHub-backed editing (resolved by #12)

Resolved: MVP Studio validates, previews, syncs, activates, rolls back, diagnoses, and opens GitHub edits.
It never creates commits, branches, or pull requests — GitHub content stays read-only from the
website's side, the same pattern already proven in the sibling AI Shipping Labs Django site
(webhook push → clone → parse → upsert, no write-back). The staged prepare/ready/activate/rollback
pipeline already built in `content/services.py` is more elaborate than that sibling's direct
sync-and-upsert; whether to keep that extra staging is a separate, non-blocking implementation
question, not part of this decision.

## 2. Course-platform adoption (resolved by #13)

Resolved: copied the existing course-management Django apps, migrations, behavior, compatibility APIs, and tests into this repository as-is from clean commit `98a235283904b4ef9ad29e196298540756cf1bcc`. Evolve them in place instead of reimplementing the platform. Implementation tracked in #30.

## 3. Course curriculum and cohorts (resolved by #14)

Resolved: new Course owns reusable family identity. The current edition-like Course becomes Cohort and keeps its current homework, projects, criteria, enrollments, submissions, scores, and certificates. Cohort duplication workflow implemented (`courses/tests/test_course_duplication.py`). Reusable/versioned curriculum remains deferred.

## 4. Current course data grouping (resolved by #15)

Resolved: a reviewed mapping file from every legacy course-edition slug to a course family and
cohort slug, never inferred by stripping a year. The family list is expected to grow as new
courses launch — this is a living mapping, not a fixed enum.

`courses/course_family_catalog.py` already carries a reviewed 2024+ mapping for the six current
families (`de-zoomcamp`, `ml-zoomcamp`, `llm-zoomcamp`, `mlops-zoomcamp`, `sma-zoomcamp`,
`ai-dev-tools`), fail-closed on anything unmapped.

The confirmed complete pre-2024 legacy-edition inventory is DE/MLOps/ML Zoomcamp 2021-2023 (7
cohort slugs); `ml-zoomcamp-2021` has certificates like every other edition, and the recovered
`sha1(email)` hash is retained as the legacy identifier/alias. Regex-based slug inference is
approved only as a one-time authoring aid inside import/fixture scripts, never live in production
code.

Implementation gap (not part of this decision, but required to satisfy it): the historical import
added in `2032ceb` still creates pre-2024 cohorts without an explicit `course=`, so it still runs
through `courses/models/cohort.py`'s regex year-stripping fallback in `Cohort.save()`. That fallback
must be removed once the explicit mapping entries exist, and `ml-zoomcamp-2021` certificates still
need locating/importing. Tracked in #224.

## 5. Course URL consolidation (resolved by #16)

Approved: new canonical pages live under `datatalks.club/courses/<course>/<cohort>/` (no
`cohorts/` segment — matches the routes already implemented in `courses/urls.py`).
Static SEO articles preserve their established `/blog/<slug>.html` canonicals. Their clean
`/blog/<slug>` and trailing-slash aliases redirect directly to the `.html` final while preserving
the raw query. Route the old course hostname to compatibility views until all consumers migrate,
then replace it with a Terraform-managed redirect Lambda using an explicit path map.

Owner input received: no known external/third-party API consumers of `courses.datatalks.club`
beyond browsers and the known internal paths already catalogued in
`_docs/compatibility/course-route-contracts.json` (115 routes, all classified `preserve`).

Resolved standalone: ICS calendar event UIDs (`courses/views/course_calendar_events.py`) keep the
`@courses.datatalks.club` suffix permanently as a stable namespace string, independent of what
happens to the host itself — changing it would orphan every already-subscribed calendar entry.

Owner override (2026-08-26): skip the authenticated production probe requirement and per-route
owner/volume gate — a plain redirect is fine. Any authenticated route that doesn't survive the hop
cleanly is an acceptable one-time inconvenience; affected users will just update their bookmarks.
Activate the Terraform-managed redirect Lambda using the existing route contract manifest without
waiting on that verification step.

## 6. Accountless event registration verification (resolved by #17)

Resolved: event registration offers two paths. OAuth (Google/GitHub) sign-in is the preferred,
easier path — already-verified by the provider, confirmed immediately, no extra step. A plain
email-only path stays available as a fallback for anyone who doesn't want to sign in: it starts
`pending` and becomes `confirmed` only after the registrant clicks an emailed verification link.
Neither path creates a learner account. Course registration is a different, account-owned flow: it
requires a durable account with verified email ownership and a completed member profile before
creating the confirmed registration, and it never creates an anonymous `CourseRegistration`.

Considered and rejected: requiring OAuth for event registration with no email-only fallback (the
sibling AI Shipping Labs site's pattern — its `EventRegistration.user` is non-nullable). Simpler to
build, but forces sign-in before registering for a talk; kept as the *preferred* path instead of the
*only* path.

Implementation gap (not part of this decision, but required to satisfy it): `CourseRegistration.user`
(`courses/models/cohort.py:334`) is currently nullable and must become required; no
`EventRegistration`/pending-verification model exists yet and needs building from scratch.

## 7. Event capacity (resolved by #18)

Resolved: no capacity/waitlist in MVP — events are unlimited-capacity. No partial `capacity` field
should exist implying unsupported correctness. Add capacity/waitlist only later as an explicit,
fully-specified feature with pending-seat, verification TTL, promotion, and concurrency rules.

## 8. Legacy timezone (resolved by #19)

Resolved: interpret legacy naive main-site event/book timestamps as `Europe/Berlin`, store new
timestamps in UTC, and always retain/display an IANA timezone. Matches what's already implemented
in `scripts/build_public_projection.py`.

DST-ambiguous and leap-day timestamps: no special handling — plain `zoneinfo.ZoneInfo` conversion,
Python's default `fold=0` (earliest occurrence) for the one ambiguous hour at fall-back. This
matches the existing convention in both `course_management/` (the legacy app already copied into
this repo) and the sibling AI Shipping Labs site — neither does anything special for this case
either. No manual exception-review process needed unless a specific past event is known to fall in
that window.

## 9. Staff identity (resolved by #20; MFA still pending externally)

Resolved: reuse the existing shared Cognito pool (`us-east-1_H7nJu52Bs`, `auth.dtcdev.click`,
already used for Datamailer's web auth per `aws-infra/sandbox/datamailer/variables.tf`) for Studio
staff login, rather than a separate provider. Django groups/permissions still gate authorization on
top of it; generic `is_staff` is not used as authorization.

Blocking gap: this pool does not currently have MFA enabled — tracked in
[DataTalksClub/aws-infra#24](https://github.com/DataTalksClub/aws-infra/issues/24). Staff OIDC login
should not be considered production-ready until that lands.

Related: [AI-Shipping-Labs/website#1464](https://github.com/AI-Shipping-Labs/website/issues/1464)
proposes the sibling site adopt the same pool, for one consistent staff-identity story instead of a
per-repo `is_staff` gate there.

Owner override (2026-08-26): no dedicated break-glass credential process (no separate
ownership/storage/rotation policy). Emergency recovery relies on the management API — which must
match everything Studio can do (tracked in the parity epic #7) — with direct database access as the
last-resort fallback if the API itself is unreachable.

## 10. Email provider and semantics (resolved by #21)

Resolved: Relay is the sole canonical template-rendering and transactional-email delivery service.
The website atomically commits the business mutation, one logical `EmailDelivery` intent, and one
durable job; only a leased job contacts Relay after commit. Relay owns immutable published template
versions, safe validation/rendering, sender resolution, provider submission/lifecycle, suppression,
callbacks, reconciliation, and authoritative transport status. The website keeps the business
intent and a redacted status projection only. New website code calls neither Amazon SES nor
Datamailer directly.

Provider acceptance is not delivery. An uncertain acknowledgement becomes `ambiguous` and is never
automatically resent; reconciliation or an audited operator action resolves it. Datamailer is
read-only migration/history/reconciliation input, receives no new sends, and is never a rollback
sender.

The development Relay sender ID `courses`, mapped by Relay to
`DataTalks.Club Courses <courses@dtcdev.click>`, is approved by #21. Production configuration and
broad recipients remain out of scope.

Owner override (2026-08-26): marketing/newsletters are no longer deferred — see #22.

## 11. Email purpose catalog (resolved by #22)

Resolved: no per-purpose pre-approval gate. Any purpose described in these specs — accountless
event, Slack access, account, course/event lifecycle, and now marketing/newsletter — may send once
implemented; there is no separate owner/sender/retention sign-off step blocking it before launch,
and no fail-closed default pending individual approval.

Recipients control what they receive through an account-settings preference center rather than a
pre-launch approval process. Retention uses one unified policy across every purpose instead of a
bespoke retention class per purpose (see #12). Newsletter/marketing sends are in scope and subject
to the same preference-center opt-out and unified retention policy as every other purpose.

## 12. Privacy retention (resolved by #23)

Resolved: use the provisional periods already drafted in the security/privacy spec (unverified
registrations 14 days, event PII 90 days post-event, delivery metadata 180 days, audit events 1
year, etc.) as approved defaults.

Privacy contact: Alexey Grigorev, alexey@datatalks.club. Minors policy: no restriction — teenagers
taking courses is fine, no age-gating or special handling (age isn't verified at signup, so this is
a blanket policy rather than a confirmed absence of under-13/16 users). Deletion/anonymization: full
erasure of a learner's personal data on request. Educational-record retention: leaderboard/
certificate display already defaults to an anonymous generated name, so erasure doesn't create
identity gaps in the common case; if a learner opts into showing their real name (e.g. on a
certificate), that choice is permanent — an already-issued certificate isn't retroactively
anonymized by a later deletion request.

## 13. Search (approved 2026-08-26)

Approved: use a backend-portable search projection for public site/docs/FAQ/Podwiki while
preserving FAQ JSON and Podwiki search/filter contracts. The content/search issue owns ranking and
indexing details. Retire the separate Podwiki search Lambda only after relevance parity.

## 14. Development network cost (resolved by #25)

Resolved: dedicated two-AZ VPC, public ALB, tightly restricted public-IP ECS tasks (ingress only
from the ALB security group), isolated private RDS (PostgreSQL ingress only from the task security
group), and no NAT gateway in development — avoiding the ~$65+/month baseline NAT Gateway cost for
an environment that isn't serving real traffic, without weakening task/database isolation.
Production retains private-task plus NAT/VPC-endpoint options. A NAT-backed sandbox remains
available later as a separately costed change, not a blocker. Cross-repo follow-up filed as
[DataTalksClub/aws-infra#25](https://github.com/DataTalksClub/aws-infra/issues/25) to confirm the
provisioned sandbox VPC matches this topology and to decide production's NAT/VPC-endpoint choice.

## 15. Service and recovery targets (resolved by #26)

Approved: the drafted initial targets in the security/operations spec — 99.9% monthly availability
for public reads and registration/enrollment submission; 95th-percentile cached response <500ms,
uncached HTML <1s at the edge; 99% of approved transactional intents accepted by Relay within 5
minutes; GitHub content freshness <15 minutes after an accepted main-branch commit; production
database RPO ≤15 minutes, service RTO ≤4 hours; development RPO 24 hours, RTO one business day.

Named alert/runbook owners per target are not part of this approval and remain open for whoever
picks up the observability implementation (#66).

## 16. Analytics and tracking (resolved by #27)

Approved: preserve only necessary existing analytics after privacy review; no new tracking pixels
or behavioral analytics in MVP. Development/previews do not send production analytics.

Owner addition (2026-08-26): build a proper GA4 integration, scoped to acquisition-only signal
(pageviews, sessions, referrer/UTM source) with GA4 "enhanced measurement" (behavioral event
auto-tracking) explicitly off, wired behind the existing analytics-consent preference center.
Implementation tracked in #225.

## 17. High-risk approvals (resolved by #28)

Approved: reauthentication and explicit confirmation for staff-role grants, credentials, PII exports, content activation, bulk email/event cancellation, grading repair, and certificate mutation. No dual approval for now; add it later only if operational evidence warrants it.

## 18. Production cutover scope (approved with a narrowed scope, 2026-08-26)

Approved, narrowed to articles: static SEO articles (`/blog/<slug>.html` and their canonicals, per
decision #5) keep the strict preserve-first rule — no URL redesign or SEO changes bundled into
cutover; measure, then improve later. Reason: articles are the content that's mainly indexed by
Google, so their existing URLs/canonicals carry real search ranking that a change could damage.

Owner override: every other resource is not indexed as heavily, so the risk is lower — non-article
routes may have their URLs updated and get SEO experimentation at or around cutover instead of
waiting for a later release.

## 19. Member profile and Slack onboarding

Resolved MVP default: use one private, account-owned `MemberProfile` after verified email ownership.
It is independent of the GitHub-backed public editorial `Person` and is never linked or synchronized
by inference. Profile completion creates immediate automated Slack eligibility and a durable
transactional delivery for the shared join URL held in the approved secret channel. There is no
Slack invitation API, membership synchronization, public member directory, or manual review queue.

Required work status, professional role, and seniority vocabularies are code-defined and
migration-stable. Members enter shared values once, edit them in account settings, and confirm them
before first Slack access or each new course registration. Course registrations retain deliberately
minimal immutable shared-profile snapshots containing only profile UUID, completion schema version,
profile revision, snapshot timestamp, optional certificate/display name, country/region,
organization, work status, professional role, and seniority. Normalized email, target
campaign/cohort, course comment, privacy-notice evidence, and optional marketing-consent evidence
remain separate registration-owned values; profile edits affect only future registrations.

## 20. CloudFront cache, WAF, and cost plan

Resolved MVP default: positively cache only explicitly classified anonymous public `GET`/`HEAD`
responses through a generated route registry and fail every unknown, private, credential-bearing,
personalized, unsafe, search, or operational request to zero TTL/no-store. Preserve production URL,
canonical, robots, sitemap, structured-data, and indexing contracts; development stays noindex and
nofollow for hits, misses, redirects, errors, assets, and WAF denials.

Select the cheapest plan that accepts the exact reviewed distribution, cache/origin policies, WAF
rules, standard logging, infrastructure automation, and measured/projection allowance with
headroom. Free is eligible only when the complete contract fits. Prefer Pro when its real
eligibility check accepts the candidate; otherwise compare pay-as-you-go with Business and select
the cheaper sufficient option. Retain pay-as-you-go when flat-plan lifecycle cannot be reproduced
through accepted automation. Premium, targeted/advanced bot control, fraud/account-takeover,
CAPTCHA, challenge, and real-time logging require a separate owner-approved issue.
