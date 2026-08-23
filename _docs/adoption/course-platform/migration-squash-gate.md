# Course migration-history compatibility gate

The deployed lower boundary is the exact `11b2bd1` course graph:
`courses.0001_initial` through `courses.0041_courseregistrationcountsourcerun_and_more`. The
`4825aa3` local squash removed those identities without a Django replacement or bridge; the
current candidate is based on `b7c693e`.

The repaired graph intentionally preserves the deployed modules. For fresh databases, Django uses
`courses.0001_squashed_0029` for the main legacy branch (`0001`–`0026`, `0028`, and `0029`), then
applies the separate `0027_homework_instructions_url_project_instructions_url_and_more` branch
before `0031_merge_instruction_urls_and_profile_fields`. `0027` is not in the replacement list
because `0028` and `0029` do not depend on it; `0031` is the explicit merge point, so absorbing it
would silently omit its homework/project instruction fields. The branch then continues through
`0030`–`0041`. The latter segment remains unsquashed because `courses.0030` depends on
`accounts.0005`, which itself depends on the legacy `courses.0001`; squashing across that
cross-app dependency would create a circular graph. For an existing database with the legacy
records applied, `MigrationLoader` recognizes the replacement without deleting or rewriting those
records.

`courses.0042_course_schema_bridge` is the repaired boundary. It keeps the physical
`courses_course` table and its legacy primary keys, creates one deterministic Course family per
legacy course primary key, converts the legacy row to a Cohort, preserves all existing foreign-key
values, and then applies the current post-squash migrations as `0043`–`0051`. UUIDs and family
slugs are stable across retry; the bridge uses historical `apps` models only and has no reverse
data deletion policy.

The supported engineering envelope is therefore:

- fresh zero-to-leaf migration through `courses.0051_unitreadstate`;
- a populated database whose applied course history ends at the deployed `0041` boundary;
- replay at the repaired leaf with unchanged rows, checksums, foreign keys, and migration
  provenance.

This document records the compatibility contract; it is not production authorization. Exact-image
populated deployment, readiness, smoke, failure classification, and CI/on-call evidence remain the
separate HUMAN gates in issue #220.
