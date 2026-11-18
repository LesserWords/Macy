-- migrate:up
SET lock_timeout = '2s';
SET statement_timeout = '10s';

CREATE TABLE IF NOT EXISTS publications (
  workspace_id uuid NOT NULL REFERENCES workspaces(id),
  id           uuid NOT NULL DEFAULT uuidv7(),
  draft_id     uuid,
  destination  text NOT NULL DEFAULT 'sitemap',
  mode         text NOT NULL DEFAULT 'live' CHECK (mode IN ('draft','scheduled','live')),
  status       text NOT NULL DEFAULT 'published' CHECK (status IN ('pending','published','failed')),
  external_id  text,
  url          text NOT NULL,
  title        text NOT NULL,
  scheduled_at timestamptz,
  published_at timestamptz,
  response     jsonb,
  created_at   timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (id),
  UNIQUE (workspace_id, id),
  UNIQUE (workspace_id, url)
);

-- squawk-ignore require-concurrent-index-creation
CREATE INDEX IF NOT EXISTS pub_title_trgm ON publications USING gin (title gin_trgm_ops);

GRANT SELECT, INSERT, UPDATE, DELETE ON publications TO app;
GRANT SELECT ON publications TO readonly;

-- migrate:down
-- squawk-ignore ban-drop-table
DROP TABLE IF EXISTS publications CASCADE;
