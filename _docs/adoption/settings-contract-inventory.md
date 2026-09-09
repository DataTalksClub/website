# Settings contract inventory (D0.1b)

Generated from the live `core.configuration` registry; regenerate with the
snippet in `_docs/adoption/` history if declarations change. Every row maps to
the released `community_base.config` declare surface or names its blocking gap.
No secret values are recorded — this inventory is structural only.

## Declaration matrix

| Key | Group | Package type | Default | Site validator | Validation owner | Env var | Settings attr |
|---|---|---|---|---|---|---|---|
| `datamailer.audience` | datamailer | str | `''` | site callable | site adapter | `DATAMAILER_AUDIENCE` | `DATAMAILER_AUDIENCE` |
| `datamailer.client` | datamailer | str | `''` | site callable | site adapter | `DATAMAILER_CLIENT` | `DATAMAILER_CLIENT` |
| `datamailer.from_email` | datamailer | str | `''` | site callable | package (`is_email`) | `DATAMAILER_FROM_EMAIL` | `DATAMAILER_FROM_EMAIL` |
| `datamailer.import_s3_bucket` | datamailer | str | `''` | site callable | site adapter | `DATAMAILER_IMPORT_S3_BUCKET` | `DATAMAILER_IMPORT_S3_BUCKET` |
| `datamailer.import_s3_prefix` | datamailer | str | `'datamailer-imports'` | site callable | site adapter | `DATAMAILER_IMPORT_S3_PREFIX` | `DATAMAILER_IMPORT_S3_PREFIX` |
| `datamailer.import_s3_region` | datamailer | str | `''` | site callable | site adapter | `DATAMAILER_IMPORT_S3_REGION` | `DATAMAILER_IMPORT_S3_REGION` |
| `datamailer.import_url_expires_seconds` | datamailer | int | `3600` | site callable | site adapter | `DATAMAILER_IMPORT_URL_EXPIRES_SECONDS` | `DATAMAILER_IMPORT_URL_EXPIRES_SECONDS` |
| `datamailer.outbox_dispatch_immediately` | datamailer | bool | `False` | - | none needed | `DATAMAILER_OUTBOX_DISPATCH_IMMEDIATELY` | `DATAMAILER_OUTBOX_DISPATCH_IMMEDIATELY` |
| `datamailer.strict` | datamailer | bool | `False` | - | none needed | `DATAMAILER_STRICT` | `DATAMAILER_STRICT` |
| `datamailer.sync_on_user_create` | datamailer | bool | `True` | - | none needed | `DATAMAILER_SYNC_ON_USER_CREATE` | `DATAMAILER_SYNC_ON_USER_CREATE` |
| `datamailer.timeout_seconds` | datamailer | int | `60` | site callable | site adapter | `DATAMAILER_TIMEOUT_SECONDS` | `DATAMAILER_TIMEOUT_SECONDS` |
| `datamailer.transactional_dry_run` | datamailer | bool | `False` | - | none needed | `DATAMAILER_TRANSACTIONAL_DRY_RUN` | `DATAMAILER_TRANSACTIONAL_DRY_RUN` |
| `datamailer.url` | datamailer | str | `''` | site callable | site adapter | `DATAMAILER_URL` | `DATAMAILER_URL` |
| `observability.cloudwatch_metric_namespace` | observability | str | `'CourseManagement/App'` | site callable | site adapter | `CLOUDWATCH_APP_METRIC_NAMESPACE` | `CLOUDWATCH_APP_METRIC_NAMESPACE` |
| `observability.cloudwatch_metric_region` | observability | str | `''` | site callable | site adapter | `CLOUDWATCH_APP_METRIC_REGION` | `CLOUDWATCH_APP_METRIC_REGION` |
| `observability.event_schema_version` | observability | str | `'1'` | site callable | site adapter | `OBSERVABILITY_EVENT_SCHEMA_VERSION` | `OBSERVABILITY_EVENT_SCHEMA_VERSION` |
| `public_media.max_object_bytes` | public_media | int | `8388608` | site callable | site adapter | `PUBLIC_MEDIA_MAX_OBJECT_BYTES` | `PUBLIC_MEDIA_MAX_OBJECT_BYTES` |
| `public_media.s3_bucket` | public_media | str | `''` | site callable | site adapter | `PUBLIC_MEDIA_S3_BUCKET` | `PUBLIC_MEDIA_S3_BUCKET` |
| `public_media.s3_endpoint_url` | public_media | str | `''` | site callable | site adapter | `PUBLIC_MEDIA_S3_ENDPOINT_URL` | `PUBLIC_MEDIA_S3_ENDPOINT_URL` |
| `public_media.s3_prefix` | public_media | str | `''` | site callable | site adapter | `PUBLIC_MEDIA_S3_PREFIX` | `PUBLIC_MEDIA_S3_PREFIX` |
| `public_media.s3_region` | public_media | str | `''` | site callable | site adapter | `PUBLIC_MEDIA_S3_REGION` | `PUBLIC_MEDIA_S3_REGION` |
| `public_media.s3_timeout_seconds` | public_media | int | `5` | site callable | site adapter | `PUBLIC_MEDIA_S3_TIMEOUT_SECONDS` | `PUBLIC_MEDIA_S3_TIMEOUT_SECONDS` |
| `public_media.store_backend` | public_media | str | `'local'` | site callable | site adapter | `PUBLIC_MEDIA_STORE_BACKEND` | `PUBLIC_MEDIA_STORE_BACKEND` |
| `relay.link_bridge.base_url` | relay.link_bridge | str | `''` | site callable | site adapter | `RELAY_LINK_BRIDGE_BASE_URL` | `RELAY_LINK_BRIDGE_BASE_URL` |
| `relay.link_bridge.click_timeout_seconds` | relay.link_bridge | int | `3` | site callable | site adapter | `RELAY_LINK_BRIDGE_CLICK_TIMEOUT_SECONDS` | `RELAY_LINK_BRIDGE_CLICK_TIMEOUT_SECONDS` |
| `relay.link_bridge.open_timeout_seconds` | relay.link_bridge | int | `2` | site callable | site adapter | `RELAY_LINK_BRIDGE_OPEN_TIMEOUT_SECONDS` | `RELAY_LINK_BRIDGE_OPEN_TIMEOUT_SECONDS` |
| `relay.link_bridge.pool_size` | relay.link_bridge | int | `16` | site callable | site adapter | `RELAY_LINK_BRIDGE_POOL_SIZE` | `RELAY_LINK_BRIDGE_POOL_SIZE` |
| `relay.link_bridge.unsubscribe_timeout_seconds` | relay.link_bridge | int | `10` | site callable | site adapter | `RELAY_LINK_BRIDGE_UNSUBSCRIBE_TIMEOUT_SECONDS` | `RELAY_LINK_BRIDGE_UNSUBSCRIBE_TIMEOUT_SECONDS` |
| `site.announcement.enabled` | site.announcement | bool | `False` | - | none needed | `` | `` |
| `site.announcement.message` | site.announcement | str | `''` | site callable | site adapter | `` | `` |
| `site.origin.canonical` | site.origin | str | `'https://datatalks.club'` | site callable | site adapter | `CANONICAL_ORIGIN` | `CANONICAL_ORIGIN` |

