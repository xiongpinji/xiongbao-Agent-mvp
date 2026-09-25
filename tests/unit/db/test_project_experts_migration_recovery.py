"""A repeated v27 upgrade must recover after DDL applied before watermark."""

from pathlib import Path

from octop.infra.db.migrate import _max_discovered_version, run_migrations
from octop.infra.db.pool import SqlitePool


def test_project_experts_migration_recovers_before_watermark(tmp_path: Path) -> None:
    pool = SqlitePool(tmp_path / "partial-027.db")
    run_migrations(pool)
    assert _max_discovered_version("sqlite") >= 27

    # SQLite executes DDL independently; a process can stop after schema
    # changes but before the _schema_version write. Replaying 027 must work.
    with pool.connect() as conn:
        conn.execute("UPDATE _schema_version SET version = 26")

    run_migrations(pool)

    with pool.connect() as conn:
        assert conn.execute("SELECT version FROM _schema_version").fetchone()[0] == 27
        project_columns = {row["name"] for row in conn.execute("PRAGMA table_info(project_spaces)")}
        context_columns = {
            row["name"] for row in conn.execute("PRAGMA table_info(project_task_contexts)")
        }
        assert "experts_revision" in project_columns
        assert "expert_selection_revision" in context_columns
        assert (
            conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='project_experts'"
            ).fetchone()
            is not None
        )
