<!-- markdownlint-disable MD013 -->

# Event-linked Q&A integration

Status: architecture/provenance slice for [issue #215](https://github.com/DataTalksClub/website/issues/215). This document records the integration boundary and the source contract for the later implementation slices. It does not add models, routes, templates, assets, jobs, or migrations.

The website specifications are authoritative: [platform architecture](../specs/01-platform-architecture.md), [events and registration](../specs/05-events-registration-email.md), [Studio and admin API](../specs/06-studio-and-admin-api.md), [security/privacy/operations](../specs/07-security-privacy-operations.md), and [verification strategy](../specs/10-verification-strategy.md). The [app boundaries](app-boundaries.md) and [shared service/durable-work primitives](shared-primitives.md) define the implementation seams. DataQnA supplies proven Q&A behavior, not website ownership or security policy.

## Decision summary

- `events` owns the Event-to-Q&A relation and the Q&A application services. Public views, the participant/admin frontend, Studio, the admin API, durable jobs, and tests call that service boundary; none reimplements Q&A rules.
- The first backend is a native Django adapter backed by the website database and `jobs.DurableJob`. A `QnaBackend` protocol keeps the frontend and service DTOs independent of that storage choice. A remote DataQnA adapter is an extension point, not a second public surface or a browser dependency.
- The public share link is on the website host and is derived from the canonical Event path. A DataQnA room slug or remote room ID is never a second public Event identity and is never required in a participant URL.
- The reference transport is conditional `ETag` polling, not WebSockets. Keep it for the initial integration: audience polling is 4 seconds while visible and 30 seconds while hidden; the presenter polls at 1 second; the moderation console polls at 5 seconds. A presenter-only Server-Sent Events adapter is the recorded future extension if 1-second polling stops being sufficient. Do not add a socket registry in this issue.
- Event creation provisions exactly one Q&A session through the Event service, with a unique relation and an after-commit durable ensure/retry job. A failed wake-up or worker does not remove the committed Event or create a duplicate session.

## User flow and ownership boundary

1. An authorized Event service creates an Event. In the same transaction it ensures one Q&A session in `draft` and one durable provisioning intent keyed by the Event UUID. Replayed Event imports and the existing-Event backfill use the same ensure operation.
2. An event operator opens the Q&A from the website Event/Studio surface, sets the supported session settings, and receives the website-hosted share URL and QR URL. The operator can move the Q&A through its lifecycle, moderate questions, and open presentation mode.
3. An attendee follows the share URL or scans the QR code. The website serves the adapted participant page without account, email, or external Q&A-host knowledge. The page issues an anonymous signed participant cookie, asks for a question, optionally collects a display name according to the session settings, and lets the participant upvote or withdraw a vote.
4. An authorized event operator or a redeemed room-scoped co-host invite moderates the same question queue. They can mark a question answered, pin one question, delete it, fix a typo within the permitted admin operation, and open the presentation view. Event operators own Q&A settings and lifecycle; the exact settings/lifecycle scope of a co-host remains an explicit compatibility decision recorded below and is not assumed by this architecture slice.
5. Presentation mode shows the ranked queue, the readable share URL, and the QR code. The host can spotlight, pin, answer, undo the last answer, change theme, and use browser fullscreen. It has no delete control or unrelated Event data.

The ownership boundary is:

- `events.Event` remains the only Event identity. Its UUID is the internal management identity; its stable positive public ID and current title slug define the canonical public Event path. The Q&A relation may have its own internal UUID, but that UUID is not a public Event lookup key.
- The proposed `events.qna` service owns session settings/state, questions, votes, participant-scoped edit/vote state, co-host invites, rate-limit decisions, QR target construction, serialization, and Q&A audit calls. It must use scalar identifiers in queued work and must not import Studio, API, or frontend modules.
- Event operators receive capability- and object-scoped authorization through the website permission registry. Studio and `/api/v1/admin/` are adapters over the same service, with `If-Match`, `Idempotency-Key`, safe result, and audit parity. A co-host is not a website account and cannot manage the Event, Event identity, Event registrations, staff permissions, or other sessions.
- `core` supplies request/correlation IDs, redaction, service context, rate-limit primitives, and audit primitives. `jobs` supplies leases, fencing, scheduling, durable retry, and after-commit wake-up. Q&A does not create a private provider-attempt store or a second deployment.
- The public Event projection may expose a safe Q&A CTA/state/share destination when product presentation enables it. It never includes question text, participant identity, vote state, co-host credentials, or a management URL. The private Event/Studio surface exposes the complete safe session summary.

## Website-hosted routes and stable interface

The route shape below is the website contract. All paths are relative to the configured website host, including whichever development host `deploy/development_target.py` selects. There is no browser redirect to a separate Q&A host and no cross-origin participant API.

### Route shape

| Surface | Website route | Boundary |
| --- | --- | --- |
| Participant page/share URL | `GET /events/<public-id>/<current-title-slug>/qna/` | Public only when the Event is public and the Q&A is `open` or `closed`; `draft` is a safe `404`, archived Q&A is a safe `410`. The response is private/no-store/noindex because it may set a participant cookie and may contain moderator links. |
| Participant question API | `GET/POST /events/<public-id>/<current-title-slug>/qna/api/questions/` | Website-hosted JSON contract below; no account. `GET` accepts only the pinned source's allowlisted `sort` and `status` fields. `limit` and `cursor` are not part of `qna.v1`; the pagination/`next_cursor` examples in `docs/api.md` are stale and are not implemented by the pinned source. |
| Question edit/moderation | `PATCH /events/<public-id>/<current-title-slug>/qna/api/questions/<question-id>/` | Participant author scope or moderator/co-host scope; state-changing browser requests require Django CSRF. Management adapters additionally use the current revision/`If-Match`. |
| Vote | `POST/DELETE /events/<public-id>/<current-title-slug>/qna/api/questions/<question-id>/vote/` | Anonymous participant cookie, CSRF, idempotent add/remove, and the reference `409` closed-room behavior. |
| Co-host gate | `GET/POST /events/<public-id>/<current-title-slug>/qna/cohost/<name>/` | GET shows only the passcode form. POST validates the non-secret URL name plus passcode and sets a room-scoped co-host cookie. The passcode is never in the URL. |
| QR | `GET /events/<public-id>/<current-title-slug>/qna/qr.svg` and `GET .../qna/qr.png?size=<bounded>` | Public, cacheable image of the website share URL only. Draft/archived public delivery follows the page visibility policy; private Studio can preview a not-yet-live share target without making it public. |
| Host console | `GET /studio/events/<event-uuid>/qna/` for staff/event operators; `GET /events/<public-id>/<current-title-slug>/qna/host/` for a redeemed co-host (and an optional staff handoff) | Private/no-store/noindex. Both paths call the same service and hide unrelated Events. |
| Presentation | `GET /events/<public-id>/<current-title-slug>/qna/present/` | Private/no-store/noindex; independently authorizes an event operator or valid co-host. The participant page may show this link only to a viewer already authorized to moderate. |
| Admin API session surface | `/api/v1/admin/events/<event-uuid>/qna/` and child question/co-host/bulk action routes | UUID is allowed here because this is the management API. Capability registry, bearer principal, `If-Match`, `Idempotency-Key`, OpenAPI, and audit rules apply. No admin route resolves an Event by public ID. |

Event aliases and approved title changes retain the website’s exact one-hop canonicalization: a GET/HEAD old Event path reaches the current `/events/<public-id>/<current-title-slug>/qna/` path, and the QR target is always the current canonical share URL. Unsafe API methods do not blindly replay through a redirect; the frontend is configured with the canonical path and stale mutation requests receive a safe conflict/not-found result. No slug-only, date/title-derived, provider-ID, remote-room-ID, or Q&A-session-ID public lookup is added.

### Frontend/backend contract

The frontend consumes a versioned `qna.v1` contract through a relative `api_base` supplied in the server-rendered page configuration. It knows the share URL, QR URL, settings, capability flags, and operation results; it does not know whether the service uses native Django rows or a future server-side adapter.

The page configuration contains only safe, bounded values:

```json
{
  "contract": "qna.v1",
  "api_base": "/events/123/example-event/qna/api",
  "share_url": "/events/123/example-event/qna/",
  "qr_url": "/events/123/example-event/qna/qr.svg",
  "state": "open",
  "settings": {
    "allow_names": true,
    "require_names": false,
    "answered_placement": "separate",
    "default_sort": "popular"
  },
  "max_length": 315,
  "can_ask": true,
  "can_vote": true,
  "banner": null,
  "host_links": null
}
```

`host_links` is omitted or null for an ordinary participant. It may contain only the website host console/presentation paths for a viewer already authorized by the service. It never contains a co-host passcode, participant cookie, Event UUID in a public response, or remote backend credential.

The stable JSON operations are:

- `GET questions` returns `{items, counts, etag, state}` and accepts only the allowlisted `sort` and `status` query fields. There is no `limit` or `cursor` query in the pinned implementation. Each current question item contains `question_id`, escaped plain-text `text`, nullable `author_name`, `status` (`visible` or `answered` in a collection), integer `score`, `pinned`, `created_at`, and `answered_at`. `deleted` is an internal terminal state and is absent from public and moderator collections. For a participant it also contains `own`, `voted`, and `editable`; these are relative to the signed cookie and are absent from key/service-principal views. The current code is the source of truth here: the older API example’s `anonymous` and `editable_until` fields are not emitted by the pinned implementation; an adapter must normalize that drift rather than expose two contracts.
- `POST questions` accepts `{text, author_name?}` and returns `201` with the created item. Text is trimmed, plain text, 1–315 characters; names are optional/required/rejected according to settings and bounded to 60 characters. A new question is immediately `visible` and starts at score 1 through the author’s implicit vote.
- `PATCH question` accepts the reference fields `text`, `status`, and `pinned`, subject to author/moderator scope. Public authors can only edit or withdraw their own visible question during the 300-second window and before a second vote. Moderators can fix text, set `visible`/`answered`/`deleted`, and set the singular room pin. Website management mutations also carry the current revision and require `If-Match`.
- `POST`/`DELETE vote` return `{score, voted}`. A repeated add or remove is a no-op at the vote-row boundary; a participant cannot vote for their own question beyond the implicit starting vote.
- Bulk moderation is a bounded, partial-success operation (`answer`, `delete`, `pin`, `unpin`, at most 100 question IDs). It is a management command, requires explicit confirmation and `Idempotency-Key`, and returns safe per-item result codes rather than question text.
- Errors retain the reference safe envelope `{ "error": { "code": "...", "message": "..." } }`, with website request ID/field-error additions where the adapter requires them. Preserve `400` validation, `401` missing management authentication, `403` scope denial, `404` non-public/missing objects, `409` closed/conflicting/idempotency or stale mutation, `410` archived, `429` with `Retry-After`, `405`, and `304` conditional-read behavior. Error text never identifies a hidden Event, invite, participant, or question unnecessarily.

`ETag` is a weak validator derived from the ordered public question representation, including score, status, pin, and text. The server returns the same `ETag` in the header and response body; `If-None-Match` returns `304` with no body and `Cache-Control: private, no-store`. Optimistic submit/vote and host actions are reconciled by the next poll. This is the frontend/backend seam: changing storage or transport must not change these DTOs, status codes, or optimistic failure behavior.

## Provisioning, idempotency, and after-commit retry

The native implementation uses a proposed unique `EventQnaSession` relation owned by `events`; the name is architectural, not an instruction to add it in this slice. It stores the Q&A session state/settings and backend reference, while questions, votes, co-hosts, and rate counters remain Q&A-owned data. The relation’s uniqueness is a database constraint, not an application-only check.

```text
Event command transaction
  ├─ Event identity and fields
  ├─ get-or-create one EventQnaSession (draft)
  └─ dispatch_after_commit(events.qna.provision, event_id, stable dedupe key)
             │ commit
             └─ on_commit best-effort wake
                    └─ leased/fenced worker → backend.ensure_session(...)
                                      └─ retry/reconcile without a second session
```

Rules:

1. Only the Event application service provisions Q&A. Do not use a model signal, view-side create, or frontend request. Event creation, the unique session relation, and the durable job intent commit atomically. A transaction rollback creates none of them.
2. The deduplication key is stable and independent of title, slug, request ID, or retry count, for example `event-qna-provision:<event-uuid>:v1`. The durable payload contains only an opaque Event UUID and bounded non-secret version data. It contains no URL, email, question text, participant ID, cookie, passcode, token, or provider payload.
3. `dispatch_after_commit` persists the job in the Event transaction and registers only a best-effort wake after commit. If wake-up, queue submission, or a worker fails, the committed pending/retry-wait row remains sweepable by the scheduler. A worker leases and fences the job, reloads current Event/session state, and never trusts an old serialized title, slug, authorization decision, or state.
4. Native `ensure_session` is a safe read/create/reconcile operation: concurrent calls converge on the one unique relation and return the existing session. A replay after a completed job is a no-op. A duplicate Event import first resolves the existing Event identity and then runs the same ensure path.
5. The remote adapter extension, if later approved, is server-to-server only. Its worker calls the provider after commit with the same stable idempotency key and immutable request hash. A timeout or lost response is reconciled by that key/provider status; it is never treated as permission to blindly create another room. The website stores only an opaque backend reference and safe status, not provider credentials or raw responses. The native adapter has no network side effect.
6. Existing Events are handled by a bounded, resumable backfill command that enumerates Event UUIDs, submits one deduplicated ensure job per Event, records counts/safe failures, and can be rerun. It must not open Q&A sessions automatically, rewrite Event public identity, or import standalone DataQnA rooms/questions.
7. Provisioning failure does not roll back an Event. Studio shows a safe `pending`/`retrying`/`blocked` state and an audited repair/retry command. The retry command reuses the provisioning intent; it is not a new session create.

Session lifecycle is deliberately separate from Event lifecycle:

```text
draft ──open──> open ──close──> closed ──archive──> archived
  └─archive     ^                 └─reopen──────────┘
                └──────── reopen ──────────────────
```

The compatibility rules are `draft -> open|archived`, `open -> closed|archived`, `closed -> open|archived`, and `archived -> open|closed`. Expiry closes an open Q&A; expiry alone never deletes it. An archived session is `410` to participants but remains visible to its authorized hosts for reopening. The reference archive undo/delete clock is seven days and the reference default retention is 365 days; the implementation must map those values to a website-approved Q&A retention class and make any privacy-owner tightening explicit. `null`/indefinite retention is not enabled by accident.

The Event visibility gate is additional: a Q&A cannot become public through an open session while its Event is draft, cancelled, or archived. Event cancellation/archive hides the public share route without granting a new Q&A state transition; authorized Event management can inspect the session and apply the normal Q&A lifecycle. This avoids making a Q&A state silently rewrite Event lifecycle or public identity.

## Behavior preservation and website adaptations

### Authentication and anonymous participation

- Staff authentication is the website’s OIDC/MFA/session/capability system. Do not copy DataQnA’s Cognito client, `@datatalks.club` room-admin bootstrap, Google-only admin session, or DataQnA API-key mechanism. The admin API uses the website’s scoped bearer principals; Studio uses the website’s secure session and capability registry.
- Participants never sign in, provide an email, or create an account. On the first Q&A page/API visit, issue a secure, HttpOnly, SameSite, signed random opaque participant identifier scoped to the Q&A retention policy. It carries no personal data and grants no authority. Clearing it creates a new participant, as in the reference.
- The participant page and all browser state changes use the website CSRF contract. Anonymous participation is not a reason to exempt question, vote, withdraw, or co-host-form POSTs from CSRF. The page must not make cross-origin API calls.
- Participant-relative `own`, `voted`, and `editable` fields are computed at request time and force private/no-store responses. They never enter Event public projections, shared caches, analytics, or logs.

### Co-hosts and authorization

- A co-host invite is two halves: a non-secret normalized name in the website-hosted path and a separate generated/readable passcode. The passcode alphabet excludes ambiguous glyphs; matching ignores case, separators, and surrounding whitespace and uses constant-time comparison.
- The wrong name and wrong passcode have the same safe failure. The link alone never authenticates. The redeemed cookie is secure, HttpOnly, SameSite, signed, room/session-scoped, and expires in 30 days; the invite itself lasts only as long as the Q&A/Event policy and is immediately ineffective after revocation on the next request.
- Event admins may create/list/revoke invites. An authorized private response may show the generated passcode only under the reviewed co-host-management policy; it must never appear in a URL, public Event/Q&A projection, OpenAPI example, log, metric, trace, screenshot, or audit. A later implementation must choose hash/encrypted-at-rest handling without weakening the split-link contract.
- Provenance conflict: `docs/specification.md` lines 62–68 say that a co-host cannot change room settings, state, or expiry, while `tests/test_cohosts.py` lines 162–177 assert that a co-host can change `default_sort` and close the session. The pinned documentation and tests therefore disagree; neither is silently promoted to a website fact.
- The website choice is unresolved pending PM/security acceptance. Proposed conservative default (not yet approved): a co-host may moderate, pin, answer, delete, and use presentation mode, but may not change Q&A settings, expiry, or lifecycle; the event operator retains those controls. If the broader tested behavior is selected, update the capability matrix and its positive/negative tests explicitly before implementation.
- In either choice, a co-host cannot manage Event metadata or lifecycle, change the canonical Event/share path, add staff/admin grants, create/read/revoke co-host invites, create API credentials, enumerate sessions, or reach another Event. Every endpoint re-checks the current invite/session relation; the cookie is not authority by itself.

### Questions and moderation

- A submitted question is immediately visible. There is no review queue, hidden status, pause switch, downvote, or recoverable UI hide operation. Supported statuses are exactly `visible`, `answered`, and `deleted`; deletion removes the question from public and author views.
- Plain text is escaped on output. The 315-character product limit is not a mutable session setting. Names remain optional/required according to settings and are capped at 60 characters.
- The author may edit text or withdraw/delete only during the first five minutes and before any additional upvote. The author cannot change another person’s question, pin, answer, or restore a deleted question.
- Score is the atomic upvote count. Submission records the author’s implicit vote as score 1. One participant can add one vote per question and withdraw it; repeated requests do not double-count or decrement below the recorded vote. An author cannot add another vote to their own question.
- Popular ranking is score descending with oldest-first ties; recent ranking is newest-first. A pinned question leads either ordering. Pin is singular per session; pinning a new question unpins the old one atomically. Answering or deleting a pinned question clears its pin and updates safe counters.
- Console actions may show ordinary busy/error states. Presentation actions retain the reference optimistic behavior: pin/answer renders immediately, marking answered offers one-level undo, no spinner blocks the projected screen, and a refusal rolls back the local change and announces a safe cue.

### Rate limits and errors

The website must preserve, or explicitly review before changing, these current DataQnA limits:

| Action | Reference scope and limit |
| --- | --- |
| Question submit | 1 per participant/session every 10 seconds; 20 per participant/session per hour |
| Vote | 120 per participant/session per hour |
| Question abuse | 300 question submissions per source IP across sessions per hour |
| General public traffic | 2,000 requests per source IP per 5 minutes |
| Co-host redemption | 10 attempts per source IP per 5 minutes |

Use the website’s distributed/application limit service and route-class WAF controls rather than copying DynamoDB counter rows. IP data is hashed with a deployment salt and retained only for the rate window. Exceeded limits return `429` and `Retry-After`, do not disclose participant/session existence, and do no downstream mutation. Website edge limits remain defense in depth, not authorization.

### Polling, ETags, and presentation

- The room page polls the same questions endpoint every 4 seconds while visible and every 30 seconds while hidden. It sends `If-None-Match`; an unchanged result is `304`. It applies optimistic submit/vote state and reconciles on the next successful poll.
- The moderation console uses the same endpoint and reference conditional-read behavior. Presentation mode polls the same endpoint every 1 second while visible, with `sort=popular` and only visible questions. The one presenter connection is the deliberate cost/performance boundary; the audience remains polling.
- WebSockets are explicitly not part of the initial decision. Do not add connection registries, fan-out, long-polling, third-party realtime scripts, or push infrastructure. If later measurements show that presenter-only 1-second polling is inadequate, evaluate presenter-only SSE behind the same `QnaBackend`/frontend contract; the audience still polls unless a separate accepted decision changes that.
- Presentation preserves manual progression, spotlight, visible pin/answer controls with accessible names, selected-question keyboard arrows, `Escape` for spotlight/QR overlay, reduced-motion behavior, a readable full question without line clamp, and no unrelated Event names or moderation-delete controls.

### QR and link behavior

- QR encodes only the current website share URL. The presentation join strip and overlay show the same readable URL beside the code. No code, token, participant cookie, Event UUID, or remote URL is encoded.
- SVG should follow the surrounding theme with a viewBox/current-color treatment and no fixed width; PNG size is bounded (reference default 512, maximum 2048). QR responses are public/cacheable only when the encoded target itself is safe; participant HTML/API and management QR previews remain private/no-store as appropriate.
- A QR or share response never makes a draft Q&A public. It can be generated privately for setup, and the URL remains stable across a title change through the Event alias/canonical redirect rules.

### Lifecycle, privacy, cache, and audit

- Public Q&A HTML, JSON, co-host gate, presentation, and all participant-relative responses are `private, no-store, noindex`, because they can set cookies or differ by viewer. They are never in the anonymous public edge class. QR image bytes may use a separately classified public cache because they carry no viewer state.
- Q&A text and optional names are the only participant-submitted content. No email, account, attendee list, analytics identity, or third-party script is introduced. Questions are not copied into the Event public detail, feeds, search, sitemap, JSON-LD, OG metadata, logs, metrics, traces, or error reports.
- Application logs exclude question text, names, participant IDs/cookies, passcodes, invite names where sensitive, tokens, raw IP, complete query, request bodies, and response bodies. Safe operational metrics contain only bounded route/operation/status, event/session opaque IDs where permitted, counts, latency, rate-limit class, and durable-job state.
- Q&A deletion/anonymization, archive cleanup, restored-backup tombstones, and retention must join the website privacy workflow. The reference seven-day archive undo window and explicit retention behavior are preserved as compatibility requirements, but the final retention class requires the website privacy owner’s approval; no implementation may use an unreviewed indefinite default.
- Session creation/provisioning, settings/lifecycle changes, co-host create/revoke, moderation, bulk actions, retries/repair, and denied management/high-risk operations use append-only redacted `AuditEvent` records. Record actor/role, action, outcome, Event/session/question opaque IDs, revision, request/correlation/idempotency IDs, and bounded reason/status data. Never record question text, participant identity, cookie, passcode, invite token, management link, or raw provider response. Anonymous question/vote activity is not individually audited; only safe aggregate operational counters may exist.

### Accessibility

Adapt the reference UI into the website design system while meeting the website WCAG 2.2 AA contract: semantic landmarks and headings, skip/focus behavior, keyboard-complete controls, visible focus and adequate target sizes, 200% zoom/reflow, contrast in both themes, reduced motion, explicit labels/autocomplete, preserved input and linked field errors, and `aria-live` announcements for submit/rate/error/optimistic rollback states. The readable URL and QR have a text alternative. Presentation actions remain visible buttons even though arrow/Escape shortcuts are also retained. No state is conveyed by color alone, and no inaccessible challenge is added.

## DataQnA provenance inventory

### Snapshot and authority

The issue names the reference snapshot. At capture on 2026-08-20, `/home/alexey/git/dataqna` was on clean `main` at:

```text
7704f99fcf48334d9837815f391f834b42f77033
Tint the pinned card in presentation mode
2026-08-17T23:45:26+02:00
```

Every `path @ revision` below was inspected from that commit. A short revision is the unique path-tip abbreviation at the pinned snapshot; the full HEAD above is the reproducible source baseline. The reference documents are draft contract material; the current source and tests win where they differ.

The most important documented mismatches are in `docs/api.md`: its older example includes `anonymous` and `editable_until`, it still describes admin-grant routes that current `src/dataqna/api.py` deliberately does not expose, and its pagination examples use `limit`, `cursor`, and `next_cursor`, none of which the pinned `src/dataqna/api.py` parses for `GET questions`. Current `questions.serialize`, route tests, and co-host tests emit/use `own`, `voted`, and `editable`, and keep account grants outside the co-host path. The website contract above follows the current code/tests and records website-specific security extensions explicitly.

### Documents to keep beside the port

- `docs/specification.md @ 7704f99` — v0.1 draft; sections 2–7 define roles, co-host scope, lifecycle, settings, question/vote rules, public page, polling, admin console, and presentation; sections 11–15 define storage/auth/rate/privacy behavior; section 17 lists later non-goals.
- `docs/api.md @ dc406d9` — v0.1 draft; JSON shapes, status/error conventions, question/vote/co-host/QR endpoints, conditional ETag reads, and idempotent room creation. Reconcile its stale examples against the source/tests as noted above.

### Domain, transport, and security source files

These are the exact files to copy/adapt or use as a behavior reference; the revision is the file’s last-touch commit at the pinned snapshot.

- `src/dataqna/config.py @ dc406d9` — product constants and reference cookie/session names/TTLs. Adapt `MAX_QUESTION_LENGTH=315`, `MAX_NAME_LENGTH=60`, and archive/participant timing to website configuration; do not copy deployment secrets or DataQnA auth settings.
- `src/dataqna/ids.py @ 0774cdd` — ULID/readable-code/slug and ambiguous-character rules. Adapt only Q&A opaque IDs and co-host names; Event public IDs/slugs remain website-owned.
- `src/dataqna/http.py @ db1b0d2` — safe JSON/HTML/error/redirect/header/cookie transport helpers. Use for response semantics only; replace API Gateway event parsing with Django request/response and website security middleware.
- `src/dataqna/rooms.py @ dc406d9` — `STATES`, `TRANSITIONS`, settings validation, expiry, archive seven-day clock, retention, canonical slug behavior, public/admin views, `accepting_questions`, `accepting_votes`, and live-state rules. Adapt the room into the one-to-one Event Q&A session; do not copy a second Event identity or standalone directory.
- `src/dataqna/questions.py @ 377db0d` — submission, 315-character validation, implicit vote, author edit window, statuses, singular pin, counters, ranking, serialization, ETag digest, and collection filtering. This is the primary domain-behavior port.
- `src/dataqna/api.py @ 865a00b` — `Identity`, per-request authorization re-checks, public question/vote routes, moderator/co-host scope, bulk actions, closed/archived status handling, co-host redemption, and idempotent room-create pattern. Adapt to website services and route adapters; do not copy its Lambda dispatcher or DataQnA API-key/admin-grant system.
- `src/dataqna/security.py @ 0774cdd` — HMAC signing/verification, anonymous participant identity, co-host code/token generation, normalization, expiry, and constant-time comparison. Adapt to website secret/cookie/CSRF/session conventions; never copy plaintext credential handling into logs or public projections.
- `src/dataqna/store.py @ 865a00b` — DynamoDB atomicity reference for unique pointers, votes, pins, co-host name claims, rate counters, idempotency records, and immediate revocation. Adapt invariants to Django constraints/transactions/portable ORM; do not copy the DynamoDB partition/key schema.
- `src/dataqna/qr.py @ db1b0d2` — SVG/PNG QR generation, bounded sizing, theme-safe SVG treatment, and public share-target behavior. Adapt to the website asset/response pipeline and approved dependency set.
- `src/dataqna/render.py @ c7ab3d7` — escaped server rendering, JSON page configuration, private HTML responses, versioned assets, notice/co-host form, and asset traversal protection. Adapt to Django templates/static assets, CSP, cache registry, and website design system.
- `src/public_handler.py @ 377db0d` — participant page, host-link disclosure, canonical old-slug redirect, participant cookie issuance, QR, co-host gate, and safe public error routing. Adapt to website URL/view layers; do not copy the Lambda handler or standalone home/`/live` directory.
- `src/admin_handler.py @ 95dbfe3` — presentation authorization for an admin or co-host and separate host surface. Adapt to Studio/website host views; do not copy its OIDC callback/login or Lambda split.

`src/dataqna/oidc.py @ ea346ac` is explicitly not a port: website staff auth, MFA, session revocation, and capabilities belong to `accounts`/Studio. `template.yaml`, SAM/IAM/deployment files, DataQnA `boto3`/DynamoDB wiring, and its separate domain are also outside the website boundary.

### Frontend assets to adapt

- `src/web/room.html @ c7ab3d7` and `src/web/room.js @ 377db0d` — participant composer, optional/required names, 315-character ring, tabs, question rendering, own-question state and withdraw control, vote toggles, banners, host-link disclosure, optimistic updates, 4s/30s polling, ETag handling, and safe empty/error states. The API supports the reference author text-edit operation, but pinned `room.js` does not render an edit control; no participant edit UI is assumed here.
- `src/web/admin.html @ dc406d9` and `src/web/admin.js @ 865a00b` — room/session list/detail, share panel, QR links, lifecycle/settings panels, co-host management, moderation queue, ETag refresh, and console error/busy behavior. Adapt to Event/Studio routes and capability fields.
- `src/web/present.html @ c440111` and `src/web/present.js @ 7704f99` — projection layout, ranked queue, spotlight/manual navigation, QR overlay, pin/answer/undo, optimistic rollback, 1-second ETag polling, keyboard controls, fullscreen, and theme behavior.
- `src/web/qna.js @ 0774cdd` — shared optimistic PATCH/rollback helper used by admin and presentation surfaces; retain the single frontend mutation helper rather than duplicating action logic.
- `src/web/app.css @ 7704f99` — responsive cards, themes, focus states, motion, QR/presentation layout, and the no-line-clamp rule. Rebase on website design tokens; do not import an isolated visual system without review.
- `src/web/theme.js @ c7ab3d7` — shared light-default/dark-opt-in theme toggle and pre-paint behavior. Adapt to the website’s existing accessibility/theme shell.

### Tests to copy/adapt

- `tests/conftest.py @ f5e8b36` — isolated fake-table/environment fixture pattern. Replace Moto/DynamoDB with project-local isolated Django/SQLite factories and synthetic identities; no reference secrets or production data.
- `tests/test_rooms.py @ dc406d9` — session creation/identity, explicit/generated slug behavior, expiry, transition guards, archive/reopen timer, settings validation, and retention. Port to EventQnaSession and add Event visibility gates, one-to-one uniqueness, and provisioning/backfill tests.
- `tests/test_questions.py @ 377db0d` — implicit vote, duplicate/withdraw vote, closed submission, 315 limit, immediate visibility, ranking/ties/pin, answer/delete counters, no hidden status, ETag changes, and author edit rules. Port as the native service regression suite.
- `tests/test_api.py @ 865a00b` — public participant versus moderator authorization, no account-grant escape hatch, pin/answer behavior, room-scoped credentials, idempotent create, root/admin access, and archived read/reopen behavior. Adapt to website capabilities, Event object scope, admin API, CSRF, `If-Match`, and provisioning; do not port DataQnA API-key tests as a new website credential model.
- `tests/test_cohosts.py @ 51c9e40` — split link/passcode, normalization, indistinguishable failures, rate limiting, room scope, settings/lifecycle/moderation/presentation powers, no escalation, immediate revocation, token binding/forgery, and form handoff. Port every applicable case with website cookie flags, CSRF, redaction, and Event boundary assertions.
- `tests/test_qr.py @ db1b0d2` — SVG theme/current-color/viewBox and PNG signature/size bounds. Adapt to website QR route/cache policy and canonical Event share URL.
- `tests/test_render.py @ b0c7f3b` — escaped titles, page config, asset versioning/traversal, no line clamp, safe notices, co-host form shape, and private/error rendering. Adapt to Django templates and route/cache/security headers.
- `tests/test_routing.py @ cb20f47` — every rendered POST form has a reachable POST route. Replace the SAM route parser with Django URL resolver/route registry checks, including the co-host POST path and CSRF-bearing forms.
- `tests/test_theme.py @ 77e978c` — light/dark contrast, hero/QR contrast, focus/theme script availability, canvas-before-CSS, and responsive visual assumptions. Adapt to the website design system and add automated accessibility checks.
- `tests/test_directory.py @ ff599bc` — selectively port the host-link disclosure/ordinary-participant non-disclosure cases. Do not port the standalone DataQnA directory, `/live`, or unlisted-room product.

`tests/test_oidc.py @ ea346ac` and `tests/test_packaging.py @ b4acfc0` are reference-only and are not ported: website auth/deployment verification already has its own owners. The later website suite must add Event provisioning, service parity, audit/redaction, cache classification, CSRF, browser, privacy-retention, and durable-job tests that DataQnA could not provide.

## Smallest subsequent implementation and test slices

1. **Event relation and provisioning.** Add the Event-owned one-to-one session relation/migration, the `events.qna.provision` durable handler, transaction/on-commit dispatch, exact dedupe/replay semantics, and the existing-Event backfill command. Test rollback atomicity, concurrent duplicate creation, wake-up failure, lease/fence retry, native ensure no-op, safe blocked state, and Event public-state gating.
2. **Native Q&A service/backend contract.** Port `rooms.py`, `questions.py`, the relevant `security.py` and `store.py` invariants into portable Django services/models. Freeze the `qna.v1` DTO/error/ETag contract. Port `test_rooms.py`, `test_questions.py`, and the applicable `test_api.py`/co-host cases; add transaction, uniqueness, rate-limit, revision, redaction, and audit assertions.
3. **Website participant/co-host/QR routes.** Add the public Event-subpath views, relative JSON API, participant/co-host cookies, CSRF, safe errors, canonical redirects, private cache class, QR SVG/PNG, and co-host gate. Port `test_qr.py`, route/render cases, and co-host security cases; add cookie/CSP/cache/CSRF/unknown-object tests.
4. **Studio/admin/presentation surfaces.** Register Event-Q&A capabilities, Studio/admin API parity, permissions, `If-Match`/`Idempotency-Key`, audit actions, moderation/bulk results, host links, and the presenter route. Adapt `admin.html/js`, `present.html/js`, and `qna.js`; add positive/negative capability tests, API/OpenAPI parity, stale/replay/conflict tests, co-host scope tests, and audit canaries.
5. **Browser/accessibility/operations verification.** Adapt the remaining HTML/CSS/theme assets, run focused Django plus desktop/mobile browser flows, inspect QR/share/presentation/error states, verify 4s/30s/1s ETag polling and optimistic rollback, and complete WCAG keyboard/screen-reader/zoom/reduced-motion checks. Add retention/deletion/backfill/restart evidence and deployment/runbook coverage; screenshots and scratch artifacts stay under `.tmp/`.

## Explicit non-goals

- No polls, quizzes, word clouds, gamification, categories/tags, threads, written answers, AI clustering, Slack notifications, or unrelated Event registration/email changes.
- No second Event identity, public UUID lookup, Q&A slug identity, standalone Q&A directory, `/live` product, or public DataQnA host/subdomain. Existing Event numeric/public-slug/alias rules remain authoritative.
- No separate Q&A deployment, DynamoDB table, SAM stack, Cognito client, DataQnA admin OIDC flow, DataQnA API-key system, or browser-side remote-service adapter in the initial implementation.
- No WebSocket/SSE infrastructure in this issue; SSE is only a presenter-only future extension point behind the unchanged contract.
- No account requirement, email collection, attendee directory, public participant identity, tracking, third-party frontend script, or question text in Event/SEO/public projections.
- No pre-publication review queue, pause toggles, downvotes, automatic presentation advance, or recoverable “hidden” question status. The tested DataQnA moderation/lifecycle model remains the baseline.
- No automatic opening of provisioned sessions, import of standalone DataQnA rooms/questions, Event ownership/admin redesign, or implementation work in this architecture slice.
