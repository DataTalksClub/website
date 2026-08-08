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

Recommendation: new canonical pages live under `datatalks.club/courses/<course>/cohorts/<cohort>/`. Keep static SEO articles at their existing `/blog/*.html` paths. Route the old course hostname to compatibility views until all consumers migrate, then replace it with a Terraform-managed redirect Lambda using an explicit path map.

Owner input needed: inventory authenticated API clients before redirecting them; cross-host redirects may drop authorization. Keep direct compatibility responses until those clients are migrated.

## 6. Event and course registration verification

Recommendation: accountless registration requires email verification before confirmation/enrollment. Do not create a learner account until email ownership is verified.

Alternative: immediate confirmation is simpler but makes third-party email abuse and typo registrations easier.

## 7. Event capacity

Recommendation: no capacity/waitlist in MVP. Add it only with explicit pending-seat, verification TTL, promotion, and concurrency rules.

## 8. Legacy timezone

Recommendation: interpret legacy naive main-site event/book timestamps as `Europe/Berlin`, store new timestamps in UTC, and always retain/display an IANA timezone.

Owner input needed if legacy data used a different convention.

## 9. Staff identity

Recommendation: OIDC provider with enforced MFA, Django groups/permissions, and local break-glass access only.

Owner input needed: use the existing shared Cognito setup or configure a different organizational OIDC provider.

## 10. Email provider and semantics

Recommendation: unified durable outbox with direct Amazon SES transactional delivery from the verified `dtcdev.click` identity in `us-east-1` for development. Retain Datamailer only as a temporary migration adapter. Prefer a rare duplicate over a missed critical message at the unacknowledged-provider boundary.

Owner input needed: development/production from and reply-to addresses, and whether Datamailer should remain the long-term delivery provider.

## 11. Email scope

Recommendation: event/course registration verification and confirmation, cancellation, event cancellation/reschedule, enrollment changes, deadlines, peer review, scores, certificates, account verification, and password recovery. Marketing/newsletters are deferred.

## 12. Privacy retention

Recommendation: use the provisional periods in the security/privacy spec, then obtain owner/privacy review before production data import.

Owner input needed: privacy contact, minors policy, educational-record retention, and deletion/anonymization expectations.

## 13. Search

Recommendation: PostgreSQL search for public site/docs/FAQ/Podwiki in MVP while preserving FAQ JSON and Podwiki search/filter contracts. Retire the separate Podwiki search Lambda only after relevance parity.

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
