# Course unit page — design specification

Designer pass for the course unit page (`courses/templates/courses/unit.html` and the
shared partials `courses/templates/courses/_module_rail.html`,
`courses/templates/courses/_module_rail_styles.html`). All paths below are relative to
the worktree `/home/alexey/git/dtc-website/.tmp/production-prep/` unless absolute.
Verified against the live pages
`http://localhost:8000/courses/ml-zoomcamp/2026/modules/01-intro/01-what-is-ml` and
`http://localhost:8000/courses/llm-zoomcamp/2026/modules/01-agentic-rag/01-intro`;
screenshots in `/home/alexey/git/dtc-website/.tmp/design-unit-page/`
(`ml-unit-desktop.png`, `ml-unit-mobile.png`, `llm-unit-desktop.png`,
`module-desktop.png`, `homework-desktop.png`).

This is a spec, not code. Every colour named here is an existing token from
`templates/core/_design_system.html:42-212`; **no new tokens are needed**, which
satisfies the "never an inline hex" rule (`_design_system.html:21`).

---

## 0. Verification of the reported problems (with corrections)

Everything reported was confirmed on the live pages, with two corrections:

1. **Prev/next navigation now exists.** `unit.html:163-178` renders a bottom
   `.unit-navigation` with prev (subtle), next (secondary) and, on the last unit,
   "Continue to homework →" (primary). The owner's "no next/previous anywhere"
   predates this. What remains wrong: the buttons carry raw upstream titles
   ("1.2 ML vs Rule-Based Systems →"), prev/next use unequal variants while the
   homework page uses secondary for both (`courses/templates/courses/_submission_page.html:194-202`),
   and the button floats at the far right of a column much wider than the prose
   (see §3.1). I ratify and refine rather than redesign this.
2. **A worse problem than the ones listed:** when signed in, the rail renders a
   full-width "Mark as read / Mark as unread" button under **every** row
   (`_module_rail.html:24-35`, `_module_rail_styles.html:105-112`, min-height
   2.75rem each). A 10-lesson module gives the rail ten buttons; a 20-lesson
   module gives it twenty. This dominates the density question and is addressed
   in §4.6.

Confirmed as reported:

- Double numbering: `_module_rail.html:16-18` prints `forloop.counter` in the
  circle while ML titles already carry "1.1", "1.2", … prefixes.
- Ragged data: row 6 of ML module 1 is "Setting up the Environment", no "1.6"
  prefix. LLM 2026 titles carry no prefixes at all ("Introduction",
  "Environment"). The raggedness is upstream; §7.1 normalises display.
- Sign-in stated twice in the rail (`_module_rail.html:7` sentence,
  `_module_rail.html:41-44` bordered button after all rows) while the masthead
  already offers "Log in" and "Sign up free" globally
  (`templates/core/_site_shell_head.html:132,189-191`).
- Sign-in flow bug: the rail button's `?next=` is `module_url`
  (`_module_rail.html:43`), so signing in from a unit returns the reader to the
  **module** page, not the unit they were reading. Verified in rendered HTML
  (`next=/courses/llm-zoomcamp/2026/modules/01-agentic-rag`).
- Homework as afterthought: `_module_rail.html:46-49`, uppercase label + wrapped
  link under the sign-in button, below the fold on the desktop screenshot.
- Chrome imbalance: the rail is a `.card` (2px ink border + drop shadow,
  `_design_system.html:1211-1222`) while the reading column is unframed.
- "Edit on GitHub →" is the first element of the content column
  (`unit.html:145-154`), set in `.band-link` indigo before the reader has seen a
  word of the lesson.
- Accent bar: `.sidebar-unit.is-current` draws `border-left: 0.3rem solid
  var(--indigo)` (`_module_rail_styles.html:47`). **This violates the owner's
  "no accented borders" rule.** The design system itself still contains two
  `border-left: 4px solid var(--bubble)` uses — `.when` at
  `_design_system.html:2817` and `.prose blockquote` at `_design_system.html:3298`
  — see §9 owner decisions.
- Module page duplication (coherence finding): `module.html:178-188` renders the
  same ten lessons in its main column that the included rail renders beside it —
  the list appears twice on one screen (`module-desktop.png`). See §9.

