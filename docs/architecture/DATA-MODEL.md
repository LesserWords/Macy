# Data Model — Content Intelligence Engine

**Status:** Implemented (M0–M7 migrations; PostgreSQL 18 runtime) · **Date:** 2026-11-03 (updated 2026-11-17) · **Database:** PostgreSQL 19 (18 until 19 GA) · **Parent:** [MVP-PRD.md](../prd/MVP-PRD.md) §8

## 1. Design principles

| # | Principle | How it shows up in the schema |
|---|---|---|
| P1 | **Tenant isolation by construction** | Every customer table has `workspace_id NOT NULL`. Each table has `UNIQUE (workspace_id, id)` and child tables reference **composite foreign keys** `(workspace_id, parent_id)`, so a row can never point at another workspace's row, even through an application bug. Row-level security can be added later without schema change |
| P2 | **Time-ordered IDs** | `uuid` primary keys from native `uuidv7()` (PG 18+). Sortable by creation time, index-friendly, safe to expose in URLs and webhooks |
| P3 | **Typed columns for what we query, JSONB for contract payloads** | Status, scores, locale, FKs and money are real columns with constraints. The full Pydantic contract lives in `payload jsonb` with a `schema_version`, so contracts can evolve without migrations |
| P4 | **Explicit state machines** | `status` is `text` + `CHECK (status IN (...))`. Every transition is a guarded `UPDATE … WHERE id = $1 AND status = $expected`: 0 rows means a conflict, never a silent overwrite |
| P5 | **Immutable history** | Briefs and drafts are never edited; a change creates a new version or revision. `events` is append-only (the application role has no UPDATE/DELETE on it) and serves as both the audit log and the webhook outbox |
| P6 | **Idempotency in the database** | Natural unique keys stop duplicates on retry: canonical URL per workspace, one active run per campaign, one publication per (draft, destination), one delivery per (subscription, event) |
| P7 | **Money is exact** | `numeric(12,6)` USD, never float. Every cost lands in `usage_ledger`; run and monthly totals are sums of it |
| P8 | **Retention by design** | Large or sensitive blobs live in separate tables or columns that can be purged without touching facts (LLM request/response bodies, extracted article text) |

## 2. Entity overview

```
 workspaces ─┬─< memberships >── users
             ├─< brand_profiles (versioned, one current)
             ├─< workspace_integrations (1 per port, secrets encrypted)
             ├─< webhook_subscriptions ─< webhook_deliveries >─ events
             ├─< sources (+ shared pack sources with workspace_id NULL)
             ├─< site_audits ─< audit_pages
             ├─< gsc_url_inspection_cache
             ├─< url_monitors
             ├─< keyword_rankings
             └─< campaigns ─< runs ─< research_records >─ documents ─< draft_claims
                                  │         │                              │
                                  │         └─< opportunities ─< briefs ─< drafts ─┬─ quality_reports (1:1)
                                  │                                                └─< publications
                                  └─< usage_ledger ─ llm_payloads (1:1, purgeable)
 keyword_cache (global, no customer data)
```

## 3. DDL

Conventions: `created_at timestamptz NOT NULL DEFAULT now()`. All timestamps are UTC `timestamptz`; schedules are interpreted in `workspaces.timezone`. The `-- …` lines are elided columns described in prose.

### 3.1 Tenancy & access

