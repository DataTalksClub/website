# Development process

This lifecycle was adapted from the proven AI Shipping Labs workflow. DataTalks.Club specifications in `_docs/specs/` are authoritative; no source-project product rules carry over.

## Issue lifecycle

```text
User intake -> orchestrator files issue -> PM grooms -> engineer builds
            -> tester verifies -> PM accepts -> local merge/push -> on-call observes CI
```

All product work is tracked in GitHub Issues for `DataTalksClub/website`. There are no project boards and no pull requests.

1. The orchestrator records raw user intake as an issue labelled `needs grooming` plus an area and priority when known. It does not turn raw intake into an implementation spec itself.
2. The product manager researches the repository and specifications, then rewrites the issue with scope, explicit non-goals, dependencies, testable acceptance criteria, and browser scenarios. It removes `needs grooming`.
3. The software engineer implements the groomed issue and tests locally. The engineer does not commit or push.
4. A separate tester reviews the uncommitted diff, runs focused Django and core Playwright checks, checks every acceptance criterion, and captures and inspects screenshots of changed pages.
5. The product manager performs final acceptance from the user's perspective after technical verification passes.
6. After both gates pass, the engineer creates a focused commit whose body contains `Closes #N` (or `Refs #N` for an issue intentionally left open for human verification).
7. The orchestrator merges the approved branch into local `main` with `--no-ff`, pushes `main`, and dispatches on-call. No GitHub pull request is created.
8. On-call alone observes the resulting CI run and reports green, or reopens/traces/fixes a failure through the same issue.

Every issue goes through every applicable stage. A failed tester or PM gate returns to the engineer and repeats until it passes. Criteria marked `[HUMAN]` are reported separately; they do not permit skipping automated criteria.

## Roles

| Role | Guidance | Responsibility |
| --- | --- | --- |
| Product manager | `.claude/agents/product-manager.md` | Grooming and final user acceptance |
| Software engineer | `.claude/agents/software-engineer.md` | Implementation and tests, initially uncommitted |
| Tester | `.claude/agents/tester.md` | Independent technical verification and screenshots |
| On-call engineer | `.claude/agents/oncall-engineer.md` | Sole post-push CI observer |

The orchestrator coordinates the handoffs and must not silently collapse independent review roles into its own review.

## Issue format and labels

A groomed issue contains:

- normative specification links;
- scope and explicit non-goals;
- dependencies and blocked work;
- concrete acceptance criteria using checkboxes;
- Django/integration, browser, and repository/operations scenarios as applicable.

Workflow label:

- `needs grooming`: raw intake waiting for a PM specification.

Priorities:

- `P0`: must-have or release-blocking;
- `P1`: important follow-up;
- `P2`: nice-to-have or later;
- `human`: automated work passed but named manual verification remains;
- `decision`: owner decision is required.

Area labels are `foundation`, `auth`, `admin`, `frontend`, `content`, `courses`, `events`, `email`, `seo`, `infra`, `integration`, `accessibility`, `security`, `operations`, `testing`, and `data-migration`. Use `bug`, `enhancement`, or `documentation` when applicable.

Only check an acceptance checkbox when its implementation exists and the owning role has run the relevant evidence. Engineers check implemented criteria; testers independently leave failures unchecked.

## Selecting work and dependencies

- Do not implement an issue labelled `needs grooming`.
- Select the lowest-numbered unblocked groomed issue at the highest applicable priority unless the product owner directs otherwise.
- A `Depends on` relationship is valid only when another issue supplies a model, interface, decision, or infrastructure prerequisite. Related work is not automatically a dependency.
- Do not start while a required dependency is open. Independent issues may proceed concurrently in isolated worktrees.
- New Python packages require a concrete use in the current issue, compatibility with Python 3.13 and Django 6, a bounded constraint in `pyproject.toml`, and an updated `uv.lock`. Prefer the approved architecture baseline and standard-library or Django capabilities over speculative dependencies.

