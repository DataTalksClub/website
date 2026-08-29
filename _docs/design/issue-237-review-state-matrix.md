# Issue 237 rendered-review data and state matrix

This is the durable setup and coverage contract for the sitewide adversarial
design review in issue #237. The generated database is realistic but wholly
synthetic. It does not read a production export, the pinned production-like CMP
catalogue, provider data, or personal data. Runtime databases, manifests,
authenticated browser states, and screenshots remain below `.tmp/`.

The homepage `/` and the `/events` listing are absolute exclusions. Neither is a
review route or fixture destination in this matrix. An individual event detail
remains in scope.

## Build and run

From the repository root, build a fresh isolated database:

```bash
uv run python scripts/build_synthetic_design_review_db.py
```

The builder refuses to overwrite an existing database and refuses every target
outside the project-local `.tmp/` directory. Its default outputs are:

- `.tmp/design-review/issue-237.sqlite3` — migrated synthetic SQLite database;
- `.tmp/design-review/issue-237-manifest.json` — redacted route/persona manifest;
- `.tmp/design-review/browser-state/*.json` — session-bearing Playwright states.

Start the site against that database:

```bash
DTC_ENVIRONMENT=local \
DTC_SQLITE_PATH=.tmp/design-review/issue-237.sqlite3 \
DJANGO_SETTINGS_MODULE=website.settings.local_review \
uv run python manage.py runserver 0.0.0.0:8000
```

The `local_review` settings disable outbound network and provider/email side
effects. For anonymous pages, open the manifest path directly under
`http://localhost:8000`. For an authenticated render, create the browser context
with the appropriate state, for example:

```python
context = browser.new_context(
    storage_state=".tmp/design-review/browser-state/active-learner.json",
)
```

The four state names are `active-learner`, `peer-reviewer`, `graduate`, and
`observer`. Browser-state files contain authentication material: keep them in
`.tmp`, never print their contents, attach them to reports, or commit them. The
redacted manifest contains no email address or session value and is the source of
truth for generated paths and expected GET statuses.

Use 1440×900 and 390×844 for every representative route. Sample every shell in
both themes; add 200% zoom/reflow to dense curricula, long-form content, tables,
forms, and long titles. Use reduced motion and keyboard-only navigation on every
interactive family.

## Course representation vocabulary

The implementation has three separate axes. Review findings must name the axis
they concern instead of calling every old-looking path “legacy.”

| Axis | Values | Review meaning |
| --- | --- | --- |
| Curriculum presentation | `legacy` or `modules` | `legacy` renders Homework followed by a separate Projects section. `modules` renders one ordered flow of modules and interleaved projects; each module terminates in one existing Homework. |
| URL contract | canonical family/cohort route or compatibility edition-slug route | Canonical routes use `/courses/<family>/<identifier>`. The compatibility adapter preserves `/courses/<edition-slug>/...` and redirects where the contract requires. This is independent of curriculum format. |
| Content ownership/provenance | DB-managed or repository-source-managed | Nullable complete provenance identifies repository-managed records. Absence identifies DB-managed records. The review seed is entirely DB-managed; it does not pretend that synthetic rows were imported. |

“Adopted legacy curriculum” below means the current adopted course-platform ORM
and services with `curriculum_format=legacy`; it does not mean an old model still
named `Course`. The reusable `Course` family and dated `Cohort` are current in both
fixture paths. “Native module curriculum” means the current module presentation,
not a replacement for adopted homework, project, scoring, or peer-review behavior.

The seed first composes
`FactoryContext(...)+create_current_scenario(bundle="adopted_courses",
state="minimal_valid")`. It then gives that current graph credible review copy and
adds supported module-format and lifecycle records. This preserves the repository's
deterministic factory, ORM relationships, and existing assignment workflows.

## Synthetic records and personas

