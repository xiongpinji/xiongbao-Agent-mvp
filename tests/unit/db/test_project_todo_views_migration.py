"""Real 035 state, derived-key backfills, seed preservation and atomic failures."""

from __future__ import annotations

import json
import re
import sqlite3
import unicodedata
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from importlib import import_module
from pathlib import Path
from typing import Any

import pytest

from octop.infra.backup.snapshot import capture_users_from_pool, upsert_users_into_pool
from octop.infra.db.migrate import _discover, _max_discovered_version, run_migrations
from octop.infra.db.pool import DatabasePool, SqlitePool
from octop.infra.db.repos.project_todos import ProjectTodoRepo
from octop.infra.db.repos.projects import ProjectRepo
from octop.infra.db.repos.users import UserRepo
from octop.infra.utils.ulid import new_ulid

MIGRATIONS = Path(__file__).resolve().parents[3] / "src/octop/infra/db/migrations"
VIEW_TABLES = {"project_todo_views", "project_todo_view_state"}
TITLE = "\ufdfa" * 200
DISPLAY = "\ufdfa" * 800 + "Straße"
TABLE_DEFINITION = {
    "schema_version": 1,
    "fields": ["title", "status", "assignee", "priority", "tags", "start_date", "due_date"],
    "group_by": None,
    "filters": [],
    "sort": [{"field": "updated_at", "direction": "desc"}],
}
BOARD_DEFINITION = {
    **TABLE_DEFINITION,
    "fields": ["title", "status", "assignee", "priority", "tags"],
    "group_by": "status",
}


def _key(value: str) -> str:
    return unicodedata.normalize("NFKC", value).casefold()


def _build_legacy(pool: DatabasePool, version: int, monkeypatch: pytest.MonkeyPatch) -> None:
    migration = import_module("octop.infra.db.migrate")
    discover = migration._discover
    with monkeypatch.context() as patch:
        patch.setattr(
            migration,
            "_discover",
            lambda dialect: [(v, p) for v, p in discover(dialect) if v <= version],
        )
        run_migrations(pool)


def _seed_legacy(pool: DatabasePool, version: int) -> None:
    with pool.transaction() as conn:
        conn.execute(
            "INSERT INTO users(id,username,password_hash,role,display_name,created_at) "
            "VALUES (1,'Ｓtraße','synthetic','admin',?,11)",
            (DISPLAY,),
        )
        conn.execute(
            "INSERT INTO users(id,username,password_hash,role,display_name,created_at) "
            "VALUES (2,?,'synthetic','user','',11)",
            ("\ufdfa" * 200,),
        )
        for project_id, archived in (("p1", 0), ("p2", 1)):
            conn.execute(
                "INSERT INTO project_spaces(project_id,creator_user_id,name,archived,"
                "created_at,updated_at) VALUES (?,1,?,?,11,12)",
                (project_id, project_id, archived),
            )
            conn.execute(
                "INSERT INTO project_members(project_id,user_id,role,joined_at) "
                "VALUES (?,1,'owner',11)",
                (project_id,),
            )
        conn.execute(
            "INSERT INTO project_todos(id,todo_id,project_id,creator_user_id,title,"
            "description,description_format,status,version,created_at,updated_at) "
            "VALUES (17,'t1','p1',1,?,'**kept**','markdown','done',7,11,12)",
            (TITLE,),
        )
        conn.execute(
            "INSERT INTO project_todo_comments VALUES "
            "('c1','t1',1,'kept comment','request-1',?,13)",
            ("a" * 64,),
        )
        conn.execute(
            "INSERT INTO project_todo_comment_images VALUES "
            "('i1','c1','p1/i1',9,?,'image/png',0,14)",
            ("b" * 64,),
        )
        conn.execute("INSERT INTO project_todo_comment_image_usage VALUES ('p1',9)")
        conn.execute("CREATE INDEX idx_q2_kept_title ON project_todos(title)")
        if version >= 34:
            from octop.infra.db.project_plan_seed import seed_todo_catalog

            for project_id in ("p1", "p2"):
                seed_todo_catalog(conn, project_id, 16)
            priority = conn.execute(
                "SELECT priority_id FROM project_todo_priorities "
                "WHERE project_id='p1' ORDER BY position LIMIT 1"
            ).fetchone()[0]
            conn.execute(
                "UPDATE project_todos SET start_date='2000-02-29',due_date='2000-03-01',"
                "priority_id=? WHERE todo_id='t1'",
                (priority,),
            )
            conn.execute(
                "UPDATE project_todo_priorities SET name='kept',name_key='kept',"
                "position=7,archived_at=18 WHERE priority_id=?",
                (priority,),
            )
            conn.execute(
                "INSERT INTO project_todo_tags(tag_id,project_id,name,name_key,color,"
                "archived_at,created_at,updated_at) "
                "VALUES ('tag1','p1','kept tag','kept tag','purple',18,16,18)"
            )
            conn.execute("INSERT INTO project_todo_tag_links VALUES ('p1','t1','tag1')")
            conn.execute(
                "UPDATE project_todo_catalog_state SET revision=8,updated_at=18 "
                "WHERE project_id='p1'"
            )


