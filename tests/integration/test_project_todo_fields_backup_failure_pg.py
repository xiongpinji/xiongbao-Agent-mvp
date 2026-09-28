"""Restore compensation on the same isolated PG lease as the C1 real gates."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import pytest

from octop.config import DatabaseConfig
from octop.infra.backup import system_archive
from octop.infra.db.migrate import _max_discovered_version
from octop.infra.db.pool import PostgresPool
from octop.infra.errors import OctopError
from octop.infra.utils.paths import PathLayout
from tests.integration.test_project_todo_fields_pg import postgres_pair as postgres_pair
from tests.unit.backup.test_project_todo_fields_archive import _seed_archive_rows
from tests.unit.backup.test_project_todo_fields_archive_failure import (
    _private_tree,
    _seed_plan_fields,
)


def _identifier(value: str) -> str:
    return '"' + value.replace('"', '""') + '"'


def _database_state(pool: PostgresPool) -> dict[str, Any]:
    """Compare every public row, sequence value and FK/constraint definition."""
    with pool.connect() as conn:
        tables = conn.execute(
            "SELECT tablename FROM pg_catalog.pg_tables WHERE schemaname='public' "
            "ORDER BY tablename"
        ).fetchall()
        rows = {
            str(table[0]): tuple(
                sorted(
                    (
                        tuple(row[key] for key in row)
                        for row in conn.execute(
                            f"SELECT * FROM public.{_identifier(str(table[0]))}"
                        )
                    ),
                    key=repr,
                )
            )
            for table in tables
        }
        sequences = conn.execute(
            "SELECT sequencename FROM pg_catalog.pg_sequences WHERE schemaname='public' "
            "ORDER BY sequencename"
        ).fetchall()
        values = {}
        for sequence in sequences:
            value = conn.execute(
                "SELECT last_value,is_called FROM public." + _identifier(str(sequence[0]))
            ).fetchone()
            values[str(sequence[0])] = (value[0], value[1])
        constraints = tuple(
            (row[0], row[1], row[2])
            for row in conn.execute(
                "SELECT c.relname,k.conname,pg_get_constraintdef(k.oid) "
                "FROM pg_catalog.pg_constraint k JOIN pg_catalog.pg_class c ON c.oid=k.conrelid "
                "JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace "
                "WHERE n.nspname='public' ORDER BY c.relname,k.conname"
            )
        )
    return {"rows": rows, "sequences": values, "constraints": constraints}


@pytest.mark.parametrize("interrupted", [False, True], ids=["migration-error", "interrupt"])
def test_real_pg_failed_restore_recovers_current_database_and_private_bytes(
    postgres_pair: dict[str, Any],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    interrupted: bool,
) -> None:
    env = postgres_pair
    pool, context = env["first"], env["context"]
    source = PathLayout(tmp_path / "archive")
    project_id, todo_id, _ = _seed_archive_rows(pool, source)
    _seed_plan_fields(pool, source, project_id, todo_id, "archive")
    archive_state = _database_state(pool)
    archive_tree = _private_tree(source)
    config = DatabaseConfig(
        driver="postgresql",
        host="127.0.0.1",
        port=context["port"],
        database=env["database"],
        user=context["user"],
    )
    monkeypatch.setenv("PATH", "/usr/lib/postgresql/18/bin:" + os.environ.get("PATH", ""))
    for key in tuple(os.environ):
        if key.startswith("PG"):
            monkeypatch.delenv(key, raising=False)
    archive = tmp_path / "plan-034.tar.gz"
    system_archive.create_system_backup(
        paths=source, agent_rows=[], pool=pool, db_config=config, dest=archive
    )
    target = PathLayout(tmp_path / "current")
    _seed_plan_fields(pool, target, project_id, todo_id, "current")
    before_database = _database_state(pool)
    before_tree = _private_tree(target)
    assert before_database != archive_state and before_tree != archive_tree
    real_migrate = system_archive.run_migrations
    reached_real_upgrade = []

    def fail_after_real_upgrade(target_pool: PostgresPool) -> None:
        real_migrate(target_pool)
        with target_pool.connect() as conn:
            assert conn.execute("SELECT version FROM _schema_version").fetchone()[
                0
            ] == _max_discovered_version("postgresql")
            assert (
                conn.execute(
                    "SELECT title FROM project_todos WHERE todo_id=?", (todo_id,)
                ).fetchone()[0]
                == "archive todo"
            )
        reached_real_upgrade.append(True)
        if interrupted:
            raise KeyboardInterrupt("injected after actual PG restore and run_migrations")
        raise RuntimeError("injected after actual PG restore and run_migrations")

    monkeypatch.setattr(system_archive, "run_migrations", fail_after_real_upgrade)
    with pytest.raises(
        KeyboardInterrupt if interrupted else OctopError,
        match="injected after actual PG restore and run_migrations",
    ):
        system_archive.restore_system_backup(
            archive, paths=target, pool=pool, db_config=config, restore_config=False
        )
    assert reached_real_upgrade == [True]
    assert _database_state(pool) == before_database
    assert _private_tree(target) == before_tree
