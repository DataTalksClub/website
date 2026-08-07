# Deferred migration-squash gate

All original migrations from the pinned source commit are retained. No squash or replacement migration is created in issue #30 because no trustworthy anonymized production-like source snapshot was supplied.

Original files may be replaced only through Django-supported migration machinery after all of the following evidence exists:

1. the complete original graph installs on a fresh database;
2. a candidate replacement graph installs independently and has schema, constraints, indexes, app labels, migration state, and data-operation parity with the original graph;
3. an anonymized production-like snapshot upgrades through the candidate path without data loss;
4. per-table counts, stable keys, permissions/content types, representative derived values, and source/import reconciliation have no unexplained differences;
5. supported reverse/forward migration windows pass; and
6. the independent tester and product owner review the parity evidence.

Until then, deleting original migration files would violate the adoption contract. The preserved chains are the supported final graph for this baseline.
