# Homework page design specification

Page under review: `/courses/llm-zoomcamp/2026/homework/homework-01`
(route `courses/urls.py:149`, template `courses/templates/homework/homework.html`,
shared parent `courses/templates/courses/_submission_page.html`, form partial
`courses/templates/homework/_submission_form.html`).

Written by the design agent, 2026-09-02. Screenshots used while forming this:
desktop 1280px and mobile 390px, signed-out, deadline-passed state (the state the
owner saw — today is 2026-09-02, HW1 deadline was 2026-08-31).

---

## 1. Compliance finding: the accented border

### What the homework page actually draws

The accent border the owner sees on this page is the **callout tone rail**:

- `templates/core/_design_system.html:2763` — `.callout { border-inline-start: 0.35rem solid var(--indigo); }`
- `templates/core/_design_system.html:2773` — `.callout-info { border-inline-start-color: var(--indigo); }`
- `templates/core/_design_system.html:2778` — `.callout-attention { border-inline-start-color: var(--olive); }`
- (`.callout-quiet` at `:2783` removes it, which is why the two stacked hero
  callouts on this page have visibly different treatments.)

The homework page renders up to three of these callouts through
`core/_callout.html` (homework.html:75–117). The signed-out/deadline-passed
state shows the indigo-railed "The deadline has passed…" callout — that rail is
the violation on this page.

The two locations the owner's brief guessed (`_design_system.html:2811`/`:3292`,
now at `:2817`/`:3298` after drift) are real but are **not** what the homework
page draws — they are the events date rail and the prose blockquote (below).

### Is the prohibition written down? No — and that is itself a defect

I searched `_docs/design/design-system.md`, `_docs/design/*`, `_docs/specs/*`, the
design system's own comments, git history, and GitHub issues. There is **no
written rule forbidding accented borders.** The closest documented artifact is
the border enumeration at `_docs/design/design-system.md`:

> **Borders**: `2px solid var(--line)` on components; `1.5px` on chips, status
> pills and inner dashed rules; `3px solid var(--ink)` only on the featured
> slab; `2px dashed var(--line-soft)` for band and masthead separators.

That is a closed list, and a coloured single-side accent rail is not in it — so
the treatment was always outside the sanctioned set. But the same document
*endorses* two accent rails elsewhere: "a `--bubble`-ruled italic `blockquote`"
(`design-system.md`) and the events date rail "A `--bubble` left rule…"
(`design-system.md`), and the mockup source itself draws the date rail
(the mockup source, since deleted). So the design system
is internally inconsistent: the border law excludes the treatment; two later
sections bless it; the `.callout` rail is documented nowhere at all (the word
"callout" does not appear in design-system.md).

**Recommendation:** whatever the owner decides about scope (see §5), write the
rule into `design-system.md`'s border section explicitly — e.g. "No coloured
single-side accent borders (tone rails, left rules). Tone is carried by surface
tint and words, never by an accent stripe." — and update `design-system.md` and
`:1028` in the same commit. An unwritten rule that three different primitives
violated independently is a design-system defect, not three engineer errors.

### Complete inventory of the treatment

Accent rails (coloured single-side border used decoratively / as tone):

