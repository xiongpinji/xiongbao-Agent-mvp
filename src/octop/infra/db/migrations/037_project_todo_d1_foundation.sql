CREATE TABLE IF NOT EXISTS project_todo_display_state (
  project_id TEXT NOT NULL,
  todo_id TEXT NOT NULL,
  revision INTEGER NOT NULL DEFAULT 1
    CHECK (typeof(revision) = 'integer' AND revision BETWEEN 1 AND 9007199254740991),
  updated_at INTEGER NOT NULL CHECK (typeof(updated_at) = 'integer' AND updated_at >= 0),
  PRIMARY KEY (project_id, todo_id),
  FOREIGN KEY (project_id, todo_id) REFERENCES project_todos(project_id, todo_id) ON DELETE CASCADE
);

-- D1_STATEMENT_BOUNDARY

CREATE TABLE IF NOT EXISTS project_todo_attachment_state (
  project_id TEXT NOT NULL,
  todo_id TEXT NOT NULL,
  revision INTEGER NOT NULL DEFAULT 1
    CHECK (typeof(revision) = 'integer' AND revision BETWEEN 1 AND 9223372036854775807),
  updated_at INTEGER NOT NULL CHECK (typeof(updated_at) = 'integer' AND updated_at >= 0),
  PRIMARY KEY (project_id, todo_id),
  FOREIGN KEY (project_id, todo_id) REFERENCES project_todos(project_id, todo_id) ON DELETE CASCADE
);

-- D1_STATEMENT_BOUNDARY

CREATE TABLE IF NOT EXISTS project_todo_attachment_usage (
  project_id TEXT NOT NULL PRIMARY KEY REFERENCES project_spaces(project_id) ON DELETE CASCADE,
  used_bytes INTEGER NOT NULL DEFAULT 0
    CHECK (typeof(used_bytes) = 'integer' AND used_bytes BETWEEN 0 AND 1073741824),
  updated_at INTEGER NOT NULL CHECK (typeof(updated_at) = 'integer' AND updated_at >= 0)
);

-- D1_STATEMENT_BOUNDARY

CREATE TABLE IF NOT EXISTS project_todo_attachments (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  attachment_id TEXT NOT NULL UNIQUE CHECK (length(attachment_id) = 26),
  project_id TEXT NOT NULL,
  todo_id TEXT NOT NULL,
  uploader_user_id INTEGER NOT NULL CHECK (typeof(uploader_user_id) = 'integer' AND uploader_user_id > 0),
  display_name TEXT NOT NULL CHECK (
    length(display_name) BETWEEN 1 AND 120 AND display_name = trim(display_name)
    AND instr(display_name, '/') = 0 AND instr(display_name, char(92)) = 0
    AND instr(display_name, char(0)) = 0 AND length(replace(display_name, '.', '')) > 0
  ),
  size_bytes INTEGER NOT NULL
    CHECK (typeof(size_bytes) = 'integer' AND size_bytes BETWEEN 0 AND 26214400),
  sha256 TEXT NOT NULL CHECK (length(sha256) = 64 AND sha256 NOT GLOB '*[^0-9a-f]*'),
  preview_media_type TEXT CHECK (preview_media_type IN ('image/png', 'image/jpeg', 'image/webp')),
  preview_width INTEGER,
  preview_height INTEGER,
  object_key TEXT NOT NULL UNIQUE CHECK (object_key = project_id || '/' || attachment_id),
  created_at INTEGER NOT NULL CHECK (typeof(created_at) = 'integer' AND created_at >= 0),
  removed_at INTEGER CHECK (removed_at IS NULL OR (typeof(removed_at) = 'integer' AND removed_at >= created_at)),
  client_request_id TEXT NOT NULL CHECK (
    length(client_request_id) = 36 AND client_request_id = lower(client_request_id)
    AND substr(client_request_id, 9, 1) = '-' AND substr(client_request_id, 14, 1) = '-'
    AND substr(client_request_id, 19, 1) = '-' AND substr(client_request_id, 24, 1) = '-'
    AND substr(client_request_id, 15, 1) = '4' AND substr(client_request_id, 20, 1) IN ('8', '9', 'a', 'b')
    AND length(replace(client_request_id, '-', '')) = 32
    AND replace(client_request_id, '-', '') NOT GLOB '*[^0-9a-f]*'
  ),
  request_fingerprint TEXT NOT NULL
    CHECK (length(request_fingerprint) = 64 AND request_fingerprint NOT GLOB '*[^0-9a-f]*'),
  FOREIGN KEY (project_id, todo_id) REFERENCES project_todos(project_id, todo_id) ON DELETE CASCADE,
  UNIQUE (project_id, todo_id, uploader_user_id, client_request_id),
  CHECK (
    (preview_media_type IS NULL AND preview_width IS NULL AND preview_height IS NULL)
    OR (preview_media_type IS NOT NULL AND typeof(preview_width) = 'integer'
      AND typeof(preview_height) = 'integer' AND preview_width BETWEEN 1 AND 40000000
      AND preview_height BETWEEN 1 AND 40000000 AND preview_width * preview_height <= 40000000
      AND size_bytes BETWEEN 1 AND 8388608)
  )
);

