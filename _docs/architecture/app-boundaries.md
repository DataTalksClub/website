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
email_app/jobs may receive identifiers from domains, but domains do not import worker tasks
```

- `core`: bootstrap configuration, health, middleware, request IDs, and future audit primitives.
- `accounts`: the email-based user model, private one-to-one member profile, Slack-access
  eligibility, staff authentication, groups, permissions, and future API credentials.
- `content`: versioned GitHub-owned read models and public content presentation.
- `content_sync`: GitHub adapters and candidate-release orchestration; depends on `content`, never the reverse.
- `courses`: database-owned courses, cohorts, and learner workflows.
- `events`: database-owned events, registrations, attendance, and exports.
- `email_app`: transactional outbox, delivery attempts, provider events, and suppression.
- `studio`: staff HTML presentation only; mutations call owning application services.
- `api`: versioned admin JSON presentation only; mutations call the same services as Studio.
- `jobs`: queue wrappers, scheduling, leases, heartbeat, and operator diagnostics.

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
`SlackAccessGrant` and an `email_app.EmailDelivery` intent in the same database transaction. Workers
receive only scalar identifiers and resolve the current Slack secret after commit, so `accounts`
never imports a worker task or stores a secret-bearing rendered message.
