# Public delivery and content-ingest boundary audit — 2026-09-07

This is a bounded addendum to [the repository audit](2026-09-07-repository-audit.md), not a claim that all possible vulnerabilities have been found. It reviews previously undercovered middleware/cache/SEO/media boundaries, content webhook admission and snapshot fetching, activation, and security-sensitive HTML rendering. No application changes, production queries, actual provider calls, deployment changes, issues, commits, or pushes were made.

## Snapshot, authority, and interpretation

Reviewed HEAD: `ca49f1bf1d8bd56b943f4f635f8a7f4f05c8b242`, with a dirty worktree. Existing shared-curriculum changes affect `content_sync/course_repository_ingest.py`, `courses/services/curriculum_import.py`, course models/views, settings, and several specs. They were preserved. Line references describe the working snapshot, not necessarily committed HEAD. PUB-03 and the course-specific portion of PUB-05 must be rechecked when that work lands. Public media/shared-asset changes already covered by ARC-06 belong to the parent audit, not this addendum.

Authority: `AGENTS.md`, `_docs/PROCESS.md`, `_docs/architecture/database-only-content.md`, `_docs/architecture/app-boundaries.md`, specs 02/03/04/07/09, and `_docs/security/non-identity-threat-control-matrix.md`. Existing ARC, BE, REL, and UX reports were consulted to avoid duplicate ownership.

P0 means an evidenced immediate release blocker; P1 means important corrective work; P2 means a smaller robustness defect. No new P0 is asserted here. A **rollout prerequisite** is an unimplemented specified contract, not evidence of an existing exploit or deployed outage. “Reproduced” below means a safe synthetic local check; it does not mean exploitation against a deployed service.

| ID | Priority | Finding | Evidence |
| --- | --- | --- | --- |
| PUB-01 | P1 | Public aliases forward sensitive query values and remain explicitly cacheable | Reproduced locally |
| PUB-02 | P1, rollout prerequisite | Response policy lacks response-based cache vetoes and search no-store coverage | Reproduced guard gaps; static registry gap |
| PUB-03 | P1 | Different course commits can apply out of order; the importer ignores worker/source ownership at mutation time | High-confidence static; snapshot-sensitive |
| PUB-04 | P1 | Image source sanitizer admits browser-normalized off-origin and non-asset destinations | Reproduced locally; not script execution |
| PUB-05 | P1 | Archive resource limits apply after some unbounded work and omit structural/time budgets | Static plus small archive reproduction |
| PUB-06 | P2 | A correctly signed invalid UTF-8 webhook escapes as a server error | Reproduced locally |
| PUB-07 | P1, rollout prerequisite | Content activation/rollback has no durable edge-invalidation intent | Confirmed static target-contract gap |

## PUB-01 — Sensitive query values survive publicly cacheable alias redirects

**Evidence and trigger.** `content/public_views.py:153` implements `permanent_public_redirect`; lines 165–167 copy the raw query into `Location` and set `Cache-Control: public, max-age=300`. `core/middleware.py:281` considers authenticated users, credential headers, and cookies, but not query credentials. The sensitive key vocabulary in `core/preview.py:13` is applied by the preview decorator, not these public aliases.

A synthetic anonymous GET through `ResponsePolicyMiddleware` and this redirect helper, targeting `/blog`, produced `301`, `public, max-age=300`, and a `Location` retaining the synthetic value for each of `preview_token`, `token`, and `auth`. The ordinary `utm_source` control was also retained. This directly violates the sensitive-query exception in spec 02 at line 256: a private token must not pass through or be stored in a public redirect object.

**Impact and limits.** A credential accidentally supplied to a public alias is propagated into another URL and explicitly permitted to be stored by browsers or compliant shared intermediaries. This is not an arbitrary-target open redirect: the inspected targets are explicit reviewed routes. No real token was used, no proxy cache was exercised, and no deployed leak is claimed. The repository's development edge policy currently enforces zero TTL; that does not change the response's public browser-cache declaration or its token propagation.

**Bounded fix, owner: public routing/middleware.**

