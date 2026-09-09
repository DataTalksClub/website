# Architecture, public content, and duplication audit — 2026-09-07

This is one part of the repository audit requested on 2026-09-07. It records defects and implementation work packages; it does not implement application changes or authorize a release. References identify the inspected working-tree snapshot, not just committed code.

## Scope and evidence

- Inspected HEAD: `ca49f1bf1d8bd56b943f4f635f8a7f4f05c8b242`.
- Read `AGENTS.md`, `_docs/PROCESS.md`, `_docs/architecture/app-boundaries.md`, `_docs/architecture/database-only-content.md`, and the relevant content/course specifications.
- Inspected public catalogue readers, docs/FAQ presentation, course catalogue queries, curriculum identity/import/storage, source parser boundaries, and test initialization. Other audit reports cover release commands, auth/API/business workflows, and browser UX.
- The checkout already contained 19 modified tracked files and substantial untracked shared-curriculum implementation. Findings explicitly marked **snapshot-sensitive** concern that unfinished work; recheck them against the eventual frozen change before opening implementation tasks. Existing code and generated `shared-lesson/` files were preserved.
- A structural scan parsed 1,447 Python files, excluding dot-directories, migrations, temporary input, static assets and dependency trees. It compared function ASTs of at least 13 lines after normalizing function names and removing docstrings. It found 19 exact duplicate groups. This detects exact duplicates, not every semantically equivalent implementation.
- All reproductions used `uv run python`, test settings, synthetic in-memory values, and mocks. They did not connect to a deployment, read registration data, send mail, or alter application records. A focused Django run is recorded below.
- `P0` means a blocker for shipping the affected feature; `P1` means a concrete important defect; `P2` means lower-priority engineering work. A P0 in unfinished shared-curriculum work does not establish that the already-deployed website has that defect.

## Finding index

| ID | Priority | Confidence | Problem |
| --- | --- | --- | --- |
| ARC-01 | P1 | Reproduced | Catalogue cache retains transient database failures and can associate rows with the wrong release key |
| ARC-02 | P1 | Reproduced | Docs search and FAQ cross-question links never invalidate on content replacement |
| ARC-03 | P1 | Reproduced | A hardcoded biography marker total can take down all person-dependent public pages |
| ARC-04 | P1 | Reproduced/static | Empty podcast and docs hubs contradict the database-only empty-state contract |
| ARC-05 | P1 | Reproduced/static | FAQ asset publication bypasses the database and FAQ ordering remains code-owned |
| ARC-06 | P0 | Reproduced/static; snapshot-sensitive | Shared lesson assets are written and linked without a serving route or configured durable storage |
| ARC-07 | P1 | Reproduced/static; partly snapshot-sensitive | Three Markdown link/image rewriting implementations disagree and rewrite code examples |
| ARC-08 | P1 | Static; snapshot-sensitive | Shared lesson moves and slug changes lack stable identity/alias enforcement |
| ARC-09 | P1 | Arithmetic reproduction/static; snapshot-sensitive | Soft-retired curriculum positions grow exponentially on later imports |
| ARC-10 | P1 | Query inspection/static | Public course counts create a multi-relation join product and repeated per-card queries |
| ARC-11 | P2 | Reproduced/static | Docs detail composition repeatedly loads the complete docs corpus |
| ARC-12 | P2 | Structural scan/static | Duplicate fixtures, identity mappings, and legacy helpers need bounded consolidation |
| ARC-13 | P1 | Static | Legacy lesson media uses a mutable GitHub branch despite immutable imported provenance |
| ARC-14 | P2 | Static/observed test setup | Every test database depends on the complete one-time staging corpus |

## ARC-01 — Catalogue cache must distinguish a failed read from empty content and honor its release key

**Evidence:** `content/catalogue.py:64-119`, particularly `_records()` at line 92 and the `except DatabaseError: return ()` branch. `records()` resolves an active release ID, but `_records(release_id, kind)` does not constrain the SQL query to that ID. It re-resolves the source's active pointer through an `F()` expression instead.

**Trigger and impact:** A cold cache read fails once while the source's active pointer remains unchanged. The error becomes a successfully cached empty tuple. Recovery of the database does not restore that collection in that worker; it requires cache eviction, another release ID, or process restart. Images can consequently look missing and detail routes can return 404 after the database has recovered. Separately, activation between the pointer read and row read lets data from release B be cached under release A. A later rollback to A can expose the cached B records. The cache has no TTL, so this is not bounded by ordinary cache freshness settings.