## Endpoint and semantics comparison

| Site contract | Package counterpart (v0.3.0) | Verdict |
|---|---|---|
| `GET/PUT` grouped settings batch (`core/settings_batch.py`), `SettingsScope` per audience | `GET /api/v1/config/settings` list + per-key `PUT` (`config/api_views.py`) | GAP-2: no grouped batch |
| Revision conflict (`SettingsRevisionConflict`, optimistic revision) | per-key `PUT` with no revision token | GAP-2 |
| Idempotent batch scope (`_idempotency_scope(scope, actor_ref)`) | none | GAP-2 |
| Sources `{studio, admin_api}` badge on `ResolvedSetting` | `Setting.source` + source badge on the package read model | mapped |
| Read/write permissions `core.read_operational_settings` / `core.change_operational_settings` | package API bearer scopes + Studio staff gate | adapter: site permission ids stay site-side |
| Audit `core.operational_settings.read` / `.batch_updated` | `SettingChange` rows | adapter: site audit event ids emitted by the site wrapper |
| `env_var` override + `settings_attr` Django fallback | `env_var` + `django_settings_fallback` | mapped |
| `docs_reference` (`_docs/specs/01-platform-architecture.md`) | `docs_url` | mapped (site path carried as the value) |
| `lifecycle` / `cache_policy` / `sensitivity` definition columns | none | GAP-3 |
| Validator callables: `_bounded_int`, `_one_of`, `_url`, `_sender`, `_origin` | `is_email` flag + `value_type` coercion only | GAP-1 |

