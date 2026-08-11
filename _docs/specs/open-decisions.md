# Open decisions

Status: owner review required

The specs use the recommendations below so implementation can be concrete. Approving the specification index approves these defaults unless an item is changed explicitly.

## 1. GitHub-backed editing

Recommendation: MVP Studio can validate, preview, sync, activate, roll back, diagnose, and open GitHub edits. It does not create commits or pull requests.

Alternative: Studio creates branches/PRs through a GitHub App. This adds credential, conflict, review, and authorship workflows.

## 2. Course-platform adoption

Recommendation: copy the existing course-management Django apps, migrations, behavior, compatibility APIs, and tests into this repository at a recorded clean commit. Evolve them in place instead of reimplementing the platform.

## 3. Course curriculum and cohorts

Recommendation: new Course owns reusable family identity. The current edition-like Course becomes Cohort and keeps its current homework, projects, criteria, enrollments, submissions, scores, and certificates. Add a complete cohort duplication workflow. Defer reusable/versioned curriculum.

## 4. Current course data grouping

Recommendation: create a reviewed mapping file from every legacy course-edition slug to a course family and cohort slug. Never infer solely by stripping a year.

Owner input needed: authoritative family names/slugs for unusual or one-off legacy courses.

## 5. Course URL consolidation

Recommendation: new canonical pages live under `datatalks.club/courses/<course>/cohorts/<cohort>/`.
Static SEO articles preserve their established `/blog/<slug>.html` canonicals. Their clean
`/blog/<slug>` and trailing-slash aliases redirect directly to the `.html` final while preserving
the raw query. Route the old course hostname to compatibility views until all consumers migrate,
then replace it with a Terraform-managed redirect Lambda using an explicit path map.

Owner input needed: inventory authenticated API clients before redirecting them; cross-host redirects may drop authorization. Keep direct compatibility responses until those clients are migrated.

## 6. Accountless event registration verification

Recommendation: accountless event registration requires email verification before confirmation and
does not create a learner account. Course registration is a different, account-owned flow: it
requires a durable account with verified email ownership and a completed member profile before
creating the confirmed registration, and it never creates an anonymous `CourseRegistration`.

Alternative: immediate confirmation is simpler but makes third-party email abuse and typo registrations easier.

## 7. Event capacity

Recommendation: no capacity/waitlist in MVP. Add it only with explicit pending-seat, verification TTL, promotion, and concurrency rules.

## 8. Legacy timezone

Recommendation: interpret legacy naive main-site event/book timestamps as `Europe/Berlin`, store new timestamps in UTC, and always retain/display an IANA timezone.

Owner input needed if legacy data used a different convention.

## 9. Staff identity

Recommendation: OIDC provider with enforced MFA, Django groups/permissions, and local break-glass access only.

Owner input needed: use the existing shared Cognito setup or configure a different organizational OIDC provider.

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

## 11. Email purpose catalog

Decision still required in #22: approve the owner, audience, Relay sender/reply-to, template/context,
idempotency/version inputs, and retention class for every non-course purpose. The accountless event,
Slack access, account, and other course/event lifecycle purposes described in these specs are target
capabilities, not authorization to send. Until #22 resolves each entry, only the approved
development `courses` sender/purpose may progress and every other purpose or sender fails closed.
Marketing/newsletters remain deferred.

## 12. Privacy retention

Recommendation: use the provisional periods in the security/privacy spec, then obtain owner/privacy review before production data import.

Owner input needed: privacy contact, minors policy, educational-record retention, and deletion/anonymization expectations.

## 13. Search

Recommendation: use a backend-portable search projection for public site/docs/FAQ/Podwiki while
preserving FAQ JSON and Podwiki search/filter contracts. The content/search issue owns ranking and
indexing details. Retire the separate Podwiki search Lambda only after relevance parity.

## 14. Development network cost

Recommendation: dedicated two-AZ VPC, public ALB and tightly restricted public-IP ECS tasks, isolated private RDS, and no NAT gateway in development. Production uses private tasks and production-grade egress.

Alternative: NAT-backed private tasks in development more closely mirror production but add recurring cost.

## 15. Service and recovery targets

Recommendation: use the initial SLO/RPO/RTO values in the security/operations spec and revise after measuring development behavior and cost.

## 16. Analytics and tracking

Recommendation: preserve only necessary existing analytics after privacy review; no new tracking pixels or behavioral analytics in MVP. Development/previews do not send production analytics.

## 17. High-risk approvals

Recommendation: reauthentication and explicit confirmation for staff-role grants, credentials, PII exports, content activation, bulk email/event cancellation, grading repair, and certificate mutation. Add dual approval later if operational evidence warrants it.

## 18. Production cutover scope

Recommendation: do not combine URL redesign or broad SEO improvements with cutover. Preserve first, measure, then improve in later releases.

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