| Record/persona | Synthetic state enabled |
| --- | --- |
| Data Reliability Zoomcamp family | Adopted legacy presentation with active 2026, completed 2025, and hidden direct-link empty archive-preview cohorts; canonical and compatibility URLs. |
| Streaming Systems Lab family | DB-managed module presentation with dense active Autumn 2026 and registration-open empty Spring 2027 cohorts. |
| Active learner | Enrolled in both active cohorts; scored/submitted/open work, mixed unit read state, leaderboard position, one prior registration, and individual Wrapped data. |
| Peer reviewer | Own project submission, assigned submitted peer review, and complaint-form access. |
| Graduate | Completed 2025 cohort, score, leaderboard position, certificate name, and synthetic certificate URL. |
| Observer | Authenticated but unenrolled; exercises empty progress and safe task denials without a privileged role. |
| Eight additional learners | Dense leaderboard including a deliberately long display name and a row hidden from public display. |
| Native modules | Five modules with 1/4/7/3/2 units, mixed read/unread state, two interleaved projects, terminal homework, sparse and dense rails. |
| Rich native unit | Long title and prose with table, block quote, fenced Python, Mermaid, and previous/next navigation. |
| Empty native unit | Empty Markdown body exercising the supported “does not have any content yet” fallback. |
| Assignments | Scored, open-unsubmitted, closed-unsubmitted, collecting-submissions, peer-reviewing, and completed states with short and long copy. |
| Registration | Open preview, already-registered actor, multi-error interaction, and inactive-campaign 404 safe denial. |
| Wrapped | Visible aggregate, individual activity, individual no-activity, and absent/hidden aggregate states. |

Course instructor relations are not invented: the current database course/cohort
models do not own a teaching-team relation. Public editorial instructor/person
relationships remain in the checked content projection and are reviewed as content
fidelity, not copied into the synthetic database.

## Generated course and platform route matrix

The manifest gives exact generated values. The paths below are stable labels or
patterns where an integer ID comes from the manifest.

