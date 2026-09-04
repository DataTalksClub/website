# Backup verification receipt boundary

`core.backup_verification` owns the provider-neutral schema-1 proof contract for one backup
snapshot. It contains frozen safe DTOs, strict canonical JSON codecs, domain-separated digests,
migration-schema identity construction, the snapshot-provider protocol, verification, and the pure
receipt evaluator. It does not discover or create a backup and a receipt grants no restore,
activation, replay, deployment, workload, RPO/RTO, or production authority.

The dependency direction is fixed:

```text
future provider adapter -> core.backup_verification -> privacy restore fence (#258)
privacy activation receipt -> future operations controller (#66)
```

The core module never imports privacy or another product domain. #258 imports
`BackupVerificationReceipt` and `evaluate_backup_receipt`; it must remove or narrow its provisional
snapshot-provider definition rather than duplicate this protocol. A future provider issue may
implement the callable protocol, but cannot register a provider through settings, environment,
database state, import order, a route, or a command. Real provider access and backup/restore
operations remain owned by infrastructure and #66.

The receipt evaluator reconstructs the canonical schema-1 complete-result projection from the
receipt's copied result fields and verifies the nested `result_digest` before accepting the receipt;
the outer `receipt_digest` alone is not sufficient to authenticate those copied fields.

Migration identity input consists only of expected migration names, ordered dependency names, and
the applied migration-name set. The builder reads no application row, database name, connection
string, SQL, or credential. Provider adapters must reduce a snapshot identity and manifest to the
bounded safe projections accepted by `build_snapshot_identity_digest` and `safe_manifest_digest`
before constructing a result. Raw provider responses, ARNs, URLs, identifiers, exceptions, and
protected values never cross this boundary.

Only explicit factories under `core/tests/backup_verification_fixtures.py` provide synthetic test
results. There is no production provider or activation hook in this delivery.
