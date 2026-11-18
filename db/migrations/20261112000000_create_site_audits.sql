-- migrate:up
SET lock_timeout = '2s';
SET statement_timeout = '10s';

CREATE TABLE IF NOT EXISTS site_audits (
  workspace_id  uuid NOT NULL REFERENCES workspaces(id),
  id            uuid NOT NULL DEFAULT uuidv7(),
  start_url     text NOT NULL,
  pages_crawled bigint NOT NULL CHECK (pages_crawled >= 0),
  max_pages     bigint NOT NULL CHECK (max_pages > 0 AND max_pages <= 200),
  overall_score numeric(5,2) NOT NULL CHECK (overall_score >= 0 AND overall_score <= 100),
  grade         text NOT NULL,
  payload       jsonb NOT NULL,
  created_at    timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (id),
  UNIQUE (workspace_id, id)
);

-- squawk-ignore require-concurrent-index-creation
CREATE INDEX IF NOT EXISTS site_audits_ws_created_idx
  ON site_audits (workspace_id, created_at DESC);

CREATE TABLE IF NOT EXISTS audit_pages (
  workspace_id  uuid NOT NULL REFERENCES workspaces(id),
  id            uuid NOT NULL DEFAULT uuidv7(),
  site_audit_id uuid,
  url           text NOT NULL,
  status_code   bigint NOT NULL,
  overall_score numeric(5,2) NOT NULL CHECK (overall_score >= 0 AND overall_score <= 100),
  grade         text NOT NULL,
  payload       jsonb NOT NULL,
  created_at    timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (id),
  UNIQUE (workspace_id, id),
  FOREIGN KEY (workspace_id, site_audit_id) REFERENCES site_audits (workspace_id, id)
);

-- squawk-ignore require-concurrent-index-creation
CREATE INDEX IF NOT EXISTS audit_pages_ws_audit_idx
  ON audit_pages (workspace_id, site_audit_id, created_at DESC);

GRANT SELECT, INSERT, UPDATE, DELETE ON site_audits, audit_pages TO app;
GRANT SELECT ON site_audits, audit_pages TO readonly;

-- migrate:down
-- squawk-ignore ban-drop-table
DROP TABLE IF EXISTS audit_pages CASCADE;
-- squawk-ignore ban-drop-table
DROP TABLE IF EXISTS site_audits CASCADE;
