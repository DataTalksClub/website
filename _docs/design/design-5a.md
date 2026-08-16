# Design 5a — the shared design system

Design 5a is the visual language introduced by the homepage rebuild (issue #179)
and carried by the 6-series pages: the courses index (6a), the course page (6b),
the events index (6c) and the podcast index and episode (6d).

The system lives in one place:

- **`templates/core/_design_system.html`** — the shared stylesheet partial.
  Tokens, reset, masthead, bands, cards and every shared primitive, including
  the extended primitives the 6-series pages need.
- **`_docs/design/mockups/`** — the decoded mockup source. For the four
  6-series pages, `datatalks-pages.source.html` is the authoritative reference
  for markup shape and values; the PNG exports under `.tmp/design-mockups/`
  are convenience renderings only.
- This document — the contract for anyone building a page in the system.

## How a page carries its stylesheet

Every rebuilt public page ships **one inline `<style>` element and zero
`<link rel="stylesheet">`** (enforced by
`core/tests/test_homepage.py::test_homepage_carries_its_own_stylesheet_and_loads_no_legacy_css`).
A shared external CSS file is therefore not an option; instead each page
includes the partial inside its own style element:

```html
<style>
  {% include "core/_design_system.html" %}
  /* page-specific rules follow */
</style>
```

Keep page-specific rules in the page template and shared primitives in the
partial. If two pages need the same rule, it belongs in the partial. Template
source must satisfy the readability contract in
`content/tests/test_public_template_source.py`: one opening tag per line, and
Django structural tags (`{% include %}` and friends) alone on their line.

## Colour tokens

**Every colour goes through a token.** Tokens are defined on `:root` and
mirrored on `body.dark-mode` in the partial. If the colour you need is not in
the table, add a token — never an inline hex in a page. The only sanctioned
literals are the fixed `#ffffff` text on green surfaces and the handful of
`body.dark-mode` counterparts (`#14172a` text on bright green, the `#0d0f1c`
ink band), which exist because those pairings must not flip with the theme.

### Core tokens

| Token | Light | Dark | Role |
| --- | --- | --- | --- |
| `--cream` | `#fdfaf3` | `#14172a` | page/band background, "paper" |
| `--lavender` | `#eff1fc` | `#191d33` | alternate band, tinted panels |
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

### Extended tokens (introduced with the 6-series pages)

Light values come from the mockup source; dark values follow the mockup's own
dark mapping in `_docs/design/mockups/datatalks-homepage.source.css`. The dark
column reached the partial with the events index (mockup 6c), the first page to
use these tokens; until then only the light values were defined, so a status
pill kept its light surface while its text flipped with the theme.

| Token | Light | Dark | Role |
| --- | --- | --- | --- |
| `--lavender-deep` | `#dfe2f7` | `#2d3565` | status pills, player stripes |
| `--sand` | `#f3f0e8` | `#2a2740` | waitlist/past pills, readonly fields |
| `--olive` | `#6b6034` | `#cbb98a` | text on `--sand` |
| `--green-ship` | `#1f6b2c` | `#93c7a5` | "you'll ship" lines, mint-pill text |
| `--bubble` | `#b9bff0` | same | brand bubble: play discs, date rail |
| `--on-bubble` | `#1e2136` | same | glyphs and digits on `--bubble` |
| `--clay` | `#e0847a` | same | YouTube platform dot |
| `--gold` | `#e2c67a` | same | RSS platform dot |

Only `--green-ship` restates itself for the dark theme, because it is the one
extended token used as *text*: on the dark mint of a `.status-pill-mint` the
light `#1f6b2c` reads at about 1.6:1. The rest are surfaces and marks that the
dark grounds already carry — the bubble disc stays the pale periwinkle the
homepage and the podcast index both draw, with its ink glyph. When a page first
needs `--sand` or `--lavender-deep` as a *text* pairing in dark, move that token
and its text partner together, never one of the pair alone.

Mockup colours that deliberately map onto existing tokens rather than getting
their own: the greys `#5b5f74`, `#7b7f95` and `#3a3e57` → `--muted` /
`--body-text`; the pill text `#2c3054` → `--ink`; the link hover `#3b43a2` has
no equivalent (the system does not change link colour on hover). The mockups'
serif face (Source Serif 4) is unused on all seven pages.

## Typefaces and type scale

The site's faces are **system stacks**; this is an owner decision recorded in
`_docs/design/mockups/README.md`. Do not add `@font-face` rules for new faces
and do not ship font binaries.

| Token | Stack | Used for |
| --- | --- | --- |
| `--font-sans` | `"Quicksand", ui-rounded, "Segoe UI", system-ui, -apple-system, BlinkMacSystemFont, "Helvetica Neue", Arial, sans-serif` | everything except mono metadata; Quicksand (variable, 300–700) is self-hosted at `core/static/core/fonts/quicksand-latin-var.woff2` and loaded by the partial |
| `--font-mono` | `ui-monospace, "SF Mono", "Cascadia Mono", Menlo, Consolas, "Liberation Mono", monospace` | machine-flavoured metadata: status pills, spec labels, timestamps, due dates, dates in list rows, the events date rail |

The mockups are set in different faces — Nunito for sans and JetBrains Mono
for mono — so the rendered type is close to the exports but will never match
them exactly. **That difference is not a bug, and the scale must not be tuned
further chasing a pixel match against the PNGs.** When you need a size,
take it from `datatalks-pages.source.html`, not from measuring an image.

The scale in use (16px root; computed size at a 1440px viewport in brackets):

| Level | Rule | Notes |
| --- | --- | --- |
| Page h1 | `clamp(2.1rem, 4.9vw, 3.4rem)` [54.4px] | hero/index titles; `letter-spacing: -0.03em` |
| Featured h3 | `clamp(1.6rem, 3.2vw, 2.35rem)` [37.6px] | the featured-card course title |
| Band h2 | `clamp(1.4rem, 3vw, 1.87rem)` [29.9px] | `.band-head h2` |
| Card h3 | `1.05–1.15rem` | card titles |
| Stat number | `1.6rem` | `.stat-tile strong` |
| Body | `1rem / 1.6`, weight 500 | the base |
| Lede | `clamp(0.95rem, 1.5vw, 1.05rem)` | hero/lede paragraphs |
| Meta | `0.85–0.92rem` | sublines, notes, fineprint |
| Chip / kicker | `0.72rem` | uppercase, letter-spaced |
| Status pill / mono label | `0.66rem` | mono, uppercase, `letter-spacing: 0.09–0.1em` |

Headings are weight 800 with `letter-spacing: -0.015em` and `line-height 1.12`
(tighter on the biggest sizes). The homepage was verified to sit within ~10% of
the export's heading and body sizes; that tolerance is the accepted state.

## Spacing, radius, borders, shadows

- **Spacing scale** (gaps and insets): `0.35 / 0.5 / 0.6 / 0.75 / 0.85 / 1 /
  1.25 / 1.5 / 2 / 2.5rem`. Card gap is `0.6rem` inside, `1.25rem` between
  cards (`0.9rem` between narrow rows).
- **Band padding**: `padding-block: 2.25rem`, growing to `2.9rem` at ≥62rem.
- **Shell**: `.shell` centres content at `max-width: var(--shell)` = `76rem`
  with `padding-inline: 1rem` (`1.25rem` at ≥40rem).
- **Radius scale**: `0.45rem` status pills · `0.5–0.7rem` small controls and
  CTAs · `0.9rem` cards, modules, player frame · `1.1rem` inset panels ·
  `1.5rem` the featured slab · `999px` pills, discs, avatars.
- **Borders**: `2px solid var(--line)` on components; `1.5px` on chips, status
  pills and inner dashed rules; `3px solid var(--ink)` only on the featured
  slab; `2px dashed var(--line-soft)` for band and masthead separators.
- **Shadows are hard offsets, never blurs**: cards `0.3rem 0.3rem 0
  var(--shadow-soft)`; CTAs `0.3rem 0.3rem 0 var(--shadow)`; the featured slab
  `0.45rem 0.45rem 0 var(--shadow-soft)`; the ink/bubble CTA pair `0.25rem
  0.25rem 0 var(--bubble)`; the signup pill `0 3px 0 var(--shadow)`.

## The band system

A page is a stack of full-width `section.band` elements, each with a `.shell`
inside. Bands alternate background to give the page its rhythm:

| Class | Background | When |
| --- | --- | --- |
| `band-cream` | `--cream` | the default; hero and most content bands |
| `band-lavender` | `--lavender` | alternate emphasis band (climb, catalogue, wiki, "open registration") |
| `band-mint` | `--mint` | events band |
| `band-ink` | `--ink` (`#0d0f1c` dark) | the closing call-to-action band only |

Every band ends with the dashed separator (`border-bottom: 2px dashed
var(--line-soft)`); `band-ink` has none because it closes the page. The
masthead carries the same dashed rule. Do not invent new band colours.

Alternation is content-driven, not strictly ABAB: consecutive cream bands are
fine when a dashed rule between them reads clearly.

## Component inventory (shared primitives)

Markup shapes below are the contract; the CSS lives in the partial.

### Structure and heads

- **`.shell`** — the centred content column; direct child of every band.
- **`.band-head`** — a band's heading block: a `div` holding `h2` plus an
  optional subline `p`, and a right-hand action (`.band-link` or `.kicker`).
  Stacked on narrow screens; from 69rem the action sits on the heading's
  baseline row (`grid-template-columns: minmax(0,1fr) auto`).
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

- **`.kicker`** — uppercase indigo eyebrow (`0.72rem`, `letter-spacing:
  0.14em`). The 6-series mono variant is `.mono-label.mono-label-indigo`.
- **`.band-link`** — underlined indigo action link, `text-underline-offset:
  0.2em`.

### Cards and chips

- **`.card`** — white slab, 2px ink border, `0.9rem` radius, hard soft shadow,
  `flex-direction: column` with `gap: 0.6rem`. `margin-top: auto` on a child
  pins it to the card foot (see `.course-link`, `.story-person`).
- **`.card-grid`** + **`.card-grid-2` / `.card-grid-3`** — the band's card
  columns; they collapse to one column on narrow screens.
- **`.chip`** — round uppercase mini-pill (`1.5px` soft border). Variants:
  `.chip-plain` (no uppercase), `.chip-green` (green outline text),
  `.chip-live` (green fill, white text), `.chip-ink` (ink fill, cream text).
- **`.avatar`** — striped round placeholder disc, `2.6rem`.

### Actions

- **`.cta`** — the block action: 2px ink border, `0.7rem` radius, hard shadow,
  `min-height: 3.25rem`. Variants: `.cta-primary` (green, white text),
  `.cta-secondary` (card surface, ink text), `.cta-ink` (ink fill, cream text,
  bubble shadow — "Continue course"), `.cta-lift` (bubble shadow on any CTA —
  "Start course now"), `.cta-compact` (row-sized: `min-height: 2.75rem`,
  `0.6rem` radius — Register buttons in list rows).
- **`.pill-button`** — round bordered control (theme toggle, subscribe links)
  with an optional `.dot` swatch. Platform dot colours: `.dot-green`,
  `.dot-bubble`, `.dot-clay`, `.dot-gold`.

  ```html
  <a class="pill-button" href="…">
    <span class="dot dot-gold" aria-hidden="true"></span>
    RSS
  </a>
  ```

- **`.signup-button`** — the masthead's green sign-up pill.

### Catalogue scroller

- **`.catalog-scroller`** — a scroll-snap column grid that hides its
  scrollbar; stays a carousel at every width (narrower auto-columns on phones
  so the next card peeks in). Pair with `.scroller-controls` /
  `.scroller-button` arrows (hidden without JavaScript via `html.js`), keep
  `tabindex="0"`, `role="group"` and an `aria-label` on the strip.

### Illustrations

- **`.doodle`** — a decorative drawing: `display: block; width: 100%`. Pages
  constrain aspect ratio per slot (see `.climb-card .doodle`). Always
  `alt=""`; see the illustration convention below.

### Masthead and footer

The masthead (`.masthead`, `.brand`, `.site-navigation`, `.masthead-actions`,
`.nav-link`, `.nav-auth`, account menu) and the footer/analytics dialog are
fully styled by the partial — copy the homepage's markup unchanged. The
navigation contract (toggle + `#site-navigation-links`, opened by
`core/site_navigation.js`, always visible without JavaScript) must not be
altered per page.

## Extended primitives (what the 6-series pages need)

Defined once in the partial so four engineers do not each invent their own.
Reference rendering: `_docs/design/mockups/datatalks-pages.source.html`.

### Stat tiles (courses index hero)

```html
<div class="stat-tiles">
  <div class="stat-tile">
    <strong>6</strong>
    <span>free courses</span>
  </div>
  …
</div>
```

Lavender tiles with ink border and hard shadow, in a 2×2 grid.

### Filter pills / segmented control

One row of toggles where exactly one is active (courses filters,
upcoming/past events, show notes / timestamps / transcript):

```html
<div class="filter-pills" role="group" aria-label="Filter courses">
  <button class="filter-pill" type="button" aria-pressed="true">All courses</button>
  <button class="filter-pill" type="button" aria-pressed="false">Active</button>
  …
</div>
```

The active pill (`[aria-pressed="true"]`, `.is-active`, or
`[aria-current="page"]` when the pills are links) fills ink with cream text.
State must be expressed through the ARIA attribute, not the class alone.

### Status pills

Mono uppercase state markers. Soft-rectangle (`0.45rem` radius) — deliberately
distinct from the round `.chip`:

```html
<span class="status-pill status-pill-live">
  <span class="status-dot status-dot-pulse" aria-hidden="true"></span>
  active
</span>
```

| Variant | Surface | Use |
| --- | --- | --- |
| *(bare)* | cream, soft border, body text | quiet facts: "enrolled · week 9 of 10" |
| `status-pill-live` | `--green-bright`, white text | a running cohort, a live event |
| `status-pill-open` | `--lavender-deep`, ink text | registration open, next cohort dates, workshop kind |
| `status-pill-wait` | `--sand`, olive text | waitlist, self-paced only, past events |
| `status-pill-mint` | `--mint`, `--green-ship` text | podcast kind, season/episode marker |

The words carry the state; colour is reinforcement only. `.status-dot` is the
small green disc (also usable beside a band heading — "● Active now");
`.status-dot-pulse` adds the slow pulse, which `prefers-reduced-motion`
disables.

### Progress bar

```html
<div
  class="progress"
  role="progressbar"
  aria-valuemin="0"
  aria-valuemax="9"
  aria-valuenow="7"
  aria-label="Homeworks submitted"
>
  <div class="progress-fill" style="width: 78%"></div>
</div>
<p class="mono-note">7 of 9 homeworks submitted</p>
```

White track, 2px ink border, `--green-bright` fill. Always pair with a mono
note stating the numbers.

### "You'll ship" line

```html
<p class="ship-line">you'll ship: a RAG assistant you can defend in review</p>
```

Italic, bold, `--green-ship`. Sits directly under a course title everywhere a
course is sold.

### Mono metadata

- **`.mono-label`** — uppercase mono eyebrow ("next assignment"); colour
  variants `.mono-label-green`, `.mono-label-indigo`.
- **`.mono-note`** — small mono note (date ranges, "#214", submission counts).

### Spec rows

Label-over-value facts, as a dashed-bounded strip (course page: STARTS /
LENGTH / PRICE / DEADLINES / REGISTERED):

```html
<dl class="spec-strip">
  <div class="spec">
    <dt class="mono-label">starts</dt>
    <dd><strong>Mon, Sep 14, 2026</strong></dd>
  </div>
  …
</dl>
```

(Reset `dd` margins in page CSS if the page uses `dl` markup.)

### List rows

The dashed-divided row pattern for index surfaces (self-paced course table,
podcast episodes; the events index combines it with the date rail):

```html
<div class="row-list">
  <div class="list-row" style="--row-cols: 1.6fr 1.4fr 1fr 0.8fr">
    …one cell per column…
  </div>
</div>
```

Rows stack on narrow screens; from 48rem each row lays out to `--row-cols`
(default `minmax(0,1fr) auto`). The row owns the dashed bottom border — never
draw separators inside cells.

### Play disc and player frame

```html
<span class="play-disc" aria-hidden="true">
  <svg viewBox="0 0 24 24" focusable="false">
    <path d="M9 6.5l9 5.5-9 5.5z"/>
  </svg>
</span>
```

Bubble-lavender disc with an ink glyph (`2.4rem`; `.play-disc-large` for
`3.5rem`; `.play-disc-card` for the white disc inside the player). The episode
page's video placeholder is `.player-frame` — a striped 16:9 slab with a
`.play-disc.play-disc-large` centred inside. `.avatar-striped` gives a guest
without a portrait the same stripes.

The mockup draws that centred disc white (`.play-disc-card`) because its frame
is empty stripes. The real episode page fills the frame with the episode's own
artwork and makes the whole frame the link to the recording, so it keeps the
bubble disc: a white disc disappears on a light thumbnail. Use
`.play-disc-card` only where the surface behind it is known.

### Timestamp / transcript rows

```html
<div class="timestamp-row">
  <span class="timestamp-mark">12:30</span>
  <p>Retrieval design: what to index and what to throw away.</p>
</div>
```

The same shape carries transcript rows with the speaker name as the mark.

### Module accordion (course page)

```html
<details class="module" open>
  <summary>
    <span class="module-number" aria-hidden="true">01</span>
    <span class="module-title">Introduction to Machine Learning</span>
    <span class="module-due">due Sep 22</span>
    <span class="module-marker" aria-hidden="true"></span>
    <span class="module-summary">Problem framing, CRISP-DM, first model.</span>
  </summary>
  <dl class="module-body">
    <div class="module-fact">
      <dt>video</dt>
      <dd>5 lessons · 1h 40m total</dd>
    </div>
    …
  </dl>
</details>
```

Wrap the set in `.module-list`. The number disc sits on `--bubble`; an open
module tints its summary lavender; the `+`/`−` marker is CSS-drawn from the
`[open]` state, so it needs no script.

### Form fields (course page register form)

```html
<div class="field">
  <label class="field-label" for="reg-email">Email</label>
  <input class="field-input" id="reg-email" type="email" readonly value="…">
  <p class="field-hint">Using your account email.</p>
</div>
```

`.field-input` styles `input`, `select` and `textarea` alike; readonly and
disabled fields sit on `--sand`.

### Panels

`.panel` + `.panel-mint` / `.panel-lavender` (+ `.panel-outlined` for the 2px
ink border) — the tinted inset blocks: "what you'll build", "next assignment",
"questions before you start?".

### Date rail (events index)

```html
<div class="when">
  <strong>Aug 17, 2026</strong>
  <span class="when-day">Monday</span>
  <span class="when-time">14:00 CEST</span>
</div>
```

A `--bubble` left rule with the date stacked beside each event card; typically
the first cell of a `.list-row` (`--row-cols: 6.5rem minmax(0,1fr)`).

## Responsive rules

Breakpoints in use:

| Query | What changes |
| --- | --- |
| `max-width: 47.99rem` | the narrow layout: **reshape, not reflow** |
| `min-width: 40rem` | shell padding grows; `.card-grid-2` becomes two columns |
| `min-width: 48rem` | `.list-row` adopts its `--row-cols` columns |
| `min-width: 62rem` | band padding grows; `.card-grid-3` becomes three columns; pages open their wide grids (hero, featured, wiki) |
| `min-width: 69rem` | the full navigation link row replaces the Menu toggle (the mockup's own 1142px width sits just under this) |

The narrow principle: below 48rem a surface changes shape rather than merely
squeezing. On the homepage the hero drops its drawing and fineprint, the climb
and events become dashed lists, stories collapse their chips to one mono-style
line, and the wiki graph becomes a centred chip cloud. New pages should make
the same kind of decision (e.g. the self-paced table stacks each row; the
events row puts the date rail above the card) instead of letting a grid wrap
arbitrarily. Shared narrow behaviour (card-grid gaps, the catalogue's narrower
carousel columns) already lives in the partial; put page reshaping in the
page's own `@media` blocks after the include.

## Accessibility guarantees

The partial guarantees, and every page must preserve:

- **Focus**: a global `:focus-visible` outline — `3px solid var(--focus)`,
  `outline-offset: 2px` — visible on both themes. The `#main-content`
  landmark suppresses its own outline; the `.skip-link` becomes visible on
  focus. Never remove an outline without an equal replacement.
- **Target sizes**: interactive controls are at least `2.75rem` (44px) tall
  (`.nav-link`, `.pill-button`, `.filter-pill`, `.cta`, `.field-input`,
  footer controls). Compact-looking mockup buttons still get `min-height:
  2.75rem` — a deliberate deviation from the export.
- **Contrast**: body and muted text pass 4.5:1 on every band in both themes;
  the new pairs were checked (olive on sand ≈ 5.5:1 light; `--green-ship` on
  cream ≈ 6.3:1). White-on-`--green-bright` pills flip to dark text
  (`#14172a`) in dark mode. State is never colour-only — pills and dots always
  carry words.
- **Decorative artwork**: every illustration has `alt=""` (see below); every
  decorative inline SVG and dot has `aria-hidden="true"` and
  `focusable="false"`.
- **Motion**: `prefers-reduced-motion: reduce` disables all animation,
  transitions and smooth scrolling globally. The status-dot pulse and the
  scroller's smooth scroll both honour it.
- **Structure**: one `h1` per page; bands are `section` elements labelled by
  their heading (`aria-labelledby`); the skip link precedes everything;
  disclosure widgets use native `details`/`summary` or a real `button` with
  `aria-expanded`.

## Illustrations

Artwork lives in `core/static/core/illustrations/` as **trimmed transparent
WebP**. The convention:

- Only the outer page background is made transparent. The drawing keeps its
  own white fills (shirts, paper, screens) so it stays readable on the dark
  theme without a plate behind it.
- Files are trimmed to the ink, so natural sizes vary; the consuming slot
  boxes them (`aspect-ratio` + `object-fit: contain`) to keep a shared rhythm.
- Illustrations are **decorative**: always `alt=""`, `decoding="async"`, and
  `loading="lazy"` for anything below the fold (the hero image loads eagerly).
  The surrounding copy carries the meaning.
- The homepage's include (`templates/core/_home_illustration.html`) is the
  worked example; give a new page its own variant map rather than overloading
  the homepage's.

## Building a new page in this system — checklist

1. Start from `templates/core/home.html`'s skeleton: `<!DOCTYPE html>`, the
   `no-js`/`js` class swap, meta/OG tags, one `<style>` whose first content is
   `{% include "core/_design_system.html" %}`, then page rules.
2. Copy the masthead, footer include (`core/_site_footer.html`), skip link,
   dark-mode bootstrap scripts and the script includes
   (`timezone_preference.js`, `core/site_navigation.js`,
   `core/accessibility.js`, `core/analytics_preferences.js`) unchanged. Set
   `aria-current="page"` on the page's own nav link.
3. Compose the page as `section.band` elements with `.shell` inside; pick band
   colours from the band table; use `.band-head` for every section heading.
4. Reach for the inventory before writing CSS: cards, chips, CTAs, status
   pills, list rows, spec rows, panels. New CSS in the page is for layout that
   is genuinely page-specific. If a primitive is missing, add it to the
   partial and to this document — do not fork it locally.
5. Colours only via tokens; mono only via `--font-mono`; sizes from the type
   scale; take disputed values from `datatalks-pages.source.html`, not from
   PNG measurement.
6. Design the narrow layout deliberately (reshape, not reflow) in the page's
   own `@media (max-width: 47.99rem)` block.
7. Keep the template readable: one opening tag per line, structural template
   tags alone on their line (`content/tests/test_public_template_source.py`).
8. Verify: both themes, 1440×900 and 390×844 screenshots, keyboard pass
   (skip link, focus visible, menus operable), and the page-owning tests plus
   `make lint format-check typecheck terminology-check`.
