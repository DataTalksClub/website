# Session handoff — 2026-09-02

Working branch: **`production-prep`** in the worktree `.tmp/production-prep`.
It is the future production branch. `main` is a separate lane and is currently red;
`production-prep` is green on `make test-ci` (617 passed).

A dev server runs on **http://localhost:8000** from that worktree with `--noreload`,
serving `.tmp/production-prep-current.sqlite3`. Restart it to pick up code changes:

```console
DTC_ENVIRONMENT=local \
DTC_SQLITE_PATH=.tmp/production-prep-current.sqlite3 \
DJANGO_SETTINGS_MODULE=website.settings.local \
uv run python manage.py runserver 0.0.0.0:8000 --noreload
```

## Working mode agreed this session

Fast: implement and commit directly on `production-prep`, no tester or PM gate, polish
before release. Four constraints still bind because they fail silently:

- public URLs must not change (`_docs/compatibility/generated-path-baseline.jsonl`, 2,937 rows);
- do not re-pin `content_sync/dtc_content/contract.py:32-34`;
- registration data and other PII stay out of git, logs, screenshots and issue bodies;
- in the shared worktree, commit only your own files by explicit path — never
  `git commit -a` or `git add -A`.

New work goes to **Codex** and **Grok**, not to new Claude agents. Invocations are
documented in `_docs/runbooks/external-model-agents.md`.

## Committed on `production-prep`