-- D1_STATEMENT_BOUNDARY

CREATE TABLE IF NOT EXISTS project_todo_attachment_retained_objects (
  object_key TEXT NOT NULL PRIMARY KEY,
  attachment_id TEXT NOT NULL UNIQUE CHECK (length(attachment_id) = 26),
  project_id TEXT NOT NULL,
  todo_id TEXT NOT NULL,
  uploader_user_id INTEGER NOT NULL CHECK (typeof(uploader_user_id) = 'integer' AND uploader_user_id > 0),
  display_name TEXT NOT NULL CHECK (
    length(display_name) BETWEEN 1 AND 120 AND display_name = trim(display_name)
    AND instr(display_name, '/') = 0 AND instr(display_name, char(92)) = 0
    AND instr(display_name, char(0)) = 0 AND length(replace(display_name, '.', '')) > 0
  ),
  size_bytes INTEGER NOT NULL
    CHECK (typeof(size_bytes) = 'integer' AND size_bytes BETWEEN 0 AND 26214400),
  sha256 TEXT NOT NULL CHECK (length(sha256) = 64 AND sha256 NOT GLOB '*[^0-9a-f]*'),
  preview_media_type TEXT CHECK (preview_media_type IN ('image/png', 'image/jpeg', 'image/webp')),
  preview_width INTEGER,
  preview_height INTEGER,
  created_at INTEGER NOT NULL CHECK (typeof(created_at) = 'integer' AND created_at >= 0),
  removed_at INTEGER CHECK (removed_at IS NULL OR (typeof(removed_at) = 'integer' AND removed_at >= created_at)),
  client_request_id TEXT NOT NULL CHECK (
    length(client_request_id) = 36 AND client_request_id = lower(client_request_id)
    AND substr(client_request_id, 9, 1) = '-' AND substr(client_request_id, 14, 1) = '-'
    AND substr(client_request_id, 19, 1) = '-' AND substr(client_request_id, 24, 1) = '-'
    AND substr(client_request_id, 15, 1) = '4' AND substr(client_request_id, 20, 1) IN ('8', '9', 'a', 'b')
    AND length(replace(client_request_id, '-', '')) = 32
    AND replace(client_request_id, '-', '') NOT GLOB '*[^0-9a-f]*'
  ),
  request_fingerprint TEXT NOT NULL
    CHECK (length(request_fingerprint) = 64 AND request_fingerprint NOT GLOB '*[^0-9a-f]*'),
  retained_at INTEGER NOT NULL CHECK (typeof(retained_at) = 'integer' AND retained_at >= 0),
  CHECK (object_key = project_id || '/' || attachment_id),
  CHECK (
    (preview_media_type IS NULL AND preview_width IS NULL AND preview_height IS NULL)
    OR (preview_media_type IS NOT NULL AND typeof(preview_width) = 'integer'
      AND typeof(preview_height) = 'integer' AND preview_width BETWEEN 1 AND 40000000
      AND preview_height BETWEEN 1 AND 40000000 AND preview_width * preview_height <= 40000000
      AND size_bytes BETWEEN 1 AND 8388608)
  )
);

-- D1_STATEMENT_BOUNDARY

CREATE INDEX IF NOT EXISTS idx_project_todo_attachments_active
  ON project_todo_attachments(project_id, todo_id, created_at, attachment_id) WHERE removed_at IS NULL;

-- D1_STATEMENT_BOUNDARY

CREATE INDEX IF NOT EXISTS idx_project_todo_attachments_removed
  ON project_todo_attachments(project_id, todo_id, removed_at, attachment_id) WHERE removed_at IS NOT NULL;

-- D1_STATEMENT_BOUNDARY

CREATE INDEX IF NOT EXISTS idx_project_todo_attachment_retained_project
  ON project_todo_attachment_retained_objects(project_id, object_key);

-- D1_STATEMENT_BOUNDARY

CREATE INDEX IF NOT EXISTS idx_project_todos_assignee_display_delete
  ON project_todos(assignee_user_id, project_id, todo_id);

