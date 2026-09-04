# The design system

This is the contract for building a page on DataTalks.Club. It covers the tokens,
the page shell, the bands and the shared primitives. Where a rule is enforced by a
test, the test is named — that test, not this document, is the thing that fails.

The system lives in three places:

- **`templates/core/_design_system.html`** — the shared stylesheet partial: tokens,
  reset, masthead, bands, cards and every shared primitive.
- **`templates/core/_site_shell_head.html`** / **`_site_shell_foot.html`** — the
  shared shell *markup*: skip link, dark-mode bootstrap, impersonation banner,
  masthead and announcement; footer, theme toggle and script set.
- **`templates/core/content_page.html`** — the parent template ordinary pages extend.

## How a page carries its stylesheet

Every public page ships **one inline `<style>` element and zero
`<link rel="stylesheet">`** — enforced by
`core/tests/test_homepage.py::test_homepage_carries_its_own_stylesheet_and_loads_no_legacy_css`.
A shared external CSS file is therefore not an option. Each page includes the
partial inside its own style element:

```html
<style>
  {% include "core/_design_system.html" %}
  /* page-specific rules follow */
</style>
```

Page-specific rules live in the page template; shared primitives live in the partial.
If two pages need the same rule, it belongs in the partial.

Page styles are emitted **after** the design system inside the same `<style>`
element, so an equal-specificity copy of a shared rule silently overrides it. A page
that restates a primitive to get a different value is drifting, not designing.

Template source must satisfy the readability contract in
`content/tests/test_public_template_source.py`: one opening tag per line, and Django
structural tags (`{% include %}` and friends) alone on their line.

## The ordinary content-page shell

Ordinary public course/platform pages **extend `templates/core/content_page.html`**.
The parent owns the document shell, the normal content width and the two surfaces:

- a cream `band-cream` header holding breadcrumbs, the title and header actions;
- a lavender `band-lavender` content surface holding the page body.

Children fill `document_title`, `metadata`, `page_styles`, `page_header`,
`page_content` and `page_scripts`, and preserve their view context, forms, metadata,
scripts and routes inside those blocks. They do **not** recreate the doctype,
masthead/footer or top-level bands, and do not create a competing normal width.

A genuinely wide table or diagram may use `.shell-breakout` inside the content
surface, with a page-specific reason.

The only top-level layout exceptions are the **homepage** (`core/home.html`) and the
**authentication pages**. Auth may use its own entrance width and composition, but
its content surface stays lavender. A registration page, dashboard, enrollment page,
leaderboard, homework or statistics page, module/unit page and project page are all
ordinary pages and use the shared parent.

Enforced by `courses/tests/test_content_page_shell.py`.

## Colour tokens

**Every colour goes through a token.** Tokens are defined on `:root` and mirrored on
`body.dark-mode` in the partial. If the colour you need is not here, add a token —
never an inline hex in a page. The only sanctioned literals are the fixed `#ffffff`
text on green surfaces and the few `body.dark-mode` counterparts (`#14172a` on bright
green, the `#0d0f1c` ink band), which exist because those pairings must not flip with
the theme.

### Core tokens

| Token | Light | Dark | Role |
| --- | --- | --- | --- |
| `--cream` | `#fdfaf3` | `#14172a` | page/band background, "paper" |
| `--lavender` | `#eff1fc` | `#191d33` | content band, tinted panels |
| `--mint` | `#eff5ea` | `#15251d` | events band, "build" panels |
| `--card` | `#ffffff` | `#1e2338` | card and control surfaces |
| `--ink` | `#1e2136` | `#f2f1ec` | headings, borders, the dark band |
| `--body-text` | `#4c5066` | `#c6cad9` | body copy |
| `--muted` | `#5c6076` | `#a4a9bd` | secondary copy, metadata |
| `--line` | `#1e2136` | `#454b6b` | 2px component borders |
| `--line-soft` | `#d6d9e8` | `#333855` | dashed rules, quiet borders |
| `--green` | `#2b7a35` | `#4cb85a` | primary action, hub accents |
| `--green-bright` | `#3ea94b` | `#4cb85a` | live markers, progress fill |
| `--green-deep` | `#41704e` | `#8fd39c` | quiet green notes |
| `--indigo` | `#5a62c4` | `#a6adf0` | links, kickers, time marks |
| `--indigo-soft` | `#c9cdf2` | `#3b4270` | soft indigo fills, avatars |
| `--shadow` | `rgba(30,33,54,.9)` | `rgba(0,0,0,.7)` | hard CTA shadow |
| `--shadow-soft` | `rgba(30,33,54,.16)` | `rgba(0,0,0,.45)` | hard card shadow |
| `--focus` | `#1e2136` | `#f2f1ec` | focus outline |
| `--page` | `var(--cream)` | `#14172a` | body background |

### Extended tokens

