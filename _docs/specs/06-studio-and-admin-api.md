# 06 - Studio and admin API

Status: draft

Studio is the product's management interface at `/studio/`. The versioned management API is at `/api/v1/admin/`. They are two adapters over the same services, permissions, validation, concurrency controls, and audit trail.

## Management parity contract

Every manageable capability is declared once in a code-level registry with:

- stable capability key and description;
- query or command service;
- required Django permission and optional object/field policy;
- Studio route and allowed HTTP method;
- admin API operation ID, route, and method;
- idempotency and concurrency policy;
- audit action and sensitive-field redaction policy;
- test factory and whether confirmation/reauthentication is required.

CI fails when:

- a Studio mutation has no admin API operation;
- an admin API management operation has no Studio workflow, unless explicitly marked machine-only with owner approval;
- either adapter bypasses the registered service or permission;
- the OpenAPI document omits a routed management operation;
- positive/negative parity tests differ in result or side effects.

Studio may submit HTML forms directly to Django rather than making browser-side API calls. HTTP self-calls are not required; shared application services are.

## Roles and permissions

Recommended groups:

- `site_admin`: staff identities, roles, API credentials, integrations, dangerous configuration, and all lower permissions;
- `content_operator`: source configuration view, sync, preview, activate, rollback, link diagnostics, navigation/announcement/sponsor management;
- `course_operator`: courses, cohorts, cohort-owned curriculum, teaching teams, registrations, enrollments, assignments, submissions, scoring, peer review, complaints, leaderboards, and certificates;
- `event_operator`: events, people assignment, registrations, attendance, exports, and event notifications;
- `email_operator`: template versions, test sends, delivery diagnosis, retry/resolution, suppression view;
- `support_operator`: limited user/registration/enrollment lookup and approved corrective actions, with masked PII by default;
- `auditor`: read-only operational and audit access.

Permissions are capability-based rather than a single `is_staff` check. One user may hold several groups. Security administration, credential creation, and role grants require `site_admin`; granting another `site_admin` requires reauthentication and an explicit confirmation.

## Studio sections

- Dashboard: service health, content freshness, worker heartbeat, queue age, failed/ambiguous email, upcoming events/cohorts, and recent high-risk actions.
- Content: sources, sync runs, candidate previews, releases, routes, links, search/graph, and GitHub edit links.
- People: searchable public profiles and derived author/guest/speaker/host/instructor relationships; profile edits go to GitHub.
- Courses: courses, cohorts, homework/questions, projects/criteria, schedules, teaching teams, registrations, enrollments, submissions, peer review, scoring, complaints, leaderboards, certificates, and communication status.
- Events: lifecycle, people, registrations, attendance, exports, calendar changes, and notifications.
- Email: templates, previews, test sends, deliveries, attempts, SES events, suppression, and worker diagnostics.
- Site: navigation, announcements, sponsors, redirects, SEO exceptions, and safe settings.
- Access: staff users, groups, service principals, API tokens, active sessions, and revocation.
- Audit: filterable append-only events and export with restricted access.

All authenticated Studio responses use `Cache-Control: private, no-store` and must not appear in search engines or shared caches.

## Admin API coverage

Management resources include:

- content sources, sync runs, releases, activation/rollback, route/link diagnostics, and search/graph status;
- people lookups and relationship validation;
- courses, cohorts, cohort-owned homework/questions/projects/criteria, teaching teams, schedules, registrations, enrollments, assignments, submissions, reviews, scores, complaints, leaderboards, statistics, certificates, imports, and exports;
- events, speakers/hosts, registrations, attendance, calendar changes, and notification operations;
- email templates/versions, previews/test sends, deliveries, attempts, provider events, suppression, retry/resolution operations;
- navigation, announcements, sponsors, redirects, site settings, health/operational reports;
- staff, roles, service principals, credentials, sessions, and audit events.

Public and learner APIs live outside `/api/v1/admin/` with separate schemas and authorization. FAQ JSON feeds and course-platform compatibility endpoints keep their existing contracts.

## Authentication

### Studio

