# Signed-in home, onboarding, and registration — design specification

Written by the design agent, 2026-09-02. One file, deliberately: the empty
signed-in home *is* the onboarding surface, and course registration is the
most common reason an account exists at all. Splitting them would force three
documents to share one dismissal store, one field-ownership table, and one
set of states. Sections: §1 corrections to the brief, §2 recommendation,
§3 URL/caching decision, §4 page architecture and data, §5 states with copy,
§6 layout, §7 onboarding, §8 registration, §9 events, §10 what is not on the
page, §11 accessibility/compliance, §12 verification recipes, §13 open
questions.

Grounding: I read the code cited throughout, fetched the running pages at
http://localhost:8000 signed in as the local admin account, computed the
contrast ratios in §11 myself, and studied the sibling product at
`/home/alexey/git/ai-shipping-labs` (AISL) as directed. Claims about
production data (20,736 enrolments, 36,320 submissions, ml-zoomcamp-2026's
9 enrolments / 0 questions) are **taken on trust — the local database was
imported without any personal data and holds zero enrollments, zero
submissions, and zero registrations**, which I verified by loading
`/accounts/settings/` ("You are not enrolled in any courses yet.") and the
cohort pages. Every state in this spec therefore comes with a local
manufacturing recipe (§12).

---

## 1. Corrections to the brief

Several second-hand claims needed checking. Three are wrong or misleading:

1. **"A per-cohort dashboard already exists … much of what a signed-in home
   needs may already be solved there" — no.** `/courses/<family>/<year>/dashboard`
   (`courses/views/dashboard.py:7-15`, `courses/templates/courses/dashboard.html`)
   is an *aggregate statistics* page: total enrollments, completion rates,
   homework difficulty, submission timing. It has no `@login_required`, no
   per-user data, and renders identically for everyone. The personal surface
   that actually exists is the **cohort page's "Your work in this course"
   block** (`courses/templates/courses/course.html:448-484`): enrollment
   pill, total score, "N of M modules submitted" progress bar. *That* is the
   component family the signed-in home should reuse.

2. **"Social sign-in via Google, GitHub and Slack is configured" — configured
   for sign-in, closed for sign-up.**
   `ConsolidatingSocialAccountAdapter.is_open_for_signup` returns `False`
   unconditionally (`accounts/auth.py:261-263`); a social login only links to
   an *existing* account whose verified email matches exactly one user
   (`accounts/auth.py:265-358`). New accounts today are created only through
   the email+password form (`ACCOUNT_SIGNUP_FIELDS = ["email*", "password1*",
   "password2*"]`, `website/settings/base.py:191`). All three provider buttons
   render on `/accounts/signup/` (verified live), but for a brand-new person
   they dead-end in the identity-conflict page. `ACCOUNT_ALLOW_REGISTRATION =
   False` (`website/settings/base.py:195`) is consumed by nothing except the
   identity inventory (`accounts/identity_inventory.py:218`) — email signup
   is in fact open, verified live. Consequence: onboarding cannot lean on
   provider-supplied names/handles for *new* users today (§7.4, §13-Q2).

3. **"A decision in flight about whether registration stays internal" — the
   decision is already written down.** `_docs/specs/open-decisions.md:84-101`
   (§6) and `_docs/specs/04-courses-and-cohorts.md:139-157` decide that course
   registration is **account-owned**: "it requires a durable account with
   verified email ownership and a completed member profile … and it never
   creates an anonymous `CourseRegistration`." It is unimplemented —
   `CourseRegistration.user` is still nullable (`courses/models/cohort.py:338-344`)
   and `MemberProfile` does not exist — but §8's sign-in-first registration is
   *implementing standing spec authority*, not making a new bet.

Verified as stated: the accent-border ban test exists
(`core/tests/test_design_accent_borders.py:123-157`); the 24×24 target floor
is enforced (`playwright_tests/accessibility_support.py:348-380`); `--indigo`
on `--lavender-deep` measures exactly 4.11:1 (§11); `/` is row 327 of the
2,937-row baseline. The local equivalent of the ml-zoomcamp-2026 state is
real and verifiable: locally it sits in the "Open registration" band of
`/courses` with `/courses/register/ml-zoomcamp/` active and every homework
`Closed` with future due dates (verified live).

---

## 2. Recommendation

**The signed-in home has one job: put the learner back into their cohort.**
Everything else on the page exists to serve the person for whom that job is
undefined — no cohort yet, or a finished one.

Argued from the product, not from dashboard convention:

- DataTalks.Club courses are **cohorts with deadlines**. The two questions a
  returning learner has are *"where was I?"* and *"what is due?"*. Both are
  currently answered nowhere: `_docs/design/specs/learning-flow-ux.md:114-125`
  lists "Resume point — Nowhere" and "What is next — Nothing persistent" as
  the two states the learner cannot see. `UnitReadState`
  (`courses/models/curriculum.py:131-152`) makes resume buildable; the
  homework/project due-date columns make "due next" buildable.
- Everything a dashboard-of-widgets would add — leaderboards, stats, blog,
  podcast, wiki — is one masthead click away and is *not* what someone signs
  in for. AISL reached the same conclusion the hard way: its logged-in-home
  audit (`ai-shipping-labs/_docs/research/2026-08-11-home-logged-in-audit.md:30`)
  diagnosed the widget version as "a chronological changelog of features
  rather than an answer to 'what should I focus on this week'" and rebuilt
  around a commitment-first spine.

**What is copied from AISL, plainly** (its signed-in home is
`ai-shipping-labs/content/views/home.py:209-233` +
`templates/content/_dashboard_commitment_zones.html`):