| Token | Light | Dark | Role |
| --- | --- | --- | --- |
| `--lavender-deep` | `#dfe2f7` | `#2d3565` | status pills, player stripes |
| `--sand` | `#f3f0e8` | `#2a2740` | waitlist/past pills, readonly fields |
| `--olive` | `#6b6034` | `#cbb98a` | text on `--sand` |
| `--green-ship` | `#1f6b2c` | `#93c7a5` | "you'll ship" lines, mint-pill text |
| `--bubble` | `#b9bff0` | same | brand bubble: play discs, number discs, CTA shadow |
| `--on-bubble` | `#1e2136` | same | glyphs and digits on `--bubble` |
| `--clay` | `#e0847a` | same | YouTube platform dot |
| `--gold` | `#e2c67a` | same | RSS platform dot |
| `--graph-edge` | `#b9bfd8` | same | drawn edges of a knowledge graph |

Most extended tokens are surfaces and marks that the dark grounds already carry, so
they do not restate themselves in dark. `--green-ship` does, because it is the one
extended token used as *text*. **When a page first needs `--sand` or
`--lavender-deep` as a text pairing in dark, move that token and its text partner
together — never one of the pair alone.**

The system has **no danger colour** and does not need one: an error reads as
`--olive` on the page ground and inside the `--sand` error summary (≈5.5:1 in both
themes). Colour is reinforcement; the words are the message.

## Typefaces and type scale

The faces are system stacks with one self-hosted variable face. **Do not add
`@font-face` rules and do not ship font binaries.**

| Token | Stack | Used for |
| --- | --- | --- |
| `--font-sans` | `"Quicksand", ui-rounded, "Segoe UI", system-ui, …` | everything except mono metadata. Quicksand (variable, 300–700) is self-hosted at `core/static/core/fonts/quicksand-latin-var.woff2` and loaded by the partial |
| `--font-mono` | `ui-monospace, "SF Mono", "Cascadia Mono", Menlo, Consolas, …` | machine-flavoured metadata: status pills, spec labels, timestamps, due dates, dates in list rows, the events date rail |

The original mockups were set in different faces, so rendered type is close to them
but will never match exactly. **That is not a bug, and the scale must not be tuned
chasing a pixel match.**

The scale (16px root; computed size at 1440px in brackets):

| Level | Rule | Notes |
| --- | --- | --- |
| Page h1 | `clamp(2.1rem, 4.9vw, 3.4rem)` [54.4px] | hero/index titles; `letter-spacing: -0.03em` |
| Featured h3 | `clamp(1.6rem, 3.2vw, 2.35rem)` [37.6px] | featured-card course title |
| Band h2 | `clamp(1.4rem, 3vw, 1.87rem)` [29.9px] | `.band-head h2` |
| Card h3 | `1.05–1.15rem` | card titles |
| Stat number | `1.6rem` | `.stat-tile strong` |
| Body | `1rem / 1.6`, weight 500 | the base |
| Lede | `clamp(0.95rem, 1.5vw, 1.05rem)` | hero/lede paragraphs |
| Prose h2 / h3 | `1.35rem` / `1.1rem` | headings inside `.prose` |
| Prose h4 / h5 / h6 | `1.05rem` / `0.9rem` uppercase | deeper source headings |
| Reading body | `1.05rem / 1.75` | `.prose-reading` — a page read end to end |
| Prose lede | `clamp(1.05rem, 2vw, 1.2rem)` | `.prose-lede`, the standfirst |
| Meta | `0.85–0.92rem` | sublines, notes, fineprint |
| Chip / kicker | `0.72rem` | uppercase, letter-spaced |
| Status pill / mono label | `0.66rem` | mono, uppercase, `letter-spacing: 0.09–0.1em` |

Headings are weight 800, `letter-spacing: -0.015em`, `line-height 1.12` (tighter at
the biggest sizes). The prose rows apply **only inside `.prose`**. A body's own
headings stay below the band `h2`, so a section of prose never competes with the
heading of the band it sits in.

## Spacing, radius, borders, shadows

- **Spacing scale**: `0.35 / 0.5 / 0.6 / 0.75 / 0.85 / 1 / 1.25 / 1.5 / 2 / 2.5rem`.
  Card gap `0.6rem` inside, `1.25rem` between cards (`0.9rem` between narrow rows).
- **Band padding**: `padding-block: 2.25rem`, growing to `2.9rem` at ≥62rem.
- **Shell**: `.shell` centres content at `--shell` = `76rem`, `padding-inline: 1rem`
  (`1.25rem` at ≥40rem).
- **Measure**: `--measure` = `38rem`, about 70 characters. `.prose-reading` and
  `.prose-lede` are held to it. Ordinary pages use `--content-width` through
  `.content-shell` rather than choosing a local max width.
- **Radius**: `0.45rem` status pills · `0.5–0.7rem` small controls and CTAs ·
  `0.9rem` cards, modules, player frame · `1.1rem` inset panels · `1.5rem` the
  featured slab · `999px` pills, discs, avatars.
- **Borders**: `2px solid var(--line)` on components; `1.5px` on chips and status
  pills; `3px solid var(--ink)` only on the featured slab; `2px dashed
  var(--line-soft)` for band and masthead separators.
