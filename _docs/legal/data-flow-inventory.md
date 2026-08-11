# Legal and analytics data-flow inventory

Issue: `#125`  
Candidate base: `8c0239254ab690918e6930c2368614dcfc709cf5`  
Inventory date: 11 August 2026

This inventory bounds the source-reviewed legal pages and analytics preferences. The architecture
and privacy authority remains `_docs/specs/07-security-privacy-operations.md`; the other cited specs
define the product flows summarized here. This document does not authorize a new processor,
production setting, message purpose, data import, or external mutation.

## Current website flows

| Surface | Data and purpose | Current boundary | Authority |
|---|---|---|---|
| Public content | GitHub-authored articles, podcasts, books, docs, FAQ, wiki, courses, events, and public editorial people | Validated versioned read models; public requests perform no GitHub fetch or render | Specs 01, 02, and 03 |
| Account and private member profile | Email identity, sign-in links, session/security state, preferences, and member-entered onboarding fields | One account-owned private profile, never inferred to or from a public editorial person | Specs 01 and 07 |
| Slack onboarding | Profile completion, eligibility, grant/delivery state, and reveal of the approved shared join URL | No Slack invitation API, public directory, or membership synchronization | Specs 01, 06, and 07 |
| Course and cohort participation | Registration/enrollment, confirmed minimal profile snapshot, target, comment, privacy evidence, separate optional marketing consent, work, reviews, scores, complaints, leaderboard, and certificates | Course-owned records and shared application services; profile edits affect future snapshots | Specs 01, 04, and 07 |
| Event registration | Event, identity/contact fields requested by the flow, privacy evidence, separate optional marketing consent, verification/management state, and attendance/status | Accountless or account-associated event services with protected hashed tokens | Specs 01, 05, and 07 |
| Transactional email | Versioned website intent and redacted delivery/reconciliation status | Relay is the only website delivery boundary; provider infrastructure stays downstream | Specs 05, 07, and issue #124 |
| Country suggestion | CloudFront country signal on explicit zero-cache onboarding/profile consumers | Editable suggestion only; raw header is not separately retained, logged, or used as a public cache key | Specs 01, 07, and 08 |
| Security and operations | Bounded request/correlation IDs, safe route/status metadata, audit events, durable job state, and redacted diagnostics | No secrets, credentials, complete query, cookies, raw IP, profile values, registration answers, or message bodies in logs/metrics | Specs 07 and 08 |

## Cookie and optional analytics boundary

- Django session and CSRF cookies are necessary security/application cookies.
- `dtc_analytics_consent` is a necessary preference cookie. Version 1 stores only `allow` or
  `deny`, lasts 180 days, uses `Path=/`, `SameSite=Lax`, and adds `Secure` on HTTPS.
- This candidate adds no analytics provider loader, measurement ID, endpoint, event taxonomy,
  account join key, attribution store, tracking pixel, tag-manager editor, or behavioral profile.
- Before a choice and after rejection or withdrawal, no optional analytics script, endpoint,
  event, or cookie exists. Rejection/withdrawal also expires recognized optional first-party
  analytics cookie prefixes without deleting the necessary preference cookie.
- Allowing records the preference only. It cannot send data because the repository contains no
  enabled provider path. A later provider/configuration change requires its own groomed scope,
  typed Studio/admin API parity, CSP reconciliation, privacy review, and production authorization.
- Local, CI, development, and preview environments therefore remain incapable of sending
  production analytics under this candidate. The existing executable source guard remains intact.

## Provisional retention represented in the policy

- unverified registration records: 14 days;
- event registration personal data: 90 days after the event;
- educational records: while active, then the published deletion/anonymization schedule;
- website Relay intent and redacted delivery metadata: 180 days;
- security audit evidence: one year; and
- development logs: 30 days.

The educational-record schedule and any production exception remain subject to owner/privacy
review, as required by specification 07 and open decision 12.

## Explicit human acceptance gate

The authoritative repository does not contain confirmed DataTalks.Club operator identity, public
address, authorized representative, legal/privacy contact, VAT or tax identifiers, final minors
policy, or current German statutory/dispute-resolution wording. The candidate deliberately does not
infer those values or copy them from AI Shipping Labs. An authorized owner/legal reviewer must:

1. supply and approve the exact Impressum identity, address, representative, contact, and tax data;
2. approve current statutory and dispute-resolution wording;
3. approve the Terms governing-law/contact wording and the Privacy controller/contact, transfer,
   retention, rights, and minors wording; and
4. confirm the displayed last-updated date before production legal acceptance.

Until that review is recorded, `/impressum` remains visibly marked as requiring verification and
issue #125 cannot receive production acceptance.