| Page/route | Actor and state | Why it matters | Synthetic enabler/access |
| --- | --- | --- | --- |
| `/courses` | Anonymous; active, registration-open, finished/archive | Tests status grouping, family collapse, density, dates, assignment totals, long title, and empty/hidden exclusion. | Both course families; open Spring 2027; completed 2025. Use `courses-index-anonymous`. |
| `/courses/data-reliability-zoomcamp` | Anonymous; three cohort records | Tests family hierarchy and edition navigation. | Data Reliability family; `legacy-family`. |
| `/courses/data-reliability-zoomcamp/2026` | Anonymous then active learner | Compares unenrolled calls to action with score/progress/submission state. | Adopted legacy active cohort; `legacy-active-anonymous` and `legacy-active-enrolled`. |
| `/courses/data-reliability-zoomcamp-2026/` | Anonymous; expected 301 | Preserves the edition-slug compatibility contract and destination clarity. | `legacy-compatibility`; follow the one redirect to canonical. |
| `/courses/data-reliability-zoomcamp/2025` | Graduate; completed | Tests archive language, completed assignments, certificate/score state. | `legacy-completed`. |
| `/courses/data-reliability-zoomcamp/archive-preview` | Anonymous; hidden but direct-link reachable and empty | Tests supported sparse/empty curriculum without leaking it into `/courses`. | `legacy-hidden-empty`. |
| `/courses/streaming-systems-lab` | Anonymous; two cohorts | Tests current family page and upcoming-versus-active edition hierarchy. | `native-family`. |
| `/courses/streaming-systems-lab/autumn-2026` | Anonymous then active learner | Tests dense ordered flow, long title, interleaved projects, progress, and responsive reflow. | `native-active-anonymous` and `native-active-enrolled`. |
| `/courses/register/streaming-systems-lab-spring-2027/` | Anonymous | Tests normal registration preview and long marketing copy. | `native-registration`. Submit blank/invalid fields for multi-error summary and focus. |
| Same registration route | Active learner | Tests already-registered state without a second identity. | `native-already-registered`. |
| `/courses/register/streaming-systems-lab-closed-preview/` | Anonymous; expected 404 | Tests safe denial for inactive campaigns. | `native-registration-closed`. |
| Native module 03 route from manifest | Active learner; seven units and mixed read state | Tests dense rail, current location, terminal homework, target size, and mobile reshape. | `native-module-dense`. |
| Rich native unit route from manifest | Active learner | Tests long title, prose/table/code/Mermaid overflow, rail current state, and previous/next destinations. | `native-unit-rich`. |
| Empty native unit route from manifest | Anonymous | Tests meaningful empty fallback and anonymous read-only rail. | `native-unit-empty`. |
| Unit read endpoint (`.../<unit>/read`) | Anonymous POST then active-learner POST | Anonymous must redirect to login; learner toggles `is_read=0/1`, rejects any other value with 400. | Use the unit paths above; browser state `active-learner`. This is an interaction, not a GET manifest row. |
| Legacy dashboard route from manifest | Active learner then observer | Contrasts busy deadlines/score/progress with authenticated unenrolled empty state. | `legacy-dashboard`, `legacy-dashboard-unenrolled`. |
| Open/scored/closed homework routes | Active learner or observer | Tests not-started, editable submission, submitted/scored feedback, closed state, long question, optional fields, and action clarity. | `legacy-homework-open`, `legacy-homework-scored`, `legacy-homework-closed`. Submit the open form empty for validation summary/preserved values; do not complete a network-side-effect path. |
| Scored homework `/stats` | Anonymous | Tests dense statistic sections and question rows. | `legacy-homework-stats`. |
| Peer-reviewing and collecting project routes | Active learner | Tests existing submission versus open submission form and state comprehension. | `legacy-project-peer-review`, `legacy-project-collecting`. |
| Project `/eval` | Peer reviewer | Tests assigned/completed review grouping and closed/open labels. | `legacy-peer-review`. |
| Legacy leaderboard | Active learner | Tests populated ranking, privacy-hidden row, long display name, pagination geometry, and self-state. | `legacy-leaderboard`. |
| Leaderboard score detail | Active learner | Tests score breakdown hierarchy and complaint destination. | `legacy-score-breakdown`. |
| Leaderboard report form | Peer reviewer | Tests authenticated form, errors, focus, and return destination. | `legacy-complaint`; submit blank to render validation without creating report evidence. |
| `/courses/wrapped/2026/` | Anonymous | Visible aggregate with two courses and leaderboard. | `wrapped-aggregate`. |
| `/courses/wrapped/2025/` | Anonymous | Supported hidden/absent no-data page. | `wrapped-no-data`. |
| Wrapped individual routes from manifest | Anonymous | Contrasts shareable learner summary with a valid user who has no activity. | `wrapped-individual`, `wrapped-individual-empty`. |

## Public content, utility, and authentication matrix

These pages read checked public/review projections, not the synthetic course
database. Their existing content is the product under review and must retain content
fidelity; this work does not copy it into a new fixture. When an edge state needs
synthetic content, use the already maintained accessibility URL configuration and
factory environment in `playwright_tests/test_accessibility.py`, which creates only
reserved synthetic identities and values. Do not create production lookalike people,
events, or editorial documents in the review database.

