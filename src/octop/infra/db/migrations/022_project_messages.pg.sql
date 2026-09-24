-- Schema v22 (PostgreSQL): project messages + activity-feed index — PS-03A.
-- project_messages stores plain-text member messages: body is the only
-- content column (1-4000 characters, CHECK-enforced) and author_user_id
-- nulls out when the author is deleted so the message survives with a
-- safe null actor. message_id is referenced by project_events.object_id
-- for project.message_created events; the event payload stays '{}', so
-- message text never lives in project_events. The new project_events
-- index serves the activity feed's (created_at DESC, id DESC) seek paging.

CREATE TABLE IF NOT EXISTS project_messages (
  message_id TEXT PRIMARY KEY,
  project_id TEXT NOT NULL REFERENCES project_spaces(project_id) ON DELETE CASCADE,
  author_user_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
  body TEXT NOT NULL CHECK (length(body) BETWEEN 1 AND 4000),
  created_at INTEGER NOT NULL
);

-- Message lookup/paging inside a project by (created_at, message_id).
CREATE INDEX IF NOT EXISTS idx_project_messages_project_time
  ON project_messages(project_id, created_at, message_id);

-- Activity-feed seek: whitelist filter, then (created_at DESC, id DESC) pages.
CREATE INDEX IF NOT EXISTS idx_project_events_project_activity
  ON project_events(project_id, created_at DESC, id DESC);

UPDATE _schema_version SET version = 22;
