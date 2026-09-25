-- Schema v27 (SQLite): versioned project expert selection — PS-07 expert gate.
-- owner/admin save an ordered list of globally shared, enabled single experts.
-- experts_revision versions ONLY admin PUT writes: unshare/disable/hard-delete
-- change the effective available set without bumping it, so GET re-reads live
-- rows and masks unavailable profiles with name/description NULL. The task
-- context column stores the config revision captured at creation time (0 for
-- rows predating 027). project_experts rows cascade with the project; a hard
-- agent deletion cascades them away, and deleting the adding user only clears
-- added_by. This is a NEW migration — not folded into 026 — because databases
-- already at watermark 26 (pushed or local) would otherwise skip this DDL.
ALTER TABLE project_spaces ADD COLUMN experts_revision INTEGER NOT NULL DEFAULT 0;

CREATE TABLE IF NOT EXISTS project_experts (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  project_id TEXT NOT NULL REFERENCES project_spaces(project_id) ON DELETE CASCADE,
  agent_id TEXT NOT NULL REFERENCES agents(agent_id) ON DELETE CASCADE,
  sort_order INTEGER NOT NULL CHECK (sort_order >= 0),
  added_by INTEGER REFERENCES users(id) ON DELETE SET NULL,
  added_at INTEGER NOT NULL CHECK (added_at >= 0),
  UNIQUE (project_id, agent_id)
);

CREATE INDEX IF NOT EXISTS idx_project_experts_project
  ON project_experts(project_id, sort_order);

ALTER TABLE project_task_contexts ADD COLUMN expert_selection_revision INTEGER NOT NULL DEFAULT 0;

UPDATE _schema_version SET version = 27;