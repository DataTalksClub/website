# Design 5a — the shared design system

Design 5a is the visual language introduced by the homepage rebuild (issue #179)
and carried by the 6-series pages: the courses index (6a), the course page (6b),
the events index (6c) and the podcast index and episode (6d).

The five public wiki surfaces — the hub, search results, the graph, the special
pages and a wiki page — joined the system afterwards, then the public person
profile, and then the shared collection hub behind `/blog` and `/books`
(`templates/public/collection_hub.html`). They have **no mockup**: they are
composed from the inventory below, and where they needed something the system
did not have (a knowledge-graph node, long-form prose, a search row, an inline
mono literal) the primitive was added to the partial and documented here rather
than forked into a page. When there is no drawing, the rules in this document
are the whole brief — bands from the band table, primitives from the inventory,
and nothing on the page that the catalogue does not hold. "The wiki surfaces, as
built" and "The person profile, as built" record what each settled on.

The blog article page joined next, also without a mockup. It is the first page
whose whole purpose is reading, so it extended the wiki pages' `.prose`
primitive rather than forking one: a reading modifier that holds the body to a
measure, and the elements a richer body carries (deeper headings, real lists,
quotes, code, figures, captions, rules and tables). Every other body of imported
text — books next — renders through that same primitive.

The system lives in one place:

- **`templates/core/_design_system.html`** — the shared stylesheet partial.
  Tokens, reset, masthead, bands, cards and every shared primitive, including
  the extended primitives the 6-series pages need.
- **`templates/core/_site_shell_head.html`** and
  **`templates/core/_site_shell_foot.html`** — the shared shell *markup*: the
  skip link, dark-mode bootstrap, impersonation banner, masthead and
  announcement; and the footer, theme toggle and script set. Every page in the
  system includes both.
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
| `--graph-edge` | `#b9bfd8` | same | the drawn edges of a knowledge graph |

`--graph-edge` was the one hex the homepage still carried inline, as an SVG
`stroke` attribute on each graph line. It became a token when `/wiki/graph`
started drawing the same edges; the value is unchanged in both themes, because
the line has to read on a light lavender band and on a dark navy one and this
pale periwinkle does both.

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
| Prose h2 / h3 | `1.35rem` / `1.1rem` | headings inside `.prose` |
| Prose h4 / h5 / h6 | `1.05rem` / `0.9rem` uppercase | deeper source headings inside `.prose` |
| Reading body | `1.05rem / 1.75` | `.prose-reading` — a page that is read end to end |
| Prose lede | `clamp(1.05rem, 2vw, 1.2rem)` | `.prose-lede`, the standfirst above a body |
| Meta | `0.85–0.92rem` | sublines, notes, fineprint |
| Chip / kicker | `0.72rem` | uppercase, letter-spaced |
| Status pill / mono label | `0.66rem` | mono, uppercase, `letter-spacing: 0.09–0.1em` |

Headings are weight 800 with `letter-spacing: -0.015em` and `line-height 1.12`
(tighter on the biggest sizes). The homepage was verified to sit within ~10% of
the export's heading and body sizes; that tolerance is the accepted state.

The prose rows apply **only inside `.prose`**; a card, a row or a band heading
keeps the sizes above. `.prose-reading` is the system's one deliberate step
outside the mockups' scale, added with the article page: a page read end to end
wants a slightly larger body on a longer leading. A body's own headings stay
below the band `h2` so a section of prose never competes with the heading of
the band it sits in.

## Spacing, radius, borders, shadows

- **Spacing scale** (gaps and insets): `0.35 / 0.5 / 0.6 / 0.75 / 0.85 / 1 /
  1.25 / 1.5 / 2 / 2.5rem`. Card gap is `0.6rem` inside, `1.25rem` between
  cards (`0.9rem` between narrow rows).
- **Band padding**: `padding-block: 2.25rem`, growing to `2.9rem` at ≥62rem.
- **Shell**: `.shell` centres content at `max-width: var(--shell)` = `76rem`
  with `padding-inline: 1rem` (`1.25rem` at ≥40rem).
- **Measure**: `--measure` = `38rem` is the reading measure — about 70
  characters of prose body text. `.prose-reading` and `.prose-lede` are held to
  it; a page that wants its whole column at the measure sets its shell to
  `calc(var(--measure) + 2.5rem)` so the shell's own padding still fits (the
  article page does exactly that).
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
inside. Four grounds exist:

