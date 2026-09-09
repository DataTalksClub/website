# Audit coverage, completion criteria, and evidence limits

This is the scope ledger for the [7 September repository audit](2026-09-07-repository-audit.md). It distinguishes completing the requested investigation from fixing the resulting backlog or accepting a deployment.

## Requested outcomes and deliverables

| User-requested outcome | Work performed | Durable result |
| --- | --- | --- |
| Find code problems | Traced authorization, grading, persistence, identity, durable work, public reads, content sync, event services, and CI boundaries; exercised selected failures with synthetic inputs | [Backend](2026-09-07-backend-security-audit.md), [architecture](2026-09-07-architecture-duplicates-audit.md), [public boundaries](2026-09-07-public-boundaries-audit.md), [events/management](2026-09-07-events-management-audit.md), and [CI](2026-09-07-ci-verification-audit.md) cards |
| Find potential duplicates | Compared normalized function ASTs across 1,447 Python files and reviewed repeated responsibilities for behavioral drift | ARC-07/12/14 and the main report's consolidation table; 19 exact duplicate groups are candidates, not independently counted defects |
| Find UX problems | Read templates/scripts and public/staff flows; ran routed desktop/mobile accessibility smoke and isolated Chromium interaction probes; inspected three screenshots | [UX audit](2026-09-07-ux-audit.md), including keyboard access, focus, error recovery, progressive enhancement, and dangerous confirmation behavior |
| Analyze release preparation | Traced active workflows, release selection/receipts, shell/controller behavior, web-worker coherence, verification provenance, and recovery boundaries | REL-01/02/06/07/08/19 and the [CI addendum](2026-09-07-ci-verification-audit.md) |
| Analyze ingestion scripts | Traced claims/progress, source checkouts, production target selection, local dataset preparation, historical identity/learner imports, content releases, curriculum/media, and webhook jobs | REL-03/04/05/09–18, relevant ARC cards, and the [public-boundary addendum](2026-09-07-public-boundaries-audit.md) |
| Launch multiple agents | Three specialist agents ran concurrently; they subsequently covered CI, public boundaries, and events/management gaps while the coordinating agent continued backend review and consolidation | Separately owned reports, per-owner evidence ledgers, and independent corrections to high-risk backend conclusions |
| Detailed fixes usable by simpler models | Supplied stable IDs, named files/symbols, observed failure, ordered changes, acceptance cases, non-goals/dependencies, execution waves, and a bounded assignment prompt | Main index/task template plus the owning implementation cards; this is grooming input under [PROCESS.md](../PROCESS.md), not a substitute for it |
| Save to docs | Stored the main index, seven specialist reports, and this scope ledger under the repository's required point-in-time audit directory | `_docs/audits/2026-09-07-*.md`; scratch evidence remains under `.tmp/` |

## Source-surface coverage

The following table describes actual attention, not a claim of complete line coverage. A reviewed subsystem can still contain undiscovered defects. Related findings sometimes share an interface but require independent fixes.

| Surface | Principal inspected areas | Kind of evidence / limitation |
| --- | --- | --- |
| Identity and management entry | `accounts/auth.py`, user/token models, Studio authorization, admin/loginas routing, new management authentication, compatibility API safety | Synthetic session/admin/API-shape cases plus static path tracing. Existing scoped credentials and answer-key staff checks were observed; no deployed gateway or real credential test |
| Learner writes and grading | Enrollment/account toggles, homework/project submission, peer-review lookup/save, criteria scoring, assignment/votes, project results, leaderboard contexts | Routed synthetic requests and value/race probes; exact reproductions in BE cards. No historical learner export or PostgreSQL concurrency run |
| Mail and durable work | Legacy send callers, Relay unsubscribe bridge/intents, durable jobs, scheduler/lease interfaces, import preference reconciliation | Mocked send interleavings and failed-job recovery. Generic job fencing exists; job-specific missing fencing is evaluated separately. No live sends/provider mutation |
| Public content and media | Catalogue/document readers and caches, FAQ publication/assets, release-bound reads, response/cache middleware, HTML media validation, course catalogue/query composition | Synthetic cache/URL/HTML checks, focused catalogue/FAQ suite, SQL query-shape inspection; not a production performance benchmark |
| Curriculum and source sync | Legacy/shared parsers/importers, identity/order behavior, source archives, webhook validation, import jobs, routes/storage | Static review, route resolution, arithmetic and bounded parser/job probes. Shared-curriculum changes were already in progress and remain snapshot-sensitive |
| Browser/UI | Public page shell, Studio confirmation forms, settings/country controls, learning links, Q&A polling/composer/moderation, enrollment tables/forms | Actual JS and Django attribute rendering in isolated Chromium plus two routed smoke cases and inspected desktop/mobile screenshots; not every page/state/assistive technology |
| Event and management services | Q&A public/moderator boundary, command services, validation, idempotency, revision checks, capability adapters/parity | Synthetic requests and DB-backed service probes; public-browser UI defects remain in UX, server-boundary defects in EVT |
| Release and ingestion | `.github/workflows`, `deploy`, `scripts/prod`, dataset/source preparation, CMP learner/history claims, editorial/FAQ/events/sponsor imports | Safe contract suites, stubbed commands, crash/checkpoint simulations, static recovery analysis. No AWS deployment or destructive checkout/database action |
| Verification/test safety | `ci`, change classification/evidence/gates/runner, `test_support`, runtime no-network guard, workflow path coverage | Isolated provenance/authorization/process probes and existing unit checks. No attempt to contact a production service |
| Local-review tooling | `review_import/environment.py`, `review_import/workflow.py`, review settings, no-network middleware, related preparation scripts | Read-only inspection of sanitization/target ownership/staging/locking and method/network assumptions. No new real-data export was built; do not treat this as end-to-end production-data certification |
| Maintenance and schema | Duplicate AST scan, Ruff/mypy scope, Make quality targets, migration drift, database portability | Source scan and repository-wide configured static/schema checks. Lint/type errors in concurrent WIP are recorded below, not silently repaired |

