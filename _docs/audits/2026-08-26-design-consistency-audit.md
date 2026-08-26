# Design consistency audit — 2026-08-26

This audit reviewed real Playwright screenshots of the site — logged-out and
logged-in, at 1440×900 and 390×844 — against the shared design system
(`_docs/design/design-5a.md`) and the prior `2026-08-20-design-surface-audit.md`.
Five independent visual reviews were run (marketing/course pages, wiki/blog/books,
docs/FAQ/auth, and the course-platform surfaces twice — the first logged-in
capture pass was found to be broken, see "Capture note" below), plus a sixth
review comparing four content-ground palette options. All findings below are
grounded in what the reviews actually saw in the rendered pixels, not in the
prior audit's claims.

Screenshots live under
`.tmp/screenshots/design-audit-20260826/` (gitignored, not part of this commit).

## Capture note (methodology, not a design finding)

The first logged-in capture pass filled the login form as a seeded student
user and submitted it, but the local sign-in form
(`accounts/views/login.py:social_login_view`) only authenticates the exact
bootstrapped "development owner" account — a regular user is always rejected.
The capture silently "succeeded" (no HTTP error) while every "logged-in" page
was actually anonymous or a redirect to `/accounts/login/`. This was caught
because the pages showed the anonymous masthead, and fixed by injecting a real
Django session cookie for the test student directly, then verifying the
authenticated `.user-menu` was present before each screenshot. All logged-in
findings below are from the corrected, verified-authenticated pass.

## Part 1 — Content-ground palette

The site currently paints every content band after a page's cream hero with a
pale lavender tint (`--lavender: #eff1fc`, `.band-lavender`). The homepage's
deliberate cream/lavender/mint/ink alternation is explicitly out of scope and
unchanged by this review.

Four options were rendered for three representative ordinary pages (a
card-grid index, a data-dense dashboard, and a table-heavy leaderboard) and
judged side by side:

| Variant | Ground colour | Courses index | Dashboard | Leaderboard | Verdict |
| --- | --- | --- | --- | --- | --- |
| a — current lavender | `#eff1fc` pale blue-purple | Mixed — cards pop, but the seam against the cream hero clashes and the tint greys the warm tan/green accents | Best of the four — the tint fills the otherwise-sparse band and gives the two panels the strongest lift | Mixed — indigo links on lavender are the softest-contrast reading of the four | Demote to an inset/accent surface, not the page ground |
| b — white ground | `#ffffff` (`--card`) | Clean but cold — two seams (cream→white→cream) and a clinical feel | Weak — the page reads sparse, panels float in empty white | Best raw legibility, but clinical | Solid but loses the brand's paper warmth |
| **c — cream ground** | `#fdfaf3` (`--cream`) | **Best — the hero-to-body seam disappears entirely, warm accents stay harmonious** | Good — cohesive, panels still pop against their own ink borders | Near-white legibility with warmth kept | **Recommended default for ordinary content pages** |
| d — white + orange accent | White ground, indigo swapped for `#c1662b` | Worst — repeated orange links overwhelm the page | Worst — clashes with the unchanged green CTA, and the swap was leaky (breadcrumbs stayed indigo) | Worst — the entire name column reads as alert text | Reject outright |
| (proposed) cream + lavender insets | Cream ground; lavender demoted to table headers, zebra rows, dashboard sub-panels | not rendered | not rendered | not rendered | Combines (c)'s seamless warmth with (a)'s one genuine win — structure for dense/sparse data zones |

**Decision: adopt cream (`c`) as the ground for every content band on ordinary
pages.** Reasoning: this system's cards and panels already do their own
separation work via 2px ink borders and hard offset shadows, so the lavender
tint isn't load-bearing for hierarchy — it only earned its keep on the sparse
dashboard, and even there cream reads as "good," not "broken." Lavender's cost
was paid on every single page: a warm/cool seam under every hero, and a grey
cast on the tan/green accents that sit on it. White fixes the tint but adds a
second seam and reads like a generic admin panel, not DataTalks.Club. Cream
was the only option that won or tied on all three page types.

