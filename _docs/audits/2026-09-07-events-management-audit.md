# Events/Q&A and management-boundary audit — 2026-09-07

This addendum covers the server-side Q&A boundary and its Studio/admin-API adapters, including management-operation policies. It supplements the [main audit](2026-09-07-repository-audit.md), [backend audit](2026-09-07-backend-security-audit.md), and [UX audit](2026-09-07-ux-audit.md). It does not repeat the Q&A JavaScript findings or the legacy administration/token findings.

Nine bounded work packages are identified: **1 P0, 7 P1, 1 P2**. The most consequential confirmed defect is that an authenticated staff member whose Studio session has been revoked can still moderate through the public Q&A API, using a valid CSRF token. This path also omits the moderation audit record. Other packages address divergent mutation contracts, cross-principal idempotency collisions, recoverable deletion, validation, quota scope, retention readiness, safe concurrency failures, and the public configuration schema.

This is an audit, not implementation or release acceptance. Application files were not changed. Findings describe the inspected working tree, including existing unrelated work in progress. Proposed fixes must enter the repository's grooming, independent testing, and PM-acceptance lifecycle before implementation/commit. No real registration data, production database, live credentials, external endpoints, or external mutation was used.

## Authority, coverage, and evidence

Read authorities: `AGENTS.md`, `_docs/PROCESS.md`, `_docs/specs/06-studio-and-admin-api.md`, and `_docs/architecture/event-qna-integration.md`. The Q&A architecture explicitly distinguishes accepted website adaptations from unresolved reference-product choices. In particular, broader co-host settings/lifecycle authority and the final privacy retention class remain owner decisions; this audit does not silently choose either.

Named coverage:

| Boundary | Files and behavior reviewed | Evidence level |
| --- | --- | --- |
| Public Q&A and hosts | `events/qna/views.py`, `security.py`, `services.py`; event/session visibility, participant/host identity, CSRF, collection/query rules, mutation validation, quotas, private serialization | Source review plus focused routed/service probes |
| Persistence | Q&A models in `events/models.py`; question/vote/session counters, revision compare-and-swap, invite revocation, retention/rate buckets | Source review; isolated synthetic database tests |
| Studio | `events/qna/studio_views.py`, Q&A template, `studio/auth.py`, `accounts/studio_authorization.py`, `accounts/studio_sessions.py` | Source review; revoked-session and repeated/stale form probes |
| Admin API | Q&A handlers in `management_api/views.py`, `authentication.py`, `dispatch.py`, `parity.py`, `events/qna/capabilities.py` | Source review; two authorized principals exercising the actual moderation API |
| Idempotency/secret results | `core/idempotency.py`, `management_auth/idempotency.py`, Q&A co-host creation adapter | Source review; cross-principal collision and existing one-time-response tests |
| Long-running management operations | `management_api/operations.py`, `core/operations.py`, API operation/bulk tests | Source review and existing focused tests; no new confirmed cancellation defect |

Executed commands, from repository root:

```bash
DTC_TEST_RUN_ID=audit-events-management-20260907 PUBLIC_MEDIA_STORE_BACKEND=memory uv run --frozen pytest .tmp/audits/test_events_management_probe.py events/tests/test_qna.py management_api/tests/test_operations.py management_api/tests/test_bulk.py -q
```

Result: **22 passed, 4 subtests passed in 29.86 seconds**. This used the initial nine-test diagnostic probe together with the existing focused suites.

```bash
DTC_TEST_RUN_ID=audit-events-management-20260907b PUBLIC_MEDIA_STORE_BACKEND=memory uv run --frozen pytest .tmp/audits/test_events_management_probe.py -q
```

Result: **12 passed in 27.43 seconds**. The final probe strengthened the revoked-session request to `Client(enforce_csrf_checks=True)` with a real CSRF token and added positive ownership/co-host controls plus a fault-injected revision-conflict test.

The diagnostic source is `.tmp/audits/test_events_management_probe.py`, an intentionally gitignored, synthetic-only audit artifact. Several diagnostic tests assert the *current incorrect behavior* to prove it exists; a passing diagnostic is not a passing acceptance test. Their permanent regression-test equivalents must reverse the relevant assertions after a fix. Test isolation used the repository's test settings and synthetic fixture creation. No full suite, live deployment, distributed load test, or worker/backup retention run was performed. Screenshots are not applicable to this server-only continuation; rendered UX evidence is separately documented in the UX report.

## Finding index

