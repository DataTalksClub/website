# Academy

DataTalks.Club Academy (`/academy/`) is part of this website and is edited
here.  Its pages are ordinary editorial content: Markdown files with structured
frontmatter in the `DataTalksClub/content` repository, synced into the
database like articles and podcasts, and rendered by the `academy` app through
the site's own page shell and design system.  The Astro site
(`DataTalksClub/academy`) is retired as a source; nothing reads it at runtime
or in operations.

## Where the copy lives

In `DataTalksClub/content`:

| File | What it holds |
| --- | --- |
| `academy/shared.yaml` | copy several pages share: brand, facts strip, instructor, fit lists, syllabus labels, funding, admission steps, the program FAQ, the closing waitlist panel, footer links, the waitlist form's labels, errors, quick questions and survey address |
| `academy/index.md` | `/academy/` (layout `home`) |
| `academy/agentic-engineering.md`, `academy/ai-engineering.md` | the program pages (layout `program`): hero, outcomes, project, prerequisites, syllabus groups and modules, Course structured data |
| `academy/sessions.md` | `/academy/sessions/` (layout `sessions`): the session list with dates, Zoom and Meetup links, the registration form copy, and the calendar identity |
| `academy/sessions-materials.md` | `/academy/sessions/materials/` (layout `materials`), the QR recording form |
| `academy/confirm.md` | `/academy/confirm/` (layout `confirm`), every confirmation state's words |
| `academy/bildungsgutschein.md` | the German guide (layout `guide`, `lang: de`) |
| `academy/faq.md` | `/academy/faq/` (layout `faq`) |
| `academy/privacy.md`, `academy/impressum.md` | layout `legal`; the body is Markdown |
| `images/academy/` | the instructor portrait, Open Graph cards and session QR codes |

Each page file declares its `path`, `title`, `description`, optional
`og_image`, `noindex`, `lang`/`hreflang` and its `sitemap` position.  The parser
(`content/sync_parsers/academy.py`) holds one schema per layout and refuses a
file with an unknown layout, a missing or unknown field, an unsafe link or an
image the repository does not have, so a page cannot publish half-drawn.
Fields that carry links (FAQ answers, form errors, the legal bodies) are
Markdown and render through the shared sanitizer.  The images publish through
the media parser at `/images/academy/...`.

To change Academy copy, edit those files in the content repository; the
automated content sync (`.github/workflows/content-sync.yml`, every ten minutes
or on dispatch; see `data-ingest.md` section 10.3) publishes the change.  For a local check against a working tree:

```sh
uv run python manage.py sync_content --source dtc-content --from-disk /path/to/content-checkout
```

An empty database, or a content repository without `academy/`, publishes no
Academy: every `/academy/` route returns 404.  There is no file, build or
upstream fallback.

## Routes

`/academy/` and the program, sessions, Bildungsgutschein, FAQ, privacy and
Impressum pages are the sitemap pages (`/academy/sitemap.xml`, mirrored at
`/sitemaps/academy.xml` and listed in the main sitemap index).
`/academy/confirm/` and `/academy/sessions/materials/` are `noindex`.  The
calendars `/academy/sessions/<session-id>.ics` and
`/academy/sessions/<calendar_slug>.ics` (all sessions) are generated from the
sessions page; their output matches the retired Astro generator byte for byte.
`/academy/layoff-proof/` permanently redirects to `/academy/sessions/` and keeps
its query string.  The images the Astro build served under `/academy/og/` and
`/academy/qr/` (the four Open Graph cards and the three session QR codes, PNG
and SVG) permanently redirect to the same names under `/images/academy/`;
social caches and printed slides reference them.  Only those files are mapped
(`academy.views.LEGACY_IMAGES`); any other path there is a 404.  Canonicals and
Open Graph URLs are on `https://datatalks.club/academy/...`.

## Forms

