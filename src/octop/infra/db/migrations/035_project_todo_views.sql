-- Schema v35 (SQLite): shared plan views and full Unicode query keys.
-- migrate.py applies every statement, Python backfill and seed on the same
-- connection under BEGIN IMMEDIATE, with the watermark written last.

ALTER TABLE project_todos ADD COLUMN title_search_key TEXT NOT NULL DEFAULT '';
ALTER TABLE users ADD COLUMN project_plan_display_sort_key TEXT NOT NULL DEFAULT '';

CREATE TABLE IF NOT EXISTS project_todo_views (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  view_id TEXT NOT NULL UNIQUE,
  project_id TEXT NOT NULL REFERENCES project_spaces(project_id) ON DELETE CASCADE,
  name TEXT NOT NULL CHECK (name = trim(name) AND length(name) BETWEEN 1 AND 40),
  name_key TEXT NOT NULL CHECK (length(name_key) >= 1),
  view_type TEXT NOT NULL CHECK (view_type IN ('list', 'table', 'board', 'gantt', 'calendar')),
  definition_json TEXT NOT NULL CHECK (
    CASE WHEN json_valid(definition_json) THEN json_type(definition_json) = 'object' ELSE 0 END
  ),
  version INTEGER NOT NULL DEFAULT 1 CHECK (typeof(version) = 'integer' AND version >= 1),
  position INTEGER NOT NULL CHECK (typeof(position) = 'integer' AND position >= 0),
  archived_at INTEGER,
  created_at INTEGER NOT NULL,
  updated_at INTEGER NOT NULL,
  UNIQUE (project_id, view_id)
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_project_todo_views_active_name
  ON project_todo_views(project_id, name_key) WHERE archived_at IS NULL;
CREATE INDEX IF NOT EXISTS idx_project_todo_views_project
  ON project_todo_views(project_id, archived_at, position, view_id);

CREATE TABLE IF NOT EXISTS project_todo_view_state (
  project_id TEXT NOT NULL PRIMARY KEY REFERENCES project_spaces(project_id) ON DELETE CASCADE,
  revision INTEGER NOT NULL DEFAULT 1 CHECK (typeof(revision) = 'integer' AND revision >= 1),
  default_view_id TEXT,
  updated_at INTEGER NOT NULL,
  FOREIGN KEY (project_id, default_view_id)
    REFERENCES project_todo_views(project_id, view_id) ON DELETE NO ACTION
);

UPDATE _schema_version SET version = 35;