| ID | Priority | Finding | Confidence |
| --- | --- | --- | --- |
| EVT-01 | P0 | Public host authorization bypasses revoked Studio sessions and omits privileged moderation audit | High; real routed valid-CSRF probe |
| EVT-02 | P1 | Studio Q&A mutations ignore declared revision/idempotency/confirmation contracts; omitted settings are overwritten | High; routed/service probe and adapter comparison |
| EVT-03 | P1 | Q&A API idempotency keys collide across authorized principals | High; two-principal API probe |
| EVT-04 | P1 | Deleted questions remain in moderator collections and can be restored | High; service probe |
| EVT-05 | P1 | Inconsistent settings/types can make a room unusable or produce an unhandled request error | High; service probes and adapter exception path |
| EVT-06 | P1 | Participant quota keys include IP; co-host quota is per event instead of global IP | High for scope mismatch; general-traffic guard is a coverage gap |
| EVT-07 | P1 | Null retention is accepted; archive/rate-bucket cleanup contract is not connected | High for code behavior; production cleanup not tested |
| EVT-08 | P1 | Public mutation adapters do not translate session revision conflicts into safe errors | High for exception/rollback path; race frequency unmeasured |
| EVT-09 | P2 | Public configuration reuses management serialization and includes internal UUIDs | High; rendered response probe; no authorization bypass claimed |

## EVT-01 — Revoked Studio sessions still authorize Q&A host actions

**Priority:** P0. **Confidence:** high. **Classification:** confirmed alternate-adapter authorization and audit defect. Related common boundary: **BE-01** in the backend report; this card owns the Q&A implementation and tests, not a second independent account-system redesign.

Evidence:

- `events/qna/views.py:63` authorizes a moderator using authenticated/active/staff flags and `user.has_perm("events.manage_event_qna")`. It does not call the Studio authorizer or inspect the current `StaffSession`.
- `accounts/studio_authorization.py:64` provides the canonical authorization path, including a refreshed account, explicit permissions, and staff-session policy. `accounts/studio_sessions.py:35` checks revocation and idle/absolute session validity. `studio/auth.py:143` uses that path for Studio adapters.
- `events/qna/views.py:104` lets the moderator branch inspect a session before ordinary public event/session visibility restrictions; `views.py:323` uses the same moderator helper for host/presentation pages. Such privileged inspection is intended only after correct authorization.
- `events/qna/views.py:245` calls `update_question(..., moderator=True)` without an audit context. `events/qna/services.py:670` records moderation only when an audit context is supplied. The architecture requires website staff authentication at line 134 and redacted moderation audit at line 190.

Reproduction and observed result:

1. Create a synthetic public event/open Q&A and a synthetic staff account with the explicit Q&A permission.
2. Establish the website staff-session reference, then revoke its `StaffSession` while retaining the ordinary authenticated Django session.
3. Use the same client with CSRF enforcement enabled; GET the Q&A page to obtain a genuine CSRF token.
4. PATCH a synthetic question through `/events/<public-id>/<slug>/qna/api/questions/<question-id>/` with that token.
5. Observe successful moderation despite the revoked staff session, and no added moderation `AuditEvent`. The canonical Studio authorization boundary rejects the revoked session.

This is not a CSRF bypass. A legitimate browser session whose elevated staff authority has been revoked can continue exercising it through a different adapter. The raw `has_perm` shortcut also differs from the canonical explicit-permission treatment of superusers. No bearer-token bypass or cross-event co-host escalation was demonstrated.

Small-model implementation steps:

1. Replace the staff portion of `_moderator` with a small shared resolver returning an authorized actor context, not just a boolean. Use the canonical staff-session/capability authorizer for the requested Q&A action and target event/session.
2. Keep the signed, database-checked co-host branch independent. A co-host does not acquire a website staff session and must remain scoped to the redeemed session. Do not grant co-host settings or lifecycle authority while that choice is unresolved.
3. Pass the resolved safe actor/audit context into every privileged public-browser moderation call. Reuse existing audit primitives; identify staff by user ID and co-hosts by a safe opaque grant reference, never passcode/name/cookie/question text.
4. Make host, presentation, participant configuration capability flags, and PATCH actions consume the same current decision. Check authorization on each request, including after revocation.
5. Add required denied-management audit handling through the shared policy boundary; do not indiscriminately audit anonymous participation or record request bodies.

Acceptance tests:

