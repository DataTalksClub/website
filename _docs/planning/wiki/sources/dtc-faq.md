# DataTalks.Club FAQ repository

Locator: https://github.com/DataTalksClub/faq/tree/c8da1deea9e24945922702994de101dd90a5380a

Accessed: 2026-08-07

## Summary

This repository defines FAQ pages, stable fragments, and JSON feeds consumed by other systems.

## Claims

- [FACT dtc-faq] FAQ content contains 1,395 records across six courses in `_questions/<course>/<section>/`.
- [FACT dtc-faq] Each question has a stable ten-character `id`; the public fragment remains stable when a record moves between sections.
- [FACT dtc-faq] Public JSON endpoints use `/faq/json/courses.json` and `/faq/json/<course>.json`, with item fields `id`, `course`, `section`, `question`, and raw Markdown `answer`.
- [FACT dtc-faq] FAQ rendering intentionally treats Jinja/Liquid-looking snippets as content, because answers include examples such as dbt expressions.
- [FACT dtc-faq] Public question pages include edit-on-GitHub links.

## Limitations

- [FACT dtc-faq] The local repository contained an unrelated modified FAQ file during inspection; no planning work changed it.

## Related

- [INFERENCE dtc-faq] [Django content architecture](../concepts/content-architecture.md)