| Location | Rule | Surface |
| --- | --- | --- |
| `templates/core/_design_system.html:2763` (+`:2773`, `:2778`) | `border-inline-start: 0.35rem solid var(--indigo)`/`--olive` | `.callout` — homework, project, eval pages (`courses/templates/homework/homework.html`, `homework/_submission_form.html:124`, `projects/project.html`, `projects/eval_submit.html`) |
| `templates/core/_design_system.html:2817` | `border-left: 4px solid var(--bubble)` | `.when` events-index date rail |
| `templates/core/_design_system.html:3298` | `border-left: 4px solid var(--bubble)` | `.prose blockquote` — every prose surface (unit pages, blog, wiki, FAQ) |
| `templates/public/book_detail.html:152` | `border-left: 4px solid var(--bubble)` | `.book-replies` — answers hang off a question ("the same mark the events date rail uses", its own comment says) |
| `courses/templates/courses/_module_rail_styles.html:47` | `border-left: 0.3rem solid var(--indigo)` | `.sidebar-unit.is-current` — module/unit rail current item (**unit-page agent's territory**, flag to them, do not fix here) |
| `events/static/events/qna/qna.css:21` | `border-left: .25rem solid var(--qna-accent)` | Q&A banner |
| `events/static/events/qna/qna.css:40` | `border-left: .25rem solid #a31d31` | Q&A error callout — **also an inline hex**, double violation of `_design_system.html:21` |

Not offenders (neutral structural separators in the sanctioned dashed/`--line-soft`
language, listed so the implementer doesn't over-delete):
`templates/review/course_cohort.html:90`, `templates/core/home.html:801`,
`courses/templates/courses/course.html:125`, `templates/public/event_detail.html:137`
(all `--line-soft` hairlines), `templates/core/home.html:243` (schedule tick).

### What replaces the rail (in `.callout`, the piece this page owns)

Tone is carried by **surface tint + words**, which is already the system's own
principle ("State is never colour-only — pills and dots always carry" words,
`design-system.md`). New `.callout` (edit `_design_system.html:2761-2785`):

```css
.callout {
  background: var(--card);
  border: 2px solid var(--line-soft);
  border-radius: 0.7rem;
  color: var(--body-text);
  display: grid;
  gap: 0.45rem;
  padding: 1rem 1.15rem;
}
/* callout-info: no extra rules — the white card IS the info surface */
.callout-attention {
  background: var(--sand);
}
.callout strong { color: var(--ink); }
.callout-attention strong { color: var(--olive); }
```

Delete `.callout-quiet` (`:2781-2785`) and its uses (`homework.html:87`,
`_submission_form.html:124`): with the rail gone there is nothing for "quiet" to
remove, and the two-callout-weights problem disappears with it.

Measured contrast (WCAG 2.2 AA, threshold 4.5:1 normal text):
light — `--body-text` on `--card` 7.93:1, `--ink` on `--card` 16.9:1 (trivially
passing), `--olive` on `--sand` 5.51:1, `--body-text` on `--sand` 6.6:1 (all
computed from the token hexes at `_design_system.html:42-141`);
dark — `--body-text` on `--card` 7.75:1, `--olive` on `--sand` 7.42:1,
`--muted` on `--sand` 6.15:1. All pass.

The blockquote (`:3298`), `.when` (`:2817`), `.book-replies`, and qna.css rails
are **outside this page's scope** — see §5 for the owner decision they need. Do
not change them as part of the homework issue; one primitive per owning issue
(`AGENTS.md`: product-domain behavior is introduced only by its owning issue).

---

## 2. Critique of the current page

Judged against the page's job: *a learner arrives to learn what the homework
asks, when it is due, and how to submit.*

1. **The hero is a message board.** The cream band carries: h1 → "Instructions
   on GitHub" button → deadline row → timezone paragraph → up to three stacked
   callouts (`homework.html:75-117` renders them inside `hero_notices`, which
   `_submission_page.html:146-178` places in the *header band*). On a 390px
   phone the questions start below the second screenful. State notices are not
   header content; they gate the form and belong beside it.

2. **The state messaging contradicts itself and repeats itself.** Signed-out
   after the deadline the page says, in order: "Deadline passed" (mono text from
   `time_left.js:11`), "The deadline has passed, but this homework is still
   open… submit your answers below" (`homework.html:90`), and then presents a
   **fully disabled form** (`homework_context.py:117` — signed-out is always
   `disabled=True`). "Submit below" over controls you cannot touch is actively
   wrong; the fact "deadline passed" is stated twice in adjacent blocks.

3. **Two callout weights for peer messages.** `.callout-quiet` (white card, no
   rail — `homework.html:87`) sits directly above `.callout-info` (lavender,
   indigo rail — `homework.html:90`). Same kind of message, two visual
   languages, stacked.

4. **The forbidden accent rail** (§1).

5. **The form surface is invisible.** `.cmp-form` paints `--lavender` with a
   `--line-soft` border (`_design_system.html:2551-2560`) *on the lavender
   content band* (`.band-lavender`, `:958`) — a lavender box on lavender ground,
   read only by its faint outline. Meanwhile the **disabled empty textarea** is
   the darkest object on the page: `.form-control` keeps its `2px solid
   var(--line)` ink border even when disabled (`homework.html:227`,
   `_design_system.html:2603` — disabled state changes only background/color,
   `:2612-2618`). Visual weight is exactly inverted: the dead control shouts,
   the live surface whispers.

6. **Style duplication with drift.** `homework.html:177-270` re-declares
   `.form-check`, `.form-check-input`, `.form-check-label`, `.form-control`,
   `.invalid-feedback`, `.readonly-value` — all already in the design system
   (`_design_system.html:2600-2671`) — with drift: disabled text is `--olive`
   in the page copy (`homework.html:239`) but `--muted` in the system
   (`:2617`). `.homework-status`/`.homework-submit` (`homework.html:271-285`)
   duplicate `.submission-status`/`.submission-form-actions` from the parent
   (`_submission_page.html:90-104`), and the markup carries both class sets
   (`_submission_form.html:197,212`). This duplication is *how* pages drift
   from the system; the git history shows this page has been patched twice for
   exactly that ("Remove decorative homework question borders", 625609e).

7. **The breadcrumb loses the module.** Trail is Courses / LLM Zoomcamp / 2026
   (`_submission_page.html:147-161`; the `breadcrumbs` block at `:158` is left
   empty by homework.html). A learner arrives from a unit page whose rail links
   "Homework 1: Agentic RAG" (`_module_rail.html:46-49`) — and the module
   context vanishes. The concurrent unit-page redesign settles the trail as
   Courses / family / identifier / module, current page as the h1 only
   (rationale comments in the modified `courses/templates/courses/unit.html:99-115`).
   Homework should read as the same family.

8. **No route back to the module.** The module page and rail both link *to* the
   homework; the homework page links back to nothing but prev/next homework
   (`_submission_page.html:186-206`). The learner's loop (module → units →
   homework → back) has a dead end at its last stop.

9. **Signed-out radios are near-invisible.** 1.1rem grey discs
   (`_design_system.html:2662-2667`), further dimmed by the browser's disabled
   rendering, floating on lavender.

Data problems, not design (fix is different, flag to the data owner):

- `homework.description` is empty for every llm-zoomcamp/2026 homework, so the
  hero lede (`homework.html:28-32`) and the meta description (`:14`) render
  empty. The design below keeps the lede slot; it will work when data arrives.
- The known escaped-raw-HTML content bug does **not** apply here: this page
  renders model fields (`question.text`, `option.value`), not the markdown
  pipeline, and I verified HW01–HW04 question/option text renders clean. Caveat,
  honestly: if upstream homework markdown ever carries backticked code in
  question text it will print literally (there is no code styling on
  `.question-text`); nothing observed today.

---

## 3. The proposed design

Principle: the hero states **facts** (what, when); the content band holds **one**
state notice, the form, and navigation. Never more than one state callout;
POST feedback (`messages`/`errors`) may add above it. Main content sits directly
on the band, like unit prose — no box around the page's whole purpose.

### 3.1 Hero (cream band) — facts only

Top-to-bottom, all in existing blocks of `_submission_page.html`:

1. **Breadcrumb** — add the module crumb via the `breadcrumbs` block
   (`_submission_page.html:158`): `Courses / {{ course_family.title }} /
   {{ course.identifier }} / {{ module.title }}` where module =
   `homework.terminal_module` (reverse of `Module.terminal_homework`,
   `courses/models/curriculum.py:25-29`; related_name `terminal_module`).
   Guard: only module-format cohorts have one — the view context needs a
   `homework_module` variable added in `homework_context.py` (both the
   authenticated and unauthenticated builders), `None`-safe. No self-crumb —
   matches the unit-page agent's "trail stops at the parent" decision.
2. **h1** — unchanged (`homework.html:24-26`).
3. **Lede** — unchanged slot (`homework.html:28-32`); empty today (data gap).
4. **Deadline row** — keep `homework.html:44-49` exactly: `.mono-label`
   "deadline" + `<strong>` date + `.time-left` (mono 0.78rem; `--muted` normal
   / `--olive` 700 warning / `--ink` 700 overdue — all pass on cream: 5.94,
   6.02, 16.1). Shrink the timezone note: replace the `.submission-support`
   paragraph (`homework.html:50-57`) with the same copy at `.field-hint` scale
   (`--muted` 0.8rem, `_design_system.html:2620-2623`), keeping the
   account-settings link for signed-in users. `--muted` on cream = 5.94:1 ✓.
5. **Instructions CTA** — keep the secondary compact button
   (`homework.html:34-42`), moved *below* the deadline row so the reading order
   is what-when-act. It stays the hero's only CTA.
6. **Studio/statistics actions** (`homework.html:60-73`) — unchanged, they are
   role-scoped and rare.

**Removed from the hero:** the entire `hero_notices` block content
(`homework.html:75-117`) moves to the content band (§3.2). This is also the fix
the 2026-08-26 audit made once before (commit e59a786's message: "moved the
state notices into the content band instead") which the current template has
regressed from.

### 3.2 Content band (lavender) — notice, form, navigation

Order inside `page_content`:

1. **One state callout** (restyled per §1), width `var(--form-measure)` (46rem,
   `_design_system.html:80`), margin-bottom 2rem. It must render even when the
   form does not — move it *outside* the `{% if accepting_submissions or … %}`
   gate at `_submission_page.html:182`, so a closed homework never produces an
   empty lavender band. State table in §3.4. `messages`/`errors` POST feedback
   (`homework.html:77-84`) render above it, same styling, `role="status"`/
   `role="alert"` kept.
2. **Questions section** — `.band-head` h2 "Questions" (or "Correct answers"
   when scored & signed out, as now — `_submission_form.html:10-14`). Delete
   the boilerplate support line "This form is only for submitting your answers…"
   (`_submission_form.html:19-24`) — the hero's Instructions button already owns
   that job; keep only the scored-signed-out variant line ("Review the expected
   answers…").
3. **Question fieldsets** — see §3.3.
4. **Submission details** (signed-in only) — `.band-head.band-head-secondary`
   h2, fields unchanged (`_submission_form.html:128-196`).
5. **Status + actions** — `.submission-status` line and
   `.submission-form-actions` dashed-top row with the primary Save/Update
   button, as now (`_submission_form.html:197-219`) but using only the parent's
   classes (drop `homework-status`/`homework-submit` duplicates).
6. **Navigation** — `.submission-navigation` dashed-top row
   (`_submission_page.html:187-204`): **add** a leading "← Back to
   {{ module.title }}" subtle-variant compact button (guarded on
   `homework_module`), keep "← Previous homework" / "Next homework →" as
   secondary compact buttons. On mobile the row grids to full-width stacked
   buttons (parent already does this at `:130-136`).

**The form box goes away.** Edit `.cmp-form` (`_design_system.html:2551-2560`):
remove `background`, `border`, `border-radius`, `padding`; keep `display:grid`,
`gap`, `max-width: var(--form-measure)`, `width:100%`. The comment above it
(`:2547-2550`) says the class exists so "a different field set cannot create a
different page shape" — that intent (one measure, one rhythm) survives; only the
invisible lavender-on-lavender box painting is removed. This applies uniformly
to the project and eval pages that share the class — same parent, same band,
same problem. Note: `courses/tests/test_submission_design.py` asserts on this
family of styles and will need updating.

### 3.3 Questions

- **Fieldset** — keep the borderless `.question` grid (`homework.html:151-159`;
  borders were deliberately removed in commit 625609e — do not reintroduce any).
  Raise inter-question gap: `.submission-fields` gap 1.5rem → 2rem
  (`_submission_page.html:76-80`) so questions read as units without boxes.
- **Legend** — keep `.question-text` (`--ink`, 700) with the counter and the
  `(1 point)` / `(not graded)` `.question-note`. *Proposed, not derived*: set
  `.question-note` to `.mono-label` treatment (mono 0.66rem uppercase
  `--muted`) so the metadata reads as metadata, matching the deadline label's
  language. `--muted` on lavender 5.50:1 ✓.
- **Options** — `.form-check` rows unchanged (min-height 2.75rem = the tap
  target). Bump `.form-check-input` to 1.25rem × 1.25rem (design-system edit at
  `_design_system.html:2665-2666`, delete the page copy). `accent-color:
  var(--green)` stays.
- **Disabled/preview state** — quiet, not loud: add to the system's disabled
  rules (`_design_system.html:2612-2618`) `border-color: var(--line-soft)`.
  Disabled text `--muted` on `--sand` = 5.44:1 ✓ (and delete the page's
  drifted `--olive` copy, `homework.html:236-239`).
- **Graded marks** — keep the ✓/× label suffixes (`homework.html:194-209`;
  `--green-ship` 5.83:1 and `--olive` 5.57:1 on lavender ✓) and the
  Correct/Incorrect `status-pill-mint`/`status-pill-wait` pills
  (`_submission_form.html:117-121`) — pills carry words, per
  `design-system.md`. Keep `.no-answer-indicator` as is (`homework.html:216`).
- **Cleanup** — delete from `homework.html` every rule duplicated from the
  design system: `.form-check*` (:177-193), `.form-control*` (:225-250 —
  keeping the two `option-answer-*` border-color rules, which are page-specific
  grading states), `.invalid-feedback` (:252-260), `.readonly-value`
  (:262-270), `.homework-status` (:271-276), `.homework-submit` (:278-285).
  What legitimately stays page-local: `.time-left` states, `.homework-deadline`,
  `.question*`, `.no-answer-indicator`, the `option-answer-*` marks.

### 3.4 State matrix (one callout, exact copy)

| State | Callout tone | Content |
| --- | --- | --- |
| Signed out · open · before deadline | info (white) | **Log in to submit this homework.** "You can preview the questions below; submissions are saved only for logged-in students." + primary compact "Log in to submit" |
| Signed out · open · after deadline | info (white) | **Log in to submit — late submissions are still accepted.** "The deadline has passed, but answers are accepted until the homework is closed for scoring. You can preview the questions below." + primary compact "Log in to submit" — *one* callout replaces today's two (`homework.html:85-91`) |
| Signed out · closed, not scored | attention (sand) | **This homework is closed.** "Log in to see the status of your submission." + link (today's `:100-102`) |
| Signed out · scored | info (white) | **Correct answers are shown below.** "Log in to see your own submission, score, and feedback." + primary "Log in to view my results" (today's `:113`) — options pre-checked with correct answers, as now |
| Signed in · open · before deadline | **no callout** | The default path is quiet: enabled form, status line, Save button |
| Signed in · open · after deadline | info (white) | **Still accepting late submissions.** "Your latest saved version will be scored when the homework closes." (do not restate "deadline passed" — the hero's `.time-left` already says it) |
| Signed in · closed · has submission | attention (sand) | **This homework is closed.** "Your saved submission is shown below and cannot be changed." |
| Signed in · closed · no submission | attention (sand) | **This homework is not open for submissions.** |
| Signed in · scored · has submission | info (white) | eyebrow `.mono-label` "Scored homework" · **Your submission has been graded.** · `.callout-score` "Score N" (today's `:107`) |
| Signed in · scored · no submission | attention (sand) | **This homework was scored without a submission from you.** |

The `time-left` mono text in the hero is the *only* other state voice on the
page; `time_left.js` needs no change.

### 3.5 Mobile (<48rem)

- Hero after the move: crumb, h1, deadline row (wraps via existing flex-wrap,
  `homework.html:144-149`), hint, one button. Questions reachable in one
  screen.
- Callout and form full width; `.submission-actions`/`.submission-form-actions`
  already grid to full-width buttons (`_submission_page.html:130-136`,
  `_design_system.html:2673-2681`).
- Navigation row stacks to full-width buttons (same rule); back-to-module first
  in DOM order so it's first in the stack.
- No `.shell-breakout` anywhere — nothing on this page is wide (rule:
  `AGENTS.md` page-shells; the questions column is `--form-measure`).

### 3.6 Accessibility

- Landmark/semantics unchanged: `fieldset`/`legend` per question, labels bound
  by `for`/`aria-labelledby` (`_submission_form.html:28-115`), breadcrumb nav,
  `role="status"`/`role="alert"` on POST feedback.
- Focus: system-wide `:focus-visible` 3px `--focus` outline
  (`_design_system.html:273-277`) covers all controls; the callout's login
  button is a real `<a class="cta">`, keyboard-reachable.
- All colour pairs stated above measured ≥4.5:1 in both themes (dark values:
  `--olive`#cbb98a on `--sand`#2a2740 7.42:1, `--body-text` on `--card`
  7.75:1, `--muted` on `--sand` 6.15:1, `--green-ship` on `--mint` 6.91:1).
- State is never colour-only: every state has words (callout title, pill
  labels, ✓/× are suffixes to labelled options and duplicated by the pills).

---

## 4. What I deliberately did not change

- **The page shell.** It correctly extends `content_page.html` through
  `_submission_page.html` (cream hero / lavender band / `.content-shell`);
  per `AGENTS.md` that is not negotiable and it is not the problem.
- **The form's field set, names, ids, disabled rules, CSRF, and JS hooks**
  (`homework.js`, `learning_in_public.js`, `time_left.js`) — the template's own
  header comment (`homework.html:4-9`) marks these as contract; nothing in this
  design touches them.
- **Questions stay borderless.** Commit 625609e removed decorative question
  boxes; I am not re-adding card chrome around questions. I also considered and
  rejected numbered discs on questions (echoing the rail's `.read-indicator`) —
  it is decoration the legend counter already provides.
- **`.prose blockquote`, `.when`, `.book-replies`, qna.css** — same forbidden
  treatment, different owners; inventoried in §1, decision in §5, untouched
  here so the fix lands issue-by-issue.
- **The module rail's `is-current` accent border**
  (`_module_rail_styles.html:47`) — the unit-page agent owns that file right
  now (uncommitted edits in this worktree); they need to know it carries the
  same violation, but two agents editing one file is worse than a handoff note.
- **`time_left.js` thresholds and copy** — behaviour is sensible; only its
  container moved visually.
- **Heading scale** — "Questions" at `.band-head h2` clamp(1.4–1.87rem) looked
  heavy to me at first, but it *is* the system scale (`_design_system.html:983-985`);
  local exceptions are how drift starts.

## 5. Owner decisions needed (not a designer's call)

1. **Scope of the prohibition.** Does "no accented borders" cover the
   `--bubble` blockquote rule (`_design_system.html:3298`) and the events date
   rail (`:2817`, mockup-derived, documented at `design-system.md`), and
   `.book-replies`? If yes, each needs a replacement design and its own issue
   (blockquote could take the `.panel` tint treatment; the date rail is load-
   bearing layout on /events). If no, the written rule must name the sanctioned
   exceptions. Either way the rule must be *written* (§1).
2. **Callout-info as a plain white card** — tone then lives entirely in words
   and the sand/white split. If the owner wants stronger tone signalling
   without a rail, the alternative is a `.mono-label` eyebrow per tone
   ("status" / "attention") baked into the partial. My recommendation is the
   quiet version; flagging because it reduces at-a-glance tone contrast.
3. **Late-submission copy.** "Log in to submit — late submissions are still
   accepted" states policy (late work counts until closing). Confirm that is
   the intended policy voice on a public page.
4. **`homework.description` data** — empty for llm-zoomcamp/2026; the lede and
   meta description render empty. Content owner, not this issue.
5. **Coordination with the unit-page agent** on the shared trail format
   (Courses / family / identifier / module) and on `_module_rail_styles.html:47`
   — their in-flight diff (uncommitted, this worktree) already matches the
   trail format proposed here; someone should own writing the shared decision
   into `design-system.md`.

## 6. Implementation notes (for the building agent)

Files to edit, in dependency order:

1. `templates/core/_design_system.html:2761-2785` — callout restyle (§1);
   `:2551-2560` — cmp-form unboxing (§3.2); `:2612-2618` — disabled
   border-color; `:2665-2666` — 1.25rem check inputs.
2. `courses/templates/courses/_submission_page.html` — move notices block into
   `page_content` above the `:182` gate; nav additions (§3.2.6); fields gap.
3. `courses/templates/homework/homework.html` — hero re-order (§3.1), notice
   consolidation to the §3.4 matrix, style deletions (§3.3 cleanup).
4. `courses/templates/homework/_submission_form.html` — drop boilerplate
   support line, drop duplicate classes, drop `callout-quiet` usage.
5. `courses/views/homework_context.py` — add `homework_module`
   (`homework.terminal_module`, None-safe) to both context builders.
6. `_docs/design/design-system.md` — write the border rule + callout primitive
   documentation (with the owner's §5.1 answer).

Tests that will notice: `courses/tests/test_submission_design.py`,
`courses/tests/test_homework.py`; project/eval pages share `.cmp-form` and
`_submission_page.html` — screenshot them in verification. The dashed
`--line-soft` separators, pills, buttons, and band system need no changes.
