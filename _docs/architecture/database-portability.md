# Database portability boundary

Issue #98 makes SQLite the deterministic database for local development and ordinary CI. The
maintained Django application uses portable ORM operations and declarative constraints. Deployed
development and production continue to use RDS PostgreSQL as an infrastructure choice, validated
only by exact-image migration, database-aware readiness, and deployed smoke.

## Settings boundary

- `website.settings.local` defaults to `.tmp/local.sqlite3`.
- `website.settings.test` defaults to `.tmp/test.sqlite3`; Django still creates isolated test
  databases from that setting.
- `DTC_SQLITE_PATH` may select an absolute SQLite path or a repository-contained relative path.
- Local and test settings do not read `DATABASE_URL` or the retired `DTC_USE_SQLITE` opt-in.
- `website.settings.development` and `website.settings.production` require a valid PostgreSQL
  `DATABASE_URL` and never fall back to SQLite.

`make database-portability-check` prevents PostgreSQL services or configuration from returning to
ordinary quality, Django, Playwright, or container CI jobs. It also rejects vendor branches,
backend-feature skips, PostgreSQL-only test modules, raw migration SQL, triggers, advisory locks,
and row-lock calls in maintained Django code. The same gate scans the authoritative specifications
and fails closed on any PostgreSQL reference not classified as deployed configuration/storage,
runtime-driver connectivity, an explicit prohibition or SQLite contrast, bounded historical/adoption
follow-up, or clearly marked hypothetical/reported-invalid documentation. It also rejects links to
the former AWS-specification path; maintained links use
`_docs/specs/08-aws-development-terraform.md`.

Mandate classification is clause-scoped rather than phrase-specific. It recognizes active and
passive provider predicates, negative permission, past-use markers, conditional/counterexample and
reported-invalid scopes, and explicit SQLite responsibility. Contrast and temporal boundaries end
an exemption before a current mandate, while an explicit SQLite selection resets later database
anaphora; any later explicit PostgreSQL mandate still fails.

The normative scan uses a code-owned exact catalog for the specification index, specifications 01
through 10, and open decisions, with the former AWS filename retained only as a known non-normative
compatibility shim. Missing, renamed, case-variant, or extra Markdown candidates fail the gate, as
do symbolic links, directories, special files, unreadable files, or paths escaping the catalog
root. Before classification, prose and fenced configuration are normalized for Unicode NFKC,
dash variants, default-ignorable characters, bounded fixed-point HTML decoding, apostrophes, case,
and whitespace. Classification retains each reference's full statement context and evaluates
application, search, indexing, transaction, concurrency, and isolation mandates before considering
deployment, historical, or prohibition wording. AWS link checks use bounded fixed-point HTML and
percent decoding across inline/reference Markdown (including multiline labels), raw HTML,
autolinks, encoded targets, plain relative targets, and bare URLs. The obsolete filename is rejected
anywhere in a link target, while historical prose that merely names a former file is not treated as
a link; decoding that cannot stabilize within the bound fails closed.

## Portable active-content namespace

An enabled source's active document and asset paths are represented by persisted
`ActiveContentPath` claims. The SHA-256 path digest is the unique fixed-width database key, while
the exact path, source, and release remain inspectable fields. Activation and rollback replace one
source's claims in the same transaction as release statuses, the source pointer, and the audit
event. A unique-claim collision therefore rolls the losing swap back completely on both SQLite
and PostgreSQL.

Activation and rollback perform a read-only preflight, then repeat every revision, readiness,
pointer, current-claim, and namespace check inside the write transaction. The second validation
closes the gap in which another source may activate after preflight. SQLite write contention and
equivalent transient `OperationalError` failures retry only that atomic phase with a fixed bound;
the test seam and any external work are not repeated. Exhaustion propagates without a partial
pointer, claim, release, or audit change.

Migration `content.0002_active_content_path_claims` backfills claims for already-active enabled
sources and fails closed if their path ownership is already ambiguous. This is a portable Django
data migration, not backend-specific SQL.

## Remaining-term inventory

Repository searches for `postgres`, `postgresql`, `psycopg`, and `DATABASE_URL` are expected only
in these bounded categories:

| Category | Paths | Reason |
| --- | --- | --- |
| Deployed bootstrap and release | `core/bootstrap.py`, `website/settings/base.py`, `Makefile`, `deploy/`, release workflow evidence, and their focused tests | Fail-closed RDS configuration, task-secret wiring, migration/readiness/smoke, and rollback evidence |
| Infrastructure specification and operations | `_docs/specs/08-aws-development-terraform.md`, deployed sections of the architecture/specs, and `_docs/runbooks/development-release.md` | The deployed database remains private RDS PostgreSQL |
| Runtime dependency | `pyproject.toml` and `uv.lock` | `psycopg` is required by deployed Django processes |
| Redaction and compatibility fixtures | `core/redaction.py`, compatibility tests, bootstrap/settings tests, and deployment tests | Prove database URLs are rejected or redacted without exposing credentials |
| Preserved CMP or migration tooling | `course_management/settings.py`, the production importers under `scripts/prod/`, and adoption provenance records | Byte-preserved historical code, or a migration path that reads a SQLite export in place; no ordinary Make/CI entry point uses it. `scripts/load_rds_export.py` was retired here: its `main()` was disabled, but its copy plan skipped only `sqlite_sequence` and `django_migrations`, so re-enabling it would have copied live sessions and OAuth rows out of a production export |
| Local review-data gate | `review_import/` and its tests | The separately named, SQLite-to-SQLite allowlisted review workflow uses SQLite integrity/introspection pragmas; it is not Django application behavior or ordinary CI database setup |
| Historical planning evidence | `_docs/planning/` and pinned compatibility/adoption evidence | Non-normative source material retained for provenance; current specifications supersede its database recommendations |

Raw cursor use remains only in portable schema-introspection tests and the disabled, copied RDS
export utility plus its characterization tests. SQLite `PRAGMA` calls remain in the separately
named local review-data gate and its tests. Both are absent from Django application services and
the maintained migration graph. `core/bootstrap.py` is the only maintained application module
that compares an engine name, and does so solely to enforce the deployed configuration boundary.

## Already-applied development objects

Earlier migrations may already have installed PostgreSQL-only hardening in the shared development
database: the `dtc_core_*_append_only`, `dtc_content_*_guard`, and
`dtc_management_*_immutable` functions and their triggers, plus
`jobs_unique_code_schedule_name`. The portable application no longer depends on them, and the
fresh migration graph neither creates nor destructively drops them. They do not block the portable
service transitions.

Before a future production-account migration, operations must inventory these names in the source
database and decide in a separately reviewed maintenance issue whether to leave or remove them.
Any removal must use an explicit backup-verified operation; it must not be hidden in a baseline
migration or performed as part of ordinary CI.