1. Extract a small shared sensitive-query classifier from the preview key contract; keep its vocabulary code-owned and covered by tests. Inspect decoded query keys case-insensitively, including repeated and encoded spellings, without logging values.
2. Before generating an alias redirect, handle sensitive/private variants according to a reviewed policy: a bounded no-store refusal is the simplest safe default. If removal is deliberately selected, remove all sensitive occurrences, never reflect their values, and make the response no-store. Do not forward the original query through a public redirect first.
3. Add the same sensitive-query veto to response cache eligibility so a different view cannot accidentally make a private variant public. Do not treat this classifier as authentication or imply that it can recognize every secret embedded in arbitrary free text.
4. Preserve ordinary query behavior where the route contract explicitly requires raw-query preservation. The fix must not globally change trailing slashes, podcast selectors, tracking behavior, status codes, or reviewed one-hop aliases.

**Acceptance.** GET/HEAD on representative blog/podcast/event aliases with mixed-case, percent-encoded, repeated, and ordinary-plus-sensitive keys never returns a publicly cacheable credential-bearing `Location`; unsafe methods remain no-store 405 with the correct `Allow`. Ordinary query-preservation fixtures remain unchanged. Test helpers and mounted routes, not just the classifier. Suggested existing suites: `content.tests.test_canonical_routes`, `content.tests.test_podcast_stable_routes`, and `core.tests.test_non_identity_security` under `uv run python manage.py test ... --settings=website.settings.test --noinput`.

**Dependencies/non-goals.** No cache infrastructure change is required to contain this. Coordinate with PUB-02's classifier; do not wait for PUB-07. Do not print supplied query strings in errors or tests' failure logs.

## PUB-02 — The cache policy does not veto unsafe response characteristics

**Evidence.** `core/middleware.py:335` applies a no-store override at lines 364–365 only for a credential-bearing request or private surface. It does not evaluate response cookies, `Vary: *`, error status, general unsafe methods, or a complete route classification. `content/public_views.py:1077` returns wiki query-search output without explicitly setting no-store. Spec 02 at lines 203–225 requires a versioned route registry, disabled unknown routes, private/no-store search, and an unsafe/error response veto.

Safe synthetic middleware probes supplied an anonymous request and downstream `Cache-Control: public, max-age=300`: a response setting a synthetic `sessionid`, a response with `Vary: *`, and a 503 response all retained the public directive. A wiki search probe with its DB/results/render dependencies replaced by inert test responses returned no Cache-Control header. The response fixtures demonstrate missing guards; they are not evidence that an existing public production view currently creates all three dangerous combinations.

**Impact and rollout classification.** Future cacheable views can bypass the intended fail-closed boundary simply by returning an unsafe response. Search already lacks the specified explicit protection, although it is not explicitly marked public. The development source validator, `deploy/development_seo_policy.py:292`, requires all CloudFront min/default/max TTLs to remain zero. Therefore this card blocks enabling positive shared caching, not the present zero-TTL configuration, and does not assert cross-user cache leakage has occurred.

**Bounded fix, owner: cache policy.**

1. Add a route-class registry whose initial default is private/disabled. Register only the already-reviewed public classes and make route resolution tests fail on omissions. Do not infer cacheability from the absence of authentication or from an arbitrary downstream `public` directive.
2. Add a final response guard: only eligible GET/HEAD public routes may retain public caching; `response.cookies`, `Vary: *`, designated error statuses, existing private/no-store directives, sensitive queries, and documented identity/CSRF state force no-store. Inspect Django's cookie container, not only a manually supplied header.
3. Place the final guard so it can observe cookies/header changes made by inner session/CSRF/auth middleware and short-circuit responses. Preserve required operational responses and status-specific security headers. Do not overwrite error bodies or canonical metadata merely to classify caching.
4. Make wiki/docs search and arbitrary unlisted query variants explicitly private/no-store now. Keep the specific approved podcast/event query and redirect rules; a blanket “any query is invalid” change would break accepted contracts.
5. Keep deployed positive TTL disabled until origin, edge, generated route tests, and poison/isolation canaries agree. Do not solve this by weakening the existing zero-TTL validator.

