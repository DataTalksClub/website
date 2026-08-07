# DataTalks.Club docs repository

Locator: https://github.com/DataTalksClub/docs/tree/3f23e006ffdaa498bbc69697408853b6f5eb37dc

Accessed: 2026-08-07

## Summary

This repository defines the source and behavior of the `/docs/` subtree.

## Claims

- [FACT dtc-docs] Docs use pretty directory URLs beneath `/docs/` and a frontmatter hierarchy based on `parent`, `grand_parent`, `nav_order`, and optional `permalink`.
- [FACT dtc-docs] Rendering includes Liquid `relative_url`, callouts, Mermaid, raw HTML, tables, fenced code, code-copy, breadcrumbs, theme behavior, and edit-on-GitHub links.
- [FACT dtc-docs] Search is heading-aware and currently implemented with a generated client-side Lunr corpus.
- [FACT dtc-docs] Parent references are display titles rather than immutable IDs, so sync validation must detect ambiguity and broken hierarchy.

## Limitations

- [FACT dtc-docs] Generated `_site` files describe current output but are not editorial source.

## Related

- [INFERENCE dtc-docs] [Django content architecture](../concepts/content-architecture.md)
