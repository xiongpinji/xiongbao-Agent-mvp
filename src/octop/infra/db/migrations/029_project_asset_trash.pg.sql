-- Schema v29 (PostgreSQL): 042 recoverable project-asset trash.
-- Project asset nodes gain a soft-delete layer: ``deleted_at`` marks a node
-- as trashed, ``deleted_by`` records the actor (SET NULL when that user is
-- deleted, mirroring ``created_by``), ``trash_root_id`` binds every row of
-- one delete operation to the trashed target (the root of that trash tree),
-- and ``original_name_key`` stores the root's pre-trash folded name so the
-- display name can be released for re-creation and later restored exactly.
-- ``project_asset_versions`` and stored object bytes are never touched by
-- trashing: restore must return identical node ids, version ids, current
-- pointers, and bytes. The index serves the per-project trash-root listing
-- ordered by ``deleted_at``. All four columns stay nullable so every
-- pre-existing (active) row keeps working unchanged. SQLite applies this via
-- migrate.py::_ensure_project_asset_trash_v29 so a stopped upgrade replays
-- safely; this file is the canonical shape of that helper.
ALTER TABLE project_asset_nodes ADD COLUMN deleted_at INTEGER;

ALTER TABLE project_asset_nodes ADD COLUMN deleted_by INTEGER REFERENCES users(id) ON DELETE SET NULL;

ALTER TABLE project_asset_nodes ADD COLUMN trash_root_id TEXT;

ALTER TABLE project_asset_nodes ADD COLUMN original_name_key TEXT;

CREATE INDEX IF NOT EXISTS idx_project_asset_nodes_trash
  ON project_asset_nodes(project_id, trash_root_id, deleted_at);

UPDATE _schema_version SET version = 29;