- Missing, revoked, idle-expired, and absolute-expired staff sessions fail host/presentation access and moderation despite a still-authenticated Django cookie.
- Removing explicit Q&A permission is effective on the next request; superuser behavior matches the documented canonical policy.
- Valid staff authorization and valid room-scoped co-host moderation still succeed with CSRF; missing/incorrect CSRF still fails.
- A co-host cookie cannot authorize another session, and invite revocation takes effect immediately.
- Successful moderation writes exactly one safe audit event. Denials have safe policy-required records; none contains authored text, names, cookies, or credentials.

Dependencies: reuse the canonical authorization boundary and coordinate BE-01 tests/helper changes. No new identity system is needed. Keep UX host capability rendering aligned with the returned decision.

## EVT-02 — Q&A management adapters have different mutation guarantees

**Priority:** P1. **Confidence:** high. **Classification:** confirmed lost-update/retry/confirmation and settings-preservation defects, grouped as one adapter-contract work package.

Evidence:

- `events/qna/capabilities.py:118` declares required idempotency and `If-Match` concurrency for session management. The architecture requires management revision/idempotency parity at lines 29, 43, and 86.
- `management_api/views.py:943` requires an idempotency key and `If-Match`, runs an idempotent command, and passes `expected_revision` to `update_session`.
- `events/qna/studio_views.py:57` directly calls the same low-level mutation without reading either field. `templates/studio/event_qna.html:31` includes an idempotency field but no revision field; the generated key is not used by the handler.
- `studio_views.py:60` always submits `listed=False` when the field is absent and `answered_placement="separate"` when absent. The form at `templates/studio/event_qna.html:31` does not expose those settings, so an ordinary save can silently reset values previously set by another adapter.
- Studio moderation, retry, co-host creation and revocation similarly call low-level services directly at `studio_views.py:86`, `117`, `128`, and `147`. The API retry/revoke adapters require exact `confirmed=True` at `management_api/views.py:1025` and `1102`; their Studio counterparts do not validate an equivalent confirmation contract.
- Even API question moderation at `management_api/views.py:984` has no `If-Match` check; its capability at `events/qna/capabilities.py:134` does not declare the revision policy required by the Q&A architecture.

Reproduction and impact:

1. Open a synthetic Studio Q&A form, mutate its session through another actor, then submit the older form with a stale revision/idempotency field.
2. The Studio adapter accepts it. Submitting the same command again executes again instead of returning a replay. The probe observed revision advancement despite an explicitly stale supplied revision.
3. Independently, set `listed=True` or a non-default answered placement via the service/API and save the ordinary Studio form. Source inspection shows that omitted controls are converted into default writes.

Two legitimate operators can overwrite each other's settings; browser retries can repeat privileged work/audit; the visible UI does not describe all settings it changes. Calling the same low-level function does not establish adapter parity.

Small-model implementation steps:

1. Define a shared Q&A management-command layer that accepts an authorized actor, command data, expected revision where required, idempotency key, and explicit confirmation for the relevant operation. Keep HTTP/form parsing in adapters.
2. For session settings, carry the currently rendered session revision in the Studio form, verify it transactionally in the shared command, and return a recoverable stale-form error preserving user input. Do not silently reload-and-overwrite.
3. Make settings updates sparse: only map fields actually owned by the form. For checkboxes, use an explicit form schema so unchecked rendered fields mean false while unrendered fields remain unchanged. Either render `listed`/answered placement intentionally or stop writing them from this form.
4. Consume Studio idempotency keys, using the actor-scoped implementation from EVT-03. Replays must not produce another mutation/audit/one-time secret response.
5. Add the missing expected-revision contract to management question moderation and its capability/schema. Decide a documented resource revision representation; session revision is already available but can contend with audience activity, so do not invent a second version policy implicitly.
6. Enforce retry/revocation confirmation in the shared command, not just a template dialog. Use the safe one-time result mechanism for co-host creation.
7. Replace metadata-only parity confidence with paired behavior tests for these operations. `management_api/dispatch.py:144` attaches service/audit metadata from the registry, and `management_api/parity.py:62` compares metadata; this cannot prove a Q&A handler actually enforced revision/idempotency or called the registered service. Do not count this testing weakness as an additional unrelated finding.

Acceptance tests:

- Equivalent authorized Studio/API mutations have the same final state, safe audit schema, and denial behavior.
- Stale or missing required revision causes no write. Repeating the same actor/key/payload returns replay semantics without incrementing revision; a changed payload conflicts.
- `listed` and answered placement set through the API survive an unrelated Studio save.
- Missing or false confirmation rejects the designated retry/revoke command on both adapters.
- Co-host creation retries do not create extra invites or reveal an already-consumed passcode.