- `/` branches on authentication in one view; no redirect (§3).
- Commitment-first ordering: what's due → continue → discover.
- "Getting started" checklist with per-item skip and server-persisted
  dismissals (§7).
- The empty-state philosophy: **hide empty sections entirely; never render
  "nothing here yet"** — asserted in their tests
  (`ai-shipping-labs/content/tests/test_dashboard.py:218-225`). One designed
  lead (the checklist + catalogue) carries the empty page instead.
- Single-column, reading-width dashboard (their audit's one recorded
  "DECIDED": `max-w-3xl` single column). DTC's `content-shell` is the same
  idea and already exists.

**Where this design diverges from AISL, and why:**

| AISL pattern | DTC decision | Reason |
| --- | --- | --- |
| Tier pill, "Unlock more" upsell zone | none | DTC is free; there is nothing to upsell |
| "Your week" sprint-plan card | "Next deadline" per cohort + merged "Due soon" | DTC's dated commitments are homework/project deadlines, not staff-authored plans |
| Onboarding questionnaire / AI chat | none — checklist + one slim "About you" page | AISL's questionnaire feeds humans writing a paid plan; DTC has no consumer for those answers |
| "For you" blended feed (8 cards) | none | the feed interleaves locked content as upsell; DTC has no locked content |
| Greeting "Welcome back, {first_name}" | no name | DTC email signups carry no name; `certificate_name` is optional and legal-name-flavoured (§13-Q3) |
| `/` authenticated response has no cache headers | explicit `Cache-Control: private, no-store` | AISL has no CDN in front of HTML; DTC is about to put one there (§3) |

---

## 3. The URL decision: `/` serves both, branching on authentication

**Decision: keep one URL. `core.views.home` returns the marketing page for
anonymous requests and the member home for authenticated ones. No redirect.**
This is the AISL pattern verbatim (`ai-shipping-labs/content/views/home.py:218-233`).

### Why this survives the CloudFront work (the caching argument)

The Phase 5 plan (`_docs/runbooks/production-hosting-and-dns-migration.md:1217-1300`,
§10; `_docs/specs/open-decisions.md:267-281`; `_docs/specs/01-platform-architecture.md:136-141`)
is: a viewer-request CloudFront function marks a request `anonymous-v1` only
when it is *provably credential-free* (no session/auth/CSRF or
credential-shaped cookie), anything doubtful is private, and an
origin-response guard forces `private, no-store` on any response carrying
cookies or identity. `_docs/specs/04-courses-and-cohorts.md:298-310` already
places "account/profile, enrollment, dashboard" responses in the permanent
zero-TTL class. Under that architecture:

- **A signed-in request never touches the cache**, whatever `/` renders for
  it. Branching adds zero new caching burden *provided the anonymous branch
  stays byte-stable, cookie-free and CSRF-token-free* — which it already is:
  the shared shell emits its CSRF token only when authenticated
  (`templates/core/_site_shell_head.html:39-41`), an invariant §10.2 of the
  runbook depends on and this design preserves.
- **A redirect buys no cache safety.** The 302 would itself be a credentialed
  response — exactly as uncacheable as the member home — while costing an
  extra hop on every sign-in (`LOGIN_REDIRECT_URL = "/"`,
  `website/settings/base.py:184`; `safe_next_path` falls back to `/`,
  `accounts/navigation.py:14,147-167`) and minting a second public URL for
  one concept.
- **The compatibility baseline is satisfied either way, and I verified how it
  is checked**: `/` (row 327 of
  `_docs/compatibility/generated-path-baseline.jsonl`) must answer 200 HTML,
  and the checker observes routes through a **fresh anonymous Django test
  client** (`compatibility/django.py:109,143-161`). The anonymous branch is
  unchanged, so the contract holds.
- The marketing page is *already* personalised: every page, `/` included,
  renders the user menu vs. "Sign up free" from the shared masthead
  (`templates/core/_site_shell_head.html:131-192`). "A `/` that varies by
  cookie" is the status quo of the entire site; the classifier design exists
  precisely because of it.

### The implementation contract (normative)

1. Anonymous `GET /` → today's `core/views.py:63-107` response, byte-stable,
   no `Set-Cookie`, no CSRF token in the body.
2. Authenticated `GET /` → the member home, with
   `Cache-Control: private, no-store` set explicitly on the response — the
   defence AISL skipped (their `studio/middleware.py` exempts `/`) and DTC
   must not, because DTC will actually have an edge cache.
3. Tests to add: (a) anonymous `/` emits no `Set-Cookie` and no csrf token
   (the runbook §10.2 names this exact test); (b) authenticated `/` carries
   `private, no-store`; (c) authenticated `/` does not contain the marketing
   hero heading, anonymous `/` does not contain "Getting started".
4. No `robots` work needed: crawlers are anonymous and can only ever receive
   the marketing branch.

### What happens to the marketing content