- **Shadows are hard offsets, never blurs**: cards `0.3rem 0.3rem 0
  var(--shadow-soft)`; CTAs `0.3rem 0.3rem 0 var(--shadow)`; the featured slab
  `0.45rem 0.45rem 0 var(--shadow-soft)`; the ink/bubble CTA pair `0.25rem 0.25rem 0
  var(--bubble)`; the signup pill `0 3px 0 var(--shadow)`.

### No accent left-borders

The system never marks a block with a single-side rail — no `border-inline-start`, no
`border-left`, no shorthand equivalent, however quiet the colour.

A block that has to stand apart takes **a surface and a full border**
(`background: var(--card)` with `2px solid var(--line-soft)`, the tint carrying the
tone — `--sand` for attention, a dashed border for the quietest notice) or the
system's **dashed rule** above it.

A border that **separates siblings** (the dashed rule between spec cells), a table
cell rule, an input outline, or a shape drawn out of borders is not an accent rail
and is allowed. The difference is whether the border marks one block or divides two.

Enforced by `core/tests/test_design_accent_borders.py`, which fails on any new
`border-left` / `border-inline-start` that is not a removal or a reviewed structural
row in that test's table.

## The band system

A page is a stack of full-width `section.band` elements, each with a `.shell` inside.

| Class | Background | When |
| --- | --- | --- |
| `band-cream` | `--cream` | the hero — the first band on a page, and only that |
| `band-lavender` | `--lavender` | the content ground — every band after the hero |
| `band-mint` | `--mint` | an events *section* inside a page about several things |
| `band-ink` | `--ink` | the closing call-to-action band only |

Every band ends with the dashed separator (`border-bottom: 2px dashed
var(--line-soft)`); `band-ink` has none because it closes the page. **Do not invent
new band colours.**

**The hero keeps cream. Everything after it is lavender.**

- The **first band is the hero**: masthead-adjacent, breadcrumb, `h1`, byline. It
  takes the warm paper. Recolouring the ground directly under the shared masthead
  makes a page look like it belongs to a different site.
- **Every band after the hero is lavender.** A reader moving from `/events` to
  `/blog` to a person profile should meet the same ground under the same kind of
  content.
- **The warm band marks where the page starts; it is not the page.** Everything below
  the seam is content, and content is cool. Above it: the masthead, breadcrumb, title
  block and dateline, plus the actions and facts belonging to the title block (an
  event's links, a course's spec strip, an index's filter pills). A page whose
  heading block is shaped differently puts the seam somewhere else, not nowhere.
- **Consecutive lavender bands are expected.** What separates two sections is the
  heading and the space, not the colour — give every band after the hero a real
  `.band-head`.

Enforced by `content/tests/test_band_grounds.py`, which reads the band sequence out
of every public page template and pins where each page's body sits.

**`--page` follows the cool ground.** `--page` draws the strip below the last band.
A page whose last band is lavender sets, in its own style block:

```css
:root {
  --page: var(--lavender);
}
```

or that strip snaps back to cream between the lavender it continues and the cream
footer. The dark theme keeps its own `--page` from the partial, so the override names
the light token only.

**Mint** marks an events *section* inside a page that is about several things — it
says "this part is events" only where the rest of the page is not. On a page that is
*itself* about an event, or about one kind of work among four, mint says nothing the
heading did not and breaks the content ground. `--mint` remains correct as a *panel*
(`.panel-mint`) and a *pill* (`.status-pill-mint`) on any ground.

**The homepage is the exception.** `templates/core/home.html` alternates cream,
lavender, mint and ink deliberately — it is a magazine cover, not a content page, and
the alternation is the drawing. `test_the_homepage_keeps_its_own_alternation` holds
the cream hero, the closing ink band and the fact that it uses more than two grounds.

**Outside the rule for now**: the course platform's task surfaces (dashboard,
homework, submissions, statistics, the leaderboard family, enrolment, registration,
wrapped) and Studio.

**Contrast when a band changes ground.** `--cream`, `--lavender` and `--mint` all
flip with the theme, so a band that reads in light must be checked in dark. Muted
text clears AA on every ground in both themes, but check any muted text you move onto
a new ground rather than assuming.

## Component inventory

Markup shapes below are the contract; the CSS lives in the partial. **Reach for this
inventory before writing CSS.** If a primitive is missing, add it to the partial and
to this document — do not fork it locally.

### Structure and heads

- **`.shell`** — the centred content column; direct child of every band.
- **`.band-head`** — a band's heading block: a `div` holding `h2` plus an optional
  subline `p`, and a right-hand action (`.band-link` or `.kicker`). Stacked on narrow
  screens; from 69rem the action sits on the heading's baseline row.
  `.band-head-secondary` adds top margin for a second head inside one band.

  ```html
  <div class="band-head">
    <div>
      <h2 id="…">Heading</h2>
      <p>Subline.</p>
    </div>
    <a class="band-link" href="…">action →</a>
  </div>
  ```

- **`.kicker`** — uppercase indigo eyebrow. The mono variant is
  `.mono-label.mono-label-indigo`.