**Acceptance.** Table-driven tests cover anonymous clean public responses; authenticated/header/cookie/query credentials; Set-Cookie added by inner middleware; Vary:*; 400/401/403/405/409/429/5xx; unsafe methods; unknown route names; public 404 eligibility; query search; and retained ordinary aliases/selectors. Include mounted middleware integration tests, not only direct middleware calls. Start with `core.tests.test_non_identity_security`, `core.tests.test_development_seo`, and relevant public route suites. No infrastructure/provider call is needed for unit containment; later positive-cache acceptance needs actual reviewed deployed canaries.

**Dependencies/non-goals.** Share the sensitive-query helper with PUB-01. PUB-07 is a separate required prerequisite to positive caching, not a prerequisite to this guard. This is not a request to cache all public routes or to rewrite the site URL scheme.

## PUB-03 — Course import jobs can overwrite newer content with an older first-time commit

**Evidence.** The webhook enqueues separate jobs keyed by source, commit, and parser at `api/views/course_repository_webhooks.py:86`. `content_sync/course_repository_sync.py:58` names its `JobContext` argument `_context` and does not use it; it checks source enabled/adapter once, fetches/parses, then invokes ingestion at line 70. `courses/services/curriculum_import.py:1274` identifies runs by source UUID/commit/parser. At line 1354, only a run already marked succeeded returns as replay; any new commit proceeds to update the projection in a transaction. There is no expected source generation or current desired commit check at the final write boundary. `ContentSource.sync_locked_at`/`pending_follow_up` at `content/models.py:66` are not an implemented source fence for this path.

**Concrete sequence.** Commit A's first import starts or remains delayed before successfully applying. A newer commit B's independent job applies successfully. A then reaches its first successful apply and writes A's source-managed rows over B. This sequence does not rely on concurrent database writes; serialized completion in the wrong order is enough. Replaying an A run that already succeeded earlier is a different scenario and is correctly short-circuited by the existing replay check.

**Related ownership issue.** The job framework fences its own completion by lease token/expiry (`jobs/execution.py:177` onward), but that does not roll back domain changes made by an expired handler. This importer never carries the job token into its mutation boundary. Source disable/configuration changes during the fetch are likewise not rechecked there. The finding is about this handler's domain ownership, not a claim that the general job completion fence is missing.

**Impact/confidence.** High-confidence static publication/provenance error: the applied curriculum and recorded source SHA can move backwards because of delay, retry, or lease loss. No database concurrency or remote webhook replay was executed. The active shared-curriculum work changes the imported graph; recheck the ownership interface before implementation.

**Bounded fix, owner: course-sync orchestration plus import command boundary.**

1. Define a per-source desired generation/commit and a durable superseded outcome. GitHub webhook arrival order is not authoritative commit order: resolve the registered branch's verified desired HEAD/coalesced sync target and specify force-push behavior. Do not compare SHA strings lexicographically or assume webhook timestamps establish ancestry.
2. Fetch and parse outside the short apply transaction. Pass an immutable expected source identity/generation and, for worker-owned commands, job/lease identity into the mutation service.
3. At the apply boundary atomically validate source enabled/configuration, desired generation, and still-valid ownership; serialize or compare-and-swap the source mutation so two different commits cannot both believe they own the same generation. A preflight check without an atomic write fence is insufficient.
4. Treat a superseded job as a safe no-op with bounded audit metadata; do not overwrite the newer graph, turn it into an endless retry, or mark the newer run failed. Preserve same-commit checksum/idempotency checks and transactional learner-data protection.
5. Make any manual historical rollback a distinct explicit audited operation. Ordinary webhook processing must not acquire implicit rollback authority. Keep independent sources parallel.

**Acceptance.** Deterministic barriers: delay A before first apply, apply B, resume A, and assert B's graph/provenance stays active; revoke A's lease before final mutation; disable or repoint the source while parsing; send duplicate same-commit deliveries; exercise a reviewed force-push and explicit rollback; run two independent sources. Assert no partial curriculum or learner-data writes on rejection. Add these alongside `content_sync.tests.test_course_repository_sync`, `courses.tests.test_shared_curriculum_import`, and `jobs.tests.test_execution`. Run a supported database transaction/concurrency test for the actual fence, not just a mocked generation comparison.