-- D1_STATEMENT_BOUNDARY

CREATE INDEX IF NOT EXISTS idx_project_todos_creator_attachment_delete
  ON project_todos(creator_user_id, project_id, todo_id);

-- D1_STATEMENT_BOUNDARY

CREATE TRIGGER IF NOT EXISTS trg_project_todo_attachment_retain_before_delete
BEFORE DELETE ON project_todo_attachments
FOR EACH ROW
BEGIN
  SELECT RAISE(ABORT, 'todo_attachment_retained_conflict')
  WHERE EXISTS (
    SELECT 1 FROM project_todo_attachment_retained_objects r WHERE r.object_key = OLD.object_key
    AND (r.attachment_id IS NOT OLD.attachment_id OR r.project_id IS NOT OLD.project_id
      OR r.todo_id IS NOT OLD.todo_id OR r.uploader_user_id IS NOT OLD.uploader_user_id
      OR r.display_name IS NOT OLD.display_name OR r.size_bytes IS NOT OLD.size_bytes
      OR r.sha256 IS NOT OLD.sha256 OR r.preview_media_type IS NOT OLD.preview_media_type
      OR r.preview_width IS NOT OLD.preview_width OR r.preview_height IS NOT OLD.preview_height
      OR r.created_at IS NOT OLD.created_at OR r.removed_at IS NOT OLD.removed_at
      OR r.client_request_id IS NOT OLD.client_request_id
      OR r.request_fingerprint IS NOT OLD.request_fingerprint)
  );
  INSERT INTO project_todo_attachment_retained_objects (
    object_key, attachment_id, project_id, todo_id, uploader_user_id, display_name,
    size_bytes, sha256, preview_media_type, preview_width, preview_height,
    created_at, removed_at, client_request_id, request_fingerprint, retained_at
  ) VALUES (
    OLD.object_key, OLD.attachment_id, OLD.project_id, OLD.todo_id, OLD.uploader_user_id, OLD.display_name,
    OLD.size_bytes, OLD.sha256, OLD.preview_media_type, OLD.preview_width, OLD.preview_height,
    OLD.created_at, OLD.removed_at, OLD.client_request_id, OLD.request_fingerprint,
    CAST(strftime('%s', 'now') AS INTEGER)
  ) ON CONFLICT(object_key) DO NOTHING;
END;

-- D1_STATEMENT_BOUNDARY

CREATE TRIGGER IF NOT EXISTS trg_users_todo_display_before_delete
BEFORE DELETE ON users
FOR EACH ROW
BEGIN
  SELECT RAISE(ABORT, 'todo_display_state_missing_or_exhausted')
  WHERE EXISTS (
    SELECT 1 FROM project_todos t
    LEFT JOIN project_todo_display_state s ON s.project_id = t.project_id AND s.todo_id = t.todo_id
    WHERE t.assignee_user_id = OLD.id AND t.creator_user_id <> OLD.id
      AND (s.todo_id IS NULL OR s.revision >= 9007199254740991)
  );
  UPDATE project_todo_display_state
  SET revision = revision + 1, updated_at = CAST(strftime('%s', 'now') AS INTEGER)
  WHERE EXISTS (
    SELECT 1 FROM project_todos t
    WHERE t.project_id = project_todo_display_state.project_id
      AND t.todo_id = project_todo_display_state.todo_id
      AND t.assignee_user_id = OLD.id AND t.creator_user_id <> OLD.id
  );
END;

-- D1_STATEMENT_BOUNDARY

INSERT INTO project_todo_display_state(project_id, todo_id, revision, updated_at)
SELECT project_id, todo_id, 1, CAST(strftime('%s', 'now') AS INTEGER) FROM project_todos WHERE 1
ON CONFLICT(project_id, todo_id) DO NOTHING;

-- D1_STATEMENT_BOUNDARY

INSERT INTO project_todo_attachment_state(project_id, todo_id, revision, updated_at)
SELECT project_id, todo_id, 1, CAST(strftime('%s', 'now') AS INTEGER) FROM project_todos WHERE 1
ON CONFLICT(project_id, todo_id) DO NOTHING;

-- D1_STATEMENT_BOUNDARY

INSERT INTO project_todo_attachment_usage(project_id, used_bytes, updated_at)
SELECT project_id, 0, CAST(strftime('%s', 'now') AS INTEGER) FROM project_spaces WHERE 1
ON CONFLICT(project_id) DO NOTHING;

-- D1_STATEMENT_BOUNDARY

UPDATE _schema_version SET version = CASE WHEN version < 37 THEN 37 ELSE version END;