**Reproduction:** Patch `ContentDocument.objects.filter` to raise `OperationalError`, call `_records("synthetic-release", "book")`, then replace the mock with a healthy query yielding a book and call again. Both results are `()` and the recovered query is called **zero times**. Calling `_records("old-release", "book")` with a mocked current record returns that record; the captured SQL filter is `release_id=F("release__source__active_release_id")`, not the supplied ID.

**Fix instructions:**

1. Separate pointer resolution, immutable release loading, and presentation. Bind the document query to the exact release ID used as the cache key.
2. Decide and implement a coherent concurrent-activation policy: either finish the request on the previously resolved immutable release, or re-read/retry if it is no longer active. Do not cache the newer release under the older ID.
3. Do not catch a database exception inside the cached function and return a normal empty value. Let it escape that function; translate it at the request boundary according to the outage policy, with bounded non-content diagnostics.
4. Preserve the real empty-database behavior: no active source/pointer is an empty catalogue, not an operational exception. Do not add a bundled fallback.
5. Keep cache entries bounded and avoid caching mutable caller-owned dictionaries that callers can change across requests; treat this as a follow-up while changing this seam.

**Acceptance:** Simulate a failed first row query followed by success without changing the release ID; the second request must query and recover. Exercise A → B → A with the pointer change between lookup and row retrieval; A must never return B's content. Test source disable/re-enable, true empty state, ordinary repeated reads, and two independent worker caches. Run `uv run python manage.py test content.tests.test_catalogue --settings=website.settings.test --noinput` plus the new activation/cache regression module.

**Dependencies/non-goals:** None. Do not replace the whole content-release service or require process-wide cache clears from the importer; imports and web workers need not share a process.

## ARC-02 — Docs search and FAQ question maps cache mutable database content forever

**Evidence:** `content/docs_presentation.py:233-276` decorates `_docs_search_corpus()` with a no-argument `lru_cache(maxsize=1)`. `content/faq_data.py:126-160` caches `_faq_question_reference_index(course_slug)` using only the course slug. Their comments still describe immutable/frozen projections, whereas their readers now query published database documents.

**Trigger and impact:** A worker first reads docs or FAQ release A, then the database activates B or rolls back. Docs detail pages can show B while search still matches A. FAQ answers can show B while links resolve to deleted A question IDs. An initially empty read can keep search empty after ingest. Each web worker can disagree depending on when its first request arrived.

**Reproduction:** Synthetic docs replacement produced `old matches=1`, `new matches=0`, and one total `docs_pages()` call. Replacing a synthetic FAQ question ID from `aaaaaaaaaa` to `bbbbbbbbbb` retained the old filename-to-ID map with one total `faq_course()` call.

**Fix instructions:**

1. Add source-specific active-release lookup helpers for docs and FAQ, following the corrected semantics in ARC-01.
2. Pass the resolved release ID to a bounded cached corpus/index builder. Its database query must use that ID, not separately read a moving pointer.
3. Pass the already-resolved FAQ document or a release-bound index into answer rendering, so one response cannot combine question bodies and maps from different releases.
4. Remove comments claiming the source is frozen. Do not use a signal or an importer-local `cache_clear()` as the sole invalidation mechanism.

**Acceptance:** Load A, warm both caches, activate B, then roll back A; verify new/deleted titles, new/deleted question IDs, and links in the rendered answer at every step. Verify empty → published and enabled → disabled. Create a second independent cache instance/process or explicitly model two workers. Run the docs search and FAQ tests plus new lifecycle regressions.

**Dependencies/non-goals:** Coordinate the release-key helper with ARC-01, but docs and FAQ fixes can otherwise be separate small changes. No search engine or new cache package is required.

## ARC-03 — Runtime corpus-wide biography canary makes legitimate content updates fail

**Evidence:** `content/catalogue.py:138-173`: `_EXPECTED_LEAKED_TARGET_MARKERS = {"article": 0, "people": 10}`; every nonempty people collection must have exactly ten markers before any person records are returned. `people_by_slug()`/`people_by_path()` are also used by article, podcast and event credit composition.

**Trigger and impact:** Cleaning a historical biography, omitting one affected profile, or publishing a valid smaller people source changes the total. One unrelated biography change raises `ImproperlyConfigured` for the whole collection, so a person detail and any page resolving person chips can fail. This is validation of one historical snapshot on a live request path, not validation of a malicious target.

**Reproduction:** `_records` mocked to return one clean synthetic biography caused `_cleaned_bodies(..., "people")` to raise `ImproperlyConfigured: Public catalogue leaked target marker count mismatch.`

**Fix instructions:**