```sql
CREATE EXTENSION IF NOT EXISTS pg_trgm;

CREATE TABLE workspaces (
  id            uuid PRIMARY KEY DEFAULT uuidv7(),
  slug          text NOT NULL UNIQUE CHECK (slug ~ '^[a-z0-9-]{3,40}$'),
  name          text NOT NULL,
  industry_pack text NOT NULL,                       -- e.g. 'general' (git pack id)
  timezone      text NOT NULL DEFAULT 'America/Sao_Paulo',
  config        jsonb NOT NULL DEFAULT '{}'::jsonb,  -- M7-01 capability switchboard & settings
  created_at    timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE users (
  id            uuid PRIMARY KEY DEFAULT uuidv7(),
  email         text NOT NULL,
  name          text,
  ui_locale     text NOT NULL DEFAULT 'pt-BR' CHECK (ui_locale IN ('pt-BR','en-US')),  -- admin UI language
  password_hash text,                                -- M7-02 PBKDF2-HMAC-SHA256 hash for local auth
  created_at    timestamptz NOT NULL DEFAULT now(),
  last_login_at timestamptz
);
CREATE UNIQUE INDEX users_email_uq ON users (lower(email));

CREATE TABLE memberships (
  workspace_id uuid NOT NULL REFERENCES workspaces(id),
  user_id      uuid NOT NULL REFERENCES users(id),
  role         text NOT NULL DEFAULT 'owner' CHECK (role IN ('owner')),  -- MVP: owner only
  created_at   timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (workspace_id, user_id)
);
```

### 3.2 Customer configuration

```sql
CREATE TABLE brand_profiles (
  workspace_id   uuid NOT NULL REFERENCES workspaces(id),
  id             uuid NOT NULL DEFAULT uuidv7(),
  version        int  NOT NULL CHECK (version > 0),
  is_current     boolean NOT NULL DEFAULT false,
  schema_version smallint NOT NULL,
  payload        jsonb NOT NULL CHECK (jsonb_typeof(payload) = 'object'),  -- BrandProfile
  created_by     uuid NOT NULL REFERENCES users(id),
  created_at     timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (id), UNIQUE (workspace_id, id), UNIQUE (workspace_id, version)
);
CREATE UNIQUE INDEX brand_one_current ON brand_profiles (workspace_id) WHERE is_current;

CREATE TABLE campaigns (
  workspace_id      uuid NOT NULL REFERENCES workspaces(id),
  id                uuid NOT NULL DEFAULT uuidv7(),
  name              text NOT NULL,
  locale            text NOT NULL CHECK (locale ~ '^[a-z]{2}-[A-Z]{2}$'),
  market            text NOT NULL CHECK (market ~ '^[A-Z]{2}$'),
  articles_per_week smallint NOT NULL CHECK (articles_per_week BETWEEN 1 AND 20),
  schedule_cron     text NOT NULL,                      -- validated in code (croniter)
  monthly_cap_usd   numeric(10,2) NOT NULL CHECK (monthly_cap_usd > 0),
  run_cap_usd       numeric(10,2) NOT NULL CHECK (run_cap_usd > 0 AND run_cap_usd <= monthly_cap_usd),
  config            jsonb NOT NULL,                     -- CampaignConfig: goal, weights, CTA limits
  config_schema_version smallint NOT NULL,
  status            text NOT NULL DEFAULT 'active' CHECK (status IN ('active','paused','archived')),
  created_by        uuid NOT NULL REFERENCES users(id),  -- = approver in MVP
  created_at        timestamptz NOT NULL DEFAULT now(),
  updated_by        uuid REFERENCES users(id),
  updated_at        timestamptz,
  PRIMARY KEY (id), UNIQUE (workspace_id, id), UNIQUE (workspace_id, name)
);

CREATE TABLE sources (
  id             uuid PRIMARY KEY DEFAULT uuidv7(),
  workspace_id   uuid REFERENCES workspaces(id),       -- NULL = shared industry-pack source
  industry_pack  text NOT NULL,
  name           text NOT NULL,
  domain         text NOT NULL CHECK (domain = lower(domain)),
  url            text NOT NULL,
  feed_url       text,
  fetch_mode     text NOT NULL CHECK (fetch_mode IN ('feed','site_search')),
  tier           smallint NOT NULL CHECK (tier BETWEEN 1 AND 3),
  role           text NOT NULL CHECK (role IN ('news','company','data','management')),
  locales        text[] NOT NULL CHECK (cardinality(locales) > 0),
  topics         text[] NOT NULL DEFAULT '{}',
  enabled        boolean NOT NULL DEFAULT true,
  last_fetch_ok  boolean,
  last_fetched_at timestamptz,
  added_by       uuid REFERENCES users(id),
  created_at     timestamptz NOT NULL DEFAULT now(),
  CHECK (fetch_mode <> 'feed' OR feed_url IS NOT NULL),
  UNIQUE NULLS NOT DISTINCT (workspace_id, domain)
);

CREATE TABLE workspace_integrations (
  workspace_id   uuid NOT NULL REFERENCES workspaces(id),
  port           text NOT NULL CHECK (port IN ('llm','feed_source','news_search','extractor',
                                               'keyword_provider','publisher','analytics_source')),
  plugin         text NOT NULL,                        -- entry-point name, validated against registry
  settings       jsonb NOT NULL DEFAULT '{}',          -- non-secret fields of plugin Settings
  secrets        bytea,                                -- Fernet token of the SecretStr fields (JSON)
  key_version    smallint NOT NULL DEFAULT 1,          -- MultiFernet rotation
  last_test_ok   boolean,
  last_tested_at timestamptz,
  updated_by     uuid NOT NULL REFERENCES users(id),
  updated_at     timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (workspace_id, port)
);

CREATE TABLE webhook_subscriptions (
  workspace_id uuid NOT NULL REFERENCES workspaces(id),
  id           uuid NOT NULL DEFAULT uuidv7(),
  url          text NOT NULL CHECK (url ~ '^https://'),   -- http allowed only via dev override
  event_types  text[] NOT NULL CHECK (cardinality(event_types) > 0),
  secret       bytea NOT NULL,                            -- encrypted Standard Webhooks secret
  enabled      boolean NOT NULL DEFAULT true,
  created_by   uuid NOT NULL REFERENCES users(id),
  created_at   timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (id), UNIQUE (workspace_id, id)
);
```

