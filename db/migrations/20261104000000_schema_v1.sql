-- migrate:up
SET lock_timeout = '2s';
SET statement_timeout = '10s';

CREATE EXTENSION IF NOT EXISTS pg_trgm;

CREATE TABLE IF NOT EXISTS workspaces (
  id            uuid PRIMARY KEY DEFAULT uuidv7(),
  slug          text NOT NULL UNIQUE CHECK (slug ~ '^[a-z0-9-]{3,40}$'),
  name          text NOT NULL,
  industry_pack text NOT NULL,
  timezone      text NOT NULL DEFAULT 'America/Sao_Paulo',
  created_at    timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS users (
  id            uuid PRIMARY KEY DEFAULT uuidv7(),
  email         text NOT NULL,
  name          text,
  ui_locale     text NOT NULL DEFAULT 'pt-BR' CHECK (ui_locale IN ('pt-BR','en-US')),
  created_at    timestamptz NOT NULL DEFAULT now(),
  last_login_at timestamptz
);
-- squawk-ignore require-concurrent-index-creation
CREATE UNIQUE INDEX IF NOT EXISTS users_email_uq ON users (lower(email));

CREATE TABLE IF NOT EXISTS memberships (
  workspace_id uuid NOT NULL REFERENCES workspaces(id),
  user_id      uuid NOT NULL REFERENCES users(id),
  role         text NOT NULL DEFAULT 'owner' CHECK (role IN ('owner')),
  created_at   timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (workspace_id, user_id)
);

CREATE TABLE IF NOT EXISTS brand_profiles (
  workspace_id   uuid NOT NULL REFERENCES workspaces(id),
  id             uuid NOT NULL DEFAULT uuidv7(),
  version        bigint NOT NULL CHECK (version > 0),
  is_current     boolean NOT NULL DEFAULT false,
  schema_version bigint NOT NULL,
  payload        jsonb NOT NULL CHECK (jsonb_typeof(payload) = 'object'),
  created_by     uuid NOT NULL REFERENCES users(id),
  created_at     timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (id),
  UNIQUE (workspace_id, id),
  UNIQUE (workspace_id, version)
);
-- squawk-ignore require-concurrent-index-creation
CREATE UNIQUE INDEX IF NOT EXISTS brand_one_current ON brand_profiles (workspace_id) WHERE is_current;

CREATE TABLE IF NOT EXISTS campaigns (
  workspace_id      uuid NOT NULL REFERENCES workspaces(id),
  id                uuid NOT NULL DEFAULT uuidv7(),
  name              text NOT NULL,
  locale            text NOT NULL CHECK (locale ~ '^[a-z]{2}-[A-Z]{2}$'),
  market            text NOT NULL CHECK (market ~ '^[A-Z]{2}$'),
  articles_per_week bigint NOT NULL CHECK (articles_per_week BETWEEN 1 AND 20),
  schedule_cron     text NOT NULL,
  monthly_cap_usd   numeric(10,2) NOT NULL CHECK (monthly_cap_usd > 0),
  run_cap_usd       numeric(10,2) NOT NULL CHECK (run_cap_usd > 0 AND run_cap_usd <= monthly_cap_usd),
  config            jsonb NOT NULL,
  config_schema_version bigint NOT NULL,
  status            text NOT NULL DEFAULT 'active' CHECK (status IN ('active','paused','archived')),
  created_by        uuid NOT NULL REFERENCES users(id),
  created_at        timestamptz NOT NULL DEFAULT now(),
  updated_by        uuid REFERENCES users(id),
  updated_at        timestamptz,
  PRIMARY KEY (id),
  UNIQUE (workspace_id, id),
  UNIQUE (workspace_id, name)
);

CREATE TABLE IF NOT EXISTS sources (
  id              uuid PRIMARY KEY DEFAULT uuidv7(),
  workspace_id    uuid REFERENCES workspaces(id),
  industry_pack   text NOT NULL,
  name            text NOT NULL,
  domain          text NOT NULL CHECK (domain = lower(domain)),
  url             text NOT NULL,
  feed_url        text,
  fetch_mode      text NOT NULL CHECK (fetch_mode IN ('feed','site_search')),
  tier            bigint NOT NULL CHECK (tier BETWEEN 1 AND 3),
  role            text NOT NULL CHECK (role IN ('news','company','data','management')),
  locales         text[] NOT NULL CHECK (cardinality(locales) > 0),
  topics          text[] NOT NULL DEFAULT '{}',
  enabled         boolean NOT NULL DEFAULT true,
  last_fetch_ok   boolean,
  last_fetched_at timestamptz,
  added_by        uuid REFERENCES users(id),
  created_at      timestamptz NOT NULL DEFAULT now(),
  CHECK (fetch_mode <> 'feed' OR feed_url IS NOT NULL),
  UNIQUE NULLS NOT DISTINCT (workspace_id, domain)
);

CREATE TABLE IF NOT EXISTS workspace_integrations (
  workspace_id   uuid NOT NULL REFERENCES workspaces(id),
  port           text NOT NULL CHECK (port IN ('llm','feed_source','news_search','extractor',
                                               'keyword_provider','publisher','analytics_source')),
  plugin         text NOT NULL,
  settings       jsonb NOT NULL DEFAULT '{}',
  secrets        bytea,
  key_version    bigint NOT NULL DEFAULT 1,
  last_test_ok   boolean,
  last_tested_at timestamptz,
  updated_by     uuid NOT NULL REFERENCES users(id),
  updated_at     timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (workspace_id, port)
);

CREATE TABLE IF NOT EXISTS webhook_subscriptions (
  workspace_id uuid NOT NULL REFERENCES workspaces(id),
  id           uuid NOT NULL DEFAULT uuidv7(),
  url          text NOT NULL CHECK (url ~ '^https?://'),
  event_types  text[] NOT NULL CHECK (cardinality(event_types) > 0),
  secret       bytea NOT NULL,
  enabled      boolean NOT NULL DEFAULT true,
  created_by   uuid NOT NULL REFERENCES users(id),
  created_at   timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (id),
  UNIQUE (workspace_id, id)
);

CREATE TABLE IF NOT EXISTS events (
  workspace_id  uuid NOT NULL REFERENCES workspaces(id),
  id            uuid PRIMARY KEY DEFAULT uuidv7(),
  type          text NOT NULL,
  actor_user_id uuid REFERENCES users(id),
  entity_type   text NOT NULL,
  entity_id     uuid NOT NULL,
  payload       jsonb NOT NULL,
  occurred_at   timestamptz NOT NULL DEFAULT now()
);
-- squawk-ignore require-concurrent-index-creation
CREATE INDEX IF NOT EXISTS events_ws_time ON events (workspace_id, occurred_at DESC);
-- squawk-ignore require-concurrent-index-creation
CREATE INDEX IF NOT EXISTS events_entity  ON events (entity_type, entity_id);

CREATE TABLE IF NOT EXISTS webhook_deliveries (
  workspace_id     uuid NOT NULL,
  subscription_id  uuid NOT NULL,
  event_id         uuid NOT NULL REFERENCES events(id),
  status           text NOT NULL DEFAULT 'pending'
                   CHECK (status IN ('pending','delivered','retrying','dead')),
  attempts         bigint NOT NULL DEFAULT 0,
  next_attempt_at  timestamptz NOT NULL DEFAULT now(),
  last_http_status bigint,
  last_error       text,
  delivered_at     timestamptz,
  PRIMARY KEY (subscription_id, event_id),
  FOREIGN KEY (workspace_id, subscription_id) REFERENCES webhook_subscriptions (workspace_id, id)
);
-- squawk-ignore require-concurrent-index-creation
CREATE INDEX IF NOT EXISTS deliveries_due ON webhook_deliveries (next_attempt_at) WHERE status IN ('pending','retrying');

CREATE TABLE IF NOT EXISTS runs (
  workspace_id   uuid NOT NULL,
  id             uuid NOT NULL DEFAULT uuidv7(),
  campaign_id    uuid NOT NULL,
  trigger        text NOT NULL CHECK (trigger IN ('scheduled','manual','e2e')),
  status         text NOT NULL DEFAULT 'queued'
                 CHECK (status IN ('queued','running','blocked','completed','failed','cancelled')),
  stage          text CHECK (stage IN ('research','score','report','keywords','brief','write','evaluate','publish')),
  window_start   timestamptz NOT NULL,
  window_end     timestamptz NOT NULL CHECK (window_end > window_start),
  run_cap_usd    numeric(10,2) NOT NULL,
  cost_usd       numeric(12,6) NOT NULL DEFAULT 0,
  code_version   text NOT NULL,
  pack_version   text NOT NULL,
  blocked_reason text,
  error          jsonb,
  created_by     uuid REFERENCES users(id),
  started_at     timestamptz,
  finished_at    timestamptz,
  created_at     timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (id),
  UNIQUE (workspace_id, id),
  FOREIGN KEY (workspace_id, campaign_id) REFERENCES campaigns (workspace_id, id)
);
-- squawk-ignore require-concurrent-index-creation
CREATE UNIQUE INDEX IF NOT EXISTS runs_one_active ON runs (campaign_id) WHERE status IN ('queued','running');

CREATE TABLE IF NOT EXISTS usage_ledger (
  workspace_id   uuid NOT NULL REFERENCES workspaces(id),
  id             uuid PRIMARY KEY DEFAULT uuidv7(),
  run_id         uuid,
  stage          text NOT NULL,
  kind           text NOT NULL CHECK (kind IN ('llm','api')),
  vendor         text NOT NULL,
  model          text,
  generation_id  text,
  input_tokens   bigint,
  output_tokens  bigint,
  cached_tokens  bigint,
  units          numeric(12,3),
  cost_usd       numeric(12,6) NOT NULL CHECK (cost_usd >= 0),
  latency_ms     bigint,
  prompt_version text,
  entity_type    text,
  entity_id      uuid,
  created_at     timestamptz NOT NULL DEFAULT now(),
  FOREIGN KEY (workspace_id, run_id) REFERENCES runs (workspace_id, id)
);
-- squawk-ignore require-concurrent-index-creation
CREATE INDEX IF NOT EXISTS usage_month ON usage_ledger (workspace_id, created_at);

CREATE TABLE IF NOT EXISTS llm_payloads (
  usage_id  uuid PRIMARY KEY REFERENCES usage_ledger(id) ON DELETE CASCADE,
  request   jsonb NOT NULL,
  response  jsonb NOT NULL
);

-- Database roles and permissions
DO $$
BEGIN
  IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'app') THEN
    CREATE ROLE app WITH LOGIN PASSWORD 'app_secret' NOSUPERUSER NOCREATEDB NOCREATEROLE;
  ELSE
    ALTER ROLE app WITH LOGIN PASSWORD 'app_secret' NOSUPERUSER NOCREATEDB NOCREATEROLE;
  END IF;
  IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'retention') THEN
    CREATE ROLE retention WITH LOGIN PASSWORD 'retention_secret' NOSUPERUSER NOCREATEDB NOCREATEROLE;
  ELSE
    ALTER ROLE retention WITH LOGIN PASSWORD 'retention_secret' NOSUPERUSER NOCREATEDB NOCREATEROLE;
  END IF;
  IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'readonly') THEN
    CREATE ROLE readonly WITH LOGIN PASSWORD 'readonly_secret' NOSUPERUSER NOCREATEDB NOCREATEROLE;
  ELSE
    ALTER ROLE readonly WITH LOGIN PASSWORD 'readonly_secret' NOSUPERUSER NOCREATEDB NOCREATEROLE;
  END IF;