| Class | Background | When |
| --- | --- | --- |
| `band-cream` | `--cream` | the hero — the first band on a page, and only that |
| `band-lavender` | `--lavender` | the content ground — every band after the hero |
| `band-mint` | `--mint` | an events *section* inside a page about several things (homepage only) |
| `band-ink` | `--ink` (`#0d0f1c` dark) | the closing call-to-action band only |

Every band ends with the dashed separator (`border-bottom: 2px dashed
var(--line-soft)`); `band-ink` has none because it closes the page. The
masthead carries the same dashed rule. Do not invent new band colours.

### Which ground a band takes

This was rediscovered by hand three times — once per page that joined the
system — so it is written down here rather than derived again.

**The hero keeps cream. Everything after it is lavender.**

- The **first band on a page is the hero**: masthead-adjacent, breadcrumb,
  `h1`, byline. It takes `band-cream`, the warm paper. The masthead is the one
  strip every page shares, and recolouring the ground directly under it makes a
  page look like it belongs to a different site.
- **Every band after the hero takes `band-lavender`.** Lavender is the content
  ground. A reader moving from `/events` to `/blog` to a person profile should
  meet the same ground under the same kind of content; a page that alternates on
  its own reads as a different site with the same masthead.
- A page whose hero and body are one band (the legal `text_page.html`) keeps
  cream: it has no band after the hero. If it ever grows a second band, that one
  is lavender.

Enforced by `content/tests/test_band_grounds.py`, which reads the band sequence
out of every public page template.

**When mint is still correct.** `band-mint` marks an events *section* inside a
page that is about several things — it says "this part is events" only where the
rest of the page is not. On a page that is *itself* about an event (the event
page's speakers band) or about one kind of work among four (a person profile's
groups) the mint band said nothing the heading did not already say, and it broke
the content ground; both now take lavender. Mint remains correct on the homepage
events band, and `--mint` remains correct as a *panel* (`.panel-mint`) and as a
*pill* (`.status-pill-mint`) on any ground.

**When ink is still correct.** Unchanged: `band-ink` is the closing call to
action, last on the page, and it draws no dashed rule because nothing follows
it.

**Lavender after lavender.** Consecutive bands of the same ground are expected
now, and they are separated the way the system always separated consecutive
cream bands: the `.band` dashed bottom rule, then `2.25rem` (`2.9rem` at
≥62rem) of padding, then the next `.band-head` heading. The dashed rule is
deliberately quiet and reads the same on either ground — `--line-soft` against
its band is 1.25:1 on light lavender against 1.35:1 on light cream, and 2.02:1
against 2.10:1 in dark. What actually separates two sections is the heading and
the space, not the colour; give every band after the hero a real `.band-head`.

**The homepage is the exception.** `templates/core/home.html` is composed
directly from `datatalks-homepage.source.html` and alternates cream, lavender,
mint and ink deliberately — it is a magazine cover, not a content page, and the
alternation is the drawing. Do not flatten it:
`test_the_homepage_keeps_its_own_alternation` holds the cream hero, the closing
ink band and the fact that it uses more than two grounds, so a band can still be
added to the homepage but the alternation cannot be quietly removed.

The 6-series mockup (`datatalks-pages.source.html`) is *not* an exception: it
draws each of 6a–6d on one cream page ground with lavender used for tiles,
panels and a single dashed-bounded strip. It never alternates full-width bands,
so the courses index and the course page follow the rule like every other page.

Outside the rule for now: the course platform's task surfaces (dashboard,
homework, submissions, statistics, the leaderboard family, enrolment,
registration, wrapped) and Studio. They arrived through the course-platform
adoption rather than the editorial system, several of them use alternation to
pace a long working list, and bringing them across is its own issue.

