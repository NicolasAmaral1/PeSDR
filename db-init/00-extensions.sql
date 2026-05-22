-- Create extensions as superuser (the default POSTGRES_USER, ai_sdr).
-- Runs once when the postgres data volume is first initialized, BEFORE the
-- app role exists. The alembic migration 0001_extensions becomes a no-op
-- (CREATE EXTENSION IF NOT EXISTS detects them as already present).

CREATE EXTENSION IF NOT EXISTS "uuid-ossp";
CREATE EXTENSION IF NOT EXISTS "vector";
