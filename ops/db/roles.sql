-- The tracker's database roles, one per job (ADR-009). Run once per cluster
-- with a login that may create roles. Idempotent. No password is set here, so
-- none is ever in the repo: a deploy sets each one from a secret file.
--
--   tracker_owner  owns the tables and runs migrations. RLS doesn't bind it, so
--                  the running app never uses it. Adding a person is its job.
--   tracker_app    the running app. Doesn't own the tables, so RLS binds it.
DO $$
DECLARE
    role_name text;
BEGIN
    FOREACH role_name IN ARRAY ARRAY['tracker_owner', 'tracker_app'] LOOP
        IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = role_name) THEN
            EXECUTE format('CREATE ROLE %I LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOBYPASSRLS', role_name);
        END IF;
    END LOOP;
END
$$;