**Dependencies/non-goals.** The source-generation interface must be designed before a small model changes the importer; this is one coordinated bounded task, not a broad jobs rewrite. REL-05's deployment target denial and PUB-05's resource limits are separate defects and can be fixed independently. Do not replace explicit source identity with a global queue lock.

## PUB-04 — Image source checks disagree with browser URL parsing

**Evidence.** `content/services.py:53` permits image `src` when it starts with one slash, has no `?`/`#`, and has no whitespace/control characters. It rejects literal `//` but not backslashes or dot segments. The following synthetic markup survives `sanitize_rendered_html("docs", markup)` unchanged:

```html
<img src="/\evil.invalid/image.svg" alt="synthetic">
<img src="/images/../admin/logout/" alt="synthetic">
<img src="/%2e%2e/admin/logout/" alt="synthetic">
```

Node's WHATWG URL parser, with base `https://datatalks.club/docs/`, resolves the first destination to `https://evil.invalid/image.svg` and both others to `https://datatalks.club/admin/logout/`. No browser navigation or network request was made. `.invalid` is an intentionally non-routable example domain.

**Impact and limits.** The common publication sanitizer is not enforcing its apparent same-origin image boundary: a published image can request an external origin or a non-asset endpoint instead of a vetted immutable asset. This is an origin/privacy and potentially active-GET destination problem, **not demonstrated script execution or SVG XSS**. The precise content-adapter reachability varies: some adapters perform stronger source validation before this shared sanitizer, so do not claim every article import accepts these payloads. The admin logout method defect itself is owned by BE-01; PUB-04 owns URL admission, not that endpoint.

**Bounded fix, owner: rendered-content policy.**

1. Define accepted image destinations in terms of the publication contract and a reviewed asset namespace/manifest, not merely a string prefix. Validate the destination after HTML parsing/entity decoding and against browser-equivalent special-URL rules.
2. Reject backslashes, encoded separators/control characters, dot-segment traversals, protocol-relative/off-origin resolution, and malformed encodings. Do not silently normalize an author-supplied path into a different active application route.
3. When a candidate references a disallowed or missing image, provide a bounded source-path diagnostic and fail/quarantine the candidate before activation according to the adapter contract. Silently stripping `src` and activating a broken image is not sufficient acceptance.
4. Keep ordinary Markdown/article images and correctly manifest-owned assets working. If source adapters have separate policies, share only canonical URL admission mechanics; preserve their intentional format/size/provenance differences.

**Acceptance.** Add sanitizer and adapter publication tests for raw/encoded backslashes, encoded dot segments, duplicate slashes, entities, mixed encodings, query/fragment/control cases, off-origin URLs, and valid local asset paths. Verify accepted/rejected destinations against WHATWG URL resolution in an existing JS/browser test, with requests intercepted so no external traffic is sent. Check release validation refuses unsafe surviving markup and that current safe sanitizer/concurrency tests pass: `content.tests.test_services` and `content.tests.test_sanitizer_concurrency`.

**Dependencies/non-goals.** No dependency on the unfinished shared-asset storage work to reject plainly unsafe URL forms. Coordinate namespace/manifest admission with ARC-06 once its current implementation stabilizes. Preserve script stripping and the existing per-call sanitizer instance; do not broaden HTML/CSP allowances or claim CSP `img-src https:` enforces local images.

## PUB-05 — Snapshot limits do not bound all work before allocation or parsing

**Evidence.** Three related stages lack enforceable resource ceilings:

1. `content_sync/snapshot.py:241` calls `subprocess.run(..., capture_output=True)` and only tests `len(completed.stdout) > max_bytes` at line 280. The entire `git archive` stdout and stderr have already been buffered by then. The 120-second process timeout bounds time, not output memory.
2. `read_snapshot_archive` at line 180 uses `tarfile` automatic decompression and skips directory entries at lines 185–187 before counting admitted files/bytes. A tiny synthetic tar with 100 directories was accepted as `{}` even with maximum file count, total file bytes, and per-file bytes all set to 1. Archive structural/header/decompressed work has no independent cap; tar metadata can consume work before an admitted regular-file check.
3. `content_sync/course_repository_ingest.py:192` does bound the downloaded byte total, but the request at line 285 uses a socket timeout, not a total operation deadline. A stream that continues making progress can retain a worker much longer than that timeout. No monotonic wall-clock budget or cooperative ownership check is present in the read loop.