**Contrast when a band changes ground.** `--cream`, `--lavender` and `--mint`
all flip with the theme, so a band that reads in light must be checked in dark.
Muted text clears AA on every ground in both themes (`--muted` on light
lavender 5.50:1, on light mint 5.58:1; dark lavender 5.90:1, dark mint 6.07:1),
but that is only true since issue #185 corrected the dark token — check any
muted text you move onto a new ground rather than assuming.

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
- **`.breadcrumbs`** — the breadcrumb trail on a detail page, restoring what
  the adopted shell used to draw. The mockups draw no breadcrumb, so the shape
  is this system's own: links take the `.band-link` treatment (indigo,
  underlined) at the `0.92rem` meta size, the current page is `--muted`, and
  the `/` separator is CSS-drawn with empty alternative text
  (`content: "/" / ""`) so it is never announced. Links keep the system's
  `2.75rem` minimum target height in both themes, which also satisfies the
  target-spacing contract when the trail wraps on a phone. Markup contract: a
  `nav` with an accessible name holding an `ol`, one `li` per level, every
  ancestor a link, and the current page on `li[aria-current="page"]` as plain
  text — never a link.

  ```html
  <nav class="shell breadcrumbs" aria-label="Breadcrumb">
    <ol>
      <li>
        <a href="…">Courses</a>
      </li>
      <li aria-current="page">
        Machine Learning Zoomcamp
      </li>
    </ol>
  </nav>
  ```

  Use it on pages that sit below an index: the course page carries
  `Courses / <course title>` at the top of its hero band, the blog article page
  carries `Blog / <article title>`, and the four wiki surfaces below `/wiki`
  carry `Wiki / Search`, `Wiki / Knowledge graph`,
  `Wiki / Special pages[ / <category>]` and `Wiki / <topic>`. Index pages
  (`/courses`, `/events`, `/podcast`, `/wiki`) and the homepage do not carry
  one — the masthead already marks them with `aria-current="page"` and a lone
  "Home" crumb duplicates the brand link. The podcast episode page keeps its
  `← all episodes` back-link instead; a page offers one way back, not two. The
  person profile carries neither: the site deliberately serves no people index
  (`/people` is a 404), so a crumb or a back-link there would point nowhere.

### Cards and chips

- **`.card`** — white slab, 2px ink border, `0.9rem` radius, hard soft shadow,
  `flex-direction: column` with `gap: 0.6rem`. `margin-top: auto` on a child
  pins it to the card foot (see `.course-link`, `.story-person`).
- **`.card-grid`** + **`.card-grid-2` / `.card-grid-3`** — the band's card
  columns; they collapse to one column on narrow screens.
- **`.event-card`** — the card for one event, drawn by the events index and by
  the homepage's three-up band. The whole card is the link: the title is the one
  real anchor and its `::after` covers the card, so the accessibility tree holds
  exactly one link per event and focus still lands on it. Anything else
  interactive inside — a speaker's chip link, the kind pill that takes focus for
  its tip — sits above that overlay with `position: relative; z-index: 1`. It
  carries `.event-summary` (clamped to three lines). Both pages held a copy of
  these rules until they were folded into the shared layer; a page adds only its
  own layout around the card.
- **`.chip`** — round uppercase mini-pill (`1.5px` soft border). Variants:
  `.chip-plain` (no uppercase), `.chip-green` (green outline text),
  `.chip-live` (green fill, white text), `.chip-ink` (ink fill, cream text).