- **`.band-link`** — underlined indigo action link.
- **Eyebrow rule** — an eyebrow is optional context, never a second title. Do not use
  one to repeat the page title, course/cohort name, year, or anything the breadcrumb
  or heading already supplies. The homepage and the sign-in/sign-up entrances are the
  only page-level exceptions; a `.mono-label` inside a card or spec row is metadata,
  not a page eyebrow.
- **`.breadcrumbs`** — the trail on a detail page. Links take the `.band-link`
  treatment at the meta size, the current page is `--muted`, and the `/` separator is
  CSS-drawn with empty alternative text (`content: "/" / ""`) so it is never
  announced. Links keep the `2.75rem` minimum target height. Markup contract: a `nav`
  with an accessible name holding an `ol`, one `li` per level, every ancestor a link,
  and the current page on `li[aria-current="page"]` as plain text — never a link.

  Build the trail with the shared `{% breadcrumbs %}` tag rather than hand-writing
  list items; an empty level is skipped.

  Use it on pages below an index. **Index pages and the homepage do not carry one** —
  the masthead already marks them with `aria-current="page"`. A page offers one way
  back, not two: a page with a back-link does not also carry a trail, and a page
  whose parent index does not exist carries neither.

### Cards and chips

- **`.card`** — white slab, 2px ink border, `0.9rem` radius, hard shadow, column flex
  with `gap: 0.6rem`. `margin-top: auto` on a child pins it to the card foot.
- **`.card-grid`** + **`.card-grid-2` / `.card-grid-3`** — the band's card columns;
  they collapse to one column on narrow screens.
- **`.interactive-card.interactive-lift`** — the whole-card link treatment. Hover and
  keyboard focus lift the surface 2px and reveal a fixed bottom-right `→` by opacity
  only. The arrow never travels or bounces; reduced-motion removes the lift while
  preserving the keyboard cue.
- **`.event-card`** — the card for one event. The whole card is the link: the title is
  the one real anchor and its `::after` covers the card, so the accessibility tree
  holds exactly one link per event. Anything else interactive inside sits above that
  overlay with `position: relative; z-index: 1`.
- **`.chip`** — round uppercase mini-pill. Variants: `.chip-plain`, `.chip-green`,
  `.chip-live`, `.chip-ink`.
- **`.avatar`** — striped round placeholder disc, `2.6rem`.
- **`.person-chip`** — one credited person: a small portrait and their linked name.
  Every surface that names a person draws this.

  ```html
  {% include "public/_person_chip.html" with person=speaker|person_chip %}
  ```

  The chip is an **inline-flex atom**, so it wraps like a word. `.person-chips` is the
  wrapping row; `.person-chips-label` sets the optional "By" / "With".

  There is **one face size for the whole site** (`1.6rem`): a chip is a credit beside
  a title, not a portrait shot. The one exception is `.person-chip-lead` (`3rem`), for
  a block whose subject *is* that person.

  On a card, pair `.person-chips` with `.card-credit` so the credit is the card's last
  line and pins to the floor of a fixed-height column.

  Three facts can each be missing and each degrades on its own: no portrait keeps
  `.avatar.avatar-striped` (`aria-hidden`); no profile keeps the name as a plain
  `span.person-chip-name`, not a link to nowhere; the portrait always carries `alt=""`
  because the name beside it is already the credit and a screen reader must not hear
  it twice. Give the picture its intrinsic square so the line cannot shift.

### Actions

- **`.cta`** — the block action: 2px ink border, `0.7rem` radius, hard shadow,
  `min-height: 3.25rem`. Variants: `.cta-primary` (green, white text),
  `.cta-secondary` (card surface, ink text), `.cta-subtle` (transparent),
  `.cta-ink` (ink fill, cream text, bubble shadow), `.cta-lift` (bubble shadow on any
  CTA), `.cta-compact` (row-sized: `min-height: 2.75rem`).
- **`.pill-button`** — round bordered control (theme toggle, subscribe links) with an
  optional `.dot` swatch: `.dot-green`, `.dot-bubble`, `.dot-clay`, `.dot-gold`.
- **`.signup-button`** — the masthead's green sign-up pill.

### State and metadata

- **Status pills** — mono uppercase state markers, soft-rectangle (`0.45rem` radius),
  deliberately distinct from the round `.chip`:

  ```html
  <span class="status-pill status-pill-live">
    <span class="status-dot status-dot-pulse" aria-hidden="true"></span>
    active
  </span>
  ```

  | Variant | Surface | Use |
  | --- | --- | --- |
  | *(bare)* | cream, soft border | quiet facts: "enrolled · week 9 of 10" |
  | `status-pill-live` | `--green`, white text | a running cohort, a live event |
  | `status-pill-open` | `--lavender-deep`, ink text | registration open, next cohort dates |
  | `status-pill-wait` | `--sand`, olive text | waitlist, finished courses, past events |
  | `status-pill-mint` | `--mint`, `--green-ship` text | podcast kind, season/episode |

  **The words carry the state; colour is reinforcement only.** `.status-dot` is the
  small green disc; `.status-dot-pulse` adds the pulse, which `prefers-reduced-motion`
  disables.