## Parallel delivery and waiting

The orchestrator keeps the delivery pipeline moving; it does not act as a second watcher for work
already owned by another role.

- Give each active issue or gate one named owner and one isolated worktree when files may change.
  Do not assign two agents to make overlapping edits.
- While an engineer, tester, PM, on-call engineer, or external workflow is running, use available
  capacity for independent implementation, grooming, bounded research, or preparation of the next
  unblocked issue. A downstream change that depends on the pending result may be inspected and
  planned, but it is not rebased, committed, or merged early.
- Waiting is delegated to the role that owns the gate. The orchestrator must not shadow-poll CI,
  deployments, or an agent that has already promised a checkpoint. It resumes coordination when
  the owner reports a meaningful transition or misses an agreed checkpoint.
- User updates are event-driven: report a gate starting, passing, failing, deploying, becoming
  blocked, or reaching live acceptance. Do not spend turns or tokens narrating unchanged waiting
  state.
- Reuse valid evidence when a follow-up cannot affect it. Retest the changed contract and its
  security/regression boundary; do not rerun broad suites or browser matrices solely to occupy an
  agent. Required independent tester and PM gates still apply.
- If every remaining task truly depends on one external result, record that dependency once and
  let the owning watcher wait. Do not create speculative work, duplicate runs, or repeated status
  checks to simulate progress.

Parallelism improves elapsed time, not scope. It never permits skipping acceptance criteria,
working around a dependency, merging before the independent gates pass, or hiding a red pipeline.

## Engineering and verification gates

- Read the issue and every linked specification before changing code.
- Use `uv` and the repository Make targets. Do not install project dependencies with `pip`.
- Implement only the issue scope and preserve unrelated worktree changes.
- Add migrations for every model change and run the migration-drift check.
- Add focused automated tests for every changed contract.
- Template work must be verified at desktop and mobile sizes. The tester stores screenshots below `.tmp/screenshots/`, reads each image, and reports whether it contains the expected page rather than an error, debug page, or broken layout.
- The tester runs focused Django tests plus `make test-playwright-core`. Broader checks are added when shared infrastructure or fixtures change.
- The PM accepts only after the tester passes, and evaluates navigation, copy, empty/error states, safe denials, and consistency with the issue.
- Agents post their own role reports to the issue.

## Temporary files and sensitive data

All temporary files belong under the gitignored project-local `.tmp/` directory. This includes screenshots, browser profiles, downloaded fixtures, generated previews, command output, and scratch data.

- Never use a system temporary directory or a path outside the repository for task artifacts.
- Never commit `.env`, SQLite databases, Playwright state, screenshots, tokens, or production exports.
- Redact credentials, personal data, cookies, and authorization headers from issue comments and logs.

Evergreen documentation lives directly under `_docs/` or a named subject directory. Point-in-time audits and investigations belong in `_docs/audits/` using `YYYY-MM-DD-topic.md`.

## Local merge convention: no pull requests

After tester pass and PM acceptance, the engineer creates a commit with a short imperative subject and an issue reference in the body:

```text
Bootstrap the Django foundation

Closes #1
```

The orchestrator then, from a clean and current main checkout:

```bash
git fetch origin
git merge --no-ff <approved-branch> -m "Merge <approved-branch>: <subject> (#N)"
git push origin main
```

Do not run `gh pr create` or `gh pr merge`. The independent tester and PM gates are the review process. If a criterion needs later human verification, use `Refs #N`, add the `human` label, list the exact remaining checks, and leave the issue open.

## CI and on-call

On-call is the only role that waits for post-push CI. Green means all required jobs passed. A red pipeline is investigated, attributed to the introducing issue, and fixed; it is never dismissed as an acceptable pre-existing failure. A cancellation or missing verdict is reported as unresolved, not green.

The orchestrator continues grooming, implementation coordination, or other independent work while
on-call observes CI. It returns to the release only when on-call reports a meaningful transition.