---

## 1. Design intent

One sentence: **the lesson is the page; everything else is quiet chrome.**

- The reading column carries the only framed/saturated elements (video frame,
  code blocks, images — content). The rail becomes an unframed navigation list,
  the same altitude as breadcrumbs — the system already states "a trail is
  chrome, not content" (`_design_system.html:1024-1031`); the rail is the same
  kind of thing.
- One global action (sign-in) appears locally at most once, as a quiet link, and
  only where it explains a visible gap (the absent read-state marks).
- The linear flow — read → mark read → next lesson → homework — is expressed
  once, at the end of the content column, in the same shape the homework page
  already uses.

---

## 2. Page structure

Top-level template structure of `unit.html` is unchanged: extends
`core/content_page.html` (per AGENTS.md), header band = breadcrumbs + h1
(**another agent's scope — untouched**), content band holds
`.module-layout.shell-breakout` with main column + rail.

`.shell-breakout` stays, with this stated reason for the template comment (AGENTS.md
requires one): *a reading column at the system's reading measure plus a
persistent module rail is wider than `--content-width` (40.5rem) but must not
widen the page shell; the article itself never exceeds the reading measure.*

### 2.1 Layout grid (shared, `_module_rail_styles.html:1-7`)

Replace:

```css
.module-layout {
  grid-template-columns: minmax(17rem, 21rem) minmax(0, 1fr);
}
```

with:

```css
.module-layout {
  grid-template-columns: minmax(16rem, 19rem) minmax(0, var(--content-width));
  justify-content: center;
  gap: 3rem;            /* was 2rem; the rail loses its card frame and needs air instead */
}
```

Why: today the main column stretches to ~53rem while the article inside is
38rem, so the next-button and video float far past the prose edge
(`llm-unit-desktop.png`). Capping the main track at `--content-width`
(40.5rem, `_design_system.html:77`) makes the unit column the **same felt width
as the homework page's** `content-shell` — a learner moving unit → homework sees
no change of shape (this is the system's own reading-width doctrine,
`_design_system.html:485-503`). `justify-content: center` centers the pair
(~62rem) inside the 76rem breakout. The rail narrows from 21rem to 19rem max:
it is chrome and should weigh less than the content.

This shared change also applies to `module.html`; a 40.5rem lesson list is fine
there (titles + state, cf. the same doctrine comment about the course page).

---

## 3. The content column (`.unit-main`)

Order, top to bottom:

1. **Video frame** (`unit.html:133-144`) — unchanged treatment
   (`.unit-video`, card border + `--shadow-soft`, 16:9). It now fills the
   40.5rem column naturally; no rule change needed beyond the grid fix. A video
   is content, so it may keep its frame.
2. **Article** — `article.prose.prose-reading.unit-content`, unchanged classes.
   `.prose-reading` holds 38rem / 1.05rem / 1.75 (`_design_system.html:3224-3230`).
   The body will legitimately contain, once the sanitizer fix lands:
   - images → already handled by `.prose img` (`_design_system.html:3472-3478`);
   - tables → `.prose table` (`:3530`) with the instruction that the renderer
     wrap wide tables in `.prose-scroll` (`:3521-3528`), a keyboard-focusable
     scroll region. **No `.shell-breakout` inside the article** — the rail
     occupies that space; a wide table scrolls in place instead.
   - code → `.prose pre` / `.code-block` with copy button (`:3319`, `:3410-3470`);
   - embeds → `.prose-embed` (`:3486`).
   No new CSS; this is why the design can trust the content will sit well once
   engineering fixes the escaping.
3. **Unit footer row** — new, replaces the top-of-page edit link. A single flex
   row (`.unit-footer`), `margin-top: 2rem`, `justify-content: space-between`,
   `align-items: center`, `flex-wrap: wrap`, `gap: 0.75rem`:
   - **Left: "Mark as read" toggle** (signed-in only) — the one read-state form,
     moved out of the rail (§4.6). `core/_button.html` with `tag="button"`,
     `variant="subtle"`, `compact=True`; label "Mark as read" / "Mark as unread".
     Placement rationale: the action belongs where its moment happens — you mark
     a lesson read when you finish reading it, at the bottom, next to "next".
     POSTs to the existing `unit_read_state` endpoint. Signed out: slot empty.
   - **Right: "Edit this page on GitHub →"** — `.band-link`
     (`_design_system.html:1002-1007`) at `font-size: 0.85rem`. An editor
     affordance is meta-information; it belongs after the content like a
     colophon, not above it. Indigo on lavender = 4.69:1 light / 7.17:1 dark ✓.
4. **Prev/next navigation** (`.unit-navigation`, `unit.html:163-178`) — kept at
   the bottom with its dashed top rule (`unit.html:59-68`), refined:
   - Prev changes `variant="subtle"` → `variant="secondary"` so both directions
     match the homework page's navigation exactly
     (`_submission_page.html:194-202` uses secondary for both).
   - Labels use **normalised titles** (§7.1): "← Model Selection Process",
     "Setting up the Environment →" — no more "1.2 …" inside a button.
   - Last unit keeps `variant="primary"` "Continue to homework →" — the one
     green action on the page, marking the module's forward path (green =
     primary action per `_design_system.html:85` doc table).
   - aria-labels stay ("Previous unit: …", "Next unit: …", `unit.html:166,172,175`).
   - First unit: prev slot simply absent (current behaviour). No "Back to
     module" button — the breadcrumb's module crumb already is that link.
   - No top-of-page duplicate: the rail is the wayfinding instrument at the top;
     prev/next is the finish-reading instrument at the bottom.

