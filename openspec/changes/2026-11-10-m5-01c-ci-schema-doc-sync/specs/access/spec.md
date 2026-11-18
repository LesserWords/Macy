# access Delta Specification

## ADDED Requirements

### Requirement: Committed schema snapshot and data-model documentation parity
The repository SHALL keep the committed `db/schema.sql` snapshot synchronized with a clean `pg_dump --schema-only` across all `db/migrations/*.sql` files (through M4 `anonymise_user_events`) and align `docs/architecture/DATA-MODEL.md` DDL and constraints with the implemented PostgreSQL schema.

#### Scenario: Schema snapshot includes all M0–M4 tables, constraints, and functions
- **WHEN** `db/schema.sql` is inspected against `db/migrations/*.sql`
- **THEN** every table (`workspaces` through `keyword_cache`, `briefs`, `drafts`, `draft_claims`, `quality_reports`, M4 `publications` columns/constraints), role grant, and `anonymise_user_events(uuid)` exists cleanly in `db/schema.sql`

#### Scenario: DATA-MODEL documentation matches actual migration DDL
- **WHEN** `docs/architecture/DATA-MODEL.md` is compared to applied migrations
- **THEN** column types (`bigint` counts/versions/ranks/revisions), `webhook_subscriptions.url` regex (`^https?://`), `opportunities.created_at`, `drafts.decision_note`, `draft_claims`/`quality_reports` cascades/defaults, `publications` nullable fields & `error`, and `anonymise_user_events` match accurately