1. Move the exact historical count/digest assertion into the one-time ingest or migration contract, bound to that exact source revision.
2. At ingestion, normalize the allowed legacy syntax and validate each incoming document. A rejected candidate must leave the prior release active.
3. Runtime presentation may retain a narrow idempotent cleanup during migration, but must not require the whole live corpus to have a frozen number of historical mistakes.
4. Change tests that currently require that global count at render time into importer-bound provenance tests; preserve sanitizer tests for unsupported syntax.

**Acceptance:** A clean one-person release and an updated full release both render. An unsupported unsafe marker fails preparation before activation. All former ten legacy markers remain correctly removed in the reviewed bootstrap. Article/podcast/event pages with ordinary person credits continue to render.

**Dependencies/non-goals:** Separate from ARC-01. Do not broaden the HTML allowlist or silently accept arbitrary malformed content.

## ARC-04 — Empty hub handling is incomplete and tests explicitly exempt the failure

**Evidence:** `content/podcast_content.py:118-123` raises on an empty podcast catalogue. `content/tests/test_catalogue.py:91-99` explicitly excludes `/podcast` from the empty-hub test. `content/review_views.py:86-89` returns 404 when the docs root document is absent. `_docs/architecture/database-only-content.md` says an empty database is normal, hubs render empty, and detail routes 404.

**Impact:** A fresh deployment can fail public podcast availability checks with 500 before ingest, and the docs hub returns a missing-page response rather than its documented empty state. Disabling one source reproduces the same behavior. The podcast helper failure was reproduced with `podcast_seasons(())`.

**Fix instructions:**

1. Make `podcast_seasons(())` return no seasons. Handle the no-season state before callers choose a default/first season or call `season_episodes()`.
2. Render an explicit podcast empty state without invented episodes, feed addresses or counts. Unknown requested seasons and missing episode details should retain deliberate 404 behavior.
3. Render a docs hub empty state at `/docs/` even when its root content row is absent; the hub shell must not invent a documentation article.
4. Remove the test exemption and include `/podcast`, `/docs/`, and `/faq/` in the same empty-hub matrix. If product owners intentionally want a different docs contract, update the normative architecture and tests together instead of leaving the contradiction.

**Acceptance:** With no active docs/podcast/FAQ sources, each hub returns 200 with understandable empty copy; absent details return 404; no filesystem content is consulted. Include mobile/desktop checks for the changed empty-state templates.

**Dependencies/non-goals:** No ingest dependency. Coordinate visible copy with the UX work package; do not seed data as a workaround.

## ARC-05 — FAQ assets remain publishable directly from files

**Evidence:** `content/faq_data.py:265-285` authorizes files using the hardcoded `FAQ_COURSE_ORDER` and `Path.is_file()`; `content/review_views.py:346-350` serves them. Unlike docs asset resolution, this path checks no active `ContentDocument` or `ContentAsset`. `scripts/prod/import_faq.py` validates image declarations but does not create corresponding `ContentAsset` records. `content/faq_data.py:247-250` also chooses the live FAQ course order from code rather than stored position.

**Trigger and impact:** Removing/disabling the FAQ source or dropping an image from its published documents does not unpublish the image URL. A new database-owned FAQ course can appear in the catalogue but is rejected by the asset resolver unless its slug is added to code. This contradicts the database-only publication rule and the architecture inventory's claim that FAQ asset records are database rows. The bytes remaining on disk are already acknowledged migration work; the missing database publication gate is a separate defect.

**Reproduction:** With `ContentDocument.objects.filter` patched to fail if touched, resolving the existing public fixture image `data-engineering-zoomcamp/image_073b1786.png` still returned a local file. No database query was needed.

**Fix instructions:**

1. Import FAQ image metadata into `ContentAsset` or another explicitly owned asset table, including source/release, exact public path, content type, byte length and checksum.
2. Resolve the exact URL only against the active, enabled source. During the existing local-byte transition, read the checked asset file only after that database authorization.
3. Keep CSS design assets separate from editorial images; moving CSS to the static asset system is reasonable but not necessary to fix image publication.
4. Persist editorial FAQ ordering during ingestion and order by that field, rather than using the live runtime tuple. Keep the frozen input-order assertion in the one-time importer if required for bootstrap provenance.
5. Correct the architecture inventory to describe the implementation actually shipped.

**Acceptance:** A declared image is served; absent, unpublished, old-release and disabled-source images return 404; a newly added FAQ course works without a Python slug change. Path traversal and symlink rejection remain covered. The entire test works with synthetic images and no staging tree.

**Dependencies/non-goals:** Coordinate asset-row preparation with the ingest audit. Do not delete checked image bytes until the separate media-storage migration is complete.

## ARC-06 — Shared curriculum assets have no public delivery implementation

