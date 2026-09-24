-- Schema v24 (SQLite): project task card shares — PS-05B slice 1.
-- A task owner may grant same-project members revocable read access to the
-- task CARD SUMMARY only — never history, files, streams, or read status.
-- Grants are keyed (project_id, thread_id, grantee_user_id): revoke stamps
-- revoked_at (the row survives), regrant reactivates the same row. Removing
-- the grantee's membership, removing the owner's membership (which detaches
-- the link), deleting the link/thread, or deleting the project all cascade
-- the share away; deleting the granting user only clears granted_by_user_id.
-- This is a NEW migration — not folded into 023 — because databases already
-- at watermark 23 (pushed or local) would otherwise skip this DDL forever.

-- Composite foreign keys need a non-partial unique index on the parent
-- columns, created BEFORE the child table. (project_id, thread_id) is
-- already unique de facto — thread_id is globally UNIQUE since 021 — so
-- this index can never fail on existing data.
CREATE UNIQUE INDEX IF NOT EXISTS idx_project_task_links_project_thread
  ON project_task_links(project_id, thread_id);

CREATE TABLE IF NOT EXISTS project_task_shares (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  project_id TEXT NOT NULL REFERENCES project_spaces(project_id) ON DELETE CASCADE,
  thread_id TEXT NOT NULL REFERENCES threads(thread_id) ON DELETE CASCADE,
  grantee_user_id INTEGER NOT NULL,
  granted_by_user_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
  role TEXT NOT NULL DEFAULT 'reader' CHECK (role IN ('reader')),
  granted_at INTEGER NOT NULL CHECK (granted_at >= 0),
  revoked_at INTEGER CHECK (revoked_at IS NULL OR revoked_at >= granted_at),
  UNIQUE (project_id, thread_id, grantee_user_id),
  FOREIGN KEY (project_id, thread_id)
    REFERENCES project_task_links(project_id, thread_id) ON DELETE CASCADE,
  FOREIGN KEY (project_id, grantee_user_id)
    REFERENCES project_members(project_id, user_id) ON DELETE CASCADE
);

-- Active-grant read paths: "cards shared with me" (grantee prefix) and
-- "who can see this card" (thread prefix). Revoked rows stay in the table
-- but out of both indexes.
CREATE INDEX IF NOT EXISTS idx_project_task_shares_active_grantee
  ON project_task_shares(project_id, grantee_user_id) WHERE revoked_at IS NULL;

CREATE INDEX IF NOT EXISTS idx_project_task_shares_active_thread
  ON project_task_shares(project_id, thread_id) WHERE revoked_at IS NULL;

UPDATE _schema_version SET version = 24;
