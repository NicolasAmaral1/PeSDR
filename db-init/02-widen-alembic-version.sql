-- Widen alembic_version.version_num beyond the default 32 chars.
--
-- Alembic creates this column at varchar(32) on first run, but our
-- revision id naming (e.g. "0010_follow_up_and_talkflow_columns" = 38
-- chars) regularly exceeds that. Alembic then crashes when updating
-- the row after applying a migration with a long name.
--
-- This script runs once when the postgres data volume is first
-- initialized, BEFORE alembic ever touches the DB. We pre-create the
-- table with the wider column so alembic finds it ready.
--
-- Idempotent: if the table already exists (e.g. legacy deploys), the
-- ALTER widens the column in place. Existing data is preserved.

CREATE TABLE IF NOT EXISTS alembic_version (
    version_num varchar(255) NOT NULL,
    CONSTRAINT alembic_version_pkc PRIMARY KEY (version_num)
);

ALTER TABLE alembic_version ALTER COLUMN version_num TYPE varchar(255);
