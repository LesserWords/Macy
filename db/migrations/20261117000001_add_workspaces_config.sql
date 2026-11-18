-- migrate:up
SET lock_timeout = '2s';
SET statement_timeout = '10s';

ALTER TABLE workspaces
  ADD COLUMN IF NOT EXISTS config jsonb;

-- migrate:down
-- squawk-ignore ban-drop-column
ALTER TABLE workspaces DROP COLUMN IF EXISTS config;
