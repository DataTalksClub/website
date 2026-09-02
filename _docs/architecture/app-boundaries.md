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

content_sync -> content + courses + core
domains -> email_app intent service -> jobs -> Relay (leased call after commit only)
email_app/jobs may receive identifiers from domains, but domains do not import worker tasks
```

- `core`: bootstrap configuration, health, request IDs, shared redaction, non-identity browser and
  request-boundary controls, and future audit primitives.  The decision-free threat/control
  matrix for these controls lives in [`../security/non-identity-threat-control-matrix.md`](../security/non-identity-threat-control-matrix.md);
  identity and high-risk semantics remain with their owning issues.
- `accounts`: the email-based user model, private one-to-one member profile, Slack-access
  eligibility, staff authentication, groups, permissions, and future API credentials.
- `content`: versioned GitHub-owned read models and public content presentation.
- `content_sync`: GitHub adapters and candidate-release orchestration; it may call validated
  application services in `content` and `courses`, but those domain apps never import sync
  adapters.
- `courses`: database-owned courses, cohorts, and learner workflows.
- `events`: database-owned events, registrations, attendance, and exports.
- `email_app`: logical `EmailDelivery` intents, Relay idempotency/correlation metadata, redacted
  transport projections, and callback/reconciliation commands. It owns no canonical template body,
  renderer, provider adapter, provider attempt/event stack, suppression engine, or sender worker.
  It additionally owns the Relay recipient-link ingress described below.
- `studio`: staff HTML presentation only; mutations call owning application services.
- `api`: versioned admin JSON presentation only; mutations call the same services as Studio.
- `jobs`: queue wrappers, scheduling, leases/fences, heartbeat, and operator diagnostics. A leased
  durable job is the only website boundary that *initiates* a Relay call, and it does so only after
  the business transaction commits. The recipient-link ingress below is the single named exception,
  and it initiates nothing: it answers a request Relay already caused.

## Relay recipient-link ingress

Relay renders open-tracking, click-tracking, and unsubscribe URLs into mail from one
`PUBLIC_BASE_URL`. That value names the public website, because `relay.datatalks.club` is an
operator surface behind OIDC and the public unsubscribe surface belongs on the website. Relay has
no public ingress and is reachable only in-VPC over plain HTTP, so the website answers those three
paths and bridges each request to Relay.

Which website host carries them is a deployment value that changes once. Until the stage-2 apex
swap, `datatalks.club` still ALIASes the legacy static site and cannot serve a Django route, so
`PUBLIC_BASE_URL` is `https://prod.datatalks.club`; after the swap it becomes
`https://datatalks.club`. The application therefore never derives one of these URLs from a request
host, and holds neither value: a mail client fetching a pixel presents no host worth trusting.

This is a second, deliberately narrow Relay boundary, owned by `email_app`, and it is bounded by
these rules:

- It is inbound-caused. Relay's own link brings the request; the website starts nothing.
- The website holds no recipient token logic. Hashing, lookup, scope semantics, engagement
  counters, and the unsubscribe mutation stay in Relay, which owns the recipient data. Duplicating
  them is how two codebases drift apart on a security-sensitive contract.
- Every route is anonymous, session-free, and CSRF-free, touches no website database row on the
  read path, and emits no `Set-Cookie` for an anonymous caller.
- Failure behavior is decided per route, not shared. The open pixel always returns a valid GIF. A
  click is redirected only on Relay's verdict; otherwise the reader is shown the destination and
  chooses. An unsubscribe is never refused: when Relay does not answer, the request is persisted
  and replayed by a leased durable job, which is the ordinary after-commit boundary above.
- A recipient token is identifying. It never appears in a log record, an observability event, an
  exception, or an error report, on this seam or in the access log.
- With no Relay configured the routes answer `404` and the click route never redirects, so an
  unconfigured deployment cannot become an open redirect.

Apps may depend on `accounts` for actor or ownership references and on `core` for generic primitives. Cross-domain behavior is coordinated by an application service at the owning boundary, using scalar identifiers for queued work. Circular imports are not an acceptable coordination mechanism.

Repository curriculum adapters parse an immutable source graph and pass it to the `courses`
curriculum import service. The graph types belong to the receiving domain boundary, so the course
service remains usable without importing a GitHub adapter; fetching, parsing, webhook handling,
and durable job orchestration stay outside `courses`.

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
