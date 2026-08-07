# Django app boundaries

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
- `accounts`: the email-based user model, staff authentication, groups, permissions, and future API credentials.
- `content`: versioned GitHub-owned read models and public content presentation.
- `content_sync`: GitHub adapters and candidate-release orchestration; depends on `content`, never the reverse.
- `courses`: database-owned courses, cohorts, and learner workflows.
- `events`: database-owned events, registrations, attendance, and exports.
- `email_app`: transactional outbox, delivery attempts, provider events, and suppression.
- `studio`: staff HTML presentation only; mutations call owning application services.
- `api`: versioned admin JSON presentation only; mutations call the same services as Studio.
- `jobs`: queue wrappers, scheduling, leases, heartbeat, and operator diagnostics.

Apps may depend on `accounts` for actor or ownership references and on `core` for generic primitives. Cross-domain behavior is coordinated by an application service at the owning boundary, using scalar identifiers for queued work. Circular imports are not an acceptable coordination mechanism.
