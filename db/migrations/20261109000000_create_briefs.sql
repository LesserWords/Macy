-- migrate:up
SET lock_timeout = '2s';
SET statement_timeout = '10s';

CREATE TABLE IF NOT EXISTS briefs (
  workspace_id     uuid NOT NULL,
  id               uuid NOT NULL DEFAULT uuidv7(),
  opportunity_id   uuid NOT NULL,
  version          bigint NOT NULL CHECK (version > 0),
  brand_profile_id uuid NOT NULL,
  prompt_version   text NOT NULL,
  keywords         jsonb NOT NULL,
  schema_version   bigint NOT NULL,
  payload          jsonb NOT NULL,
  created_at       timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (id),
  UNIQUE (workspace_id, id),
  UNIQUE (opportunity_id, version),
  FOREIGN KEY (workspace_id, opportunity_id)   REFERENCES opportunities (workspace_id, id),
  FOREIGN KEY (workspace_id, brand_profile_id) REFERENCES brand_profiles (workspace_id, id)
);
-- squawk-ignore require-concurrent-index-creation
CREATE INDEX IF NOT EXISTS briefs_ws_opp_ver_idx
  ON briefs (workspace_id, opportunity_id, version DESC);

GRANT SELECT, INSERT, UPDATE, DELETE ON briefs TO app;
GRANT SELECT ON briefs TO readonly;

-- migrate:down
-- squawk-ignore ban-drop-table
DROP TABLE IF EXISTS briefs CASCADE;
