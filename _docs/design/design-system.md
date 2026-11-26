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
surface, with a page-specific reason. The breakout grows to the home shell's
content box (`--shell-content`) and never past it.

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

A colour one page needs is added as a token in that page's own style block rather
than in the shared partial. A page that implements a designer comp names the comp's
one-off colours there — the catalogue's comp palette, the family landing's
`--band-green` family — as a page-local `:root` block mirrored in `body.dark-mode`
(a comp ground deepens in dark rather than flipping), so no rule carries a
light-only literal. The rule the page cannot escape is the one above: hexes live in
the token block, never inline in a rule, and every text pair that sits on a
page-local colour holds 4.5:1 in both themes.

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
| `--line` | `#1e2136` | `#454b6b` | ink edge of `--border-strong` (buttons, drawn marks) |
| `--line-soft` | `#d6d9e8` | `#333855` | soft frames, rules and seams |
| `--green` | `#2b7a35` | `#4cb85a` | primary action, hub accents |
| `--green-bright` | `#3ea94b` | `#4cb85a` | live markers, progress fill |
| `--green-deep` | `#41704e` | `#8fd39c` | quiet green notes |
| `--indigo` | `#5a62c4` | `#a6adf0` | links, time marks |
| `--indigo-soft` | `#c9cdf2` | `#3b4270` | soft indigo fills, avatars |
| `--shadow` | `rgba(30,33,54,.9)` | `rgba(0,0,0,.7)` | hard CTA shadow |
| `--shadow-soft` | `rgba(30,33,54,.16)` | `rgba(0,0,0,.45)` | the popover blur |
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

### Event art tokens

The event picture drawn in HTML (`public/_event_art.html`, the home event tiles)
takes its palette from `--art-<kind>-{ground,shape,pill,pill-ink,accent}` for the
four kinds (webinar, podcast, workshop, conference), plus `--art-line` (portrait
ring, pill outline) and `--art-shadow` (their offset shadow). Light values are
banner-generator `dtc-event-speaker`'s own, so the art matches the PNG. Dark
restates every one: a single ground a step lifted from `--card` (`#31364a`) so art
and tile read as one surface, a muted but clearly hued blob per kind (gold, not
brown, for podcasts), light pills under `#1e2136` text, date accents ≥ 4.5:1 on the
ground, the ring in the light ink and the shadow near-black.

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
| Chip | `0.72rem` | uppercase, letter-spaced |
| Status pill / mono label | `0.66rem` | mono, uppercase, `letter-spacing: 0.09–0.1em` |

Headings are weight 800, `letter-spacing: -0.015em`, `line-height 1.12` (tighter at
the biggest sizes). The prose rows apply **only inside `.prose`**. A body's own
headings stay below the band `h2`, so a section of prose never competes with the
heading of the band it sits in.

## Spacing

Every margin, padding and gap in page and partial CSS is a token from one scale,
defined once in `templates/core/_design_system.html` `:root`. Spacing has no theme,
so dark mode redefines nothing.

### The scale

| Token | Value |
| --- | --- |
| `--space-1` | `0.25rem` |
| `--space-2` | `0.5rem` |
| `--space-3` | `0.75rem` |
| `--space-4` | `1rem` |
| `--space-5` | `1.5rem` |
| `--space-6` | `2rem` |
| `--space-7` | `3rem` |
| `--space-8` | `4rem` |

### The roles

