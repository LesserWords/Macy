-- migrate:up
SET lock_timeout = '2s';
SET statement_timeout = '10s';

GRANT SELECT ON usage_ledger TO retention;

CREATE OR REPLACE FUNCTION anonymise_user_events(target_user_id uuid)
RETURNS bigint
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public
AS $$
DECLARE
  updated_rows bigint;
BEGIN
  UPDATE events
  SET payload = payload - ARRAY['email', 'name', 'user_email', 'user_name']
  WHERE (actor_user_id = target_user_id OR entity_id = target_user_id)
    AND (payload ?| ARRAY['email', 'name', 'user_email', 'user_name']);
  GET DIAGNOSTICS updated_rows = ROW_COUNT;
  RETURN updated_rows;
END;
$$;

REVOKE ALL ON FUNCTION anonymise_user_events(uuid) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION anonymise_user_events(uuid) TO app, owner;

-- migrate:down
REVOKE EXECUTE ON FUNCTION anonymise_user_events(uuid) FROM app, owner;
-- squawk-ignore ban-drop-function
DROP FUNCTION IF EXISTS anonymise_user_events(uuid);
REVOKE SELECT ON usage_ledger FROM retention;
