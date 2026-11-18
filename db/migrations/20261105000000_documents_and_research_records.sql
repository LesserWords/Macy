-- migrate:up
SET lock_timeout = '2s';
SET statement_timeout = '10s';

CREATE TABLE IF NOT EXISTS documents (
  workspace_id   uuid NOT NULL REFERENCES workspaces(id),
  id             uuid NOT NULL DEFAULT uuidv7(),
  source_id      uuid REFERENCES sources(id),
  url            text NOT NULL,
  canonical_url  text NOT NULL,
  content_hash   bytea,
  title          text NOT NULL,
  lang           text,
  published_at   timestamptz,
  date_status    text NOT NULL CHECK (date_status IN ('verified','inferred','unverified')),
  fetch_status   text NOT NULL CHECK (fetch_status IN ('ok','http_error','blocked','extraction_failed','paywalled')),
  http_status    bigint,
  extracted_text text,
  text_purged_at timestamptz,
  meta           jsonb NOT NULL DEFAULT '{}',
  fetched_at     timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (id),
  UNIQUE (workspace_id, id),
  UNIQUE (workspace_id, canonical_url)
);
-- squawk-ignore require-concurrent-index-creation
CREATE INDEX IF NOT EXISTS documents_title_trgm ON documents USING gin (title gin_trgm_ops);
-- squawk-ignore require-concurrent-index-creation
CREATE INDEX IF NOT EXISTS documents_published ON documents (workspace_id, published_at DESC);

CREATE TABLE IF NOT EXISTS research_records (
  workspace_id   uuid NOT NULL,
  id             uuid NOT NULL DEFAULT uuidv7(),
  run_id         uuid NOT NULL,
  document_id    uuid NOT NULL,
  duplicate_of   uuid,
  status         text NOT NULL DEFAULT 'new'
                 CHECK (status IN ('new','extracted','verified','stale','duplicate','failed')),
  schema_version bigint NOT NULL,
  payload        jsonb NOT NULL,
  created_at     timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (id),
  UNIQUE (workspace_id, id),
  UNIQUE (run_id, document_id),
  FOREIGN KEY (workspace_id, run_id)       REFERENCES runs (workspace_id, id),
  FOREIGN KEY (workspace_id, document_id)  REFERENCES documents (workspace_id, id),
  FOREIGN KEY (workspace_id, duplicate_of) REFERENCES research_records (workspace_id, id),
  CHECK ((status = 'duplicate') = (duplicate_of IS NOT NULL))
);
-- squawk-ignore require-concurrent-index-creation
CREATE INDEX IF NOT EXISTS rr_queue ON research_records (run_id, status) WHERE status IN ('new','extracted');

GRANT SELECT, INSERT, UPDATE, DELETE ON documents, research_records TO app;
GRANT SELECT, UPDATE (extracted_text, text_purged_at) ON documents TO retention;
GRANT SELECT ON documents, research_records TO readonly;

-- migrate:down
-- squawk-ignore ban-drop-table
DROP TABLE IF EXISTS research_records CASCADE;
-- squawk-ignore ban-drop-table
DROP TABLE IF EXISTS documents CASCADE;