| Page/route | State and evidence | Why it matters | Setup/access |
| --- | --- | --- | --- |
| `/events/365/ai-dev-tools-zoomcamp-2026-course-launch` | Anonymous canonical individual event detail; light/dark/mobile | Tests title/date/action/speaker hierarchy and content relationships. | Checked public projection. This individual detail is allowed; do not visit/audit `/events`. |
| `/podcast` | Anonymous hub | Tests collection hierarchy and episode density. | Checked public projection. |
| `/podcast/practical-llm-engineering-and-rag.html` | Long-form episode/transcript/media | Tests long title, metadata, player/transcript, and body measure. | Checked public projection; also test missing media through `/_accessibility/missing-media/` under `playwright_tests.accessibility_fixture_urls`. |
| `/blog` and `/blog/sponsor-datatalks-club.html` | Hub and rich long-form article | Tests collection pagination, headings, prose, links, figures/tables where present, and long reading flow. | Checked public projection. |
| `/books` and `/books/20251006-software-development-at-rocket-speed.html` | Hub and long-title book | Tests card density, title wrapping, imported prose, and outbound-label fidelity. | Checked public projection. |
| `/people/alexeygrigorev.html` | Contribution-dense profile | Tests long grouped contributions, metadata, and cross-family links. | Existing public editorial identity; do not synthesize or alter identity data. |
| `/wiki` | Hub | Tests topic hierarchy and search affordance. | Checked public projection. |
| `/wiki?q=machine+learning` and `/wiki?q=no-such-public-topic` | Results and zero results | Contrasts dense result rows with meaningful empty state. | No DB seed; query the checked index. |
| `/wiki/graph` | Dense interactive graph | Tests wide content, focus, overflow, contrast, and reduced motion. | Checked graph projection. |
| `/wiki/special-pages` | Dense utility list | Tests grouped metadata and narrow wrapping. | Checked public projection. |
| `/wiki/a-a-testing` | Detail | Tests article-like wiki prose and local navigation. | Checked public projection. |
| `/docs/` and `/docs/activities/` | Hub and nested detail | Tests trail/current location, navigation depth, code/prose, and long links. | Checked review projection. |
| `/faq/` and `/faq/ai-dev-tools-zoomcamp.html#4487db3924` | Hub and anchored course question | Tests scanning, anchor arrival/focus context, and long answer prose. | Checked review projection. |
| `/slack` | Anonymous normal state | Tests focused call to action, explanatory copy, and destination semantics. | Checked review projection. |
| `/terms` | Anonymous long legal prose | Tests reading measure, deep headings, lists, and 200% reflow. | Checked legal content. |
| `/accounts/login/` | Normal, keyboard focus, invalid synthetic credentials | Tests provider/action hierarchy, error alert, preserved privacy, and return intent. | Seed local placeholder providers if buttons are required: `uv run python manage.py seed_local_social_providers`; enter only `invalid@example.invalid` and a synthetic invalid password. |
| `/accounts/signup/` | Normal social signup entry | Tests distinction from login, provider targets, terms copy, and narrow layout. | Same local provider seed; do not follow placeholder providers. |
| `/__issue_237_missing__` | 404 | Tests application error shell and recovery navigation. | No seed. Expected 404. |

## Review interactions and state limits

- Capture screenshots only below `.tmp/screenshots/issue-237/`; mask any
  session-bearing or email-shaped value. The synthetic fixture uses reserved
  `example.invalid` emails internally, but the redacted manifest never exposes them.
- Do not follow external links. The local-review middleware and browser test network
  guard must remain enabled.
- Form error states may be produced with blank or clearly synthetic invalid values.
  Do not submit a successful path that queues mail or a durable provider job merely
  for visual evidence.
- The current `Cohort` model derives public lifecycle grouping from dates,
  `registration_url`, and `finished`; it does not yet expose the full draft → archived
  enum proposed by specification 04. The fixture therefore covers only states the
  current product renders and does not invent grading/cancelled UI.
- Module pages are public GETs. Only the read/unread mutation requires authentication;
  the anonymous redirect and invalid-value 400 are the supported denial/error states.
- A hidden cohort remains directly reachable by established behavior while excluded
  from the course index. Treat that as a visibility edge state, not an authorization
  boundary.
- Instructor/learner editorial linking, closed-registration explanatory UI, and a
  dedicated locked-module lifecycle are not current model/view contracts. Review the
  existing safe redirect/404/empty states; do not infer missing product behavior from
  the absence of synthetic rows.

## Focused verification

Run the deterministic graph and route contract:

```bash
uv run --frozen pytest test_support/tests/test_design_review_data.py -q
uv run ruff check \
  test_support/design_review_data.py \
  test_support/tests/test_design_review_data.py \
  scripts/build_synthetic_design_review_db.py
uv run ruff format --check \
  test_support/design_review_data.py \
  test_support/tests/test_design_review_data.py \
  scripts/build_synthetic_design_review_db.py
```

The test asserts the distinct `legacy`/`modules` cohort counts, read-state data,
all redacted manifest routes and expected statuses, absence of email/session values
from the manifest, and continued exclusion of `/` and `/events`.
