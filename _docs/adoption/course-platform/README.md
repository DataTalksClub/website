# Course platform adoption provenance

Issue: [#30](https://github.com/DataTalksClub/website/issues/30)

Source repository: `DataTalksClub/course-management-platform`

Pinned source commit: `98a235283904b4ef9ad29e196298540756cf1bcc`

The machine-readable pin is [`source-pin.json`](source-pin.json). The controlled
upstream procedure is [`upstream-sync.md`](upstream-sync.md).

The adoption source was created without using the source working tree's files.
For a fresh checkout, provision the pin with:

```bash
uv run python scripts/prepare_course_platform_source.py
uv run python scripts/verify_course_platform_adoption.py
```

The helper clones `source_repository`, checks out the exact pinned commit
detached, and rejects a dirty existing checkout. The verifier then confirms the
exact pinned commit followed by no status entries. The complete upstream
procedure is in [`upstream-sync.md`](upstream-sync.md).

## Tracked allowlist and literal copy

Original migrations were copied first:

```bash
mkdir -p accounts/migrations courses/migrations data/migrations cadmin/migrations
cp -R .tmp/cmp-source-98a2352/accounts/migrations/. accounts/migrations/
cp -R .tmp/cmp-source-98a2352/courses/migrations/. courses/migrations/
cp -R .tmp/cmp-source-98a2352/data/migrations/. data/migrations/
cp -R .tmp/cmp-source-98a2352/cadmin/migrations/. cadmin/migrations/
```

The maintained tracked application/test surface was then copied literally:

```bash
cp -R .tmp/cmp-source-98a2352/accounts/. accounts/
cp -R .tmp/cmp-source-98a2352/api/. api/
cp -R .tmp/cmp-source-98a2352/cadmin/. cadmin/
cp -R .tmp/cmp-source-98a2352/course_management/. course_management/
cp -R .tmp/cmp-source-98a2352/courses/. courses/
cp -R .tmp/cmp-source-98a2352/data/. data/
cp -R .tmp/cmp-source-98a2352/e2e/. e2e/
mkdir -p course_platform_templates
cp -R .tmp/cmp-source-98a2352/templates/. course_platform_templates/
cp -R .tmp/cmp-source-98a2352/scripts/. scripts/
```

Issue #116 mechanically moves the copied `cadmin/` destination to `studio_courses/`, including
its Python, test, template, and static namespaces. The source paths above remain the literal-copy
provenance. `copied-files.tsv` now records each pinned `cadmin/*` source against its exact
`studio_courses/*` destination, while the two-file target-owned `cadmin/` package contains only
the accepted legacy URL adapter.

The source root templates use a distinct destination so their byte identity is retained without replacing the unified website's root template shell. The manifest records this mapping.

Issue #133 keeps `courses/templates/courses/register.html` byte-identical and adds one deliberately
narrow copied-view seam in `courses/views/registration.py`: the previous direct registration-row
count is supplied by the course-owned completeness-gated historical-baseline service. Its current
view and characterization-test bytes are recorded in `integration-patched-files.tsv`; the source
bytes remain pinned in `copied-files.tsv`. All source derivation, activation, replacement, rollback,
Studio, and admin API behavior is target-owned outside the copied template.

Allowlisted tracked roots are `accounts/`, `api/`, `cadmin/`, `course_management/`, `courses/`, `data/`, `e2e/`, `templates/`, and `scripts/`. This includes all original app migrations, tests, templates/static assets, management commands, compatibility API, cadmin, and source operational scripts referenced by characterization tests.

## Explicit exclusions

All non-allowlisted source paths were excluded rather than copied:

- repository/agent/editor metadata: `.claude/`, `.github/`, `.vscode/`, `.gitignore`, `.dockerignore`, `.prod-versions`, `.python-version`, `AGENTS.md`, and `CLAUDE.md`;
- environment/developer files: `.env_template`, `.envrc_template`, `tunnel-dev.sh`, and `tunnel-prod.sh`;
- source deployment/runtime scaffolding: `Dockerfile`, `Makefile`, `docker-compose.yml`, `docker-entrypoint.sh`, `entrypoint.sh`, `deploy/`, and `db/`;
- source project wrappers resolved through target integration: `manage.py`, `pyproject.toml`, and `uv.lock`;
- prose/reference material outside the maintained runtime allowlist: `README.md`, `docs/`, and `endpoints.md`;
- unrelated exploratory material: `notebooks/`.

No untracked source file was eligible because the clean checkout was verified before copying. No source secret, local database, environment, cache, build output, deployment state, or notebook was copied.

## Reproduction and drift verification

`copied-files.tsv` records every allowlisted tracked source path, destination, byte size, and SHA-256. Run:

```bash
uv run python scripts/verify_course_platform_adoption.py
```

The verifier checks the source commit and clean status, recomputes the Git-tracked allowlist and
manifest, detects omitted allowlisted files, and proves every unpatched copied destination remains
byte-identical to the pinned source. Every deliberate target integration change is listed in
`integration-patches.md`; copied business behavior is retained and characterized rather than
reimplemented.

Copied presentation and adapter files changed for target integration keep their source checksums
in `copied-files.tsv` and their post-integration checksums and rationales in
`integration-patched-files.tsv`. The verifier checks that explicit state instead of treating those
files as source-identical.

## Evidence index

- `behavior-inventory.md`: generated inventory of all 89 adopted HTML/account/API/Studio Courses
  routes, all 13 adopted management commands, and the active app/preserved migration identities;
- `verification.md`: characterization counts, migration evidence, environment dispositions, and
  remaining parity gate;
- `integration-patches.md`: target-owned compatibility changes;
- `cadmin-reference-allowlist.tsv`: every remaining source-provenance or legacy-adapter reference,
  with its owner and removal gate;
- `target-owned-compatibility-shims.tsv`: checksums and per-file rationale for the two retained
  scaffold admin API shims and the two-file legacy `/cadmin` route adapter;
- `migration-squash-gate.md`: evidence required before any original migration can be replaced.