**Rejected:** the orange-accent experiment. It clashes with the unchanged
green CTAs/status pills everywhere, and even the prepared mockup shipped the
swap incompletely (breadcrumbs stayed indigo) — a preview of how easy this is
to get half-applied across a real site. `--lavender` and `--indigo` are
otherwise unchanged.

**Deferred, not implemented this pass:** repurposing `--lavender` as an
*inset* accent (table header rows, zebra striping, dashboard sub-panels) once
cream is the ground. This is a genuine opportunity the reviewer identified but
it is new design work, not a mechanical fix, so it is recorded here for a
follow-up rather than shipped blind.

## Part 2 — Consistency findings

Findings are grouped by area, most-important first within each group. "Fixed
this pass" / "Deferred" marks what this audit's follow-up implementation
actually changed — see the commit/diff for exact edits.

### Sitewide / design system

1. **Content ground was lavender; now cream** (Part 1). Fixed this pass.
2. **`--page` tail colour was inconsistent** — pages whose last band was
   lavender needed a page-local `:root { --page: var(--lavender); }` override
   or the strip below the last band snapped back to cream; several
   course-platform pages (enrollment, leaderboard, homework, homework-stats,
   project, project-stats, account-settings) were missing it while dashboard
   had it. Moot after the cream-ground change: `--page` already defaults to
   cream, so no per-page override is needed for the common case. Fixed this
   pass (stale lavender overrides removed rather than added).
3. **Wiki hub, wiki search, wiki special-pages, blog hub, books hub all sit at
   the ~38rem reading measure**, not the 76rem shell that `/courses`,
   `/events` and `/podcast` use — so the wiki/blog/books family reads
   narrower than its 6-series siblings and the wiki hub's own h1 wraps at
   1440px. No documented rule assigns these pages a narrower width; this
   reads as an oversight, not a decision. Deferred (structural width change
   across five templates; flagged for a follow-up pass).
4. **Wiki topic page claims "one 48rem reading column"** in
   `design-5a.md`'s "as built" section, but the page actually renders at the
   shared 38rem `--measure`, same as the blog article. Fixed this pass (doc
   text corrected to match the shared primitive — the 38rem measure is
   correct and shared with the blog article; the "48rem" number was wrong).

### Course platform (logged-in, corrected capture)

5. **Leaderboard prints the literal string `None`** in the position column
   and in a `POSITION: NONE` pill whenever a student has no rank; on mobile
   it reads "`# None`". The dashboard already renders an unset value as a
   plain `-`. Fixed this pass.
6. **Homework and project pages (all four: homework, homework-stats, project,
   project-stats) put every callout in the cream hero band and leave a
   completely empty lavender band below it** — a bare ~90–110px strip with no
   heading and no content, at both viewports. This is a template bug, not a
   pacing choice. Fixed this pass (callouts moved into the content band).
7. **Redundant eyebrows repeating the breadcrumb/heading**: dashboard,
   enrollment and leaderboard all carry a `COURSE · YEAR` mono eyebrow
   directly under a breadcrumb that already says exactly that (the dashboard
   repeats it a third time in its own h1); project and project-stats carry a
   content-free `THE BRIEF` label. This is the same class of issue the
   2026-08-20 audit already named for removal and it is still in the
   pixels. Fixed this pass.
8. **Homework breadcrumb missing its final current-page crumb**: the
   homework page itself stops at the year ("Courses / Data Engineering
   Zoomcamp / 2026") with no final crumb, unlike its sibling `project.html`,
   which already appends one. Traced, not assumed: the "homework-stats" and
   "project-stats" captures that looked identical to their parent pages were
   not a template bug — `homework_statistics`/`project_statistics`
   deliberately redirect to the parent page with a flash message when the
   homework isn't scored yet or the project isn't completed
   (`courses/views/homework_statistics.py`,
   `courses/views/project_statistics.py`); the seed data used for this
   capture was in exactly that state. The real `homework/stats.html` and
   `projects/stats.html` templates already carry their own distinguishing
   h1/breadcrumb. Fixed this pass: added the missing final crumb to
   `homework.html` only; no stats template needed a change.
9. **`.spec-strip`/deadline fact drawn two different ways**: homework shows a
   left-aligned inline mono deadline line; project shows the same fact as a
   centred, dashed-bounded strip — centre alignment appears nowhere else in
   the system's spec rows. Deferred (needs a shared partial, not a one-line
   fix — flagged for follow-up).
