-- Schema v33 (SQLite): private image attachments to project todo comments.
-- Project ownership is derived through comment -> todo; object keys are server-only.
CREATE TABLE IF NOT EXISTS project_todo_comment_images (
  image_id TEXT PRIMARY KEY,
  comment_id TEXT NOT NULL REFERENCES project_todo_comments(comment_id) ON DELETE CASCADE,
  object_key TEXT NOT NULL UNIQUE,
  size_bytes INTEGER NOT NULL CHECK (size_bytes > 0 AND size_bytes <= 8388608),
  sha256 TEXT NOT NULL CHECK (length(sha256) = 64),
  media_type TEXT NOT NULL CHECK (media_type IN ('image/png', 'image/jpeg', 'image/webp')),
  position INTEGER NOT NULL CHECK (position >= 0 AND position < 5),
  created_at INTEGER NOT NULL,
  UNIQUE (comment_id, position)
);

CREATE INDEX IF NOT EXISTS idx_project_todo_comment_images_comment
  ON project_todo_comment_images(comment_id, position ASC);

-- A retained soft-deleted todo still owns its bytes and consumes quota.
CREATE TABLE IF NOT EXISTS project_todo_comment_image_usage (
  project_id TEXT PRIMARY KEY REFERENCES project_spaces(project_id) ON DELETE CASCADE,
  used_bytes INTEGER NOT NULL DEFAULT 0 CHECK (used_bytes >= 0 AND used_bytes <= 536870912)
);

UPDATE _schema_version SET version = 33;