### 3.3 Pipeline

```sql
CREATE TABLE runs (
  workspace_id  uuid NOT NULL,
  id            uuid NOT NULL DEFAULT uuidv7(),
  campaign_id   uuid NOT NULL,
  trigger       text NOT NULL CHECK (trigger IN ('scheduled','manual','e2e')),
  status        text NOT NULL DEFAULT 'queued'
                CHECK (status IN ('queued','running','blocked','completed','failed','cancelled')),
  stage         text CHECK (stage IN ('research','score','report','keywords','brief','write','evaluate','publish')),
  window_start  timestamptz NOT NULL,
  window_end    timestamptz NOT NULL CHECK (window_end > window_start),
  run_cap_usd   numeric(10,2) NOT NULL,                -- snapshot of campaign cap at start
  cost_usd      numeric(12,6) NOT NULL DEFAULT 0,      -- maintained from usage_ledger
  code_version  text NOT NULL,                         -- git sha of image
  pack_version  text NOT NULL,                         -- git sha / hash of packs + prompts
  blocked_reason text,                                 -- e.g. 'integration_missing:keyword_provider', 'cap_reached'
  error         jsonb,
  created_by    uuid REFERENCES users(id),             -- NULL for scheduled
  started_at    timestamptz, finished_at timestamptz,
  created_at    timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (id), UNIQUE (workspace_id, id),
  FOREIGN KEY (workspace_id, campaign_id) REFERENCES campaigns (workspace_id, id)
);
CREATE UNIQUE INDEX runs_one_active ON runs (campaign_id) WHERE status IN ('queued','running');

CREATE TABLE documents (                                -- one fetched web page / PDF = evidence
  workspace_id   uuid NOT NULL,
  id             uuid NOT NULL DEFAULT uuidv7(),
  source_id      uuid REFERENCES sources(id),           -- NULL when found by open search
  url            text NOT NULL,
  canonical_url  text NOT NULL,
  content_hash   bytea,                                 -- sha256 of extracted text; change detection
  title          text NOT NULL,
  lang           text,                                  -- detected (lingua), BCP-47 primary tag
  published_at   timestamptz,
  date_status    text NOT NULL CHECK (date_status IN ('verified','inferred','unverified')),
  fetch_status   text NOT NULL CHECK (fetch_status IN ('ok','http_error','blocked','extraction_failed','paywalled')),
  http_status    smallint,
  extracted_text text,                                  -- purgeable (retention, §5)
  text_purged_at timestamptz,
  meta           jsonb NOT NULL DEFAULT '{}',           -- author, site name, outbound links
  fetched_at     timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (id), UNIQUE (workspace_id, id), UNIQUE (workspace_id, canonical_url),
  FOREIGN KEY (workspace_id) REFERENCES workspaces(id)
);
CREATE INDEX documents_title_trgm ON documents USING gin (title gin_trgm_ops);
CREATE INDEX documents_published ON documents (workspace_id, published_at DESC);

CREATE TABLE research_records (                         -- a document as seen by one run (ResearchRecord)
  workspace_id   uuid NOT NULL,
  id             uuid NOT NULL DEFAULT uuidv7(),
  run_id         uuid NOT NULL,
  document_id    uuid NOT NULL,
  duplicate_of   uuid,                                  -- earlier record of the same story
  status         text NOT NULL DEFAULT 'new'
                 CHECK (status IN ('new','extracted','verified','stale','duplicate','failed')),
  schema_version smallint NOT NULL,
  payload        jsonb NOT NULL,                        -- facts[], entities[], summary, source_type
  created_at     timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (id), UNIQUE (workspace_id, id), UNIQUE (run_id, document_id),
  FOREIGN KEY (workspace_id, run_id)       REFERENCES runs (workspace_id, id),
  FOREIGN KEY (workspace_id, document_id)  REFERENCES documents (workspace_id, id),
  FOREIGN KEY (workspace_id, duplicate_of) REFERENCES research_records (workspace_id, id),
  CHECK ((status = 'duplicate') = (duplicate_of IS NOT NULL))
);
CREATE INDEX rr_queue ON research_records (run_id, status) WHERE status IN ('new','extracted');

CREATE TABLE opportunities (
  workspace_id   uuid NOT NULL,
  id             uuid NOT NULL DEFAULT uuidv7(),
  run_id         uuid NOT NULL,
  record_id      uuid NOT NULL,
  scores         jsonb NOT NULL,                        -- per criterion: score, evidence, is_estimate
  total          numeric(5,2) NOT NULL CHECK (total BETWEEN 0 AND 100),
  rank           smallint,                              -- position in this run's report
  funnel_stage   text NOT NULL CHECK (funnel_stage IN ('tofu','mofu','bofu')),
  confidence     text NOT NULL CHECK (confidence IN ('low','medium','high')),
  angle          text NOT NULL,
  status         text NOT NULL DEFAULT 'scored'
                 CHECK (status IN ('scored','approved','rejected','expired')),
  decided_by     uuid REFERENCES users(id),
  decided_at     timestamptz,
  decision_note  text,
  PRIMARY KEY (id), UNIQUE (workspace_id, id), UNIQUE (run_id, record_id),
  FOREIGN KEY (workspace_id, run_id)    REFERENCES runs (workspace_id, id),
  FOREIGN KEY (workspace_id, record_id) REFERENCES research_records (workspace_id, id),
  CHECK ((status IN ('approved','rejected')) = (decided_by IS NOT NULL AND decided_at IS NOT NULL))
);
CREATE INDEX opp_report ON opportunities (run_id, total DESC);

CREATE TABLE briefs (
  workspace_id     uuid NOT NULL,
  id               uuid NOT NULL DEFAULT uuidv7(),
  opportunity_id   uuid NOT NULL,
  version          smallint NOT NULL CHECK (version > 0),   -- new version on REVISION→brief
  brand_profile_id uuid NOT NULL,                           -- exact brand version used
  prompt_version   text NOT NULL,
  keywords         jsonb NOT NULL,                          -- KeywordData snapshot, is_estimate flags
  schema_version   smallint NOT NULL,
  payload          jsonb NOT NULL,                          -- EditorialBrief
  created_at       timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (id), UNIQUE (workspace_id, id), UNIQUE (opportunity_id, version),
  FOREIGN KEY (workspace_id, opportunity_id)   REFERENCES opportunities (workspace_id, id),
  FOREIGN KEY (workspace_id, brand_profile_id) REFERENCES brand_profiles (workspace_id, id)
);

CREATE TABLE drafts (
  workspace_id   uuid NOT NULL,
  id             uuid NOT NULL DEFAULT uuidv7(),
  brief_id       uuid NOT NULL,
  revision       smallint NOT NULL CHECK (revision BETWEEN 0 AND 2),
  status         text NOT NULL DEFAULT 'draft'
                 CHECK (status IN ('draft','evaluating','pass','revision','reject','approved','declined','published')),
  prompt_version text NOT NULL,
  schema_version smallint NOT NULL,
  payload        jsonb NOT NULL,                        -- ContentDraft (title, meta, slug, body_md, faq, cta)
  decided_by     uuid REFERENCES users(id),
  decided_at     timestamptz,
  created_at     timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (id), UNIQUE (workspace_id, id), UNIQUE (brief_id, revision),
  FOREIGN KEY (workspace_id, brief_id) REFERENCES briefs (workspace_id, id)
);

CREATE TABLE draft_claims (                             -- provenance made queryable and enforced
  workspace_id uuid NOT NULL,
  draft_id     uuid NOT NULL,
  idx          smallint NOT NULL,
  kind         text NOT NULL CHECK (kind IN ('fact','analysis','recommendation')),
  text         text NOT NULL,
  document_id  uuid,
  PRIMARY KEY (draft_id, idx),
  FOREIGN KEY (workspace_id, draft_id)    REFERENCES drafts (workspace_id, id),
  FOREIGN KEY (workspace_id, document_id) REFERENCES documents (workspace_id, id),
  CHECK (kind <> 'fact' OR document_id IS NOT NULL)      -- a fact without a source cannot be stored
);

CREATE TABLE quality_reports (
  workspace_id    uuid NOT NULL,
  draft_id        uuid NOT NULL PRIMARY KEY,            -- 1:1 with draft revision
  verdict         text NOT NULL CHECK (verdict IN ('pass','revision','reject')),
  checks          jsonb NOT NULL,                       -- deterministic check results
  findings        jsonb NOT NULL,                       -- criterion, severity, detail, responsible_module
  evaluator_model text NOT NULL,
  prompt_version  text NOT NULL,
  created_at      timestamptz NOT NULL DEFAULT now(),
  FOREIGN KEY (workspace_id, draft_id) REFERENCES drafts (workspace_id, id)
);

CREATE TABLE publications (
  workspace_id uuid NOT NULL,
  id           uuid NOT NULL DEFAULT uuidv7(),
  draft_id     uuid NOT NULL,
  destination  text NOT NULL,                           -- plugin name + target, e.g. 'http_webhook'
  mode         text NOT NULL CHECK (mode IN ('draft','scheduled','live')),
  status       text NOT NULL CHECK (status IN ('pending','published','failed')),
  external_id  text,
  url          text,
  scheduled_at timestamptz,
  published_at timestamptz,
  response     jsonb,                                   -- PublishedContent.raw
  created_at   timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (id), UNIQUE (workspace_id, id),
  UNIQUE (workspace_id, draft_id, destination),         -- idempotent re-publish
  FOREIGN KEY (workspace_id, draft_id) REFERENCES drafts (workspace_id, id)
);

CREATE TABLE keyword_cache (                            -- global: market data, no customer data
  provider     text NOT NULL,
  locale       text NOT NULL,
  market       text NOT NULL,
  keyword_norm text NOT NULL,                           -- lower, unaccented, trimmed
  data         jsonb NOT NULL,                          -- KeywordData
  fetched_at   timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (provider, locale, market, keyword_norm)
);
```

