-- Schema v36 (PostgreSQL): equivalent derived Unicode thread-title key.
-- The dedicated applier runs this DDL, Python NFKC+casefold backfill and
-- validation in the same connection transaction, writing the watermark last.
ALTER TABLE threads ADD COLUMN IF NOT EXISTS title_search_key TEXT NOT NULL DEFAULT '';

UPDATE _schema_version SET version = 36;
