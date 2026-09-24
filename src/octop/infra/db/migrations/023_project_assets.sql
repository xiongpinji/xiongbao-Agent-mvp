-- Schema v23 (SQLite): secure project assets (folders + file versions) — PS-06A.
-- project_asset_nodes is the folder/file tree: one hidden root folder per
-- project (parent_node_id IS NULL, enforced by a partial unique index),
-- display names stored NFC-trimmed with name_key = casefold(name) so Foo and
-- foo collide under the same parent via UNIQUE (project_id, parent_node_id,
-- name_key). Children carry the composite FK (project_id, parent_node_id) so
-- a node can never be re-parented across projects, and folder deletion
-- cascades to descendants. project_asset_versions stores immutable file
-- versions; object_key is server-generated (project id + version id) and the
-- display filename never appears in it. Exactly one current version per file
-- node is enforced by a partial unique index on is_current = 1. Actor columns
-- SET NULL on user deletion; project deletion cascades all three levels.

CREATE TABLE IF NOT EXISTS project_asset_nodes (
  node_id TEXT NOT NULL,
  project_id TEXT NOT NULL REFERENCES project_spaces(project_id) ON DELETE CASCADE,
  parent_node_id TEXT,
  kind TEXT NOT NULL CHECK (kind IN ('file', 'folder')),
  name TEXT NOT NULL,
  name_key TEXT NOT NULL,
  created_by INTEGER REFERENCES users(id) ON DELETE SET NULL,
  created_at INTEGER NOT NULL,
  updated_at INTEGER NOT NULL,
  UNIQUE (project_id, node_id),
  UNIQUE (project_id, parent_node_id, name_key),
  FOREIGN KEY (project_id, parent_node_id)
    REFERENCES project_asset_nodes(project_id, node_id) ON DELETE CASCADE
);

-- Exactly one hidden root folder per project.
CREATE UNIQUE INDEX IF NOT EXISTS idx_project_asset_nodes_root
  ON project_asset_nodes(project_id) WHERE parent_node_id IS NULL;

-- Listing children of a folder inside one project.
CREATE INDEX IF NOT EXISTS idx_project_asset_nodes_parent
  ON project_asset_nodes(project_id, parent_node_id);

CREATE TABLE IF NOT EXISTS project_asset_versions (
  version_id TEXT NOT NULL,
  project_id TEXT NOT NULL REFERENCES project_spaces(project_id) ON DELETE CASCADE,
  node_id TEXT NOT NULL,
  object_key TEXT NOT NULL,
  size_bytes INTEGER NOT NULL CHECK (size_bytes >= 0),
  sha256 TEXT NOT NULL,
  media_type TEXT,
  uploaded_by INTEGER REFERENCES users(id) ON DELETE SET NULL,
  created_at INTEGER NOT NULL,
  is_current INTEGER NOT NULL DEFAULT 0 CHECK (is_current IN (0, 1)),
  UNIQUE (project_id, version_id),
  FOREIGN KEY (project_id, node_id)
    REFERENCES project_asset_nodes(project_id, node_id) ON DELETE CASCADE
);

-- Exactly one current version per file node.
CREATE UNIQUE INDEX IF NOT EXISTS idx_project_asset_versions_current
  ON project_asset_versions(node_id) WHERE is_current = 1;

-- Version lookup inside one project.
CREATE INDEX IF NOT EXISTS idx_project_asset_versions_node
  ON project_asset_versions(project_id, node_id);

UPDATE _schema_version SET version = 23;
