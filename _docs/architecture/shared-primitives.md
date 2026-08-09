# Shared application primitives

Issue #31 establishes the small contracts used by every domain without moving domain behavior
into `core`.

## Bootstrap configuration

`core.bootstrap` parses bootstrap values before database access. Boolean values use exact
`false`/`true`/`0`/`1` forms, integer values use canonical decimal text, lists are bounded and
reject empty or duplicate members, and runtime
environment names are explicit. Errors name only the setting and a stable reason; they never echo
the rejected value because database URLs and other bootstrap values may contain credentials.

Deployed development and production accept PostgreSQL only, and each deployed settings module
rejects a missing, unknown, or mismatched runtime-environment value. Local and test settings select
project-local SQLite deterministically, ignore ambient `DATABASE_URL`, and never become an implicit
deployed fallback.
Deployed secret keys must meet Django's length and character-diversity baseline, contain no control
characters, and differ from known development defaults. Validation errors never echo secret input.

Safe settings that can change after startup belong in the database-backed configuration models
and services. They remain typed, versioned, source-visible, and audited rather than becoming
unstructured environment overrides.

## Execution context and redaction

Request, correlation, and job IDs live in `ContextVar` values so concurrent requests and worker
tasks cannot overwrite each other. IDs are opaque, bounded, and restricted to a small character
set. HTTP middleware accepts valid request/correlation headers, generates safe replacements for
invalid input, propagates both response headers, and resets its tokens in `finally`. Job wrappers
use the same binding/reset contract. External request/correlation values that resemble known JWT,
GitHub, or AWS credential shapes are replaced; internal opaque identifiers keep the general ID
contract.

Audit and diagnostic metadata passes through `core.redaction.redact`. It makes a bounded recursive
copy, normalizes field-name case and separators before applying the sensitive-field policy,
protects explicit/provider canaries even beneath an innocuous key, handles cycles, and never calls
`str()` on an unknown value that might contain a secret. Authorization, credentials, tokens,
passwords, secret keys, database URLs, cookies, bodies, private keys, email addresses, and HTTP(S)
management links are redacted. Canary input is itself bounded; excessive canary configuration
fails closed rather than allowing an unbounded scan or leaking an unexamined canary.

## Command and query boundary

Domain applications expose commands and queries matching `core.services.CommandService` and
`QueryService`. `ServiceContext` carries only opaque actor and execution identifiers; adapters do
not pass request bodies, credentials, or unnecessary PII through it.

An `actor_ref` is a bounded attribution snapshot such as a user or API-principal identifier. It is
persisted with audits, operations, and setting history, remains after a linked user is deleted, and
is queryable for investigations. It is never an authorization input; adapters and services must
still resolve the current user/principal and permissions.

- A command owns validation, authorization hooks, its database transaction, revision and
  idempotency enforcement, audit creation, and persistence of durable work.
- Network work never runs inside that transaction. The command stores a durable job/outbox row and
  arranges wake-up through `transaction.on_commit`.
- A query is side-effect free and applies its authorization scope before object lookup.
- Public HTML, Studio, admin API, jobs, and tests call the same service. Adapters translate
  transport input/output but do not reimplement business rules.
- Queued work carries scalar identifiers plus its correlation/job context. A worker reloads and
  reauthorizes the current database state before acting.
- Durable handlers execute **at least once**, not exactly once. A lease token fences stale database
  completion but cannot undo an external effect made immediately before a worker crash. Handlers
  must reload current domain state and use the stable `JobContext.job_id` (or a domain outbox key
  derived from it) as the provider/domain idempotency key on every retry. Long-running handlers
  must also renew their fenced job lease with `job_id` and `lease_token` before expiry while they
  report progress; otherwise recovery may overlap an attempt that is still running.

The concrete revision, idempotency, operation, audit, configuration, lease, scheduler, and durable
dispatch models remain owned by their named `core` or `jobs` modules. Domain code uses their
services instead of importing presentation or worker code.

Append-only audit and configuration-history behavior is enforced at the supported application
boundary: model methods and managers expose insertion and retention-reference cleanup only, while
Studio, API, jobs, and public adapters share the same writers. Declarative constraints protect
state consistency on both supported engines. Privileged direct database tampering is outside this
portable application trust boundary, and maintenance uses an explicitly reviewed procedure.

Content activation additionally uses a persisted, uniquely keyed active-path claim for every
enabled source path. The service revalidates after preflight and swaps claims, release state, the
source pointer, and its audit event atomically. Bounded whole-transaction retry handles portable
database write contention; it never reruns the preflight seam and cannot publish two path owners.
