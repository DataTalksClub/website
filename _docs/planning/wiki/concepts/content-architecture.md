# GitHub-backed content architecture

- [HUMAN] Blog, podcast, docs, FAQ, and Podwiki remain authored in GitHub.
- [INFERENCE dtc-main-site,dtc-docs,dtc-faq,dtc-podwiki,aisl-reference] Django should serve a normalized database read model produced by atomic repository sync, never clone or call GitHub in a public request.
- [INFERENCE dtc-main-site,dtc-faq,dtc-podwiki] Source-specific adapters are necessary because the repositories have different Markdown dialects and public contracts.
- [INFERENCE dtc-main-site] `Person.short` is a stable cross-content key. Guest, speaker, host, author, and maintainer are relationships, not exclusive person types.
- [INFERENCE dtc-main-site,dtc-docs,dtc-faq,dtc-podwiki] A sync candidate should be validated and rendered completely before one transaction swaps it into the active snapshot. A failed sync leaves the last good snapshot live.
- [INFERENCE github-webhook-validation] Signed webhooks should enqueue sync by repository and commit SHA; scheduled reconciliation and manual Studio sync provide recovery paths.
- [INFERENCE dtc-main-site,dtc-docs,dtc-faq,dtc-podwiki] Each record retains repository, branch, source path, source commit, content hash, raw frontmatter, raw body, rendered HTML, stable identifier, canonical path, timestamps, and validation status.
- [INFERENCE dtc-main-site,dtc-docs,dtc-faq,dtc-podwiki] The application must build and test a complete route/link manifest from current generated sites before cutover.

## Course-platform boundary

- [HUMAN] The course platform is adopted from its existing Django repository and evolved in place.
- [INFERENCE dtc-course-platform] GitHub-backed editorial content uses versioned read models, while adopted course/cohort data remains operational relational data managed in Studio/API.
- [INFERENCE dtc-course-platform] The existing edition-like `Course` becomes `Cohort`; a new `Course` parent holds reusable family identity. Curriculum stays cohort-owned for the first release.
