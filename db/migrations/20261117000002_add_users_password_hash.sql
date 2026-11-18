-- migrate:up
SET lock_timeout = '2s';
SET statement_timeout = '10s';

ALTER TABLE users
  ADD COLUMN IF NOT EXISTS password_hash text;

-- migrate:down
-- squawk-ignore ban-drop-column
ALTER TABLE users DROP COLUMN IF EXISTS password_hash;
