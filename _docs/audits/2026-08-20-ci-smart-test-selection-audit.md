# CI smart test-selection audit — 2026-08-20

This note audits the homegrown selective-testing system (classifier, ownership graph, focused Django runner, browser profiles, evidence reuse) for correctness, reliability, and speed. It is research and recommendations only; no CI, `ci/`, or Makefile code was changed. Each punch-list item below is scoped to roughly one PR and is intended to become one GitHub issue.

## What the current system already does well

Do not rebuild this. The design is documented in `_docs/ci/change-selective-ci.md` and is unusually rigorous:

- **Fail-closed everywhere.** Unknown paths, malformed/rename-heavy diffs, symlinks, zero/invalid SHAs, non-ancestor bases, cross-application changes, migrations, shared apps (`accounts/`, `core/`), templates/static/HTML, config, and CI-policy files all force the `full` profile (`ci/selection.py:305-372`, `ci/classifier.py:41-107`). A brand-new top-level directory falls to `unknown_path` → full (`ci/selection.py:289-295,148-154`). The system rots toward slowness, never toward missed coverage of its own paths.
- **Validated, single-owner impact graph.** `ci/ownership.json` is schema-checked for duplicate/nested prefixes, dangling edges, cycles, and empty closures (`ci/ownership.py:73-307`), with regression tests pinning the reviewed closures (`ci/tests/test_selection.py:27`, `tests_ci/test_ownership.py`).
- **Tamper-resistant evidence/provenance.** Selection artifacts are digest-bound per run/attempt with no cross-run fallback; reuse requires exact input/environment/policy digests and unexpired trusted evidence (`_docs/ci/change-selective-ci.md:105-121`).
- **Scheduled full-regression backstop** every 4 UTC hours (`_docs/ci/change-selective-ci.md:160-174`) bounds the worst case of any selection mistake to a few hours. This backstop is what makes the more aggressive selection proposed below defensible.
- **Per-run explanation.** Profile and reason are written to the Actions step summary (`ci/selection.py:268-286`) and the plan summary, so a contributor can see why a push ran full.
- **Always-run baseline.** Regardless of profile, every push runs the quality contract — `terminology-check`, `database-portability-check`, `security-check`, lint, format, typecheck, `migrations-check`, `django-check`, `deployment-check`, `test-ci` (`ci/quality_contract.py:13-24`) — plus the CI-orchestration tests (`make test-ci`, ~546 tests), the browser component (core profile or validated reuse), and the container build (`ci/gate.py:26-34`). Boot-level breakage (bad import, settings error, missing migration) is caught on every push even under `focused`.

## Punch list

Priorities: **P0** reliability-blocking, **P1** correctness/reliability, **P2** speed/maintainability, **P3** nice-to-have.

### 1. [P0] Time-bound the Playwright CI job and its tests (the #204 hang)

**Problem.** The Playwright suite can hang and there is no bound anywhere on the CI path:

- The `playwright` job in `.github/workflows/ci.yml` (lines 716-802) has **no `timeout-minutes`**; the only job-level timeout in the whole workflow is `deploy` (line 1706). GitHub's default job timeout is 360 minutes.
- `test-playwright` / `test-playwright-core` (`Makefile:288-298`) run pytest with no per-test timeout; `pyproject.toml` has no `pytest-timeout` dependency and `addopts` (line 93) sets none.
- The workflow-wide `concurrency.group: website-development-release` with `cancel-in-progress: false` (`ci.yml:60-62`) serializes **all** runs, so one hung Playwright job queues every subsequent push for up to 6 hours.

