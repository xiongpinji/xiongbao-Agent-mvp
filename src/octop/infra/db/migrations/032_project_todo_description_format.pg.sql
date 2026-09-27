-- Schema v32 (PostgreSQL): distinguish legacy literal text from opted-in Markdown.
ALTER TABLE project_todos ADD COLUMN description_format TEXT NOT NULL DEFAULT 'plain'
  CHECK (description_format IN ('plain', 'markdown'));

UPDATE _schema_version SET version = 32;
