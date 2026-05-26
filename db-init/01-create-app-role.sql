-- Create a non-superuser role for runtime app + tests.
-- Superusers bypass RLS even with FORCE ROW LEVEL SECURITY, so multi-tenant
-- isolation requires a regular role.
-- This script runs once when the postgres data volume is first initialized.

CREATE ROLE ai_sdr_app WITH LOGIN PASSWORD 'ai_sdr_app_dev' NOSUPERUSER;

GRANT ALL PRIVILEGES ON DATABASE ai_sdr TO ai_sdr_app;
GRANT ALL PRIVILEGES ON SCHEMA public TO ai_sdr_app;
GRANT ALL PRIVILEGES ON ALL TABLES IN SCHEMA public TO ai_sdr_app;
GRANT ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA public TO ai_sdr_app;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT ALL ON TABLES TO ai_sdr_app;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT ALL ON SEQUENCES TO ai_sdr_app;

-- Alembic also runs as ai_sdr_app — make sure created objects are owned by it
-- so it can drop/alter them later.
ALTER DEFAULT PRIVILEGES FOR ROLE ai_sdr_app IN SCHEMA public GRANT ALL ON TABLES TO ai_sdr_app;