Dependencies: EVT-03 supplies actor-safe idempotency; coordinate EVT-01 actor context and EVT-08 safe conflict handling. UX-12's pin/unpin presentation fix is separate; do not omit server guarantees when fixing its label.

## EVT-03 — Q&A API idempotency namespaces are shared across principals

**Priority:** P1. **Confidence:** high. **Classification:** confirmed isolation/retry defect between already-authorized API principals, not an unauthenticated data-access claim.

Evidence:

- `management_api/views.py:956` and `995` call `core.execute_idempotent` with `scope=capability.key`; their request hashes include target/payload but not principal identity. Retry and revoke repeat that pattern at lines 1031 and 1108.
- `core/idempotency.py:123` derives the stored key from scope/key. `execute_idempotent` at line 148 does not implicitly augment the namespace with request identity.
- `management_api/authentication.py:113` does authenticate/check the token scope and principal permission before handlers run. Both principals in the probe had legitimate moderation authority; this upstream control does not give their idempotency records separate namespaces.
- `_qna_audit_context` at `management_api/views.py:184` includes actor-scoped information, but that audit identity does not change the actual idempotency lookup.
- Co-host creation already uses `execute_one_time_idempotent(principal=...)` at `management_api/views.py:1084`, providing a useful existing actor-fenced pattern.

Reproduction and observed result:

1. Create two synthetic independently authorized Q&A API principals, A and B.
2. A moderates a synthetic question using a client-local idempotency key.
3. B sends the same command/key. B receives `replayed=True` for A's previous operation rather than an independent command result.
4. B changes the command while retaining its own same key and receives a conflict against A's stored request.

Different tools commonly generate client-local sequence keys. Here one client's key poisons another's retry space and suppresses the latter's execution/audit. The demonstrated same-object response was already readable to both actors; broader confidential-response disclosure was not tested or claimed.

Small-model implementation steps:

1. Introduce a common actor-scoped management idempotency helper, or pass a stable principal-specific scope into the existing primitive. Include operation/capability and authenticated principal identity; never use a raw bearer token.
2. Keep target identity, normalized payload, and required expected revision in the request fingerprint. Do not substitute request/correlation IDs for durable client retry identity.
3. Authenticate and re-check current capability/object permission before looking up or returning a replay. Revoked credentials must not remain useful merely because a result exists.
4. Align audit idempotency references with the actual stored key namespace. Apply the helper to session management, moderation, provisioning retry, and co-host revoke, and to the Studio command layer from EVT-02.
5. Plan compatibility with existing records explicitly. Do not blindly replay an old global record to a new principal, and do not blindly re-execute a potentially completed destructive/external command during key migration.

Acceptance tests: A/B can use the same key independently; A's exact retry replays; A's changed payload conflicts; B never receives A's replay metadata; stale/revoked permission fails even on a known key; exactly one audit exists per actual actor command; passcodes never enter generic stored results.

Dependencies: shared helper/schema ownership in `core`/`management_auth`; coordinate EVT-02. Existing one-time-secret behavior must remain intact.

## EVT-04 — Deleted questions are listed and recoverable

**Priority:** P1. **Confidence:** high. **Classification:** confirmed lifecycle/data-removal contract violation.

The authority is explicit: `_docs/architecture/event-qna-integration.md:84` says deleted is terminal and excluded from public **and moderator** collections; line 150 rejects a recoverable hide operation.

Evidence:

- `events/qna/services.py:811` defaults a moderator collection to every `QUESTION_STATUSES` value, including deleted. The query at line 817 returns those rows. Administrative session serialization uses the same moderator collection.
- `_set_status_locked` at `services.py:582` validates only the target and treats any distinct transition as allowed. Lines 603–604 explicitly increase `q_total` when transitioning *from* deleted.
- `update_question` at `services.py:646` retrieves deleted questions for moderators and can update their text/status. The pin path correctly rejects deleted rows at line 618, but that protection does not make status deletion terminal.

Reproduction: submit a synthetic question, delete it, list questions as moderator, and then set its status to visible. The diagnostic observed the deleted item in the collection and successful restoration.

Impact: a user's withdrawn/deleted content remains in an ordinary moderation listing and can be republished, contrary to the stated product contract. Physical retention is a separate policy; terminal deletion does not necessarily require immediate hard deletion of every database row.

Small-model implementation steps:

