# Critique

## Reflection

The specification was reviewed against the user requirements, audited source repositories, the existing course platform, the AI Shipping Labs reference, and failure/security/operations concerns.

Findings resolved in the draft:

- The initial risk of treating all four content repositories as generic Markdown was removed. Each now has an explicit adapter and its existing render/search/graph/JSON contracts are named.
- “Preserve links” was expanded into a measurable route, fragment, asset, internal/external destination, canonical, metadata, structured-data, sitemap, and robots manifest with zero unexplained release differences.
- Person is no longer modeled as one exclusive guest/speaker type; `Person.short` remains the stable imported key and roles are explicit ordered relationships.
- The conflict between GitHub as editorial source and “manage everything in Studio” is resolved for MVP: Studio manages sync/release/diagnostics and opens GitHub edits, while authoring remains GitHub-owned.
- Studio and API parity is enforceable through a capability registry and shared services rather than an aspiration or duplicate implementations.
- Event confirmation was upgraded from a direct web-request email into verified registration plus a durable outbox, idempotency, provider-event, suppression, and ambiguous-acknowledgement design.
- The course plan was changed from possible clean-room domain recreation to direct adoption of the existing Django code, migrations, compatibility APIs, and approximately 808 characterization/E2E tests.
- Course/Cohort scope was reduced to the required structural change: add reusable Course identity, evolve the current edition into Cohort, keep curriculum cohort-owned, and defer reusable curriculum versions.
- The final `courses.datatalks.club` Lambda is gated on browser and authenticated API consumer migration and uses an explicit path map rather than heuristic year stripping.
- Development indexability, privacy retention, accessibility, authorization, secrets, SSRF, concurrency, backups, restore, rollback, metrics, alarms, and cost/portability constraints are now explicit.

Remaining weaknesses are discovery/owner-input problems rather than missing architecture:

- Production access logs and client ownership are required to enumerate authenticated course API consumers.
- Legacy course-family grouping and naive timestamp timezone cannot be proven from schema alone.
- Privacy retention/minors rules, production email identities/provider, staff identity provider, and final SLO/RPO/RTO values require organizational decisions.
- Exact current URL/render baselines must be generated during Milestone 0; repository inspection identifies contracts but is not a substitute for crawling deployed/generated output.
- Live AWS resources and repository worktrees may change before implementation and must be re-read. No infrastructure mutation was made during planning.

Alternatives deliberately rejected for the first migration:

- a generic Markdown renderer, because each source has incompatible extensions and public behaviors;
- synchronous GitHub reads on public requests, because availability and atomic consistency would depend on GitHub;
- database/Studio ownership of editorial bodies, because the user explicitly retains GitHub as source of truth;
- reimplementing course workflows, because the current code/test corpus captures substantial corner-case behavior;
- reusable/versioned curriculum in the first model migration, because cohort-owned rows already isolate historical deliveries;
- blanket `is_staff` and plaintext global API tokens, because course/event/content responsibilities need scoped authorization;
- sending email before recording intent, because crashes create missing or unauditable messages;
- a blanket course-host redirect, because it would lose deep routes and can break authenticated non-GET consumers;
- combining URL redesign/SEO experimentation with cutover, because attribution and rollback become ambiguous.

## Human grilling

The plan has not been approved by the owner. The draft asks the owner to accept or change the recommendations in [`_docs/specs/open-decisions.md`](../specs/open-decisions.md), especially:

1. Is GitHub edit linking sufficient for Studio MVP, or must Studio create pull requests?
2. Who owns the authoritative course-family mapping and the inventory/migration of authenticated course API clients?
3. Which OIDC provider enforces staff MFA, and who controls break-glass access?
4. Which production sender/reply-to identities and delivery provider should be used after the Datamailer transition?
5. What privacy contact, minors policy, educational-record retention, deletion/anonymization, SLO, RPO, and RTO apply?
6. Is `Europe/Berlin` correct for legacy naive timestamps?
7. Are the recommended no-NAT development network and PostgreSQL Podwiki search acceptable subject to parity tests?

Recommended defaults are written into the specifications so implementation can proceed predictably after approval, but they are not recorded as human decisions yet.

## Accepted risks

None yet. Risks are proposed in the specifications and remain unaccepted until owner review.
