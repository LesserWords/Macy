-- migrate:up
SET lock_timeout = '2s';
SET statement_timeout = '10s';

CREATE TABLE IF NOT EXISTS gsc_url_inspection_cache (
  workspace_id  uuid NOT NULL REFERENCES workspaces(id),
  site_url      text NOT NULL,
  inspected_url text NOT NULL,
  payload       jsonb NOT NULL,
  fetched_at    timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (workspace_id, site_url, inspected_url)
);

-- squawk-ignore require-concurrent-index-creation
CREATE INDEX IF NOT EXISTS gsc_inspection_ws_fetched_idx
  ON gsc_url_inspection_cache (workspace_id, fetched_at DESC);

CREATE TABLE IF NOT EXISTS url_monitors (
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
  PRIMARY KEY (id),
  UNIQUE (workspace_id, url)
);

-- squawk-ignore require-concurrent-index-creation
CREATE INDEX IF NOT EXISTS url_monitors_ws_checked_idx
  ON url_monitors (workspace_id, last_checked_at DESC);

CREATE TABLE IF NOT EXISTS keyword_rankings (
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

-- squawk-ignore require-concurrent-index-creation
CREATE INDEX IF NOT EXISTS keyword_rankings_ws_kw_idx
  ON keyword_rankings (workspace_id, domain, keyword, checked_at DESC);

GRANT SELECT, INSERT, UPDATE, DELETE ON gsc_url_inspection_cache, url_monitors, keyword_rankings TO app;
GRANT SELECT ON gsc_url_inspection_cache, url_monitors, keyword_rankings TO readonly;

-- migrate:down
-- squawk-ignore ban-drop-table
DROP TABLE IF EXISTS keyword_rankings CASCADE;
-- squawk-ignore ban-drop-table
DROP TABLE IF EXISTS url_monitors CASCADE;
-- squawk-ignore ban-drop-table
DROP TABLE IF EXISTS gsc_url_inspection_cache CASCADE;