- **`.mono-label`** — uppercase mono eyebrow; variants `.mono-label-green`,
  `.mono-label-indigo`.
- **`.mono-note`** — small mono note (date ranges, submission counts).
- **`.mono-code`** — an inline literal inside running copy: a Slack channel, a
  command, a file name. Mono face at `0.92em`, no surface of its own. Put it on the
  `code` element the content already deserves. It is a typographic mark, not a state
  marker (`.status-pill`) and not a metadata line (`.mono-note`).
- **`.ship-line`** — italic bold `--green-ship`; sits directly under a course title
  everywhere a course is sold.
- **`.stat-tiles` / `.stat-tile`** — lavender tiles with ink border and hard shadow,
  in a 2×2 grid: a number as `strong`, a label as `span`.
- **Progress bar** — white track, 2px ink border, `--green-bright` fill. Always pair
  it with a `.mono-note` stating the numbers, and give it the full progressbar ARIA:

  ```html
  <div class="progress" role="progressbar" aria-valuemin="0" aria-valuemax="9" aria-valuenow="7" aria-label="Homeworks submitted">
    <div class="progress-fill" style="width: 78%"></div>
  </div>
  ```

### Filter pills

One row of toggles where exactly one is active:

```html
<div class="filter-pills" role="group" aria-label="Filter courses">
  <button class="filter-pill" type="button" aria-pressed="true">All courses</button>
  <button class="filter-pill" type="button" aria-pressed="false">Active</button>
</div>
```

The active pill (`[aria-pressed="true"]`, `.is-active`, or `[aria-current="page"]`
when the pills are links) fills ink with cream text. **State must be expressed
through the ARIA attribute, not the class alone.** A label wider than the viewport
wraps inside the pill and the pill grows, rather than pushing the page sideways.

### Rows and lists

- **Spec rows** — label-over-value facts as a dashed-bounded strip:

  ```html
  <dl class="spec-strip">
    <div class="spec">
      <dt class="mono-label">starts</dt>
      <dd><strong>Mon, Sep 14, 2026</strong></dd>
    </div>
  </dl>
  ```

- **List rows** — the dashed-divided row pattern for index surfaces:

  ```html
  <div class="row-list">
    <div class="list-row" style="--row-cols: 1.6fr 1.4fr 1fr 0.8fr">…</div>
  </div>
  ```

  Rows stack on narrow screens; from 48rem each row lays out to `--row-cols` (default
  `minmax(0,1fr) auto`). **The row owns the dashed bottom border — never draw
  separators inside cells.**

- **Archive row** — one row of a dated index: the date on a fixed rail, the record
  beside it as a card. It is a partial, not a pattern to copy:

  ```html
  {% include "public/_archive_row.html" with row_title=record.title row_url=record.public_path row_date=record.published row_summary=record.description row_credits=record.author_profiles only %}
  ```

  Optional slots: `row_date` (absent drops the rail), `row_mark="play"`,
  `row_eyebrow`, `row_pill`/`row_pill_variant`/`row_pill_extra`, `row_summary`,
  `row_credits`, `row_class`/`row_hook`. Always pass `only`.

  Inside the card the order is fixed: leading mark, eyebrow, pills, `h3` title,
  clamped summary, credited people last as `.person-chips.card-credit`. The rail is
  `.date-rail`, day above year.

  Drawn by `/blog`, `/books`, `/podcast` and every contribution band of a person's
  profile. **Not** by the events index, whose rail carries three facts including a
  start clock — that is `.when` below, and its card is the shared `.event-card`.

- **Date rail (events index)** — set the way `.date-rail` is set: position and type,
  no rule.

  ```html
  <div class="when">
    <strong>Aug 17, 2026</strong>
    <span class="when-day">Monday</span>
    <span class="when-time">14:00 CEST</span>
  </div>
  ```

- **Folded rows (`.row-fold`)** — a list long enough to be a wall keeps its first rows
  in view and puts the rest behind one `<details>` that **names what it holds**:

  ```html
  <details class="row-fold" id="person-events-more">
    <summary class="row-fold-summary">
      <span class="row-fold-open">Show 44 more events</span>
      <span class="row-fold-close">Show fewer events</span>
      <span class="row-fold-marker" aria-hidden="true"></span>
    </summary>
  </details>
  ```

  It sits inside the `.row-list`, so folded rows keep the dashed division. `<details>`
  does the work: keyboard behaviour and announced expanded state come free and it
  opens with JavaScript off. A bare "Show more" makes the reader click to find out how
  much they are in for.

- **Timestamp rows** — `.timestamp-row` with a `.timestamp-mark` and a `p`. The same
  shape carries transcript rows with the speaker name as the mark.

### Panels, discs and scrollers

- **`.panel`** + `.panel-mint` / `.panel-lavender` (+ `.panel-outlined` for the 2px
  ink border) — tinted inset blocks.
- **`.play-disc`** — bubble-lavender disc with an ink glyph (`2.4rem`;
  `.play-disc-large` `3.5rem`; `.play-disc-card` the white disc inside a player).
  Use `.play-disc-card` **only where the surface behind it is known** — a white disc
  disappears on a light thumbnail. `.player-frame` is the striped 16:9 slab.