| Commit | What |
| --- | --- |
| `41269f8` | Content-update contract asserts count invariants instead of stale literals (#303) |
| `f432035` | Social card no longer drawn above blog article prose |
| `61be78b` | Public projection media served through a pluggable store (#301) |
| `6bf0954` | 1,254 media objects untracked from git (#301) |
| `6d40253` | One-command production-like dataset rebuild, plus the missing manifest generator |
| `23f8954` | Cohort family paths restored in the projection builder |
| `47703e7` | Full-fidelity CMP migration section and corrected erasure premise in the runbook |

At handoff there were 39 uncommitted files in the worktree from agents still running.
Check `git status` before assuming anything is finished.

## Blocked on the owner

1. **Push the module curricula.** `llm-zoomcamp@c04db93` and
   `machine-learning-zoomcamp@1aa481e` exist only on local `main`. GitHub returns 422 for
   both. Production ingests curricula through a webhook and cannot see unpushed commits,
   so those two courses cannot be served in production at all. This also causes the
   "Edit on GitHub" 404 on 72 units, and means 177 of 181 units record provenance the
   public cannot resolve. The recommended fix is to revert the `c04db93` relocation
   (a pure `git mv` that contradicts the convention in `llm-zoomcamp/cohorts/README.md`)
   and push all three repos.
2. **Eight external link literals** need a reviewed decision before the events refresh can
   run. Two are on the events that would become visible, including a Canva shortlink and a
   vendor campaign link carrying influencer UTM parameters. One (`https://index.md`) is a
   malformed upstream typo and should be fixed at source rather than allowlisted.
3. **Speaker-bio normalization rows** for nine new events — hand-author, or commission a
   generator first. There is no generator today and the existing artifact is backed by a
   449-line human checklist.
4. **Is the accent-border ban global?** It is real but written down nowhere, and the design
   documents contradict it. Occurrences: `_design_system.html:2763` (`.callout`), `:2817`
   (`.when`), `:3298` (`.prose blockquote`), `_module_rail_styles.html:47`,
   `templates/public/book_detail.html:152`, `events/static/events/qna/qna.css:21,40`.
5. **`HEAD` is un-rebuildable.** `_docs/migration-data/event-registration-sources.json` is
   modified but uncommitted with refreshed Luma facts (166 → 174 events). A rebuild from
   committed `HEAD` fails with `checksum_drift`. The refreshed export and its facts file
   must be committed together.
6. **Luma export hygiene:** `/home/alexey/tmp/luma-exporter/luma-events` has 174 `_json/`
   but 175 `descriptions/`. A superseded `.md` was left behind when its `.json` moved to
   `_superseded/`, and the bridge builder fails set-equality before it reads anything.

Decision already taken: event 361 is a **retitle**, not a replacement — keep identity 361
and its four aliases so `/events/361/…` keeps resolving.

## Ready to implement, not yet started

Two design specifications are written and waiting. Both should go to Codex or Grok, and
both touch `courses/templates/courses/unit.html`, so they should not run while the
raw-HTML sanitizer work is live in the same files.

- `.tmp/design-spec-unit-page.md` — unit page. Note prev/next navigation already exists at
  `unit.html:163-178`; the spec refines rather than adds it. The worst defect it found is a
  full-width "Mark as read" button under every rail row (ten buttons for ten lessons,
  `_module_rail.html:24-35`). Also a flow bug: the rail's sign-in `?next=` targets the
  module rather than the unit. Contrast trap: `--indigo` on `--lavender-deep` is 4.11:1 and
  fails AA.
- `.tmp/homework-page-design-spec.md` — homework page. Replaces the callout tone rail,
  gives a ten-state matrix with exact copy, unboxes the form, restores the module crumb.

## Known display defects, unassigned

From the course-content pipeline audit. Each is the same failure: the parser extracts the
data correctly and the importer or the view discards it.

- 272 relative links break — `unit_links.py:97` only rewrites `.md` targets.
- `homework.md` links break — `instructions_source_path` is parsed and never persisted.
- Duplicate heading on 103 of 105 ML units — bodies open with `## N.N Title` while
  `_LEADING_H1_RE` only strips a matching `#`.
- Module `README.md` files are never imported, so module pages have no overview.
- `Unit.rendered_html` is written at import and then re-rendered per request anyway.

Codex was mid-flight on four related fixes: a guard rejecting unreachable commits, lesson
video/code metadata persistence, removal of the dead `_VIDEO_LINE_RE`, and adding `img`
to the sanitizer allowlist (no image has ever rendered on a unit page).

## Data problems that look like design problems

- Homework questions are generated placeholders: 27 in total across every cohort, 2 on
  `homework-01`. The real CMP database has 595.
- All 32 projects are titled `Project Attempt N`; 80 of 100 homework rows read
  `Practice assignment for …`.
- `homework.description` is empty for every llm-zoomcamp 2026 homework.
- `curriculum_import.py:339` overwrites `Course.description` from the repository README
  unconditionally and runs after the seed, so three families now carry 13–19k characters of
  raw markup where they had curated text.
- No upcoming events: the newest event anywhere is 2026-08-31. `events.Event` has no date
  column at all — upcoming versus past is computed from the projection.

## Infrastructure

- **Live**: `dtc-website-media` (account 387546586013, eu-west-1) behind CloudFront
  `d3tgrbv0nfqbcz.cloudfront.net`, publisher role `dtc-website-media-publisher`. The bucket
  was empty at handoff; publishing 1,254 objects and running `public_media_verify`
  (expects 1253/1253) was in progress. This session has write access.
- **Open PR**: `DataTalksClub/aws-infra#31`, a sandbox backup vault for the Luma export.
  Not applied. Its 365-day expiry is the agent's own figure, not derived from the spec, and
  needs an owner decision.

## Issues filed this session

**#301** media to object storage (implemented, in verification) · **#302** inventory
validator red on `main` · **#303** stale podwiki pins (fixed on this branch) · **#304**
gunicorn worker timeout · **#305** Datamailer send boundary, P0, no dependencies, startable
immediately · **#306** 310 legacy `/podwiki/*` URLs would 404 at cutover · **#307** stale
course projection (groomed) · **#308** duplicate AI Dev Tools family — note a from-scratch
rebuild does **not** reproduce it, so it is a historical artifact in existing databases
rather than a live import defect.

## Corrections to earlier claims in this session

Recorded so they are not repeated:

- The unit page **does** have prev/next navigation; an earlier claim that it was absent was
  wrong.
- The accent-border violation on the homework page is `.callout` at
  `_design_system.html:2763`, not the `--bubble` rails at `:2817`/`:3298`.
- Event public_ids run **1–421 contiguously**; new ones are 422–430 (nine, not eight), and
  every one of the 421 existing manifest entries must be rewritten, not merely extended.
- Adding manifest entries **does** require a database import; `content/public_data.py:421`
  fails closed and every public page would 500 until `import_event_identity_manifest` runs.
- `.local/` was mode 755, not 700, while holding ~76,000 registration records. It has been
  tightened.
- `/courses/ai-dev-tools` was never empty; it renders the 2025 cohort only.