### 3.4 Events, webhooks, usage

```sql
CREATE TABLE events (                                   -- append-only: audit log + outbox
  workspace_id  uuid NOT NULL REFERENCES workspaces(id),
  id            uuid PRIMARY KEY DEFAULT uuidv7(),      -- = webhook-id header
  type          text NOT NULL,                          -- 'run.completed', 'settings.changed', ...
  actor_user_id uuid REFERENCES users(id),              -- NULL = system
  entity_type   text NOT NULL,
  entity_id     uuid NOT NULL,
  payload       jsonb NOT NULL,                         -- secrets always redacted
  occurred_at   timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX events_ws_time ON events (workspace_id, occurred_at DESC);
CREATE INDEX events_entity  ON events (entity_type, entity_id);
-- REVOKE UPDATE, DELETE ON events FROM app;   (only the retention job role may delete)

CREATE TABLE webhook_deliveries (                       -- created in the SAME transaction as the event
  workspace_id    uuid NOT NULL,
  subscription_id uuid NOT NULL,
  event_id        uuid NOT NULL REFERENCES events(id),
  status          text NOT NULL DEFAULT 'pending'
                  CHECK (status IN ('pending','delivered','retrying','dead')),
  attempts        smallint NOT NULL DEFAULT 0,
  next_attempt_at timestamptz NOT NULL DEFAULT now(),
  last_http_status smallint,
  last_error      text,
  delivered_at    timestamptz,
  PRIMARY KEY (subscription_id, event_id),
  FOREIGN KEY (workspace_id, subscription_id) REFERENCES webhook_subscriptions (workspace_id, id)
);
CREATE INDEX deliveries_due ON webhook_deliveries (next_attempt_at) WHERE status IN ('pending','retrying');

CREATE TABLE usage_ledger (                             -- every billable call: LLM and paid APIs
  workspace_id  uuid NOT NULL REFERENCES workspaces(id),
  id            uuid PRIMARY KEY DEFAULT uuidv7(),
  run_id        uuid,
  stage         text NOT NULL,
  kind          text NOT NULL CHECK (kind IN ('llm','api')),
  vendor        text NOT NULL,                          -- 'openrouter', 'dataforseo', 'brave'
  model         text,                                   -- LLM model id or API endpoint
  generation_id text,                                   -- vendor request id (OpenRouter generation)
  input_tokens  int, output_tokens int, cached_tokens int,
  units         numeric(12,3),                          -- non-LLM: queries, keywords
  cost_usd      numeric(12,6) NOT NULL CHECK (cost_usd >= 0),
  latency_ms    int,
  prompt_version text,
  entity_type   text, entity_id uuid,
  created_at    timestamptz NOT NULL DEFAULT now(),
  FOREIGN KEY (workspace_id, run_id) REFERENCES runs (workspace_id, id)
);
CREATE INDEX usage_month ON usage_ledger (workspace_id, created_at);

CREATE TABLE llm_payloads (                             -- debug bodies, purged after 90 days
  usage_id  uuid PRIMARY KEY REFERENCES usage_ledger(id) ON DELETE CASCADE,
  request   jsonb NOT NULL,
  response  jsonb NOT NULL
);
```