**Status:** **Snapshot-sensitive; P0 for shipping shared-curriculum image/code support.**

**Evidence:** `courses/services/curriculum_import.py:752-782` writes `SharedCurriculumAsset` and rewrites images to `/course-assets/lessons/<content-id>/<checksum>/<filename>`. No matching URL/resolver/view exists in the inspected checkout. Default storage remains `FileSystemStorage`. The existing test at `courses/tests/test_shared_curriculum_import.py:122-140` checks stored bytes and rewritten HTML, not HTTP delivery.

**Reproduction:** Django URL resolution of the generated public URL returned `Resolver404`. Settings inspection returned `django.core.files.storage.FileSystemStorage` and `MEDIA_ROOT == ""`. Existing untracked `shared-lesson/` artifacts in the repository root are consistent with that default location; this audit did not create/remove them.

**Current-state refresh:** Concurrent work after the first pass added `MEDIA_ROOT` at `website/settings/base.py:255`, defaulting to `.local-media/` with an environment override, and test settings now use `.tmp/test-media/`. The root-level generated artifacts are no longer present in the current status. The original empty-`MEDIA_ROOT` observation above is historical evidence, not a remaining defect. This correction removes the immediate working-directory default problem, but no emitted `/course-assets/lessons/` route or deployed shared durable volume/object backend was found. Test media now shares one directory across test runs; use a per-run owned storage root when finishing isolation. Keep the P0 scoped to the remaining asset delivery/durability gap.

**Impact:** Successful import produces broken images and unavailable companion files. Importing into one ephemeral deployment container would not make those bytes available in another container. A later endpoint would also need a validated MIME/security policy: this importer currently accepts referenced SVG bytes without the media validator used for editorial assets.

**Fix instructions:**

1. Add a root URL for the emitted path and a course-owned resolver that looks up the exact asset row. Require the owning course/module/lesson to be publicly visible/current, with an explicit decision for immutable historical asset URLs.
2. Reuse or extend the managed media storage abstraction. Bind durable storage through environment configuration. For tests, use `InMemoryStorage` or an owned `.tmp/` location; never rely on the process working directory.
3. Validate image type/bytes, SVG safety, limits and checksum before publication. Serve code files with deliberate content disposition/content type and `nosniff`; do not expose arbitrary executable HTML on the website origin.
4. Handle storage failures as failed imports with bounded diagnostics; database rollback does not roll back object-store writes. Retain safe content-addressed orphan objects for later reviewed garbage collection.
5. Honor the storage API's returned key, or use a backend primitive guaranteeing exact immutable keys. `exists()` followed by `save()` is not a concurrency guarantee, and `save()` may rename on collision.
6. Add a fresh-storage HTTP test to the release preparation verification, not merely an assertion that rendered HTML contains the intended URL.

**Acceptance:** Import synthetic SVG/raster/code fixtures into clean managed storage; request every emitted URL and verify status, MIME, content disposition, bytes and checksum. Missing rows return 404; temporarily missing recorded bytes fail without a cacheable false 404. Restart/change the application worker and verify delivery still succeeds. No root-level `shared-lesson/` directory is created by tests. Reject script-bearing SVG and MIME-mismatched image input before current rows change.

**Dependencies/non-goals:** Finish before shared-curriculum release acceptance. Coordinate with ongoing storage/route owners. Do not fetch GitHub from public requests or treat a mutable remote URL as fallback.

## ARC-07 — Markdown rewriting has divergent parsers and modifies literal examples

**Evidence:** `courses/services/curriculum_import.py:107-120,568-569,679-704` use a small image regex; `courses/services/unit_assets.py:31-41,180-201` uses another; `courses/services/unit_links.py:25-31,179-185` uses a third link regex without code-span/fence awareness. `courses/services/lesson_content.py:18-19,67-113` has a separate partial fence guard. These are similar operations with materially different accepted syntax.

**Reproduction of shared importer:** Plain `![Diagram](images/diagram.svg)` is discovered and rewritten. A titled image, raw `<img>`, and reference-style image are not discovered. A fenced example containing `![Diagram](example.svg)` is treated as a real asset dependency. That example can therefore reject the whole import if `example.svg` is intentionally illustrative and absent.

**Impact:** Legitimate repository Markdown renders broken images, import rejects ordinary documentation examples, and literal source examples may be rewritten into site URLs. The shared module overview is rendered directly at `curriculum_import.py:650-654`, outside lesson asset normalization. Existing legacy link rendering also passes query-bearing Markdown destinations through a different resolution path, so a `.md?query#fragment` link need not resolve like its equivalent fragment-only link.

**Fix instructions:**