- **`.catalog-scroller`** — a scroll-snap column grid that hides its scrollbar and
  stays a carousel at every width. Pair with `.scroller-controls` / `.scroller-button`
  (hidden without JavaScript via `html.js`); keep `tabindex="0"`, `role="group"` and
  an `aria-label` on the strip.
- **`.doodle`** — a decorative drawing: `display: block; width: 100%`. Pages constrain
  aspect ratio per slot. Always `alt=""`.
- **`.module` / `.module-list`** — the disclosure accordion for a surface with genuine
  per-module content. **No page uses it today**; a page whose job is to show every
  deadline and state draws `.row-list` tables instead of folding them behind a click.

## Forms

```html
<div class="field">
  <label class="field-label" for="reg-email">Email</label>
  <input class="field-input" id="reg-email" type="email" readonly value="…">
  <p class="field-hint">Using your account email.</p>
</div>
```

`.field-input` styles `input`, `select` and `textarea` alike; readonly and disabled
fields sit on `--sand`.

**A disabled field drops to a `--line-soft` edge; a readonly field does not.** WCAG
1.4.11 exempts an *inactive* component from the 3:1 non-text floor, and readonly is
still focusable and still in the tab order, so it keeps the full `--line` border.
Softening the readonly edge too would be an accessibility regression wearing a design
fix's clothes.

Mutation forms use the shared primitives so homework, project submission and
peer-review forms keep one visual system even when their fields differ:

- **`.cmp-form`** — the shared 46rem measure and vertical rhythm, and nothing else.
  **The form is not a box**: it already sits on the lavender content band, and a
  second lavender panel with its own border only frames the page again at 1.25:1
  against its own identical ground. The measure and the field rhythm do the grouping.
- **`.cmp-form-fields`** — the field group rhythm.
- **`.cmp-form-actions`** — the dashed action rule, spacing and responsive full-width
  button layout. Use `.cta-primary`, `.cta-secondary` or `.cta-subtle` for intent.
- **`include/learning_in_public_links.html`** — the one learning-in-public field
  primitive. Its cap is carried in `data-learning-in-public-cap`.
- **`.form-check`** with `.form-check-input` / `.form-check-label` — one option row.
  The row keeps the 2.75rem (44px) pointer target; the control itself is **1.25rem
  (20px)** in every theme and on every page.

A form that can be wrong draws the shared accessible form marks, rendered by
`core/templates/accessibility/`:

- **`.a11y-error-summary`** — the form-level summary (`role="alert"`, `tabindex="-1"`,
  `data-focus-error-summary`), one list item per error linking to the field it names.
- **`.a11y-required`** — the `*` beside a required label. **Always** paired with a
  `.sr-only` "(required)" and a fineprint line saying what the mark means; the
  requirement is stated in words, never carried by the glyph alone.
- **`.a11y-help`** / **`.a11y-error`** — hint and error under a field, linked by
  `aria-describedby` / `aria-errormessage` through the `accessible_widget` tag. The
  error opens with a `.sr-only` "Error:".

The site owns **one** registration form, on the registration campaign page
(`courses/templates/courses/register.html`), with its own POST target, duplicate
detection, country and role validation and error summary. A page that wants a
registration links to it rather than growing a second copy of a mutation surface.

## Long-form prose

A page whose text arrives as typed blocks renders them inside `.prose`:

```html
<div class="prose">
  <h2 id="purpose-of-aa-tests">Purpose of A/A Tests</h2>
  <p class="prose-paragraph">…</p>
  <p class="prose-item">…</p>
</div>
```

Rhythm comes from `.prose > * + *`. `.prose-paragraph` keeps the source's own line
breaks (`white-space: pre-line`), so its opening tag and value stay on one line. A
list item that arrives as its own block is `.prose-item` (`display: list-item`)
instead of the page inventing a `<ul>` the source never marked. Headings keep the
identifier the source gives them, which is what in-page anchors land on, and get
`scroll-margin-top` so an anchored heading does not land against the viewport top.

Modifiers:

- **`.prose-reading`** — the modifier a page adds when its body *is* the page: the
  reading measure at `1.05rem/1.75`, with `overflow-wrap` so a long URL breaks
  instead of widening the column. Without it `.prose` keeps the body size of the page
  around it. Pair with **`.prose-lede`** for the standfirst.
- **`.prose-scroll`** — the frame a wide table sits in. It is a **named region with
  its own focus stop** (`role="region"` + `tabindex="0"` + an `aria-label`), because a
  scrollable box no keyboard can reach is a barrier. Two frames in one document may
  not share a name.
- **`.prose-embed`** — one already-sanitized fragment from a source that wrote its own
  block markup, keeping the rhythm inside it.
- **`.prose-unavailable`** — a dashed plate naming an element this site cannot draw.
- **`.prose-caption`** / `figcaption` — `--muted` at the meta size.