- OIDC-backed human login with provider-enforced MFA.
- Secure, HttpOnly, SameSite cookies; CSRF on every state-changing request.
- Idle and absolute session timeouts, login throttling, logout/revocation, and break-glass recovery.
- Active staff status is checked on every request.

### Admin API

- `Authorization: Bearer <token>` only; never credentials in URLs.
- Human and service tokens are separate principals with explicit scopes aligned to capabilities.
- Tokens contain a lookup prefix plus secret, are shown only once, and only a password-style digest is stored.
- Tokens have creator, owner/principal, name, expiry, revocation, last-used approximation, and optional network restrictions.
- Disabling the owner/principal or removing a permission invalidates effective access immediately.
- Rotation creates a new token with an overlap window only when explicitly requested; revocation is audited.
- CORS is deny-by-default.

## API conventions

- JSON-only request/response for management operations.
- Stable `/api/v1/admin/` prefix and generated OpenAPI 3.1 document.
- UUID identifiers in management routes; public slugs are fields, not identity.
- Consistent error envelope with code, safe message, field errors, request ID, and documentation link where useful.
- Cursor or bounded page-number pagination with maximum page size.
- Explicit filter/sort field allowlists and field-level serialization.
- No mass assignment: every writable field is declared per command.
- No object existence leak: authorized querysets/policies apply before lookup.
- Revision or `If-Match` required for concurrent mutable resources; stale writes return `409 Conflict`.
- `Idempotency-Key` required for retryable creates and side-effect commands such as sync, activation, bulk enrollment, scoring, certificate issue, export, and email initiation.
- Long-running/bulk operations return `202 Accepted` plus an operation resource with progress, errors, cancellation policy, and result summary.
- Safe methods are side-effect free. Mutations never use GET.
- Request/body/time limits and per-principal rate/cost limits apply.

## Runtime release identity

Studio's footer displays only `Version <VERSION>`. The authenticated management health response
and generated OpenAPI document use the same canonical runtime identity: OpenAPI `info.version` is
VERSION, while health reports `version`, nullable `source_sha`, and nullable `image_digest`.
Production/development ECS runtime values are non-null and exact; only local execution uses the
documented fallback version with null source and digest. These safe identifiers may appear in
operational output, but credentials and task environment payloads may not.

## Audit model

`AuditEvent` is append-only and stores:

- actor user and/or API principal with `SET_NULL` retention;
- action, target type/UUID, and immutable target-label snapshot;
- outcome, timestamp, request/correlation/idempotency IDs;
- structured redacted changes and metadata;
- source IP classification where justified, with limited retention.

Audit at minimum:

- authentication failures, session/token/role changes;
- content sync, activation, rollback, and source configuration;
- course/cohort lifecycle, grading changes, enrollment changes, peer-review repair, leaderboard complaint resolution, and certificate actions;
- event lifecycle and registration changes;
- PII access/export, bulk operations, template publication, sends, retries, and ambiguous email resolution;
- redirect/site configuration and integration changes;
- allowed and denied high-risk API requests.

Authorization headers, plaintext credentials, management links, email bodies, and unnecessary PII are never audited.

## Safety requirements

- Destructive, bulk, export, send, sync activation, and role/credential actions use explicit confirmation.
- Dangerous actions show scope and expected count before execution.
- Deleted operational records use guarded archival unless legal deletion requires anonymization.
- CSV/worksheet exports neutralize formula injection.
- Support preview/view-as functionality is capability-scoped, prominently labeled, read-only by default, and audited. Unrestricted impersonation is not copied from the reference systems.
- Built-in Django admin is disabled in production or reserved as a separately protected superuser-only break-glass surface; it is not the normal management interface.

## Acceptance criteria

- The capability registry covers every Studio and admin API management action.
- CI proves route, OpenAPI, permission, service, audit, idempotency, and result parity.
- Each role has positive and negative tests, including object- and field-level PII restrictions.
- API secrets are one-time display and hashed at rest; rotation/revocation tests pass.
- Studio/API responses containing authenticated or PII data are never publicly cached.
- Stale edits, duplicate commands, bulk partial failures, denied operations, and audit redaction behave consistently.
