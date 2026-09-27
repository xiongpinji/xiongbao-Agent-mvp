-- Schema v31 (SQLite): private todo comments, separate from project messages.
-- Empty body is allowed at the storage layer for B2 image-only comments.

CREATE TABLE IF NOT EXISTS project_todo_comments (
  comment_id TEXT PRIMARY KEY,
  todo_id TEXT NOT NULL REFERENCES project_todos(todo_id) ON DELETE CASCADE,
  author_user_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
  body_text TEXT NOT NULL CHECK (body_text = trim(body_text) AND length(body_text) <= 4000),
  client_request_id TEXT NOT NULL,
  request_fingerprint TEXT NOT NULL CHECK (length(request_fingerprint) = 64),
  created_at INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_project_todo_comments_todo_time
  ON project_todo_comments(todo_id, created_at DESC, comment_id DESC);
CREATE UNIQUE INDEX IF NOT EXISTS uq_project_todo_comments_request
  ON project_todo_comments(todo_id, author_user_id, client_request_id);

UPDATE _schema_version SET version = 31;