### 3.5 SEO & GEO audits, GSC cache, route monitors & rank tracking (M6-02, M6-03)

```sql
CREATE TABLE site_audits (
  workspace_id  uuid NOT NULL REFERENCES workspaces(id),
  id            uuid NOT NULL DEFAULT uuidv7(),
  start_url     text NOT NULL,
  pages_crawled bigint NOT NULL CHECK (pages_crawled >= 0),
  max_pages     bigint NOT NULL CHECK (max_pages > 0 AND max_pages <= 200),
  overall_score numeric(5,2) NOT NULL CHECK (overall_score >= 0 AND overall_score <= 100),
  grade         text NOT NULL,
  payload       jsonb NOT NULL,
  created_at    timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (id), UNIQUE (workspace_id, id)
);
CREATE INDEX site_audits_ws_created_idx ON site_audits (workspace_id, created_at DESC);

CREATE TABLE audit_pages (
  workspace_id  uuid NOT NULL REFERENCES workspaces(id),
  id            uuid NOT NULL DEFAULT uuidv7(),
  site_audit_id uuid,                                   -- NULL for standalone single-URL audits
  url           text NOT NULL,
  status_code   bigint NOT NULL,
  overall_score numeric(5,2) NOT NULL CHECK (overall_score >= 0 AND overall_score <= 100),
  grade         text NOT NULL,
  payload       jsonb NOT NULL,
  created_at    timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (id), UNIQUE (workspace_id, id),
  FOREIGN KEY (workspace_id, site_audit_id) REFERENCES site_audits (workspace_id, id)
);
CREATE INDEX audit_pages_ws_audit_idx ON audit_pages (workspace_id, site_audit_id, created_at DESC);

CREATE TABLE gsc_url_inspection_cache (                 -- 12h TTL protects GSC 2,000/day quota
  workspace_id  uuid NOT NULL REFERENCES workspaces(id),
  site_url      text NOT NULL,
  inspected_url text NOT NULL,
  payload       jsonb NOT NULL,
  fetched_at    timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (workspace_id, site_url, inspected_url)
);
CREATE INDEX gsc_inspection_ws_fetched_idx ON gsc_url_inspection_cache (workspace_id, fetched_at DESC);

CREATE TABLE url_monitors (                             -- scheduled post-publish route/TLS checks
  workspace_id         uuid NOT NULL REFERENCES workspaces(id),
  id                   uuid NOT NULL DEFAULT uuidv7(),
  url                  text NOT NULL,
  schedule_cron        text NOT NULL DEFAULT '0 9 * * *',
  score_drop_threshold numeric(5,2) NOT NULL DEFAULT 5.00,
  tls_expiry_warn_days bigint NOT NULL DEFAULT 14,
  last_score           numeric(5,2),
  last_checks          jsonb,
  last_tls_days_left   bigint,
  last_status          text NOT NULL DEFAULT 'pending',
  last_checked_at      timestamptz,
  created_at           timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (id), UNIQUE (workspace_id, url)
);
CREATE INDEX url_monitors_ws_checked_idx ON url_monitors (workspace_id, last_checked_at DESC);

CREATE TABLE keyword_rankings (                         -- paid-tier RankTrackerPort history
  workspace_id      uuid NOT NULL REFERENCES workspaces(id),
  id                uuid NOT NULL DEFAULT uuidv7(),
  keyword           text NOT NULL,
  domain            text NOT NULL,
  locale            text NOT NULL DEFAULT 'pt-BR',
  market            text NOT NULL DEFAULT 'BR',
  position          bigint,
  previous_position bigint,
  ranking_url       text,
  provider          text NOT NULL,
  checked_at        timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (id)
);
CREATE INDEX keyword_rankings_ws_kw_idx ON keyword_rankings (workspace_id, domain, keyword, checked_at DESC);
```