def _assert_035(conn: Any) -> None:
    if hasattr(conn, "in_transaction"):
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        assert tables >= VIEW_TABLES, "035 must create persistent shared view tables"
        todo_columns = {r["name"] for r in conn.execute("PRAGMA table_info(project_todos)")}
        user_columns = {r["name"] for r in conn.execute("PRAGMA table_info(users)")}
    else:
        columns = conn.execute(
            "SELECT table_name,column_name FROM information_schema.columns "
            "WHERE table_schema=current_schema()"
        ).fetchall()
        tables = {str(r[0]) for r in columns}
        assert tables >= VIEW_TABLES, "035 must create persistent shared view tables"
        todo_columns = {str(r[1]) for r in columns if r[0] == "project_todos"}
        user_columns = {str(r[1]) for r in columns if r[0] == "users"}
    assert "title_search_key" in todo_columns
    assert "project_plan_display_sort_key" in user_columns
    dialect = "sqlite" if hasattr(conn, "in_transaction") else "postgresql"
    assert conn.execute("SELECT version FROM _schema_version").fetchone()[
        0
    ] == _max_discovered_version(dialect)


def _replay_035(pool: DatabasePool) -> None:
    """Exercise the historical helper, rather than reentry of a later migration."""
    migration = import_module("octop.infra.db.migrate")
    migration._ensure_project_todo_views_v35(pool, MIGRATIONS / "035_project_todo_views.sql")


def _assert_defaults(conn: Any, project_id: str) -> None:
    state = conn.execute(
        "SELECT * FROM project_todo_view_state WHERE project_id=?", (project_id,)
    ).fetchone()
    assert state is not None and state["revision"] == 1
    views = conn.execute(
        "SELECT * FROM project_todo_views WHERE project_id=? ORDER BY position,view_id",
        (project_id,),
    ).fetchall()
    assert [(r["name"], r["view_type"], r["position"], r["version"]) for r in views] == [
        ("表格", "table", 0, 1),
        ("看板", "board", 1, 1),
    ]
    assert all(re.fullmatch(r"[0-9A-HJKMNP-TV-Z]{26}", r["view_id"]) for r in views)
    assert views[0]["view_id"] != views[1]["view_id"]
    assert state["default_view_id"] == views[0]["view_id"]
    assert [json.loads(r["definition_json"]) for r in views] == [
        TABLE_DEFINITION,
        BOARD_DEFINITION,
    ]
    assert all(r["name_key"] == _key(r["name"]) for r in views)
    assert all(r["archived_at"] is None for r in views)