**Impact/confidence.** Static high confidence for late buffering and missing time budgets; the directory-admission gap is reproduced at harmless scale. Large or pathological repository snapshots can consume excess memory/CPU or monopolize an import worker before the content limits reject them. No large archive, OOM attempt, slow external request, or malicious Git remote was exercised. This is an input/resource boundary issue, not an archive extraction traversal finding: this code reads archive members into memory and does not extract arbitrary paths onto disk.

**Bounded fix, owner: snapshot transport.**

1. Replace unbounded subprocess capture with incremental bounded collection or a project-local `.tmp` spool with explicit byte accounting. Bound stderr independently, kill/reap the child promptly at a limit/deadline, and preserve the existing bounded/redacted failure interface. A spool without a disk-byte limit is not a fix.
2. Add independent limits for total archive entries/headers and decompressed traversal work, enforced before constructing unbounded metadata/content. Keep a legitimate directory allowance separate from the regular-file limit: simply charging each directory as a source file would change accepted repository semantics.
3. Give network fetching a monotonic total deadline in addition to connect/read timeouts, closing the response and returning a bounded retryable error on expiry. Ensure reads cannot each consume a full timeout after the overall budget is already exhausted. Feed long-operation ownership checks into the PUB-03 design.
4. Preserve symlink/hardlink/device rejection, regular-file length checks, duplicate-path rejection, shared admission semantics, and immutable commit selection. Test local archive and codeload transports with the same limits; do not duplicate archive parsing into adapters.
5. Optional adjacent hardening within the same path parser: reject raw control characters and ambiguous dot/repeated-separator forms before `PurePosixPath` collapses them. The audit observed `repo//a.md` and `repo/./a.md` becoming `a.md`, and a newline-bearing name surviving. Do not mislabel this as demonstrated filesystem escape.

**Acceptance.** Use very small configured caps and synthetic streams/processes: output exceeds cap and is terminated before full materialization; stderr has a separate cap; many directory/metadata entries exhaust structural budget; compressed input exhausts decompressed budget; fake-clock progress exceeds the overall deadline; every failure closes/reaps resources. Legitimate nested trees, export attributes, duplicate paths, forbidden member types, and both transport variants retain reviewed behavior. Extend `content_sync.tests.test_course_repository_transport_parity` and `content_sync.tests.test_course_repository_sync`; avoid memory-stress tests in ordinary CI.

**Dependencies/non-goals.** Resource containment can ship independently of PUB-03; cooperative lease fencing needs that command interface. No new content sources, arbitrary remote URL support, git cleanup, or destructive checkout operation belongs here. Fetching uses a code-owned GitHub codeload origin; no exploitable arbitrary-host SSRF was established.

## PUB-06 — Validly signed invalid UTF-8 raises instead of returning a bounded 400

**Evidence.** `api/views/course_repository_webhooks.py:54` correctly verifies the signature before parsing. At lines 63–66, `json.loads(body)` only catches `TypeError` and `JSONDecodeError`. With a synthetic configured secret and matching HMAC over `b"\xff"`, a direct view invocation raises `UnicodeDecodeError`. This occurs before source lookup or job creation. A wrong-signature control was rejected; no actual webhook secret was read or logged.

**Impact/confidence.** Reproduced small P2 robustness defect: malformed authenticated provider input causes a server error and avoidable retries/alerts. It is not an unsigned authentication bypass. Bounded deeply nested JSON probes returned safe 400 responses; this audit does not report a speculative nesting crash.

**Bounded fix, owner: webhook parsing.** Catch `UnicodeDecodeError` at the JSON decode boundary (or perform strict UTF-8 decoding with the same bounded exception handling) and return the existing `github_payload_invalid` 400. Keep raw-byte HMAC verification first and avoid broad `except Exception`, response-body echoing, or logging secrets/raw bodies. Preserve body-size and delivery-ID controls.

