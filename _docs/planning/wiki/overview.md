# Overview

- [HUMAN] Replace the static DataTalks.Club sites with Django while preserving GitHub content ownership and public URLs.
- [FACT dtc-main-site,dtc-docs,dtc-faq,dtc-podwiki] The current public surface spans four independently built repositories mounted under one production origin.
- [FACT dtc-main-site] People are shared entities referenced as article authors, podcast guests, event speakers, book authors, and tool maintainers.
- [INFERENCE dtc-main-site,dtc-docs,dtc-faq,dtc-podwiki] URL, link, asset, anchor, JSON, search, graph, metadata, and structured-data parity must be an automated release gate.
- [INFERENCE aisl-reference,dtc-course-platform] AI Shipping Labs offers useful boundaries for sync, Studio, API, jobs, email, and AWS deployment, but the new site should omit its memberships, payments, CRM, and course implementation; DTC's existing course platform is adopted separately.
- [HUMAN] The development stack belongs at `web.dtcdev.click` in AWS account `817685572750` and uses Terraform designed for later production-account instantiation.
- [HUMAN] The existing course-management platform becomes part of the same Django site and is redesigned around reusable courses with dated cohorts.
- [HUMAN] All management actions have both Studio and admin API entry points.

## Key pages

- [HUMAN] [Human decisions](notes/human-decisions.md)
- [INFERENCE dtc-main-site,dtc-docs,dtc-faq,dtc-podwiki] [GitHub-backed content architecture](concepts/content-architecture.md)
- [OPEN] [Open questions](open-questions.md)
