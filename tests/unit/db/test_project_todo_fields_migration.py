"""PS-04C schema upgrades must preserve the full PS-04B attachment chain."""

from __future__ import annotations

import re
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from importlib import import_module
from pathlib import Path
from typing import Any

import pytest

from octop.infra.db.migrate import _discover, _max_discovered_version, run_migrations
from octop.infra.db.pool import SqlitePool

MIGRATIONS = Path(__file__).resolve().parents[3] / "src/octop/infra/db/migrations"
TABLES = (
    "project_todos",
    "project_todo_comments",
    "project_todo_comment_images",
    "project_todo_comment_image_usage",
)
CATALOG_TABLES = (
    "project_todo_catalog_state",
    "project_todo_priorities",
    "project_todo_tags",
    "project_todo_tag_links",
)
OLD_COLUMNS = (
    "id,todo_id,project_id,creator_user_id,assignee_user_id,title,description,status,"
    "version,created_at,updated_at,deleted_at,description_format"
)
DEFAULTS = [("紧急", "red", 0), ("高", "orange", 1), ("中", "blue", 2), ("低", "gray", 3)]


def _run_historical_034(pool: SqlitePool, monkeypatch: pytest.MonkeyPatch) -> None:
    migration = import_module("octop.infra.db.migrate")
    discover = migration._discover
    with monkeypatch.context() as patch:
        patch.setattr(
            migration,
            "_discover",
            lambda dialect: [(v, p) for v, p in discover(dialect) if v <= 34],
        )
        run_migrations(pool)


def _projects(conn: Any) -> None:
    conn.execute(
        "INSERT INTO users(id,username,password_hash,role,created_at) "
        "VALUES (1,'migration-owner','test','user',11)"
    )
    for project_id, archived in (("p1", 0), ("p2", 1)):
        conn.execute(
            "INSERT INTO project_spaces(project_id,creator_user_id,name,archived,created_at,"
            "updated_at) VALUES (?,1,?,?,11,12)",
            (project_id, project_id, archived),
        )


def _snapshot(conn: Any) -> dict[str, list[tuple[Any, ...]]]:
    return {
        table: [tuple(row) for row in conn.execute(f"SELECT * FROM {table} ORDER BY 1")]
        for table in TABLES
    }


@pytest.fixture
def old_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[SqlitePool]:
    """Actually build the old 033 schema, rather than downgrade a fresh 034 DB."""
    migration = import_module("octop.infra.db.migrate")
    discover = migration._discover
    pool = SqlitePool(tmp_path / "old-033.db")
    with monkeypatch.context() as patch:
        patch.setattr(
            migration,
            "_discover",
            lambda dialect: [(v, p) for v, p in discover(dialect) if v <= 33],
        )
        run_migrations(pool)
    with pool.transaction() as conn:
        assert conn.execute("SELECT version FROM _schema_version").fetchone()[0] == 33
        _projects(conn)
        for pk, todo_id, project_id, version, deleted, fmt in (
            (17, "t1", "p1", 7, None, "plain"),
            (23, "t2", "p2", 3, 99, "markdown"),
        ):
            conn.execute(
                f"INSERT INTO project_todos({OLD_COLUMNS}) VALUES (?,?,?,1,1,?,?,?, ?,11,12,?,?)",
                (
                    pk,
                    todo_id,
                    project_id,
                    "原待办",
                    "**原文**\n@[literal]",
                    "done",
                    version,
                    deleted,
                    fmt,
                ),
            )
            conn.execute(
                "INSERT INTO project_todo_comments VALUES (?,?,1,?,?,?,?)",
                (f"c{pk}", todo_id, "原评论", f"request-{pk}", "a" * 64, 13),
            )
            conn.execute(
                "INSERT INTO project_todo_comment_images VALUES (?,?,?,?,?,?,?,?)",
                (f"i{pk}", f"c{pk}", f"{project_id}/i{pk}", pk, "b" * 64, "image/png", 0, 14),
            )
            conn.execute(
                "INSERT INTO project_todo_comment_image_usage VALUES (?,?)", (project_id, pk)
            )
        conn.execute(
            "INSERT INTO project_todos(id,todo_id,project_id,creator_user_id,title,created_at,"
            "updated_at) VALUES (100,'removed','p1',1,'removed',1,1)"
        )
        conn.execute("DELETE FROM project_todos WHERE id = 100")
        conn.execute("CREATE INDEX idx_ps04c_original_title ON project_todos(title)")
    yield pool
    pool.close()


