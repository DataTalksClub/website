# D5.3a source and DTC policy contract

Issue: [#446](https://github.com/DataTalksClub/website/issues/446). This is a source-only proof, not a storage, reader, source-ref, or deployment cutover. The application remains on its existing community-base v0.5.10 runtime at 4c692db461f4c7cbbc2f60afaa05162987a640f6. Only the isolated test project in ci/source_policy_proof consumes immutable v0.5.21 at bb067c3d4bf66e9bd20d436ccf22d3402667da27 (published wheel SHA-256 e9a7139016aa301814790b47c7d99fdfde4a9e8bf0f596a8cee684468516bef0). Its pyproject, lock and release manifest fail closed on runtime/proof identity drift and run outside application and Django test discovery. The locked identities and local proof evidence belong in the #446 SWE report.

The [read-only writer and policy map](https://github.com/DataTalksClub/website/issues/446#issuecomment-5907768194) identifies the current single ingest/import call and field owners. The [immutable public-source inventory](https://github.com/DataTalksClub/website/issues/446#issuecomment-5907981487) inspected all six complete trees and all 22 schema-2 cohort manifests. Its source identities are repeated here for review against exact inputs. Registration in content_sync/course_repository_sources.json does not prove enabled ContentSource rows or deployed refs.

## Public source identities and declared shapes

| Source ID | Public source commit | Declared source shape |
|---|---|---|
| ai-dev-tools-zoomcamp | 49b7a73b013fb82e5e93eb189345a940b1ed24b1 | Schema 2; four root modules, 2026 explicit YAML-homework bindings, 2025 GitHub archive. |
| de-zoomcamp | 7f853724310d53efdf37a8d60f2abd0076d977d4 | Schema 2; seven modules, unpublished 2027 with seven explicit bindings, 2022–2026 archives. |
| llm-zoomcamp | c04d02f2ea7171d0be1e2b17d555c49651f2b5a3 | Schema 2; seven modules, seven 2026 bindings, 2024–2025 archives. |
| ml-zoomcamp | 5356b3b16e5c87f8f9fe0c3f8fdb2183d34b18fb | Schema 2; nine modules, nine 2026 bindings, 2021–2025 archives. |
| mlops-zoomcamp | dead66d7ec45ff70337238e97863c2324f906a74 | Schema 2; six modules, self-paced current cohort with explicit empty homework bindings, 2022–2025 archives. |
| sma-zoomcamp | dc5e150efc25960662349d7ea6c779555c5758ad | Schema 1; no structured cohort, module, or homework YAML manifests in the inspected tree. |

The complete-tree inspection found no nested module.yaml in these public snapshots. Mixed siblings, nested sections, bonus metadata, YAML homework units, partial refusal, and explicit ignores are synthetic contracts. A current-year marker does not prove publication, and historical Markdown homework does not create a current structured binding.

## Executable fixture identities

| Fixture | Base git tree | Proof |
|---|---|---|
| content_sync/tests/fixtures/course_repository/llm_zoomcamp_shared | 6e8d87d9f7c72acab71a69bac332ed88d309f6fc | Existing schema-2 flat module, current/self-paced/archive cohorts, explicit YAML homework with stable question and option IDs and an answer envelope. |
| content_sync/tests/fixtures/course_repository/llm_zoomcamp_2026 | 22067c701e820b683aa4a9b14a19337fc0c23cb6 | Existing schema-1 cohort flow with ordered module then project-01 reference. |
| ci/source_policy_proof/contract.py | #446 candidate, isolated v0.5.21 lock | Copies a fixture to the test runner's owned temporary directory, adds bounded mixed siblings, section/bonus, YAML homework unit and ignore input, converts through the tagged public API, then parses through the tagged public API. The maintained CI runner invokes it explicitly; the application runtime and Django discovery continue to use v0.5.10. |

The schema-1 project fixture proves only that the current DTC parser retains its ordered module then project-01 flow. A disposable v0.5.20 conversion reports `dropped flow` and removes that entry from the cohort manifest. The v0.5.21 regression proves that a nonempty ordered module/project flow is refused under FORMAT rule 3.8, with the cohort subtree byte-identical and unrelated course/self-paced scopes converted. Repeating conversion makes no further changes and reports the same refusal. That test fails against v0.5.20 because its report contains no refusal. The shared parsed graph still has no corresponding ordered project placement. FORMAT section 3.8 omits cohort `flow`; its optional project-module reader validates project-to-module paths but does not represent a project's position in a cohort flow. Scoped refusal protects the source bytes; project-flow adoption remains an open source/import contract gate. D5.1/#414 cannot adopt this cohort until a reviewed package or DTC-owned source representation and importer retain project identity and order, with a tagged compatible package if the shared contract changes.

The flat proof preserves current DTC route segments by authoring explicit slugs 01-lesson and 02-practice in disposable input. The original site parser preserves those numeric filename slugs implicitly; the shared format strips numeric prefixes unless a slug is explicit. Live authored sources need reviewed adaptation during D7.4, with D5.1/D5.3b preserving site route policy. The proof must never treat /courses/llm-zoomcamp/01-agentic-rag/lesson as equivalent to /courses/llm-zoomcamp/01-agentic-rag/01-lesson.

The [AI Dev Tools source snapshot](https://github.com/DataTalksClub/ai-dev-tools-zoomcamp/blob/49b7a73b013fb82e5e93eb189345a940b1ed24b1/01-ai-native-workflow/01-ai-native-developer-workflow.md) authors a cross-module next_url. DTC's courses/views/shared_course.py owns module-local previous/next; the source-contract test proves the converter accounts for the removed next_url, and the route test proves site neighbors stay inside the module. D5.1/D5.3b must record the source-link disposition and keep this current site behavior until a separate reviewed product decision changes it. The relative project link in that lesson remains an asset/link rewrite obligation, not a navigation override.

The existing archive fixture has an old cohort homework entry without a module. The package converter refuses that cohort manifest and preserves its exact bytes while converting unrelated valid course files. This is accepted partial-refusal behavior, not permission to activate an incomplete source. D5.1/D7.4 must resolve its archive disposition before live conversion. The source-only tests do not inspect enabled live sources or imported rows.

## DTC policy and field owners

| Contract | Existing DTC owner | Adoption disposition and gate |
|---|---|---|
| Source IDs, authored course/cohort/module/unit UUIDs, paths, ordering and explicit homework placement | content_sync/course_repository.py; courses/services/curriculum_source.py; courses/services/curriculum_import.py | Tagged parser/converter proves the generic graph and cohort bindings; D5.1 maps values to its one writer without guessing from filenames. |
| Publication and archive | courses/services/curriculum_import.py; courses/views/shared_course.py | Keep unpublished DE 2027 hidden, MLOps self-paced without inferred homework, and archive notice paths including leaderboard.md. D5.1 owns import state; D5.3b owns display. |
| Cohort context and access | courses/services/course_context.py; courses/views/shared_course.py | Explicit, remembered, enrolled and chooser context plus access filtering remain site policy. D5.3b retains the existing behavior. |
| Homework form, scoring and project flow | courses/services/curriculum_flow.py; courses/services/curriculum_import.py | Preserve question/option IDs and answer envelope without copying answers into this document. The current DTC modules-format flow retains ordered project-01; the v0.5.21 converter refuses that cohort scope unchanged, and the shared graph does not carry that placement. D5.1 must supply a reviewed source/import contract for the project and its cohort position before adoption. |
| Canonical URLs, authored next_url and module-local neighbors | courses/urls.py; courses/views/shared_course.py; courses/tests/test_d53a_source_routes.py | DTC routes retain explicit numeric segments and previous/next among published lessons of the same module. AI Dev Tools' authored cross-module next_url requires an explicit D5.1/D5.3b disposition and cannot silently replace the current neighbors. |
| Assets, relative links and code paths | courses/services/curriculum_import.py; courses/tests/test_shared_course_assets.py | Preserve managed asset/URL rewriting and source-relative paths. D5.1 proves importer mapping; D5.3b proves rendered links. |
| Section/bonus and nested presentation | Package curriculum parser; DTC reader not yet adopted | Synthetic source proof preserves metadata and sibling order. D5.3b/#447 owns visible placement. |

## Unresolved acceptance gates owned after D5.3a

- D5.1/#414 needs an exact enabled ContentSource database inventory and an approved disposition for public catalog fields/offerings absent from the six repositories; seed JSON and these fixtures cannot substitute for it.
- D5.1/#414 needs a target/import owner for preexisting projects, site-only course copy and schema-1 Stock Markets Analytics, plus the external scheduled-pull invocation if it is part of the active writer. The current runbook alone does not prove that scheduler exists.
- D5.1/#414 cannot adopt schema-1 project cohort placement merely because v0.5.21 refuses a lossy conversion. Existing `curriculum_flow._modules_flow` renders ordered `ProjectFlowItem`; `_shared_flow` covers modules only. Preserve project identity and ordered placement through an explicit reviewed source representation and target import contract before storage adoption. If this needs a shared package capability, implement and tag that dependency first; the current project-module reader alone does not satisfy this gate.
- D7.4 owns live authored conversion and source-ref cutover, including explicit numeric slug materialization and review of any refused archive scope.
- D5.3b/#447 and D5.3/#436 own imported-row, route, API, rendered-page, learner-link and development-deploy equivalence. The current Dev blocker can delay that acceptance; it does not stop this source-only implementation.