A richer body also carries `h4`–`h6` continuing the ladder, real `ul`/`ol`/`li`,
`--ink` strong, an italic `blockquote` on the card surface inside a soft 2px border,
inline `code`, a `pre` block that **wraps rather than scrolling** (a box that scrolls
sideways with no focus stop of its own cannot be reached from a keyboard), `img`
bounded to the column with its own `width`/`height` so the box is reserved, a dashed
`hr`, and table cells on dashed rules.

Turning a *projected* body into this markup is `content.article_content.prose_sections`
plus `templates/public/_prose_blocks.html`. A block that carries a source segment is
rendered through Markdown and the shared sanitizer
(`content.services.sanitize_rendered_html`). **No page writes unsanitized external
HTML.**

## Search row

```html
<form class="search-row" action="…" method="get">
  <label class="sr-only" for="wiki-query">Search the Wiki</label>
  <input id="wiki-query" class="field-input" name="q" type="search" required>
  <button class="cta cta-compact cta-primary" type="submit">Search</button>
</form>
```

The field grows and the button keeps its size; both wrap on a narrow screen.

## Knowledge graph

`/wiki/graph` and the homepage wiki band draw the wiki the same way: a hub, a ring of
its real neighbours, and the edges between them, as **one SVG per width** with a
`viewBox`, so the drawing scales as a unit, a label cannot escape the node it names,
and nothing overflows a phone.

- **`.graph-svg`** with `.graph-svg-wide` / `.graph-svg-narrow` — the two arrangements
  of the same drawing. Both are in the markup and one is `display: none` at a time,
  which keeps the hidden one out of the tab order. Each is capped near its design size
  (44rem / 24rem) so labels stay label-sized.
- **`.graph-svg-node`** (`.graph-svg-shape` + `text`) — one node, drawn as an SVG link
  so it is keyboard reachable; `.graph-svg-hub` is the green centre. The focus ring is
  a thickened `--focus` stroke, for engines that draw no outline on an SVG link.
- **`.graph-svg-edge`** — a drawn relation, styled from the partial and **never from
  an attribute in the page**.
- **`.graph-node`** / **`.graph-cloud`** — one node outside the drawing, and a
  wrapping row of them for a set too large to draw.
- **`.graph-index`** / `.graph-index-summary` — the `<details>` folding a complete
  node-type list away.
- **`.graph-legend`** with `.legend-key` / `.legend-swatch` / `.legend-hub` /
  `.legend-page` / `.legend-swatch-line`.
- **`.topic-list`** / **`.topic`** — the bordered topic cards beside the drawing.

The geometry is a layout constant in `core/graph_layout.py`, shared by both pages.
Which node sits where is read from the graph's own edges, and a hub or spoke the data
cannot supply raises rather than rendering an invented edge. A label too long for its
node wraps — the node grows taller, never wider than its frame's bound.
`core/tests/test_graph_layout.py` holds the result: no drawn node overlaps another or
leaves its frame.

## Masthead, footer and shell markup

The masthead (`.masthead`, `.brand`, `.site-navigation`, `.masthead-actions`,
`.nav-link`, `.nav-auth`, account menu) and the footer/analytics dialog are fully
styled by the partial, and their **markup is shared too**:

```html
<body …>
  {% include "core/_site_shell_head.html" %}
  <main id="main-content" tabindex="-1">
    …the page…
  </main>
  {% include "core/_site_shell_foot.html" %}
  …the page's own scripts, if it has any…
</body>
```

**Do not copy that markup into a page.** The first five pages in the system each
carried their own copy, and the copies drifted — one lost `/slack` from the
navigation, and one lost `user_menu.js`, so the account menu would not close on
Escape. `core/tests/test_design_system_shell.py` renders every page in the system and
compares their navigation entries and script sets to each other.

The navigation is **nine entries** — Events, Courses, Blog, Podcast, Wiki, Books,
Docs, FAQ, Slack. The current entry is set from `primary_navigation_current`
(`core/context_processors.site_context`), so **a page never marks it by hand**. The
navigation contract (toggle + `#site-navigation-links`, opened by
`core/site_navigation.js`, always visible without JavaScript) must not be altered per
page.

**The brand lockup.** The mark is one drawing: the bubble outline and the `DTC` inside
it are stroked paths in the same weight and round cap, because the illustrations this
masthead sits above are hand-lettered and a typeset `DTC` was the one printed-looking
thing in the mark. `DataTalks.Club` is a single weight throughout — it is one name,
not a light half and a bold half.

## The account entrance family

Sign up (and its closed state), sign in, sign out, the four password-reset steps, the
inactive-account notice and the provider outcomes are one page in several states.
Each is an ordinary document of this system — one inline `<style>`, no external CSS,
both shell partials, and the band-ground rule with `:root { --page: var(--lavender); }`
so the tail matches. `content/tests/test_band_grounds.py` reads them with the public
pages.

- **The column is the reading measure** (`.shell.shell-reading`). A sign-up page is a
  single decision and does not want the full 76rem shell.
- **The shared block is a partial, not seven copies.**
  `accounts/templates/account/_entrance_styles.html` holds the family's own rules and
  each page includes it inside its own style element, straight after
  `core/_design_system.html`. Nothing in it is site-wide, so it does not belong in the
  partial every page loads.
