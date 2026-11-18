-- migrate:up
SET lock_timeout = '2s';
SET statement_timeout = '10s';

CREATE TABLE IF NOT EXISTS keyword_cache (
  provider     text NOT NULL,
  locale       text NOT NULL,
  market       text NOT NULL,
  keyword_norm text NOT NULL,
  data         jsonb NOT NULL,
  fetched_at   timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (provider, locale, market, keyword_norm)
);

-- squawk-ignore require-concurrent-index-creation
CREATE INDEX IF NOT EXISTS keyword_cache_fetched_at_idx ON keyword_cache (fetched_at);

GRANT SELECT, INSERT, UPDATE, DELETE ON keyword_cache TO app;
GRANT SELECT ON keyword_cache TO readonly;

-- migrate:down
-- squawk-ignore ban-drop-table
DROP TABLE IF EXISTS keyword_cache CASCADE;
