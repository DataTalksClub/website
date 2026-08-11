# Portable database boundary

- Local development, focused CI, ordinary full CI, and scheduled full regression use isolated
  SQLite databases with backend-portable Django contracts.
- RDS PostgreSQL is deployed durable storage, validated by exact-image migration,
  database-aware readiness, and deployed smoke.
- Deployed settings fail closed unless the PostgreSQL `DATABASE_URL` is valid.
- The runtime `psycopg` driver dependency provides deployed connectivity.
- Maintained application tests must not require PostgreSQL-only behavior.
- The four-hour application regression is PostgreSQL-free.
- Historical adoption records describe former PostgreSQL fields, indexes, and triggers.
- A bounded legacy PostgreSQL object catalog inventory remains a follow-up before production
  migration; any leave/remove decision belongs to a separate maintenance issue.
- [Current AWS specification](08-aws-development-terraform.md)