END $$;

GRANT USAGE ON SCHEMA public TO app, retention, readonly;
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO app;
REVOKE UPDATE, DELETE ON events FROM app;

GRANT SELECT, DELETE ON events, llm_payloads, webhook_deliveries TO retention;
GRANT SELECT ON ALL TABLES IN SCHEMA public TO readonly;

-- migrate:down
-- squawk-ignore ban-drop-table
DROP TABLE IF EXISTS llm_payloads CASCADE;
-- squawk-ignore ban-drop-table
DROP TABLE IF EXISTS usage_ledger CASCADE;
-- squawk-ignore ban-drop-table
DROP TABLE IF EXISTS runs CASCADE;
-- squawk-ignore ban-drop-table
DROP TABLE IF EXISTS webhook_deliveries CASCADE;
-- squawk-ignore ban-drop-table
DROP TABLE IF EXISTS events CASCADE;
-- squawk-ignore ban-drop-table
DROP TABLE IF EXISTS webhook_subscriptions CASCADE;
-- squawk-ignore ban-drop-table
DROP TABLE IF EXISTS workspace_integrations CASCADE;
-- squawk-ignore ban-drop-table
DROP TABLE IF EXISTS sources CASCADE;
-- squawk-ignore ban-drop-table
DROP TABLE IF EXISTS campaigns CASCADE;
-- squawk-ignore ban-drop-table
DROP TABLE IF EXISTS brand_profiles CASCADE;
-- squawk-ignore ban-drop-table
DROP TABLE IF EXISTS memberships CASCADE;
-- squawk-ignore ban-drop-table
DROP TABLE IF EXISTS users CASCADE;
-- squawk-ignore ban-drop-table
DROP TABLE IF EXISTS workspaces CASCADE;