I considered a "Lesson 3 of 10" `.mono-note` centered in the nav row and left it
out — the rail already answers position; this is proposal territory, not derived
need.

---

## 4. The rail (shared partial — new anatomy)

The rail stops being a card and becomes a quiet sticky nav list, sitting
directly on the lavender band. Wrapper element changes `<aside>` → `<nav
aria-labelledby="module-navigation-heading">`: it is site wayfinding and
deserves the landmark (the page then has three nav landmarks, each distinctly
named: Breadcrumb / In this module / Unit navigation — all distinguishable in a
rotor).

### 4.1 Markup contract (`_module_rail.html`, rewritten)

```
nav.module-rail [aria-labelledby=module-navigation-heading] [sticky]
  h2#module-navigation-heading.rail-heading   "In this module"
  p.rail-progress                             "3 of 10 read"        (signed-in only)
  ol.rail-units
    li.rail-unit [.is-current] [.is-read]
      a.rail-unit-link [aria-current=page on current]
        span.read-indicator [aria-hidden=true]     ordinal | ✓
        span.rail-unit-title                       normalised title
        span.sr-only "(read)"                      (read rows only)
  a.rail-unit-link.rail-homework                   (after the ol, same row anatomy)
    span.read-indicator.rail-homework-mark [aria-hidden=true]  "HW"
    span.rail-unit-title                           homework title
  p.rail-signin                                    (signed-out only)
    a.band-link "Sign in to track progress"
```

Changes from today's markup (`_module_rail.html:1-51`):

- The **whole row is the link** (indicator inside the `<a>`), instead of a
  1.75rem dead circle next to a text link — bigger target, one focus stop.
- The card `<div>` is gone; per-row forms are gone (§4.6); the opening sign-in
  sentence and the closing sign-in button are both gone, replaced by one quiet
  line (§4.5); homework joins the sequence as a terminal row (§4.4).
- `<ol>` retained deliberately: list semantics announce "3 of 10" position, which
  is the accessible counterpart of the visible ordinals. Homework sits **outside**
  the `<ol>` because it is not one of the N lessons.

### 4.2 Row treatment (`_module_rail_styles.html`, rewritten section)

- `.module-rail`: `display: grid; gap: 0.75rem;` — no background, border,
  radius or shadow. Keeps `position: sticky; top: 1rem` on desktop
  (`_module_rail_styles.html:14-18` today), plus
  `max-height: calc(100vh - 2rem); overflow-y: auto;` so a 20-unit module
  scrolls inside the rail instead of pushing the sticky box off-screen (§4.7).
