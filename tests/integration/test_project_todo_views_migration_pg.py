"""Q2 real PostgreSQL migration gates use only the root-issued synthetic lease."""

from __future__ import annotations

import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

import pytest

from octop.infra.backup.snapshot import capture_users_from_pool, upsert_users_into_pool
from octop.infra.db.migrate import _max_discovered_version, run_migrations
from octop.infra.db.pool import PostgresPool
from octop.infra.db.repos._base import now_ts
from octop.infra.db.repos.invites import InviteRepo
from octop.infra.db.repos.project_todos import ProjectTodoRepo
from octop.infra.db.repos.projects import ProjectRepo
from octop.infra.db.repos.users import UserRepo
from tests.integration.test_project_todo_fields_backup_failure_pg import _database_state
from tests.integration.test_project_todo_fields_pg import (
    _ObservedPool,
    _race,
)
from tests.integration.test_project_todo_fields_pg import (
    postgres_pair as postgres_pair,
)
from tests.unit.db.test_project_todo_views_migration import (
    CAS_NEW_NAME,
    CAS_WRITERS,
    DISPLAY,
    FAULT_SQL,
    TITLE,
    _assert_035,
    _assert_defaults,
    _cas_assert_final,
    _cas_events,
    _cas_expected_user,
    _cas_replay_setup,
    _cas_writer,
    _CasAfterReadConnection,
    _key,
    _seed_legacy,
)


def _rows(conn: Any, sql: str, params: Any = ()) -> list[tuple[Any, ...]]:
    return [tuple(row[key] for key in row) for row in conn.execute(sql, params)]


@contextmanager
def _pg_fault_after_sql(
    pool: PostgresPool,
    monkeypatch: pytest.MonkeyPatch,
    marker: str,
    failure: type[BaseException],
) -> Iterator[list[dict[str, Any]]]:
    real_connect = pool.connect
    reached: list[dict[str, Any]] = []

    class Connection:
        def __init__(self, conn: Any) -> None:
            self.conn = conn

        def execute(self, sql: str, params: Any = ()) -> Any:
            result = self.conn.execute(sql, params)
            if sql.strip().startswith(marker) and not reached:
                reached.append(
                    {
                        "sql_executed": True,
                        "transaction_status": str(self.conn.info.transaction_status),
                        "pid": self.conn.execute("SELECT pg_backend_pid()").fetchone()[0],
                    }
                )
                raise failure("injected after actual PG 035 SQL")
            return result

        def __getattr__(self, name: str) -> Any:
            return getattr(self.conn, name)

    @contextmanager
    def connect() -> Iterator[Connection]:
        with real_connect() as conn:
            yield Connection(conn)

    with monkeypatch.context() as patch:
        patch.setattr(pool, "connect", connect)
        yield reached