def _assert_schema(conn: Any) -> None:
    columns = {row["name"] for row in conn.execute("PRAGMA table_info(project_todos)")}
    assert {"start_date", "due_date", "priority_id"} <= columns
    tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert set(CATALOG_TABLES) <= tables
    assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1
    assert conn.execute("PRAGMA foreign_key_check").fetchall() == []
    foreign_keys = list(conn.execute("PRAGMA foreign_key_list(project_todos)"))
    priority_fk = [row for row in foreign_keys if row["table"] == "project_todo_priorities"]
    assert [(row["from"], row["to"]) for row in priority_fk] == [
        ("project_id", "project_id"),
        ("priority_id", "priority_id"),
    ]
    assert all(row["on_delete"] != "SET NULL" for row in priority_fk)
    assert {
        row["table"] for row in conn.execute("PRAGMA foreign_key_list(project_todo_comments)")
    } == {
        "project_todos",
        "users",
    }
    assert {
        row["table"] for row in conn.execute("PRAGMA foreign_key_list(project_todo_comment_images)")
    } == {"project_todo_comments"}
    unique_columns = {
        tuple(row["name"] for row in conn.execute(f"PRAGMA index_info('{index['name']}')"))
        for index in conn.execute("PRAGMA index_list(project_todos)")
        if index["unique"]
    }
    assert ("project_id", "todo_id") in unique_columns


def _assert_defaults(conn: Any) -> None:
    for project_id in ("p1", "p2"):
        assert (
            conn.execute(
                "SELECT revision FROM project_todo_catalog_state WHERE project_id=?", (project_id,)
            ).fetchone()[0]
            == 1
        )
        rows = conn.execute(
            "SELECT priority_id,name,color,position,archived_at FROM project_todo_priorities "
            "WHERE project_id=? ORDER BY position",
            (project_id,),
        ).fetchall()
        assert [(row["name"], row["color"], row["position"]) for row in rows] == DEFAULTS
        assert all(re.fullmatch(r"[0-9A-HJKMNP-TV-Z]{26}", row["priority_id"]) for row in rows)
        assert all(row["archived_at"] is None for row in rows)


def test_034_fresh_schema_and_numbered_pair(tmp_path: Path) -> None:
    pool = SqlitePool(tmp_path / "fresh.db")
    try:
        run_migrations(pool)
        with pool.connect() as conn:
            _assert_schema(conn)
            assert conn.execute("SELECT version FROM _schema_version").fetchone()[
                0
            ] == _max_discovered_version("sqlite")
        assert _max_discovered_version("sqlite") == _max_discovered_version("postgresql")
        assert (34, MIGRATIONS / "034_project_todo_fields.sql") in _discover("sqlite")
        assert (34, MIGRATIONS / "034_project_todo_fields.pg.sql") in _discover("postgresql")
    finally:
        pool.close()


