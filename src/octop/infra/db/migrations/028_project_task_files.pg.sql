-- Schema v28 (PostgreSQL): 030A project-task file runtimes (B1 persistence).
-- ``agents.runtime_kind`` is the database-trusted internal marker: every
-- pre-existing row and every public creation path stays ``standard``, and
-- ``project_task_files`` is written only by the dedicated AgentRepo quota
-- insertion method (fixed 8-per-owner / 64-per-instance limits, counted and
-- inserted inside one serialized write transaction). ``config_json`` is
-- opaque free text and can never forge this column.
-- ``project_task_contexts`` gains ``mode`` (default ``chat`` keeps every old
-- row and every manual-link task valid) plus nullable ``source_expert_id``
-- and ``runtime_agent_id`` columns; the ``mode`` CHECK requires BOTH ids on
-- ``files`` rows, and a partial unique index binds at most one context per
-- runtime Agent. The id columns deliberately carry no REFERENCES: a
-- detached private file task must survive hard deletion of its source
-- expert, and runtime cleanup ordering belongs to the 030A delete flows,
-- not to cascade timing. ``mode`` is added last because its CHECK references
-- the id columns. SQLite applies this via migrate.py::_ensure_project_task_
-- files_v28 so a stopped upgrade replays safely; this file is the canonical
-- shape of that helper.
ALTER TABLE agents ADD COLUMN runtime_kind TEXT NOT NULL DEFAULT 'standard'
  CHECK (runtime_kind IN ('standard', 'project_task_files'));

ALTER TABLE project_task_contexts ADD COLUMN source_expert_id TEXT;

ALTER TABLE project_task_contexts ADD COLUMN runtime_agent_id TEXT;

ALTER TABLE project_task_contexts ADD COLUMN mode TEXT NOT NULL DEFAULT 'chat'
  CHECK (mode IN ('chat', 'files'))
  CHECK (mode <> 'files' OR (source_expert_id IS NOT NULL AND runtime_agent_id IS NOT NULL));

CREATE UNIQUE INDEX IF NOT EXISTS idx_project_task_contexts_runtime_agent
  ON project_task_contexts(runtime_agent_id) WHERE runtime_agent_id IS NOT NULL;

UPDATE _schema_version SET version = 28;
