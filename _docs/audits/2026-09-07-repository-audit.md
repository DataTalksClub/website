# Repository audit and implementation backlog — 7 September 2026

The audit produced **83 findings/work packages: 13 P0, 61 P1, and 9 P2**, covering release preparation, ingestion, application correctness, authorization, duplicate implementations, public content, UX, accessibility, CI evidence, and event/management boundaries. Several are established migration gaps or explicitly conditional rollout prerequisites; others were demonstrated with new synthetic reproductions. The highest-impact reproduced problems are cross-database identity attachment during import, peer-review score manipulation, updates to closed reviews through another project's URL, executable metadata in Studio confirmation handlers, and continued Q&A moderation after staff-session revocation.

This report is the starting point. The seven companion reports contain the detailed evidence, affected symbols, reproduction steps, ordered fixes, acceptance tests, and dependencies for every item. A separate [coverage ledger](2026-09-07-audit-coverage.md) maps the requested outcomes and records checks/limits:

| Report | Findings | Use it for |
| --- | --- | --- |
| [Release preparation and ingestion](2026-09-07-release-ingest-audit.md) | REL-01–19: 5 P0 / 13 P1 / 1 P2 | Deployment gates/recovery, import claims/resume, dataset rebuilding, source checkouts, production scripts, and operator instructions |
| [Backend, security, and learner integrity](2026-09-07-backend-security-audit.md) | BE-01–17: 5 P0 / 11 P1 / 1 P2 | Management authority, grading/results, form persistence, identity state, durable unsubscribe, email cutover, and API validation |
| [Architecture, public content, and duplicates](2026-09-07-architecture-duplicates-audit.md) | ARC-01–14: 1 P0 / 10 P1 / 3 P2 | Release-bound reads, database-only publication, curriculum identity/assets, query cost, and consolidation candidates |
| [UX, accessibility, and browser behavior](2026-09-07-ux-audit.md) | UX-01–12: 1 P0 / 9 P1 / 2 P2 | Studio action safety, privacy feedback, keyboard/form accessibility, Q&A recovery/focus, and table sorting |
| [CI verification and test safety](2026-09-07-ci-verification-audit.md) | CI-01–05: 0 P0 / 5 P1 / 0 P2 | Evidence/release identity, source freeze, result ordering, workflow coverage, and target-isolation authority |
| [Public delivery and ingest boundaries](2026-09-07-public-boundaries-audit.md) | PUB-01–07: 0 P0 / 6 P1 / 1 P2 | Sensitive redirects, cache-rollout prerequisites, source ordering, image URLs, archive budgets, and webhook parsing |
| [Events/Q&A and management adapters](2026-09-07-events-management-audit.md) | EVT-01–09: 1 P0 / 7 P1 / 1 P2 | Staff-session authority/audit, mutation parity, actor-safe retries, lifecycle, validation, quotas, retention, and public schema |

The work packages intentionally separate independent failure modes at the same boundary. For example, an import checkpoint needs both correct database ownership and atomic durability. Fixing only one does not fix the other. The duplicate scan's 19 exact function groups are candidates inside ARC-12, not 19 additional bugs.

## Scope and snapshot

User request: find code problems, potential duplicates, UX problems, and release/ingest issues; use multiple agents; save a detailed report with fixes that simpler models can implement.

Three specialist agents worked concurrently on release/ingest, architecture/duplication, and UX. The coordinating agent audited backend/security and consolidated the reports. The architecture agent independently reviewed the backend findings; the final wording incorporates its corrections about existing CSRF/transaction protections and mail-migration dependencies. A completion pass reused those agents for CI evidence/test safety, public security/source-sync boundaries, and event/management services while the coordinator checked remaining learner-result states and configured quality gates.

The audit began on `main` at `ca49f1bf1d8bd56b943f4f635f8a7f4f05c8b242`. There were already modified tracked files and untracked shared-curriculum implementation, fixtures, documentation, migration files, and generated lesson assets. Other work continued during the audit. Consequently this is a working-tree investigation, not a verdict against one immutable commit. Recheck findings marked **snapshot-sensitive** when that work is frozen. Source symbols remain useful if line numbers have moved.

Application files and existing user changes were preserved. Audit deliverables are this index, seven specialist reports, and the [coverage/completion ledger](2026-09-07-audit-coverage.md). Synthetic probes, browser evidence, and test data live under the repository's `.tmp/`. No production/registration export, live account dataset, external provider mutation, deployment, issue creation, commit, or push was performed.

“Find all” cannot be established by a finite source/browser audit. This is a broad, evidence-backed backlog, not a guarantee that every defect has been found. The structural scan covered 1,447 Python files under its stated exclusions; detailed review prioritized reachable critical paths and suspicious seams. Live cloud controls, production data conditions, full screen-reader behavior, every role/route/content permutation, and a full regression suite were not verified.

