-- migrate:up transaction:false
SET lock_timeout = '2s';
SET statement_timeout = '10s';

BEGIN;

-- squawk-ignore require-concurrent-index-creation
CREATE UNIQUE INDEX IF NOT EXISTS publications_ws_draft_dest_idx
  ON publications (workspace_id, draft_id, destination);

-- squawk-ignore ban-drop-not-null
ALTER TABLE publications ALTER COLUMN url DROP NOT NULL;

ALTER TABLE publications
  ADD COLUMN IF NOT EXISTS error text;

ALTER TABLE publications
  ADD CONSTRAINT publications_workspace_draft_fk
  FOREIGN KEY (workspace_id, draft_id) REFERENCES drafts (workspace_id, id) NOT VALID;

-- squawk-ignore disallowed-unique-constraint
ALTER TABLE publications
  ADD CONSTRAINT publications_workspace_draft_destination_key
  UNIQUE USING INDEX publications_ws_draft_dest_idx;

COMMIT;

BEGIN;
ALTER TABLE publications
  VALIDATE CONSTRAINT publications_workspace_draft_fk;
COMMIT;

-- migrate:down transaction:false
BEGIN;

-- squawk-ignore ban-drop-constraint
ALTER TABLE publications DROP CONSTRAINT IF EXISTS publications_workspace_draft_destination_key;
-- squawk-ignore ban-drop-constraint
ALTER TABLE publications DROP CONSTRAINT IF EXISTS publications_workspace_draft_fk;
-- squawk-ignore ban-drop-column
ALTER TABLE publications DROP COLUMN IF EXISTS error;
UPDATE publications SET url = COALESCE(url, 'urn:failed:' || id::text) WHERE url IS NULL;
-- squawk-ignore adding-not-nullable-field
ALTER TABLE publications ALTER COLUMN url SET NOT NULL;

COMMIT;
