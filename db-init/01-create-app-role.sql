-- Create a non-superuser role for runtime app + tests.
-- Superusers bypass RLS even with FORCE ROW LEVEL SECURITY, so multi-tenant
-- isolation requires a regular role.
-- This script runs once when the postgres data volume is first initialized.

-- Password must match the DATABASE_URL in docker-compose.yml. Both are
-- 'ai_sdr_app' (yes, same as the role name — this is a dev/test password,
-- not a production secret). Change both in lock-step if rotating.
CREATE ROLE ai_sdr_app WITH LOGIN PASSWORD 'ai_sdr_app' NOSUPERUSER;

GRANT ALL PRIVILEGES ON DATABASE ai_sdr TO ai_sdr_app;
GRANT ALL PRIVILEGES ON SCHEMA public TO ai_sdr_app;
GRANT ALL PRIVILEGES ON ALL TABLES IN SCHEMA public TO ai_sdr_app;
GRANT ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA public TO ai_sdr_app;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT ALL ON TABLES TO ai_sdr_app;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT ALL ON SEQUENCES TO ai_sdr_app;

-- Alembic also runs as ai_sdr_app — make sure created objects are owned by it
-- so it can drop/alter them later.
ALTER DEFAULT PRIVILEGES FOR ROLE ai_sdr_app IN SCHEMA public GRANT ALL ON TABLES TO ai_sdr_app;

-- BYPASSRLS lets ai_sdr_app opt out of RLS via `SET LOCAL row_security = off`
-- WITHIN a transaction. Normal queries still respect RLS because the GUC
-- defaults to `on`. The only legitimate caller of the opt-out is
-- follow_up_scanner, which needs a cross-tenant scan of pending jobs.
-- Without BYPASSRLS, `SET LOCAL row_security = off` raises rather than
-- silently filtering, breaking the scanner in any multi-tenant deploy.
ALTER ROLE ai_sdr_app BYPASSRLS;
