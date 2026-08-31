# Content-update CI/CD lane

This runbook defines the website-owned contract for checking the four source-backed content
families that have checked-in projections: courses, Podwiki, FAQ, and docs. It is deliberately a
projection check. It does not fetch a source repository, rebuild a source site, activate content,
or write to another repository.

## Ownership boundary

GitHub remains authoritative for editorial and curriculum source. The source repositories keep
their own authoring, build, Pages, search, and deployment workflows. The website lane checks the
immutable projection and adapter code reviewed into this repository:

| Family | Source of truth | Website-owned check |
| --- | --- | --- |
| Courses | `DataTalksClub/course-management-platform` | The pinned public course projection plus the course-repository parser, webhook registration, sync, and import tests. |
| Podwiki | `DataTalksClub/podwiki` | The pinned wiki, graph, and search projections plus the public wiki and graph-link tests. |
| FAQ | `DataTalksClub/faq` | The pinned FAQ projection and checked assets plus the FAQ route/feed tests. |
| Docs | `DataTalksClub/docs` | The pinned docs projection and checked assets plus the docs navigation/rendering tests. |

The source repositories' workflows are not callers of this website workflow and this repository
does not add a token, deploy role, AWS credential, source checkout, or cross-repository dispatch.
Their source changes become eligible here only when a reviewed website change updates the pinned
projection. The CMP adoption sync remains the explicit, reviewed process in
[`upstream-sync.md`](../adoption/course-platform/upstream-sync.md); its retired drift workflow is
not replaced by an automatic source mutation.

The `DataTalksClub/content` workflow and the structured article/podcast/book adapter are outside
this four-family lane. Podcast route, show-notes, event/Luma, graph, and course-worker behavior
remain owned by their existing changes and tests. No report from this lane contains learner or
registration data, account records, or provider data.

## Common contract

The authoritative implementation is the matrix workflow
[`content-update.yml`](../../.github/workflows/content-update.yml), the local composite action in
[`content-update/action.yml`](../../.github/actions/content-update/action.yml), and
[`ci.content_update`](../../ci/content_update.py). Every matrix entry uses the same contract:

- **Trigger:** pull requests targeting `main`, pushes to `main`, and manual dispatch, limited to
  the workflow, contract, adapter, projection, asset, and focused-test paths for this lane.
- **Permissions:** `contents: read` at workflow and job scope. Checkout uses one shallow reviewed
  tree with `persist-credentials: false`; no deployment credentials or write authority is
  available.
- **Concurrency:** `website-content-update-${{ github.ref }}` with cancellation disabled, so a
  newer projection check cannot erase an earlier result.
- **Security:** the runner uses the pinned uv version and `uv sync --locked`; the action runs with
  `set -euo pipefail`, reads only repository-local paths, rejects symlinks/traversal/non-regular
  files, and applies bounded file and byte limits. It never evaluates source code from a source
  checkout.
- **Checksum:** each declared projection/asset file is recorded by byte size and SHA-256, along
  with a deterministic length-prefixed aggregate SHA-256. Existing loaders additionally verify
  source revisions, body/asset checksums, the public-projection manifest/tree, and graph
  references.
- **Validation:** `ci.content_update` is the common fail-closed gate. The matrix adds only the
  source-specific adapter/surface tests listed in the table below.
- **Redaction:** reports contain only family/source identity, safe aggregate counts, file metadata,
  checks, and bounded diagnostic codes. They do not contain projected text, answers, bodies,
  source URLs, edit links, credentials, or database values. The step summary is generated from
  the validated report rather than by printing a projection or test artifact.
- **Reporting:** each entry writes the deterministic report to
  `.tmp/content-update/<family>/report.json`, appends a metadata-only GitHub Step Summary, and
  uploads that report as a 30-day artifact. `.tmp/` is gitignored and reports must not be copied
  into issues, screenshots, or external systems.

The source-specific additions are intentionally narrow:

| Family | Additional command |
| --- | --- |
| Courses | `pytest` for the bounded course-repository parser, HMAC webhook/delivery fence, registration, immutable archive sync, curriculum import, and provenance contracts. |
| Podwiki | Django tests for the wiki surfaces and podcast episode graph links that consume the Podwiki graph. |
| FAQ | Django tests for FAQ routes, stable anchors, and JSON feeds. |
| Docs | Django tests for docs projection, navigation, and rendering. |

## Local use

Run all four checks from the repository root:

```bash
make content-update-check
```

Run one family while iterating:

```bash
make content-update-check CONTENT_UPDATE_FAMILY=faq
```

The target accepts only the four family names (or its default `all`, which expands to all four)
and writes reports below `.tmp/content-update/`. A failed check still writes a safe report with a
diagnostic code when the contract can be initialized. Inspect source content in its source
repository or a protected local checkout, not in CI artifacts.

## Refresh and acceptance boundary

1. A source owner publishes the desired source commit using that repository's own reviewed
   workflow.
2. A website issue updates the exact source pin and projection through the owning adapter or
   reviewed compatibility/source-builder process. Do not use this lane to fetch a moving branch or
   copy a source checkout.
3. The website pull request runs all four common checks when a shared contract or projection path
   changes. The independent tester reruns the relevant focused tests and records the normal
   versioned verification evidence required by [`_docs/PROCESS.md`](../PROCESS.md).
4. Only the normal product acceptance and local merge lifecycle can make the projection change
   part of `main`; this lane never commits, pushes, activates, or deploys it.

If a source repository changes its own trigger, Pages deployment, search deployment, or authoring
automation, update that repository there. Update this runbook and the local contract only when the
website's projection boundary, adapter safety rules, or report schema changes.