def test_034_upgrade_preserves_rows_children_files_ids_and_indices(
    old_db: SqlitePool, tmp_path: Path
) -> None:
    image_file = tmp_path / "controlled-image.png"
    image_file.write_bytes(b"\x89PNG\r\n\x1a\noriginal-file-bytes")
    with old_db.connect() as conn:
        before = _snapshot(conn)
    run_migrations(old_db)
    with old_db.connect() as conn:
        _assert_schema(conn)
        _assert_defaults(conn)
        assert [
            tuple(row)
            for row in conn.execute(f"SELECT {OLD_COLUMNS} FROM project_todos ORDER BY id")
        ] == before["project_todos"]
        for table in TABLES[1:]:
            assert _snapshot(conn)[table] == before[table]
        assert [
            tuple(row)
            for row in conn.execute("SELECT start_date,due_date,priority_id FROM project_todos")
        ] == [(None, None, None)] * 2
        indexes = {row["name"] for row in conn.execute("PRAGMA index_list(project_todos)")}
        assert {
            "idx_project_todos_project_list",
            "idx_project_todos_status",
            "idx_project_todos_assignee",
            "idx_ps04c_original_title",
        } <= indexes
        assert (
            conn.execute("SELECT seq FROM sqlite_sequence WHERE name='project_todos'").fetchone()[0]
            == 100
        )
    assert image_file.read_bytes() == b"\x89PNG\r\n\x1a\noriginal-file-bytes"


@pytest.mark.parametrize("phase", ["after_copy", "after_swap", "before_watermark"])
def test_034_failure_rolls_back_whole_upgrade_and_replays(
    old_db: SqlitePool, monkeypatch: pytest.MonkeyPatch, phase: str
) -> None:
    original_connect = old_db.connect
    with original_connect() as conn:
        before = _snapshot(conn)
        schema = [
            tuple(row) for row in conn.execute("SELECT name,sql FROM sqlite_master ORDER BY name")
        ]
    markers = {
        "after_copy": "INSERT INTO project_todos_v34_new",
        "after_swap": "ALTER TABLE project_todos_v34_new RENAME TO project_todos",
        "before_watermark": "UPDATE _schema_version SET version = 34",
    }
    reached: list[tuple[int, bool]] = []

    class FaultConnection:
        def __init__(self, conn: Any) -> None:
            self.conn = conn

        def execute(self, sql: str, params: Any = ()) -> Any:
            if sql.strip().startswith(markers[phase]):
                reached.append(
                    (
                        self.conn.execute("PRAGMA foreign_keys").fetchone()[0],
                        self.conn.in_transaction,
                    )
                )
                if phase != "before_watermark":
                    self.conn.execute(sql, params)
                raise RuntimeError(f"injected {phase}")
            return self.conn.execute(sql, params)

        def executescript(self, _sql: str) -> None:
            raise AssertionError("034 cannot use executescript")

        def __getattr__(self, name: str) -> Any:
            return getattr(self.conn, name)

    @contextmanager
    def fault_connect() -> Iterator[FaultConnection]:
        with original_connect() as conn:
            yield FaultConnection(conn)

    with monkeypatch.context() as patch:
        patch.setattr(old_db, "connect", fault_connect)
        with pytest.raises(RuntimeError, match=f"injected {phase}"):
            run_migrations(old_db)
    assert reached == [(0, True)]
    with original_connect() as conn:
        assert _snapshot(conn) == before
        assert [
            tuple(row) for row in conn.execute("SELECT name,sql FROM sqlite_master ORDER BY name")
        ] == schema
        assert conn.execute("SELECT version FROM _schema_version").fetchone()[0] == 33
        assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1
        assert not conn.in_transaction
        assert conn.execute("PRAGMA foreign_key_check").fetchall() == []
    run_migrations(old_db)
    with original_connect() as conn:
        _assert_schema(conn)
        _assert_defaults(conn)