1. Define a small common token-level Markdown destination transformer using the already-installed parser. Operate on image/link nodes, leaving code spans/fences and escaped syntax untouched.
2. Give each caller an explicit destination policy: known public lesson/module/homework mapping, managed image mapping, immutable upstream browse link, or rejected/left-literal destination. Do not flatten these policies into one permissive URL function.
3. Support or explicitly reject titled, reference-style, angle-bracket and raw-HTML image forms at ingestion. Supported forms must participate in the same asset existence/import checks.
4. Apply the same policy to shared module overview content and declared lesson code links.
5. Retire redundant regex walkers only after their callers have equivalent fixture coverage; keep source Markdown bytes unchanged in provenance fields.

**Acceptance:** Table-driven tests cover plain/titled/reference/HTML images, escaped images, code spans, fenced code, links with title/query/fragment, relative parent traversal, and absent real assets. A nonexistent illustrative path in fenced code must not fail import. Compare parsed rendered destinations, not string-presence checks alone.

**Dependencies/non-goals:** Shared asset tests depend on ARC-06; pure token-transform tests do not. Do not use this cleanup to broaden HTML or URL permissions.

## ARC-08 — Shared curriculum does not enforce identity continuity or reviewed slug changes

**Status:** **Snapshot-sensitive; static evidence, not a database concurrency reproduction.**

**Evidence:** `courses/services/curriculum_import.py:637-661` overwrites module `slug` by stable ID without checking aliases; `:783-809` scopes lesson upsert to `(module, source_content_id)` and overwrites lesson slug. `courses/models/shared_curriculum.py:143-147` makes source ID uniqueness module-scoped. Read state points to the lesson row at `:219-225`. The parser registers source IDs across the complete source graph in `content_sync/course_repository_v2.py:90-95`. The import test `test_...rename...` around `courses/tests/test_shared_curriculum_import.py:173-201` accepts a module directory/slug change without establishing an alias.

**Trigger and impact:** Moving one stable lesson ID into a different module produces a different row or conflicts with its asset public/storage keys; existing read state remains attached to the retired lesson. Renaming a numbered module/lesson changes canonical URLs while no importer step requires a `CurriculumRouteAlias`. The intended stable identity and old-link contracts therefore do not follow the upsert key.

**Fix instructions:**

1. Resolve lesson identity at the course/shared-curriculum scope before applying module placement changes. If a stable ID moves, preserve its row/read-state identity or deliberately reject the move with a documented diagnostic until supported.
2. Compare prior module/lesson slugs and public paths before overwriting them. Require a reviewed alias manifest or existing validated database alias for every published path change.
3. Validate the complete move/rename plan before changing positions, assets or row ownership. Do not infer identity from titles or auto-create redirects to guessed destinations.
4. Add the necessary domain uniqueness constraint and a migration that detects conflicting existing IDs. Coordinate with the unfinished model migration rather than appending conflicting schema work blindly.

**Acceptance:** Mark a synthetic lesson read, move it to another module with the same stable ID, and confirm the same read state survives or the import refuses atomically. Renaming without an approved alias refuses; with an alias the old route redirects in one hop to the new canonical. Same slug/new ID cannot silently reuse an old published identity.

**Dependencies/non-goals:** Requires agreement on the ongoing shared-curriculum identity/alias contract. Do not delete old progress, auto-merge unrelated lesson IDs, or create user-facing historical lesson selectors.

## ARC-09 — Reordering soft-retired rows eventually exceeds the position column

**Status:** **Snapshot-sensitive.**

**Evidence:** `courses/services/curriculum_import.py:602-617` and `:663-678` calculate `offset = maximum_position + count + 1000`, then add it to every existing position, including already-retired rows. Only incoming active rows get reset to small positions. Both shared module and lesson positions are `PositiveIntegerField` values (`courses/models/shared_curriculum.py:58,111`).

**Trigger and impact:** Remove a module/lesson and keep publishing later commits. The retained retired row's maximum approximately doubles each import. On PostgreSQL the positive integer field is backed by a bounded integer; a routine later import can fail even though the visible curriculum is tiny. SQLite's wider integer storage can conceal the deployment failure.

**Reproduction:** Applying the exact arithmetic to two positions while keeping one row retired exceeded `2,147,483,647` after 22 imports; the retired position reached `4,204,788,758`. This is an arithmetic reproduction, not a PostgreSQL execution result.

**Fix instructions:**

1. Replace additive growth with a bounded two-phase assignment that accounts for both active and retained rows.
2. Prefer an explicit retired ordering policy: assign deterministic compact positions for all retained rows, or make the uniqueness rule apply only to active rows and separately preserve historical order if needed.
3. Validate the entire incoming position set and range before updates. Keep the operation atomic and preserve read state/aliases.
4. Avoid a `BigIntegerField`-only fix: that delays exponential exhaustion but retains the underlying algorithmic problem.