- **The provider buttons are the primary path**: `.cta.cta-secondary` at full width,
  one per row. Each carries a `.provider-mark` — a single monochrome path filled with
  `currentColor`, so the mark takes the control's own `--ink` and survives the theme
  without a hex or a second asset — and it is `aria-hidden` because the button already
  says "Continue with GitHub". A provider with no mark keeps its words.
- **`.entrance-or`** is the seam between the two ways in: the dashed rule on both
  sides of a real word, so the choice is announced.
- **The email form is the alternative**, in a `.card` below the divider, using the
  shared field and `.a11y-*` primitives.
- **Who may reach these pages is not a design decision.** Redesigning a template
  changes no route, gate or rate limit.

## Responsive rules

| Query | What changes |
| --- | --- |
| `max-width: 47.99rem` | the narrow layout: **reshape, not reflow** |
| `min-width: 40rem` | shell padding grows; `.card-grid-2` becomes two columns |
| `min-width: 48rem` | `.list-row` adopts its `--row-cols` columns |
| `min-width: 62rem` | band padding grows; `.card-grid-3` becomes three columns; pages open their wide grids |
| `min-width: 69rem` | the full navigation link row replaces the Menu toggle |

**Below 48rem a surface changes shape rather than merely squeezing.** The homepage
hero drops its drawing and fineprint, the climb and events become dashed lists, and
the wiki graph swaps its landscape SVG ring for a portrait one. Make the same kind of
decision — the finished-course table stacks each row; the events row puts the date
rail above the card — instead of letting a grid wrap arbitrarily. Shared narrow
behaviour lives in the partial; put page reshaping in the page's own `@media` block
after the include.

## Accessibility guarantees

The partial guarantees these, and every page must preserve them:

- **Focus** — a global `:focus-visible` outline, `3px solid var(--focus)` with
  `outline-offset: 2px`, visible on both themes. `#main-content` suppresses its own;
  `.skip-link` becomes visible on focus. **Never remove an outline without an equal
  replacement.**
- **Target sizes** — interactive controls are at least `2.75rem` (44px) tall. A control
  the design drew smaller still gets the floor; that deviation is deliberate.
- **Contrast** — body and muted text pass 4.5:1 on every band in both themes.
  `--green-bright` is a **marker** colour (progress fill, status dot), not a text
  surface: white on it is only 3:1, so a live pill takes `--green`. **State is never
  colour-only** — pills and dots always carry words.
- **Decorative artwork** — every illustration has `alt=""`; every decorative inline
  SVG and dot has `aria-hidden="true"` and `focusable="false"`.
- **Motion** — `prefers-reduced-motion: reduce` disables all animation, transitions
  and smooth scrolling globally.
- **Structure** — one `h1` per page; bands are `section` elements labelled by their
  heading (`aria-labelledby`); the skip link precedes everything; disclosure widgets
  use native `details`/`summary` or a real `button` with `aria-expanded`.

## Illustrations

Artwork lives in `core/static/core/illustrations/` as **trimmed transparent WebP**.

- Only the outer page background is transparent. The drawing keeps its own white fills
  so it stays readable on the dark theme without a plate behind it.
- Files are trimmed to the ink, so natural sizes vary; the consuming slot boxes them
  (`aspect-ratio` + `object-fit: contain`).
- Illustrations are **decorative**: always `alt=""`, `decoding="async"`, and
  `loading="lazy"` below the fold. The surrounding copy carries the meaning.
- `templates/core/_home_illustration.html` is the worked example; give a new page its
  own variant map rather than overloading the homepage's.

How an illustration is produced and processed is `_docs/design/illustration-assets.md`.

## Building a new page — checklist

1. Extend `templates/core/content_page.html` unless the page is the homepage or an
   auth entrance. If it is one of those, start from `templates/core/home.html`'s
   skeleton: doctype, the `no-js`/`js` class swap, meta/OG tags, one `<style>` whose
   first content is `{% include "core/_design_system.html" %}`, then page rules.
2. Include `core/_site_shell_head.html` first in the body and
   `core/_site_shell_foot.html` after `</main>`. Do not copy any of it into the page,
   and do not set `aria-current` by hand. Add the page to `DESIGN_SYSTEM_TEMPLATES` in
   `core/tests/test_design_system_shell.py`.
3. Compose the page as `section.band` elements with `.shell` inside; pick grounds from
   the band table; use `.band-head` for every section heading.
4. Reach for the inventory before writing CSS. New page CSS is for layout that is
   genuinely page-specific. If a primitive is missing, add it to the partial and to
   this document — do not fork it locally.
5. Colours only via tokens; mono only via `--font-mono`; sizes from the type scale.
6. Design the narrow layout deliberately (reshape, not reflow) in the page's own
   `@media (max-width: 47.99rem)` block.
7. Keep the template readable: one opening tag per line, structural template tags
   alone on their line.
8. Verify: both themes, 1440×900 and 390×844 screenshots, a keyboard pass (skip link,
   focus visible, menus operable), and the page-owning tests plus
   `make lint format-check typecheck`.
