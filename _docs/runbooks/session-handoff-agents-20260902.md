# Agent status at handoff — 2026-09-02

Companion to `session-handoff-20260902.md`. This records what every agent was doing, how
far it got, and which files it owns, so work can resume without collisions.

**Read this before touching `.tmp/production-prep`.** At handoff that worktree had 39
uncommitted files from several agents at once, and the index was shared. Two agents had
already been harmed by the contention: one stood down rather than commit into a dirty
index, and another could not attribute its test results because files changed underneath
it mid-run.

## First actions for whoever picks this up

1. `git -C .tmp/production-prep status` and reconcile the 39 uncommitted files against the
   ownership table below before changing anything.
2. Commit finished work by explicit path. Never `git commit -a` or `git add -A` — a media
   lane had ~1,254 deletions staged in the shared index during this session.
3. Restart the dev server (it runs `--noreload`) so the landed fixes are visible.

## In-process agents (Claude)

Claude agents do not survive the session. Anything marked *running* was interrupted; its
uncommitted work is in the worktree and the task must be re-dispatched — to **Codex or
Grok**, per the owner's instruction that new work goes to those two.

| Task | State | Owns |
| --- | --- | --- |
| #303 stale podwiki pins | **Done**, committed `41269f8` | `ci/tests/test_content_update.py` |
| Blog cover image removal | **Done**, committed `f432035` | `templates/public/article_detail.html`, `content/article_content.py` |
| #301 media store port | **Done**, committed `61be78b`, `6bf0954` | `content/media_store.py`, `content/media_tooling.py`, management commands |
| Production-like dataset rebuild | **Done**, committed `6d40253` | `scripts/build_course_modules_manifest.py`, `scripts/verify_local_dataset.py` |
| Database rebuild from scratch | **Done**, nothing to commit | verified the documented path works; see main handoff |
| Events projection refresh | **Stopped deliberately**, committed `23f8954` only | `scripts/build_public_projection.py`. Blocked on three owner decisions — see main handoff. Do not resume until they are answered |
| Migration runbook | **Done**, committed `47703e7` | `_docs/runbooks/production-hosting-and-dns-migration.md` (2,050 lines) |
| False homepage claims + category pills | **Stood down without committing** | Work is live and uncommitted in `core/home_content.py`, `core/views.py`, `templates/core/home.html`, `core/tests/test_homepage.py`, `courses/views/course_list.py`, `_docs/adoption/course-platform/integration-patched-files.tsv`. It refused to commit because another agent was rewriting the same functions. **Its corrected copy must not be lost** — see below |
| #307 DB course reads | **Running when interrupted** | `core/home_content.py`, `core/views.py`, `content/review_views.py`, `courses/services/public_course_catalog.py`, migration `0052_merge_duplicate_course_families.py`. Was told to absorb and commit the stood-down agent's work with its own |
| #308 duplicate family merge | **Running when interrupted** | `courses/course_family_catalog.py`, `courses/services/curriculum_import.py`, migration `0052`. Note a from-scratch rebuild does **not** reproduce the split |
| Raw-HTML sanitizer fix | **Running when interrupted** | `courses/registration.py`, the unit render path, `courses/templates/courses/unit.html`. Contested with Codex — see below |
| Breadcrumb redesign | **Running when interrupted** | the shared breadcrumb partial, the h1/subtitle block. Owner's exact design is in the main handoff |
| Media publish to S3 | **Running when interrupted** | Had write access. Bucket `dtc-website-media` was still empty at handoff; publish and `public_media_verify` (expects 1253/1253) still to run |
| #301 independent tester | **Running when interrupted** | Read-only in `.tmp/issue-301-engineer`, which still holds 1,278 uncommitted files |
| Gate audit on `production-prep` | **Running when interrupted** | Read-only |
| Unit page design (Fable) | **Done** | Spec at `.tmp/design-spec-unit-page.md` |
| Homework page design (Fable) | **Done** | Spec at `.tmp/homework-page-design-spec.md` |
| Course content pipeline audit | **Done** | Analysis only; findings in the main handoff |
| Projection-read audit | **Done** | Analysis only; fed #307 |
| Worktree triage | **Done** | 20.7 GB reclaimable across 37 worktrees, but agents were active in some. Nothing deleted |

