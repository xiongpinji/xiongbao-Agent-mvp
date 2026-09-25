-- Schema v26 (PostgreSQL): immutable project-task instruction snapshots — PS-05C/PS-07A.
-- A project member creating a task (link source='project') captures the
-- project's instructions at that instant in the SAME transaction that inserts
-- the thread, history projection, and task link. The snapshot is server-written
-- and never updated: later project instruction edits only affect tasks created
-- afterwards. thread_id is the PK and cascades from project_task_links, so
-- detach / member removal / project or thread deletion clears the snapshot
-- while the owner's original private thread stays. Manual links (021) never
-- gain a row here. instructions_sha256 is the SHA-256 hex digest of the
-- captured UTF-8 text; snapshot_version 1 is the only supported mode.
CREATE TABLE IF NOT EXISTS project_task_contexts (
  thread_id TEXT PRIMARY KEY REFERENCES project_task_links(thread_id) ON DELETE CASCADE,
  project_id TEXT NOT NULL REFERENCES project_spaces(project_id) ON DELETE CASCADE,
  owner_user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  instructions_snapshot TEXT NOT NULL,
  snapshot_version INTEGER NOT NULL DEFAULT 1 CHECK (snapshot_version >= 1),
  instructions_sha256 TEXT NOT NULL CHECK (length(instructions_sha256) = 64),
  captured_at INTEGER NOT NULL CHECK (captured_at >= 0)
);
CREATE INDEX IF NOT EXISTS idx_project_task_contexts_project_owner
  ON project_task_contexts(project_id, owner_user_id);
UPDATE _schema_version SET version = 26;