@pytest.fixture
def legacy34(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[SqlitePool]:
    pool = SqlitePool(tmp_path / "actual-034.db")
    _build_legacy(pool, 34, monkeypatch)
    _seed_legacy(pool, 34)
    try:
        yield pool
    finally:
        pool.close()


def test_035_fresh_paired_schema(tmp_path: Path) -> None:
    pool = SqlitePool(tmp_path / "fresh.db")
    try:
        run_migrations(pool)
        with pool.connect() as conn:
            _assert_035(conn)
            assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1
            assert conn.execute("PRAGMA foreign_key_check").fetchall() == []
        assert _max_discovered_version("sqlite") == _max_discovered_version("postgresql") >= 35
        assert (35, MIGRATIONS / "035_project_todo_views.sql") in _discover("sqlite")
        assert (35, MIGRATIONS / "035_project_todo_views.pg.sql") in _discover("postgresql")
    finally:
        pool.close()


@pytest.mark.parametrize("source_version", [33, 34])
def test_035_upgrades_real_history_preserving_old_rows_and_full_keys(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, source_version: int
) -> None:
    pool = SqlitePool(tmp_path / f"actual-{source_version}.db")
    try:
        _build_legacy(pool, source_version, monkeypatch)
        _seed_legacy(pool, source_version)
        with pool.connect() as conn:
            columns = [r["name"] for r in conn.execute("PRAGMA table_info(project_todos)")]
            projection = ",".join(columns)
            original = [tuple(r) for r in conn.execute(f"SELECT {projection} FROM project_todos")]
            children = {
                table: [tuple(r) for r in conn.execute(f"SELECT * FROM {table}")]
                for table in (
                    "project_todo_comments",
                    "project_todo_comment_images",
                    "project_todo_comment_image_usage",
                )
            }
            catalog = (
                {
                    table: [tuple(r) for r in conn.execute(f"SELECT * FROM {table} ORDER BY 1")]
                    for table in (
                        "project_todo_catalog_state",
                        "project_todo_priorities",
                        "project_todo_tags",
                        "project_todo_tag_links",
                    )
                }
                if source_version == 34
                else {}
            )
        run_migrations(pool)
        with pool.connect() as conn:
            _assert_035(conn)
            assert [
                tuple(r) for r in conn.execute(f"SELECT {projection} FROM project_todos")
            ] == original
            for table, before in {**children, **catalog}.items():
                assert [
                    tuple(r) for r in conn.execute(f"SELECT * FROM {table} ORDER BY 1")
                ] == before
            _assert_defaults(conn, "p1")
            _assert_defaults(conn, "p2")  # archived projects also have real views
            assert conn.execute(
                "SELECT title_search_key FROM project_todos WHERE todo_id='t1'"
            ).fetchone()[0] == _key(TITLE)
            users = conn.execute(
                "SELECT project_plan_display_sort_key FROM users ORDER BY id"
            ).fetchall()
            assert [r[0] for r in users] == [_key(DISPLAY), _key("\ufdfa" * 200)]
            assert "idx_q2_kept_title" in {
                r["name"] for r in conn.execute("PRAGMA index_list(project_todos)")
            }
            assert (
                conn.execute(
                    "SELECT seq FROM sqlite_sequence WHERE name='project_todos'"
                ).fetchone()[0]
                == 17
            )
            assert conn.execute("PRAGMA foreign_key_check").fetchall() == []
    finally:
        pool.close()


FAULT_SQL = {
    "ddl": "CREATE TABLE IF NOT EXISTS project_todo_views",
    "title": "UPDATE project_todos SET title_search_key",
    "user": "UPDATE users SET project_plan_display_sort_key",
    "first_view": "INSERT INTO project_todo_views(",
    "default": "UPDATE project_todo_view_state SET default_view_id",
    "watermark": "UPDATE _schema_version SET version = 35",
}


@contextmanager
def _fault_after_sql(
    pool: SqlitePool,
    monkeypatch: pytest.MonkeyPatch,
    marker: str,
    failure: type[BaseException],
) -> Iterator[list[tuple[int, bool]]]:
    real_connect = pool.connect
    reached: list[tuple[int, bool]] = []

    class Connection:
        def __init__(self, conn: Any) -> None:
            self.conn = conn

        def execute(self, sql: str, params: Any = ()) -> Any:
            result = self.conn.execute(sql, params)
            if sql.strip().startswith(marker) and not reached:
                reached.append(
                    (
                        self.conn.execute("PRAGMA foreign_keys").fetchone()[0],
                        self.conn.in_transaction,
                    )
                )
                raise failure("injected after actual 035 SQL")
            return result

        def executescript(self, _sql: str) -> None:
            raise AssertionError("035 must not use executescript")

        def __getattr__(self, name: str) -> Any:
            return getattr(self.conn, name)

    @contextmanager
    def connect() -> Iterator[Connection]:
        with real_connect() as conn:
            yield Connection(conn)

    with monkeypatch.context() as patch:
        patch.setattr(pool, "connect", connect)
        yield reached


@pytest.mark.parametrize("phase", list(FAULT_SQL))
@pytest.mark.parametrize("failure", [RuntimeError, KeyboardInterrupt])
def test_035_actual_sql_fault_rolls_back_schema_rows_watermark_and_connection(
    legacy34: SqlitePool,
    monkeypatch: pytest.MonkeyPatch,
    phase: str,
    failure: type[BaseException],
) -> None:
    with legacy34.connect() as conn:
        before = tuple(conn.iterdump())
    with (
        _fault_after_sql(legacy34, monkeypatch, FAULT_SQL[phase], failure) as reached,
        pytest.raises(failure, match="injected after actual 035 SQL"),
    ):
        run_migrations(legacy34)
    assert reached == [(1, True)], "the failing SQL must have really executed in the transaction"
    with legacy34.connect() as conn:
        assert tuple(conn.iterdump()) == before
        assert conn.execute("SELECT version FROM _schema_version").fetchone()[0] == 34
        assert not conn.in_transaction
        assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1
        assert conn.execute("PRAGMA foreign_key_check").fetchall() == []
    run_migrations(legacy34)
    with legacy34.connect() as conn:
        _assert_035(conn)
        _assert_defaults(conn, "p1")


def test_033_committed_034_survives_later_035_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pool = SqlitePool(tmp_path / "actual-033.db")
    try:
        _build_legacy(pool, 33, monkeypatch)
        _seed_legacy(pool, 33)
        with (
            _fault_after_sql(pool, monkeypatch, FAULT_SQL["ddl"], KeyboardInterrupt),
            pytest.raises(KeyboardInterrupt, match="injected after actual 035 SQL"),
        ):
            run_migrations(pool)
        with pool.connect() as conn:
            assert conn.execute("SELECT version FROM _schema_version").fetchone()[0] == 34
            assert "start_date" in {
                r["name"] for r in conn.execute("PRAGMA table_info(project_todos)")
            }
            assert "title_search_key" not in {
                r["name"] for r in conn.execute("PRAGMA table_info(project_todos)")
            }
            assert conn.execute(
                "SELECT title,version FROM project_todos WHERE todo_id='t1'"
            ).fetchone()[:] == (TITLE, 7)
            assert conn.execute("PRAGMA foreign_key_check").fetchall() == []
    finally:
        pool.close()


def test_035_seed_preserves_custom_default_order_versions_and_archived_config(
    legacy34: SqlitePool,
) -> None:
    run_migrations(legacy34)
    custom = new_ulid()
    definition = {
        **TABLE_DEFINITION,
        "fields": ["title", "status", "assignee", "priority"],
        "calendar": {"date_basis": "start_date", "mode": "week"},
    }
    with legacy34.transaction() as conn:
        _assert_035(conn)
        conn.execute(
            "UPDATE project_todo_views SET archived_at=51,version=9,position=12 "
            "WHERE project_id='p1' AND view_type='table'"
        )
        conn.execute(
            "UPDATE project_todo_views SET name='Team',name_key='team',version=6,position=0 "
            "WHERE project_id='p1' AND view_type='board'"
        )
        conn.execute(
            "INSERT INTO project_todo_views(view_id,project_id,name,name_key,view_type,"
            "definition_json,version,position,created_at,updated_at) "
            "VALUES (?,'p1','Calendar','calendar','calendar',?,4,1,41,52)",
            (custom, json.dumps(definition)),
        )
        conn.execute(
            "UPDATE project_todo_view_state SET revision=12,default_view_id=?,updated_at=52 "
            "WHERE project_id='p1'",
            (custom,),
        )
        conn.execute("UPDATE project_todos SET title_search_key='untrusted archived key'")
        conn.execute("UPDATE users SET project_plan_display_sort_key='untrusted archived key'")
        before = {
            table: [tuple(r) for r in conn.execute(f"SELECT * FROM {table} ORDER BY 1")]
            for table in VIEW_TABLES
        }
    _replay_035(legacy34)  # explicitly exercise the historical same-watermark helper
    _replay_035(legacy34)
    with legacy34.connect() as conn:
        for table, original in before.items():
            assert [tuple(r) for r in conn.execute(f"SELECT * FROM {table} ORDER BY 1")] == original
        assert conn.execute(
            "SELECT title_search_key FROM project_todos WHERE todo_id='t1'"
        ).fetchone()[0] == _key(TITLE)
        assert conn.execute(
            "SELECT project_plan_display_sort_key FROM users WHERE id=1"
        ).fetchone()[0] == _key(DISPLAY)


@pytest.mark.parametrize(
    "partial", ["empty_state", "views_without_state", "null_default", "archived_default"]
)
def test_035_persisted_partial_seed_is_rejected_without_guessing(
    legacy34: SqlitePool,
    partial: str,
) -> None:
    run_migrations(legacy34)
    with legacy34.transaction() as conn:
        _assert_035(conn)
        if partial == "empty_state":
            conn.execute(
                "UPDATE project_todo_view_state SET default_view_id=NULL WHERE project_id='p1'"
            )
            conn.execute("DELETE FROM project_todo_views WHERE project_id='p1'")
        elif partial == "views_without_state":
            conn.execute("DELETE FROM project_todo_view_state WHERE project_id='p1'")
        elif partial == "null_default":
            conn.execute(
                "UPDATE project_todo_view_state SET default_view_id=NULL WHERE project_id='p1'"
            )
        else:
            conn.execute(
                "UPDATE project_todo_views SET archived_at=52 WHERE view_id="
                "(SELECT default_view_id FROM project_todo_view_state WHERE project_id='p1')"
            )
        before = tuple(conn.iterdump())
    with pytest.raises(RuntimeError, match="incomplete view seed"):
        _replay_035(legacy34)
    with legacy34.connect() as conn:
        assert tuple(conn.iterdump()) == before


@pytest.mark.parametrize(
    "statement",
    [
        "UPDATE project_todo_view_state SET revision=0 WHERE project_id='p1'",
        "UPDATE project_todo_view_state SET revision=1.5 WHERE project_id='p1'",
        "UPDATE project_todo_views SET version=0 WHERE project_id='p1'",
        "UPDATE project_todo_views SET version=1.5 WHERE project_id='p1'",
        "UPDATE project_todo_views SET position=-1 WHERE project_id='p1'",
        "UPDATE project_todo_views SET position=0.5 WHERE project_id='p1'",
        "UPDATE project_todo_views SET view_type='unknown' WHERE project_id='p1'",
        "UPDATE project_todo_views SET name='' WHERE project_id='p1'",
        "UPDATE project_todo_views SET name=' padded ' WHERE project_id='p1'",
        "UPDATE project_todo_views SET name_key='' WHERE project_id='p1'",
        "UPDATE project_todo_views SET definition_json='[]' WHERE project_id='p1'",
        "UPDATE project_todo_view_state SET default_view_id="
        "(SELECT view_id FROM project_todo_views WHERE project_id='p2' LIMIT 1) WHERE project_id='p1'",
    ],
)
def test_035_database_checks_and_same_project_default(
    legacy34: SqlitePool,
    statement: str,
) -> None:
    run_migrations(legacy34)
    with legacy34.connect() as conn:
        _assert_035(conn)
    with pytest.raises(sqlite3.IntegrityError), legacy34.transaction() as conn:
        conn.execute(statement)


def test_035_active_name_uniqueness_keeps_archived_history(legacy34: SqlitePool) -> None:
    run_migrations(legacy34)
    with legacy34.transaction() as conn:
        _assert_035(conn)
        table = conn.execute(
            "SELECT * FROM project_todo_views WHERE project_id='p1' AND view_type='table'"
        ).fetchone()
        statement = (
            "INSERT INTO project_todo_views(view_id,project_id,name,name_key,view_type,"
            "definition_json,version,position,created_at,updated_at) "
            "VALUES (?,'p1','表格',?,'table',?,1,4,1,1)"
        )
    with pytest.raises(sqlite3.IntegrityError), legacy34.transaction() as conn:
        conn.execute(statement, (new_ulid(), table["name_key"], table["definition_json"]))
    with legacy34.transaction() as conn:
        conn.execute(
            "UPDATE project_todo_views SET archived_at=51 WHERE view_id=?", (table["view_id"],)
        )
        conn.execute(statement, (new_ulid(), table["name_key"], table["definition_json"]))


def test_035_upgrade_preserves_an_unknown_column_on_the_complete_034_parent(
    legacy34: SqlitePool,
) -> None:
    with legacy34.transaction() as conn:
        conn.execute("ALTER TABLE project_todos ADD COLUMN custom_data TEXT DEFAULT 'kept'")
        conn.execute("UPDATE project_todos SET custom_data='custom original'")
    run_migrations(legacy34)
    with legacy34.connect() as conn:
        _assert_035(conn)
        assert (
            conn.execute("SELECT custom_data FROM project_todos").fetchone()[0] == "custom original"
        )
        assert conn.execute("PRAGMA foreign_key_check").fetchall() == []


def test_035_future_watermark_does_not_rewrite_unknown_schema_or_values(
    legacy34: SqlitePool,
) -> None:
    run_migrations(legacy34)
    with legacy34.transaction() as conn:
        _assert_035(conn)
        conn.execute("ALTER TABLE project_todos ADD COLUMN future_data TEXT")
        conn.execute(
            "UPDATE project_todos SET future_data='preserve',title_search_key='future key'"
        )
        conn.execute(
            "UPDATE _schema_version SET version=?", (_max_discovered_version("sqlite") + 1,)
        )
        before = tuple(conn.iterdump())
    run_migrations(legacy34)
    run_migrations(legacy34)
    with legacy34.connect() as conn:
        assert tuple(conn.iterdump()) == before


def test_new_project_seeds_real_views_in_original_project_transaction(tmp_path: Path) -> None:
    pool = SqlitePool(tmp_path / "new-project.db")
    try:
        run_migrations(pool)
        owner = UserRepo(pool).create(username="owner", role="admin")
        project = ProjectRepo(pool).create_with_owner(creator_user_id=owner, name="new")
        with pool.connect() as conn:
            _assert_035(conn)
            _assert_defaults(conn, project.project_id)
    finally:
        pool.close()


def test_new_project_seed_failure_rolls_back_project_membership_and_event(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pool = SqlitePool(tmp_path / "new-project-fault.db")
    try:
        run_migrations(pool)
        owner = UserRepo(pool).create(username="owner", role="admin")
        with pool.connect() as conn:
            _assert_035(conn)
            before = tuple(conn.iterdump())
        seed_module = import_module("octop.infra.db.project_plan_seed")
        projects_module = import_module("octop.infra.db.repos.projects")
        seed = seed_module.seed_todo_views
        reached = []

        def fail(conn: Any, project_id: str, ts: int) -> None:
            seed(conn, project_id, ts)
            reached.append(True)
            raise RuntimeError("after real project view seed")

        with monkeypatch.context() as patch:
            patch.setattr(projects_module, "seed_todo_views", fail)
            with pytest.raises(RuntimeError, match="after real project view seed"):
                ProjectRepo(pool).create_with_owner(creator_user_id=owner, name="rolled back")
        assert reached == [True]
        with pool.connect() as conn:
            assert tuple(conn.iterdump()) == before
    finally:
        pool.close()


CAS_WRITERS = ("title", "display", "sso_display", "username_null")
CAS_NEW_NAME = "New Ｓtraße"


def _cas_rows(conn: Any, sql: str, params: Any = ()) -> list[tuple[Any, ...]]:
    values = []
    for row in conn.execute(sql, params):
        columns = tuple(row.keys())
        values.append(tuple(row[column] for column in columns))
    return values


def _cas_stable_plan(pool: DatabasePool, project_id: str) -> dict[str, list[tuple[Any, ...]]]:
    with pool.connect() as conn:
        return {
            "views": _cas_rows(
                conn,
                "SELECT * FROM project_todo_views WHERE project_id=? ORDER BY id",
                (project_id,),
            ),
            "view_state": _cas_rows(
                conn, "SELECT * FROM project_todo_view_state WHERE project_id=?", (project_id,)
            ),
            "catalog": _cas_rows(
                conn, "SELECT * FROM project_todo_catalog_state WHERE project_id=?", (project_id,)
            ),
        }


def _cas_events(pool: DatabasePool, project_id: str) -> list[dict[str, Any]]:
    with pool.connect() as conn:
        return [
            dict(row)
            for row in conn.execute(
                "SELECT * FROM project_events WHERE project_id=? ORDER BY id", (project_id,)
            )
        ]


def _cas_replay_setup(pool: DatabasePool, writer: str) -> dict[str, Any]:
    initial_display = "Old Ｄisplay" if writer in {"title", "display"} else None
    owner = UserRepo(pool).create(
        username="Old Ｕser",
        password_hash="synthetic-cas-password",
        role="admin",
        display_name=initial_display,
    )
    sentinel = UserRepo(pool).create(
        username="Untouched Ｓtraße",
        password_hash="synthetic-sentinel-password",
        role="user",
        display_name=None,
    )
    project_id = (
        ProjectRepo(pool)
        .create_with_owner(creator_user_id=owner, name="synthetic CAS replay")
        .project_id
    )
    todo = (
        ProjectTodoRepo(pool)
        .create(project_id=project_id, creator_user_id=owner, title="Old Ｔitle")
        .row
    )
    other = (
        ProjectTodoRepo(pool)
        .create(project_id=project_id, creator_user_id=owner, title="Untouched Ｔitle")
        .row
    )
    assert todo is not None and other is not None
    with pool.transaction() as conn:
        conn.execute(
            "UPDATE project_todos SET title_search_key='' WHERE project_id=?", (project_id,)
        )
        conn.execute(
            "UPDATE users SET project_plan_display_sort_key='' WHERE id IN (?,?)", (owner, sentinel)
        )
    saved = next(row for row in capture_users_from_pool(pool) if row[0] == owner)
    assert len(saved) == 11
    return {
        "owner": owner,
        "sentinel": sentinel,
        "project_id": project_id,
        "todo_id": todo.todo_id,
        "other_todo_id": other.todo_id,
        "original_user": saved,
        "initial_display": initial_display,
        "plan_before": _cas_stable_plan(pool, project_id),
        "events_before": _cas_events(pool, project_id),
    }


def _cas_writer(pool: DatabasePool, state: dict[str, Any], writer: str) -> None:
    if writer == "title":
        result = ProjectTodoRepo(pool).update(
            project_id=state["project_id"],
            todo_id=state["todo_id"],
            actor_user_id=state["owner"],
            expected_version=1,
            title=CAS_NEW_NAME,
        )
        assert result.outcome == "updated" and result.row is not None
        assert result.row.title == CAS_NEW_NAME and result.row.version == 2
    elif writer == "display":
        UserRepo(pool).set_display_name(state["owner"], None)
    elif writer == "sso_display":
        UserRepo(pool).update_sso_profile(state["owner"], display_name=CAS_NEW_NAME)
    else:
        assert writer == "username_null"
        final = list(state["original_user"])
        final[1], final[4] = CAS_NEW_NAME, None
        # Only the changed owner is upserted; the NULL sentinel remains corrupt for backfill.
        upsert_users_into_pool(pool, [tuple(final)])


def _cas_expected_user(state: dict[str, Any], writer: str) -> tuple[Any, ...]:
    expected = list(state["original_user"])
    if writer == "display":
        expected[4] = None
    elif writer == "sso_display":
        expected[4] = CAS_NEW_NAME
    elif writer == "username_null":
        expected[1], expected[4] = CAS_NEW_NAME, None
    return tuple(expected)


def _cas_assert_final(pool: DatabasePool, state: dict[str, Any], writer: str) -> None:
    import json

    with pool.connect() as conn:
        target = conn.execute(
            "SELECT title,title_search_key,version FROM project_todos WHERE todo_id=?",
            (state["todo_id"],),
        ).fetchone()
        expected_title = CAS_NEW_NAME if writer == "title" else "Old Ｔitle"
        assert target["title"] == expected_title
        assert target["version"] == (2 if writer == "title" else 1)
        assert target["title_search_key"] == _key(expected_title)
        other = conn.execute(
            "SELECT title,title_search_key,version FROM project_todos WHERE todo_id=?",
            (state["other_todo_id"],),
        ).fetchone()
        assert (other["title"], other["title_search_key"], other["version"]) == (
            "Untouched Ｔitle",
            _key("Untouched Ｔitle"),
            1,
        )
        user = conn.execute(
            "SELECT username,display_name,project_plan_display_sort_key FROM users WHERE id=?",
            (state["owner"],),
        ).fetchone()
        expected_user = _cas_expected_user(state, writer)
        assert (user["username"], user["display_name"]) == (expected_user[1], expected_user[4])
        assert user["project_plan_display_sort_key"] == _key(
            str(expected_user[4] or expected_user[1])
        )
        sentinel = conn.execute(
            "SELECT username,display_name,project_plan_display_sort_key FROM users WHERE id=?",
            (state["sentinel"],),
        ).fetchone()
        assert (
            sentinel["username"],
            sentinel["display_name"],
            sentinel["project_plan_display_sort_key"],
        ) == ("Untouched Ｓtraße", None, _key("Untouched Ｓtraße"))
        assert conn.execute("SELECT version FROM _schema_version").fetchone()[0] == 35
    assert next(
        row for row in capture_users_from_pool(pool) if row[0] == state["owner"]
    ) == _cas_expected_user(state, writer)
    assert _cas_stable_plan(pool, state["project_id"]) == state["plan_before"]
    events = _cas_events(pool, state["project_id"])
    if writer == "title":
        assert events[:-1] == state["events_before"]
        assert len(events) == len(state["events_before"]) + 1
        assert json.loads(events[-1]["payload_json"])["fields"] == ["title"]
    else:
        assert events == state["events_before"]


class _CasAfterFetchCursor:
    def __init__(self, cursor: Any, after_fetch: Callable[[list[Any]], None]) -> None:
        self.cursor, self.after_fetch = cursor, after_fetch

    def fetchall(self) -> list[Any]:
        rows = self.cursor.fetchall()
        self.after_fetch(rows)
        return rows

    def __getattr__(self, name: str) -> Any:
        return getattr(self.cursor, name)


class _CasAfterReadConnection:
    def __init__(self, conn: Any, marker: str, after_fetch: Callable[[list[Any]], None]) -> None:
        self.conn, self.marker, self.after_fetch = conn, marker, after_fetch
        self.observed = False

    def execute(self, sql: str, params: Any = ()) -> Any:
        cursor = self.conn.execute(sql, params)
        if not self.observed and sql.startswith(self.marker):
            self.observed = True
            return _CasAfterFetchCursor(cursor, self.after_fetch)
        return cursor

    def __getattr__(self, name: str) -> Any:
        return getattr(self.conn, name)


@pytest.mark.parametrize("writer", CAS_WRITERS)
def test_035_backfill_helper_stale_snapshot_keeps_normal_writer_key(
    tmp_path: Path, writer: str
) -> None:
    pool = SqlitePool(tmp_path / "cas-helper.db")
    try:
        run_migrations(pool)
        state = _cas_replay_setup(pool, writer)
        reached = []

        def after_fetch(rows: list[Any]) -> None:
            assert rows and [row["id"] for row in rows] == sorted(row["id"] for row in rows)
            reached.append(True)
            _cas_writer(pool, state, writer)

        marker = (
            "SELECT id,title,title_search_key FROM project_todos"
            if writer == "title"
            else "SELECT id,username,display_name,project_plan_display_sort_key FROM users"
        )
        migration = import_module("octop.infra.db.migrate")
        with pool.connect() as conn:
            assert not conn.in_transaction
            observed = _CasAfterReadConnection(conn, marker, after_fetch)
            # Direct helper schedule has no outer BEGIN IMMEDIATE. It is not production SQLite concurrency.
            migration._apply_project_todo_views_v35(
                observed,
                (MIGRATIONS / "035_project_todo_views.sql").read_text(encoding="utf-8"),
                dialect="sqlite",
            )
        assert reached == [True] and observed.observed
        _cas_assert_final(pool, state, writer)
    finally:
        pool.close()


@pytest.mark.parametrize(
    "display", [None, "Unchanged Ｓtraße"], ids=["null-source", "nonnull-source"]
)
def test_035_replay_unchanged_source_repairs_untrusted_key(
    tmp_path: Path, display: str | None
) -> None:
    pool = SqlitePool(tmp_path / "cas-unchanged.db")
    try:
        run_migrations(pool)
        owner = UserRepo(pool).create(
            username="Fallback Ｓtraße",
            password_hash="synthetic",
            role="admin",
            display_name=display,
        )
        project = ProjectRepo(pool).create_with_owner(
            creator_user_id=owner, name="unchanged source"
        )
        todo = (
            ProjectTodoRepo(pool)
            .create(project_id=project.project_id, creator_user_id=owner, title="Unchanged Ｔitle")
            .row
        )
        assert todo is not None
        before_user = capture_users_from_pool(pool)
        before_plan = _cas_stable_plan(pool, project.project_id)
        before_events = _cas_events(pool, project.project_id)
        with pool.transaction() as conn:
            conn.execute("UPDATE users SET project_plan_display_sort_key='' WHERE id=?", (owner,))
            conn.execute(
                "UPDATE project_todos SET title_search_key='' WHERE todo_id=?", (todo.todo_id,)
            )
        _replay_035(pool)
        with pool.connect() as conn:
            assert conn.execute(
                "SELECT project_plan_display_sort_key FROM users WHERE id=?", (owner,)
            ).fetchone()[0] == _key(display or "Fallback Ｓtraße")
            row = conn.execute(
                "SELECT title,title_search_key,version FROM project_todos WHERE todo_id=?",
                (todo.todo_id,),
            ).fetchone()
            assert (row["title"], row["title_search_key"], row["version"]) == (
                "Unchanged Ｔitle",
                _key("Unchanged Ｔitle"),
                1,
            )
        assert capture_users_from_pool(pool) == before_user
        assert _cas_stable_plan(pool, project.project_id) == before_plan
        assert _cas_events(pool, project.project_id) == before_events
    finally:
        pool.close()