def test_034_replay_preserves_existing_calendar_and_catalog(
    old_db: SqlitePool,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _run_historical_034(old_db, monkeypatch)
    with old_db.transaction() as conn:
        priority_id = conn.execute(
            "SELECT priority_id FROM project_todo_priorities WHERE project_id='p1' AND position=0"
        ).fetchone()[0]
        conn.execute(
            "UPDATE project_todos SET start_date='2024-02-29',due_date='2024-03-01',priority_id=? WHERE todo_id='t1'",
            (priority_id,),
        )
        conn.execute(
            "UPDATE project_todo_priorities SET name='自定义',name_key='自定义',archived_at=44 WHERE priority_id=?",
            (priority_id,),
        )
        conn.execute("UPDATE project_todo_catalog_state SET revision=5 WHERE project_id='p1'")
        conn.execute("UPDATE _schema_version SET version=33")
        before = _snapshot(conn)
        options = [
            tuple(row) for row in conn.execute("SELECT * FROM project_todo_priorities ORDER BY id")
        ]
    _run_historical_034(old_db, monkeypatch)
    with old_db.connect() as conn:
        _assert_schema(conn)
        assert _snapshot(conn) == before
        assert [
            tuple(row) for row in conn.execute("SELECT * FROM project_todo_priorities ORDER BY id")
        ] == options
        assert (
            conn.execute(
                "SELECT revision FROM project_todo_catalog_state WHERE project_id='p1'"
            ).fetchone()[0]
            == 5
        )


def test_034_repairs_partial_columns_even_at_current_watermark(old_db: SqlitePool) -> None:
    with old_db.transaction() as conn:
        conn.execute("ALTER TABLE project_todos ADD COLUMN start_date TEXT")
        conn.execute("UPDATE project_todos SET start_date='2024-02-29' WHERE todo_id='t1'")
        conn.execute("UPDATE _schema_version SET version=34")
    run_migrations(old_db)
    with old_db.connect() as conn:
        _assert_schema(conn)
        _assert_defaults(conn)
        assert (
            conn.execute("SELECT start_date FROM project_todos WHERE todo_id='t1'").fetchone()[0]
            == "2024-02-29"
        )


@pytest.mark.parametrize(
    "start,due",
    [
        ("1899-12-31", None),
        (None, "10000-01-01"),
        ("2026-9-28", None),
        ("2026-09-28T00:00:00Z", None),
        ("2026-aa-28", None),
        ("2026-09-29", "2026-09-28"),
    ],
)
def test_034_database_rejects_date_shape_range_and_reversed_dates(
    old_db: SqlitePool, start: str | None, due: str | None
) -> None:
    run_migrations(old_db)
    with old_db.transaction() as conn, pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "UPDATE project_todos SET start_date=?,due_date=? WHERE todo_id='t1'", (start, due)
        )


def test_034_composite_project_references_are_database_enforced(old_db: SqlitePool) -> None:
    run_migrations(old_db)
    with old_db.transaction() as conn:
        conn.execute(
            "INSERT INTO project_todo_tags(tag_id,project_id,name,name_key,color,created_at,updated_at) VALUES ('tag1','p1','标签','标签','green',1,1)"
        )
        conn.execute(
            "INSERT INTO project_todo_tags(tag_id,project_id,name,name_key,color,created_at,updated_at) VALUES ('tag2','p2','标签','标签','green',1,1)"
        )
        priority_id = conn.execute(
            "SELECT priority_id FROM project_todo_priorities WHERE project_id='p2' LIMIT 1"
        ).fetchone()[0]
        conn.execute("INSERT INTO project_todo_tag_links VALUES ('p1','t1','tag1')")
    for sql, params in (
        ("UPDATE project_todos SET priority_id=? WHERE todo_id='t1'", (priority_id,)),
        ("INSERT INTO project_todo_tag_links VALUES (?,?,?)", ("p1", "t1", "tag2")),
        ("INSERT INTO project_todo_tag_links VALUES (?,?,?)", ("p2", "t1", "tag2")),
        ("INSERT INTO project_todo_tag_links VALUES (?,?,?)", ("p1", "t1", "tag1")),
    ):
        with old_db.transaction() as conn, pytest.raises(sqlite3.IntegrityError):
            conn.execute(sql, params)