### The stood-down agent's work must be preserved

Its corrected copy is uncommitted and easy to lose:

- `FEATURED_BUILD_ITEMS` replaced with four items traced to
  `content/docs_projection.json` (`DataTalksClub/docs@3f23e006`), replacing fabricated
  claims about multi-agent systems and RAG evaluation.
- `FEATURED_GROUP_NOTE = "small groups of 6–8 people"` deleted entirely — the owner says
  it is untrue — along with its `.build-note` element, CSS, and context key.
- Category pills removed from catalogue cards; `COURSE_FAMILIES` is now two-valued and the
  `integration-patched-files.tsv` ledger row was updated to match, which is mandatory or
  `core.tests.test_course_platform_adoption` fails.
- Two new test classes in `core/tests/test_homepage.py` that pin each bullet to a verbatim
  phrase in the curriculum source, so invented copy fails the build. Fix them to work with
  the DB-backed reads rather than deleting them.

It also reported that `FEATURED_COHORT_SUMMARY` still says "Over four modules" while the
curriculum says **six modules plus a final project** — the same defect class, reintroduced.
And it listed further unsubstantiated homepage claims: `"130,000 people building with you"`
is hardcoded with no source, and `MEMBER_STORIES` carries a code comment saying the six
named people have not consented to homepage use and it must not ship to production.

## External CLI agents (Codex and Grok)

These run as background shell processes and were killed with the session. None had produced
output at handoff, so **all four must be re-run.** Invocation is documented in
`_docs/runbooks/external-model-agents.md`.

| Agent | Task | Output it was told to write |
| --- | --- | --- |
| Grok | Learning-flow UX across course, module, unit and homework pages | `.tmp/ux-learning-flow-grok.md` |
| Grok | Fix the CMP data import so real questions, homework and projects arrive instead of placeholders | `.tmp/cmp-import-fix-grok.md` |
| Codex | Map CMP's 46-table schema onto the new layout, with consolidation rules | `.tmp/cmp-schema-mapping-codex.md` |
| Codex | Four pipeline fixes: unreachable-commit guard, lesson video/code persistence, remove dead `_VIDEO_LINE_RE`, add `img` to the sanitizer allowlist | committed directly |

The schema map is upstream of the import work. Run it first, so the importer lands on a
written mapping rather than reinventing how `ai-dev-tools-zoomcamp-2026` becomes
`ai-dev-tools` + `2026` — that divergence is exactly what produced the duplicate family.

## Known contention

- `courses/registration.py` — the sanitizer agent and Codex's fix 4 both change
  `ALLOWED_MARKDOWN_TAGS`. Reconcile before committing either.
- `courses/templates/courses/unit.html` — the sanitizer agent, the breadcrumb agent, and
  both design specs all touch it. Sequence rather than parallelise.
- `core/home_content.py`, `core/views.py`, `templates/core/home.html` — #307 was made sole
  owner after the collision described above.
- `courses/course_family_catalog.py` — #308 owns it; the projection-builder commit
  `23f8954` imports from it, and was compatible at handoff.
- Migration `0052_merge_duplicate_course_families.py` — appears in both the #307 and #308
  lanes. Confirm there is only one.

## Other worktrees

- `.tmp/issue-301-engineer` — 1,278 uncommitted files, the #301 implementation, was under
  independent test. Do not delete.
- `.tmp/production-prep` — the working branch. Do not delete.
- Roughly 60 further worktrees under `.tmp/`, about 20.7 GB reclaimable. A triage
  classified them but nothing was deleted, and agents were active in several. Re-verify
  before removing anything.