Live evidence: on [#204](https://github.com/DataTalksClub/website/issues/204#issuecomment-5347851967) the required Playwright component selected 220 tests, passed 11, then hung at 5% with no output for ~19 minutes until the tester killed it. Run 32283673588 was cancelled at the Playwright gate, so promotion was skipped and the live site stayed on the `a9edeea` rollback (`_docs/audits/2026-08-19-backlog-agent-handoff.md`, "#204 — current blocker" and "Why the live site shows the old version"). The #202 tester and the #65 tester (interrupted at 144/195) hit the same wall.

**Relationship to #199.** The accepted #199 candidate (`.tmp/website-199-engineer`, commit `175ad3b`, "Bound verification runner component execution with a wall-clock timeout") bounds only the **local** `ci/runner.py` component execution, default 3600 s. It does not touch `.github/workflows/ci.yml` or the Makefile pytest invocations, so after #199 merges the GitHub Actions exposure is unchanged, and local testers still sit through up to 60 silent minutes per hung component. #199 is necessary but not sufficient; this item is the remaining CI-side half.

**Fix (one PR).**
- Add `timeout-minutes` to `playwright` (e.g. 30), and while there to `django`, `quality`, `screenshots`, and `classification` (generous bounds sized from recent green-run durations).
- Add a per-test timeout to the Playwright targets: either add `pytest-timeout` (e.g. `--timeout=120 --timeout-method=thread`) or, dependency-free, `--faulthandler-timeout=120` so a hang at least dumps the stack of the wedged test. Ensure the failure output names the hung test so item 2 can act on it.
- Verify the evidence recorder still produces a valid `timed_out`/failure envelope when pytest is killed mid-run (partial `playwright-output.log` must not validate as success — spot-check `ci/evidence.py` count parsing).

### 2. [P0] Root-cause the actual Playwright hang

**Problem.** Item 1 converts a 6-hour hang into a bounded failure, but the suite still cannot pass. Three independent runs hung at different points (11/220 on #204, 144/195 on #65, hang on #202's gate), which suggests an environmental/harness deadlock (live-server thread + `DJANGO_ALLOW_ASYNC_UNSAFE=true`, or a `page.goto` against a dead server) rather than one bad test.

**Fix (one PR, investigation first).** Reproduce `make test-playwright` under `timeout(1)` with `--faulthandler-timeout` locally; identify the wedged test/fixture from the stack dump; fix the harness (e.g. server-fixture readiness/teardown, per-navigation timeouts). File the findings on the issue even if the fix lands separately. Do not paper over it with retries until the deadlock is understood.

### 3. [P1] Ownership closures miss real import dependencies — five concrete false-negative windows

**Problem.** The classifier accounts for transitive impact only through the manually declared `downstream` edges in `ci/ownership.json`; nothing checks those edges against the actual import graph. Cross-referencing reverse imports today finds five gaps where a `focused` run skips tests of an app that imports the changed app:

| Changed app (owner node) | Declared closure misses | Evidence |
| --- | --- | --- |
| `app.jobs` (`ownership.json:64`, downstream `django.jobs` only) | `events` tests | `events/jobs.py`, `events/services.py` import `jobs`; `events/tests/` has 4 test files but **no verification node exists for it at all** |
| `app.management_api` (`ownership.json:65`) | `django.studio` | `studio/views.py` imports `management_api` |
| `app.management_auth` (`ownership.json:66`) | `django.accounts` | `accounts/views/login.py` imports `management_auth` |
| `app.review_import` (`ownership.json:67`) | `django.courses` | `courses/services/development_content_import.py` imports `review_import` |
| `app.content` (`ownership.json:61`) | `content_sync` tests | `content_sync/dtc_content/adapter.py` (and 3 siblings) import `content`; `content_sync/tests/` has 6 test files but no verification node |

These are bounded by the 4-hourly scheduled full regression, but a broken push can deploy in the window.

**Fix (one PR).** Correct the five closures; add `django.events` and `django.content_sync` verification nodes (both apps have real suites); update the pinned closure table in `ci/tests/test_selection.py:27` and `_docs/ci/change-selective-ci.md:20-31`. Then add a `tests_ci` test that computes app-level reverse imports (AST of top-level `import`/`from` statements per app package) and asserts every `app.*` closure covers every importing app that has a test label — so the closures cannot silently rot as imports evolve.

### 4. [P1] Flake policy for Playwright (quarantine or bounded retry)

**Problem.** There are no retries and no quarantine mechanism anywhere (`Makefile:288-298`, `ci.yml` playwright job). Real cost: #183 was rejected partly for "five unrelated Playwright failures" (`_docs/audits/2026-08-19-backlog-agent-handoff.md`, "Latest gate results"), and any single flake fails the aggregate gate and blocks deploy for the whole queue.

**Fix (one PR).** Pick one: (a) a `quarantine` marker excluded from `test-playwright`/`test-playwright-core`, each quarantined test requiring a tracking issue, plus a scheduled job that still runs quarantined tests non-blockingly; or (b) bounded rerun-on-failure (`pytest-rerunfailures`, max 1-2 reruns) with rerun counts surfaced. Caveat for (b): the evidence machinery parses executed/pass/fail counts from the retained pytest output (`_docs/ci/change-selective-ci.md:123-128`); reruns change those counts, so the envelope validation must be taught the rerun format or option (a) is the safer first step.

### 5. [P1] The `courses/` focused profile is likely slower than `full` — and the focused runner is serial

**Problem.** `app.courses` (the dominant app, 144 of ~380 test files) closes over 7 labels — `accounts api content.tests core courses data studio_courses` (`ci/ownership.json:62`) — roughly 285 test files, ~75% of the suite. The focused runner executes them with `manage.py test <labels>` **without `--parallel` or `--noinput`** (`ci/focused_tests.py:14-17`), while the `full` profile runs the whole suite with `--parallel` (`Makefile:166-168`). So for the app where speed matters most, `focused` plausibly takes longer than `full`. Missing `--noinput` is also a hang risk on a leftover test database (interactive prompt).

**Fix (one PR).** Add `--parallel --noinput` to `ci/focused_tests.py` and pin it in `ci/tests/test_selection.py:194` (which currently asserts the exact argument vector). Optionally, after item 3's import check exists, re-derive whether the `courses` closure can shrink.

### 6. [P2] The Playwright "core" profile is 87% of the full suite; the `smoke` tier is empty

**Problem.** `test-playwright-core` selects `-m core` (`Makefile:288-292`) — measured today: **192 of 220 tests collected**. So the "reduced" browser gate that runs on every backend-only push is nearly the whole flaky, slow suite; the core/full split buys almost nothing. Meanwhile a `smoke` marker is declared ("core availability/auth smoke checks (run first, fail fast)", `pyproject.toml:102`) but **zero local Playwright tests carry it**.

**Fix (one PR).** Define a true smoke tier (~20-30 tests: health/availability, auth, one representative page per major surface), add `make test-playwright-smoke`, and re-tier: backend-only changes (`browser_profile == "core"` today) run smoke; render-impact changes run core; template/harness changes (`surface.playwright`, `surface.templates`) run full. Most current `core` marks get demoted to `full`. The 4-hourly scheduled full regression plus item 1's timeouts keep the blast radius bounded. Update `_component_command` in `ci/verification.py:1552-1553` and the browser-profile derivation (`ci/verification.py:238-240`) accordingly.

### 7. [P2] Single source of truth: `ci/selection.py` root lists duplicate — and already contradict — the ownership graph

**Problem.** `_force_full_reason` (`ci/selection.py:305-372`) hardcodes top-level-directory lists that shadow `ci/ownership.json`, and they have drifted:

- `events` and `content_sync` are classified `testless_application` (`ci/selection.py:320-322`) although `events/tests/` has 4 test files and `content_sync/tests/` has 6. The effect is conservative (full run), but the reason is false and hides item 3's missing verification nodes.
- `cadmin/` is listed in the django component's `relevant_patterns` (`ci/ownership.json:24`) but has **no owner node** and appears in no `selection.py` list, so `cadmin/` changes fall to the generic `unknown_path` reason.
- Adding a new app currently requires coordinated edits in at least three places: `ownership.json` (node + labels), `selection.py` (root lists), and the pinned closures in `ci/tests/test_selection.py`.

**Fix (one PR).** Derive `selection.py`'s shared/testless/config root sets from `ownership.json` node metadata (risk flags / node kind) instead of literal lists; rename or remove the stale `testless_application` reason; give `cadmin/` an explicit owner node. Everything stays fail-closed; only the duplication and misleading reasons go away.

### 8. [P2] Guard-rail test for new top-level directories + selection-rate observability

**Problem.** A new top-level directory fails closed to `full` forever, silently — the safe direction, but it means the fast path degrades with repo growth and nobody is told. There is also no aggregate visibility: nobody can currently answer "what fraction of pushes ran focused, and which `reason` dominates the full fallbacks?", which is the number that should drive items 6-7.

**Fix (one PR).** (a) Add a `tests_ci` test that enumerates actual repo top-level directories and asserts each is either an owner-node prefix in `ci/ownership.json` or on an explicit reviewed "deliberately unmapped → full" list, so a new app fails CI with instructions instead of silently running full. (b) Extend the existing history machinery (`ci/history.py` already pages recent workflow runs) with a small report of profile/reason and evidence-reuse rates over the last N runs, emitted in the scheduled run's summary.

### 9. [P3] `full` Django profile double-runs the compatibility suite

**Problem.** `make test` depends on `test-compatibility` (`Makefile:166`), so a `full` django job always runs compatibility tests even when the plan set `compatibility_mode=skip`, and runs them a second time in the `quality` job when `compatibility_mode=rerun` (`ci.yml:494-500`). Pure duplication, a few minutes per full run.

**Fix (one PR).** Give the django job a `test-django-full` target without the `test-compatibility` prerequisite (keep `make test` unchanged for local use), or make the prerequisite conditional. Verify the quality contract's target expectations (`ci/quality_contract.py:13-24`) are unaffected.

### 10. [P3] Contributor-facing "why did my push run full / how do I add an app" runbook section

**Problem.** `_docs/ci/change-selective-ci.md` documents the design thoroughly but has no operational checklist. The reasons in the step summary (`unknown_path`, `configuration_or_dependency`, ...) are not explained anywhere a contributor would look, and the add-an-app procedure (ownership node, labels, closure test, selection.py list until item 7 lands) is undocumented — the doc only warns that unmapped apps "deliberately receive the unknown/full fallback" (line 37-38).

**Fix (one PR, docs only).** Add two short sections to `_docs/ci/change-selective-ci.md`: a reason glossary mapping each `reason` string to cause and remedy, and a numbered "adding a new app" checklist naming the exact files and tests to update.

## Suggested sequencing

1 and 2 first (they unblock #204/#202/#183-class work and the stuck deployment pipeline); then 3 and 5 (correctness of the fast path); 4 next (gate stability); 6-8 once the observability from 8b confirms where full-fallbacks actually come from; 9-10 anytime.