## Blocking gaps (filed before D0.1c)

- GAP-1 (#358): the package `declare` surface has no validation vocabulary for
  bounded integers, choice lists, or URL-scheme checks. Until the package grows
  one, validation stays adapter-owned: the D0.1c shim keeps the site validator
  callables and runs them after the package coercion.
- GAP-2 (#360): the package API has no grouped settings batch with an optimistic
  revision token and an idempotent batch scope. D0.1c keeps
  `core/settings_batch.py` as the writer over package `Setting` rows, or the
  package grows batch support first.
- GAP-3 (#362): definition metadata (`lifecycle`, `cache_policy`, `sensitivity`)
  has no package columns; the adapter persists them site-side or they are
  dropped deliberately in D0.1c.

`value_type` vocabulary differs in spelling only (STRING/INTEGER/BOOLEAN vs
str/int/bool); the mapping is tested in
`core/tests/test_community_base_config_parity.py`, which also proves every row
above is accepted by the released `declare` surface with synthetic values.

## D0.1c implementation mapping (cutover)

Cutover keeps the site surfaces (studio batch, admin API, runtime resolution)
as adapters over the package storage. Writers are dual: the site
`OperationalSetting` row stays the optimistic-revision and audit authority and
is written first with its existing CAS semantics; the package `Setting` row is
upserted in the same transaction (`core.settings_batch.upsert_package_setting`),
so the two storages commit together or not at all.

| Concern | Cutover owner | Where |
|---|---|---|
| Declaration of the 31 keys | site registry mirrors each registration into the package registry | `core.configuration._declare_in_package` |
| Value-type vocabulary | `string/integer/boolean/string_list/json_object` maps onto `str/int/bool/list/json` | `core.configuration.PACKAGE_VALUE_TYPES` via `package_value_type()` |
| Validation vocabulary | stays adapter-owned until GAP-1 (#358) lands | `core.operational_settings` validators run after package coercion |
| Reads (batch/public) | package row first, site row fallback; value+source from the winning row, revision always from the site row | `core.settings_batch.query_settings` |
| Reads (runtime cache) | package row first, site row fallback, per-resolution | `core.runtime_config._resolve_uncached` |
| Cache stamp | unchanged: `OperationalSetting` aggregates still drive the stamp because site rows keep being written | `core.runtime_config` |
| Grouped batch, revision, If-Match, idempotency | unchanged site code paths over dual writes | `core.settings_batch` |
| Historical copy | data migration copies key/value/type/source, skips existing keys, single transaction, noop reverse | `core/migrations/0003_copy_operational_settings_to_cb_config.py` |
| Audit/history | site `AuditEvent` rows unchanged; package `SettingChange` accrues for package-side writes | unchanged |

Query count during cutover: batch and public reads issue one bounded query per
storage (`assertNumQueries(2)` in `core/tests/test_site_settings.py`). The
single-query contract returns in D0.1d when the site-table read is retired.

## Copy migration rehearsal (synthetic development copy, 2026-09-08)

Ran on a fresh SQLite database built from the checkout's own migrations with
synthetic values only (`REHEARSAL` markers, no production data):

- Seeded five `OperationalSetting` rows (string/integer/boolean, one malformed
  integer value) plus two package rows preseeded for keys the site also holds.
- Forward copy: exactly five rows in each storage; values and source badges
  preserved; types mapped onto the package vocabulary; preseeded package rows
  not clobbered; the malformed value copied verbatim (reads fail closed).
- Reverse then reapply: reverse retains the copied rows (noop by design, the
  drop is D0.1d's), the second copy adds zero rows.
- Divergence: a studio batch write of two announcement keys leaves site and
  package rows with identical values and sources; the revision lives only on
  the site row.

## Rollback runbook (settings cutover)

1. Revert the deploy to the previous release tag; migrations run noop-reverse
   for `core.0003_copy_operational_settings_to_cb_config` and leave both
   storages in place.
2. Readers fall back to site rows automatically while running cutover code.
   On the reverted (pre-cutover) code, readers use site rows only; the site
   rows were kept current by dual writes, so values, sources and revisions are
   continuous.
3. To undo a single bad value written after cutover, write through the studio
   batch again (both storages update together). Do not edit one storage only:
   single-storage edits create divergence the readers cannot arbitrate.
4. Drop of `core_operationalsetting` and its history happens in D0.1d only
   after the rollback window is explicitly closed; until then every rollback
   path relies on the site table remaining populated.

## D0.1c implementation mapping (cutover)

Cutover keeps the site surfaces (studio batch, admin API, runtime resolution)
as adapters over the package storage. Writers are dual: the site
`OperationalSetting` row stays the optimistic-revision and audit authority and
is written first with its existing CAS semantics; the package `Setting` row is
upserted in the same transaction (`core.settings_batch.upsert_package_setting`),
so the two storages commit together or not at all.

| Concern | Cutover owner | Where |
|---|---|---|
| Declaration of the 31 keys | site registry mirrors each registration into the package registry | `core.configuration._declare_in_package` |
| Value-type vocabulary | `string/integer/boolean/string_list/json_object` maps onto `str/int/bool/list/json` | `core.configuration.PACKAGE_VALUE_TYPES` via `package_value_type()` |
| Validation vocabulary | stays adapter-owned until GAP-1 (#358) lands | `core.operational_settings` validators run after package coercion |
| Reads (batch/public) | package row first, site row fallback; value+source from the winning row, revision always from the site row | `core.settings_batch.query_settings` |
| Reads (runtime cache) | package row first, site row fallback, per-resolution | `core.runtime_config._resolve_uncached` |
| Cache stamp | unchanged: `OperationalSetting` aggregates still drive the stamp because site rows keep being written | `core.runtime_config` |
| Grouped batch, revision, If-Match, idempotency | unchanged site code paths over dual writes | `core.settings_batch` |
| Historical copy | data migration copies key/value/type/source, skips existing keys, single transaction, noop reverse | `core/migrations/0003_copy_operational_settings_to_cb_config.py` |
| Audit/history | site `AuditEvent` rows unchanged; package `SettingChange` accrues for package-side writes | unchanged |

Query count during cutover: batch and public reads issue one bounded query per
storage (`assertNumQueries(2)` in `core/tests/test_site_settings.py`). The
single-query contract returns in D0.1d when the site-table read is retired.

## Copy migration rehearsal (synthetic development copy, 2026-09-08)

Ran on a fresh SQLite database built from the checkout's own migrations with
synthetic values only (`REHEARSAL` markers, no production data):

- Seeded five `OperationalSetting` rows (string/integer/boolean, one malformed
  integer value) plus two package rows preseeded for keys the site also holds.
- Forward copy: exactly five rows in each storage; values and source badges
  preserved; types mapped onto the package vocabulary; preseeded package rows
  not clobbered; the malformed value copied verbatim (reads fail closed).
- Reverse then reapply: reverse retains the copied rows (noop by design, the
  drop is D0.1d's), the second copy adds zero rows.
- Divergence: a studio batch write of two announcement keys leaves site and
  package rows with identical values and sources; the revision lives only on
  the site row.

## Rollback runbook (settings cutover)

1. Revert the deploy to the previous release tag; migrations run noop-reverse
   for `core.0003_copy_operational_settings_to_cb_config` and leave both
   storages in place.
2. Readers fall back to site rows automatically while running cutover code.
   On the reverted (pre-cutover) code, readers use site rows only; the site
   rows were kept current by dual writes, so values, sources and revisions are
   continuous.
3. To undo a single bad value written after cutover, write through the studio
   batch again (both storages update together). Do not edit one storage only:
   single-storage edits create divergence the readers cannot arbitrate.
4. Drop of `core_operationalsetting` and its history happens in D0.1d only
   after the rollback window is explicitly closed; until then every rollback
   path relies on the site table remaining populated.