10. **Undocumented left-rule callout primitive**, used with two different
    colour variants for the same "not available yet" message between
    homework-stats (lavender/indigo) and project-stats (sand/olive). Deferred
    (needs a decision: promote to the partial, or replace with `.panel`).
11. **Account settings** has a later cream band ("Courses enrolled") after
    its lavender content — becomes moot after the ground change since all
    ordinary bands are cream now — and a bespoke ~43.5rem column instead of
    the ~38rem the course pages use, with no breadcrumb. Width/breadcrumb
    deferred; band-ground finding resolved by Part 1.
12. **Leaderboard's "Your Record" panel sits inside the cream hero**, above
    the seam, rather than in the content band with "Standings". Deferred.
13. Toggle switches (enrollment/account-settings) render identically on both
    pages but are not in the component inventory — should be documented, not
    a rendering bug. Deferred (doc-only follow-up).
14. Enrollment's own lede ("Certificate name is managed in account settings")
    contradicts the certificate-name field directly below it. Content/UX
    note, not a design-system violation; flagged for editorial follow-up.

### Marketing / course pages

15. **Correction, not a bug**: the initial pass of this audit described the
    episode page's Timestamps section as duplicating the full transcript by
    mistake. Traced against `content/podcast_content.py::episode_view` and
    `content/tests/test_podcast_catalog.py`, which pins
    `len(view.timestamp_entries) == 146` for a real episode: Timestamps
    deliberately holds (almost) every transcript line, each independently
    clickable to jump the video to that exact moment — a scrubbing index, not
    a chapter list — while Transcript is the same lines grouped under section
    headers for a start-to-end read. Two different, intentional, tested views
    of the same lines, not a duplication defect. What *is* real: "Show Notes |
    Timestamps | Transcript" is plain anchor tab links with all three sections
    permanently stacked and visible (hence the 51,000–78,000px page height),
    not the `.filter-pills` segmented-control pattern the design doc names
    this exact trio as an example of. Making it a real single-visible-at-a-time
    switch is a genuine interaction-model change (JS toggle, ARIA state, a
    no-JS fallback) against a page with its own dedicated test suite — left
    deferred as a scoped follow-up rather than risked under this pass's time
    budget, not because it isn't worth doing.
16. **"More from this season" cards carry ~230px of empty space** above their
    eyebrow (an unfilled artwork slot) and are cramped into a narrow reading
    column instead of the ordinary card grid. Deferred.
17. **The same episode record has three different visual treatments**: a
    borderless ink-outline row with lavender showing through on `/podcast`,
    a fully borderless dashed row on the person profile, and a white
    hard-shadow card in the episode page's "related connections." The
    profile's whole reason for existing is "a piece of work looks the same
    wherever the reader meets it" — these three don't. Deferred.
18. **Course page spec strip mixes two surfaces**: STARTS/ENDS sit on
    lavender with indigo labels, LENGTH/HOMEWORK/PROJECTS sit on white with
    muted labels, in the same dashed strip. Fixed this pass (unified to one
    neutral surface, consistent with the cream-ground change).
19. **Correction, not a bug**: an undated episode row on a person profile
    loses its date rail and sits left of its dated siblings. Traced to
    `templates/public/_archive_row.html`, which documents and
    `content/tests/test_archive_row.py` guards this exact behaviour: "A record
    with no date gives its rail back to the card rather than leaving a column
    of empty space (seventeen podcast episodes have none)." Deliberate,
    tested graceful degradation for genuinely undated records, not a
    misalignment defect. No code change made.
