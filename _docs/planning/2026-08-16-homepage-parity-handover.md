# Homepage design-parity handover — 2026-08-16

State of the design 5a homepage (`templates/core/home.html`, issue #179) after the
2026-08-16 session in the `website-179-design` worktree. Everything listed under
"Requested, not yet implemented" was asked for during that session and is still open.
Work directly on `main`; no branch/PR ceremony is wanted for these.

## Done and committed

- Masthead logo traced 1:1 from the mockup (ink-outlined lavender bubble, `DTC`).
- Quicksand self-hosted (`core/static/core/fonts/quicksand-latin-var.woff2`) and used
  everywhere; the mono font and its eight usages are gone.
- Featured course card rebuilt as the mockup draws it: `New cohort` chip, title,
  starts/format subline, description, homework/projects meta, `View the syllabus` CTA,
  mint "What you'll build" panel. The CTA links to the featured cohort page
  (`core/views.py::home()` reverses `course-cohort-<cohort slug>` with a family-page
  fallback).
- Catalog scroller cards restyled (chip, title, green cohort line, promise,
  `View course →`) and the scrollbar hidden while the strip keeps scrolling, arrow
  buttons, and keyboard focus.
- Hero and climb artwork replaced with the supplied images: `~/tmp/hero.png` for the
  hero, issue #182's step 2 and step 3 for the middle and last climb cards
  (`home-hero.webp`, `home-step-2.webp`, `home-step-3.webp`). The first climb card keeps
  the mockup's smaller stuck-scene export (`home-stuck.webp`) so the hero and step 1
  read as different pictures.
- `core/tests/test_homepage.py` copy assertions follow the rebuilt cards.

## Requested, not yet implemented

Copy removals:

1. Remove the hero caption `everybody starts here — nobody stays here`
   (`figcaption.hero-caption`).
2. Remove the climb band note `…and then you're the one shipping it` (`p.band-note`).
3. Remove the hero fineprint `Takes 20 seconds. No card, no upsell. Courses, weekly
   events, homework and the Slack come with the account.` (`p.hero-fineprint`).

Design fixes:

4. Buttons differ between light and dark mode (visible in the mockup too). Take the
   light variant as the reference and adjust the dark styles to match.
5. The light-mode green does not match the mockup palette. Align the palette with the
   mockup first, then adjust the dark-mode green to it.
6. The checkboxes in the featured card's "What you'll build" list render broken; fix
   them (they are CSS-drawn squares with a rotated-border tick).
7. The catalog scroller's arrow buttons must match the mockup's styling.
8. The wiki graph edges are straight lines; make the graph more interesting with
   curved/organic edges (a subagent was requested for this rework).

Section parity work still open against the mockup:

9. "Something to attend this week" section.
10. Podcast and article cards; the article card needs an icon (extract it from the
    mockup rather than redrawing).
11. The three how-the-courses-work strip icons (Real homework / Peer review / Weekly
    live events) — take them from the mockup.
12. Footer.
13. Icon size/colour sweep and a full-page side-by-side diff against the mockup at
    1440 and 390, light and dark.

## Conventions established in the session

- One commit per discrete parity change; commit regularly.
- Prefer extracting/measuring real assets from the mockup exports over hand-approximating
  them (`/tmp/dtc-mockups/export/homepage-5a-desktop.png`, 2284×7864; render at
  device scale factor 1.586 for a 1440 viewport).
- The dev server must be restarted after every template edit (cached template loader):
  `cd .tmp/website-179-design && DJANGO_SETTINGS_MODULE=website.settings.local
  uv run --no-sync python manage.py runserver 127.0.0.1:8902 --noreload`.
- Playwright tests need the Makefile env
  (`DTC_TEST_RUN_ID=… DJANGO_SETTINGS_MODULE=website.settings.test
  DJANGO_ALLOW_ASYNC_UNSAFE=true`); do not edit the tree while they run.
- Single-line `{# #}` comments only — multi-line ones leak as visible text and a test
  guards against it.
