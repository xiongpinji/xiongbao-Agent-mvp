-- Schema v25 (PostgreSQL): project task conversation TEXT grants — PS-05B-2A.
-- A task owner may grant a named member who ALREADY holds an active 024
-- card share separate, explicit, revocable read-only access to the task
-- conversation TEXT. Text grants are keyed (project_id, thread_id,
-- grantee_user_id): revoke DELETES the row and regrant is a brand-new row
-- (fresh confirmation), never a reactivation. The composite FK onto the
-- 024 unique triple means every card-share cascade (grantee membership
-- removal, owner removal/detach, link/thread/project delete) removes text
-- access automatically, and card revocation deletes the matching row in
-- the same transaction that stamps revoked_at. Card regrant alone can
-- never revive text access, and no existing 024 row is backfilled.
-- Deleting the granting user only clears granted_by_user_id.
CREATE TABLE IF NOT EXISTS project_task_content_grants (
  project_id TEXT NOT NULL,
  thread_id TEXT NOT NULL,
  grantee_user_id INTEGER NOT NULL,
  granted_by_user_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
  granted_at INTEGER NOT NULL CHECK (granted_at >= 0),
  PRIMARY KEY (project_id, thread_id, grantee_user_id),
  FOREIGN KEY (project_id, thread_id, grantee_user_id)
    REFERENCES project_task_shares(project_id, thread_id, grantee_user_id) ON DELETE CASCADE
);

UPDATE _schema_version SET version = 25;