1. Exclude deleted questions from all ordinary collection serializers, including moderator/admin collections and explicit status filters. Choose the contract's safe empty/error treatment for a requested deleted filter; do not add a new recovery UI.
2. Guard the current terminal state at the shared update boundary before text/status/pin writes. Allow only the specified visible/answered transitions plus terminal delete.
3. Define a safe repeat-delete result without permitting restore. Keep the entire question/counter/pin update atomic.
4. Remove unreachable restoration counter logic after tests prove the terminal state invariant. Preserve any policy-approved tombstone needed for later retention/backup workflows.

Acceptance tests: author withdrawal and moderator deletion disappear from all ordinary collections; another actor cannot restore/edit/pin the deleted row; answering/undoing an answer still works for non-deleted content; repeat deletion has documented safe behavior; total/answered counters and singular pin remain correct.

Dependencies: align permanent deletion/anonymization with EVT-07, but do not block the terminal-state guard on designing a full retention worker. EVT-02 supplies management revision/idempotency safety around the command.

## EVT-05 — Invalid input can change meaning or make every question invalid

**Priority:** P1. **Confidence:** high. **Classification:** confirmed shared-service validation defects; one exception path proven at the service boundary and traced to the public adapter.

Evidence and reproductions:

- `_clean_settings` at `events/qna/services.py:399` validates the individual booleans but accepts `allow_names=False` together with `require_names=True`. `_validate_name` at line 523 rejects nonempty names in that state and line 525 rejects empty names. The probe set this configuration successfully and confirmed that both possible name choices fail: an open room cannot accept any question.
- `update_question` at line 655 checks `payload.get("status")` in a set before validating its type. An author's own editable question with `{"status": []}` raises `TypeError` rather than a `QnaError`. The public PATCH adapter at `events/qna/views.py:257` catches only `QnaError`, so this malformed request escapes its safe error envelope. The equivalent moderator enum-membership path also needs typed validation.
- `update_question` at `services.py:668` uses `bool(payload["pinned"])`; `{"pinned": "false"}` pins the question. JSON strings are not JSON booleans. The probe confirmed the unexpected true value.

Small-model implementation steps:

1. Validate the incoming command into a typed internal shape before mutation. Check `status` is an allowed string before any set membership; define whether explicit null means omitted or invalid and test that choice consistently.
2. Require an actual boolean for `pinned`, rather than coercing strings/numbers/containers. Keep this in the shared service/command validation so Studio and API cannot diverge.
3. Reject the contradictory name-settings combination with a field-level error, or normalize it only if an explicitly accepted product rule requires normalization. A silent choice is not appropriate.
4. Convert known validation failures into the established bounded `QnaError(400, ...)` shape. Do not catch arbitrary exceptions and disguise genuine server faults as user input.
5. Validate the complete payload before updating text/status/pin, and retain transactional rollback as defense in depth. Do not include raw bodies, question text, or names in error logs.

Acceptance tests: valid allow/require-name combinations accept/reject expected names; the contradictory combination changes no state; status values covering strings, null, lists, objects, integers and booleans never produce 500; pinned accepts only true/false; malformed multi-field updates leave text/counters/revision unchanged; adapters return safe field errors without losing form input.

Dependencies: EVT-02's shared command layer is the preferred owner of adapter-normalized input; validation helpers can be fixed independently without waiting for the whole parity refactor.

## EVT-06 — Rate-limit identities do not match the declared quotas

**Priority:** P1. **Confidence:** high for the key-scoping mismatch; source-only for missing general-traffic coverage. **Classification:** contract mismatch, not a claim that anonymous participants are permanently identifiable.

The exact reviewed limits are `_docs/architecture/event-qna-integration.md:163`: questions and votes are **per participant/session**, whereas question-abuse and co-host-redemption limits are **per source IP across sessions**. A policy may be deliberately changed, but line 159 requires explicit review first.

Evidence:

