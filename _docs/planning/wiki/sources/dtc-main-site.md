# DataTalks.Club main website repository

Locator: https://github.com/DataTalksClub/datatalksclub.github.io/tree/ee43d3fa0929faf691178d79f19528e6f15a83e5

Accessed: 2026-08-07

## Summary

The repository is the primary evidence for current URLs, content schemas, joins between people and content, and SEO output.

## Claims

- [FACT dtc-main-site] Main hubs use legacy `.html` paths, while posts, podcasts, books, people, courses, tools, and conferences use collection-specific `.html` detail paths configured in `_config.yml`.
- [FACT dtc-main-site] The captured tree contains 55 posts, 206 podcast files, 99 books, and 439 people profiles.
- [FACT dtc-main-site] A person's `short` value is the stable identifier referenced from authors, podcast guests, event speakers, and tool maintainers.
- [FACT dtc-main-site] Frontmatter and bodies contain nested YAML, raw HTML, Liquid, Kramdown attributes, JavaScript integrations, MathJax, charts, and reusable includes; plain Markdown rendering is not compatible by itself.
- [FACT dtc-main-site] Canonical, Open Graph, Twitter, sitemap, robots, and structured-data behaviors are implemented in the current templates and generated files.
- [FACT dtc-main-site] Events are currently sourced from `_data/events.yaml` and link to external registration pages.

## Limitations

- [FACT dtc-main-site] The repository was inspected with local uncommitted `uv.lock` changes present; claims are based on source files and recorded HEAD, not that unrelated local change.
- [FACT dtc-main-site] Older podcast files have schema variants and need compatibility fixtures rather than a single strict import assumption.

## Related

- [HUMAN] [Human decisions](../notes/human-decisions.md)
- [INFERENCE dtc-main-site] [Django content architecture](../concepts/content-architecture.md)