Reach for a role first. A role names *why* two things are apart, so the same
relationship gets the same distance on every page. A bare scale step is for
component internals no role names (a pill's padding, a table cell, a form row).

| Role | Value | Where it applies | Examples |
| --- | --- | --- | --- |
| `--space-inline` | `--space-2` | icon ↔ label, a row of chips or pills | `.past-supporters`, chip rows |
| `--space-tight` | `--space-2` | a heading ↔ its own subline; label ↔ value | `.band-head p`, `.band-head` row gap, `.card` gap (title ↔ text) |
| `--space-eyebrow` | `--space-3` | a page header's status pill or breadcrumb trail ↔ its `h1` — one value on every page header | `.*-hero h1`, `.person-identity h1`, `.article-hero .breadcrumbs`, `.course-hero .breadcrumbs`, `.event-head` |
| `--space-stack` | `--space-4` | paragraph ↔ paragraph; `h1` ↔ hero lede; blocks inside a card | `.prose > * + *`, `.*-lede` |
| `--space-head` | `--space-5` | a heading block ↔ the content it introduces (including an FAQ block under its heading); hero lede ↔ hero actions | `.band-head`, `.article-faq`, `.sponsor-section-heading`, `.module-section-heading`, `.admin-head`, `.*-hero-actions` |
| `--space-grid` | `--space-5` | between cards in a grid, list or scroller of cards | `.card-grid`, `.catalog-scroller`, `.stories-scroller`, `.featured-supporters`, `.episode-navigation` |
| `--space-card` | `--space-5` | card and panel padding | `.card`, `.catalog-card`, `.faq-panel` (block padding less the rows' own `--space-3`) |
| `--space-section` | `--space-7` | between sections inside one band | `.band-head-secondary`, `.family-chapter`, `.member-home-section + …`, `.docs-chapter`, `.course-certificate`, `.course-closing` |
| `--space-band` | `2.5rem`, `--space-7` at ≥62rem | a band's `padding-block` (and the page header, which is a band) | `.band`, full-bleed breakouts that act as bands (`.zoomcamp-steps`, `.courses-faq`) |

Two shapes recur and have one answer each:

- **Section seams.** A section divided from the previous one by a rule takes
  `margin-top: var(--space-section)` *and* `padding-top: var(--space-section)` —
  the section distance on each side of the rule. A section without a rule takes
  only the margin (`.band-head-secondary`).
- **Cancelling a band's padding.** An element that must run flush to its band's
  edge (an ink closing strip, a full-bleed last section) pulls itself out with
  `calc(var(--space-band) * -1)`, never with a copied literal, so it follows the
  band at every width.

Prose is a typographic context, not a stack of sections: inside `.prose` a heading
sits `--space-6` (`h2`) or `--space-5` (`h3`–`h6`) below the block before it and
`--space-3` above the block it heads, and blocks are `--space-stack` apart.

### Rules

1. Every `margin*`, `padding*`, `gap`, `row-gap`, `column-gap` and `inset*` value in
   page and partial CSS is `0`, `auto`, a `--space-*` token, or an expression of
   tokens (`calc(var(--space-2) * -1)`, `clamp(var(--space-5), 4vw, var(--space-7))`).
2. Heading → content is always `--space-head`. Do not put a margin on a section `h2`
   to space it from its content; wrap it in `.band-head` and let the head carry it.
   Do not add a `margin-top` to the first block after a `.band-head`.
3. Sections inside one band are always `--space-section` apart.
4. A grid's column formula uses the same token as its gap
   (`calc((100% - 3 * var(--space-grid)) / 4)`), so the two cannot drift.
5. Off-scale literals are allowed only for hairline optical nudges of at most
   `0.15rem` / `2px`, and for reviewed optical alignments listed with a reason in
   the test's `ALLOWED` table (the wordmark's `0.28rem` lift). Component sizes —
   `min-height`, `width`, the 2.75rem target height, `font-size`,
   `scroll-margin-top` — are not spacing and are not scanned.
6. HTML email templates keep literal pixels: mail clients do not support custom
   properties.

### Enforcement

`core/tests/test_design_spacing_tokens.py` scans every template under
`templates/` and `*/templates/` (CSS comments stripped) and fails on any raw
`rem`/`px` literal in a spacing declaration outside the hairline limit and its
`ALLOWED` table. It also pins the scale values, the role names, and the structural
rules (`.band`, `.band-head`, `.band-head-secondary`, `.card-grid`) to their roles.
Adding an `ALLOWED` row is a design decision with a written reason, not a way to
silence the test.

## Shell, measure

- **Shell**: `.shell` centres content at `--shell` = `76rem`, `padding-inline:
  var(--shell-gutter)` (`var(--space-4)`, `var(--space-5)` at ≥40rem). The box
  inside that padding is `--shell-content` (`76rem` less two gutters, `73rem` on
  a desktop): the homepage's column, and the footer's.
- **Home shell = maximum content width; pages may be narrower, never wider.** No
  content on any page -- a breakout section, a wide table or chart, the inner
  column of a full-bleed band -- may start left of, or end right of, the home
  shell's content box. Full-bleed band *backgrounds* may run to the viewport
  edge; what sits on them is capped at `--shell-content` with
  `--shell-gutter` as the band's inline padding. `.shell-breakout` and the
  wide-table breakout are bounded by it, so a page cannot widen past it by
  using them. Enforced in a browser by
  `playwright_tests/test_shell_width_guard.py`, which measures every visible
  content box in `<main>` of every public page type at 1440, 900, 705 and
  390px against the footer's content box.
- **Measure**: `--measure` = `38rem`, about 70 characters. `.prose-reading` and
  `.prose-lede` are held to it. Ordinary pages use `--content-width` = `56rem`
  (the course platform's `.app-content-shell` width) through `.content-shell`
  rather than choosing a local max width; prose inside the column stays at the
  measure.
- **Reading measure vs card-list measure.** Reading text — an article body, a wiki
  topic's title, summary and body, an episode's hero and overview — holds the
  reading column, `--reading-width` (`44rem` shell, 656px of text at a desktop
  width): the blog article's measure, which the owner judged the better read.
  A list of cards under that text — the episode timeline ("Related episodes",
  "Podcast episodes about …"), topic cards, event/article/book cards — may take
  the wider card-list column, `--content-width` (`56rem` shell, 848px), so a
  card's platform links and tags fit on one line. Both columns are centred on
  the same axis, the way the homepage aligns its wide and narrow blocks. A card
  list in its own band uses `.content-shell`; one inside a reading column adds
  `.shell-breakout.shell-breakout-cards`. Enforced in a browser by
  `playwright_tests/test_shell_width_guard.py::test_reading_text_and_card_lists_hold_their_measures`.
- **Index and hub pages hold the content column too.** `/docs/`, `/wiki` (and its
  search and special pages), `/blog`, `/podcast`, `/events`, `/books`, `/people`,
  `/sponsors` and `/slack` all sit at `--content-width`, so their titles share one
  left edge with each other and with the docs and wiki pages a reader clicks into. A
  hub does not break its hero or card grids out to the full shell; a card grid picks
  a column count that fills its rows, or lets its last row stretch. Only the
  homepage and the `/courses` landing use the full `--shell`.
- **Index titles use `.page-title`** (the "Page h1" size above). Pages put the class
  on their `h1`; they do not restate the size in page CSS.
- **`.hero-split` / `.hero-art`** — an index hero with a drawing: the copy column and
  a fixed `15rem` picture column at the content column's right edge, so there is no
  dead band between words and picture. The drawing drops below `48rem`. The picture
  is a light/dark pair through `core/_hero_illustration.html` (`art=` names
  `core/illustrations/<art>.webp` and `<art>-dark.webp`).
- **Borders, radius and shadows** are the border system below — tokens, never raw
  values.

## Borders

One rule decides every edge: **content sits in soft frames; only the things you
press are drawn in ink.** A card, panel, popover, image, table or chart has a 1px
`--line-soft` edge and no shadow — the `/events` and `/podcast` cards are the
reference. The heavy 2px ink edge and the hard offset shadow belong to buttons and
calls to action (and to the drawn marks that share their hand: numbered discs,
portraits, toggles). Dashes mean a seam between parts of a page, nothing else.

Every `border`, `border-*`, `border-radius`, `box-shadow` and `outline` in page and
partial CSS is one of the tokens below, defined once on `:root, body` in
`templates/core/_design_system.html`. `core/tests/test_design_border_tokens.py`
fails on a raw width, radius or shadow anywhere else; the few shapes drawn out of
borders (a chevron, a tick, a chart key) are reviewed rows in that test.
`border-color` stays free for states (hover, invalid, active).

### Tokens

| Token | Value | Role |
| --- | --- | --- |
| `--border-soft` | `1px solid var(--line-soft)` | every card, panel, popover, media frame, chip |
| `--border-strong` | `2px solid var(--line)` | buttons, CTAs, pill controls, toggles, progress tracks, numbered discs, portraits |
| `--border-field` | `1px solid var(--field-edge)` | text inputs, selects, textareas (`--field-edge` is ink at 52% on the card, full `--line` in dark) |
| `--border-quiet` | `1.5px dashed var(--line-soft)` | a placeholder or the quietest notice (`.prose-unavailable`, `.entrance-notice`, empty states) |
| `--rule` | `1px solid var(--line-soft)` | a divider *inside* one block: list rows, table rows, FAQ folds, menu groups, a card's footer |
| `--seam` | `2px dashed var(--line-soft)` | the seam *between* parts of a page: band bottoms, masthead, footer, page sections and chapters, form action rows, `hr` |
| `--radius-sm` | `0.4rem` | status pills, inline `code`, small marks, the focus ring |
| `--radius-control` | `0.6rem` | CTAs, inputs, square buttons, nav links, option tiles, date tiles |
| `--radius-media` | `0.75rem` | images, video frames, tables, charts, code blocks, blockquotes, callouts |
| `--radius-card` | `1rem` | cards, panels, slabs, popovers and menus |
| `--radius-pill` | `999px` | pills, chips, round buttons, portraits (drawn discs use `--drawn-circle*`) |
| `--shadow-cta` | `0.3rem 0.3rem 0 var(--shadow)` | `.cta` and other block buttons |
| `--shadow-cta-sm` | `0.2rem 0.2rem 0 var(--shadow)` | small pressable controls (consent choices, the video play button) |
| `--shadow-cta-pill` | `0 3px 0 var(--shadow)` | the masthead signup pill and its package twin |
| `--shadow-cta-bubble` | `0.25rem 0.25rem 0 var(--bubble)` | `.cta-ink` / `.cta-lift` on the ink band |
| `--shadow-popover` | `0 0.75rem 1.75rem var(--shadow-soft)` | the one blur: menus, popovers, the consent dialog, sticky save bar |
| `--outline-focus` | `3px solid var(--focus)` | every focus ring (with `outline-offset: 2px`) |

### Roles

| Role | Border | Radius | Shadow | Used by |
| --- | --- | --- | --- | --- |
| Card (static or link) | `--border-soft` | `--radius-card` | none | `.card`, `.event-item`, `.home-event`, `.module`, `.stat-tile`, `.topic`, `.graph-frame`, `.newsletter-card`, `.featured`, `.panel-outlined`, `.featured-supporter`, `.course-specs`, `.lesson-list`, `.family-syllabus-list`, `.project-section`, `.event-register` |
| Interactive card hover | border tints to `--indigo-soft`, lift 2px, `→` cue | — | none | `.interactive-card.interactive-lift` |
| Popover / menu / dialog | `--border-soft` | `--radius-card` | `--shadow-popover` | nav menus, mobile nav, `.analytics-preferences`, country combobox, `.flag-dialog`, `.save-bar` |
| Media frame | `--border-soft` | `--radius-media` | none | `.prose img`, `.prose-chart-card`, `.prose-scroll` (tables), `.prose pre`, `.player-frame`, `.unit-video`, `.book-cover`, event media, instructor photos |
| In-content block | `--border-soft` (or `--border-quiet`) | `--radius-media` | none | `.callout`, `.prose blockquote`, `.cb-alert`, `.book-replies`, empty states |
| Input | `--border-field` | `--radius-control` | none | `.field-input`, `.form-control`, `.cb-field`, entrance and event-register fields; read-only values use `--border-soft` |
| Search field | `--border-strong` | `--radius-control` | none (the button beside it keeps `--shadow-cta`) | `.search-row` (`core/_search_row.html`): the field takes its Search button's ink edge, radius and 3.25rem height so the pair reads as one control; focus is `--outline-focus`. Every search box is this partial. The newsletter signup (`.newsletter-form-row`, `core/_newsletter_signup_form.html`) is the same pairing with Subscribe |
| Button / CTA | `--border-strong` | `--radius-control` or `--radius-pill` | a `--shadow-cta*` token | `.cta`, `.signup-button`, `.pill-button`, `.filter-pill`, `.scroller-button`, `.cohort-tab`, nav toggles |
| Chip / status pill | `--border-soft` | `--radius-pill` / `--radius-sm` | none | `.chip`, `.status-pill`, `.cb-badge`, past-supporter names, inline `code`; state variants change only `border-color` |
| Drawn mark | `--border-strong` | `--drawn-circle*` / `--radius-pill` | none | `.step-number`, `.module-number`, `.promise-mark`, `.play-disc`, `.avatar` and portraits, `.progress` |
| Rule (inside a block) | `--rule` | — | — | `.row-list` rows, table rows, `.faq-fold`, `.module-fact`, menu groups, timestamp and chapter rows |
| Seam (between page parts) | `--seam` | — | — | `.band`, `.masthead`, `.site-footer`, `.spec-strip`, page sections and chapters, `.cmp-form-actions`, `.prose hr` |
| Focus | `--outline-focus` | — | — | every `:focus-visible`, including whole-card focus |

What this rules out: an ink-bordered card or panel, a hard shadow on anything that
is not pressed, a solid ink rule between page sections, a dashed divider inside a
card, a radius that is not one of the five steps.

### No accent left-borders

The system never marks a block with a single-side rail — no `border-inline-start`, no
`border-left`, no shorthand equivalent, however quiet the colour.

A block that has to stand apart takes **a surface and a full border**
(`background: var(--card)` with `var(--border-soft)`, the tint carrying the
tone — `--sand` for attention, `--border-quiet` for the quietest notice) or the
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

**A page that implements a designer comp may close on the comp's own band.** The
family landing's mock closes on a full-bleed green band, and the comp palette is
available to such a closing through page-local tokens — but the band system has
no green ground, so the course family landing closes on the sanctioned mint
panel on the lavender ground instead: the action line at display size, the
strongest honest actions beside it, the page's last word before the shell takes
over. The catalogue draws the comp green on controls only. A comp band never
recolours the hero or the shared content ground, and a page with no comp behind
it does not get one. An *interior* beat of the same comp follows the same rule:
the family landing sets its mock's graduate-stories chapter on the comp's
full-bleed ink ground — the mock draws that chapter on ink — from the page-local
`--band-ink` tokens through the `.shell-breakout` mechanism, with the section's
own card tokens re-declared so no text pair sits on a colour that flips with the
theme.

**Outside the rule for now**: the course platform's task surfaces (homework,
submissions, statistics, the leaderboard family, enrolment, registration,
wrapped) and Studio. The dashboard no longer belongs on this list: it read as a
landing page at the full 76rem shell, so it now sits at the same narrow
`.content-shell` default as its sibling task surfaces instead of overriding it.

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
  subline `p`, stacked. It never holds a link — a
  section's link is its `.band-more` (see **Section links**).
  `.band-head-secondary` adds `--space-section` above a second head inside one band;
  the head itself sits `--space-head` above its content.

  ```html
  <div class="band-head">
    <div>
      <h2 id="…">Heading</h2>
      <p>Subline.</p>
    </div>
  </div>
  ```

- **`.band-link`** — underlined indigo action link.
- **No eyebrows: never place a small label above a heading.** No kicker, section
  label, category word, count or context line (mono, uppercase, letter-spaced or
  not) sits above an `h1`, `h2` or `h3` — on a page head, a band head, a card or a
  list row, with no exceptions. A fact that matters (a count, a role, a cohort, a
  season/episode) goes in the lede or in the quiet `.mono-note` line under the
  title; a fact the breadcrumb or heading already carries is dropped. Still fine:
  a date rail beside a row, an event's date line, a breadcrumb trail, a step
  number, a label inside a spec row or table, and a stat label under its number.
- **`.breadcrumbs`** — the trail on every page below a hub, and its one way back to
  the list it came from. The blog article's trail is the reference every page
  matches: underlined `--muted` links at `0.78rem` that turn indigo on hover, the
  current page as the last crumb in `--muted` plain text, a line of text and never
  a button. The `/` separator is CSS-drawn with empty alternative text
  (`content: "/" / ""`) so it is never announced. Links keep a `2rem` minimum
  target height. The trail starts on the left edge of the
  column the page's content sits in — on a two-column detail page (an event) that
  is the left edge of the whole layout, not the shell's edge. Markup contract: a `nav`
  with an accessible name holding an `ol`, one `li` per level, every ancestor a link,
  and the current page on `li[aria-current="page"]` as plain text — never a link.

  Build the trail with the shared `{% breadcrumbs %}` tag rather than hand-writing
  list items; an empty level is skipped.

  Use it on every page below an index: articles ("Blog / …"), books, wiki topics,
  events ("Events / …"), podcast episodes ("Podcast / Season 25 / …"), people
  ("People / …"), course and academy pages. No page draws an ad-hoc "← all …" link
  instead. **Index pages and the homepage do not carry one** —
  the masthead already marks them with `aria-current="page"`. A page offers one way
  back, not two: a page with a back-link does not also carry a trail, and a page
  whose parent index does not exist carries neither.

### Cards and chips

- **`.card`** — white slab, `--border-soft`, `--radius-card`, no shadow, column flex
  with `gap: var(--space-tight)` and `padding: var(--space-card)`. `margin-top: auto` on a child pins it to the card foot.
- **`.card-grid`** + **`.card-grid-2` / `.card-grid-3`** — the band's card columns:
  one column on a phone, two from the tablet tier (`36rem`; a lone odd card
  spans the last row), and `.card-grid-3` three from `62rem`. A grid of stage
  cards keeps three from `48rem` and lays the cards on their side below it.
- **`.interactive-card.interactive-lift`** — the whole-card link treatment. Hover and
  keyboard focus lift the surface 2px and reveal a fixed bottom-right `→` by opacity
  only. The arrow never travels or bounces; reduced-motion removes the lift while
  preserving the keyboard cue.
- **`.event-card`** — the card for one event. The whole card is the link: the title is
  the one real anchor and its `::after` covers the card, so the accessibility tree
  holds exactly one link per event. Anything else interactive inside sits above that
  overlay with `position: relative; z-index: 1`.
- **Stage card** (`.stage-card`, numbered `.step-number` badge) — one numbered stage on
  a plain `.card`: badge, illustration, heading, short description. It is a real shared
  include, `templates/core/_stage_card.html`, not just shared CSS — the homepage's
  "climb" section and a course family landing's "journey" section both render it, so
  the two cards cannot drift apart the way their hand-copied badge sizes once did.

  ```html
  {% include "core/_stage_card.html" with step_index=1 heading="…" description="…" illustration_template="core/_home_illustration.html" illustration_variant="stuck" %}
  ```

  `step_index` (1/2/3) picks the badge digit and its `.step-number-2` /
  `.step-number-3` fill. `illustration_template` is the artwork include rendered
  inside, with `illustration_variant` / `illustration_loading` passed through to it.
  Both the homepage and the course family landing draw the plain grid-flow card
  (illustration first, the badge grouped with the heading). The older `on_rail` shape
  (badge astride the corner, a dashed rail behind the row) is no longer used by any
  page.
- **Founder story** (`.founder-story`) — the member-story card carrying the
  community's own beginning: two labelled excerpts ("How it started", "Why it's free")
  that read in sequence rather than side by side, then the founder row. A real shared
  include, `templates/core/_founder_story.html`, so the About page (`/about`) and the
  homepage's About band cannot drift apart. Its companion,
  `templates/core/_community_stats.html`, draws the community's survey figures as
  `.stat-tiles`. Both hold no copy and no number of their own: they render one
  published editorial record (`content.tour_content`), and draw nothing without one.

  ```html
  {% include "core/_community_stats.html" with tour_content=tour_content tiles_variant="stat-tiles-row" only %}
  {% include "core/_founder_story.html" with tour_content=tour_content founder=founder only %}
  ```

  `tiles_variant` is optional: the About page passes `stat-tiles-row` (four figures in
  one row); the homepage leaves it out. `quote_only=True` draws the founder card with
  just the "why it's free" quote and the founder row, no mono labels: the homepage's
  About band tells "how it started" as plain prose in its story column and sets this
  card beside it as a pull-quote over the figures.
- **`.chip`** — round uppercase mini-pill. Variants: `.chip-plain`, `.chip-green`,
  `.chip-live`, `.chip-ink`. **`a.chip-link`** is a chip that opens a page (a wiki
  concept): indigo border and text, underlined on hover.
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

  Three facts can each be missing and each degrades on its own: no portrait draws
  the person's initials in the same disc (`.person-chip-initials`, `aria-hidden`),
  and a portrait that fails to load is swapped for that disc by
  `core/media_fallback.js` (which also removes a card thumbnail that fails, so a
  card never shows a broken picture box); no profile keeps the name as a plain
  `span.person-chip-name`, not a link to nowhere; the portrait always carries `alt=""`
  because the name beside it is already the credit and a screen reader must not hear
  it twice. Give the picture its intrinsic square so the line cannot shift.

### Actions

- **`.cta`** — the block action: `--border-strong`, `--radius-control`, `--shadow-cta`,
  `min-height: 3.25rem`. Variants: `.cta-primary` (green, white text),
  `.cta-secondary` (card surface, ink text), `.cta-subtle` (transparent),
  `.cta-ink` (ink fill, cream text, bubble shadow), `.cta-lift` (bubble shadow on any
  CTA), `.cta-compact` (row-sized: `min-height: 2.75rem`).
  A CTA inside a Markdown body uses the `{.button}` marker (`{.button-primary}` for the primary
  variant); see `_docs/content-authoring.md`.
- **`.pill-button`** — round bordered control (theme toggle, subscribe links) with an
  optional `.dot` swatch: `.dot-green`, `.dot-bubble`, `.dot-clay`, `.dot-gold`.
- **`.signup-button`** — the masthead's green sign-up pill.

#### Section links

A link that leads from a section to the rest of its kind — "see all events →", "all
episodes →", "read the blog →", "all course guides →", "open the guide →" — is the
section's **`.band-more`** line: a `p.band-more` holding one `.band-link`, placed
**under the section's content**, immediately after its last block, on the content's
**left edge**, with the shared spacing above it.

```html
<div class="card-grid card-grid-3">…</div>
<p class="band-more">
  <a class="band-link" href="…">see all events →</a>
</p>
```

It is never in the heading row (`.band-head`) and never right-aligned; pages do not
restyle `.band-more`. Enforced by `core/tests/test_design_section_links.py`.

### State and metadata

- **Status pills** — mono uppercase state markers, soft-rectangle (`--radius-sm`),
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

- **`.mono-label`** — uppercase mono label (spec rows, table heads, stat labels;
  never above a heading — see **No eyebrows**); variants `.mono-label-green`,
  `.mono-label-indigo`.
- **`.mono-note`** — small mono note (date ranges, submission counts).
- **`.mono-code`** — an inline literal inside running copy: a Slack channel, a
  command, a file name. Mono face at `0.92em`, no surface of its own. Put it on the
  `code` element the content already deserves. It is a typographic mark, not a state
  marker (`.status-pill`) and not a metadata line (`.mono-note`).
- **`.ship-line`** — italic bold `--green-ship`; sits directly under a course title
  everywhere a course is sold.
- **`.stat-tiles` / `.stat-tile`** — white tiles in the soft card frame (`--border-soft`, `--radius-card`, no shadow),
  in a 2×2 grid: a number as `strong`, a label as `span`. **`.stat-tiles-row`** is the
  one-row modifier for a strip of 2-4 live facts (the course family landing's outcome
  numbers, the courses index's own facts strip, the About page's survey figures):
  shorter tiles, and the tile count
  itself decides the column count — a lone odd tile spans the full row below 40rem,
  and a fourth tile opens a 4-column row at 40rem instead of wrapping onto its own
  row. These pages share this one component rather than each inventing a grid.
- **Progress bar** — white track, `--border-strong`, `--green-bright` fill. Always pair
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
  `row_eyebrow`, `row_pill`/`row_pill_variant`/`row_pill_extra`, `row_meta`,
  `row_summary`, `row_tags`/`row_tags_label`, `row_credits`, `row_class`/`row_hook`.
  Always pass `only`.

  Inside the card the order is fixed: leading mark, pills, `h3` title, then one
  quiet `.mono-note` line joining `row_eyebrow` (the row's role, e.g. "guest"), a
  podcast row's season/episode and `row_meta` (a count such as a book's "41 questions
  answered" — a fact, not a state, so never a pill), clamped summary, tags
  (`ul.archive-tags` of `a.chip-link`, saying why the row is on this page; they sit
  above the stretched card link), credited people last as
  `.person-chips.card-credit`. The rail is `.date-rail`, day above year. An episode
  row is the play-mark row with `row_class="episode-row"`.

  Drawn by `/blog`, `/books`, `/podcast`, an episode page's related episodes (the
  episodes sharing wiki concepts with it, the shared concepts as tags; there is no
  previous/next pair — episodes are not a sequence, and the season link in that
  band's head is the way to browse in order) and the articles and books bands of a
  person's profile, through `public/_article_row.html` and `public/_book_row.html`
  (the /blog and /books rows, so a profile draws the same card). A profile's episodes
  are the podcast timeline (`public/_podcast_episode_timeline.html`, as on a wiki
  page and an episode page) and its events the events timeline
  (`public/_event_timeline.html`, the /events card); its bands take the
  `--content-width` card-list column. **Not** by the events index, whose rail carries three facts including a
  start clock — that is `.when` below, and its card is the shared `.event-card`.

- **Date rail (events index)** — set the way `.date-rail` is set: position and type,
  no rule. The event's kind is the rail's last line (`.when-kind`, bold uppercase
  indigo mono), never a boxed pill above the title; the event page leads its
  dateline with the same word and draws no past/upcoming pill — the date says it.

  ```html
  <div class="when">
    <strong>Aug 17, 2026</strong>
    <span class="when-day">Monday</span>
    <span class="when-time">14:00 CEST</span>
    <span class="when-kind">webinar</span>
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

  It sits inside the `.row-list`, so folded rows keep the dashed division (a profile
  band folds whole cards after its list: episodes, event days or rows). `<details>`
  does the work: keyboard behaviour and announced expanded state come free and it
  opens with JavaScript off. A bare "Show more" makes the reader click to find out how
  much they are in for.

- **FAQ fold (`.faq-panel` + `.faq-fold`)** — the one way the site writes a list of
  questions: the article FAQ, the course-family FAQ, the `/faq/` pages and the
  dashboard's per-homework breakdown all draw it.

  ```html
  <div class="faq-panel panel panel-outlined">
    <details class="faq-fold" id="…">
      <summary class="faq-fold-summary">
        <h3 class="faq-question-title">Question?</h3>
        <span class="faq-fold-marker" aria-hidden="true"></span>
      </summary>
      <div class="prose faq-answer">…</div>
    </details>
  </div>
  ```

  A white card padded `--space-card` (block padding less the rows' own), rows
  `--space-3` above and below with a dashed rule between them, the chevron centred on
  the question's first line in both states, and `--space-head` below the heading that
  introduces it. A page does not restyle the fold or wrap it in a `.row-list` (that
  draws a second rule under each row).

- **Timestamp rows** — `.timestamp-row` with a `.timestamp-mark` and a `p`. The same
  shape carries transcript rows with the speaker name as the mark.

### Panels, discs and scrollers

- **`.featured`** (`.featured-main`, `.featured-subline`, `.featured-desc`,
  `.featured-meta`, `.featured-actions`, `.featured-build`) — the featured slab: white,
  in the soft card frame (`--border-soft`, `--radius-card`, no shadow); its weight
  comes from its size and the mint build panel. It marks the one thing a page most wants the reader to act on: the homepage's featured course, a course
  landing's cohort to join, the registration page's account gate. Each page sets its
  own wide-screen columns; the slab itself lives in the partial.
- **`.panel`** + `.panel-mint` / `.panel-lavender` (+ `.panel-outlined` for the
  soft card edge) — tinted inset blocks. A course landing closes on one
  `panel-mint panel-outlined` holding its strongest action.
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
  per-module content; a page whose job is to show every deadline and state draws
  `.row-list` tables instead of folding them behind a click (the Academy syllabus is
  read week by week as list rows).

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

**A `select` draws the system's chevron, not the platform's.** `select.field-input`
(and `select.form-control`) sets `appearance: none` and draws the FAQ fold's indigo
2px V inset from the right edge, with `2.75rem` of right padding kept clear for it.
The mark is two gradient squares rather than an SVG data URL so it reads the
`--indigo` token and follows the theme; the invalid state keeps it.

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
  <ul>
    <li>…</li>
  </ul>
</div>
```

Rhythm comes from `.prose > * + *`. `.prose-paragraph` keeps the source's own line
breaks (`white-space: pre-line`), so its opening tag and value stay on one line. A
run of consecutive source list items (a hard-wrapped item keeps its continuation
lines) is grouped by the `prose_runs` filter into one real `<ul>` or `<ol>`. Headings keep the
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
- **`.prose-chart`** — a survey chart in an article body: a framed block on `--card`
  (`--border-soft`, `--radius-media`, no shadow) holding the title, an
  inline-SVG horizontal bar chart drawn from the block's typed data
  (`content/article_charts.py`, no script), a visually hidden data table, and the
  source caption. One series in `--chart-bar`; labels in `--body-text`, values in
  `--ink`. The SVG has no `viewBox`: bar lengths are percentages of the column and
  rows are fixed pixel heights, so text stays at body size on a phone.
- **`.prose-caption`** / `figcaption` — `--muted` at the meta size.
- **`.prose-action`** — a CTA inside a body uses the `{.button}` marker: a standalone
  `[Label](url){.button}` line renders as this paragraph holding the shared `.cta` control
  (`core.markdown_actions`, documented in `_docs/content-authoring.md`).

A richer body also carries `h4`–`h6` continuing the ladder, real `ul`/`ol`/`li`,
`--ink` strong, an italic `blockquote` on the card surface inside the soft media frame,
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

Every search box is one partial, `templates/core/_search_row.html`:

```django
{% url 'docs-home' as docs_home_url %}
{% include "core/_search_row.html" with action=docs_home_url input_id="docs-query" label="Search the documentation" placeholder="…" value=query %}
```

It draws a `form.search-row` with the label (screen-reader only, or above the row
with `label_visible=True`), a `type="search"` field named `q`, and a `cta-primary`
Search button. `listbox_id` turns the field into the wiki's live-results combobox.
The field and button are one control: the same 3.25rem height, the same
`--radius-control`, and the field takes the button's `--border-strong` ink edge
(the "Search field" role under Borders) with no shadow of its own. The field gives
way and the button keeps its size, so the pair stays on one line down to a phone,
where a long placeholder ends in an ellipsis. The row is capped at `34rem`. The
wiki hub and results, the docs hub and every docs page include it; a page places
the row (`classes`) but does not restate its size, edge or button.
`core/tests/test_design_border_tokens.py` fails if a public template draws its own
`type="search"` field.

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

**The footer** is one compact block at the masthead's shell width. It does not repeat
the masthead's sections: one main row has the brand and tagline on the left and, on
the right, only what the masthead lacks — Newsletter and Sponsors as two small links
(no underline until hover) — beside the social icons; one quiet rule, then the base
line with the legal row (Terms, Privacy, Analytics preferences, Impressum) and the
version. Analytics preferences is a `<button>` drawn and behaving exactly like the
legal links beside it — no `interactive-lift`. Below 48rem the brand stacks over a
full-width row with the links on the left and the icons on the right. It carries no
Academy links — the site-wide Academy stripe above it is the Academy route.

**The analytics choice** is one `<dialog>` in two states. While nothing is chosen it is
a non-modal consent banner fixed to the viewport foot, never part of the page flow;
`analytics_preferences.js` reserves its height (`html.has-consent-banner`,
`--consent-banner-space`) as the page's bottom padding and as the document's
`scroll-padding-bottom`, so every control keyboard focus scrolls to stops above the
banner, never behind it, and the last footer control can scroll clear of it. Escape
dismisses it without choosing. Reopened from the footer it is the centred modal
dialog.

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

## The Academy pages

The `/academy/` pages (`academy/templates/academy/`) are ordinary content pages:
each extends `core/content_page.html`, so the masthead, cream header, lavender
content band and footer are the shared ones. They share one page-level partial,
`academy/_styles.html`, included straight after the design system; it only lays
out primitives the system already owns. What each landing says, section by
section, is the blueprint in `_docs/design/landing-pages.md`.

- **Two columns, one per kind of page.** The landings (home, programs, sessions,
  the German guide), the FAQ, the articles index and the forms sit in the
  ordinary content column (`--content-width`) and every block fills it, so no
  block stops short and leaves a dead strip. The one exception is the home
  page's hero, which breaks out to the full shell (`.shell-breakout`) to set
  the copy beside the Academy's drawing in the course family hero's grid
  (owner, 2026-11-24); every chapter under it keeps the content column. A page
  read end to end (an article, a legal page) sits in the reading column the
  blog uses (`--reading-width`), header and body alike; an article also
  carries the blog article's trail, heading size, standfirst, mono date and
  person-chip byline. There is no
  Academy sub-navigation bar and no Academy footer.
- **The hero is the course family hero's title block**: heading and a 2-3
  sentence lede, one primary action (the waitlist, or "Choose my sessions"),
  one credibility line (portrait and sentence), and the key facts at its foot
  as the compact `.spec-strip` a course family landing closes its title block
  with (label over value), two rows of three. Only the home hero carries a
  drawing (light/dark `.doodle-variants`, dropped below 48rem as the homepage
  hero drops its own). No form, link list or status pill beside or above the
  copy.
- **Every chapter is the same frame**: a dashed seam and a real `.band-head`.
  Cards pair in `.card-grid-2` and mirror each other (the home's program cards
  carry the same three short lines and a `cta-secondary` pinned to the foot).
- **The syllabus is week by week**: each phase a small heading over `.row-list`
  rows, one row per week with "Week N" on the mono rail and the week's
  module(s) beside it, each an action title and its tangible output in
  `--green-ship`. The final project and interview preparation are chapters of
  their own drawn with the same rows (`academy/_weeks.html`).
- **One waitlist form per page.** On the home and program pages it closes the
  page in the mint `.panel` (heading, the limited seats and how you get in on
  the left, the form card on the right); the hero action points to it
  (`#signup-hero`). There is no floating waitlist bar. The sessions registration is the first chapter
  under the header, on the `.featured` slab. Reading pages close on a mint
  panel with one action to the waitlist.
- **Forms are one column**: every field full width over a full-width
  `.cta-primary`; the confirmation warning is a `.callout-attention` in the
  success state, shown once the reader has submitted, not on the empty form
  (owner, 2026-11-24). Choice groups are
  `fieldset`s whose `.form-check` options sit in one even row from 48rem (as
  many columns as options, so none is left alone on a row); the quick-question
  answers are `.filter-pill` toggles. A form card starts with its fields, and
  a session that has already happened is not offered.
- Lists of articles are the shared archive row (`public/_archive_row.html`);
  FAQ is `.faq-fold`; an article's Markdown tables sit in `.prose-scroll`.
- On a program page the questions come last, right before the closing
  waitlist.

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

The site has three tiers. Phone (390px) and desktop (1366px) are the tuned
references; the tablet tier between them is laid out on purpose, not left to
stretch the phone layout.

| Tier | Width | Shape |
| --- | --- | --- |
| Phone | below `36rem` (576px) | one column; actions full width; stage cards read as a list |
| Tablet | `36rem`–`61.99rem` (576–991px; a half-screen window is ~705px) | card grids two (or three) a row; actions at their own width; pictures centred and capped |
| Desktop | `62rem` and up | the wide grids and two-column heroes |

| Query | What changes |
| --- | --- |
| `max-width: 35.99rem` | phone only: hero, closing and card actions go full width (`width: 100%`); the plain stage card becomes a numbered list row |
| `min-width: 36rem` | the tablet tier opens: `.card-grid-2` and `.card-grid-3` show two columns, a stage-card grid shows three; `.stat-tiles-row` opens to one row |
| `36rem`–`61.99rem` | a lone odd card on a `.card-grid-3` row spans the whole row |
| `36rem`–`47.99rem` | stage cards turn on their side (drawing left in a 13rem column, words right), one a row |
| `max-width: 47.99rem` | the narrow layout: **reshape, not reflow** (hero drawings that drop, row-list stacking) |
| `min-width: 40rem` | shell padding grows |
| `min-width: 48rem` | `.list-row` adopts its `--row-cols` columns; `.hero-split` shows its drawing |
| `min-width: 62rem` | band padding grows; `.card-grid-3` becomes three columns; pages open their wide grids |
| `min-width: 69rem` | the full navigation link row replaces the Menu toggle |

**Tablet rules.** Between phone and desktop:

- **Grids fill the row.** A card grid shows two or more cards a row; a page grid
  of its own (`.slack-benefits`, `.tour-norms`, `.home-events`) opens at `36rem`
  too, with a lone last card spanning the row rather than leaving a hole.
- **No phone-style buttons.** An action is full width only below `36rem`. Put a
  page's `width: 100%` CTA rule in its own `@media (max-width: 35.99rem)` block,
  never in the `47.99rem` reshape block. (Form submit rows, `.cmp-form-actions`,
  keep their own full-width layout below 48rem: the form is its own narrow
  measure.)
- **Pictures are never drawn larger than on a desktop.** A hero drawing that
  stacks under its words is centred and capped (the homepage hero at `24rem`
  until it moves beside the words at `56rem`; the `/courses` collage runs as one
  row of four). Event art scales with its tile, so the homepage events show two
  a row below `48rem` and three (the fourth behind "see all events") until the
  desktop row of four.

Enforced in a browser by `playwright_tests/test_tablet_layout.py` (705px and
390px) and by `playwright_tests/test_shell_width_guard.py`, which includes a
705px viewport.

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

## Course landings

The course family landing (`courses/course_family.html`) sits in the ordinary
content column (`--content-width`, the same column as the Academy pages) from the
title to the closing action, and **every block fills that column** — no chapter is
capped narrower than the column it sits in, so none leaves a dead strip on its right.
The hero is the title, the 2–3 sentence lede, **one** primary action and the drawing
(scaled to the column, never widening it), closed by the course's facts as a
`.spec-strip` — format, syllabus size, weekly time, the docs link — the way an Academy
program closes its title block. The hero carries no secondary text links.

Every chapter below is the same frame — a dashed seam above, a real `.band-head`,
then the site's own components. The chapter order is fixed: about the course (the
overview at the column width, the outcome numbers as `.stat-tiles-row` under it),
join (only when a cohort is open: the cohort on a `.featured` slab beside the
start-on-your-own card), the journey (the homepage's own plain stage cards in a
`.card-grid-3`, no connecting rail and no eyebrows), the syllabus, the certificate,
who it is for, cohort editions (one `.list-row` per edition: its title, and a status
pill only on the current one — the edition's own page carries its dates), stories, what people built, the FAQ, and the
closing mint panel. A chapter the data cannot fill is not drawn.

**One goal, one action.** The hero and the closing panel draw the same button from
one include (`courses/_family_primary_action.html`) and its label says where it goes:
register for the open cohort, apply when approval-gated, start the self-paced
edition, *Start module 1* on the platform, or open the course on GitHub. The landing
ends on its own closing panel, so it hides the site-wide Academy and tour stripes
(`hide_academy_cta`, `hide_tour_cta`); a cohort page ends on the Academy stripe
alone. The full landing guidance is `_docs/design/landing-pages.md`.

The syllabus is one list drawn by one row component on one card, rows at the full
column width: a mono index, the title, the one line on what the module builds. A
project sits at its chronological slot with a mono `Project` marker instead of a
number; the attempts of one project (`Project Attempt 1`, `Last Project Attempt`,
`Capstone Project (Attempt 2)`) are one row listing each attempt's own deadlines
(`courses.course_page_content.merge_syllabus_rows_with_projects`). No rule divides the
index from the row.

**A shared module page** (`courses/shared_module.html`) is an ordinary detail page at
the content width: breadcrumb, `Module N of M`, the title and the module's summary;
then its lessons as one numbered list (no side rail repeating it), each cohort's
homework for the module as `.list-row`s naming the cohort
(`courses/_module_homework.html`), and the way on to the next module. There is no
"choose your delivery" step; the delivery notice is drawn only for an archived
delivery.

The registration page keeps the ordinary content width. Its campaign copy is one
editor-owned Markdown field, presented as sections by
`courses.views.registration_marketing`: each heading opens a section, a `Label: value`
list is a schedule of dashed definition rows, a bold-led list is titled rows, and the
rest stays prose. A leading heading that repeats the campaign title is not drawn, and
with a hero banner (which already sets the title) the `h1` is visually hidden.

## Building a new page — checklist

0. Define the page's single primary goal and the roles it plays in
   `_docs/design/page-goals.md` before writing markup: one primary CTA
   (`.cta-primary`) per page serving that goal; everything else secondary or
   moved elsewhere.
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
5. Colours only via tokens; mono only via `--font-mono`; sizes from the type scale;
   every margin, padding and gap from the spacing roles or scale (see Spacing).
6. Design the narrow layout deliberately (reshape, not reflow) in the page's own
   `@media (max-width: 47.99rem)` block.
7. Keep the template readable: one opening tag per line, structural template tags
   alone on their line.
8. Verify: both themes, 1440×900, 705×900 (the tablet tier) and 390×844 screenshots, a keyboard pass (skip link,
   focus visible, menus operable), and the page-owning tests plus
   `uv run --frozen python scripts/ci.py lint`, `format-check`, and `typecheck`.
