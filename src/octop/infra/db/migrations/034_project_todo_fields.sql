-- Schema v34 (SQLite): project-owned plan dates, priorities and tags.
-- migrate.py runs this DDL statement-by-statement under the shared connection
-- lock, with foreign_keys OFF before BEGIN IMMEDIATE. It preserves optional
-- partial-upgrade date/reference columns, verifies children and seeds catalogs
-- in that same transaction before applying the final watermark.

CREATE TABLE IF NOT EXISTS project_todo_catalog_state (
  project_id TEXT NOT NULL PRIMARY KEY REFERENCES project_spaces(project_id) ON DELETE CASCADE,
  revision INTEGER NOT NULL DEFAULT 1 CHECK (typeof(revision) = 'integer' AND revision >= 1),
  updated_at INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS project_todo_priorities (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  priority_id TEXT NOT NULL UNIQUE,
  project_id TEXT NOT NULL REFERENCES project_spaces(project_id) ON DELETE CASCADE,
  name TEXT NOT NULL CHECK (name = trim(name) AND length(name) BETWEEN 1 AND 40),
  name_key TEXT NOT NULL CHECK (length(name_key) >= 1),
  color TEXT NOT NULL CHECK (color IN ('red', 'orange', 'yellow', 'green', 'blue', 'purple', 'gray')),
  position INTEGER NOT NULL CHECK (typeof(position) = 'integer' AND position >= 0),
  archived_at INTEGER,
  created_at INTEGER NOT NULL,
  updated_at INTEGER NOT NULL,
  UNIQUE (project_id, priority_id)
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_project_todo_priorities_active_name
  ON project_todo_priorities(project_id, name_key) WHERE archived_at IS NULL;
CREATE INDEX IF NOT EXISTS idx_project_todo_priorities_project
  ON project_todo_priorities(project_id, archived_at, position, priority_id);

CREATE TABLE IF NOT EXISTS project_todo_tags (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  tag_id TEXT NOT NULL UNIQUE,
  project_id TEXT NOT NULL REFERENCES project_spaces(project_id) ON DELETE CASCADE,
  name TEXT NOT NULL CHECK (name = trim(name) AND length(name) BETWEEN 1 AND 40),
  name_key TEXT NOT NULL CHECK (length(name_key) >= 1),
  color TEXT NOT NULL CHECK (color IN ('red', 'orange', 'yellow', 'green', 'blue', 'purple', 'gray')),
  archived_at INTEGER,
  created_at INTEGER NOT NULL,
  updated_at INTEGER NOT NULL,
  UNIQUE (project_id, tag_id)
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_project_todo_tags_active_name
  ON project_todo_tags(project_id, name_key) WHERE archived_at IS NULL;
CREATE INDEX IF NOT EXISTS idx_project_todo_tags_project
  ON project_todo_tags(project_id, archived_at, name_key, tag_id);

-- Keep the old formal parent name until the full copy has been checked.
CREATE TABLE project_todos_v34_new (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  todo_id TEXT NOT NULL UNIQUE,
  project_id TEXT NOT NULL REFERENCES project_spaces(project_id) ON DELETE CASCADE,
  creator_user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  assignee_user_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
  title TEXT NOT NULL,
  description TEXT NOT NULL DEFAULT '',
  status TEXT NOT NULL DEFAULT 'todo' CHECK (status IN ('todo', 'in_progress', 'done')),
  version INTEGER NOT NULL DEFAULT 1,
  created_at INTEGER NOT NULL,
  updated_at INTEGER NOT NULL,
  deleted_at INTEGER,
  description_format TEXT NOT NULL DEFAULT 'plain' CHECK (description_format IN ('plain', 'markdown')),
  start_date TEXT,
  due_date TEXT,
  priority_id TEXT,
  UNIQUE (project_id, todo_id),
  FOREIGN KEY (project_id, priority_id)
    REFERENCES project_todo_priorities(project_id, priority_id) ON DELETE NO ACTION,
  CONSTRAINT ck_project_todos_start_date CHECK (
    start_date IS NULL OR (
      length(start_date) = 10 AND
      start_date GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]' AND
      start_date BETWEEN '1900-01-01' AND '9999-12-31'
    )
  ),
  CONSTRAINT ck_project_todos_due_date CHECK (
    due_date IS NULL OR (
      length(due_date) = 10 AND
      due_date GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]' AND
      due_date BETWEEN '1900-01-01' AND '9999-12-31'
    )
  ),
  CONSTRAINT ck_project_todos_date_order CHECK (
    start_date IS NULL OR due_date IS NULL OR start_date <= due_date
  )
);

INSERT INTO project_todos_v34_new (
  id, todo_id, project_id, creator_user_id, assignee_user_id, title, description,
  status, version, created_at, updated_at, deleted_at, description_format
)
SELECT id, todo_id, project_id, creator_user_id, assignee_user_id, title, description,
  status, version, created_at, updated_at, deleted_at, description_format
FROM project_todos;

DROP TABLE project_todos;
ALTER TABLE project_todos_v34_new RENAME TO project_todos;

CREATE INDEX IF NOT EXISTS idx_project_todos_project_list
  ON project_todos(project_id, updated_at DESC, todo_id DESC);
CREATE INDEX IF NOT EXISTS idx_project_todos_status
  ON project_todos(project_id, status);
CREATE INDEX IF NOT EXISTS idx_project_todos_assignee
  ON project_todos(project_id, assignee_user_id);

CREATE TABLE IF NOT EXISTS project_todo_tag_links (
  project_id TEXT NOT NULL,
  todo_id TEXT NOT NULL,
  tag_id TEXT NOT NULL,
  PRIMARY KEY (project_id, todo_id, tag_id),
  FOREIGN KEY (project_id, todo_id)
    REFERENCES project_todos(project_id, todo_id) ON DELETE CASCADE,
  FOREIGN KEY (project_id, tag_id)
    REFERENCES project_todo_tags(project_id, tag_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_project_todo_tag_links_tag
  ON project_todo_tag_links(project_id, tag_id, todo_id);

UPDATE _schema_version SET version = 34;