@pytest.mark.parametrize("postgres_pair", [33, 34, 35], indirect=True)
def test_real_pg_fresh_and_historical_035_preserve_full_keys_and_rows(
    postgres_pair: dict[str, Any],
    request: pytest.FixtureRequest,
) -> None:
    env = postgres_pair
    pool = env["first"]
    with pool.connect() as conn:
        version = int(conn.execute("SELECT version FROM _schema_version").fetchone()[0])
    _seed_legacy(pool, version)
    with pool.connect() as conn:
        columns = [
            row[0]
            for row in conn.execute(
                "SELECT column_name FROM information_schema.columns WHERE table_schema=current_schema() "
                "AND table_name='project_todos' ORDER BY ordinal_position"
            )
            if row[0] != "title_search_key"
        ]
        projection = ",".join(columns)
        todos = _rows(conn, f"SELECT {projection} FROM project_todos ORDER BY id")
        children = {
            table: _rows(conn, f"SELECT * FROM {table} ORDER BY 1")
            for table in (
                "project_todo_comments",
                "project_todo_comment_images",
                "project_todo_comment_image_usage",
            )
        }
    run_migrations(pool)
    with env["second"].connect() as conn:
        _assert_035(conn)
        assert _rows(conn, f"SELECT {projection} FROM project_todos ORDER BY id") == todos
        for table, original in children.items():
            assert _rows(conn, f"SELECT * FROM {table} ORDER BY 1") == original
        _assert_defaults(conn, "p1")
        _assert_defaults(conn, "p2")
        assert conn.execute(
            "SELECT title_search_key FROM project_todos WHERE todo_id='t1'"
        ).fetchone()[0] == _key(TITLE)
        assert _rows(conn, "SELECT project_plan_display_sort_key FROM users ORDER BY id") == [
            (_key(DISPLAY),),
            (_key("\ufdfa" * 200),),
        ]
        indexes = [
            str(row[0])
            for row in conn.execute(
                "SELECT indexdef FROM pg_indexes WHERE schemaname=current_schema()"
            )
        ]
        assert all(
            "title_search_key" not in sql and "project_plan_display_sort_key" not in sql
            for sql in indexes
        ), "complete unbounded keys must not exceed PG Btree tuple limits"
    env["report"]["q2_history"] = {"source_version": version, "nodeid": request.node.nodeid}


@pytest.mark.parametrize("postgres_pair", [34], indirect=True)
@pytest.mark.parametrize("phase", list(FAULT_SQL))
@pytest.mark.parametrize("failure", [RuntimeError, KeyboardInterrupt])
def test_real_pg_035_sql_then_fault_rolls_back_every_schema_row_sequence_and_watermark(
    postgres_pair: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
    phase: str,
    failure: type[BaseException],
) -> None:
    env = postgres_pair
    pool = env["first"]
    _seed_legacy(pool, 34)
    before = _database_state(pool)
    with (
        _pg_fault_after_sql(pool, monkeypatch, FAULT_SQL[phase], failure) as reached,
        pytest.raises(failure, match="injected after actual PG 035 SQL"),
    ):
        run_migrations(pool)
    assert len(reached) == 1 and reached[0]["sql_executed"]
    assert reached[0]["pid"] == env["report"]["pool_0_pid"]
    assert reached[0]["transaction_status"] == "2"  # psycopg INTRANS
    assert _database_state(env["second"]) == before
    with pool.connect() as conn:
        assert str(conn.info.transaction_status) == "0"
        assert conn.execute("SELECT version FROM _schema_version").fetchone()[0] == 34
    run_migrations(pool)
    with env["second"].connect() as conn:
        _assert_035(conn)
        _assert_defaults(conn, "p1")
    env["report"]["q2_fault"] = {"phase": phase, "failure": failure.__name__, "reached": reached}