**Acceptance.** Correctly signed invalid UTF-8, truncated multibyte input, invalid JSON, and wrong root shapes return bounded 400 with no source lookup/job/fence write; invalid signatures return 401 before parsing; a valid ordinary push retains its 202/idempotency contract. Add cases beside existing webhook tests under `content_sync.tests.test_course_repository_sync` or the API suite that owns mounted request tests. No DB is needed for the malformed-input unit test.

**Dependencies/non-goals.** Independent small fix, suitable for the first implementation batch. Do not replace the custom signature scheme or add new provider compatibility behavior.

## PUB-07 — Activation and rollback lack the durable invalidation half of positive caching

**Evidence.** `_activate_content_release_atomic` at `content/services.py:1269` and `_rollback_content_release_atomic` at line 1380 atomically update source pointers, release statuses, path claims, and audit events. Neither transaction creates a durable invalidation intent. No content invalidation worker/intent implementation was found on this path. Spec 09 at its “Cache and invalidation rollout” section requires intent and pointer swap to commit together, async network invalidation, a bounded TTL backstop, and distinct deployment/rollback identities.

**Classification and impact.** This is a known target-contract/rollout prerequisite, not a new assertion that active zero-TTL CloudFront responses are stale. Development cache TTLs are explicitly kept at zero by `deploy/development_seo_policy.py:292`. If positive caching is enabled without this half of the contract, a successful content activation/rollback can leave previous HTML/assets/feeds cached without any durable record to clear them. ARC-01/02's in-process catalogue/FAQ/docs cache defects are different and can exist even while edge TTL is zero.

**Bounded fix, owner: content-release/outbox; subsequent owner: cache deployment.**

1. Add a durable, versioned invalidation intent keyed by a stable content transition identity/source revision, recording previous/current release identities and approved path coverage. Create it in the same database transaction as activation/rollback and audit. Failed swaps must leave neither pointer changes nor orphan intents.
2. Use the durable job/outbox boundary to deliver after commit. Retries deduplicate an intent; provider timeout/ambiguous acknowledgement must not lose the intent or silently declare completion. No network I/O belongs in the pointer-swap transaction.
3. Define path coverage for changed/removed details, stable assets, aliases, hubs, feeds, and sitemap dependencies. An initial reviewed wildcard policy can be safer than an incomplete incremental graph; do not invent a large dependency engine before required coverage is known.
4. Record/alert terminal invalidation failures without reverting the committed content pointer. Keep bounded class TTLs as the eventual-correctness backstop. Rollback is a new transition with its own intent, not reuse of an old completed activation intent.
5. Separately integrate application-SHA invalidation into deployment finalization/rollback. Keep positive TTL disabled until that integration, PUB-02, and reviewed canaries all pass. Do not retrofit provider calls into unrelated data-seeding scripts.

**Acceptance.** Transaction rollback leaves no intent; successful activation and rollback each leave exactly one correctly bound intent; source-revision conflicts do not publish either competing intent incorrectly; a crash immediately after DB commit is recoverable; provider retries do not lose/duplicate logical work; removed paths and shared outputs are covered; provider terminal failure preserves the chosen pointer and raises an observable alert. Begin with `content.tests.test_activation_concurrency`, `content.tests.test_services`, and `jobs.tests.test_execution`, then add mocked provider and separately approved deployed cache verification. None of these proposed acceptance checks was run against an actual cache provider during this audit.

**Dependencies/non-goals.** Intent creation can be developed without enabling caching. Actual positive-TTL rollout depends on PUB-02 plus reviewed edge/provider/deployment authority. Coordinate deployment receipt/error recovery with REL-02/REL-07 rather than opening a competing release mechanism. Do not replace database-owned content with an on-disk projection to make invalidation easier.

## Reviewed controls with no new finding