## How priorities and confidence work

- **P0:** must be resolved before shipping the affected feature, running the affected migration, or declaring its cutover complete. This does not imply every P0 is currently exposed on production. Legacy admin/API/email findings are target-contract gaps; shared-curriculum asset findings concern unfinished work.
- **P1:** important correctness, safety, reliability, accessibility, or operational work. Do not let the number of P0 items turn these into indefinite cleanup.
- **P2:** bounded maintenance or later improvement, generally after immediate behavioral defects.
- **Reproduced:** a local synthetic test or isolated browser component demonstrated the behavior. A passing defect probe asserts the existing bad behavior; it is not a passing fix test.
- **Static/conditional:** source establishes a reachable path or failure mechanism, but a particular cloud state, historical collision, or concurrent production execution was not tested. Those limits are stated per finding.

All priorities are audit recommendations for grooming. [_docs/specs/](../specs/README.md) remains the product/architecture authority, and [PROCESS.md](../PROCESS.md) owns implementation, independent verification, acceptance, and who may commit.

## First decisions and containment work

The repository should not be treated as ready for unconditional production promotion based only on its current successful deployment workflow. REL-01 and REL-02 show gaps in the active path, while the existing five deployment-contract tests still pass. This is a source-level conclusion; external GitHub/environment protections or ECS circuit-breaker configuration were not inspected and are not assumed absent.

Before another multi-target learner import, address REL-03/04 as one checkpoint-design effort. The current default claims files contain source and target integer IDs without enough ownership/provenance. A valid-looking PK can therefore select a different account after changing or rebuilding the target database. A different bug can lose mappings after the database batch commits. Both were reproduced without real identity data.

The following thirteen P0 items need explicit completion or feature containment before their relevant release/migration:

| ID | Required outcome | Scope/qualification |
| --- | --- | --- |
| REL-01 | Deployment consumes successful required verification for the exact candidate SHA | Active workflow dependency defect; no live workflow dispatch was performed |
| REL-02 | Failed promotion restores/contains the web-worker pair and preserves a safe receipt | Active shell path has no application recovery; migration rollback is a separate concern |
| REL-03 | Claims prove source snapshot and target database identity before any write | Reproduced wrong-account attachment of a verified source address |
| REL-04 | Mappings and progress survive the same commit/crash boundary | Reproduced completed progress with missing mappings and missing dependent rows |
| REL-05 | Explicitly authorized deployed curriculum ingest reaches the shared service | Production selection currently reaches a local-only refusal; configuration-stubbed reproduction |
| BE-01 | Admin and support impersonation obey an explicit protected boundary | Application path bypasses Studio evidence; actual deployed gateway exposure remains unverified |
| BE-02 | Compatibility APIs cannot bypass scoped credential policy with legacy tokens | Known live compatibility gap; migrate callers and preserve direct authorized responses |
| BE-03 | Review ownership includes its URL project and both submission relations | Reproduced edit to a closed project's review through an open project's URL |
| BE-04 | Review answers obey criterion type, allowed options, and cardinality | Reproduced maximum-3 radio criterion scoring 9 and persisted out-of-range indices |
| BE-13 | Business mail uses atomic logical intent/job and approved Relay transport | Known cutover gap; configured legacy Datamailer paths still initiate new work |
| ARC-06 | Imported shared assets have authorized HTTP delivery and durable storage | Snapshot-sensitive unfinished curriculum work; generated route currently unresolved |
| UX-01 | Display metadata is never compiled as privileged Studio JavaScript | Browser probe proved execution through Django-rendered confirmation text |
| EVT-01 | Public Q&A host/moderation adapters enforce current staff authority and audit | Revoked Studio session moderated successfully with valid CSRF; room-scoped co-host controls remain distinct |

BE-01, BE-02 and BE-13 require decisions about retained compatibility/break-glass/email consumers if those surfaces stay enabled. Their first tasks can inventory and close unsupported paths. They must not become unspecific “rewrite authentication” or “rewrite email” assignments. The companion reports split containment from the larger migration.

## Complete finding directory

Use the stable ID to assign work. Each row links to its owning report; the matching heading there provides the implementation card. Confidence and detailed evidence remain in that card so this index does not dilute important qualifications.

### Release and ingest

