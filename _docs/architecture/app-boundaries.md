# Django app boundaries

Shared bootstrap, execution-context, redaction, command/query, and durable-work conventions are
defined in [`shared-primitives.md`](shared-primitives.md).

The website is one deployable Django project. Dependencies point from presentation and orchestration toward domain applications and `core`; domain apps do not import Studio or API code.

```text
public views   studio   api   jobs
       \         |      |     /
        application services
                  |
 accounts  content  courses  events  email_app
                  |
                 core

content_sync -> content + core
domains -> email_app intent service -> jobs -> Relay (leased call after commit only)
email_app/jobs may receive identifiers from domains, but domains do not import worker tasks
```

- `core`: bootstrap configuration, health, middleware, request IDs, and future audit primitives.
- `accounts`: the email-based user model, private one-to-one member profile, Slack-access
  eligibility, staff authentication, groups, permissions, and future API credentials.
- `content`: versioned GitHub-owned read models and public content presentation.
- `content_sync`: GitHub adapters and candidate-release orchestration; depends on `content`, never the reverse.
- `courses`: database-owned courses, cohorts, and learner workflows.
- `events`: database-owned events, registrations, attendance, and exports.
- `email_app`: logical `EmailDelivery` intents, Relay idempotency/correlation metadata, redacted
  transport projections, and callback/reconciliation commands. It owns no canonical template body,
  renderer, provider adapter, provider attempt/event stack, suppression engine, or sender worker.
- `studio`: staff HTML presentation only; mutations call owning application services.
- `api`: versioned admin JSON presentation only; mutations call the same services as Studio.
- `jobs`: queue wrappers, scheduling, leases/fences, heartbeat, and operator diagnostics. A leased
  durable job is the only website boundary that calls Relay, and it does so only after the business
  transaction commits.

Apps may depend on `accounts` for actor or ownership references and on `core` for generic primitives. Cross-domain behavior is coordinated by an application service at the owning boundary, using scalar identifiers for queued work. Circular imports are not an acceptable coordination mechanism.

`accounts.MemberProfile` is the database-owned community-membership and learner-onboarding record.
It is not `content.Person`, which remains the GitHub-owned public editorial identity for authors,
speakers, guests, hosts, instructors, and maintainers. Neither record implies the other. A future
reviewed relation may connect them, but it must not synchronize fields or grant authority.

The accounts application service is the only write boundary for member-profile values and their
temporary `CustomUser` compatibility projections. Course registration asks that service for scalar,
confirmed values and writes a deliberately minimized immutable shared-profile snapshot owned by
`courses`; it does not take ownership of the profile. Outside that snapshot, the registration owns
its normalized verified-email snapshot, target campaign/cohort snapshot, course-specific comment,
privacy-notice evidence, and optional marketing-consent evidence. Profile completion coordinates a
`SlackAccessGrant`, one `email_app.EmailDelivery` intent, and its durable job in the same database
transaction. Workers receive only scalar identifiers, resolve the current Slack secret after
commit, and submit allowed context to Relay; Relay owns validation/rendering and transport. Thus
`accounts` never imports a worker task or stores a secret-bearing rendered message. Provider
acceptance is distinct from delivery, and an ambiguous acknowledgement is never automatically
resent. Datamailer remains read-only migration/history/reconciliation input, never a sender.
