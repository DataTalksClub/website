# DataTalks.Club Podwiki repository

Locator: https://github.com/DataTalksClub/podwiki/tree/988b79d0d655bf4755945c3118544cb9e0dbead6

Accessed: 2026-08-07

## Summary

This repository defines the `/podwiki/` wiki, its typed links, search, graph, and SEO metadata.

## Claims

- [FACT dtc-podwiki] Podwiki exposes landing, catalog, wiki, special-page, search, and graph paths beneath `/podwiki/`.
- [FACT dtc-podwiki] The captured tree has 282 public wiki pages and private registries that mirror podcasts, people, and books for graph/search use.
- [FACT dtc-podwiki] Wiki Markdown contains custom chips, aliases, and timestamped podcast citations processed by a committed WASM extension.
- [FACT dtc-podwiki] Graph hash deep links, search query/filter contracts, generated JSON assets, and canonical links to main-site people/podcast/book pages are public behavior.
- [FACT dtc-podwiki] SEO output includes canonical, Open Graph, Twitter, structured data, breadcrumbs, and git-derived publication/modification dates.

## Limitations

- [FACT dtc-podwiki] Search currently has both a browser fallback and a Lambda-backed production path; the Django replacement must choose ownership without changing the visible contract.

## Related

- [INFERENCE dtc-podwiki] [Django content architecture](../concepts/content-architecture.md)
