"""Real C1 PostgreSQL gates, enabled only by our fresh isolated cluster lease.

This module never consumes an application DSN. The optional QA context must
identify the current checkout, a fresh /tmp cluster and its synthetic owner.
"""

from __future__ import annotations

import json
import os
import threading
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from importlib import import_module
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest

from octop.config import DatabaseConfig
from octop.infra.backup.system_archive import create_system_backup, restore_system_backup
from octop.infra.db.migrate import _max_discovered_version, run_migrations
from octop.infra.db.pool import PostgresPool
from octop.infra.db.project_plan_seed import seed_todo_catalog
from octop.infra.db.repos.project_todo_catalog import ProjectTodoCatalogRepo
from octop.infra.db.repos.project_todos import ProjectTodoRepo
from octop.infra.db.repos.projects import ProjectRepo
from octop.infra.db.repos.users import UserRepo
from octop.infra.utils.paths import PathLayout
from octop.infra.utils.ulid import new_ulid
from tests.unit.backup.test_project_todo_fields_archive import _seed_archive_rows


def _identify(conn: Any, context: dict[str, Any], database: str) -> int:
    row = conn.execute(
        "SELECT current_database(),current_user,inet_server_port(),pg_backend_pid(),"
        "host(inet_server_addr())"
    ).fetchone()
    directory = conn.execute("SHOW data_directory").fetchone()[0]
    assert (row[0], row[1], row[2], row[4]) == (
        database,
        context["user"],
        context["port"],
        "127.0.0.1",
    )
    assert str(directory) == context["cluster"]
    return int(row[3])


