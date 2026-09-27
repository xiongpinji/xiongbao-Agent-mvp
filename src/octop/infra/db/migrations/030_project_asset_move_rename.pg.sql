-- Schema v30 (PostgreSQL): freeze each independent trash root's deletion-time path.
ALTER TABLE project_asset_nodes ADD COLUMN deleted_from_path TEXT;

WITH RECURSIVE paths(project_id, node_id, path) AS (
  SELECT project_id, node_id, ''::text FROM project_asset_nodes
  WHERE parent_node_id IS NULL
  UNION ALL
  SELECT child.project_id, child.node_id,
    CASE WHEN parent.path = '' THEN child.name
         ELSE parent.path || '/' || child.name END
  FROM project_asset_nodes child
  JOIN paths parent ON parent.project_id = child.project_id
                   AND parent.node_id = child.parent_node_id
)
UPDATE project_asset_nodes AS node
SET deleted_from_path = paths.path
FROM paths
WHERE paths.project_id = node.project_id
  AND paths.node_id = node.node_id
  AND node.deleted_at IS NOT NULL AND node.trash_root_id = node.node_id
  AND node.deleted_from_path IS NULL;

UPDATE _schema_version SET version = 30;