## Final configured quality checks

These commands run local analysis using the existing environment. They are not equivalent to the full regression suite or an independently accepted verification plan.

| Command | Observed result | Interpretation |
| --- | --- | --- |
| `make lint` | Failed: 6 findings, of which 5 are fixable, in shared-curriculum work/fixture code | The initial snapshot had 9. Concurrent storage cleanup removed root-generated notebook artifacts; no audit auto-fix was applied. Read `.tmp/audit-20260907/lint-final.log` |
| `make typecheck` | Failed: 5 errors in `content_sync/tests/test_course_repository_shared.py`; 546 source files checked | The manifest fixture is typed too broadly as `object` for length/index/iteration uses at lines 101/102/104/109/110. Give parsed fixture data a validated shape or narrow assertions/types at its boundary; do not suppress the module. Read `.tmp/audit-20260907/typecheck.log` |
| `DTC_TEST_RUN_ID=audit-completion-20260907 PUBLIC_MEDIA_STORE_BACKEND=memory make migrations-check database-portability-check` | Passed: no model drift and portability check passed | Does not prove data migrations are safe for actual historical data. Read `.tmp/audit-20260907/schema-checks.log` |

The schema command was repeated because its first completed process output was unavailable during continuation. The durable second log above is the evidence used here. Test/media settings changed concurrently, so ARC-06 explicitly distinguishes the fixed empty-storage-root default from the still-unimplemented asset serving/durability contract.

Static-check cleanup is for the existing curriculum owner, not a competing migration/refactor task. Coordinate before editing those files. Fix the specific import/fixture/type issues, rerun the same targets, and retain substantive asset/identity acceptance tests; a green formatter/typechecker does not close those behavioral findings.

For a narrow static-check handoff: sort the import blocks in `content_sync/course_repository_layout.py`, `content_sync/course_repository_v2.py`, and `content_sync/tests/test_course_repository_shared.py`; import `Mapping` from `collections.abc` in the layout module; remove the unused test-module `sys` import. The sixth finding is the synthetic `content_sync/tests/fixtures/course_repository/llm_zoomcamp_shared/01-agentic-rag/code/notebook.ipynb`, whose notebook JSON lacks `nbformat_minor`. If intended as a valid notebook, make its minimal schema valid and preserve the fixture's curriculum semantics; if deliberately malformed, move/exclude only that negative fixture with an explicit owning test, rather than exempting all notebooks. Address the five type errors by defining/narrowing the manifest fixture shape where it is created/read, not by broad ignores or blind casts that conceal missing keys.

## Completion and limits

The audit deliverable is complete when the requested domains have an owning report, confirmed findings have bounded implementation instructions, conditional findings state their assumptions, the master index matches the report IDs/priorities, and local links/anchors and Markdown integrity validate. The coordinating agent performs this final consolidation after the specialist reports finish. This document does not mark any application fix accepted.

Final completion check: all three specialist owners finished; nine documents contain the main index, seven specialist reports, and this ledger. The index and owning cards agree on **83 packages: 13 P0, 61 P1, 9 P2**. The local validator checked all 120 Markdown file/anchor links, unique finding IDs, severity totals, fences, final newlines, and trailing whitespace. `git diff --check` also passed. The checked HEAD remained `ca49f1bf1d8bd56b943f4f635f8a7f4f05c8b242`; unrelated worktree changes remain preserved. The requested audit/report work is complete; application remediation and release acceptance are not.

Out of scope for this read-only investigation:

- Implementing the backlog, modifying application source, repairing real learner history, or changing schema/data to make a diagnostic pass.
- Filing or grooming GitHub issues, committing, pushing, deploying, or claiming the required independent engineer/tester/PM delivery gates have occurred.
- Reading production or registration exports, validating real-person collisions, inspecting live provider credentials, or testing real email delivery.
- Inspecting deployed GitHub environment protections, cloud circuit breakers/IAM, all live tasks, or production database behavior.
- Running the full Django/Playwright matrix, every browser engine, load tests, exhaustive security fuzzing, or a complete manual screen-reader audit.

No finite audit establishes that *all possible* defects have been found. The requested full-repository investigation is delivered as broad documented coverage with evidence limits, not as a security certification or a claim of release readiness. Implementers must recheck current code and the authoritative specs before acting on a card, especially where concurrent work or a product-policy decision is identified.
