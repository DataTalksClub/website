# 02 - URL, link, and SEO compatibility

Status: draft

URL and link preservation is a release-blocking requirement, not a best-effort migration task.

## Compatibility scope

The baseline inventory covers:

- all generated main-site pages and collection details;
- all docs pages and headings beneath `/docs/`;
- FAQ pages, ten-character question fragments, and JSON feeds beneath `/faq/`;
- Podwiki pages, search/filter URLs, generated graph assets, and hash deep links beneath `/podwiki/`;
- current course-platform HTML and API paths on `courses.datatalks.club`;
- assets beneath `/images/`, `/assets/`, `/docs/assets/`, `/faq/assets/`, and `/podwiki/assets/`;
- canonical, alternate, Open Graph, Twitter, structured-data, sitemap, robots, and edit-on-GitHub links;
- internal and external link destinations embedded in every rendered page.

## Route rules that must remain exact

- Main hubs retain paths such as `/articles.html`, `/podcast.html`, `/events.html`, `/books.html`, and `/people.html`.
- Main collection details retain `/blog/<slug>.html`, `/podcast/<slug>.html`, `/books/<slug>.html`, `/people/<slug>.html`, `/courses/<slug>.html`, `/tools/<slug>.html`, and `/conferences/<slug>.html` where they currently exist.
- Docs retain pretty trailing-slash paths under `/docs/`.
- FAQ retains `/faq/<course>.html#<question-id>` and the exact JSON field/path contracts at `/faq/json/`.
- Podwiki retains pretty paths under `/podwiki/`, the `q` and type-filter search contract, graph JSON, and graph hash semantics.
- Course-platform compatibility routes remain available on `courses.datatalks.club` until every known browser, script, certificate tool, and email template has migrated.
- Path case, percent encoding, Unicode, query strings used by public behavior, and trailing-slash behavior are tested rather than normalized globally.

## Canonical course URLs

The new canonical course structure is:

- `/courses/<course-slug>/` for the reusable course landing page;
- `/courses/<course-slug>/cohorts/<cohort-slug>/` for one dated delivery;
- cohort-relative paths for dashboard, calendar, homework, projects, peer review, leaderboard, and certificates.

Existing SEO-bearing static course articles under `/blog/*.html` stay at their current paths during the migration and link to the new course/cohort application. They are not folded into new paths during cutover.

Existing `courses.datatalks.club` routes initially reach copied compatibility views in the unified Django app. After all links and clients are migrated, that hostname moves to a small Terraform-managed redirect Lambda. HTML paths receive explicit one-hop redirects only after their destination is verified. Existing API routes remain functioning compatibility endpoints until each consumer is updated because API clients may not safely preserve authorization across a cross-host redirect.

The redirect Lambda uses a generated explicit path map, preserves query strings, emits `301` for GET/HEAD HTML, and uses `308` for non-GET only after client tests prove method/body/auth behavior. Unknown paths are logged without PII and return a real `404`, not a homepage redirect.

## URL and link manifest

Before building replacement views, a deterministic crawler writes a versioned manifest containing, for every current URL:

- source repository and source path;
- public path and expected status;
- redirect target when present;
- canonical URL;
- title, meta description, first heading, language, and robots directives;
- structured-data types and identifiers;
- all fragment IDs;
- all internal links, external links, form actions, and asset URLs;
- normalized main-content fingerprint;
- last-modified/sitemap state where present.

The manifest is built from both committed generated sites and a production crawl. Differences are reviewed because a generated tree can be stale while production can contain redirects or edge behavior absent from source.

Every manifest row is classified as:

- `preserve`: same path and equivalent response;
- `redirect`: one approved permanent redirect to an equivalent canonical destination;
- `retire`: an intentional `410 Gone`, requiring owner approval and evidence that no replacement exists.

No catch-all redirect to the homepage is allowed.

## Link preservation checks

For each rendered Django page, the compatibility test resolves:

- relative links against the current page;
- root-relative links against `datatalks.club`;
- FAQ fragments against IDs in the target page;
- docs and Markdown heading fragments using the existing slug algorithm;
- Podwiki chips/citations against wiki, podcast, book, and person targets;
- people references by stable `short` identifier;
- assets against their stable public path;
- external destinations without rewriting their query parameters.

Broken references fail candidate content activation. Explicitly optional or historical external links may warn instead of fail, but the warning is visible in Studio.

## SEO parity gate

Cutover is blocked unless:

- 100% of inventoried URLs resolve with the approved status and destination;
- there are no unexplained `404`, soft-404, redirect loop, or redirect-chain results;
- production canonicals are exact and self-consistent across main, docs, FAQ, Podwiki, and courses;
- page titles, descriptions, primary headings, meaningful body content, and image metadata have no unexplained loss;
- existing Event, BlogPosting, PodcastEpisode, FAQ, breadcrumb, Organization, WebSite/SearchAction, and other JSON-LD contracts validate where currently present;
- sitemap output contains the correct canonical paths and source-derived `lastmod` values;
- robots behavior does not accidentally block production content;
- internal link and asset crawls have no regression from the accepted baseline;
- response performance and rendered HTML remain crawlable without JavaScript.

SEO enhancements, such as filling current FAQ metadata gaps, ship only after parity is measured. They must not obscure migration regressions.

## Development and preview behavior

Every response from `web.dtcdev.click`, content previews, and unpublished course/cohort pages must:

- include `X-Robots-Tag: noindex, nofollow`;
- be disallowed in the development `robots.txt`;
- omit the production sitemap or expose a development-only empty sitemap;
- use the corresponding `https://datatalks.club/...` canonical for a production-equivalent public page;
- require authentication for private previews and never expose preview tokens to analytics or logs.

## Cutover monitoring

Monitor by path and referrer for:

- `404`, `410`, `5xx`, and redirect volume;
- crawler traffic and sitemap fetches;
- canonical mismatches and duplicate paths;
- top landing-page response time;
- Search Console coverage, crawl, indexing, and structured-data changes;
- changes in organic entrances and ranking pages, interpreted separately from seasonal traffic.

The legacy static build and URL manifest remain available throughout the rollback window.

## Acceptance criteria

- A committed test fixture inventories every current route and public contract.
- A Django crawl has zero unexplained compatibility differences.
- Every intentional change has one explicit redirect/retirement record, owner, reason, and test.
- A link checker validates targets and fragments across all site sections and course compatibility routes.
- Development and previews are demonstrably non-indexable.
- DNS cutover is not approved until the complete SEO parity report passes.