@pytest.mark.parametrize(
    "table,id_column", [("project_todo_priorities", "priority_id"), ("project_todo_tags", "tag_id")]
)
def test_034_active_name_unique_index_allows_archived_history(
    old_db: SqlitePool, table: str, id_column: str
) -> None:
    run_migrations(old_db)
    position_column = ",position" if table == "project_todo_priorities" else ""
    position_value = ",4" if position_column else ""
    statement = f"INSERT INTO {table}({id_column},project_id,name,name_key,color,created_at,updated_at{position_column}) VALUES (?,?,'Same','same','blue',1,1{position_value})"
    with old_db.transaction() as conn:
        conn.execute(statement, ("first", "p1"))
        conn.execute(statement, ("other-project", "p2"))
    with old_db.transaction() as conn, pytest.raises(sqlite3.IntegrityError):
        conn.execute(statement, ("duplicate", "p1"))
    with old_db.transaction() as conn:
        conn.execute(f"UPDATE {table} SET archived_at=2 WHERE {id_column}='first'")
        conn.execute(statement, ("replacement", "p1"))
    with old_db.transaction() as conn, pytest.raises(sqlite3.IntegrityError):
        conn.execute(f"UPDATE {table} SET archived_at=NULL WHERE {id_column}='first'")


def test_seed_uses_callers_transaction_and_is_idempotent(old_db: SqlitePool) -> None:
    run_migrations(old_db)
    seed = import_module("octop.infra.db.project_plan_seed").seed_todo_catalog
    with pytest.raises(RuntimeError, match="rollback seed"), old_db.transaction() as conn:
        conn.execute(
            "INSERT INTO project_spaces(project_id,creator_user_id,name,created_at,updated_at) VALUES ('p3',1,'p3',1,1)"
        )
        seed(conn, "p3", 55)
        seed(conn, "p3", 88)
        assert (
            conn.execute(
                "SELECT revision,updated_at FROM project_todo_catalog_state WHERE project_id='p3'"
            ).fetchone()["updated_at"]
            == 55
        )
        assert (
            conn.execute(
                "SELECT COUNT(*) FROM project_todo_priorities WHERE project_id='p3'"
            ).fetchone()[0]
            == 4
        )
        raise RuntimeError("rollback seed")
    with old_db.connect() as conn:
        assert (
            conn.execute(
                "SELECT COUNT(*) FROM project_todo_catalog_state WHERE project_id='p3'"
            ).fetchone()[0]
            == 0
        )


@pytest.mark.parametrize(
    "statement",
    [
        "UPDATE project_todo_catalog_state SET project_id=NULL WHERE project_id='p1'",
        "UPDATE project_todo_catalog_state SET revision=0 WHERE project_id='p1'",
        "UPDATE project_todo_catalog_state SET revision=1.5 WHERE project_id='p1'",
        "UPDATE project_todo_priorities SET position=-1 WHERE project_id='p1'",
        "UPDATE project_todo_priorities SET position=0.5 WHERE project_id='p1'",
        "UPDATE project_todo_priorities SET name='' WHERE project_id='p1'",
        "UPDATE project_todo_priorities SET name=' padded ' WHERE project_id='p1'",
        "UPDATE project_todo_priorities SET name='12345678901234567890123456789012345678901' WHERE project_id='p1'",
        "UPDATE project_todo_priorities SET name_key='' WHERE project_id='p1'",
        "UPDATE project_todo_priorities SET color='pink' WHERE project_id='p1'",
        "INSERT INTO project_todo_tags(tag_id,project_id,name,name_key,color,created_at,updated_at) VALUES ('bad','p1','标签','标签','pink',1,1)",
    ],
)
def test_034_catalog_storage_checks(old_db: SqlitePool, statement: str) -> None:
    run_migrations(old_db)
    with old_db.transaction() as conn, pytest.raises(sqlite3.IntegrityError):
        conn.execute(statement)


