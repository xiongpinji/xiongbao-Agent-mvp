ALTER TABLE threads ADD COLUMN archived_at BIGINT DEFAULT NULL
  CHECK (archived_at IS NULL OR (archived_at > 0 AND archived_at <= 9007199254740991));
CREATE INDEX idx_threads_user_archive ON threads(user_id, archived_at DESC, thread_id DESC);
UPDATE _schema_version SET version = CASE WHEN version < 38 THEN 38 ELSE version END;