20. Season/episode marker is a mint status pill on the episode page hero but
    a bare indigo eyebrow on `/podcast` and on the profile. Deferred.
21. Events index band has no `.band-head` — the lavender band opens directly
    with content, unlike every sibling index. Fixed this pass (heading
    added).
22. Podcast episode: guest is credited twice with two different bios and two
    different address-link treatments (plain underlined vs. `.pill-button`).
    Deferred — likely needs an editorial decision on which bio is current,
    not just a style fix.
23. Podcast episode: a second, serif-faced cover slab repeats the title under
    the video player. Deferred.
24. Podcast episode "All related connections" exposes raw graph internals
    (`person · person-podcast, podcast-link · weight 9`). Deferred.
25. Homepage keeps its hero illustration at 390px; the doc's narrow-layout
    rule says the hero drops its drawing and fineprint below 48rem. Flagged
    as a doc/build disagreement — deferred, needs a call on which is
    actually wanted before either changes.

### Wiki / blog / books

26. **Correction, not a bug**: four figures rendered as empty bordered boxes
    in the capture. Traced to `content/article_content.py::_image_source`,
    which requires (and the shared sanitizer enforces) a site-relative media
    path — never an external URL — so a working deploy always has the file
    behind that path. This local worktree never ran a content sync for
    article/blog media, so the images 404 locally while the `.prose img`
    border/frame CSS still renders around the empty element; that reads as
    "an empty bordered box" but is a local capture-environment gap, not a
    template or rendering defect. No code change made.
27. **Blog article prose tables clip their right column at 390px** with no
    scroll affordance. Traced: `content/article_content.py::prose_sections`
    already gives a real `kind="table"` source block the accessible,
    keyboard-reachable `.prose-scroll` region — but a markdown table that
    arrives inside a "paragraph"/quote block's own rendered HTML (a source
    body that "wrote its own block markup") is never reclassified as a table,
    so it renders through `.prose-embed` unwrapped. Confirmed as a real
    content-pipeline gap, not a template bug — this is the exact class of
    horizontal overflow the project's own screenshot capture safety check
    (`ci/screenshot_capture.py`'s `document.documentElement.scrollWidth`
    assertion) exists to catch. **Partially fixed this pass**: added
    `.prose-embed table { display: block; max-width: 100%; overflow-x: auto; }`
    to `templates/core/_design_system.html`, which stops the table from
    breaking the page's width on any viewport. **Still deferred**: this CSS
    safety net is not a labelled, focus-stop-carrying region the way
    `.prose-scroll` is — a fully accessible fix needs the content pipeline to
    detect and classify an embedded markdown table as a `kind="table"` block
    so it gets the real primitive, which is a content-pipeline change, not a
    template one.
28. **Wiki search status pills are stretched to a fixed grid-cell width**
    instead of hugging their label, unlike every other use of `.status-pill`
    in the system. Fixed this pass.
29. Eyebrow convention drifts across the hubs — some prefix with the section
    name the masthead already marks current (`BLOG · 55 ARTICLES`), the wiki
    hub is count-only, the wiki topic page's `WIKI` kicker repeats the
    breadcrumb directly above it. Fixed this pass (hub eyebrows reduced to
    count-only; wiki topic kicker removed).
30. Blog article's cover illustration is drawn twice in a row (once as an
    uncaptioned page-level cover, once as the body's first figure). Deferred.
31. Pagination (shared across the three hubs) is a clean, consistent
    component but appears nowhere in the component inventory, and its
    ellipsis renders as an odd sand-coloured square between round page
    circles. Fixed this pass (ellipsis mark restyled; doc entry to follow).
32. Wiki topic: inline citations/source references render as unstyled plain
    text mid-sentence with no link treatment. Deferred.
33. Wiki graph: breadcrumb says "Knowledge graph," h1 says "Podcast Graph" —
    should name the same thing. Fixed this pass.