**Acceptance:** Create two modules/lessons, retire one, apply at least 50 different commits, and assert bounded positions, stable active ordering, retained history and no integrity error. Include a PostgreSQL-backed test or explicit field-range assertions; SQLite alone is insufficient. Test reorder/swap/reintroduction as well as deletion.

**Dependencies/non-goals:** Coordinate constraint changes with ARC-08. Do not garbage-collect retired rows to mask the failure.

## ARC-10 — Course catalogue count query multiplies independent child tables

**Evidence:** `courses/services/public_course_catalog.py:25-44` adds four `Count(..., distinct=True)` annotations for homework, projects, enrollments and modules to one cohort query. Inspecting its generated SQL confirms four `COUNT(DISTINCT ...)` expressions and four outer joins. `core/home_content.py:178-180` then calls `cohort.build_items.order_by("position")` for each selected family; the selector does not prefetch build items.

**Impact:** Distinct counts preserve numeric correctness but do not remove the join product that the database must process. A synthetic shape of 10 homeworks × 2 projects × 10,000 enrollments × 10 modules creates up to 2,000,000 intermediate joined rows for one cohort. The homepage uses this query even when it only needs one latest cohort per family. Each displayed family adds another build-item query. Shared-current curricula also remain counted through legacy `modules`, so their visible module count can be zero despite published shared lessons (**that count mismatch is snapshot-sensitive**).

**Fix instructions:**

1. Separate independently aggregated counts using correlated subqueries, pre-aggregated querysets or dedicated count services; ensure no SQL query joins all large independent child collections before aggregation.
2. Select the latest visible cohort per family before fetching expensive details where possible. Preserve the explicit recency and family-normalization rules.
3. Fetch build items using an ordered `Prefetch(..., to_attr=...)`, then consume the prefetched list. Calling `.order_by()` again on the related manager can bypass an ordinary prefetch.
4. Define module count for shared/current and archived deliveries using the proper shared graph/placement semantics. Do not count legacy rows as a substitute.

**Acceptance:** Counts stay correct with multiple children in every relation, zero children, hidden cohorts and shared curricula. Assert a bounded query count for 1 and 20 displayed families. Inspect PostgreSQL `EXPLAIN` using synthetic volumes; no product of enrollments × assignments × modules should remain. Avoid strict wall-clock unit-test thresholds.

**Dependencies/non-goals:** Independent of schema-2 implementation except its module-count branch. Do not optimize by removing visibility checks or deleting count fields consumers require.

## ARC-11 — A docs detail response repeatedly loads all docs and assets

**Evidence:** `content/docs_projection.py:627-669` loads every published page body and every asset each call. `docs_page`, `docs_navigation_tree`, `docs_breadcrumbs`, `docs_children`, `docs_parent` and sequential navigation each call back into that full projection. `content/review_views.py:129-165` invokes several during a single context build.

**Reproduction:** Calling `_docs_detail_context` with a synthetic root plus one child invoked `docs_projection()` **four times** for the context alone. The initial `projected_docs_page()` lookup is another full read. Each projection normally runs two queries and materializes all docs bodies; deeper parent cases can add another read. These repeated moving-pointer queries can also mix navigation and body releases.

**Fix instructions:**

1. Resolve one release-bound docs snapshot or request context and derive its page index/navigation tree once.
2. Pass that tree into breadcrumb/child/parent/sequence functions; pure helpers should not independently reload the database.
3. Fetch asset metadata only for routes/rendering work that needs it; navigation does not need every asset record or page body.
4. If using a process cache, key it correctly as in ARC-01/02. A request-local cache is enough to remove repeated work and simplify consistency.

**Acceptance:** Compare the whole navigation/context shape before and after. Assert constant query counts with 2 and 200 synthetic docs, and that a response uses one release even if activation happens mid-request. Run docs navigation/detail tests and their browser checks.

**Dependencies/non-goals:** Reuse ARC-02's release selection helper. Do not replace the established navigation model or alter canonical docs URLs.

## ARC-12 — Duplicate inventory and safe consolidation boundaries

These are **P2 candidates**, not nineteen separate bugs. Exact repeated fixtures can be cheaper to keep than a broad inheritance refactor. Consolidate when a real fix otherwise needs several synchronized edits.

