# Deterministic test harness

All ordinary tests use one owned runtime below the current worktree. The top-level command chooses
one run id, and child processes inherit it. Django and pytest workers receive separate databases,
browser state, server state, and artifacts:

```text
.tmp/tests/<worktree-id>/<run-id>/<worker-id>/
  database/test.sqlite3
  browser/
  server/
  artifacts/
```

The runtime refuses a second owner for the same worktree/run pair. It also refuses symlinks,
database overrides, paths outside the worker root, non-SQLite Django connections, and cleanup of
another run. Do not put test databases, downloads, traces, screenshots, or scratch fixtures outside
this tree. The independent tester may copy reviewed screenshots to the process-required
`.tmp/screenshots/issue-<n>/` handoff directory.

The owner closes Django database connections and removes its exact token-attributed runtime after
success, failure, interruption, and `SIGTERM` timeout. Termination cleanup verifies the owner record
before removing the SQLite database and any WAL/SHM siblings; it never removes a neighboring run.

## Commands

The supported local entry points are:

```text
make test-core
make test
make test-ci
CI_SELECTION_PATH=.tmp/ci-selection/ci-selection.json make test-ci-focused
make test-factories
make migrations-check
make test-migrations
make test-playwright-smoke
make test-playwright-core
make test-playwright
make test-browser
make test-all
```

`test-all` is the complete locked local acceptance aggregate. It never selects a remote/live test,
provider, AWS operation, or deployed database. `test-browser` is a stable alias for the full local
Playwright target. The four explicit safety commands are `test-remote-readonly`,
`test-remote-mutation`, `test-live-email`, and `test-live-provider`; they are not CI targets and
still require the exact marker, isolated-development target class, approved HTTPS base URL, bounded
synthetic namespace, and any marker-specific authority before a client is imported or a socket is
opened.

## Factories and scenarios

Construct new deterministic data with `test_support.factories.FactoryContext`. Supply a logical
seed, an execution namespace, and a timezone-aware frozen UTC instant. Domain values may use only
the logical seed and frozen instant. Use the context's per-factory sequence, private random stream,
and name-based UUID methods; never use process-global random state, UUID4, implicit wall-clock time,
filesystem order, or database sequence order.

The shared catalog defines the accepted accounts/management, editorial content, adopted courses,
historical event totals, operations/jobs, and provider-neutral messaging bundles. Each leaf appears
in minimal, complete, boundary, rejected, stale/conflict, and privacy/redaction scenarios. Logical
payloads must remain byte-identical across worker namespaces; only physical identifiers may differ.
Calling a leaf normally returns its canonical logical specification. Call `leaf.create(context)` for
one real current-domain value, or call `create_current_scenario(context, bundle=..., state=...)` to
compose the full bundle. The persistence layer creates actual current ORM relationships (and the
accepted in-memory messaging simulator where no provider-neutral model exists), uses physical
namespace-derived database identities, and records invalid scenarios as rejected values without
persisting the rejected row. Reusing one context composes multiple named leaf calls through one
persisted scenario graph. Current-domain canonical JSON includes normalized values read from the ORM
or simulator; namespace-only primary, unique, and relationship identities are represented by stable
markers so worker namespaces remain byte-identical while changes to domain states remain visible.

When an owning product issue adds a model or service input:

1. Add the leaf to the bundle owned by that application. Do not invent a future-domain model.
2. Build service-owned state through its application service, not by bypassing its invariants.
3. Use visibly synthetic values, reserved email domains, and reserved URL hosts.
4. Add deterministic, reverse-construction-order, namespace-isolation, invalid, stale/conflict, and
   redaction assertions to `test_support/tests/test_factories.py` or the owning app test label.
5. Review the application closure in `ci/selection.py`. A new or unmapped app stays full-suite.

Public source-derived fixture bytes require a sibling provenance JSON file containing the fixture
schema/version, source repository, exact 40-character revision, safe relative source path, and byte
SHA-256. Private/provider fixtures are hand-authored synthetic data and record only their schema
family and generator version. Recursive private fixture values may not contain email/name/filename,
event/answer/token/credential/payload, attendee/registration, provider identifier/digest, or source
path/digest fields—even when the example is synthetic. Tests never fetch or refresh either fixture
class from the network.

## Browser tests and safety metadata

Every file below `playwright_tests/` must give each test exactly one `smoke`, `core`, or `full`
marker. The smoke tier is the bounded first-pass availability/auth and representative-surface
coverage; core adds ordinary render-impact coverage; full retains exhaustive and high-risk
coverage. The full target is the union of all three local tiers, while the tiers are disjoint.
`remote_readonly`, `remote_mutation`, `live_email`, and `live_provider` are orthogonal safety markers;
a test may use at most one. Ordinary local and CI marker expressions exclude all four.
Django/unittest and service tests declare the equivalent non-pytest metadata with
`@django_test_safety("remote_readonly")` (or the other exact safety class). The decorator stores the
code-owned classification and performs the same pre-connection environment authorization when the
test executes.

Use the shared `live_server` and `context` fixtures. The fixture owns the loopback origin, clean
browser context, fixed UTC clock and locale, route denial, trace, screenshot, and cleanup. External
requests, unexpected console/page/request failures, dialogs, and downloads fail the test. Do not
add a fixture that disables the network guard or shares storage/authentication state between roles.

The ordinary-process guard denies non-loopback stream and datagram sends plus every standard DNS
entry point, carries itself into normal Python children through `subprocess`, `posix_spawn`, and
`posix_spawnp`, and rejects known network CLIs, Git network operations, shell-wrapped Python/network
commands, and Python or environment flags that suppress child guard startup. This is an
application-level boundary, not an operating-system network namespace: it cannot prove that an
arbitrary native executable or an opaque script has no egress. Ordinary tests must not launch such
programs. Environments requiring universal native-process denial must also isolate egress at the
container or runner level.

Test email uses `SyntheticCaptureEmailBackend`, not Django locmem delivery. The send boundary accepts
only reserved `example.invalid` recipients and stores a redacted recipient representation without
constructing a provider. Pytest scans the owning worker publication artifacts and the complete
`.tmp/screenshots/` handoff tree before cleanup, and Playwright also scans after writing its
trace/screenshot. Screenshot capture masks email-shaped values and trace capture removes them before
publication. The scan includes compressed trace members and fails the run if any email-shaped value,
a code-owned canary, or a bounded value supplied through `DTC_TEST_ARTIFACT_CANARIES_JSON` appears;
protected values are never printed.

## Migration tests

`migrations-check` detects model/migration drift. `test-migrations` separately runs a zero-to-leaf
upgrade and maintained historical upgrade paths against unique SQLite files.

Add immutable seeds below `test_support/migration_seeds/` with a versioned name, explicit start and
target nodes, synthetic input rows, expected result, and checksum. Tests must load historical models
with the executor's app registry. Migration modules may not import current application models,
services, or factories and may not use the network, random state, or wall clock. If a maintained
historical node is replaced, update the seed contract explicitly and review its reverse/resume
expectation. Set `reversible` to the behavior of the historical migration graph: a no-op data reverse
that permits the schema reversal is reversible and must be exercised through reverse plus resume;
a missing reverse callable must assert Django's bounded `IrreversibleError` and leave the migration
at its target state.