| ID | Priority | Problem to fix |
| --- | --- | --- |
| [REL-01](2026-09-07-release-ingest-audit.md#rel-01--required-application-verification-does-not-gate-delivery) | P0 | Independent application CI does not gate deployment |
| [REL-02](2026-09-07-release-ingest-audit.md#rel-02--failed-promotion-leaves-services-changed-and-erases-recovery-evidence) | P0 | Failed/partial promotion leaves services changed and removes recovery evidence |
| [REL-03](2026-09-07-release-ingest-audit.md#rel-03--claims-are-not-bound-to-the-source-snapshot-and-target-database) | P0 | Reused claims cross source/target identity boundaries |
| [REL-04](2026-09-07-release-ingest-audit.md#rel-04--claims-and-watermarks-do-not-survive-the-actual-crash-boundary) | P0 | Committed progress outruns durable claim mappings |
| [REL-05](2026-09-07-release-ingest-audit.md#rel-05--the-deployed-course-pull-always-refuses-the-selected-production-target) | P0 | Production course pull invokes a local-only guard |
| [REL-06](2026-09-07-release-ingest-audit.md) | P1 | Deployment success does not establish worker/all-task release coherence |
| [REL-07](2026-09-07-release-ingest-audit.md) | P1 | Promotion receipts lack complete durable provenance and deployment-code binding |
| [REL-08](2026-09-07-release-ingest-audit.md) | P1 | Task-definition rewriting replaces sidecar images and starts from unselected revisions |
| [REL-09](2026-09-07-release-ingest-audit.md) | P1 | Rebuild deletes the previous dataset before validating its target/prerequisites |
| [REL-10](2026-09-07-release-ingest-audit.md) | P1 | Checkout refresh can erase data without establishing ownership |
| [REL-11](2026-09-07-release-ingest-audit.md) | P1 | Registrant dry-run accepts invalid CSV structure |
| [REL-12](2026-09-07-release-ingest-audit.md) | P1 | Username allocation loops on a maximum-length collision |
| [REL-13](2026-09-07-release-ingest-audit.md) | P1 | Local event rehearsal duplicates and omits production orchestration |
| [REL-14](2026-09-07-release-ingest-audit.md) | P1 | Dataset gate checks media descriptors without proving bytes are usable |
| [REL-15](2026-09-07-release-ingest-audit.md) | P1 | Dataset expectations are frozen in code and miss shared curricula |
| [REL-16](2026-09-07-release-ingest-audit.md) | P1 | Legacy answer replacement can commit deletion before insertion |
| [REL-17](2026-09-07-release-ingest-audit.md) | P1 | Certificate matching silently conflates equal display names |
| [REL-18](2026-09-07-release-ingest-audit.md) | P1 | Editorial importers race on sequence allocation and manufacture commit identities |
| [REL-19](2026-09-07-release-ingest-audit.md) | P2 | Competing release mechanisms and stale runbooks obscure the supported path |

### Backend, authorization, and learner records

| ID | Priority | Problem to fix |
| --- | --- | --- |
| [BE-01](2026-09-07-backend-security-audit.md) | P0 | Admin/impersonation bypass the Studio authorization boundary |
| [BE-02](2026-09-07-backend-security-audit.md) | P0 | Live legacy tokens retain unscoped management authority |
| [BE-03](2026-09-07-backend-security-audit.md) | P0 | Review URL project is not bound to the review's actual project |
| [BE-04](2026-09-07-backend-security-audit.md) | P0 | Raw review choices inflate scores or break later scoring |
| [BE-05](2026-09-07-backend-security-audit.md) | P1 | Time parsers disagree and accept negative/non-finite hours |
| [BE-06](2026-09-07-backend-security-audit.md) | P1 | Invalid project input changes account/enrollment state and can crash error rendering |
| [BE-07](2026-09-07-backend-security-audit.md) | P1 | GET mutates learner records; rejected toggles create enrollment |
| [BE-08](2026-09-07-backend-security-audit.md) | P1 | Quarantine does not consistently deny existing sessions/tokens |
| [BE-09](2026-09-07-backend-security-audit.md) | P1 | Email authentication scans the entire eligible account table |
| [BE-10](2026-09-07-backend-security-audit.md) | P1 | Reaccepted opt-out reuses a permanently exhausted job |
| [BE-11](2026-09-07-backend-security-audit.md) | P1 | Old worker completion deletes a newer opt-out choice |
| [BE-12](2026-09-07-backend-security-audit.md) | P1 | Logical learner uniqueness and vote budgets lack concurrency enforcement |
| [BE-13](2026-09-07-backend-security-audit.md) | P0 | Logical EmailDelivery is absent and legacy send paths remain live |
| [BE-14](2026-09-07-backend-security-audit.md) | P1 | Compatibility JSON shape/type errors become server errors |
| [BE-15](2026-09-07-backend-security-audit.md) | P1 | Newsletter import replay and current preference authority need an explicit cutoff contract |
| [BE-16](2026-09-07-backend-security-audit.md) | P2 | Directory-wide adoption exemptions silently exempt new code from checks |
| [BE-17](2026-09-07-backend-security-audit.md) | P1 | Project results crash for non-submitters and valid no-review fallback scores |

### Architecture, public content, and duplication

| ID | Priority | Problem to fix |
| --- | --- | --- |
| [ARC-01](2026-09-07-architecture-duplicates-audit.md) | P1 | Catalogue caches retain transient failure and can mismatch release keys/rows |
| [ARC-02](2026-09-07-architecture-duplicates-audit.md) | P1 | Docs search and FAQ reference maps stay stale after activation/rollback |
| [ARC-03](2026-09-07-architecture-duplicates-audit.md) | P1 | A historical biography marker total breaks valid public content changes |
| [ARC-04](2026-09-07-architecture-duplicates-audit.md) | P1 | Empty podcast/docs hubs violate their documented empty-state contract |
| [ARC-05](2026-09-07-architecture-duplicates-audit.md) | P1 | FAQ images bypass database publication and ordering remains code-owned |
| [ARC-06](2026-09-07-architecture-duplicates-audit.md) | P0 | Shared assets lack an implemented serving/storage contract |
| [ARC-07](2026-09-07-architecture-duplicates-audit.md) | P1 | Competing Markdown rewriters disagree and process code examples as assets |
| [ARC-08](2026-09-07-architecture-duplicates-audit.md) | P1 | Shared lesson moves/renames do not enforce stable identity and aliases |
| [ARC-09](2026-09-07-architecture-duplicates-audit.md) | P1 | Retired curriculum positions grow exponentially across imports |
| [ARC-10](2026-09-07-architecture-duplicates-audit.md) | P1 | Course count joins multiply child tables and card reads repeat queries |
| [ARC-11](2026-09-07-architecture-duplicates-audit.md) | P2 | Docs detail context reloads the whole corpus repeatedly |
| [ARC-12](2026-09-07-architecture-duplicates-audit.md) | P2 | Repeated fixtures/helpers and misplaced dependencies need bounded consolidation |
| [ARC-13](2026-09-07-architecture-duplicates-audit.md) | P1 | Immutable imported lessons still use mutable branch media |
| [ARC-14](2026-09-07-architecture-duplicates-audit.md) | P2 | Every test database depends on the full staging corpus and a weak initialization sentinel |

### UX and accessibility

| ID | Priority | Problem to fix |
| --- | --- | --- |
| [UX-01](2026-09-07-ux-audit.md) | P0 | Studio inline confirmation strings compile editable metadata as script |
| [UX-02](2026-09-07-ux-audit.md) | P1 | Failed privacy-setting saves silently revert |
| [UX-03](2026-09-07-ux-audit.md) | P1 | Country combobox expanded state and keyboard selection disagree |
| [UX-04](2026-09-07-ux-audit.md) | P1 | Initial and dynamically added learning-link controls have no accessible names |
| [UX-05](2026-09-07-ux-audit.md) | P1 | Rejected Q&A submission hides existing questions and leaves a phantom |
| [UX-06](2026-09-07-ux-audit.md) | P1 | Q&A rerenders lose keyboard focus |
| [UX-07](2026-09-07-ux-audit.md) | P1 | Q&A ignores lifecycle/default-sort state and configuration updates |
| [UX-08](2026-09-07-ux-audit.md) | P1 | Failed/missing JS makes question/name fields submit into the URL |
| [UX-09](2026-09-07-ux-audit.md) | P1 | Enrollment sorting orders only the current page of results |
| [UX-10](2026-09-07-ux-audit.md) | P1 | Enrollment form errors lack the established accessible summary/associations |
| [UX-11](2026-09-07-ux-audit.md) | P2 | Local-storage failure makes a successfully saved theme appear unsaved |
| [UX-12](2026-09-07-ux-audit.md) | P2 | Studio Q&A lacks unpin despite backend support |

### Events/Q&A and management adapters

| ID | Priority | Problem to fix |
| --- | --- | --- |
| [EVT-01](2026-09-07-events-management-audit.md) | P0 | Public Q&A moderation bypasses revoked staff sessions and omits privileged audit |
| [EVT-02](2026-09-07-events-management-audit.md) | P1 | Studio/API mutations diverge on revision, retry, confirmation, and omitted-setting semantics |
| [EVT-03](2026-09-07-events-management-audit.md) | P1 | Q&A API idempotency keys collide between separately authorized principals |
| [EVT-04](2026-09-07-events-management-audit.md) | P1 | Deleted questions remain listed for moderators and can be restored |
| [EVT-05](2026-09-07-events-management-audit.md) | P1 | Contradictory room settings and malformed JSON types escape shared validation |
| [EVT-06](2026-09-07-events-management-audit.md) | P1 | Participant/IP quota scopes differ from the accepted Q&A policy |
| [EVT-07](2026-09-07-events-management-audit.md) | P1 | Unapproved null retention is accepted and cleanup remains an incomplete privacy contract |
| [EVT-08](2026-09-07-events-management-audit.md) | P1 | Public mutation adapters leave expected revision conflicts as unhandled errors |
| [EVT-09](2026-09-07-events-management-audit.md) | P2 | Public configuration inherits management-only internal IDs instead of an explicit public schema |

### CI verification and test-safety boundaries

| ID | Priority | Problem to fix |
| --- | --- | --- |
| [CI-01](2026-09-07-ci-verification-audit.md) | P1 | Gate does not join verified evidence and release selection to the same source identity |
| [CI-02](2026-09-07-ci-verification-audit.md) | P1 | Runner emits success despite source drift before/during verification |
| [CI-03](2026-09-07-ci-verification-audit.md) | P1 | Equal-second timestamps let older success hide a newer failed result |
| [CI-04](2026-09-07-ci-verification-audit.md) | P1 | Dedicated content-update workflow misses moved ingest inputs and builder code |
| [CI-05](2026-09-07-ci-verification-audit.md) | P1 | Remote mutation isolation is caller-declared rather than bound to reviewed target policy |

### Public security, source sync, and cache rollout

| ID | Priority | Problem to fix |
| --- | --- | --- |
| [PUB-01](2026-09-07-public-boundaries-audit.md) | P1 | Public aliases forward sensitive query values in cacheable redirects |
| [PUB-02](2026-09-07-public-boundaries-audit.md) | P1 | Response policy lacks fail-closed cache vetoes; prerequisite to positive caching |
| [PUB-03](2026-09-07-public-boundaries-audit.md) | P1 | Older first-time course imports can overwrite newer commits; domain ownership is unfenced |
| [PUB-04](2026-09-07-public-boundaries-audit.md) | P1 | Image admission disagrees with browser URL parsing and accepted asset destinations |
| [PUB-05](2026-09-07-public-boundaries-audit.md) | P1 | Snapshot limits omit preallocation, structural, and overall-time budgets |
| [PUB-06](2026-09-07-public-boundaries-audit.md) | P2 | Validly signed invalid UTF-8 webhook payloads raise a server error |
| [PUB-07](2026-09-07-public-boundaries-audit.md) | P1 | Activation/rollback lack durable invalidation intents; prerequisite to positive caching |

## Duplication: what to consolidate and what to leave alone

The useful cleanup targets are duplicated responsibilities with demonstrated drift. Exact textual repetition is a weaker signal. Avoid a global abstraction/refactoring task; that would make the backlog harder for simpler models to execute safely.

| Responsibility | Competing implementations | Concrete drift / owning work |
| --- | --- | --- |
| Deployment control | Active shell helpers versus retained Python controller, target registry, and CI machinery | Different recovery, evidence, task-count and verification behavior; REL-01/02/06/07/08/19 |
| Import checkpointing | Account JSON claims, history table claims, DB watermarks | Ownership/durability split causes identity corruption and skipped work; REL-03/04 |
| Event rehearsal | `prepare_local_data` versus production import sequence | Required steps differ; REL-13 |
| Editorial release creation | Public-content, FAQ, and docs import scripts | Repeated allocation/provenance logic races or creates synthetic revision values; REL-18 |
| Management authority | Studio/admin credential registry, legacy `Token`, Django admin/loginas | Policies can be bypassed through a different entry point; BE-01/02 |
| Time-spent validation | Homework helper, project parser, direct peer-review conversion | Different accepted values and failure responses; BE-05 |
| Markdown destinations | Shared importer, legacy unit assets, unit links, lesson renderer | Supported syntax/code-fence handling differs; ARC-07 |
| Content snapshot composition | Catalogue LRU, docs LRU, FAQ map LRU, repeated docs helpers | Invalidation and release consistency differ; ARC-01/02/11 |
| Action confirmation | Inline handlers in summary, detail, deadline, and enrollment templates | Syntax failures and script execution; UX-01 |
| Immediate settings saves | Account, enrollment, email and theme UI branches | Inconsistent errors and storage handling; UX-02/11 |
| Q&A state/presentation | Participant, host and Studio adapters | Lost list/focus, ignored state and missing unpin; UX-05/06/07/12 |
| Fixture construction | 19 exact AST duplicate groups, mostly adopted test helpers | Consolidate small shared data builders only when a real change needs synchronized edits; ARC-12 |

Do not merge migration-frozen constants into live content, use a checked-in projection as a public fallback, combine privileged/public test actors, rewrite adopted fixtures wholesale, or create a broad `utils.py` that hides domain ownership. Domain services own validation, transactions and business rules; presentation and orchestration should call them.

## Execution plan for smaller models

### Wave 1: independent containment tasks

Assign separate owners where files do not overlap. These can begin once groomed against the current snapshot:

1. **Release verification:** REL-01 plus CI-01. Deliver exact-candidate gate binding with offline success/failure/cancellation cases. The gate must both be required and join its independently valid inputs to the same source. Coordinate its receipt schema with REL-07.
2. **Import bookkeeping design:** REL-03/04. One owner defines run identity, mapping storage, progress transactions and recovery, then implements the account/history adapters sequentially.
3. **Grading input/ownership:** BE-03 followed by BE-04. Keep the two defects as separate acceptance lists; they touch nearby review code.
4. **Studio metadata handling:** UX-01. Remove executable interpolation across every identified template and prove accept/decline behavior with harmless adversarial text.
5. **Alternate management containment:** BE-01 plus a BE-02 consumer/route inventory and EVT-01's separate Q&A adapter. Reuse canonical staff authority; preserve room-scoped co-host behavior and authorized compatibility contracts rather than removing URLs indiscriminately.

### Wave 2: release and data preparation completeness

REL-02/06/07/08 belong to one deployment-controller owner. The fix must handle the reviewed number of web tasks plus the worker, capture prior state, retain receipts and cover failures between each step. Merely switching from shell to the old Python controller is insufficient: the retained controller has assumptions that also need review.

REL-05 removes the local-only restriction from the deployed importer while preserving explicit target authorization; it does not weaken the local seeder's guard. REL-09/10/13 make rehearsal and checkout preparation owned, recoverable and faithful to the supported pipeline. REL-11/12/16/17 are narrow importer fixes that can proceed on separate files after their contracts are groomed.

REL-14/15 and ARC-06 need a shared acceptance definition: a complete dataset includes resolvable published curriculum and available validated media bytes, not just expected row counts and rewritten URL strings. ARC-08/09 must integrate with the existing curriculum owner before any new model migration is written. ARC-07's pure Markdown-token tests can proceed independently, then integrate with managed assets.

### Wave 3: public freshness and everyday workflows

ARC-01 supplies the correct immutable-release read semantics. ARC-02 and ARC-11 consume that helper for docs/FAQ caches and request composition. ARC-03/04/05 can be delivered separately, with browser checks on changed public empty/error states. ARC-10 gets a query-shape/scaling test rather than a flaky timing threshold.

BE-06 first separates parsing from persistence and fixes error rendering; BE-05 then makes hours validation consistent. The immediate BE-06 fix does not depend on building EmailDelivery. BE-07 converts unsafe GET mutations and fixes enrollment creation order. BE-17 is an independent results-page empty-state/vote-map correction with no data migration. UX-02/03/04/09/10 each has a bounded user-visible result and can be assigned separately with shared-script ownership.

BE-08 aligns existing-session/token eligibility with quarantine; BE-09 replaces the email table scan without weakening ambiguity handling. BE-10/11 need one versioned unsubscribe-intent design and two separate race/recovery test cases. UX-05/06/07/08 should have one Q&A state owner or sequential patches, because they share DOM and polling behavior; UX-12 can follow that state contract.

### Wave 4: schema and compatibility migration

BE-12 starts with a read-only duplicate/inconsistency inventory and approved conflict rules. Add constraints only after the preflight is clean; “delete duplicates then migrate” is not an acceptable implementation instruction. Preserve historical grades, progress and certificate ownership.

BE-13 is a groomed chain: disable unsupported legacy sends, introduce logical intent/job, implement one approved Relay purpose, migrate caller groups, then prove all new legacy sends are closed. BE-15 resolves preference authority and snapshot ordering alongside that work. Retained compatibility endpoints receive BE-14 validation and the BE-02 credential policy. REL-18 reuses the current content-release service and keeps its existing stale-base rejection intact.

After behavior is fixed, REL-19 updates the actual runbooks; ARC-12/14 and BE-16 remove bounded maintenance/test debt. ARC-13 uses the completed managed-asset contract. UX-11 is a small independent storage-failure correction.

### Supplemental source/cache work

PUB-01 and PUB-06 are small independent request-boundary fixes. PUB-04 first rejects plainly unsafe URL forms without waiting for the shared-asset migration. PUB-03 needs one source-generation/lease apply contract before changing curriculum import; PUB-05 can independently bound fetch/archive resources and later use that ownership interface.

PUB-02 and PUB-07 are explicit prerequisites to positive shared caching. The inspected development policy keeps edge TTLs at zero, so do not describe those gaps as a reproduced deployed cache leak. Implement the response registry/veto and transactional invalidation intent while caching stays disabled; provider integration and rollout require their own authority and evidence. ARC-01/02 concern separate in-process stale reads and remain actionable now.

### Supplemental verification work

CI-01/02/03 share evidence contracts: integrate them serially or give one owner the identity-join, source-freeze, and ambiguous-history interfaces. Their acceptance tests remain separate. CI-04 is a narrow independent workflow/path-contract patch. CI-05 first needs a reviewed marker-by-host isolation decision; no audit finding authorizes a live mutation to prove safety. The documented opaque/native-subprocess network limitation and existing compressed-trace scanning are recorded as non-findings, not inflated into new security defects.

### Supplemental Q&A server work

After EVT-01 containment, implement EVT-03's actor-safe idempotency interface before EVT-02's paired Studio/API command contract. Coordinate EVT-08 safe revision-error responses with that interface and UX-05/07 client recovery. EVT-04/05 are bounded lifecycle/validation fixes; EVT-06 fixes quota identity without pretending anonymous cookies are permanent identities. EVT-07 requires privacy-owner approval before destructive retention behavior; reject unapproved null configuration separately. EVT-09 is a narrow DTO/spec correction, not an assertion that a UUID is a secret. Keep these server fixes distinct from the browser-state packages, but give shared files one owner.

### Task template

Give each implementing model one small task card, not the complete report bundle. A useful prompt is:

```text
Implement audit finding <ID> from _docs/audits/2026-09-07-<report>.md.
Read AGENTS.md, _docs/PROCESS.md, the groomed issue, and the linked specs.
Recheck the finding against current code; the audit included concurrent work.

Ownership: <exact modules/templates/tests>. Preserve other agents' edits.
Prerequisites already complete: <IDs / concrete interfaces>.
Required result: <one behavior change>.
Non-goals: <adjacent migrations/refactors that are not part of this task>.

First reproduce the bad behavior using synthetic data in the owning test suite.
Then change the assertion to the desired contract and implement the smallest
complete fix. Existing scratch probes assert defects; do not copy their expected
bad result into permanent regression tests.

Use uv/Make; put scratch in .tmp; no real registration data, secrets, live mail,
or production writes. Do not delete/repair historical records speculatively.
Run the relevant tests and required verification plan. Hand off the uncommitted
change, evidence, and remaining decisions through the repository process.
```

For BE-03, for example, the finite deliverable is binding the review lookup to the resolved project, reviewer and evaluated submission, then proving that a completed review cannot be changed under an open project's URL. It does not require refactoring every course view, adding a new authentication framework, or changing review scoring. BE-04 owns score-value validation.

### Completion evidence required from implementers

Each handoff should state the finding ID, current base/head and dirty-tree assumptions, changed files, exact reproduced failure, the now-passing adversarial test, relevant baseline results, verification-plan artifacts/dispositions, and any schema/data decision still outstanding. Browser-affecting work needs the independent tester's inspected desktop/mobile evidence under the applicable process tier. Import/release work needs fault injection at the failing boundary; success-path string assertions are inadequate.

For local tests, use test settings and an owned synthetic database. A typical focused command is:

```bash
PUBLIC_MEDIA_STORE_BACKEND=memory DTC_TEST_RUN_ID=<unique-audit-fix-run> \
  DJANGO_SETTINGS_MODULE=website.settings.test \
  uv run --frozen python manage.py test <owning_test_module> --noinput
```

Replace placeholders with the task's actual values. Browser tiers and verification-plan generation come from [PROCESS.md](../PROCESS.md) and [_docs/ci/change-selective-ci.md](../ci/change-selective-ci.md); this audit did not generate an implementation acceptance plan or satisfy tester/PM gates for future fixes.

## Verification ledger

Do not sum these rows into a unique-test total: baseline tests and diagnostic probes overlap between runs. Diagnostic suites intentionally assert existing defects. Failed static checks and corrected intermediate probe fixtures are recorded explicitly rather than counted as successful application verification.

| Audit surface | Executed check | Result / what it establishes |
| --- | --- | --- |
| Project configuration | Django `manage.py check` using owned test settings | No issues, 0 silenced; configuration baseline only |
| Initial code-quality snapshot | Ruff default scope plus selected integration/prod files | 9 findings in pre-existing curriculum WIP and minimal/generated notebook fixtures; no auto-fix |
| Release contracts | `uv run --frozen python -m unittest core.tests.test_cmp_style_deployment` | 5 passed despite the workflow/recovery gaps |
| Release/prod tool style | Ruff on `deploy`, `scripts/prod`, preparation and dataset verification | All checks passed in that scope |
| Release/ingest focused suite plus initial probes | Six selected files including scratch reproductions | 69 passed, 1,046 subtests passed; 53.47 seconds |
| Expanded release/ingest diagnostic probes | Six synthetic scenarios | 6 passed; 44.93 seconds; demonstrates defects, not fixes |
| Backend final combined run | 15 synthetic probes plus three existing test modules | 29 passed, 3 subtests passed; 40.48 seconds |
| Project-result completion probes | Two routed synthetic defects plus the existing results test | 3 passed; 27.43 seconds; both legitimate empty/no-review states currently crash |
| Public boundary baseline and probes | Webhook/snapshot and security helper/body modules; synthetic headers/HTML/archive/UTF-8 cases | 18 baseline tests passed; 0.020 seconds; bounded diagnostics and static gaps separately labeled |
| CI/evidence/test-safety probes | Eight isolated Git/artifact/authorization/path-filter cases | 8 passed; 1.60 seconds; includes one documented network-guard limitation, not an additional defect |
| Events/management focused suite | Initial nine probes plus Q&A/operation/bulk tests | 22 passed, 4 subtests passed; 29.86 seconds |
| Events/management final probes | Twelve synthetic cases including genuine-CSRF revoked-session moderation and positive ownership controls | 12 passed; 27.43 seconds; revision-conflict case fault-injected, not a measured distributed race |
| Catalogue/FAQ baseline | Two existing Django test modules | 24 passed; 34.439 seconds; local-media hydration warning disclosed |
| Architecture diagnostics | Synthetic cache, marker, route, URL, query and position probes | Failures demonstrated as described; no production benchmark or PostgreSQL race claimed |
| Routed browser smoke | Existing accessibility core smoke | 2 passed; 154.15 seconds, desktop/mobile surfaces |
| UX components | Actual JS and Django-rendered attributes in isolated Chromium | Confirmed handler execution, state/focus/error defects; mocked requests |
| Rendered evidence | Two homepage screenshots and one labeled synthetic Q&A screenshot | All three inspected; limitations and paths in UX report |
| Independent audit review | Architecture agent reviewed backend high-risk conclusions | Corrected wording about existing transaction/CSRF guards and separated immediate fixes from mail dependencies |
| Final configured lint/type checks | `make lint`; `make typecheck` | Failed: 6 lint findings and 5 type errors in concurrent curriculum WIP; typecheck examined 546 source files; no auto-fix |
| Schema/portability baseline | `make migrations-check database-portability-check` using owned test settings | Passed: no changes detected; database portability passed |
| Report integrity | Local Markdown links, heading targets, finding IDs/priorities, code fences, and whitespace | Nine reports and 120 local links validated; all 83 directory entries match their owning reports; report whitespace checks and `git diff --check` passed |

Exact commands and scratch paths are in each companion report. The backend final command ran:

```bash
PUBLIC_MEDIA_STORE_BACKEND=memory DTC_TEST_RUN_ID=audit-backend-complete-20260907 \
  uv run --frozen pytest .tmp/audit-20260907/test_backend_reproductions.py \
  courses/tests/test_project_criteria_integrity.py \
  email_app/tests/test_unsubscribe_replay.py \
  core/tests/test_course_platform_policy.py -q --tb=short
```

The audit did not run the full application test suite, all browser engines, a live vulnerability scan, deployment smoke, provider sends, production database checks, or actual destructive/recovery actions. It did run the repository's configured full `make typecheck`; that target has documented adoption exclusions and failed as recorded above. Passing focused checks does not certify application health; several probes intentionally show missing coverage in otherwise green tests. The [coverage ledger](2026-09-07-audit-coverage.md) maps every requested outcome to its deliverable and records remaining limits.

## Guardrails against incorrect fixes

- Preserve database-only public publication. A failed database read must not activate a Python/JSON/file fallback.
- Bind ingestion bookkeeping to both immutable input and the actual target database, and make mappings/progress atomic. Unique filenames alone are insufficient.
- Keep reviewed source identities, learner progress, certificate associations, aliases, and existing grading records intact. Inventory conflicts before adding constraints or applying repairs.
- Treat a restored image and a restored database as different operations. Use compatible migrations and explicit recovery receipts; do not automatically reverse data migrations during deployment failure.
- Preserve current protections that already work: question/export staff checks, strict new management credentials, CSRF on impersonation entry, the peer-review save transaction, content stale-base rejection, and durable-job state/fencing constraints.
- Do not claim a skipped or unavailable live test passed. State code-level versus browser-component versus deployed evidence separately.
- Avoid broad dependency installation, full-tree formatting, feature redesign, new content fallbacks, or removal of historical compatibility artifacts as incidental cleanup.

The requested audit deliverable is complete: this reviewable backlog, seven detailed specialist reports, and the coverage/evidence ledger are saved and internally validated. Application fixes remain unimplemented and should be selected, groomed, built, and independently verified through the repository's normal lifecycle. Audit completion is not release approval.