The waitlist, session and QR forms and the confirmation page talk directly to
the existing Academy signup backend (AWS Lambda, double opt-in) from
`academy/static/academy/academy.js`: `POST /signup` and
`GET /confirm?token=...&format=json`, with the same field keys, values,
consent version (`ACADEMY_CONSENT_VERSION`), campaign parameters, referrer,
page, browser language, honeypot, program/payment fields, quick-question
answers and session selections the Astro client sent.  The backend origin is
`ACADEMY_API_ORIGIN`.  Academy responses carry the site CSP plus exactly the
backend's `/signup` and `/confirm` endpoints in `connect-src`, and are
`private, no-store`.

Measurement is the site's shared consent-gated Umami pageview only.  The Astro
client's `/pv` beacon and Umami custom events are not sent.  The confirmation
and materials routes never load the tracker, so a token never reaches
analytics.  Browser tests mock the backend (`playwright_tests/test_academy.py`);
a production submission is a controlled live check with a
`dev+...@dtcdev.click` recipient only.  No registrant data lives in this
database.

## Rolling out the switch from Astro

The release that introduced these pages keeps the old `academy.PublishedResource`
model and table, unused: nothing reads it, and no migration drops it.  That
keeps the schema compatible with the previous image, so the old containers keep
serving during the rolling deploy and a rollback to that image still works.

The synced Academy rows only exist once **this release's** parsers have run
over the content repository.  The previous image's sync engine reads a commit
holding `academy/` harmlessly, but its parsers ignore those files, so it writes
no Academy rows.  A normal sync of a commit it already synced is skipped
(`Repository commit was already synchronized`), so the first sync on the new
code must be forced.  Between the deploy and that sync, `/academy/` is a 404 on
production; there is deliberately no fallback to the old rows.  Keep the window
short:

1. Commit the converted export to `DataTalksClub/content` (the webhook sync on
   the old code is harmless).
2. Deploy this release.  Wait until every container runs it: a content sync
   run by an old container would withdraw the `images/academy/` media rows,
   because the old media parser does not list that prefix.
3. Immediately run, on production,
   `manage.py sync_content --source dtc-content --force`.  `--force` is
   required whenever the content commit was already synced in step 1.
4. Check that every Academy URL returns 200: the eight sitemap pages,
   `/academy/confirm/`, `/academy/sessions/materials/`, the four calendars,
   `/academy/sitemap.xml`, `/sitemaps/academy.xml`, the `/academy/layoff-proof/`
   and image redirects, and `/images/academy/og/home.png`.
5. In a later, separate release, once a rollback past this one is no longer
   needed, drop `PublishedResource` and its table with a schema-only migration.

A rollback to the previous image serves the old `PublishedResource` rows again
(the table and rows are untouched).  Rolling forward afterwards needs step 3
again.

## The one-time migration from Astro

`scripts/convert_academy_source.py` converted the reviewed Astro source
(`DataTalksClub/academy` at `b9ccfd0d9d3baba5fd4bd70342b5e605333ce5bd`) into
the content files above.  It evaluates `src/data/*.ts` with Node, checks every
literal it takes from `.astro` markup against the source file, converts the
legal pages' markup to Markdown, copies the images, and verifies the
calendars against the reviewed build.  It writes outside this repository
(default `~/prod/academy-django/content-export/`); the export is committed to
the content repository by hand.  It is not a recurring step: after the
migration the content repository is the only source.

The one change to the source copy is the privacy notice
(`PRIVACY_CORRECTIONS`): it described the retired static site (GitHub Pages
hosting and the Astro page-view counter).  The export says the site runs on the
DataTalks.Club platform and that page views are measured only through the
site's consent-based analytics, and moves "Last updated" to 17 November 2026;
the rest is verbatim.  The forms still send consent version `2026-11-13.v1`.
The signup Lambda stores `consent_version` as a free string with no allow-list,
so a new value would be accepted; whether to bump it is the owner's decision.