| Duplicate/candidate | Evidence | Recommended bounded action |
| --- | --- | --- |
| Impersonation policy | `website/loginas_policy.py:1`, `course_management/settings.py:405` are exact duplicates | Security behavior belongs to BE-01 in the backend report. Make the supported runtime use one reviewed policy; preserve the explicitly standalone adopted settings boundary or replace it with a compatibility import only if independent execution still works. |
| Homework fixture creation and posting helpers | `courses/tests/homework_view_base.py:92,253`; `test_homework_optional_fields.py:89,103,143`; `homework_submission_validation_base.py:84,98,146`; `homework_scoring_view_base.py:95,109` | Extract only pure fixture builders/common valid payload construction. Leave failure-case setup and assertions with the tests that own them. |
| Course learner/leaderboard fixtures | `courses/tests/course_leaderboard_base.py:70,114,128,142,158`; `course_view_base.py:87,130,145,159`; `test_course_project_submissions.py:46,60,76` | One fixture factory for the shared cohort/enrollment shape; keep ordering/scoring-specific behavior local. |
| Browser setup/login wrappers | `playwright_tests/test_navigation.py:23`, `test_sponsors.py:23`, `test_site_settings.py:23`; similarly `test_studio_foundation.py:33` and `test_management_credentials.py:35` | Reuse a fixture only when role, session proof and cleanup semantics match. Do not merge distinct privileged and public contexts. |
| Browser visual preconditions | `playwright_tests/test_podcast_design_parity.py:55`, `test_article_design_parity.py:72`, `test_person_design_parity.py:53` | One helper for identical viewport/font/image readiness if needed; preserve each route's screenshot and visible-content assertions. |
| API test payload setup | `api/tests/project_api_base.py:22`, `homework_api_base.py:25` | Small common synthetic actor/client factory; business payload builders remain separate. |
| Datamailer test fixtures | `courses/tests/datamailer_homework_score_base.py:85`, `datamailer_project_score_base.py:94`; recipient helpers at `datamailer_recipient_lists_base.py:130` and `datamailer_project_score_base.py:178` | Align with the messaging migration rather than creating a new shared legacy abstraction that will immediately be retired. |
| Temporary family identity alias | `core/home_content.py:23-47` retains titles and `FAMILY_ALIASES`; canonical write normalization is in `courses/course_family_catalog.py:98-123` | After the reviewed duplicate-family migration is verified, remove the stale UI-only identity workaround and unused title tuple values. Do not remove migration-frozen identity helpers. |
| Markdown URL transformation | ARC-07's four modules | This duplication is already behaviorally divergent and therefore merits focused consolidation first. |

**Additional architecture seam:** `accounts/forms.py:15` imports country validation from `courses.registration`, which also initializes country files and constructs the course Markdown renderer. `content/article_content.py:61` imports display helpers from `core.home_content`, a homepage compositor that itself imports the content catalogue and course selectors. These dependencies make a small reusable helper load unrelated domain presentation. Move country/reference-data validation to an appropriately neutral existing boundary, and date/reading-time helpers to a small public-presentation helper module, only as part of a scoped change with import-boundary checks. Do not create a general-purpose `utils.py` dumping ground.

**Acceptance for any consolidation:** List all actual callers first; change one group per patch; retain distinct synthetic actors/data; compare relevant tests before and after. No registration, scoring, permission or publication behavior should change as a side effect. Architectural tests should prevent the specific reversed dependency, not ban all cross-app imports.

## ARC-13 — Legacy imported lesson assets remain tied to mutable branch content

**Evidence:** `courses/services/unit_assets.py:49-89,99-131` chooses `repository_branch` or `main` for image and code browse URLs and does not use `Unit.source_commit_sha`. `courses/services/unit_links.py:165-172` explicitly uses the branch tip for otherwise unhandled repository links. `courses/views/unit.py` calls these helpers when rendering a stored unit.

**Reproduction:** A synthetic unit with `source_commit_sha="a" * 40` and branch `main` generated `https://raw.githubusercontent.com/example/course/main/01-module/image.png`.

**Impact:** An imported immutable lesson body can silently acquire changed/missing diagrams and companion code without another database import. Different users can receive different content for the same stored lesson as upstream changes. This also remains an exception to the claimed database-managed current asset architecture; a browser request to GitHub is still a content dependency even though Django itself does not fetch it.

**Fix instructions:**

1. Reuse the managed lesson asset ingestion/delivery contract being completed in ARC-06 for legacy source-managed units where those units remain public.
2. If a temporary upstream link is explicitly retained, use a validated immutable commit for browse/download links and distinguish those outbound source links from first-party published media. Do not silently substitute `main` when immutable provenance is missing.
3. Add a precise remaining-violation entry to the database-only architecture inventory until the public image dependency has been removed.

