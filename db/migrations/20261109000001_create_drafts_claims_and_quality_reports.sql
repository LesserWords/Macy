-- migrate:up
SET lock_timeout = '2s';
SET statement_timeout = '10s';

CREATE TABLE IF NOT EXISTS drafts (
  workspace_id   uuid NOT NULL,
  id             uuid NOT NULL DEFAULT uuidv7(),
  brief_id       uuid NOT NULL,
  revision       bigint NOT NULL CHECK (revision BETWEEN 0 AND 2),
  status         text NOT NULL DEFAULT 'draft'
                 CHECK (status IN ('draft','evaluating','pass','revision','reject','approved','declined','published')),
  prompt_version text NOT NULL,
  schema_version bigint NOT NULL,
  payload        jsonb NOT NULL,
  decision_note  text,
  decided_by     uuid REFERENCES users(id),
  decided_at     timestamptz,
  created_at     timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (id),
  UNIQUE (workspace_id, id),
  UNIQUE (brief_id, revision),
  FOREIGN KEY (workspace_id, brief_id) REFERENCES briefs (workspace_id, id)
);
-- squawk-ignore require-concurrent-index-creation
CREATE INDEX IF NOT EXISTS drafts_ws_brief_rev_idx
  ON drafts (workspace_id, brief_id, revision DESC);

CREATE TABLE IF NOT EXISTS draft_claims (
  workspace_id uuid NOT NULL,
  draft_id     uuid NOT NULL,
  idx          bigint NOT NULL CHECK (idx >= 0),
  kind         text NOT NULL CHECK (kind IN ('fact','analysis','recommendation')),
  text         text NOT NULL,
  document_id  uuid,
  PRIMARY KEY (draft_id, idx),
  FOREIGN KEY (workspace_id, draft_id)    REFERENCES drafts (workspace_id, id) ON DELETE CASCADE,
  FOREIGN KEY (workspace_id, document_id) REFERENCES documents (workspace_id, id),
  CHECK (kind <> 'fact' OR document_id IS NOT NULL)
);
-- squawk-ignore require-concurrent-index-creation
CREATE INDEX IF NOT EXISTS draft_claims_ws_draft_idx
  ON draft_claims (workspace_id, draft_id, idx);

CREATE TABLE IF NOT EXISTS quality_reports (
  workspace_id    uuid NOT NULL,
  draft_id        uuid NOT NULL PRIMARY KEY,
  verdict         text NOT NULL CHECK (verdict IN ('pass','revision','reject')),
  score           numeric(5,2),
  checks          jsonb NOT NULL DEFAULT '{}',
  findings        jsonb NOT NULL DEFAULT '[]',
  evaluator_model text NOT NULL,
  prompt_version  text NOT NULL,
  created_at      timestamptz NOT NULL DEFAULT now(),
  FOREIGN KEY (workspace_id, draft_id) REFERENCES drafts (workspace_id, id) ON DELETE CASCADE
);

GRANT SELECT, INSERT, UPDATE, DELETE ON drafts, draft_claims, quality_reports TO app;
GRANT SELECT ON drafts, draft_claims, quality_reports TO readonly;

-- migrate:down
-- squawk-ignore ban-drop-table
DROP TABLE IF EXISTS quality_reports CASCADE;
-- squawk-ignore ban-drop-table
DROP TABLE IF EXISTS draft_claims CASCADE;
-- squawk-ignore ban-drop-table
DROP TABLE IF EXISTS drafts CASCADE;
