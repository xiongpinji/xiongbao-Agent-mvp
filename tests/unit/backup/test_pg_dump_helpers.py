from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from octop.infra.backup import pg_dump, system_archive
from octop.infra.errors import OctopError


def test_require_tool_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(pg_dump.shutil, "which", lambda _name: None)
    with pytest.raises(OctopError, match="pg_dump not found"):
        pg_dump._require_tool("pg_dump")


def test_dump_postgres_can_exclude_chat_table_data(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    called: list[str] = []
    monkeypatch.setattr(pg_dump, "_require_tool", lambda _name: "pg_dump")

    def fake_run(command, **_kwargs):
        called.extend(command)
        return SimpleNamespace(returncode=0, stderr="", stdout="")

    monkeypatch.setattr(pg_dump.subprocess, "run", fake_run)
    pg_dump.dump_postgres(
        "postgresql://example",
        tmp_path / "backup.dump",
        exclude_table_data=("sessions", "threads"),
    )

    assert called.count("--exclude-table-data") == 2
    assert "sessions" in called
    assert "threads" in called


class _RestorePool:
    dialect = "postgresql"

    def __init__(self, error: BaseException | None = None) -> None:
        self.statements: list[str] = []
        self.events: list[str] = []
        self.error = error

    @contextmanager
    def transaction(self):
        self.events.append("begin")
        try:
            yield self
        except BaseException:
            self.events.append("rollback")
            raise
        else:
            self.events.append("commit")

    def execute(self, sql: str, _params: Any = None) -> None:
        self.statements.append(sql)
        if self.error is not None:
            raise self.error


def _restore_tool(
    monkeypatch: pytest.MonkeyPatch,
    *,
    inspect_exit: int = 0,
    restore_exit: int = 0,
    pool: _RestorePool,
) -> list[list[str]]:
    calls: list[list[str]] = []
    monkeypatch.setattr(pg_dump, "_require_tool", lambda _name: "synthetic-pg-restore")

    def fake_run(command, **_kwargs):
        calls.append(command)
        if "--list" in command:
            exit_code = inspect_exit
        else:
            # A restore process must never wait on its own cleanup transaction.
            assert not pool.events or pool.events[-1] == "commit"
            exit_code = restore_exit
        return SimpleNamespace(
            returncode=exit_code,
            stderr="PRIVATE_DSN_SENTINEL PRIVATE_PATH_SENTINEL",
            stdout="PRIVATE_SQL_SENTINEL",
        )

    monkeypatch.setattr(pg_dump.subprocess, "run", fake_run)
    return calls


@pytest.mark.parametrize("returncode", [1, 2, -9])
def test_restore_rejects_every_nonzero_exit_without_exposing_tool_output(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, returncode: int
) -> None:
    pool = _RestorePool()
    _restore_tool(monkeypatch, pool=pool, restore_exit=returncode)
    with pytest.raises(OctopError) as raised:
        pg_dump.restore_postgres(
            "PRIVATE_DSN_SENTINEL", tmp_path / "PRIVATE_PATH_SENTINEL", pool=pool, schema_version=35
        )
    assert raised.value.details == {
        "reason": "backup_restore_tool_failed",
        "stage": "restore",
        "exit_code": returncode,
    }
    assert "PRIVATE" not in str(raised.value)
    assert pool.statements == []


def test_invalid_dump_is_rejected_before_any_database_cleanup(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    pool = _RestorePool()
    calls = _restore_tool(monkeypatch, pool=pool, inspect_exit=1)
    with pytest.raises(OctopError) as raised:
        pg_dump.restore_postgres(
            "synthetic", tmp_path / "invalid.dump", pool=pool, schema_version=33
        )
    assert raised.value.details["stage"] == "inspect"
    assert raised.value.details["exit_code"] == 1
    assert len(calls) == 1 and "--list" in calls[0]
    assert pool.events == [] and pool.statements == []


@pytest.mark.parametrize("version", [33, 34, 35])
def test_old_dump_cleanup_is_schema_qualified_bounded_and_commits_before_restore(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, version: int
) -> None:
    pool = _RestorePool()
    calls = _restore_tool(monkeypatch, pool=pool)
    pg_dump.restore_postgres(
        "synthetic", tmp_path / "valid.dump", pool=pool, schema_version=version
    )
    assert len(calls) == 2 and "--list" in calls[0]
    assert "--single-transaction" in calls[1]
    assert "--clean" in calls[1] and "--if-exists" in calls[1] and "--no-owner" in calls[1]
    assert "--no-acl" not in calls[1] and "--create" not in calls[1]
    sql = "\n".join(pool.statements)
    assert "CASCADE" not in sql and "DROP SCHEMA" not in sql and "DROP DATABASE" not in sql
    if version < 35:
        assert "public.project_todo_view_state" in sql and "public.project_todo_views" in sql
        assert pool.events == ["begin", "commit"]
        assert all("RESTRICT" in line for line in pool.statements if line.startswith("DROP TABLE"))
    else:
        assert pool.events == [] and sql == ""
    if version < 34:
        assert "public.project_todo_tag_links" in sql
        assert "public.project_todo_priorities" in sql and "public.project_todo_tags" in sql
        assert "public.project_todo_catalog_state" in sql
        assert "ALTER TABLE IF EXISTS public.project_todos" in sql
        assert "DROP CONSTRAINT IF EXISTS fk_project_todos_priority" in sql
    else:
        assert "project_todo_priorities" not in sql and "fk_project_todos_priority" not in sql


@pytest.mark.parametrize("error_type", [RuntimeError, KeyboardInterrupt])
def test_cleanup_failure_rolls_back_and_never_starts_the_restore_process(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, error_type: type[BaseException]
) -> None:
    pool = _RestorePool(error_type("PRIVATE_DATABASE_SENTINEL"))
    calls = _restore_tool(monkeypatch, pool=pool)
    expected = KeyboardInterrupt if error_type is KeyboardInterrupt else OctopError
    with pytest.raises(expected) as raised:
        pg_dump.restore_postgres("synthetic", tmp_path / "valid.dump", pool=pool, schema_version=33)
    assert len(calls) == 1 and "--list" in calls[0]
    assert pool.events == ["begin", "rollback"]
    if error_type is RuntimeError:
        assert raised.value.details == {"reason": "backup_restore_cleanup_failed"}
        assert "PRIVATE" not in str(raised.value) and raised.value.__cause__ is None


@pytest.mark.parametrize("version", [-1, True, "33"])
def test_invalid_schema_version_fails_before_tool_or_database_access(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, version: Any
) -> None:
    pool = _RestorePool()
    calls = _restore_tool(monkeypatch, pool=pool)
    with pytest.raises(OctopError) as raised:
        pg_dump.restore_postgres(
            "synthetic", tmp_path / "valid.dump", pool=pool, schema_version=version
        )
    assert raised.value.details == {"reason": "backup_restore_schema_version_invalid"}
    assert calls == [] and pool.events == []


class _ReadVersionPool:
    def __init__(self, rows: list[tuple[Any, ...]], error: BaseException | None = None) -> None:
        self.rows = rows
        self.error = error
        self.statements: list[str] = []

    @contextmanager
    def connect(self):
        yield self

    def execute(self, statement: str):
        self.statements.append(statement)
        if self.error is not None:
            raise self.error
        return SimpleNamespace(fetchall=lambda: self.rows)


def test_preimage_version_uses_successfully_read_stored_watermark(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pool = _ReadVersionPool([(34,)])

    def forbidden_fallback(*_args):
        raise AssertionError("Runtime maximum or error-swallowing fallback is not the preimage")

    monkeypatch.setattr(system_archive, "_current_version", forbidden_fallback)
    monkeypatch.setattr(system_archive, "_max_discovered_version", forbidden_fallback)
    assert system_archive._postgres_preimage_schema_version(pool) == 34
    assert pool.statements == ["SELECT version FROM public._schema_version"]


@pytest.mark.parametrize("rows", [[], [(33,), (34,)], [("34",)], [(True,)], [(-1,)]])
def test_preimage_invalid_watermark_cannot_be_misreported_as_zero(rows) -> None:
    pool = _ReadVersionPool(rows)
    with pytest.raises(OctopError) as raised:
        system_archive._postgres_preimage_schema_version(pool)
    assert raised.value.details == {"reason": "backup_preimage_schema_version_unavailable"}


@pytest.mark.parametrize("error_type", [RuntimeError, KeyboardInterrupt])
def test_preimage_read_error_prevents_replacement_and_preserves_interrupt(error_type) -> None:
    pool = _ReadVersionPool([], error_type("PRIVATE_READ_SENTINEL"))
    expected = KeyboardInterrupt if error_type is KeyboardInterrupt else OctopError
    with pytest.raises(expected) as raised:
        system_archive._postgres_preimage_schema_version(pool)
    if error_type is RuntimeError:
        assert "PRIVATE" not in str(raised.value) and raised.value.__cause__ is None
        assert raised.value.details == {"reason": "backup_preimage_schema_version_unavailable"}