`settings_audit`, `events_outbox` and `llm_calls` from earlier PRD drafts are replaced by `events`, `webhook_deliveries` and `usage_ledger` + `llm_payloads`.

## 4. State machines

| Entity | Allowed transitions | Guard |
|---|---|---|
| `runs` | queued→running; running→blocked\|completed\|failed\|cancelled; blocked→running\|cancelled | `runs_one_active` index; the budget guard moves the run to `blocked` with `cap_reached` |
| `research_records` | new→extracted→verified; new\|extracted→failed; extracted→stale\|duplicate | Freshness window per format |
| `opportunities` | scored→approved\|rejected (gate 1); scored→expired (next run) | Approver = `campaigns.created_by` and must be an owner; at most `articles_per_week` approved per run (checked in the same transaction) |
| `drafts` | draft→evaluating→pass\|revision\|reject; pass→approved\|declined (gate 2); approved→published | Revision creates a new row (`revision+1`); a row at revision 2 can't move to `revision` |
| `publications` | pending→published\|failed; failed→pending (retry) | Unique (draft, destination) |
| `webhook_deliveries` | pending→delivered\|retrying; retrying→delivered\|retrying\|dead | Backoff 1 min → 24 h; `dead` after 24 h |

Every transition writes an `events` row in the same transaction.