- `.rail-heading`: the mono metadata voice, matching the existing homework label
  treatment it replaces (`_module_rail_styles.html:121-128`) and `.mono-label`
  (`_design_system.html:2085-2092`) but readable as a heading:
  `font-family: var(--font-mono); font-size: 0.72rem; font-weight: 700;
  letter-spacing: 0.08em; text-transform: uppercase; color: var(--muted);`.
  Muted on lavender = 5.50:1 light / 5.90:1 dark ✓. (Element stays `<h2>`.)
- `.rail-progress`: `.mono-note` treatment (`_design_system.html:2101-2105`) —
  mono 0.78rem muted. Text "N of M read". No progress bar — the count is
  glanceable and the `.progress` primitive would re-add visual weight just
  removed; noted as an available option in §9.
- `.rail-units`: `display: grid; gap: 0.15rem;` — **no per-row border-top**
  (today's `_module_rail_styles.html:39`). Separators between nav items are
  noise at this density; spacing does the work, as in
  `.site-navigation-links`.
- `.rail-unit-link`: `display: grid; grid-template-columns: 1.75rem minmax(0, 1fr);
  gap: 0.6rem; align-items: start; padding: 0.3rem 0.5rem; border-radius: 0.6rem;
  color: var(--ink); font-size: 0.9rem; font-weight: 700; text-decoration: none;
  min-height: 2.25rem;` (36px ≥ the 24px WCAG 2.2 target floor the breadcrumb
  comment cites, `_design_system.html:1035-1040`). `align-items: start` keeps
  the circle on the first line of a wrapped title.
  Hover: title `color: var(--indigo); text-decoration: underline;
  text-underline-offset: 0.2em;` (today's `:99-103`). Focus: the global
  `:focus-visible` outline (`_design_system.html:273-277`), nothing bespoke.
- `.read-indicator`: geometry as today (`_module_rail_styles.html:59-77`) —
  1.75rem, `border-radius: var(--drawn-circle)`, mono 0.72rem/700, default
  `border: 1.5px solid var(--line-soft); color: var(--muted)`. To keep the
  drawn-mark rhythm, alternate `--drawn-circle-b`/`-c` on 2nd/3rd rows via
  `nth-child(3n+2)`/`nth-child(3n)` exactly as `.pagination-number` does
  (`_design_system.html:1998-2007`). *(This alternation is a proposal — small,
  zero-cost, and follows an explicit system precedent — not a derived fix.)*

### 4.3 The states a row can be in

| State | Indicator | Row | Announced |
|---|---|---|---|
| Unvisited (default) | ordinal `n`, muted on transparent, `--line-soft` ring | plain | "n of M" via list position |
| Read | `✓`, `background: var(--mint); border-color/color: var(--green-deep)` (5.18:1 light, 6.91:1 dark ✓) | plain | `.sr-only` "(read)" inside the link |
| **Current** | **its ordinal** (not today's `→` — position info survives), `background: var(--indigo); color: var(--cream)` (5.06:1 light; dark `#13162a` on `#b0b7f5` = 9.30:1 ✓) | `background: var(--lavender-deep); border-radius: 0.6rem;` — **no border-left accent bar** (owner rule; replaces `_module_rail_styles.html:45-50` and its negative-margin hack — the link's own 0.5rem inline padding makes the tint read as a rounded highlight without shifting alignment) | `aria-current="page"` (kept from `_module_rail.html:21`) |
| Current + read | current wins (as today) | as current | both |

Title ink on the tinted current row: `--ink` on `--lavender-deep` = 12.34:1
light / 9.74:1 dark ✓. **Do not** put indigo text on `--lavender-deep`
(4.11:1 — fails AA); the sign-in link and any `.band-link` must never sit on
the tinted row.

### 4.4 Homework row

Directly after the `<ol>`, separated by the section-boundary rule the rail
already uses (`border-top: 2px dashed var(--line-soft); padding-top: 0.75rem;`
cf. `_module_rail_styles.html:115`): one `.rail-unit-link.rail-homework` row —
same anatomy as a lesson row, so homework reads as **the last step of the
module**, not an appendix. Its mark: `.rail-homework-mark` — the lesson circle
with `background: var(--bubble); color: var(--on-bubble); border: 2px solid
var(--line);` and the text `HW` at 0.6rem. This is deliberately the
`.module-number` treatment from the course syllabus accordion
(`_design_system.html:2456-2470`) — the same drawn bubble mark, so the mark
language carries across course → module → unit. Contrast: `--on-bubble` on
`--bubble` = 8.88:1 light / 7.45:1 dark ✓. No uppercase "HOMEWORK" label — the
row's title ("Homework 1: …") says what it is.

When the current page is the homework page (rail is also included by future
surfaces): the same `.is-current` treatment applies. (Today the rail only
renders on module/unit pages; nothing more specified.)

### 4.5 Sign-in: one quiet line, and why

Signed out, after the homework row: `p.rail-signin` holding one
`a.band-link` — "Sign in to track progress", `font-size: 0.85rem`, `min-height:
2rem` inline-flex (the breadcrumb target-size precedent,
`_design_system.html:1035-1040`). Indigo on lavender = 4.69:1 light / 7.17:1
dark ✓.

Reasoning, explicit:

- The masthead already carries "Log in" + "Sign up free" on every page
  (`_site_shell_head.html:132,189-191`), so the page does not *need* a local
  sign-in at all. The rail keeps exactly one because it explains something
  visibly absent *here* — the read-marks a signed-in learner would see — which
  a global "Log in" does not communicate. One contextual link; zero repetition.
- It is a link, not a bordered `.cta`: the homework page reserves the framed
  "Log in to submit" callout (`courses/templates/homework/homework.html:87`)
  for an action that is actually **gated** by auth. Reading is not gated;
  progress-tracking is a bonus. Same signal hierarchy across both pages.
- The sentence at the top of the rail (`_module_rail.html:7`) is deleted, not
  moved. Signed-out readers get no rail preamble at all — the list starts
  immediately under the heading.
- `?next=` must be **the current page's path** (fixes the `module_url` bug,
  `_module_rail.html:43`): the engineer should pass the requesting page's path
  into the partial (e.g. `request.get_full_path`) rather than `module_url`.

### 4.6 Read-state toggling leaves the rail

Delete `_module_rail.html:24-35` and `.read-state-form`
(`_module_rail_styles.html:105-112`). The rail shows state (✓); it does not
edit state. The single toggle lives in the content column's `.unit-footer`
(§3.3) where "I finished this lesson" is actually decided. Consequences:

- Signed-in rail density collapses from ~10 rows + 10 buttons to ~11 compact
  rows (~26rem for a 10-lesson module; homework and progress line included, the
  whole rail fits a laptop viewport).
- Marking a *different* lesson read/unread now requires visiting it. That is a
  real capability reduction, accepted deliberately: bulk-toggling other lessons
  from a sidebar is an audit task, not a reading task. Flagged in §9 with an
  alternative (auto-mark on visit) that would remove the button entirely.

### 4.7 Density and the 20-unit module

Row cost: one line-height 1.4 title at 0.9rem + 0.6rem padding ≈ 2.3rem. A
20-lesson module ≈ 20 × 2.3 + heading/progress/homework ≈ 51rem — taller than a
768px-tall viewport, which is why the sticky rail gets
`max-height: calc(100vh - 2rem); overflow-y: auto` (§4.2): the page keeps one
scroll for content while the rail scrolls itself the rare time it is taller
than the screen. Inner links give keyboard users focus stops inside the
scrollable region, so no `tabindex` is needed on the container (unlike
`.prose-scroll`, which holds no focusable children).

---

## 5. Mobile (existing breakpoint `max-width: 52rem`, `_module_rail_styles.html:134-145`)

- Order kept: content column first, rail after (`grid-template-areas`
  "main"/"rail"). A phone reader gets the lesson immediately; the module list
  is the "what's next" section after the prev/next buttons.
- Rail: `position: static` (as today, `:143`), drop the `max-height`/`overflow`
  (full list, page scroll). Because the card frame is gone, add a section
  boundary above the rail on this breakpoint only:
  `.module-rail { border-top: 2px dashed var(--line-soft); padding-top: 1.5rem; }`
  — the same divider grammar the bands themselves use (`_design_system.html:950`).
- `.unit-navigation` stacks to a grid with full-width buttons (already in
  `unit.html:74-83`); `.unit-footer` wraps naturally (flex-wrap).
- Considered and rejected: collapsing the rail into a `<details>` fold on
  mobile. It saves one flick of scrolling but hides wayfinding behind a tap and
  adds a state the desktop doesn't have. If 20-unit modules prove common,
  revisit with the `.row-fold` pattern (`_design_system.html:2251-2306`).

---

## 6. What the learner sees, by state

- **Signed out:** rail = heading, plain list (ordinals only — no read state, no
  preamble), homework row, one "Sign in to track progress" link (→ back to this
  unit). Content column: video, article, edit link (right), prev/next. No
  mark-as-read control.
- **Signed in, nothing read:** rail adds "0 of 10 read"; rows unchanged (all
  ordinals). Content column adds "Mark as read" (subtle) in `.unit-footer`.
- **Signed in, mid-module:** read rows show mint ✓ marks; current row tinted
  with indigo ordinal disc; "3 of 10 read". After POSTing "Mark as read" the
  page re-renders: current row keeps current styling, count increments, button
  flips to "Mark as unread".
- **First unit:** no prev button. **Last unit:** next slot shows green
  "Continue to homework →" — the module's flow ends where the rail's list ends,
  in the same place (homework) by both paths.

---

## 7. Data-shape decisions

### 7.1 Ordinal normalisation (display-level)

The UI should normalise, not faithfully render, the ragged upstream titles: one
template filter (e.g. `unit_display_title`) strips a leading
`^\d+(\.\d+)*[.)]?\s+` from unit titles **everywhere the ordinal is already
communicated by position**: rail rows, the module page lesson list
(`module.html:182`), and prev/next button labels. Result: the circle carries the
number once; "Setting up the Environment" stops being the odd row out; LLM and
ML courses render identically. The `<h1>` and breadcrumb are the other agent's
scope — coordinate so the same filter is available to them; whether the h1 keeps
"1.1 " is an owner call (§9).

### 7.2 Upstream body chrome

The ML/LLM markdown bodies embed their own navigation ("Navigation" heading
with course/lesson/next links; "← Back to module | Environment →"; "Slides"
links; a community-notes table) — visible in both screenshots. Once the page
provides real prev/next and a rail, these duplicate the chrome inside the
content. That is a content-pipeline/sanitizer concern, **not** something the
template should regex away — flagged for the engineering owner of the import
path in §9.

---

## 8. Coherence with sibling surfaces (explicit)

- **Homework page** (`_submission_page.html`): bottom prev/next as paired
  `cta-secondary cta-compact` (`:186-204`) — the unit page now matches
  variant-for-variant; the gated-action callout pattern (`homework.html:87`)
  is deliberately *not* copied for reading (§4.5); felt content width matches
  via `--content-width` (§2.1). If the concurrent homework redesign changes the
  nav pattern, the two specs should converge on whichever ships first.
- **Module page** (`module.html`): shares the rail partial and this spec's rail
  changes wholesale; its `.lesson-position` circles (`module.html:74-88`) are
  the same indicator language; the lesson list should apply the same title
  normalisation (§7.1). Recommendation to drop its rail entirely: §9.
- **Course page**: the homework row's bubble "HW" mark reuses `.module-number`
  (`_design_system.html:2456-2470`), the mark the learner already met on the
  syllabus accordion.
- **Site-wide**: mono metadata voice (`.mono-label`/`.mono-note`,
  `_design_system.html:2085-2105`) for the rail heading/progress; `.band-link`
  for the edit link; drawn-circle marks; the reading measure doctrine
  (`_design_system.html:485-503`).

---

## 9. Owner decisions needed (not a designer's call)

1. **`h1` ordinal prefix** — with rail/list/buttons normalised (§7.1), should
   the page `<h1>` keep "1.1 …"? (Belongs jointly to the breadcrumb/h1 agent.)
   My lean: keep the prefix in the h1 — it is the lesson's name in the course's
   own materials — but it must be one deliberate rule.
2. **Rail on the module page** — `module.html` currently shows the identical
   lesson list twice (`module-desktop.png`). I recommend removing the rail
   include from `module.html:208` and letting its main column be the module;
   the rail stays a unit-page (and future homework-page?) instrument. Owner
   call because it changes the module page, outside this brief.
3. **Existing accent bars in the system** — if "no accented borders" is global,
   `.when` (`_design_system.html:2817`) and `.prose blockquote`
   (`_design_system.html:3298`) both carry `border-left: 4px solid var(--bubble)`
   and need their own issue (a blockquote restyle touches every prose page;
   suggestion: `background: var(--sand)` tint + inset padding instead of a bar).
   This spec only removes the rail's bar.
4. **Auto-mark-read** — marking a unit read automatically on visit (or on
   clicking "next") would remove the toggle button entirely and make progress
   effortless; it also records reads the learner didn't mean. Engineering +
   product call; the design above works either way (the ✓ language doesn't
   change).
5. **Upstream body chrome** (§7.2) — strip trailing nav blocks at import time,
   fix at source in the course repos, or leave visible.
6. **Progress bar** — the `.progress` primitive (`_design_system.html:2069-2082`)
   could sit under "3 of 10 read". Left out to keep the rail quiet; cheap to add.

---

## 10. Deliberately left alone

- Breadcrumbs and the h1/subtitle block (other agent's annotated design).
- The sanitizer/raw-HTML bug and relative image paths (engineering) — §3.2
  records how the existing prose primitives absorb that content once fixed.
- The video frame treatment (`unit.html:42-57`) — content, may keep its card.
- Masthead, footer, dark-mode toggle, analytics dialog.
- `core/_button.html` contract and all `.cta` variants — reused, not modified.
- The page shell: still `content_page.html`, still one cream header + one
  lavender band; no new widths invented (`_design_system.html:504-511`).

## 11. Contrast ledger (measured, WCAG 2.2 AA, normal text ≥ 4.5:1)

| Pair (light) | Ratio | | Pair (dark) | Ratio |
|---|---|---|---|---|
| `--ink` on `--lavender` (row titles) | 14.07 | | on dark lavender | 11.55 |
| `--ink` on `--lavender-deep` (current row) | 12.34 | | dark | 9.74 |
| `--muted` on `--lavender` (heading, ordinals) | 5.50 | | dark | 5.90 |
| `--indigo` on `--lavender` (links, hover) | 4.69 | | dark | 7.17 |
| `--green-deep` on `--mint` (✓ read) | 5.18 | | dark | 6.91 |
| `--cream` on `--indigo` (current ordinal) | 5.06 | | dark (`#13162a`/`#b0b7f5`) | 9.30 |
| `--on-bubble` on `--bubble` (HW mark) | 8.88 | | dark | 7.45 |
| `--body-text` on `--lavender` (subtle cta) | 7.05 | | dark | 6.62 |
| `--on-green` on `--green` (primary cta) | 5.33 | | — | — |
| **Failure to avoid:** `--indigo` on `--lavender-deep` | **4.11 ✗** | | (dark passes at 6.05, but the rule is: no indigo text on the tinted row) | |

## 12. Implementation summary for the engineer

Files touched: `courses/templates/courses/_module_rail.html` (rewrite, §4.1),
`courses/templates/courses/_module_rail_styles.html` (rewrite of rail rules +
grid change §2.1/§4.2, keep breakpoint), `courses/templates/courses/unit.html`
(move edit link into new `.unit-footer`, add read-toggle form there, prev
variant change, breakout-reason comment), a `unit_display_title` filter (§7.1)
applied in rail/module list/nav labels, and the rail include's `next` URL fix
(§4.5). New classes: `.rail-heading`, `.rail-progress`, `.rail-units`,
`.rail-unit`, `.rail-unit-link`, `.rail-unit-title`, `.rail-homework`,
`.rail-homework-mark`, `.rail-signin`, `.unit-footer`. No design-system edits;
no new tokens; no new images or scripts.