@pytest.fixture
def postgres_pair(
    request: pytest.FixtureRequest,
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[dict[str, Any]]:
    context_file = os.environ.get("XIONGBAO_PS04C_PG_CONTEXT")
    if not context_file:
        pytest.skip("requires the dedicated PS04C PostgreSQL cluster lease")
    import psycopg
    from psycopg.conninfo import make_conninfo
    from psycopg.sql import SQL, Identifier

    repo = Path(__file__).resolve().parents[2]
    context_path = Path(context_file).resolve()
    assert context_path.parent.parent == repo.parent / "qa-ps04c-20260928" / "pg"
    context = json.loads(context_path.read_text(encoding="utf-8-sig"))
    assert context["state"] == "READY" and Path(context["repo"]).resolve() == repo
    assert 1024 < context["port"] < 65536 and context["port"] != 5432
    assert context["user"].startswith("ps04c_qa_")
    assert context["database_prefix"].startswith("ps04c_")
    cluster = Path(context["cluster"]).resolve()
    assert cluster.parent.parent == Path("/tmp")
    assert cluster.parent.name.startswith("xiongbao-ps04c-http-")
    database = context["database_prefix"] + uuid4().hex[:12]
    pools: list[PostgresPool] = []
    report: dict[str, Any] = {"nodeid": request.node.nodeid, "database": database, "races": []}
    with psycopg.connect(
        host="127.0.0.1",
        port=context["port"],
        user=context["user"],
        dbname="postgres",
        connect_timeout=3,
        autocommit=True,
    ) as admin:
        report["admin_pid"] = _identify(admin, context, "postgres")
        admin.execute(SQL("CREATE DATABASE {}").format(Identifier(database)))
        try:
            for index in range(2):
                pool = PostgresPool(
                    make_conninfo(
                        host="127.0.0.1",
                        port=context["port"],
                        user=context["user"],
                        dbname=database,
                        connect_timeout=3,
                    ),
                    min_size=1,
                    max_size=1,
                )
                pools.append(pool)
                with pool.connect() as conn:
                    report[f"pool_{index}_pid"] = _identify(conn, context, database)
            assert report["pool_0_pid"] != report["pool_1_pid"]
            historical_version = getattr(request, "param", None)
            if historical_version in (33, 34):
                migration = import_module("octop.infra.db.migrate")
                discover = migration._discover
                with monkeypatch.context() as patch:
                    patch.setattr(
                        migration,
                        "_discover",
                        lambda dialect: [
                            (v, p) for v, p in discover(dialect) if v <= historical_version
                        ],
                    )
                    run_migrations(pools[0])
            else:
                run_migrations(pools[0])
            yield {
                "first": pools[0],
                "second": pools[1],
                "admin": admin,
                "context": context,
                "database": database,
                "report": report,
            }
        finally:
            for pool in pools:
                pool.close()
            cleanup_started = time.monotonic()
            close_checks = []
            while True:
                report["remaining_connections"] = admin.execute(
                    "SELECT COUNT(*) FROM pg_stat_activity WHERE datname=%s", (database,)
                ).fetchone()[0]
                elapsed = time.monotonic() - cleanup_started
                close_checks.append(
                    {"elapsed_seconds": elapsed, "connections": report["remaining_connections"]}
                )
                if report["remaining_connections"] == 0 or elapsed >= 2:
                    break
                time.sleep(min(0.02, 2 - elapsed))
            report["connection_cleanup"] = {"timeout_seconds": 2, "checks": close_checks}
            try:
                assert report["remaining_connections"] == 0
                admin.execute(SQL("DROP DATABASE {}").format(Identifier(database)))
                report["database_dropped"] = True
            finally:
                evidence = context_path.parent / "concurrency"
                evidence.mkdir(exist_ok=True)
                (evidence / f"{database}.json").write_text(
                    json.dumps(report, indent=2) + "\n", encoding="utf-8"
                )


class _ObservedConnection:
    def __init__(self, conn: Any, observer: _ObservedPool) -> None:
        self._conn, self._observer = conn, observer

    def execute(self, sql: str, params: Any = None) -> Any:
        # The production SQL runs unchanged. Only its timing is controlled.
        result = self._conn.execute(sql, params)
        observer = self._observer
        if observer.hold_sql and observer.hold_sql in sql and "FOR UPDATE" in sql:
            observer.hold_sql = ""
            observer.locked.set()
            assert observer.release.wait(5), "owned transaction checkpoint timed out"
        return result

    def __getattr__(self, name: str) -> Any:
        return getattr(self._conn, name)


class _ObservedPool:
    dialect = "postgresql"

    def __init__(self, pool: PostgresPool, *, hold_sql: str = "") -> None:
        self._pool, self.hold_sql = pool, hold_sql
        self.locked, self.release, self.started = (
            threading.Event(),
            threading.Event(),
            threading.Event(),
        )
        self.pid = 0

    @contextmanager
    def transaction(self) -> Iterator[_ObservedConnection]:
        with self._pool.transaction() as conn:
            conn.execute("SET LOCAL lock_timeout='4s'")
            conn.execute("SET LOCAL statement_timeout='8s'")
            self.pid = int(conn.execute("SELECT pg_backend_pid()").fetchone()[0])
            self.started.set()
            yield _ObservedConnection(conn, self)

    @contextmanager
    def connect(self) -> Iterator[Any]:
        with self._pool.connect() as conn:
            yield conn

    def close(self) -> None:
        self._pool.close()


def _race(
    env: dict[str, Any],
    first: _ObservedPool,
    second: _ObservedPool,
    operation_a: Callable[[], Any],
    operation_b: Callable[[], Any],
    on_wait: Callable[[], None] | None = None,
) -> tuple[Any, Any]:
    results: dict[str, Any] = {}
    errors: list[BaseException] = []

    def run(name: str, operation: Callable[[], Any]) -> None:
        try:
            results[name] = operation()
        except BaseException as exc:
            errors.append(exc)

    one = threading.Thread(target=run, args=("a", operation_a), daemon=True)
    two = threading.Thread(target=run, args=("b", operation_b), daemon=True)
    blocking: list[int] = []
    try:
        one.start()
        assert first.locked.wait(5), "first operation did not acquire the expected SQL lock"
        two.start()
        assert second.started.wait(3)
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline:
            row = (
                env["admin"]
                .execute(
                    "SELECT wait_event_type,pg_blocking_pids(pid) FROM pg_stat_activity WHERE pid=%s",
                    (second.pid,),
                )
                .fetchone()
            )
            if row and row[0] == "Lock" and first.pid in row[1]:
                blocking = row[1]
                break
            time.sleep(0.02)
        assert blocking, "real PG did not observe the second transaction waiting on the first"
        if on_wait is not None:
            on_wait()
    finally:
        first.release.set()
        one.join(10)
        if two.ident is not None:
            two.join(10)
        env["report"]["races"].append(
            {
                "first_pid": first.pid,
                "second_pid": second.pid,
                "blocking_pids": blocking,
                "alive": [one.is_alive(), two.is_alive()],
                "errors": [repr(error) for error in errors],
            }
        )
    assert not one.is_alive() and not two.is_alive()
    assert not errors, errors
    return results["a"], results["b"]


def _setup(env: dict[str, Any]) -> tuple[int, int, int, str]:
    first = env["first"]
    users = UserRepo(first)
    owner = users.create(username="pg-owner", password_hash="synthetic", role="user")
    member = users.create(username="pg-member", password_hash="synthetic", role="user")
    admin = users.create(username="pg-admin", password_hash="synthetic", role="user")
    projects = ProjectRepo(first)
    pid = projects.create_with_owner(creator_user_id=owner, name="PG C1").project_id
    projects.add_member(pid, member)
    projects.add_member(pid, admin, role="admin")
    return owner, member, admin, pid


def _tag(db: Any, pid: str, actor: int, revision: int, name: str = "tag") -> Any:
    return ProjectTodoCatalogRepo(db).create_option(
        project_id=pid,
        actor_user_id=actor,
        expected_revision=revision,
        kind="tag",
        name=name,
        color="purple",
    )


def _event_count(pool: PostgresPool, pid: str) -> int:
    with pool.connect() as conn:
        return int(
            conn.execute(
                "SELECT COUNT(*) FROM project_events WHERE project_id=?", (pid,)
            ).fetchone()[0]
        )


def test_real_catalog_revision_and_normalized_name_race(postgres_pair: dict[str, Any]) -> None:
    env = postgres_pair
    owner, _, admin, pid = _setup(env)
    first = _ObservedPool(env["first"], hold_sql="SELECT revision FROM project_todo_catalog_state")
    second = _ObservedPool(env["second"])
    a, b = _race(
        env,
        first,
        second,
        lambda: _tag(first, pid, owner, 1, "ＦＯＯ"),
        lambda: _tag(second, pid, admin, 1, "foo"),
    )
    assert (a.outcome, b.outcome) == ("created", "stale")
    before = _event_count(env["first"], pid)
    retry = _tag(env["second"], pid, admin, 2, "foo")
    assert retry.outcome == "name_conflict"
    loaded = ProjectTodoCatalogRepo(env["first"]).get_catalog(pid, user_id=owner)
    assert loaded is not None and loaded.revision == 2 and len(loaded.tags) == 1
    assert _event_count(env["first"], pid) == before


@pytest.mark.parametrize(
    "kind,limit_kind,limit",
    [
        ("priority", "active", 32),
        ("priority", "total", 128),
        ("tag", "active", 100),
        ("tag", "total", 500),
    ],
)
def test_real_catalog_limit_race_cannot_overflow(
    postgres_pair: dict[str, Any],
    kind: str,
    limit_kind: str,
    limit: int,
) -> None:
    env = postgres_pair
    owner, _, admin, pid = _setup(env)
    table, id_column = (
        ("project_todo_priorities", "priority_id")
        if kind == "priority"
        else ("project_todo_tags", "tag_id")
    )
    with env["first"].transaction() as conn:
        count = int(
            conn.execute(f"SELECT COUNT(*) FROM {table} WHERE project_id=?", (pid,)).fetchone()[0]
        )
        for index in range(count, limit - 1):
            columns = (
                f"{id_column},project_id,name,name_key,color,archived_at,created_at,updated_at"
            )
            values: list[Any] = [
                new_ulid(),
                pid,
                f"fixture{index}",
                f"fixture{index}",
                "gray",
                None if limit_kind == "active" else 1,
                1,
                1,
            ]
            if kind == "priority":
                columns += ",position"
                values.append(index)
            conn.execute(
                f"INSERT INTO {table}({columns}) VALUES ({','.join('?' for _ in values)})", values
            )
    first = _ObservedPool(env["first"], hold_sql="SELECT revision FROM project_todo_catalog_state")
    second = _ObservedPool(env["second"])

    def create(db: Any, actor: int, revision: int, name: str) -> Any:
        return ProjectTodoCatalogRepo(db).create_option(
            project_id=pid,
            actor_user_id=actor,
            expected_revision=revision,
            kind=kind,
            name=name,
            color="blue",
        )

    before = _event_count(env["first"], pid)
    a, b = _race(
        env,
        first,
        second,
        lambda: create(first, owner, 1, "last"),
        lambda: create(second, admin, 1, "overflow"),
    )
    assert (a.outcome, b.outcome) == ("created", "stale")
    assert create(env["second"], admin, 2, "overflow").outcome == f"{limit_kind}_limit"
    assert _event_count(env["first"], pid) == before + 1
    with env["first"].connect() as conn:
        where = " AND archived_at IS NULL" if limit_kind == "active" else ""
        assert (
            conn.execute(
                f"SELECT COUNT(*) FROM {table} WHERE project_id=?{where}", (pid,)
            ).fetchone()[0]
            == limit
        )
        assert (
            conn.execute(
                "SELECT revision FROM project_todo_catalog_state WHERE project_id=?", (pid,)
            ).fetchone()[0]
            == 2
        )


def test_real_clock_after_lock_wait_rejects_newly_past_due_date(
    postgres_pair: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    env = postgres_pair
    owner, member, _, pid = _setup(env)
    clock = {"today": "2026-09-28"}
    checks: list[str] = []

    def server_today(_timezone: str) -> str:
        checks.append(clock["today"])
        return clock["today"]

    monkeypatch.setattr("octop.infra.db.repos.project_plan_locks.server_today", server_today)
    todo = (
        ProjectTodoRepo(env["first"])
        .create(
            project_id=pid,
            creator_user_id=member,
            title="midnight",
            due_date="9999-12-31",
        )
        .row
    )
    assert todo is not None
    checks.clear()
    first = _ObservedPool(env["first"], hold_sql="SELECT revision FROM project_todo_catalog_state")
    second = _ObservedPool(env["second"])
    before = _event_count(env["first"], pid)
    a, b = _race(
        env,
        first,
        second,
        lambda: _tag(first, pid, owner, 1),
        lambda: ProjectTodoRepo(second).update(
            project_id=pid,
            todo_id=todo.todo_id,
            actor_user_id=member,
            expected_version=1,
            title="must roll back",
            due_date="2026-09-28",
        ),
        on_wait=lambda: clock.update(today="2026-09-29"),
    )
    assert (a.outcome, b.outcome) == ("created", "invalid_dates")
    assert checks == ["2026-09-29"]
    loaded = ProjectTodoRepo(env["first"]).get(pid, todo.todo_id, user_id=owner)
    assert loaded is not None and (loaded.title, loaded.version, loaded.due_date) == (
        "midnight",
        1,
        "9999-12-31",
    )
    assert loaded.tag_ids == []
    assert _event_count(env["first"], pid) == before + 1


@pytest.mark.parametrize("winner", ["archive", "associate"])
def test_real_archive_and_new_association_are_atomic(
    postgres_pair: dict[str, Any],
    winner: str,
) -> None:
    env = postgres_pair
    owner, _, admin, pid = _setup(env)
    tag = _tag(env["first"], pid, owner, 1).item
    assert tag is not None
    first = _ObservedPool(env["first"], hold_sql="SELECT revision FROM project_todo_catalog_state")
    second = _ObservedPool(env["second"])

    def archive(db: Any) -> Any:
        return ProjectTodoCatalogRepo(db).set_archived(
            project_id=pid,
            actor_user_id=owner,
            expected_revision=2,
            kind="tag",
            option_id=tag.option_id,
            archived=True,
        )

    def associate(db: Any) -> Any:
        return ProjectTodoRepo(db).create(
            project_id=pid,
            creator_user_id=admin,
            title="association",
            tag_ids=[tag.option_id],
            expected_catalog_revision=2,
        )

    a, b = _race(
        env,
        first,
        second,
        lambda: archive(first) if winner == "archive" else associate(first),
        lambda: associate(second) if winner == "archive" else archive(second),
    )
    if winner == "archive":
        assert (a.outcome, b.outcome) == ("archived", "catalog_stale")
        with env["first"].connect() as conn:
            assert (
                conn.execute(
                    "SELECT COUNT(*) FROM project_todos WHERE project_id=?", (pid,)
                ).fetchone()[0]
                == 0
            )
            assert (
                conn.execute(
                    "SELECT COUNT(*) FROM project_todo_tag_links WHERE project_id=?", (pid,)
                ).fetchone()[0]
                == 0
            )
        retry = ProjectTodoRepo(env["first"]).create(
            project_id=pid,
            creator_user_id=admin,
            title="rejected",
            tag_ids=[tag.option_id],
            expected_catalog_revision=3,
        )
        assert retry.outcome == "invalid_tags"
    else:
        assert (a.outcome, b.outcome) == ("created", "archived")
        assert a.row is not None and a.row.catalog_revision == 2
        loaded = ProjectTodoRepo(env["first"]).get(pid, a.row.todo_id, user_id=owner)
        assert (
            loaded is not None
            and loaded.tag_ids == [tag.option_id]
            and loaded.catalog_revision == 3
        )


@pytest.mark.parametrize("winner", ["remove", "save"])
def test_real_member_removal_and_field_save_recheck_acl_and_version(
    postgres_pair: dict[str, Any],
    winner: str,
) -> None:
    env = postgres_pair
    owner, member, _, pid = _setup(env)
    tag = _tag(env["first"], pid, owner, 1).item
    assert tag is not None
    todo = (
        ProjectTodoRepo(env["first"])
        .create(
            project_id=pid,
            creator_user_id=member,
            assignee_user_id=member,
            title="original",
            start_date="2000-02-29",
            due_date="9999-12-31",
            tag_ids=[tag.option_id],
            expected_catalog_revision=2,
        )
        .row
    )
    assert todo is not None
    first = _ObservedPool(env["first"], hold_sql="SELECT archived FROM project_spaces")
    second = _ObservedPool(env["second"])

    def remove(db: Any) -> Any:
        return ProjectRepo(db).remove_member(project_id=pid, user_id=member, actor_user_id=owner)

    def save(db: Any) -> Any:
        return ProjectTodoRepo(db).update(
            project_id=pid,
            todo_id=todo.todo_id,
            actor_user_id=member,
            expected_version=1,
            title="saved",
            due_date="9999-12-30",
        )

    a, b = _race(
        env,
        first,
        second,
        lambda: remove(first) if winner == "remove" else save(first),
        lambda: save(second) if winner == "remove" else remove(second),
    )
    assert (a.outcome, b.outcome) == (
        ("removed", "not_member") if winner == "remove" else ("updated", "removed")
    )
    loaded = ProjectTodoRepo(env["first"]).get(pid, todo.todo_id, user_id=owner)
    assert loaded is not None and loaded.assignee_user_id is None
    assert loaded.tag_ids == [tag.option_id] and loaded.start_date == "2000-02-29"
    assert (loaded.title, loaded.version, loaded.due_date) == (
        ("original", 2, "9999-12-31") if winner == "remove" else ("saved", 3, "9999-12-30")
    )
    assert ProjectTodoRepo(env["first"]).get(pid, todo.todo_id, user_id=member) is None


@pytest.mark.parametrize("winner", ["remove", "bulk"])
def test_real_bulk_and_removal_lock_todos_in_one_order(
    postgres_pair: dict[str, Any],
    winner: str,
) -> None:
    env = postgres_pair
    owner, member, admin, pid = _setup(env)
    todos = ProjectTodoRepo(env["first"])
    rows = [
        todos.create(
            project_id=pid,
            creator_user_id=owner,
            assignee_user_id=member,
            title=f"T{i}",
        ).row
        for i in range(3)
    ]
    assert all(row is not None for row in rows)
    first = _ObservedPool(env["first"], hold_sql="SELECT archived FROM project_spaces")
    second = _ObservedPool(env["second"])

    def remove(db: Any) -> Any:
        return ProjectRepo(db).remove_member(project_id=pid, user_id=member, actor_user_id=owner)

    def bulk(db: Any) -> Any:
        return ProjectTodoRepo(db).bulk_update(
            project_id=pid,
            actor_user_id=admin,
            items=[(row.todo_id, 1) for row in reversed(rows) if row is not None],
            status="done",
        )

    a, b = _race(
        env,
        first,
        second,
        lambda: remove(first) if winner == "remove" else bulk(first),
        lambda: bulk(second) if winner == "remove" else remove(second),
    )
    assert (a.outcome, b.outcome) == (
        ("removed", "stale") if winner == "remove" else ("updated", "removed")
    )
    for row in rows:
        assert row is not None
        loaded = todos.get(pid, row.todo_id, user_id=owner)
        assert loaded is not None and loaded.assignee_user_id is None
        assert (loaded.status, loaded.version) == (
            ("todo", 2) if winner == "remove" else ("done", 3)
        )


def test_real_catalog_reader_waits_for_consistent_revision(postgres_pair: dict[str, Any]) -> None:
    env = postgres_pair
    owner, member, _, pid = _setup(env)
    first = _ObservedPool(env["first"], hold_sql="SELECT revision FROM project_todo_catalog_state")
    second = _ObservedPool(env["second"])
    a, b = _race(
        env,
        first,
        second,
        lambda: _tag(first, pid, owner, 1),
        lambda: ProjectTodoCatalogRepo(second).get_catalog(pid, user_id=member),
    )
    assert a.outcome == "created"
    assert b is not None and b.revision == 2 and len(b.tags) == 1


def test_real_todo_reader_gets_one_version_and_association_snapshot(
    postgres_pair: dict[str, Any],
) -> None:
    env = postgres_pair
    owner, member, _, pid = _setup(env)
    tag = _tag(env["first"], pid, owner, 1).item
    assert tag is not None
    todo = (
        ProjectTodoRepo(env["first"])
        .create(
            project_id=pid,
            creator_user_id=owner,
            title="old",
            tag_ids=[tag.option_id],
            expected_catalog_revision=2,
        )
        .row
    )
    assert todo is not None
    first = _ObservedPool(env["first"], hold_sql="SELECT revision FROM project_todo_catalog_state")
    second = _ObservedPool(env["second"])
    a, b = _race(
        env,
        first,
        second,
        lambda: ProjectTodoRepo(first).update(
            project_id=pid,
            todo_id=todo.todo_id,
            actor_user_id=owner,
            expected_version=1,
            title="new",
            tag_ids=[],
            expected_catalog_revision=2,
        ),
        lambda: ProjectTodoRepo(second).get(pid, todo.todo_id, user_id=member),
    )
    assert a.outcome == "updated" and a.row is not None and b is not None
    assert (a.row.version, a.row.title, a.row.tag_ids, a.row.catalog_revision) == (2, "new", [], 2)
    assert (b.version, b.title, b.tag_ids, b.catalog_revision) == (2, "new", [], 2)


def test_real_archived_project_recheck_keeps_old_todo_acl(
    postgres_pair: dict[str, Any],
) -> None:
    # PS09 has not shipped an archive endpoint. This is an explicit SQL fixture
    # of the existing archived flag using the approved member -> project order.
    env = postgres_pair
    owner, _, admin, pid = _setup(env)
    todo = (
        ProjectTodoRepo(env["first"])
        .create(
            project_id=pid,
            creator_user_id=owner,
            title="old",
        )
        .row
    )
    assert todo is not None
    first = _ObservedPool(env["first"], hold_sql="SELECT archived FROM project_spaces")
    second = _ObservedPool(env["second"])

    def set_archived() -> str:
        from octop.infra.db.repos import project_plan_locks as locks

        with first.transaction() as conn:
            assert owner in locks.member_roles(first, conn, pid, [owner], write=True)
            assert locks.project_row(first, conn, pid, write=True) is not None
            conn.execute("UPDATE project_spaces SET archived=1 WHERE project_id=?", (pid,))
        return "fixture_archived"

    a, b = _race(
        env,
        first,
        second,
        set_archived,
        lambda: _tag(second, pid, admin, 1),
    )
    assert (a, b.outcome) == ("fixture_archived", "project_archived")
    catalog = ProjectTodoCatalogRepo(env["first"]).get_catalog(pid, user_id=admin)
    assert catalog is not None and catalog.revision == 1 and catalog.tags == []
    old = ProjectTodoRepo(env["first"]).update(
        project_id=pid,
        todo_id=todo.todo_id,
        actor_user_id=admin,
        expected_version=1,
        title="old ACL retained",
    )
    assert old.outcome == "updated" and old.row is not None and old.row.version == 2


def test_real_cross_project_compound_foreign_keys_and_date_constraints(
    postgres_pair: dict[str, Any],
) -> None:
    import psycopg

    env = postgres_pair
    owner, _, _, pid = _setup(env)
    second_pid = (
        ProjectRepo(env["first"])
        .create_with_owner(
            creator_user_id=owner,
            name="foreign",
        )
        .project_id
    )
    tag = _tag(env["first"], second_pid, owner, 1).item
    priority = (
        ProjectTodoCatalogRepo(env["first"]).get_catalog(second_pid, user_id=owner).priorities[0]
    )
    todo = (
        ProjectTodoRepo(env["first"])
        .create(project_id=pid, creator_user_id=owner, title="local")
        .row
    )
    assert todo is not None and tag is not None
    for sql, params in (
        (
            "UPDATE project_todos SET priority_id=? WHERE todo_id=?",
            (priority.option_id, todo.todo_id),
        ),
        (
            "INSERT INTO project_todo_tag_links(project_id,todo_id,tag_id) VALUES (?,?,?)",
            (pid, todo.todo_id, tag.option_id),
        ),
    ):
        with pytest.raises(psycopg.errors.ForeignKeyViolation), env["first"].transaction() as conn:
            conn.execute(sql, params)
    for sql in (
        "UPDATE project_todos SET start_date='1899-12-31'",
        "UPDATE project_todos SET due_date='9999-12-31T00:00:00'",
        "UPDATE project_todos SET start_date='2026-10-01',due_date='2026-09-30'",
    ):
        with pytest.raises(psycopg.errors.CheckViolation), env["first"].transaction() as conn:
            conn.execute(sql)
    loaded = ProjectTodoRepo(env["first"]).get(pid, todo.todo_id, user_id=owner)
    assert loaded is not None and loaded.priority_id is None and loaded.tag_ids == []
    assert loaded.start_date is None and loaded.due_date is None and loaded.version == 1


@pytest.mark.parametrize(
    "postgres_pair", [33, 34], indirect=True, ids=["033-upgrade", "034-roundtrip"]
)
def test_real_pg_dump_restore_keeps_plan_and_private_attachment_chain(
    postgres_pair: dict[str, Any],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    env = postgres_pair
    first, context = env["first"], env["context"]
    source = PathLayout(tmp_path / "source")
    project_id, todo_id, image_bytes = _seed_archive_rows(first, source)
    with first.connect() as conn:
        initial_version = int(conn.execute("SELECT version FROM _schema_version").fetchone()[0])
    if initial_version == 34:
        with first.transaction() as conn:
            seed_todo_catalog(conn, project_id, 19)
            priority_id = conn.execute(
                "SELECT priority_id FROM project_todo_priorities WHERE project_id=? ORDER BY position LIMIT 1",
                (project_id,),
            ).fetchone()[0]
            tag_id = new_ulid()
            conn.execute(
                "INSERT INTO project_todo_tags(tag_id,project_id,name,name_key,color,archived_at,created_at,updated_at) "
                "VALUES (?,?,'历史标签','历史标签','purple',37,19,37)",
                (tag_id, project_id),
            )
            conn.execute(
                "INSERT INTO project_todo_tag_links(project_id,todo_id,tag_id) VALUES (?,?,?)",
                (project_id, todo_id, tag_id),
            )
            conn.execute(
                "UPDATE project_todo_priorities SET archived_at=37 WHERE priority_id=?",
                (priority_id,),
            )
            conn.execute(
                "UPDATE project_todos SET start_date='2000-02-29',due_date='2000-03-01',priority_id=? WHERE todo_id=?",
                (priority_id, todo_id),
            )
            conn.execute(
                "UPDATE project_todo_catalog_state SET revision=8 WHERE project_id=?", (project_id,)
            )

    def snapshot(conn: Any, tables: list[str]) -> dict[str, list[tuple[Any, ...]]]:
        return {
            table: [
                tuple(row[key] for key in row)
                for row in conn.execute(f"SELECT * FROM {table} ORDER BY 1")
            ]
            for table in tables
        }

    child_tables = [
        "project_todo_comments",
        "project_todo_comment_images",
        "project_todo_comment_image_usage",
    ]
    catalog_tables = [
        "project_todo_catalog_state",
        "project_todo_priorities",
        "project_todo_tags",
        "project_todo_tag_links",
    ]
    with first.connect() as conn:
        old_todos = [
            tuple(row[key] for key in row)
            for row in conn.execute(
                "SELECT id,todo_id,project_id,creator_user_id,assignee_user_id,title,description,status,version,created_at,updated_at,deleted_at,description_format FROM project_todos ORDER BY id"
            )
        ]
        children = snapshot(conn, child_tables)
        catalog = snapshot(conn, catalog_tables) if initial_version == 34 else None
        plan_fields = (
            [
                tuple(row[key] for key in row)
                for row in conn.execute(
                    "SELECT todo_id,start_date,due_date,priority_id FROM project_todos ORDER BY id"
                )
            ]
            if initial_version == 34
            else [(row[1], None, None, None) for row in old_todos]
        )
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
    archive = tmp_path / "pg-plan.tar.gz"
    create_system_backup(
        paths=source,
        agent_rows=[],
        pool=first,
        db_config=config,
        dest=archive,
    )
    with first.transaction() as conn:
        conn.execute("UPDATE project_todos SET title='modified after backup',version=version+1")
    restored = PathLayout(tmp_path / "restored")
    restored.root.mkdir()
    result = restore_system_backup(
        archive,
        paths=restored,
        pool=first,
        db_config=config,
        restore_config=False,
    )
    assert (
        result["schema_version"] == _max_discovered_version("postgresql")
        and result["project_todo_comment_image_files"] == 1
    )
    with first.connect() as conn:
        assert _identify(conn, context, env["database"]) == env["report"]["pool_0_pid"]
        assert [
            tuple(row[key] for key in row)
            for row in conn.execute(
                "SELECT id,todo_id,project_id,creator_user_id,assignee_user_id,title,description,status,version,created_at,updated_at,deleted_at,description_format FROM project_todos ORDER BY id"
            )
        ] == old_todos
        assert snapshot(conn, child_tables) == children
        assert [
            tuple(row[key] for key in row)
            for row in conn.execute(
                "SELECT todo_id,start_date,due_date,priority_id FROM project_todos ORDER BY id"
            )
        ] == plan_fields
        if initial_version == 34:
            assert snapshot(conn, catalog_tables) == catalog
        else:
            assert (
                conn.execute(
                    "SELECT COUNT(*) FROM project_todo_priorities WHERE project_id=?", (project_id,)
                ).fetchone()[0]
                == 4
            )
            assert (
                conn.execute(
                    "SELECT revision FROM project_todo_catalog_state WHERE project_id=?",
                    (project_id,),
                ).fetchone()[0]
                == 1
            )
        object_key = conn.execute("SELECT object_key FROM project_todo_comment_images").fetchone()[
            0
        ]
    assert (restored.project_todo_comment_images / object_key).read_bytes() == image_bytes
