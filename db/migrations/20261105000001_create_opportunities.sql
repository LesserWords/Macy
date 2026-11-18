-- migrate:up
SET lock_timeout = '2s';
SET statement_timeout = '10s';

CREATE TABLE IF NOT EXISTS opportunities (
  workspace_id   uuid NOT NULL,
  id             uuid NOT NULL DEFAULT uuidv7(),
  run_id         uuid NOT NULL,
  record_id      uuid NOT NULL,
  scores         jsonb NOT NULL,                        -- per criterion: score, evidence, is_estimate
  total          numeric(5,2) NOT NULL CHECK (total BETWEEN 0 AND 100),
  rank           bigint,                                -- position in this run's report
  funnel_stage   text NOT NULL CHECK (funnel_stage IN ('tofu','mofu','bofu')),
  confidence     text NOT NULL CHECK (confidence IN ('low','medium','high')),
  angle          text NOT NULL,
  status         text NOT NULL DEFAULT 'scored'
                 CHECK (status IN ('scored','approved','rejected','expired')),
  decided_by     uuid REFERENCES users(id),
  decided_at     timestamptz,
  decision_note  text,
  created_at     timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (id),
  UNIQUE (workspace_id, id),
  UNIQUE (run_id, record_id),
  FOREIGN KEY (workspace_id, run_id)    REFERENCES runs (workspace_id, id),
  FOREIGN KEY (workspace_id, record_id) REFERENCES research_records (workspace_id, id),
  CHECK ((status IN ('approved','rejected')) = (decided_by IS NOT NULL AND decided_at IS NOT NULL))
);
-- squawk-ignore require-concurrent-index-creation
CREATE INDEX IF NOT EXISTS opp_report ON opportunities (run_id, total DESC);

GRANT SELECT, INSERT, UPDATE, DELETE ON opportunities TO app;
GRANT SELECT ON opportunities TO readonly;

-- migrate:down
-- squawk-ignore ban-drop-table
DROP TABLE IF EXISTS opportunities CASCADE;
