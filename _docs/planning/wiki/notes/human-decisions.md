# Human decisions

- [HUMAN] Rebuild the current GitHub Pages website as a Django application.
- [HUMAN] Include the main website, docs, FAQ, podcast/blog, and Podwiki.
- [HUMAN] Keep blog, podcast, docs, FAQ, and Podwiki content in GitHub rather than making the Django database the editorial source of truth.
- [HUMAN] Add event registration and transactional email, including an email when someone registers.
- [HUMAN] Build a custom staff area called Studio for managing content and operations.
- [HUMAN] Provide an admin-only API for every administrative action.
- [HUMAN] Use the AI Shipping Labs website as an implementation reference.
- [HUMAN] Use `uv` for Python dependency management and command execution.
- [HUMAN] Preserve links so the migration does not harm SEO.
- [HUMAN] A person profile is the canonical entity reused for guests, speakers, hosts, authors, and other public roles.
- [HUMAN] Deploy the development environment to `web.dtcdev.click` in AWS account `817685572750` and manage it with Terraform in the sandbox infrastructure tree.
- [HUMAN] Design Terraform so the workload can later be instantiated in the production AWS account.
- [HUMAN] Integrate the existing DataTalksClub course-management platform into this application so courses and the public website are one website.
- [HUMAN] Replace the existing course-edition model with an explicit `Course -> Cohort` structure.
- [HUMAN] All management capabilities must be available in Studio.
- [HUMAN] Everything manageable in Studio must also be manageable through the admin API.
- [HUMAN] Adopt and continue using the existing course-management Django code instead of reimplementing its corner cases.
- [HUMAN] Make only the structural changes needed to add Course as the reusable parent of Cohort, then adapt existing relationships.
- [HUMAN] Eventually replace the old course application at `courses.datatalks.club` with a small redirect Lambda deployed through Terraform.

## Planning interpretations

- [INFERENCE dtc-main-site,dtc-docs,dtc-faq,dtc-podwiki] Link preservation includes public route paths, fragment identifiers, internal destinations, external destinations, asset URLs, canonical URLs, and machine-readable endpoints.
- [INFERENCE dtc-main-site] Person roles should be represented through relationships to content and events, not a single exclusive role field.
- [INFERENCE dtc-aws-infra] The development deployment should be non-indexable while emitting production canonicals, preventing the test environment from competing with `datatalks.club` in search results.
- [INFERENCE dtc-course-platform] A course should hold reusable family identity while the adopted current Course edition becomes Cohort and retains dates, curriculum rows, enrollment, deadlines, submissions, scores, peer-review assignments, leaderboard state, and certificates.
- [INFERENCE aisl-reference,dtc-course-platform] Studio/API parity should be enforced through a capability registry and shared services, not duplicated business logic.