def test_034_refuses_nested_transaction_without_changing_foreign_keys(old_db: SqlitePool) -> None:
    migration = import_module("octop.infra.db.migrate")
    with old_db.transaction() as conn:
        with pytest.raises(RuntimeError, match="requires its own SQLite migration transaction"):
            migration._ensure_project_todo_fields_v34(
                old_db, MIGRATIONS / "034_project_todo_fields.sql"
            )
        assert conn.in_transaction
        assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1
        assert conn.execute("SELECT version FROM _schema_version").fetchone()[0] == 33


def _catalog_snapshot(conn: Any) -> dict[str, list[tuple[Any, ...]]]:
    return {
        table: [tuple(row) for row in conn.execute(f"SELECT * FROM {table} ORDER BY 1")]
        for table in CATALOG_TABLES
    }


def _catalog_ddl(table: str) -> str:
    migration = import_module("octop.infra.db.migrate")
    return next(
        statement
        for statement in migration._split_pg_sql(
            (MIGRATIONS / "034_project_todo_fields.sql").read_text(encoding="utf-8")
        )
        if statement.startswith(f"CREATE TABLE IF NOT EXISTS {table} (")
    )


def test_034_preserves_future_watermark_schema_and_values_across_two_runs(
    old_db: SqlitePool,
) -> None:
    run_migrations(old_db)
    future_version = _max_discovered_version("sqlite") + 1
    with old_db.transaction() as conn:
        conn.execute("ALTER TABLE project_todos ADD COLUMN future_035 TEXT")
        conn.execute("UPDATE project_todos SET future_035='future value' WHERE todo_id='t1'")
        conn.execute("UPDATE _schema_version SET version=?", (future_version,))
        before = _snapshot(conn)
        catalog = _catalog_snapshot(conn)
        schema = [
            tuple(row) for row in conn.execute("SELECT name,sql FROM sqlite_master ORDER BY name")
        ]
    for _ in range(2):
        run_migrations(old_db)
        with old_db.connect() as conn:
            assert (
                conn.execute("SELECT version FROM _schema_version").fetchone()[0] == future_version
            )
            assert _snapshot(conn) == before
            assert _catalog_snapshot(conn) == catalog
            assert [
                tuple(row)
                for row in conn.execute("SELECT name,sql FROM sqlite_master ORDER BY name")
            ] == schema
            assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1
            assert conn.execute("PRAGMA foreign_key_check").fetchall() == []


def test_034_future_guard_preserves_known_historical_squash_repair(
    old_db: SqlitePool, monkeypatch: pytest.MonkeyPatch
) -> None:
    migration = import_module("octop.infra.db.migrate")
    with old_db.transaction() as conn:
        before = _snapshot(conn)
        conn.execute("UPDATE _schema_version SET version=9")
    monkeypatch.setattr(migration, "_max_discovered_version", lambda _dialect: 5)
    migration._reconcile_pre_squash_schema_version(old_db)
    with old_db.connect() as conn:
        assert conn.execute("SELECT version FROM _schema_version").fetchone()[0] == 5
        assert _snapshot(conn) == before


def test_034_completes_state_only_seed_prefix_without_changing_state(old_db: SqlitePool) -> None:
    with old_db.transaction() as conn:
        conn.execute(_catalog_ddl("project_todo_catalog_state"))
        conn.execute("INSERT INTO project_todo_catalog_state VALUES ('p1',1,1)")
    run_migrations(old_db)
    with old_db.connect() as conn:
        _assert_schema(conn)
        _assert_defaults(conn)
        assert tuple(
            conn.execute(
                "SELECT * FROM project_todo_catalog_state WHERE project_id='p1'"
            ).fetchone()
        ) == ("p1", 1, 1)
        assert [
            tuple(row)
            for row in conn.execute(
                "SELECT created_at,updated_at FROM project_todo_priorities WHERE project_id='p1'"
            )
        ] == [(1, 1)] * 4
        before = _catalog_snapshot(conn)
    run_migrations(old_db)
    with old_db.connect() as conn:
        assert _catalog_snapshot(conn) == before