- **`.avatar`** — striped round placeholder disc, `2.6rem`.
- **`.person-chip`** — one credited person: a small portrait and their name,
  linked to their profile. Every surface that names a person draws this, so a
  reader meets the same person the same way wherever they find them.

  ```html
  {% include "public/_person_chip.html" with person=speaker|person_chip %}
  ```

  ```html
  <span class="person-chip">
    <img class="person-chip-portrait" src="…" alt="" width="96" height="96" loading="lazy">
    <a class="band-link person-chip-name" href="/people/…">Alexey Grigorev</a>
  </span>
  ```

  The chip is an **inline-flex atom**, so it wraps like a word: a row of five
  faces inside `.shell-reading` breaks onto the next line instead of widening
  the column. `.person-chips` is the wrapping row that holds several, and
  `.person-chips-label` sets the optional "By" / "With" that opens it.
  There is **one face size for the whole site** (`1.6rem`): a chip is a credit
  standing beside a title, not a portrait shot. The single exception is
  `.person-chip-lead` (`3rem` portrait, card-title name), for a block whose
  subject *is* that one person — the episode page's guest.

  On a card, the credit is the card's last line rather than something wedged
  between the title and the summary, so pair `.person-chips` with
  `.card-credit`; its `margin-top: auto` also pins the credit to the floor of a
  fixed-height card column (the homepage's three-up events).

  Three facts can each be missing and each degrades on its own: no portrait
  keeps `.avatar.avatar-striped` (the stand-in disc, `aria-hidden`), no profile
  keeps the name as plain `span.person-chip-name` in `--ink` rather than a link
  to nowhere, and the portrait always carries `alt=""` — it is decorative
  because the name beside it is already the credit, and a screen reader must
  not hear the name twice. The picture is given its intrinsic square so the
  line cannot shift as it arrives.

  The blog article byline is where this shape was first drawn (issue #179);
  it still carries its own `.article-author` copy of the rules while the FAQ
  work owns that template, and should adopt the partial next.

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
fully styled by the stylesheet partial, and their **markup is shared too**:

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

**The brand lockup.** The mark is one drawing: the bubble outline and the `DTC`
inside it are stroked paths in the same weight and round cap, because the
illustrations this masthead sits above are hand-lettered and a typeset `DTC`
was the one printed-looking thing in the mark. `DataTalks.Club` is a single
weight throughout — it is one name, not a light half and a bold half. The
wordmark carries a small bottom margin so it aligns with the drawn letters
rather than with the mark's box, whose bottom fifth is the bubble's tail; the
arithmetic is in the comment on `.brand-name`.

Do not copy that markup into a page. The first five pages in the system each
carried their own copy, and the copies drifted: `/slack` left the navigation on
all five and `user_menu.js` left the script set on all five, so the account
menu would not close on Escape or on an outside click (issue #179).
`core/tests/test_design_5a_shell.py` now renders every page in the system and
compares their navigation entries and script sets to each other.

The navigation is **nine entries** — Events, Courses, Blog, Podcast, Wiki,
Books, Docs, FAQ, Slack — the same row the rest of the site offers. The
6-series mockups draw eight and put Slack in their own footer; this site's
footer is shared with the pages still on the older shell, and a navigation row
that loses an entry as the reader moves between pages is the worse trade. The
current entry is set from `primary_navigation_current`
(`core/context_processors.site_context`), so a page never marks it by hand.

The navigation contract (toggle + `#site-navigation-links`, opened by
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
| `status-pill-live` | `--green`, white text | a running cohort, a live event |
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
- **`.mono-code`** — an inline literal inside running copy: a Slack channel
  name, a command, a file name (`#book-of-the-week` on the books hub). It sets
  the run in the mono face at `0.92em` of the surrounding size and carries no
  surface of its own, so it reads on every band. Put it on the `code` element
  the content already deserves; it is a typographic mark, not a state marker
  (that is `.status-pill`) and not a metadata line (that is `.mono-note`).

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

### Archive row (every dated index)

One row of a dated index: the date on a fixed rail, and the record beside it as
a card. It is a partial, not a pattern to copy —
`templates/public/_archive_row.html`, whose comment is the interface:

```html
{% include "public/_archive_row.html" with row_title=record.title row_url=record.public_path row_date=record.published row_summary=record.description row_credits=record.author_profiles only %}
```

Optional slots: `row_date` (absent drops the rail, and the row gives that column
back to the card), `row_mark="play"` for the podcast disc, `row_eyebrow` for one
mono line above the title, `row_pill`/`row_pill_variant`/`row_pill_extra`,
`row_summary`, `row_credits`, and `row_class`/`row_hook` for a surface's own
hooks. Always pass `only`. A Django tag cannot be broken across lines, so the
call is one long line.

Inside the card the order is fixed: leading mark, eyebrow, pills, `h3` title,
clamped summary, and the credited people last as `.person-chips.card-credit`
with no "By"/"With" label. The rail is `.date-rail`, day above year.

Drawn by `/blog`, `/books`, `/podcast` and every contribution band of a person's
profile. **Not** by the events index: that row's rail carries three facts (day,
weekday, start clock) rather than a publication date, and its card holds the
kind pill and a link stretched over the whole card. Its card is shared with the
homepage as `.event-card` instead.

### Folded rows (`.row-fold`)

A list long enough to be a wall keeps its first rows in view and puts the rest
behind one `<details>` that names what it holds:

```html
<details class="row-fold" id="person-events-more">
  <summary class="row-fold-summary">
    <span class="row-fold-open">Show 44 more events</span>
    <span class="row-fold-close">Show fewer events</span>
    <span class="row-fold-marker" aria-hidden="true"></span>
  </summary>
  …the remaining .list-rows…
</details>
```

It sits inside the `.row-list`, so the folded rows keep the list's dashed
division. `<details>` is doing the work, as in the FAQ folds: keyboard behaviour
and announced expanded state come free and it opens with JavaScript off. Name
the control with the count — a bare "Show more" makes the reader click to find
out how much they are in for.

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

### Module accordion (unused)

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
`[open]` state, so it needs no script. `.module-title` is set at the card-title
weight, not body weight.

**Not used by the course page.** Issue #179 built the course page's assignments
as this accordion; the page it replaced showed a Homework table and a Projects
table, and folding both into one disclosure list put every deadline and every
state behind a click on the page whose whole job is to show them. The course
page now draws those two tables from the list rows above (`.row-list` with
`--row-cols: minmax(0, 1fr) 11.5rem auto`, a caption row at the top of each and
the assignment's `.status-pill` in the last column). The accordion primitive
stays in the partial for a surface that genuinely has per-module content to
disclose; no page uses it today.

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

Mockup 6b draws this form on the course page itself, but the site already owns
one registration form, on the registration campaign page (`registration_campaign`,
`courses/templates/courses/register.html`), with its own POST target, duplicate
detection, country and role validation and accessible error summary. The rebuilt
course page therefore keeps its register **call to action** and links to that
page rather than growing a second copy of a mutation surface; these field
primitives are still the ones a page in this system uses when it does own a form.

### Panels

`.panel` + `.panel-mint` / `.panel-lavender` (+ `.panel-outlined` for the 2px
ink border) — the tinted inset blocks: "what you'll build", "next assignment",
"questions before you start?".

### Knowledge graph (`/wiki/graph` and the homepage wiki band)

Both surfaces draw the wiki the same way: a hub, a ring of its real neighbours,
and the edges between them, as **one SVG per width** with a `viewBox`, so the
drawing scales as a unit, a label cannot escape the node it names, and nothing
overflows a phone. `/wiki/graph` then names the rest of the graph as clouds of
pills. Everything here is in the partial; neither page forks it.

```html
<div class="graph-frame">
  <svg class="graph-svg graph-svg-wide" viewBox="0 0 640 400" role="group" aria-label="MLOps and 8 of its 135 linked wiki topics">
    <g aria-hidden="true">
      <line class="graph-svg-edge" x1="320.0" y1="200.0" x2="115.0" y2="76.0"/>
    </g>
    <a class="graph-svg-node" href="…">
      <rect class="graph-svg-shape" x="36.5" y="60.0" width="157.0" height="32.0" rx="16.0"/>
      <text text-anchor="middle" font-size="14.0"><tspan x="115.0" y="80.9">MLOps Architecture</tspan></text>
    </a>
    <a class="graph-svg-node graph-svg-hub" href="…">…</a>
  </svg>
  <svg class="graph-svg graph-svg-narrow" viewBox="0 0 320 408" …>…</svg>
  <p class="graph-legend">…</p>
</div>
```

- **`.graph-svg`** with **`.graph-svg-wide`** / **`.graph-svg-narrow`** — the
  two arrangements of the same drawing. Both are in the markup and one is
  `display: none` at a time, which also keeps the hidden one out of the tab
  order. Each is capped near its design size (44rem / 24rem) so labels stay
  label-sized instead of scaling with the column.
- **`.graph-svg-node`** (`.graph-svg-shape` + `text`) — one node, drawn as an
  SVG link so it is keyboard reachable; `.graph-svg-hub` is the green centre.
  The focus ring is a thickened `--focus` stroke for engines that draw no
  outline on an SVG link.
- **`.graph-svg-edge`** — a drawn relation, `--graph-edge`, styled from the
  partial and never from an attribute in the page.
- **`.graph-node`** — one node *outside* the drawing: a lavender pill with an
  indigo edge, carrying no position of its own.
- **`.graph-cloud`** — a wrapping row of those pills, for a set too large to
  draw. `/wiki/graph` uses one for the busiest nodes of a type and one for the
  rest of that type.
- **`.graph-index`** / **`.graph-index-summary`** — the `<details>` that folds
  the complete list of a node type away behind its ways in.
- **`.graph-legend`** with `.legend-key` / `.legend-swatch` / `.legend-hub` /
  `.legend-page` / `.legend-swatch-line` — the key and the count under a frame.
- **`.topic-list`** / **`.topic`** — the bordered topic cards the homepage
  stacks beside its drawing.

The geometry (frames, ring positions, label wrapping, the font's own advance
widths) is a layout constant in `core/graph_layout.py`, shared by both pages;
which node sits where is read from the graph's own edges, and a hub or a spoke
the data cannot supply raises rather than rendering an invented edge. A label
too long for its node wraps to as many lines as it needs — the node grows
taller, never wider than its frame's bound — because the hub `/wiki/graph`
draws is whatever the data makes busiest. `core/tests/test_graph_layout.py`
holds the result: no drawn node overlaps another or leaves its frame.

### Long-form prose (`.prose`)

A page whose text arrives as typed blocks renders them inside `.prose`:

```html
<div class="prose">
  <h2 id="purpose-of-aa-tests">Purpose of A/A Tests</h2>
  <p class="prose-paragraph">…</p>
  <p class="prose-item">…</p>
</div>
```

Rhythm comes from `.prose > * + *`; `.prose-paragraph` keeps the source's own
line breaks (`white-space: pre-line`), so its opening tag and its value stay on
one line in the template. A list item arrives as its own block, so
`.prose-item` draws a marker (`display: list-item`) instead of the page
inventing a `<ul>` the source never marked. Headings keep the identifier the
source gives them, which is what every in-page anchor lands on.

#### Reading pages, and the rest of a body's elements

The blog article page extended the same primitive rather than forking one
(issue #179). Two additions:

- **`.prose-reading`** — the modifier a page adds when its body *is* the page:
  the reading measure (`max-width: var(--measure)`, `--measure` = `38rem`,
  about 70 characters) at `1.05rem/1.75`, with `overflow-wrap` so a long URL
  breaks instead of widening the column. Without it `.prose` keeps the body
  size of the page around it, exactly as the wiki pages have it. Pair it with
  **`.prose-lede`** for the standfirst above the body: one size up, same
  measure.
- **the elements a richer body can carry** — `h4`–`h6` continuing the heading
  ladder (below `h4` the size stops shrinking and the label goes uppercase, so
  a deep source heading still reads as one); real `ul`/`ol`/`li` drawing the
  same marker, indent and rhythm as `.prose-item`, for the case where the blocks
  really are a list (a page may group *consecutive* source list items into one
  real list, which also tells a screen reader how many items there are);
  `--ink` strong; a `--bubble`-ruled italic `blockquote`; inline `code` and a
  `pre` block that **wraps rather than scrolling** (the FAQ pages settled that
  first: a box that scrolls sideways with no focus stop of its own cannot be
  reached from a keyboard, and a command or a traceback stays readable when it
  wraps); `img` bounded to the column with the system's border and radius —
  give it the source file's own `width`/`height` so the box is reserved before
  the picture arrives; `figcaption` (also available as `.prose-caption`) in
  `--muted` at the meta size; a dashed `hr`; and table cells on dashed rules.
- **`.prose-scroll`** — the frame a wide table sits in. It is a **named region
  with its own focus stop** (`role="region"` + `tabindex="0"` + an
  `aria-label`, the same shape the Studio tables use), because a scrollable box
  no keyboard can reach is a barrier; two frames in one document may not share
  a name.
- **`.prose-embed`** — one already-sanitized fragment from a source that wrote
  its own block markup. `.prose > * + *` only reaches the fragment's outer
  element, so this keeps the rhythm inside it.
- **`.prose-unavailable`** — a dashed plate that names an element this site
  cannot draw (a survey chart that needs a script), the way a missing article
  cover says so where the picture would have been.

Headings inside `.prose` also get `scroll-margin-top`, so an anchored heading
does not land against the top of the viewport.

Turning a *projected* body (`kind`/`text` blocks) into this markup is
`content.article_content.prose_sections` plus
`templates/public/_prose_blocks.html`: headings keep their fragment id, a run
of list items of the same kind becomes one real ordered or unordered list, an
illustration, table, code sample, quotation, rule or unavailable chart is drawn
from its own fields, and any other kind keeps its text as a paragraph, so a
block kind the projection grows later is rendered rather than dropped. A block
that carries a source segment as well as its plain text is rendered through
Markdown and the shared sanitizer (`content.services.sanitize_rendered_html`),
which is how an article's links keep their addresses; no page writes
unsanitized external HTML.

### Search row

```html
<form class="search-row" action="…" method="get">
  <label class="sr-only" for="wiki-query">Search the Wiki</label>
  <input id="wiki-query" class="field-input" name="q" type="search" required>
  <button class="cta cta-compact cta-primary" type="submit">Search</button>
</form>
```

The field grows and the button keeps its size; both wrap on a narrow screen.

### Date rail (events index)

```html
<div class="when">
  <strong>Aug 17, 2026</strong>
  <span class="when-day">Monday</span>
  <span class="when-time">14:00 CEST</span>
</div>
```

A `--bubble` left rule with the date stacked beside each event card; the first
cell of a `.list-row` (`--row-cols: 6.5rem minmax(0,1fr)`). It carries three
facts, including the start clock, which is why the events index keeps it instead
of the archive row's two-line `.date-rail`. Only the events index draws it: a
dated index that publishes a day uses the archive row.

## The wiki surfaces, as built

There is no mockup for the wiki, so this is what the five pages settled on.
Every fact on them is read from the wiki data; `content/wiki_content.py` is
where that data becomes the shapes below, and it raises rather than render a
count, a group or an edge the data does not carry.

- **`/wiki`** — hero (topic count as the mono kicker, the title the page has
  always carried, a lede, the search row), then a lavender band holding the
  three ways in (graph, search, special pages) as `.row-list` rows, then the
  A–Z catalogue as the same rows: title, summary, and a `.cta-compact` read
  action. It is an index, so it carries no breadcrumb.
- **`/wiki?q=…`** — the same search row, then the count as the results band's
  own `h2` (`N results for “…”`), then one row per hit: a `.status-pill`
  naming what the hit is (the index holds whole pages and sections inside
  them), the title, and the section title underneath. No hits keeps the
  original empty state verbatim.
- **`/wiki/graph`** — one reading column, no two-up grid: the hero draws the
  wiki page with the most wiki neighbours inside the shared `.graph-frame` as
  one scalable SVG per width, with the two `.stat-tile` totals under it. Then
  each node type gets a band: heading, one-line description, a mono count, the
  dozen nodes the graph puts on the most connections as a `.graph-cloud` of
  `.graph-node`s, a link to the page that indexes that kind in full, and the
  rest of the type folded into a `.graph-index` `<details>`. The page is a way
  into the graph, not a third copy of the catalogue — `/wiki` holds the A–Z
  pages and `/wiki/special-pages` holds them filed by kind — but no node is
  dropped: each keeps the identifier the graph gives it, so a `#wiki:mlops`
  link into the page still lands, and a node the data has no destination for
  stays a named `span`, never a link.
- **`/wiki/special-pages[/<category>]`** — the categories are `.filter-pills`
  as links with `aria-current="page"` on the current one; the pages themselves
  are the hub's rows, so a reader meets one catalogue shape and not two.
- **`/wiki/<topic>`** — one 48rem reading column: the trail back, a `Wiki`
  mono kicker, the title, the summary, the page's relations as `.chip`s
  (linked ones indigo, each prefixed with its relation type for screen
  readers), and then the page itself in `.prose`.

## The person profile, as built

`templates/public/person_detail.html` has no mockup either, and it adds **no new
primitive and no new token**: every part of it is something another page already
draws.

- **Hero**: the portrait as a `2px`-bordered disc with the card surface and the
  standard hard shadow, the `.mono-label.mono-label-indigo` kicker, the `h1`, the
  community roles as `.chip`s in a labelled list, the profile summary as the
  lede, `.stat-tiles` counting what the person has done, and the external
  profile addresses as `.pill-button`s.
- **The profile's own bio** is typed blocks, so it renders in `.prose` with
  `.prose-paragraph` / `.prose-item`, exactly as a wiki page's body does —
  through the same partial, `public/_prose_body.html` (named for the primitive
  rather than for the wiki, now that two pages share it).
- **One band per kind of work**, in a fixed order — podcast episodes, events,
  articles, books — each a `.band-head` plus one `.row-list` on the content
  ground. A group appears only when the profile actually links to that kind of
  work. The four groups used to take four different grounds (cream, mint,
  lavender, cream), which made one profile look like four pages stitched
  together; the heading and the row's own marks name the kind of work, so the
  ground does not have to.
- **Every row is the shared archive row**, through
  `public/_contribution_row.html`, which maps one `Contribution` onto the slots
  above. The four groups each had a row shape of their own, and none of them was
  the shape the same episode or article had on the page it came from; now a
  piece of work looks the same wherever the reader meets it. An episode keeps
  the `.play-disc` and its `.status-pill-mint` season marker; an event keeps its
  type pill with the same open/mint/wait mapping, and — because a profile mixes
  past and future work in one list, unlike the events index, whose whole page is
  one or the other — a future event also carries the bare `.status-pill` reading
  `upcoming`, so that state is never colour-only. The role the profile states
  for a link is the row's mono eyebrow.
- **A long group folds.** The widest profile links to 63 pieces of work, 50 of
  them events, and 50 rows is a wall rather than a list. A group shows its first
  six rows and puts the rest behind one `.row-fold`, named with the count it is
  holding. Six is about one screen of rows under a band heading, and the fold
  appears only when it would hide at least three: hiding one or two rows behind
  a control is worse than showing them. Both numbers are
  `ROWS_BEFORE_FOLD`/`SMALLEST_FOLD` in `content/person_content.py`. The profile
  folds rather than paginating: it is one person's whole body of work on one
  page, and every row of it is in the document for a reader who searches it.

The composition (grouping, ordering, counts, markers) lives in
`content/person_content.py`, the same way the podcast pages keep theirs in
`content/podcast_content.py`, so the template holds no editorial logic.

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
line, and the wiki graph swaps its landscape SVG ring for a portrait one
(same data, long labels wrapped over more lines, geometry computed in
`core/graph_layout.py`). New pages should make
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
  cream ≈ 6.3:1). `--green-bright` is a **marker** colour — progress fill,
  status dot — not a text surface: white on it is only 3:1, so the live status
  pill takes `--green` (≈ 5.3:1 with white) exactly as `.cta-primary` and
  `.chip-live` do. State is never colour-only — pills and dots always carry
  words.
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
2. Include `core/_site_shell_head.html` as the first thing in the body and
   `core/_site_shell_foot.html` after `</main>`. They carry the skip link, the
   dark-mode bootstrap, the masthead, the footer, the theme toggle and the
   script set (`timezone_preference.js`, `user_menu.js`,
   `core/site_navigation.js`, `core/accessibility.js`,
   `core/analytics_preferences.js`). Do not copy any of it into the page, and
   do not set `aria-current` by hand — the shell reads it from
   `primary_navigation_current`. Add the page to `DESIGN_5A_TEMPLATES` in
   `core/tests/test_design_5a_shell.py`.
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
