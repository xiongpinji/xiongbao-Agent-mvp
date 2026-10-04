-- Schema v36 (SQLite): derived, complete Unicode thread-title search keys.
-- migrate.py uses one BEGIN IMMEDIATE transaction for this canonical DDL,
-- Python NFKC+casefold backfill/validation, and the final watermark.
ALTER TABLE threads ADD COLUMN title_search_key TEXT NOT NULL DEFAULT '';

UPDATE _schema_version SET version = 36;