**Acceptance:** Change the branch head after importing a synthetic commit and verify the lesson's published image/code bytes and URLs do not change. A fixture lacking immutable provenance has an explicit safe state. No public request fetches a repository checkout.

**Dependencies/non-goals:** Depends on the managed-media contract in ARC-06 for the final fix. Do not introduce a second bespoke asset store for the old lesson model.

## ARC-14 — Global reference seeding hides assumptions and blocks staging retirement

**Evidence:** `test_support/django_runner.py:24-40` runs `load_reviewed_reference_data()` for every test database before cloning it. `test_support/reference_data.py:118-135` uses `Event.objects.exists()` as the all-families initialization sentinel and then invokes event/docs/FAQ/public-content/testimonial importers. `_docs/architecture/database-only-content.md` already records this removal blocker.

**Impact:** A narrowly targeted model test depends on thousands of unrelated reviewed records and every importer involved in seeding them. A failure in one frozen content artifact blocks unrelated tests before their test methods run. Empty-state bugs can remain untested unless each test remembers to disable/delete global seed records; ARC-04 is an example. A partially initialized database with any Event row makes the sentinel skip the other families. This is known architectural debt, not a newly discovered runtime file fallback.

**Fix instructions:**

1. Define explicit small fixture factories for unit/service tests and a separate opt-in full reviewed-corpus integration tier.
2. Move corpus-dependent tests to the full tier or have them request a named fixture. Replace global seeded-record assumptions incrementally, one domain at a time.
3. Where a reusable full database remains useful, verify per-family import identity/counts rather than using any Event row as proof that all other sources are seeded.
4. Move rules uniquely asserted by the retired projection builder into importer/adapter contract tests before deleting staging input. Follow the existing documented removal order.

**Acceptance:** A pure catalogue empty-state test and unrelated course service test run from a fresh migrated database without the staging tree. The dedicated full-corpus suite still verifies reviewed counts and provenance. A partially populated event table does not falsely certify docs/FAQ initialization.

**Dependencies/non-goals:** Do not delete `temporary/content/` before production ingest and the documented consumer migration. Do not bulk-convert hundreds of tests in the same patch as a public bug fix.

## Verification results and remaining limits

The synthetic reproductions described above completed successfully as diagnostics: they demonstrated the listed failing behaviors. They were not regression tests added to the application suite. Exact commands used the form `uv run python - <<'PY'` with `DJANGO_SETTINGS_MODULE=website.settings.test`, `django.setup()`, and `unittest.mock.patch`; snippets operated on synthetic values only.

The focused regression command was:

```sh
uv run python manage.py test content.tests.test_catalogue content.tests.test_faq --settings=website.settings.test --noinput --verbosity=1
```

Result: **24 tests passed**, test execution **34.439 seconds**, process exit **0**. Test setup also emitted `content.W001` because the local public-media hydration directory is empty; expected 404 scenarios emitted redacted observability messages. Database initialization happens before Django's displayed test-execution duration. This run checks the current baseline; passing existing tests does not disprove the missing lifecycle, storage delivery, and empty-hub cases above.

No production database benchmark, PostgreSQL import stress test, external repository fetch, storage upload, or browser screenshot was performed for this architecture sub-audit. The multi-join cost is inferred from generated SQL and relation cardinality, not a measured production latency. Identity-move and concurrent-storage behavior are static findings pending focused implementation regressions. Shared-curriculum references must be rechecked after its existing owner finishes the active changes.

## Suggested execution batches

1. **Release correctness:** ARC-01 and ARC-02, with one owner for the release-key query helper and independent owners for docs/FAQ consumers. These fixes are small and have decisive synthetic regression tests.
2. **Public empty/publication behavior:** ARC-03, ARC-04 and ARC-05. Keep importer validation, public state handling and asset publication as separate patches, then verify changed hubs on desktop/mobile.
3. **Shared-curriculum completion gate:** ARC-06 first; ARC-08/09 together at the identity/constraint owner; ARC-07's pure token transformer can proceed independently, then integrate. Rebase on the finished existing curriculum work before claiming these as remaining defects.
4. **Request cost:** ARC-10 and ARC-11, each with one before/after query-shape test and synthetic scaling evidence. Avoid simultaneous changes to every selector.
5. **Retirement/consolidation:** ARC-13 uses the finished asset pipeline; ARC-12 and ARC-14 are small follow-ups tied to real consumers. Preserve frozen migration contracts and the production-ingest deletion prerequisites.

For each work package, the implementer should cite its ARC ID, list touched files, add the named regression scenario, and hand off uncommitted work through `_docs/PROCESS.md`. Independent tester and PM acceptance remain required for implementation; this audit is not a substitute for either gate.
