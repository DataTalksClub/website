# DataTalksClub course management platform

Locator: https://github.com/DataTalksClub/course-management-platform/tree/98a235283904b4ef9ad29e196298540756cf1bcc

Accessed: 2026-08-07

## Summary

The existing Django application is primary evidence for course operations and migration requirements.

## Claims

- [FACT dtc-course-platform] The current `Course` model combines reusable identity with edition-specific dates, visibility, completion state, scoring thresholds, enrollments, homework, projects, and registration.
- [FACT dtc-course-platform] The platform supports learner accounts, course registrations, enrollments, homework questions and submissions, scoring, projects, peer review, leaderboards and complaints, certificates, statistics, and deadline reminders.
- [FACT dtc-course-platform] It exposes public data endpoints and authenticated management/data APIs with an OpenAPI coverage test.
- [FACT dtc-course-platform] Its custom `cadmin` interface manages courses, enrollments, homework, projects, submissions, complaints, registration campaigns, and Datamailer operations.
- [FACT dtc-course-platform] Transactional and bulk communication currently flows through Datamailer and records send/audit state.
- [FACT dtc-course-platform] Production and development deployments currently use separate course hostnames and ECS-based infrastructure.
- [FACT dtc-course-platform] The audited repository contains about 760 Django test methods and 48 end-to-end test methods covering many course corner cases.
- [FACT dtc-course-platform] The custom staff surface has 26 routes and the HTTP API has 29 routes, while additional management behavior remains in Django admin actions.
- [FACT dtc-course-platform] Important current invariants, including several submission/review uniqueness rules, are enforced in views rather than database constraints and need preflight before schema hardening.
- [INFERENCE dtc-course-platform] Copying and evolving the existing Django apps/tests is lower risk than reimplementing the course domain, provided authorization, model naming, and migration steps use an expand-and-contract approach.

## Limitations

- [FACT dtc-course-platform] The local repository contained unrelated in-progress course-list changes during inspection; planning work did not modify them.
- [INFERENCE dtc-course-platform] The current model has no explicit reusable course versus dated cohort boundary, so migration requires semantic grouping rather than a mechanical table rename.

## Related

- [HUMAN] [Human decisions](../notes/human-decisions.md)