- `events/qna/views.py:176` appends `REMOTE_ADDR` to every scope passed to `_rate`.
- Question scopes at lines 214–215 and vote scope at line 269 already include participant/session, making the actual key participant/session/**IP**. The same signed participant cookie gets a fresh participant quota after an IP change.
- Co-host scope at line 296 includes the event ID, making the actual limit per event/IP rather than the documented global IP redemption budget.
- The question-IP guard at line 216 correctly uses an IP-only global class. This is a positive control, not a missing protection.
- Public GET/page paths at `views.py:140` and `185` have no general-traffic admission call. No application-level 2,000/IP/5-minute enforcement was found in the reviewed Q&A paths; deployed WAF/edge configuration was not inspected here.

Reproduction: call the participant question limiter twice from one IP using a fixed synthetic participant/session; the second admission is refused. Change only the request IP and the same participant/session is admitted. This probe demonstrates the promised participant quota resetting; it does not claim that a fresh anonymous cookie cannot create a new participant, which the product intentionally permits.

Small-model implementation steps:

1. Separate participant/session quota helpers from source-IP quota helpers. Pass fully specified identities into the bucket service; do not automatically append IP to every identity.
2. Preserve the existing participant question/vote thresholds with participant/session-only keys. Keep the separate global question-IP budget as defense in depth.
3. Remove event identity from the co-host source-IP redemption budget. An optional additional per-event budget may exist, but must not replace the required global one.
4. Identify the approved trusted-proxy/source-IP resolver. Do not fix this by blindly trusting a client-supplied forwarding header. This audit makes no assertion about the deployed proxy topology.
5. Establish where the general-public traffic budget is enforced. Implement the missing application/edge rule or obtain an explicit reviewed alternative and test its effective scope; a generic infrastructure assumption is not evidence.
6. Preserve salted key hashing, atomic counters, `429`/`Retry-After`, and no downstream content mutation after refusal. Connect bucket expiration to EVT-07.

Acceptance tests: one participant across two IPs shares its participant budget; two participants behind one IP have separate participant budgets but share the global IP budget; redemption attempts across two events consume one global IP budget; rejected attempts never create/update questions; forwarded-header spoofing does not select arbitrary budget identities; no raw IP/cookie is logged.

Dependencies: operations/security review only for effective source-IP resolution and any intended policy change. The incorrect key composition can otherwise be repaired locally.

## EVT-07 — Retention is configurable beyond the approved contract, with cleanup unconnected

**Priority:** P1. **Confidence:** high for accepted null and the inspected call graph; no production deletion/cleanup result is claimed. **Classification:** confirmed unsafe configuration acceptance plus a **known incomplete privacy/operational contract**, requiring an owner decision before destructive cleanup.

Authority: `_docs/architecture/event-qna-integration.md:126` preserves the reference seven-day archive clock/default 365-day retention, requires an approved website retention class, and forbids accidentally enabling null/indefinite retention. Lines 169 and 189 require rate-window retention and integration with deletion/anonymization/backup tombstones.

Evidence:

- `events/qna/services.py:479` accepts `retention_days=None`, and the model permits null. The synthetic settings probe persisted it without a reviewed-policy gate.
- `_transition_locked` at `services.py:368` sets `archive_delete_at` seven days after archive. Repository Python-reference inspection found model/migration/writes but no cleanup consumer of that deadline.
- `EventQnaRateLimit` buckets are persisted by `services.py:1020`; no expiry field or cleanup consumer was found in the reviewed Q&A implementation. Hashing an IP-derived identity is not equivalent to deleting its bucket after the intended window.

The absence of a worker connection is not evidence that a production worker ran and failed, nor authorization to purge existing data. The architecture already acknowledges that final retention policy needs privacy-owner acceptance. Treat this as a release-readiness/cutover work package with an explicit decision gate, rather than inventing a retention value or creating a surprise deletion migration.

Small-model implementation steps:

1. Fail closed on unapproved null/indefinite retention at the command boundary. Expose only an approved finite class/bounded range; preserve existing rows until their migration policy is explicitly decided.
2. Have the privacy owner specify question/name/vote/participant-digest retention, archive undo behavior, legal holds if applicable, and backup-restoration tombstones. Record how any existing null rows are treated.
3. Implement a bounded, resumable scheduled cleanup using existing job/lease primitives. Select eligible scalar IDs, reload current lifecycle/deadline, and recheck eligibility inside the fenced mutation so reopening before cleanup is respected.
4. Make deletion/anonymization idempotent and emit only safe aggregate/opaque-ID evidence. Integrate tombstones with the existing privacy workflow; avoid retaining deleted question text in the audit that records its deletion.
5. Give rate buckets an explicit expiry/cleanup policy derived from their window. Clean them separately from authored-content retention; expired counters should not accumulate forever.
6. Add dry-run counts and operational visibility before any approved rollout. Do not execute real-data cleanup as part of the code-fix task without its separate authorized deployment/cutover plan.

Acceptance tests: null is rejected unless an explicit approved class allows it; synthetic frozen-time tests cover immediately before/after deadlines; archived then reopened sessions are not purged by stale work; reruns/restarts do not double-delete or fail irrecoverably; expired rate buckets disappear without altering active-window counters; retained audit/tombstone records contain no participant content or secrets.

Dependencies: named privacy-owner policy approval is required for destructive retention behavior. The null-configuration guard and safe dry-run inventory can be groomed separately. Coordinate EVT-04 terminal deletion, without treating collection exclusion as proof that physical data was purged.

## EVT-08 — Session revision conflicts escape the public API's error contract

**Priority:** P1. **Confidence:** high for the exception/rollback behavior; the incidence and exact concurrent interleavings are not measured. **Classification:** confirmed error-adapter gap on a real service exception, not a demonstrated lost-write/data-corruption result.

Evidence:

- `_bump_session` at `events/qna/services.py:339` uses a revision compare-and-swap and raises `core.RevisionConflict` when the update loses.
- Submission, status/pin changes, votes, and expiry changes share that session revision. `vote_question` at line 731 bumps it even for a repeated vote-row no-op. `list_questions` at line 810 may refresh an expired session and therefore also encounter a state write during a read.
- Public request adapters catch only `QnaError` at `events/qna/views.py:231`, `257`, and `274`; `RevisionConflict` is not translated there. The management API's `_qna_error` supplies a safe conflict mapping, showing the intended pattern.

Probe: inject a `RevisionConflict` at the session bump during a public vote. The view raises rather than returning the expected JSON `409`; the atomic transaction rolls back and the question remains at its previous score. The fault injection proves handling and rollback, not an observed multi-process race. Shared session versioning makes contention a plausible trigger during live polling/moderation/audience activity, but no load claim is made.

Small-model implementation steps:

1. Centralize translation of expected Q&A/domain concurrency failures for public HTML/JSON adapters. Return a bounded safe `409` envelope and the appropriate refresh/retry cue; never leak exception details or question content.
2. Decide whether safe database-only anonymous vote/submit operations warrant a small bounded retry *outside* the failed atomic transaction. Do not retry arbitrary exceptions, loop indefinitely, or reuse this pattern for external side effects without durable idempotency.
3. Avoid bumping the revision for a true no-op vote/pin if the revision contract does not require it. Keep score and vote-row invariants atomic and preserve one vote per participant/question.
4. Add intentional concurrency tests around independent questions in one session, duplicate vote add/remove, settings/moderation conflict, and expiry-on-read. Use the repository's supported database-test patterns rather than assuming one SQLite fault-injection test proves every deployed database behavior.
5. Connect safe conflicts to the client reconciliation fixes already owned by UX-05/UX-07; a frontend rollback cannot make a 500 an acceptable server contract.

Acceptance tests: an injected expected revision conflict returns JSON `409`, leaves no partial write, and never produces a debug page; bounded retries have a fixed exhaustion result; duplicate add/remove does not corrupt scores; concurrent successful commands converge on valid session counters/pin state; expiry-triggered reads return a safe current state or safe conflict, not an unhandled exception.

Dependencies: coordinate the management revision design in EVT-02. This card does not authorize replacing the existing optimistic-concurrency mechanism wholesale.

## EVT-09 — The public configuration includes management-only internal identifiers

**Priority:** P2. **Confidence:** high. **Classification:** narrow public DTO/spec mismatch. **An Event UUID is not treated as a secret, and no authorization bypass or public UUID lookup route was demonstrated.**

Evidence:

- `_docs/architecture/event-qna-integration.md:57` defines the safe public configuration and line 80 explicitly excludes an Event UUID from a public response. Line 27 keeps UUIDs as management identities; public identity is the positive public ID/current slug.
- `events/qna/services.py:1081` exposes the general session serializer with `session_id`/`event_id` at lines 1093–1094, along with management-oriented metadata.
- `events/qna/views.py:120` reuses that serializer for ordinary participant configuration and removes only `host_links` at line 135.
- A rendered synthetic anonymous Q&A response contained both the internal Event UUID and Q&A session UUID in its JSON configuration.

Impact: the public frontend is coupled to a broader management DTO than promised, making future serializer expansion capable of unintentionally expanding public exposure. The present UUID values alone do not confer authority; severity is intentionally lower than the authorization finding.

Small-model implementation steps:

1. Add a dedicated public configuration serializer with an explicit allowlist: contract, canonical relative API/share/QR paths, allowed state/settings, fixed text limit, capability flags, and safe banner.
2. Keep the management serializer separate. If an authorized host needs additional metadata, use an explicit host DTO rather than inheriting every management field.
3. Preserve ordinary-participant omission of host links and all credential/cookie data. Do not change canonical public routing or accept UUID-based public lookups.
4. Update the frontend only if it actually consumes a removed field; do not reintroduce management IDs as convenience aliases.

Acceptance tests: assert an exact safe public configuration schema for anonymous/author views; assert internal Event/session IDs, credentials, actor identifiers, retention internals and unrelated management data are absent; staff/co-host views expose only their explicitly permitted additions; the admin API retains required UUID/revision metadata.

Dependencies: align capability flags with EVT-01; no database migration is required.

## Positive controls and bounded unfinished contracts

The audit intentionally does not turn every inspected subsystem into a finding:

- Participant ownership is checked against a digest, not a caller-supplied question author. `can_author_edit` enforces matching participant, visible status, score at most one, and the 300-second window (`events/qna/services.py:537`). The final synthetic probe verified a different participant, an expired edit window, and a second vote all block author editing.
- Co-host cookies are signed and bound to the current session/invite, and `cohost_for_request` checks the database grant/revocation (`events/qna/services.py:934`). Synthetic tests confirmed cross-session rejection and immediate revocation. Those controls must survive EVT-01.
- Browser CSRF is present; the P0 probe explicitly used it correctly. Participant/co-host cookies use Secure/HttpOnly/SameSite flags. This review did not inspect live browser cookie transport or deployment TLS settings.
- API authentication checks scoped bearer identity before handlers. One-time co-host creation uses principal-fenced idempotency with a safe stored result excluding the passcode (`management_api/views.py:1080`). Existing Q&A tests cover one-time result behavior; no raw token/passcode logging leak was newly demonstrated.
- `services.admit_rate` hashes identities and uses atomic conditional bucket increments. EVT-06 concerns the chosen key scopes and EVT-07 their lifetime, not a proven non-atomic counter implementation.
- Public event visibility and Q&A draft/archive gates are present. Expiry closing an open session during a read was observed, but is not independently classified as a defect: automatic expiry is intentional; its safe concurrency/error handling is EVT-08.
- Management operation lookup is principal-scoped in `management_api/operations.py`; core operations use revision/status checks. Start/progress reject incompatible/cancel-requested work; terminal operations reject cancellation; cancellation must be requested before marking a live operation cancelled; successful finish rejects an outstanding cancellation (`core/operations.py:197`, `238`, `306`, `350`, `396`). Existing operation/bulk tests passed. No new confirmed cross-principal operation read/cancel or terminal-state overwrite was found.
- Core operation safe payloads/errors are bounded and redacted. Failure after cancellation is not assumed to be a defect: distinguishing an actual worker failure from successful completion after cancellation requires the documented lifecycle, not a blanket ban on every terminal result.
- `bulk_moderate` exists as a bounded at-most-100-item service with per-item safe codes (`events/qna/services.py:981`), but no corresponding registered/routed management command was found. The architecture's bulk command is therefore an **unwired feature contract**, not evidence that a deployed bulk endpoint has a cancellation bug. Groom route/capability/OpenAPI/confirmation/idempotency/operation-resource integration if this feature belongs in the intended release; do not fabricate an endpoint or claim existing operation tests exercise it.

## Prioritized implementation batches

| Batch | Scope and owner boundary | Completion evidence |
| --- | --- | --- |
| 1 — release-blocking authorization | EVT-01; Q&A adapter owner, coordinated with BE-01 canonical auth owner | Valid-CSRF revoked-session regression; staff/co-host positive and negative matrix; redacted audit assertions |
| 2 — shared command guarantees | EVT-03 actor-safe idempotency, then EVT-02 Studio/API parity; coordinate EVT-08 revision errors | Two-principal retry tests, paired adapter behavior tests, stale-form/confirmation/one-time-secret tests |
| 3 — question integrity and validation | EVT-04 and EVT-05; Q&A service owner | Terminal-deletion matrix, exact JSON-type tests, valid name-settings matrix, no partial writes |
| 4 — public resilience | EVT-06 and remaining EVT-08; Q&A owner plus reviewed source-IP policy | Deterministic quota identities and exhaustion tests; safe-conflict/rollback/concurrency tests |
| 5 — privacy readiness | EVT-07; privacy owner decision first, then jobs/Q&A implementer | Approved policy, synthetic deadline/reopen/retry tests, safe dry-run counts; authorized rollout separately |
| 6 — narrow public schema | EVT-09; serializer/UI owner | Exact public DTO tests, management metadata retained, no render regressions |

Implementation handoffs should copy the specific evidence, steps, acceptance tests, and non-goals from each card into groomed issues. Keep patches small enough that a simpler model can own one shared boundary at a time; do not combine the retention policy decision with an authorization patch or assume metadata parity tests prove runtime behavior. The report's passing diagnostic probes are evidence of observations only, not permission to skip independent tester and PM acceptance after fixes.