@pytest.mark.parametrize("all_archived", [False, True])
def test_034_migration_and_seed_preserve_edited_or_all_archived_catalog(
    old_db: SqlitePool,
    all_archived: bool,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _run_historical_034(old_db, monkeypatch)
    with old_db.transaction() as conn:
        priority_id = conn.execute(
            "SELECT priority_id FROM project_todo_priorities WHERE project_id='p1' AND position=0"
        ).fetchone()[0]
        conn.execute(
            "UPDATE project_todo_priorities SET name='改过的目录',name_key='改过的目录',color='purple',updated_at=44 WHERE priority_id=?",
            (priority_id,),
        )
        if all_archived:
            conn.execute("UPDATE project_todo_priorities SET archived_at=44 WHERE project_id='p1'")
        conn.execute(
            "UPDATE project_todo_catalog_state SET revision=9,updated_at=44 WHERE project_id='p1'"
        )
        conn.execute(
            "INSERT INTO project_todo_tags(tag_id,project_id,name,name_key,color,archived_at,created_at,updated_at) VALUES ('retained-tag','p1','原标签','原标签','blue',44,1,44)"
        )
        conn.execute("INSERT INTO project_todo_tag_links VALUES ('p1','t1','retained-tag')")
        conn.execute("UPDATE project_todos SET priority_id=? WHERE todo_id='t1'", (priority_id,))
        conn.execute("UPDATE _schema_version SET version=33")
        before = _snapshot(conn)
        catalog = _catalog_snapshot(conn)
        import_module("octop.infra.db.project_plan_seed").seed_todo_catalog(conn, "p1", 99)
        assert _catalog_snapshot(conn) == catalog
    for _ in range(2):
        _run_historical_034(old_db, monkeypatch)
        with old_db.connect() as conn:
            assert _snapshot(conn) == before
            assert _catalog_snapshot(conn) == catalog
            assert conn.execute("SELECT version FROM _schema_version").fetchone()[0] == 34


@pytest.mark.parametrize("revision,existing_priority", [(2, False), (1, True)])
def test_034_rejects_ambiguous_incomplete_seed_without_advancing_or_changing_rows(
    old_db: SqlitePool, revision: int, existing_priority: bool
) -> None:
    with old_db.transaction() as conn:
        conn.execute(_catalog_ddl("project_todo_catalog_state"))
        conn.execute("INSERT INTO project_todo_catalog_state VALUES ('p1',?,1)", (revision,))
        if existing_priority:
            conn.execute(_catalog_ddl("project_todo_priorities"))
            conn.execute(
                "INSERT INTO project_todo_priorities(priority_id,project_id,name,name_key,color,position,created_at,updated_at) VALUES ('original-id','p1','已改的目录','已改的目录','purple',0,1,1)"
            )
        before = _snapshot(conn)
        state = [tuple(row) for row in conn.execute("SELECT * FROM project_todo_catalog_state")]
        priorities = (
            [tuple(row) for row in conn.execute("SELECT * FROM project_todo_priorities")]
            if existing_priority
            else None
        )
        schema = [
            tuple(row) for row in conn.execute("SELECT name,sql FROM sqlite_master ORDER BY name")
        ]
    with pytest.raises(RuntimeError, match="incomplete catalog seed"):
        run_migrations(old_db)
    with old_db.connect() as conn:
        assert conn.execute("SELECT version FROM _schema_version").fetchone()[0] == 33
        assert _snapshot(conn) == before
        assert [
            tuple(row) for row in conn.execute("SELECT * FROM project_todo_catalog_state")
        ] == state
        if existing_priority:
            assert [
                tuple(row) for row in conn.execute("SELECT * FROM project_todo_priorities")
            ] == priorities
        assert [
            tuple(row) for row in conn.execute("SELECT name,sql FROM sqlite_master ORDER BY name")
        ] == schema
        assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1
        assert conn.execute("PRAGMA foreign_key_check").fetchall() == []