34. Inline mono literals inconsistent: a Slack hashtag is boxed/filled on the
    blog article but surface-free (per spec) on the books hub. Fixed this
    pass (article's boxed treatment removed to match `.mono-code`).

### Docs / FAQ / auth

**Considered and reverted: FAQ home's `faq · N course` eyebrow.** The `faq ·`
prefix reads redundant against the eyebrow rule in isolation, and it was
briefly removed to count-only phrasing during this pass. Reverted: a dedicated
existing test, `core/tests/test_design_surface_eyebrows.py`, deliberately pins
this exact label as retained "useful context" from the *2026-08-20* audit, and
`templates/review/docs_home.html`'s sibling `docs · N sections` eyebrow was
untouched — removing only the FAQ prefix would have swapped one inconsistency
(a redundant prefix) for another (two sibling index pages disagreeing on their
own eyebrow convention). Left both as `faq ·`/`docs ·` pending a deliberate
decision to change the pinned contract for both at once, not a drive-by on
one.

35. **Docs detail page: ~4,300px of empty lavender at 1440px** — the article
    body ends a fifth of the way down the page while the section sidebar tree
    keeps running for four more screens; at 390px the entire ~150-link tree
    renders inline between the article and the prev/next cards, forcing a
    long scroll through raw navigation. Deferred — this needs the sidebar
    folded behind a named `<details>`, which is a real template change to the
    docs surface; flagged as a high-priority follow-up.
36. Docs detail composes three different column widths on one page (a
    sidebar+article layout, a ~45rem article measure, then a full-shell-width
    prev/next band) — none of them the shared content width docs-home and
    FAQ now correctly use. Deferred (bundled with #35).
37. Sign-in is a visible fork of the sign-up/entrance family: at 390px its
    submit button stays a narrow pill instead of the family's full-width
    reshape; the lavender band has no heading at all (no `.band-head`) while
    sign-up's does; the lede said "login" where the rest of the page and its
    sibling say "sign-in". Fixed this pass: sign-in now uses the shared
    `.entrance-actions` full-width mobile CTA, gained a "Choose how you sign
    in" band heading, and its lede was reworded to "sign-in" for internal
    consistency. **Not changed**: the `<h1>` stays the exact string "Sign In"
    (title case) — it is a pinned production release-gate contract
    (`deploy/smoke.py` raises `ReleaseContractError` if this exact text is
    missing from the rendered page) and several Playwright smoke suites do an
    exact-match role lookup on it. This was caught only by tracing why an
    unrelated test broke after an initial edit lower-cased it; the h1 was
    reverted and the fix scoped to what was actually safe to change.
38. Auth capture gap: no `SocialApp` was seeded locally, so the documented
    primary path (provider buttons, `.entrance-or` divider) could not be
    audited at all. Not a code defect — flagged for the next capture run to
    seed `seed_local_social_providers` first.

## Fixed this pass (summary)

Items 1, 2, 4, 5, 6, 7, 8, 18, 21, 27 (partial — CSS overflow fix; full
accessible fix needs a content-pipeline change), 28, 29, 31, 33, 34, 37.

## Corrected — not bugs, no code change (summary)

A second pass, prompted by "make sure any bugs are fixed," re-traced every
deferred item against the actual code and its tests before accepting the
first pass's read. Three were deliberate, already-tested behaviour that this
audit's first pass misread as defects:

- **15** — the podcast episode's Timestamps section duplicating the
  Transcript's lines is intentional (a scrub index vs. a chapter read), pinned
  by `content/tests/test_podcast_catalog.py`'s `len(view.timestamp_entries) ==
  146`. The one real issue in that finding (anchor tabs instead of a
  `.filter-pills` switch, all three sections always stacked) remains
  deferred — seem "Marketing / course pages" for the corrected writeup.
- **19** — a person profile's undated episode row losing its date rail is
  documented and guarded by `content/tests/test_archive_row.py`
  ("seventeen podcast episodes have none").
- **26** — the blog article's empty image boxes are a local-worktree content
  sync gap (no article media was synced into this environment), not a
  template defect; `content/article_content.py::_image_source` already
  enforces a valid site-relative path.

## Deferred (summary, highest priority first)

Docs detail sidebar fold and width (35, 36); podcast Timestamps/Transcript/
Show Notes as a real `.filter-pills` switch (15); podcast card/record/marker
consistency (16, 17, 20); `.prose-embed` table accessible region — the
content-pipeline half of 27; wiki/blog/books hub width unification (3);
spec-strip and callout primitive unification on homework/project (9, 10);
account-settings width and breadcrumb (11); remaining podcast content items
(22, 23, 24 — 22 in particular looks like intentional current-vs.-historical
bio content, not a bug, but needs an editorial call, not a code guess); wiki
topic citation styling (32); `--lavender`-as-inset follow-up design work
(Part 1).

## Verification

Every fix in this pass was made directly in templates/CSS/one Python string
and verified by re-running the project's Django test suite (`pytest` with
`DJANGO_SETTINGS_MODULE=website.settings.test`), not just re-screenshotting.
Three genuine regressions were caught this way and corrected before this audit
was considered done:

- `courses/tests/test_homework.py` asserted the missing-breadcrumb-crumb *bug*
  as expected behaviour; updated to assert the fixed contract instead.
- `content/tests/test_collection_hub.py` and `content/tests/test_wiki_design.py`
  pinned the old hub-eyebrow and wiki-topic-kicker text; updated to match the
  corrected copy.
- `accounts/tests/test_auth_configuration.py` pinned the old sign-in lede
  text; updated. Its sibling `accounts/tests/test_single_identity.py` and
  `playwright_tests/test_foundation_smoke.py` / `test_accessibility.py`
  pinned exact heading text this pass also touched (`Sign In`, `Podcast
  Graph`) — the sign-in heading was deliberately left unchanged (see item 37)
  so nothing needed updating there; the wiki-graph heading change was real, so
  the two Playwright expectations were updated to `Knowledge graph`.
- `_docs/adoption/course-platform/integration-patched-files.tsv` pins an exact
  byte size and sha256 for `courses/templates/courses/course_list.html`
  against a recorded rationale; updated with the new hash and an appended
  rationale line, following the same pattern the file already used for two
  earlier band-ground changes to this file.
- One eyebrow removal (FAQ home's `faq ·` prefix) was reverted after breaking
  `core/tests/test_design_surface_eyebrows.py`, which deliberately pins it as
  retained "useful context" from the prior audit — see the note under
  "Docs / FAQ / auth" above.

Full-suite runs (`studio/tests core/tests content/tests courses/tests
accounts/tests`, plus `ruff check`, `ruff format --check`, and
`scripts/check_development_terminology.py`) surfaced four further failures,
all confirmed pre-existing and unrelated by checking `git status` shows zero
diff on every file each failure touches:

- Six failing + six erroring tests in `courses/tests/test_migration_history_compatibility.py`
  and `courses/tests/test_curriculum_source_provenance_migration.py`, plus one
  error in `accounts/tests/test_identity_migrations.py` — these require the
  project's own `manage.py test` runner (`TEST_RUNTIME`/`IsolatedDiscoverRunner`
  worker isolation per `Makefile`'s `test-core`/`test-django-full` targets),
  not bare `pytest`; they fail the same way regardless of this audit's changes.
- `core/tests/test_course_platform_adoption.py::test_all_recorded_copies_exist_with_recorded_integration_state`
  — `courses/views/project_submission_listing.py` differs from its recorded
  copied-source hash with no corresponding patch-manifest entry explaining
  why. The file is untouched in this worktree; the gap predates this audit.
- `core/tests/test_development_terminology.py` and
  `core/tests/test_repository_contracts.py` — both fail on
  `_docs/specs/open-decisions.md`, a file untouched in this worktree.

These three are outside a design-consistency audit's scope and were left
alone rather than patched blind.