A signed-in user who clicks the logo (`href="/"`) lands on **their** home.
The marketing home's content — courses, events, blog, podcast, wiki, Slack —
is all reachable from the identical nine-entry masthead
(`_docs/design/design-system.md`), which this design does not touch
(`core/tests/test_design_system_shell.py` compares it across every page).
The marketing *page itself* becomes unreachable while signed in; AISL made
the same trade and nothing on it is member-relevant (its two CTAs are "sign
up"). If the owner wants it back later, a footer link is the cheap fix —
deliberately not designed here (§13-Q7).

Sign-out returns to the marketing home automatically
(`LOGOUT_REDIRECT_URL = "home"`, `website/settings/base.py:185`).

---

## 4. Page architecture and data

### Shell

New template `templates/core/member_home.html` **extending
`core/content_page.html`** — cream hero band, lavender content band, normal
`content-shell` width. The member home is an ordinary content page, *not* a
third top-level layout exception; `AGENTS.md`'s exception list
(marketing homepage, auth pages) stays as it is. No breadcrumbs: the settled
pattern is "the trail lists ancestors, the h1 says where you are", and a
top-level page has no ancestors — omit the `<nav class="breadcrumbs">`
entirely (precedent: `/courses`). Document title: `Home — DataTalks.Club`.
No canonical link (only crawlers care, and they never see this branch).
`:root { --page: var(--lavender); }` as on every page whose last band is
lavender.

The view branch lives in `core/views.home`; the queries belong to the
courses app (a `courses/services/member_home.py` context builder), because
every model touched is courses-owned — the implementer should confirm the
split against `_docs/architecture/app-boundaries.md` before starting.

### "My courses" — the set everything hangs on

**Neither `Enrollment` nor `CourseRegistration` alone answers "which courses
am I in".** Enrollment rows are created *lazily* on first coursework
interaction (`get_or_create` at `courses/views/course_enrollment.py:33`,
`courses/views/homework_context.py:242`, etc.) — a learner who registered
and never opened a homework has no Enrollment. Registration rows may lack a
`user` FK (anonymous registrations, matched by email — the pattern at
`courses/views/registration.py:62-66`). The member home uses the union:

```python
email_normalized = (user.email or "").strip().lower()
enrolled_ids = Enrollment.objects.filter(student=user).values("course_id")
registered_ids = CourseRegistration.objects.filter(
    Q(user=user) | Q(email_normalized=email_normalized)
).values("course_id")
my_cohorts = (
    Cohort.objects.filter(Q(id__in=enrolled_ids) | Q(id__in=registered_ids))
    .select_related("course")
)
```

Classify each cohort with the **existing derived rule** — there is no state
column (`courses/views/course_list.py:82-105`,
`courses/views/course_homepage.py:17-21`):

- `finished=True` → **finished** (state 3 material);
- else `registration_url` non-empty and `start_date` in the future →
  **upcoming** (state 4 material);
- else → **active** (state 2 material).

Do not filter on `visible` here: these are the user's own courses, and
`visible=False` cohorts remain reachable by direct link by design
(`courses/models/cohort.py:195-200`).

### Continue — the resume target

Per active cohort, for `curriculum_format == "modules"`: the first unit the
user has not read, in curriculum order, reusing the existing annotation
pattern from `courses/views/module.py:36-45`:

```python
next_unit = (
    Unit.objects.filter(module__cohort=cohort)
    .annotate(is_read=Exists(
        UnitReadState.objects.filter(user=user, unit_id=OuterRef("pk"))))
    .order_by("module__position", "position")
    .filter(is_read=False)
    .first()
)
```

- Some units read → CTA label **Continue**, target the unit page.
- No units read → label **Start course**, target the first unit.
- All units read, or `curriculum_format == "legacy"` (no modules/units), or
  no modules exist → label **Open course**, target the cohort page.

Note `UnitReadState.read_at` records the *first* read (idempotent
`get_or_create`, `courses/services/unit_read_state.py:27-31`), so
"first unread in order" is the correct resume semantics; do not sort by
`read_at` expecting last-visit behaviour. The portable per-cohort recency
query (for ordering multiple course cards) is `Max("read_at")` grouped by
cohort — the local database is SQLite, so **do not** use
`.distinct(field)`.

### Next deadline — per active cohort

Union of three date columns, **state-filtered** the way the reminder
queries do it (`courses/deadline_reminder_queries.py:51-101`) rather than
the unfiltered way `add_course_homepage_info` does
(`courses/views/course_homepage.py:60-127`) — the two existing surfaces
disagree, and this page means "due" as *actionable*, so filter:

- `Homework` with `state == OPEN` and `due_date >= now`, excluding homeworks
  the user has a `Submission` for;
- `Project` with `state == COLLECTING_SUBMISSIONS` and
  `submission_due_date >= now`, excluding the user's non-volunteer
  `ProjectSubmission`s;
- `Project` with `state == PEER_REVIEWING` and `peer_review_due_date >= now`.

Take the minimum per cohort. Render with the existing `user_datetime` filter
and the existing `time-left` element (`data-deadline`, as on
`courses/templates/courses/course.html`), which honours
`user.preferred_timezone`. If nothing qualifies, the deadline row is
omitted — not rendered empty (§2's hide-don't-explain rule).

### Progress

Reuse the cohort page's mechanics: `submission_progress(modules)`
(`courses/course_page_content.py:102`, consumed at
`courses/views/course_page_context.py:216-218`) for "N of M modules
submitted", and `Enrollment.total_score` / `certificate_url`
(`courses/views/course_page_context.py:95-113`) for finished courses. Both
should be extracted into the shared service rather than duplicated.

---

## 5. States, with exact copy

One template, four data-driven states. Sections render only when they have
content; the *only* written empty state is state 1's designed lead. The
"Getting started" checklist (§7) appears in **every** state until completed
or dismissed — it just stops being the lead once courses exist.

Common hero (cream band): eyebrow `<p class="mono-label mono-label-indigo">Home</p>`.

### State 1 — signed in, no course ties (the common case, and the onboarding surface)

Trigger: `my_cohorts` empty.

**Hero**

> h1: **Welcome to DataTalks.Club**
> lede: You're signed in. Pick a course and you're in it — everything here
> is free.

**Content band, in order:**

1. **Getting started checklist** (full spec §7.2). Lead position.
2. **Courses you can join today** — `band-head` h2 with band-link
   `all courses →` to `/courses`. Cards from the same classification the
   course list uses (`courses/views/course_list.py:82-105`): active cohorts
   first ("you can still join"), then open-registration cohorts. Card
   contents per `core/home_content.py:315-353` catalogue entries: title,
   `mono-label` cohort label ("2026 cohort"), start date when future, one
   `band-link` "View course". Cap at 4 cards; the full list is one click
   away.
3. **Upcoming events** — `band-head` h2 "Upcoming events" with band-link
   `see all events →` to `/events`; up to 3 `list-row`s (title link, date
   via the same `home_time` display the marketing home builds,
   `core/views.py:68-71`). Section omitted entirely when none.

Nothing else. The page must read as "two minutes and you're in a course",
not as an empty dashboard. There is deliberately no "your courses" heading
with placeholder text.

### State 2 — one or more active cohorts (the intended case)

**Hero**

> h1: **Welcome back**
> lede (exactly one active cohort): Pick up where you left off in
> {cohort.title}.
> lede (two or more): Pick up where you left off, or see what's due next.

Hero actions (actions belong to the title block —
`_docs/design/design-system.md`), only when **exactly one** active
cohort: primary `cta cta-ink interactive-lift` labelled per §4's Continue
rules ("Continue" / "Start course" / "Open course") linking to the resume
target — `cta-ink` is documented as the "Continue course" variant
(`design-system.md`); secondary `cta cta-secondary cta-compact`
"Course page". With multiple active cohorts the hero carries no CTAs; each
card carries its own.

**Content band, in order:**

1. **Your courses** — h2 `Your courses`. One `.card` per active cohort in
   `card-grid card-grid-2` (single card spans naturally at one). Each card:
   - h3: cohort title, linked to the cohort page;
   - `status-pill` (bare): `enrolled` when an Enrollment exists, else
     `status-pill status-pill-open`: `registered` — same vocabulary as the
     cohort page's own pills (`courses/templates/courses/course.html:451-456`);
   - progress bar + mono-note `{{ submitted }} of {{ total }} modules
     submitted` (module-format cohorts with an enrollment only; omit
     otherwise — never an empty track);
   - a `list-row`: `mono-label` **Next deadline**, then
     `{{ due|user_datetime }}` plus the `time-left` span; row omitted when
     no actionable deadline;
   - actions: `cta cta-compact cta-ink` Continue/Start/Open per §4, and
     `band-link` `Course page →`.
2. **Due soon** — only when **two or more** active cohorts (progressive
   disclosure: with one cohort the card's deadline row already says it).
   h2 `Due soon`, then a `row-list` of every actionable deadline in the next
   14 days across active cohorts, sorted ascending: each `list-row` has the
   assignment title linked to its homework/project page, a `mono-note` with
   the cohort's course title, and the date + `time-left`. Cap 6 rows.
3. **Getting started** checklist, if not yet complete/dismissed.
4. **Your finished courses** — only if any exist; as state 3's section.
5. **More free courses** — h2 with band-link `all courses →`; catalogue
   cards for families the user has no cohort in; cap 3. Omit when empty.
6. **Upcoming events** — as state 1.

**Progressive disclosure at volume:** with **four or more** active cohorts,
section 1 switches from cards to a `row-list` — one `list-row` per cohort
(title link · progress mono-note · next deadline · Continue as
`cta cta-compact`) — so five enrolments read as a table, not a wall of
cards. No other change.

### State 3 — only finished cohorts (returning learner between editions)

Trigger: `my_cohorts` non-empty, none active or upcoming.

**Hero**

> h1: **Welcome back**
> lede: Your last cohort has finished. Your scores and certificates stay
> right here — and the next editions are open.

**Content band, in order:**

1. **Your finished courses** — h2. `row-list`, one `list-row` per finished
   cohort, newest first: cohort title linked to the cohort page;
   `mono-label` **Total score** + value when
   `cohort.first_homework_scored` and an enrollment exists; `band-link`
   `Certificate →` to `certificate_url` when present
   (`courses/models/cohort.py:404-406`); and, when
   `next_edition_campaign_for_cohort(cohort)`
   (`courses/services/registration_campaigns.py:62`) returns a campaign, a
   `cta cta-compact cta-primary` **Register for the next edition** linking
   to `registration_campaign`.
2. **Getting started** checklist, if incomplete (a returning learner
   usually has it complete; it will normally be absent).
3. **More free courses** and **Upcoming events**, as state 2.

### State 4 — a registered cohort that has not opened (ml-zoomcamp-2026)

This is a *card variant* inside state 2/3's "Your courses" section, not a
page state: any cohort classified **upcoming** (§4) renders as:

- h3: cohort title, linked to the cohort page — safe even while empty,
  because an empty cohort page redirects non-staff to its campaign
  (`courses/views/course.py:14-26`);
- `status-pill status-pill-open`: `registered`;
- `list-row`: `mono-label` **Starts** + `{{ start_date|date }}`;
- note (`.dashboard-note` typography): *Materials aren't published yet.
  We'll email you when the course opens.* — true today: registration
  confirmation and course updates go through Datamailer
  (`courses/views/registration.py:88-100`);
- no progress bar, no Continue, no deadline row.

If the user's only cohorts are upcoming ones, the hero lede reads:

> You're registered for {cohort.title}. It starts {start_date} — we'll email
> you when materials open.

and section 2 of state 1 (courses to join) follows, so the page is not one
lonely card.

---

## 6. Layout

**Desktop (≥48rem).** Ordinary `content-shell` column (no `shell-breakout` —
nothing here is a wide table). Hero: eyebrow, h1, lede, action row
(`display:flex; gap:0.75rem`, the cohort page's `.course-actions` pattern).
Content: sections separated by the standard dashed rule the content page
already draws between siblings (`courses/templates/courses/dashboard.html:54-58`
shows the pattern); cards in `card-grid card-grid-2`; checklist and Due-soon
as `row-list`.

**Mobile (<48rem).** Everything single-column (card-grid collapses on its
own). Hero action row becomes `display:grid` so CTAs take full width — the
exact reshape the cohort dashboard already does
(`courses/templates/courses/dashboard.html:220-226`). Checklist rows stack:
title+description above, action + Skip below, each target ≥24×24 CSS px
(`.cta-compact` is 2.75rem tall — comfortably above the floor; the Skip
text-button must get explicit `min-height/min-width` to clear it).

**Components and tokens — reuse only, nothing new:** `band-head`, `card` /
`card-grid-2`, `row-list` / `list-row`, `status-pill` (+ `-open`), `progress`
/ `progress-fill` + `mono-note`, `mono-label` (+ `-indigo`), `cta` variants
(`cta-ink`, `cta-primary`, `cta-secondary`, `cta-compact`,
`interactive-lift`), `band-link`, `panel panel-lavender panel-outlined`
(nudges), `time-left`. All are documented in
`_docs/design/design-system.md` and defined in
`templates/core/_design_system.html`. No new colours, no new tokens, no
accent left-borders anywhere (`core/tests/test_design_accent_borders.py`
would fail the build regardless).

---

## 7. Onboarding

### 7.1 Principles (AISL's, adopted; two adapted)

1. **No wizard, no interstitial.** Signup already lands on `/`
   (`LOGIN_REDIRECT_URL`); the state-1 home *is* onboarding. AISL forces
   nothing post-signup either (`ACCOUNT_SIGNUP_REDIRECT_URL = '/'`,
   `ai-shipping-labs/website/settings.py:541-549`).
2. **Completion is derived, never a flag** — AISL derives "onboarded" from
   data existing (`ai-shipping-labs/questionnaires/onboarding.py:190-198`);
   here: profile-complete ⇔ `certificate_name` AND `country` AND
   `registration_role` all non-empty (all currently optional,
   `accounts/forms.py:85-95`, `accounts/models.py:24-46`).
3. **Every step is skippable, and skips are remembered server-side.**
4. **Never re-ask what a surface already learned.** Registration writes
   name/country/region/role back to the profile
   (`courses/views/registration_profile.py`, called from
   `courses/views/registration_form.py:94-103`) — verified in code — so a
   learner who arrives via a registration link and registers has the
   profile item auto-completed without ever seeing "onboarding".

### 7.2 The "Getting started" checklist (on the member home)

A single `.card`, h2 **Getting started**, subtitle line
*Set up your account*, progress bar + mono-note
`{{ done }} of 3 complete`. Three `list-row`s, each: bold title, one-line
description, action button (`cta cta-compact cta-secondary`), and — where
marked — a quiet `Skip` text-button.

| # | Copy (title — description) | Action | Completes when | Skippable |
| --- | --- | --- | --- | --- |
| 1 | **Pick your first course** — Free, cohort-based, with homework, projects, and a certificate at the end. | `Browse courses` → `/courses` | `my_cohorts` non-empty (derived) | yes |
| 2 | **Join the DataTalks.Club Slack** — Where course questions get asked and answered. | `Join Slack` → `/slack` | on click (persisted key) — no membership signal exists in this codebase, unlike AISL's `slack_member` | yes |
| 3 | **Tell us about you** — Your name, country, and role prefill every course registration. Enter them once. | `Add your details` → `/accounts/welcome/` | profile-complete (derived, §7.1.2) | yes |

When all three are complete or skipped, the card swaps once to:

> **You're ready to go** — Your courses and deadlines will appear here.

with a dismiss control (×, ≥24px target) that hides the card permanently.
AISL offers the dismiss only at this point too
(`_dashboard_commitment_zones.html:11-19`).

**Storage:** new `CustomUser.home_dismissals = models.JSONField(default=dict,
blank=True)` mirroring AISL's `user.dashboard_dismissals`, written through a
POST endpoint (suggested `/accounts/home/dismiss/`, `@login_required`,
allowlisted keys only, AISL precedent
`ai-shipping-labs/accounts/views/account.py:687-761`). Keys:
`getting_started_skip_course`, `getting_started_slack_done`,
`getting_started_skip_slack`, `getting_started_skip_profile`,
`getting_started_checklist` (the final dismiss), `profile_nudge` (§9).
Progressive enhancement: each Skip is a real form POST that redirects back
to `/`; JS may make it inline later.

### 7.3 The "About you" page — `/accounts/welcome/`

One slim page owning the **person-level** fields (owner: "linkedin, about
etc — should be a part of onboarding"). Not the settings page, deliberately:
`/accounts/settings/` couples in remote Datamailer email preferences that
can 503 when the mailer is unreachable
(`accounts/views/email_preferences.py:36-50`) and mixes five concerns; the
first thing a new member completes must be small and cannot depend on a
second system. New URL — additive, conflicts with nothing in the baseline.

Auth-page family layout (`_docs/design/design-system.md` — cream
hero, lavender decision area, `_entrance_styles.html`), since this is an
account-entrance page in spirit. `@login_required`.

> eyebrow: `Account` · h1: **About you** ·
> lede: Three details that follow you into every course — and a few
> optional ones. Everything here can be changed later in account settings.

Form (all fields save-if-present; nothing required):

- **Certificate name** — help: *Used on your certificates, across all your
  course enrollments.* (existing help text, `accounts/forms.py`)
- **Country** — the registration form's combobox widget
  (`country_combobox.js`), help: *Used to prefill course registration.*
- **Role** — the `CourseRegistration.Role` choices
  (`courses/models/cohort.py:309-325`), stored in
  `CustomUser.registration_role`.
- Folded `row-fold` disclosure, summary **Add links and a bio (optional)** —
  GitHub URL, LinkedIn URL, Website URL, About me. Collapsed by default:
  these matter only when a learner opts into public visibility
  (`Enrollment.display_public_profile` defaults False,
  `courses/models/cohort.py:389`), and about-me is the classic
  skip-and-never-return field. They live here so onboarding *owns* them, but
  they do not tax the primary path.

Buttons: `cta cta-primary` **Save** (→ `/` with the checklist item now
complete if the three core fields are set) and a plain link **Skip for
now** (→ `/`; does *not* write the skip key — skipping the page and
skipping the checklist item are separate acts). The page is trivially
resumable because it is stateless: saved fields are saved.

**Not asked, and why:** timezone — detected passively from the browser and
overridable in settings (`accounts/forms.py:63-75`,
`update_timezone_preference` wiring on `<body>`,
`templates/core/content_page.html:37-48`); dark mode — a toggle in the
masthead already; email preferences — Datamailer-owned, settings only;
company — per-registration, not per-person (§8.3).

### 7.4 Existing signals — prefill, never re-ask

When the account has a linked GitHub `SocialAccount`, prefill **GitHub URL**
from its `extra_data` profile URL; when Google or Slack is linked and
provides a name, prefill **Certificate name** — prefill only, blank fields
only, never overwriting (AISL's `populate_name_from_social` rule,
`ai-shipping-labs/accounts/signals.py`). **Honestly scoped:** because social
*signup* is closed (§1.2), today these prefills fire only for users who
linked a provider after creating an email account; until §13-Q2 is decided
this is a small population. The design costs nothing either way.

### 7.5 Field ownership (the single source of truth)

| Field | Owner surface | Asked where | Notes |
| --- | --- | --- | --- |
| email | account | signup | never re-asked; registration shows it read-only |
| password | account | signup | |
| certificate_name | onboarding | `/accounts/welcome/`; inline at registration if missing | write-back from registration exists today |
| country, region | onboarding | same | region derived server-side (`region_for_country`) |
| registration_role | onboarding | same | |
| github/linkedin/website/about_me | onboarding | `/accounts/welcome/` folded disclosure; settings | never in registration |
| preferred_timezone | settings | never asked; browser-detected | |
| dark_mode | settings/masthead | never asked | |
| company_name | **registration** | every registration | per-registration by design — jobs change between cohorts |
| comment | **registration** | every registration | |
| accepted_newsletter | **registration** | every registration | consent; never prefilled, never carried over (§8.4) |
| email subscription toggles | settings | never in onboarding | remote (Datamailer) |

Ordering consequence, resolved: **registration never blocks on onboarding.**
If the person-level fields are missing at registration time, registration
asks for exactly the missing ones inline (§8.2) and writes them back — the
mechanism already in production code.

---

## 8. Course registration

### 8.1 Today, verified

`/courses/register/<campaign_slug>/` (`courses/urls.py:29-33`,
`courses/views/registration.py:140-175`) renders marketing + a 7-field form
(email, name, company, country, role, comment, newsletter-consent — the
consent currently **required-to-tick**,
`courses/views/registration_form.py:106-169`). Signed-in users get email
locked and name/country/role prefilled; profile write-back on save;
anonymous users get a bare "Log in to use your account email" link. Success
re-renders in place as "You are registered". Registration creates **no
Enrollment** and there is **no hand-off into the course** beyond a hero
"Start course" button when the cohort has content
(`courses/views/registration.py:31-39`).

### 8.2 The signed-in path — "One final step"

The marketing half of the page is untouched. The form panel
(`courses/templates/courses/register.html:456-560`) is replaced for
authenticated users by one compact card:

> h2: **One final step**
> intro: You're registered in ten seconds — check your details and go.

1. **Identity block** — `dl.row-list.fact-rows` of read-only rows for
   **Name**, **Email**, **Country**, **Role**, filled from the profile.
   Below it one `row-fold` disclosure, summary **Edit these details**, which
   expands the same three editable fields the form has today (name text
   input, country combobox, role select), prefilled. This is
   shown-and-correctable — a stale job title or country is one click from
   fixable — without reading as a form to fill in again. Email is never
   editable here (`clean_email` already forces the account email,
   `courses/views/registration_form.py:143-147`); keep the existing escape
   hatch line *Log out to use a different email address.*
2. **Missing details** — any of name/country/role that are empty render as
   open fields above the fold, under the line *A couple of details we don't
   have yet:*. This is onboarding inlined at the moment of intent (§7.5);
   saving still writes back to the profile, so they are never asked again.
3. **About this registration** — always-visible per-registration fields:
   *Company (optional)*, *Anything you'd like to add? (optional)* (the
   existing textarea).
4. **Newsletter consent** — §8.4.
5. `cta cta-primary` **Register** (keep the existing
   `data-registration-submit` busy-state contract).

**After submit**, the existing "You are registered" card gains a hand-off —
closing the gap the current page leaves:

> **You are registered** — We will send course updates to {email}.
> [Start course] (existing, when the cohort has content) ·
> [Go to your home →] (`/`)

The member home then shows the cohort as a state-2 or state-4 card, so
"register → land somewhere that knows about it" finally holds.

### 8.3 The signed-out path — sign in first (the gate)

**Decision: registration requires an account.** For anonymous visitors the
form panel is replaced by:

> h2: **Create your free account to register**
> One account covers everything: registering, homework, the leaderboard,
> and your certificate. Takes about twenty seconds.
> [`cta cta-primary` **Create account**] → `/accounts/signup/?next={{ request.path }}`
> Already have one? [Sign in] → `/accounts/login/?next={{ request.path }}`

The round-trip works with existing primitives: `safe_next_path`
(`accounts/navigation.py:147-167`) honours a local `?next=` and only forces
`/` for account-transition paths — verified in code — so after signup or
sign-in the visitor lands back on the campaign, now seeing §8.2's short
final step. The campaign's marketing content, video, and "N already
registered" count stay fully visible to anonymous visitors; only the form
is gated.

Why this is right, argued (this is the conversion-path decision):

- **The account is not an extra cost, it is a moved cost.** Doing anything
  after registering — submitting homework, appearing on the leaderboard,
  getting a certificate — already requires signing in. Gating moves the
  same twenty seconds earlier, to the moment of highest motivation, and
  buys a linked registration.
- **It is the standing product decision.** `open-decisions.md:84-101` and
  spec 04:139-157 already rule that registration is account-owned and
  "never creates an anonymous `CourseRegistration`" — and the owner's
  direction ("first we get the user to sign in and then ask them") says the
  same. This spec is the UX of that decision, not a new one.
- **Unlinked registrations are the present pain**: nullable
  `CourseRegistration.user`, email-matching heuristics in three views, and
  a profile that diverges from what registration collected.
- **Risk and mitigations.** The risk is drop-off between "Create account"
  and returning. Mitigations: signup is three fields; `next` is preserved
  end-to-end; the returned-to page asks only for what is still unknown; and
  the funnel is measurable today — `registration.submitted` is already
  recorded (`courses/views/registration.py:157-167`), so compare weekly
  registration counts across the change. Implementation lever: put the gate
  behind a settings flag (suggested `REGISTRATION_REQUIRES_ACCOUNT`,
  default on) so the owner can revert on evidence, not argument. What would
  make the gate genuinely cheap is one-click social signup, which is
  currently closed — §13-Q2.

### 8.4 Newsletter consent (compliance-critical)

- The checkbox is **always rendered unticked**, is **never prefilled** from
  the profile or any previous registration, and each registration stores
  its own fresh answer (the per-row `accepted_newsletter` field already
  exists, `courses/models/cohort.py:354`).
- **Consent stays required. OWNER RULING, 2026-09-02 — this question is
  closed.** The design originally recommended unbundling and making the
  field optional. The owner considered that and ruled against it: the
  newsletter subscription is the consideration for a free course — "they
  exchange their email for the course, that's how we can offer it for
  free". A person must not be able to register without ticking it.
- The field therefore remains required-to-tick (`clean_accepted_newsletter`,
  `courses/views/registration_form.py:164-169`), pinned by
  `test_registration_requires_newsletter_consent` and
  `test_registration_requires_only_email_and_newsletter_consent` in
  `courses/tests/test_registration_campaigns.py`. **Do not modify or delete
  those tests, and do not make the field optional.**
- Required does not mean pre-agreed: it stays **unticked on render** so the
  tick is an affirmative act, and the label must say plainly what is being
  agreed to — that this signs the person up for the weekly DataTalks.Club
  newsletter as well as their cohort's course email.
- Downstream suppression is already someone's contract (Datamailer sync,
  `courses/views/registration.py:88-100`); nothing here may write a consent
  the user did not give in this submission.

---

## 9. Events

**Verified: there is no internal per-event sign-up today.** Event
registration counts are imported from external providers
(`events/current_registration.py`), and the event Q&A flow is deliberately
account-free (`events/qna/views.py:82-90` participant tokens). So this
section is a *contract for when internal event sign-up lands*, plus a nudge
that can ship with it:

- **Signed in: one click.** A single "Sign up" button; pressed → confirmed
  in place ("You're signed up — see you there"). No form, no fields, ever.
  The owner's rule verbatim: "for events just sign up is enough".
- **Signed out:** the same auth-first pattern as §8.3, `?next=` back to the
  event page.
- **The profile nudge** — after the confirmation, one
  `panel panel-lavender panel-outlined`:

  > **One more thing** — add your name and role to your profile so events
  > and certificates have you right.
  > [Complete your profile] → `/accounts/welcome/` · [No thanks]

  Rules that keep it a nudge and not a gate: it renders only while the
  profile is incomplete (derived, §7.1.2) **and** `profile_nudge` is not in
  `home_dismissals`; **No thanks** writes `profile_nudge` permanently — one
  click, never shown again on any surface; completing the profile ends it
  without any click; it never appears as a modal, never interrupts the
  sign-up, and appears at most once per page. The same key governs any
  future nudge placement, so it can never stack.

---

## 10. Deliberately not on the signed-in home

- **Blog / podcast / wiki / books / sponsors / member stories bands** — the
  masthead reaches all of them in one click; on the member home they are
  the "changelog of features" the AISL audit warned against.
- **Leaderboard positions and aggregate cohort statistics** — the cohort
  page and `/courses/<f>/<y>/dashboard` own those; duplicating figures
  here creates a second surface to keep truthful.
- **The marketing hero, illustrations, and "Join" CTAs** — the member is
  already in.
- **An activity feed or notifications** — no data source exists; nothing
  speculative gets designed against data nobody can see.
- **Studio links** — the user menu already handles the staff case
  (`templates/core/_site_shell_head.html:164-177`).
- **Email-preference management** — remote-system dependency (503 risk,
  §7.3); settings own it.
- **Per-homework detail** — one actionable deadline per cohort plus the
  Due-soon list; the cohort page owns the full table.

---

## 11. Accessibility, dark mode, compliance

- **Colour:** no new colours; every pair used is an existing documented
  token pair. Measured (WCAG 2.x relative luminance, light theme):
  `--indigo` #5a62c4 on `--lavender-deep` #dfe2f7 = **4.11:1 — fails AA;
  not used anywhere in this design**. Used pairs: indigo on lavender
  4.69:1, indigo on cream 5.06:1, indigo on card 5.28:1, body-text on
  lavender 7.05:1, olive on sand 5.51:1 (`status-pill-wait`), green-ship
  on mint 5.92:1 (`status-pill-mint`) — all pass AA for their sizes. Dark
  theme comes free by using tokens only (`body.dark-mode` restates every
  token, `templates/core/_design_system.html:153+`); the implementer must
  still eyeball both themes since the pages carry no per-page colours.
- **No accent left-borders** — nothing in this design draws one; the panel
  and status-pill primitives carry state instead
  (`core/tests/test_design_accent_borders.py` enforces).
- **Target size:** every control is `.cta`/`.cta-compact` (≥2.75rem) or a
  link in a `list-row`; the checklist **Skip** and nudge **No thanks**
  text-buttons need explicit `min-height: 24px; min-width: 24px` padding —
  `playwright_tests/accessibility_support.py:348-380` will fail them
  otherwise.
- **Structure:** one h1 per state; sections labelled by their h2 via
  `aria-labelledby`; progress bars use the documented
  `role="progressbar"` + mono-note pairing (`design-system.md`);
  `row-fold` disclosures are native `<details>`; no breadcrumbs (§4).
- **The masthead and nav are untouched** — `core/tests/test_design_system_shell.py`
  compares the nine entries and the user-menu copy across every page, and
  `content/tests/test_band_grounds.py` pins the cream-then-lavender band
  sequence the new template must follow.
- **Public URLs:** nothing in the 2,937-row baseline changes; additions
  (`/accounts/welcome/`, the dismiss endpoint) must not shadow a baseline
  path (`email_app/tests/test_link_contract.py:85-97`) — neither does.

---

## 12. Verification on a database with zero personal rows

Nobody — implementer, tester, or owner — can see states 2–4 without
manufacturing data. Each state has a recipe; all were checked against the
code paths named, and state 1 was verified live today.

- **State 1** — sign in as the local admin (it has no course ties). This is
  the default state of every local account. *Verified live.*
- **State 2 via the UI only:** while signed in, open an active cohort (e.g.
  `/courses/llm-zoomcamp/2026`), open any unit page and mark it read
  (creates `UnitReadState`); open a homework page (the lazy
  `Enrollment.get_or_create` at `courses/views/homework_context.py:242`
  fires). The member home must now show an `enrolled` card with progress,
  a Continue targeting the first unread unit, and the next open homework
  deadline.
- **State 2, registered-not-enrolled variant:** submit
  `/courses/register/ml-zoomcamp/` while signed in (creates a
  `CourseRegistration` locally) — but note locally that cohort classifies
  as *upcoming*, which is exactly the state-4 card. For an
  active-registered card, create a registration row for an active cohort in
  `manage.py shell`.
- **State 3:** `manage.py shell`:
  `Enrollment.objects.create(student=u, course=Cohort.objects.get(course__slug="mlops-zoomcamp", identifier="2025"), total_score=87, certificate_url="https://example.com/cert.pdf")`
  — that cohort is `finished=True` locally (it renders under "Finished
  courses" on `/courses`, verified live).
- **State 4:** register for ml-zoomcamp via the UI as above; locally the
  cohort has `registration_url` + future `start_date`, so it classifies
  upcoming. *The production claim (9 enrolments, 0 questions) is
  unverifiable locally; the local shape is the same class and is what the
  tester screenshots.*
- **Checklist/dismissals:** exercise Skip on each row and confirm the keys
  persist across sign-out/sign-in (they are DB rows, not localStorage).
- **Caching contract:** `curl -sI localhost:8000/` (no cookie) → no
  `Set-Cookie`; with a session cookie → `Cache-Control: private, no-store`.
  Plus the three tests in §3.

---

## 13. Open questions, each with a recommended default

1. ~~**Newsletter checkbox: optional or keep required-to-tick?**~~
   **CLOSED by owner ruling, 2026-09-02: keep it required-to-tick.** The
   newsletter subscription is the consideration for a free course. The
   concern about bundling was raised explicitly and overruled; the decision
   is the owner's to make. See §8.4 — the field stays required and stays
   unticked on render. Do not reopen this.
2. **Open social signup?** The gate (§8.3) is markedly cheaper if "Continue
   with Google/GitHub/Slack" can create accounts; today the adapter forbids
   it (`accounts/auth.py:261-263`) for identity-consolidation reasons.
   Default: **separate groomed issue; do not block this work on it** — the
   email path suffices meanwhile.
3. **Greet by name?** Default: **no** — `certificate_name` is optional,
   legal-name-flavoured, and absent for most accounts; "Welcome back"
   without a name is honest everywhere.
4. **Add a "Home" item to the user menu?** Default: **no** — the brand link
   already goes to `/`, and the menu copy is pinned by the shell test;
   revisit only if users demonstrably can't find their way back.
5. **Count email-matched registrations with no `user` FK as "my courses"?**
   Default: **yes** (same `Q(user)|Q(email_normalized)` rule the
   registration views use), becoming moot when `CourseRegistration.user`
   turns non-nullable per open-decisions §6.
6. **Slack checklist completion** has no signal (no `slack_member` field
   here, unlike AISL). Default: **complete-on-click + Skip**; a real signal
   arrives, if ever, with the MemberProfile/Slack work
   (open-decisions §19).
7. **Should signed-in users be able to reach the marketing homepage?**
   Default: **no dedicated path**; all of its content is in the masthead.
   Cheapest later fix is a footer link.
8. **Due-soon window and caps** (14 days / 6 rows / 4 catalogue cards /
   3 events). Defaults as stated in §5; all are one-constant changes and
   none is worth blocking on.