## 5. Retention, privacy (LGPD) and copyright

| Data | Kept | Then |
|---|---|---|
| `llm_payloads` | 90 days | Deleted (`usage_ledger` metrics stay forever) |
| `documents.extracted_text` | 365 days | Set to NULL, `text_purged_at` stamped; title, URL, dates and hash stay |
| `webhook_deliveries` (delivered/dead) | 30 days | Deleted |
| `events` | 24 months | Deleted by the retention role |
| `keyword_cache` | 30 days fresh | Refetched on next use |
| `gsc_url_inspection_cache` | 12 hours fresh | Refetched on next URL inspection |
| Users | While a member | On deletion request: email and name anonymised; `actor_user_id` kept as an opaque id (LGPD Art. 18) |

`cli.py purge` runs nightly from the worker. Extracted text is internal evidence only. It is never published, and quotes are limited to the copyright rule in PRD §11.

## 6. Operations

- **Migrations:** **dbmate** runs plain-SQL migrations in a one-shot Compose service. The committed `db/schema.sql` dump is diffed in PR review. **squawk** lints every migration in pre-commit and CI (no table-locking `ALTER`, `CREATE INDEX CONCURRENTLY`, etc.). Breaking changes use expand → migrate → contract across two releases.
- **Roles:** `owner` (migrations only), `app` (DML; no UPDATE/DELETE on `events`), `retention` (purge job), `readonly` (reporting).
- **Workers claim work** with `SELECT … FOR UPDATE SKIP LOCKED` on the partial queue indexes (`rr_queue`, `deliveries_due`). Several workers can run safely later without a broker.
- **Contract evolution:** a Pydantic model bump raises `schema_version`. The reader upcasts old payloads in code. JSONB is never migrated in place except by an explicit backfill migration.
- **Tenant-scoping test (CI):** every `db.py` query must mention `workspace_id`. A second seeded workspace is checked to see 0 rows from the first. Composite FKs make cross-tenant references fail at the database.
- **Later, no schema change:** row-level security (`USING (workspace_id = current_setting('app.workspace_id')::uuid)`); pgvector column on `documents` for semantic dedup; sharing `documents` across workspaces by moving them to a global table keyed by `canonical_url` (the `research_records` indirection already isolates per-workspace use).

## 7. Volume estimate (1 workspace → 50)

| Table | Rows/year (1 ws) | Rows/year (50 ws) | Notes |
|---|---|---|---|
| documents | ~10k | ~500k | Largest by bytes (text); purged after a year |
| research_records | ~10k | ~500k | |
| opportunities | ~10k | ~500k | |
| drafts / claims | ~150 / ~4k | ~8k / ~200k | |
| usage_ledger | ~5k | ~250k | |
| events | ~5k | ~250k | |

A single Postgres instance with default settings handles this comfortably. No partitioning is needed before roughly 10M rows in `documents`.