- Request body admission in `core/middleware.py` limits normal and webhook payloads, checks actual seekable ASGI stream size, restores stream position, and has coverage for misleading/missing lengths. No safe probe established a body-limit bypass in that reviewed boundary.
- Webhook HMAC uses raw bytes and constant-time comparison; source owner/repository/branch and commit SHA are validated; delivery fencing binds a hashed delivery identifier to a body digest and transaction. Malformed/wrong signatures do not gain queue authority. PUB-06 is a decode-error handling gap after valid authentication.
- Canonical URL generation validates the exact production HTTPS origin and reviewed query grammars. Public redirect destinations inspected here are explicit targets. No arbitrary-target open redirect was established.
- Public JSON-LD construction escapes `<` before safe template insertion. The shared HTML sanitizer strips disallowed active elements and builds a cleaner per call; existing concurrency protection should be retained. PUB-04 narrows a URL attribute policy discrepancy, not a blanket “rendered content permits JavaScript” claim.
- Public media serving is database-descriptor-gated, verifies expected bytes/checksum, and returns bounded no-store failure for missing/corrupt recorded media. Deployed settings reject the memory backend. Shared curriculum storage evolution belongs to the parent ARC-06 refresh; no duplicate claim is added here.
- Snapshot readers reject symlinks, hard links, device/special entries, duplicate admitted paths, and regular-file size mismatches. They do not perform filesystem extraction. PUB-05 concerns resource accounting before/around those checks.
- Generic content activation/rollback retains transactionality, source/release revision checks, active-path collision controls, and audit. Absence of an explicit row lock is not independently reported: the existing compare-and-swap/retry machinery has a documented concurrency contract.
- The outbound URL helper in `core/security.py` is tested, but no inspected runtime caller wires it into arbitrary ingestion. Therefore its existence is not presented as universal SSRF protection. The course snapshot fetch itself derives a fixed GitHub codeload origin from a registered source; no arbitrary-host exploit was found.

## Evidence ledger, limitations, and handoff batches

Executed baseline command:

```bash
PUBLIC_MEDIA_STORE_BACKEND=memory uv run python manage.py test content_sync.tests.test_course_repository_sync core.tests.test_non_identity_security.BoundaryHelperTests core.tests.test_non_identity_security.RequestBoundaryBodyTests --settings=website.settings.test --noinput --verbosity=1
```

Result: **18 tests passed in 0.020 seconds**, exit 0; `System check identified no issues (0 silenced).` The explicit memory backend is for local synthetic/test execution only. A short repeat was used to recover the exact result after tool-output context rollover; it was not an additional broad validation gate.

Additional safe probes used `uv run python` with test settings, synthetic request/response objects, mocked render/results, an in-memory tar of 100 directories, and a synthetic HMAC secret. They established the PUB-01/PUB-02 header behavior, PUB-04 sanitizer preservation, PUB-05 directory accounting, and PUB-06 exception exactly as described. Node's built-in WHATWG URL parser was used only to resolve the three synthetic image destinations without fetching them. These one-off probes were not added as application tests and are not a substitute for the acceptance suites listed on each card.

This addendum did not rerun the full Django/Playwright/release matrix; inspect actual deployed CloudFront/WAF/ALB configuration; call GitHub/codeload/S3; exercise genuine production media or registration data; exploit a rendered browser page; or perform destructive/resource-exhaustion testing. Course ordering and invalidation findings are static, not concurrency/provider reproductions. The repository remains concurrently modified, and findings touching those paths need a final line/interface recheck before delegation. No new source or dependency changes were made by this audit.

Recommended bounded batches:

1. **Small independent containment:** PUB-06, then PUB-01 and the clearly invalid URL forms in PUB-04. Each gets focused new regression tests and independent review; no infrastructure changes required.
2. **Course ingest ownership/resource work:** one owner designs PUB-03's generation/lease apply interface; another may implement PUB-05's transport limits without editing the same importer concurrently. Stabilize current shared-curriculum work before integrating the mutation fence.
3. **Cache contract implementation, still zero TTL:** PUB-02 registry/response veto and PUB-07 transactional intent can run in separate modules with an agreed transition/class interface. Existing ARC in-process cache defects remain separately actionable.
4. **Explicitly authorized rollout only:** provider/edge/deployment integration and deployed poisoning/isolation/invalidation evidence. Passing local tests does not authorize enabling positive caching or changing a production account.

Do not assign all seven cards as a single generic “security cleanup.” Their ownership and acceptance boundaries are deliberately different; small implementations should change only the named mechanism, preserve current URL/content ownership contracts, and leave unrelated audit/WIP files untouched.
