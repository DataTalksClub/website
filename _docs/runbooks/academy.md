# Academy publication

Academy is a separate Django app mounted at `/academy/`. Its public pages,
styles, fonts, scripts, images, calendars and sitemap are `PublishedResource`
rows. Requests never read an Astro checkout, build directory, S3 site, or
upstream proxy. An empty database returns 404. The source remains
`DataTalksClub/academy`; no Academy copy is maintained in website templates.

## Import a reviewed source revision

Keep the checkout and its build outside this repository, under `~/prod`.
Use a clean checkout at the reviewed full commit, `npm ci`, then build with:

```sh
SITE_URL=https://datatalks.club BASE_PATH=/academy/ \
PUBLIC_API_URL=https://augldvp3omxdxz3irqsmmxc25e0blpxx.lambda-url.eu-west-1.on.aws \
PUBLIC_UMAMI_SCRIPT_URL=https://umami.169.58.119.163.sslip.io/script.js \
PUBLIC_UMAMI_WEBSITE_ID=be4f7959-6ac3-4ce0-bec8-a316aad65d1f npm run build
```

Publish into the target database using the target's normal settings:

```sh
uv run python manage.py publish_academy /absolute/path/outside/repository/dist \
  --source-commit FULL_REVIEWED_ACADEMY_COMMIT
```

On a deployed image use its installed Python and production settings. Transfer
only the reviewed public build to an external directory, not a git-tracked
projection or a production export. The command validates the canonical sitemap
before any writes and updates resources in one transaction. It retains old
assets so open tabs survive publication. Reimporting the same build is safe;
rollback means publishing the previous reviewed build. No subscriber rows,
mail jobs, or registration backend configuration are read or changed.

The initial source revision is `253eeadd8a7472eb676019bce86bbf430f6aa925`.
The eight sitemap pages, `/confirm/`, `/sessions/materials/`, the four calendars
and the historical `/layoff-proof/` client redirect are all imported.
`/academy/sitemap.xml` is also mounted at `/sitemaps/academy.xml` and listed in
the main sitemap index. Canonicals remain on `datatalks.club/academy/`.

## Forms and live checks

The source's signup and confirmation clients use the existing Lambda backend
and its double opt-in flow, including consent version, program/payment fields,
UTMs, questionnaire and session selections. Browser requests require the
backend's CORS allowlist to admit the target origin. Academy's CSP admits that
backend's `/signup` and `/confirm` endpoints and the existing self-hosted Umami
service only. The source's optional `/pv` counter is blocked by CSP, preserving
the site's consent boundary without changing registration calls. The app replaces the
source auto-tracker with the main site's shared analytics consent controls and
allowlisted pageview payload. Confirmation pages never load the tracker; no
queries, fragments, referrers or custom event properties are sent. Confirmation responses
are private and never cached; the noindex meta comes from the source page.
Shared consent controls use Academy's palette and typography. A scoped mobile
grid rule also prevents long German program labels from overflowing their cards;
the imported source resources themselves remain unchanged.

Use mocked API responses for browser checks. A production submission is an
explicitly controlled live check: only a `dev+...@dtcdev.click` recipient, never
a real registrant. Publication itself sends no email. Do not alter DNS,
CloudFront, or the existing S3 deployment to publish this app.