@pytest.mark.parametrize("postgres_pair", [33], indirect=True)
def test_real_pg_committed_034_is_kept_when_the_next_035_transaction_interrupts(
    postgres_pair: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pool = postgres_pair["first"]
    _seed_legacy(pool, 33)
    with (
        _pg_fault_after_sql(pool, monkeypatch, FAULT_SQL["ddl"], KeyboardInterrupt) as reached,
        pytest.raises(KeyboardInterrupt, match="injected after actual PG 035 SQL"),
    ):
        run_migrations(pool)
    assert len(reached) == 1
    with postgres_pair["second"].connect() as conn:
        assert conn.execute("SELECT version FROM _schema_version").fetchone()[0] == 34
        columns = {
            row[0]
            for row in conn.execute(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_schema=current_schema() AND table_name='project_todos'"
            )
        }
        assert {"start_date", "due_date", "priority_id"} <= columns
        assert "title_search_key" not in columns
        assert _rows(conn, "SELECT title,version FROM project_todos WHERE todo_id='t1'") == [
            (TITLE, 7)
        ]
    run_migrations(pool)
    with pool.connect() as conn:
        _assert_035(conn)


def test_real_pg_key_writers_include_invite_sso_final_snapshot_and_long_values(
    postgres_pair: dict[str, Any],
) -> None:
    pool = postgres_pair["first"]
    users = UserRepo(pool)
    owner = users.create(username="\ufdfa" * 200, display_name=DISPLAY, role="admin")
    project = ProjectRepo(pool).create_with_owner(creator_user_id=owner, name="Q2 key writers")
    todos = ProjectTodoRepo(pool)
    todo = todos.create(project_id=project.project_id, creator_user_id=owner, title=TITLE).row
    assert todo is not None
    updated = todos.update(
        project_id=project.project_id,
        todo_id=todo.todo_id,
        actor_user_id=owner,
        expected_version=1,
        title="\ufdfa" * 199 + "Ｚ",
    )
    assert updated.outcome == "updated"
    users.update_sso_profile(owner, display_name="Ｓtraße")
    users.set_display_name(owner, "")
    invite = InviteRepo(pool).create(
        code="Q2PGInviteA",
        created_by=owner,
        expires_at=now_ts() + 3600,
    )
    invited, _ = InviteRepo(pool).redeem_creating_user(
        code=invite.code,
        username="Ｆallback",
        password_hash="synthetic",
        display_name=DISPLAY,
        locale="zh",
    )
    saved = capture_users_from_pool(pool)
    assert all(len(row) == 11 for row in saved)
    final = list(next(row for row in saved if row[0] == invited))
    final[1], final[4] = "final-user", "\ufdfa" * 2000 + "Final"
    upsert_users_into_pool(pool, [tuple(final)])
    with postgres_pair["second"].connect() as conn:
        assert conn.execute(
            "SELECT title_search_key FROM project_todos WHERE todo_id=?", (todo.todo_id,)
        ).fetchone()[0] == _key("\ufdfa" * 199 + "Ｚ")
        assert _rows(conn, "SELECT project_plan_display_sort_key FROM users ORDER BY id") == [
            (_key("\ufdfa" * 200),),
            (_key(str(final[4])),),
        ]


def test_real_pg_default_is_same_project_and_active_names_keep_history(
    postgres_pair: dict[str, Any],
) -> None:
    import psycopg

    pool = postgres_pair["first"]
    owner = UserRepo(pool).create(username="owner", role="admin")
    first = ProjectRepo(pool).create_with_owner(creator_user_id=owner, name="first").project_id
    other = ProjectRepo(pool).create_with_owner(creator_user_id=owner, name="other").project_id
    with pool.connect() as conn:
        foreign = conn.execute(
            "SELECT default_view_id FROM project_todo_view_state WHERE project_id=?", (other,)
        ).fetchone()[0]
        original_default = conn.execute(
            "SELECT default_view_id FROM project_todo_view_state WHERE project_id=?", (first,)
        ).fetchone()[0]
    with pytest.raises(psycopg.errors.ForeignKeyViolation), pool.transaction() as conn:
        conn.execute(
            "UPDATE project_todo_view_state SET default_view_id=? WHERE project_id=?",
            (foreign, first),
        )
    for statement in (
        "UPDATE project_todo_view_state SET revision=0",
        "UPDATE project_todo_views SET version=0",
        "UPDATE project_todo_views SET position=-1",
        "UPDATE project_todo_views SET view_type='unknown'",
        "UPDATE project_todo_views SET definition_json='[]'",
    ):
        with pytest.raises(psycopg.errors.CheckViolation), pool.transaction() as conn:
            conn.execute(statement)
    before = _database_state(pool)
    with pool.transaction() as conn:
        conn.execute(
            "UPDATE project_todo_view_state SET default_view_id=NULL WHERE project_id=?", (first,)
        )
    incomplete = _database_state(pool)
    with pytest.raises(RuntimeError, match="incomplete view seed"):
        run_migrations(pool)
    assert _database_state(pool) == incomplete
    with pool.transaction() as conn:
        conn.execute(
            "UPDATE project_todo_view_state SET default_view_id=? WHERE project_id=?",
            (original_default, first),
        )
    assert _database_state(pool) == before


@pytest.mark.parametrize("writer", ["display", "sso"])
def test_real_pg_name_read_locks_with_final_snapshot_username_upsert(
    postgres_pair: dict[str, Any],
    writer: str,
) -> None:
    env = postgres_pair
    pool = env["first"]
    owner = UserRepo(pool).create(username="old-user", display_name="old display", role="admin")
    saved = list(capture_users_from_pool(pool)[0])
    saved[1], saved[4] = "Ｆinal-user", None
    first = _ObservedPool(pool, hold_sql="SELECT username FROM users")
    second = _ObservedPool(env["second"])

    def edit_name() -> None:
        repo = UserRepo(first)
        if writer == "display":
            repo.set_display_name(owner, "")
        else:
            repo.update_sso_profile(owner, display_name="")

    _race(env, first, second, edit_name, lambda: upsert_users_into_pool(second, [tuple(saved)]))
    with pool.connect() as conn:
        row = conn.execute("SELECT * FROM users WHERE id=?", (owner,)).fetchone()
        assert row["username"] == "Ｆinal-user" and row["display_name"] is None
        assert row["project_plan_display_sort_key"] == "final-user"


def test_real_pg_future_watermark_keeps_unknown_schema_and_derived_values(
    postgres_pair: dict[str, Any],
) -> None:
    pool = postgres_pair["first"]
    owner = UserRepo(pool).create(username="owner", role="admin")
    project = ProjectRepo(pool).create_with_owner(creator_user_id=owner, name="future")
    todo = (
        ProjectTodoRepo(pool)
        .create(
            project_id=project.project_id,
            creator_user_id=owner,
            title="original",
        )
        .row
    )
    assert todo is not None
    with pool.transaction() as conn:
        conn.execute("ALTER TABLE project_todos ADD COLUMN future_data TEXT")
        conn.execute(
            "UPDATE project_todos SET future_data='preserved',title_search_key='future key'"
        )
        conn.execute(
            "UPDATE _schema_version SET version=?", (_max_discovered_version("postgresql") + 1,)
        )
    before = _database_state(pool)
    run_migrations(pool)
    run_migrations(pool)
    assert _database_state(postgres_pair["second"]) == before


@pytest.mark.parametrize("writer", CAS_WRITERS)
def test_real_pg_035_replay_keeps_committed_source_key(
    postgres_pair: dict[str, Any], monkeypatch: pytest.MonkeyPatch, writer: str
) -> None:
    env = postgres_pair
    first, second = env["first"], env["second"]
    state = _cas_replay_setup(first, writer)
    held, release, committed = threading.Event(), threading.Event(), threading.Event()
    errors: list[BaseException] = []
    evidence: dict[str, Any] = {
        "writer": writer,
        "first_pid": env["report"]["pool_0_pid"],
        "second_pid": env["report"]["pool_1_pid"],
        "full_canonical_run_migrations": True,
        "DDL_skipped_by_test": False,
        "writer_committed_before_release": False,
    }
    assert evidence["first_pid"] != evidence["second_pid"]
    observed_writer = _ObservedPool(second)
    real_connect = first.connect
    marker = (
        "SELECT id,title,title_search_key FROM project_todos"
        if writer == "title"
        else "SELECT id,username,display_name,project_plan_display_sort_key FROM users"
    )

    @contextmanager
    def connect() -> Iterator[Any]:
        with real_connect() as conn:

            def after_fetch(rows: list[Any]) -> None:
                assert rows and [row["id"] for row in rows] == sorted(row["id"] for row in rows)
                evidence["actual_fetchall_completed"] = True
                evidence["captured_ids"] = [row["id"] for row in rows]
                evidence["migration_transaction_status_after_read"] = str(
                    conn.info.transaction_status
                )
                evidence["isolation"] = conn.execute("SHOW transaction_isolation").fetchone()[0]
                assert (
                    conn.execute("SELECT pg_backend_pid()").fetchone()[0] == evidence["first_pid"]
                )
                assert evidence["migration_transaction_status_after_read"] == "2"
                assert evidence["isolation"] == "read committed"
                held.set()
                assert release.wait(10), "real backfill read checkpoint timed out"

            yield _CasAfterReadConnection(conn, marker, after_fetch)

    def migrate() -> None:
        try:
            run_migrations(first)
        except BaseException as exc:
            errors.append(exc)

    def write() -> None:
        try:
            _cas_writer(observed_writer, state, writer)
            evidence["writer_commit_monotonic"] = time.monotonic()
            committed.set()
        except BaseException as exc:
            errors.append(exc)

    replay = threading.Thread(target=migrate, daemon=True)
    normal_writer = threading.Thread(target=write, daemon=True)
    with monkeypatch.context() as patch:
        patch.setattr(first, "connect", connect)
        try:
            replay.start()
            assert held.wait(5), "canonical run_migrations did not reach real backfill fetchall"
            normal_writer.start()
            assert observed_writer.started.wait(3)
            assert observed_writer.pid == evidence["second_pid"]
            assert committed.wait(5), (
                "ordinary writer did not really commit while canonical replay was paused"
            )
            assert not errors, errors
            assert not release.is_set()
            evidence["writer_committed_before_release"] = True
            # An actual observer query is recorded without claiming an unobserved lock wait.
            observed = (
                env["admin"]
                .execute(
                    "SELECT wait_event_type,pg_blocking_pids(pid) FROM pg_stat_activity WHERE pid=%s",
                    (observed_writer.pid,),
                )
                .fetchone()
            )
            assert observed is not None
            evidence["writer_wait_event_type_after_commit"] = observed[0]
            evidence["writer_blocking_pids_after_commit"] = list(observed[1])
            assert not observed[1]
            evidence["committed_events_on_second_connection"] = _cas_events(
                second, state["project_id"]
            )
            with second.connect() as conn:
                assert str(conn.info.transaction_status) == "0"
                # No nested second-pool borrow (its max_size is one) while this connection is held.
                source = (
                    conn.execute(
                        "SELECT title,title_search_key,version FROM project_todos WHERE todo_id=?",
                        (state["todo_id"],),
                    ).fetchone()
                    if writer == "title"
                    else conn.execute(
                        "SELECT username,display_name,project_plan_display_sort_key FROM users WHERE id=?",
                        (state["owner"],),
                    ).fetchone()
                )
                evidence["committed_source_row"] = dict(source)
                if writer == "title":
                    assert (source["title"], source["title_search_key"], source["version"]) == (
                        CAS_NEW_NAME,
                        _key(CAS_NEW_NAME),
                        2,
                    )
                else:
                    expected = _cas_expected_user(state, writer)
                    assert (source["username"], source["display_name"]) == (
                        expected[1],
                        expected[4],
                    )
                    assert source["project_plan_display_sort_key"] == _key(
                        str(expected[4] or expected[1])
                    )
                    assert (
                        conn.execute(
                            "SELECT project_plan_display_sort_key FROM users WHERE id=?",
                            (state["sentinel"],),
                        ).fetchone()[0]
                        == ""
                    )
            evidence["release_monotonic"] = time.monotonic()
        finally:
            release.set()
            replay.join(10)
            if normal_writer.ident is not None:
                normal_writer.join(10)
            evidence["alive"] = [replay.is_alive(), normal_writer.is_alive()]
            evidence["errors"] = [repr(error) for error in errors]
            env["report"].setdefault("backfill_replays", []).append(evidence)
    assert evidence["alive"] == [False, False]
    assert not errors, errors
    assert evidence["writer_committed_before_release"]
    assert evidence["writer_commit_monotonic"] < evidence["release_monotonic"]
    _cas_assert_final(second, state, writer)
