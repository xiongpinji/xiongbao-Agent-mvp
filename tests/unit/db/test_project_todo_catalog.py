"""SQLite C1 repo atomicity, quotas, snapshots and real two-pool contention."""

from __future__ import annotations

import importlib
import importlib.util
import json
import threading
from contextlib import contextmanager
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from octop.infra.db.migrate import run_migrations
from octop.infra.db.pool import SqlitePool
from octop.infra.db.repos.project_todos import ProjectTodoRepo
from octop.infra.db.repos.projects import ProjectRepo
from octop.infra.db.repos.users import UserRepo


def _base(tmp_path: Path) -> tuple[Any, Any, Any, int, str]:
    pool = SqlitePool(tmp_path / "c1.db")
    run_migrations(pool)
    owner = UserRepo(pool).create(username="owner", password_hash="h", role="user")
    projects = ProjectRepo(pool)
    pid = projects.create_with_owner(creator_user_id=owner, name="C1").project_id
    return pool, projects, ProjectTodoRepo(pool), owner, pid


def _catalog_repo(pool: SqlitePool) -> Any:
    name = "octop.infra.db.repos.project_todo_catalog"
    assert importlib.util.find_spec(name) is not None, "C1 catalog repository is missing"
    return importlib.import_module(name).ProjectTodoCatalogRepo(pool)


def _create(
    repo: Any, pid: str, owner: int, revision: int, kind: str = "tag", name: str = "x"
) -> Any:
    return repo.create_option(
        project_id=pid,
        actor_user_id=owner,
        expected_revision=revision,
        kind=kind,
        name=name,
        color="blue",
    )


def _events(pool: SqlitePool, pid: str) -> list[Any]:
    with pool.connect() as conn:
        return conn.execute(
            "SELECT * FROM project_events WHERE project_id = ? ORDER BY id", (pid,)
        ).fetchall()


@pytest.mark.parametrize("kind,active,total", [("priority", 32, 128), ("tag", 100, 500)])
@pytest.mark.parametrize("limit_kind", ["active", "total"])
def test_catalog_limits_leave_revision_events_and_rows_unchanged(
    tmp_path, kind, active, total, limit_kind
) -> None:
    pool, _, _, owner, pid = _base(tmp_path)
    repo = _catalog_repo(pool)
    table = "project_todo_priorities" if kind == "priority" else "project_todo_tags"
    id_column = "priority_id" if kind == "priority" else "tag_id"
    count = active if limit_kind == "active" else total
    with pool.transaction() as conn:
        conn.execute(f"DELETE FROM {table} WHERE project_id = ?", (pid,))
        for index in range(count):
            columns = (
                f"{id_column},project_id,name,name_key,color,archived_at,created_at,updated_at"
            )
            values = [
                f"option{index:04}",
                pid,
                f"N{index}",
                f"n{index}",
                "blue",
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
    before = len(_events(pool, pid))
    result = _create(repo, pid, owner, 1, kind=kind, name="overflow")
    assert result.outcome == f"{limit_kind}_limit"
    snapshot = repo.get_catalog(pid, user_id=owner)
    assert snapshot.revision == 1
    assert len(snapshot.priorities if kind == "priority" else snapshot.tags) == count
    assert len(_events(pool, pid)) == before


def test_catalog_event_failure_rolls_back_option_and_revision(tmp_path, monkeypatch) -> None:
    pool, _, _, owner, pid = _base(tmp_path)
    repo = _catalog_repo(pool)
    module = importlib.import_module("octop.infra.db.repos.project_todo_catalog")

    def fail(*args: Any) -> None:
        raise RuntimeError("injected event failure")

    monkeypatch.setattr(module, "_append_catalog_event", fail)
    with pytest.raises(RuntimeError, match="injected"):
        _create(repo, pid, owner, 1)
    snapshot = repo.get_catalog(pid, user_id=owner)
    assert snapshot.revision == 1 and snapshot.tags == []
    assert len(_events(pool, pid)) == 1


def test_todo_create_update_return_before_commit_snapshot_without_post_get(
    tmp_path, monkeypatch
) -> None:
    pool, _, todos, owner, pid = _base(tmp_path)
    catalog = _catalog_repo(pool)
    tag = _create(catalog, pid, owner, 1).item.option_id

    def fail_get(*args: Any, **kwargs: Any) -> None:
        raise AssertionError("post-commit get is forbidden")

    monkeypatch.setattr(todos, "get", fail_get)
    created = todos.create(
        project_id=pid,
        creator_user_id=owner,
        title="snapshot",
        tag_ids=[tag],
        expected_catalog_revision=2,
    )
    assert created.outcome == "created" and created.row.tag_ids == [tag]
    assert created.row.catalog_revision == 2 and created.row.version == 1
    updated = todos.update(
        project_id=pid,
        todo_id=created.row.todo_id,
        actor_user_id=owner,
        expected_version=1,
        tag_ids=[],
        expected_catalog_revision=2,
    )
    assert updated.outcome == "updated" and updated.row.tag_ids == []
    assert updated.row.catalog_revision == 2 and updated.row.version == 2


def test_todo_link_or_event_failure_restores_fields_version_links_events(
    tmp_path, monkeypatch
) -> None:
    pool, _, todos, owner, pid = _base(tmp_path)
    catalog = _catalog_repo(pool)
    tag = _create(catalog, pid, owner, 1).item.option_id
    created = todos.create(project_id=pid, creator_user_id=owner, title="before").row
    assert created is not None
    before = len(_events(pool, pid))

    def fail(*args: Any) -> None:
        raise RuntimeError("injected event failure")

    monkeypatch.setattr("octop.infra.db.repos.project_todos._append_todo_event", fail)
    with pytest.raises(RuntimeError):
        todos.update(
            project_id=pid,
            todo_id=created.todo_id,
            actor_user_id=owner,
            expected_version=1,
            title="after",
            tag_ids=[tag],
            expected_catalog_revision=2,
        )
    loaded = todos.get(pid, created.todo_id, user_id=owner)
    assert loaded.version == 1 and loaded.title == "before" and loaded.tag_ids == []
    assert len(_events(pool, pid)) == before


def test_tag_limit_counts_distinct_and_sorts_ids(tmp_path) -> None:
    pool, _, todos, owner, pid = _base(tmp_path)
    catalog = _catalog_repo(pool)
    identifiers = []
    for index in range(21):
        item = _create(catalog, pid, owner, index + 1, name=f"tag{index}")
        assert item.outcome == "created"
        identifiers.append(item.item.option_id)
    response = todos.create(
        project_id=pid,
        creator_user_id=owner,
        title="20",
        tag_ids=list(reversed(identifiers[:20])) + identifiers[:2],
        expected_catalog_revision=22,
    )
    assert response.outcome == "created" and response.row.tag_ids == sorted(identifiers[:20])
    invalid = todos.update(
        project_id=pid,
        todo_id=response.row.todo_id,
        actor_user_id=owner,
        expected_version=1,
        tag_ids=identifiers,
        expected_catalog_revision=22,
    )
    assert invalid.outcome == "invalid_tags"
    assert todos.get(pid, response.row.todo_id).version == 1


def test_member_removal_preserves_dates_priority_tag_refs_and_private_cleanup(tmp_path) -> None:
    pool, projects, todos, owner, pid = _base(tmp_path)
    member = UserRepo(pool).create(username="member", password_hash="h", role="user")
    projects.add_member(pid, member)
    catalog = _catalog_repo(pool)
    tag = _create(catalog, pid, owner, 1).item.option_id
    priority = catalog.get_catalog(pid, user_id=owner).priorities[0].option_id
    created = todos.create(
        project_id=pid,
        creator_user_id=owner,
        title="keep",
        assignee_user_id=member,
        start_date="2000-02-29",
        due_date="9999-12-31",
        priority_id=priority,
        tag_ids=[tag],
        expected_catalog_revision=2,
    ).row
    assert created is not None
    result = projects.remove_member(project_id=pid, user_id=member, actor_user_id=owner)
    assert result.outcome == "removed"
    loaded = todos.get(pid, created.todo_id, user_id=owner)
    assert loaded.assignee_user_id is None and loaded.version == 2
    assert (loaded.start_date, loaded.due_date, loaded.priority_id, loaded.tag_ids) == (
        "2000-02-29",
        "9999-12-31",
        priority,
        [tag],
    )
    assert todos.get(pid, created.todo_id, user_id=member) is None
    assert todos.list_todos(pid, user_id=member) is None


def test_two_sqlite_pools_catalog_revision_has_one_winner(tmp_path) -> None:
    pool, _, _, owner, pid = _base(tmp_path)
    second = SqlitePool(pool.path)
    first_repo, second_repo = _catalog_repo(pool), _catalog_repo(second)
    barrier = threading.Barrier(2)
    results: list[str] = []

    def create(repo: Any, name: str) -> None:
        barrier.wait(timeout=5)
        results.append(_create(repo, pid, owner, 1, name=name).outcome)

    threads = [
        threading.Thread(target=create, args=(repo, name))
        for repo, name in ((first_repo, "first"), (second_repo, "second"))
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=10)
        assert not thread.is_alive()
    assert sorted(results) == ["created", "stale"]
    assert len(first_repo.get_catalog(pid, user_id=owner).tags) == 1
    assert first_repo.get_catalog(pid, user_id=owner).revision == 2
    second.close()


def test_catalog_does_not_event_names_or_color_values(tmp_path) -> None:
    pool, _, _, owner, pid = _base(tmp_path)
    catalog = _catalog_repo(pool)
    _create(catalog, pid, owner, 1, name="RAW SECRET")
    payload = json.loads(_events(pool, pid)[-1]["payload_json"])
    assert payload["catalog_revision"] == 2 and payload["catalog_kind"] == "tag"
    assert set(payload) == {"catalog_revision", "catalog_kind", "option_id", "action", "fields"}
    assert "RAW SECRET" not in str(payload) and "blue" not in str(payload)


@pytest.mark.parametrize("raw", ["\noption", "option\t", "option\r\n"])
def test_catalog_name_rejects_control_characters_before_trimming(raw: str) -> None:
    from octop.infra.projects.todo_catalog import validate_catalog_name

    with pytest.raises(ValueError, match="control"):
        validate_catalog_name(raw)


@pytest.mark.parametrize(
    "instant,timezone,day",
    [
        ("2026-09-28T16:01:00+00:00", "UTC", "2026-09-28"),
        ("2026-09-28T16:01:00+00:00", "Asia/Shanghai", "2026-09-29"),
        ("2026-03-08T04:59:00+00:00", "America/New_York", "2026-03-07"),
        ("2026-03-08T05:01:00+00:00", "America/New_York", "2026-03-08"),
        ("2026-03-09T03:59:00+00:00", "America/New_York", "2026-03-08"),
        ("2026-03-09T04:01:00+00:00", "America/New_York", "2026-03-09"),
        ("2026-11-01T03:59:00+00:00", "America/New_York", "2026-10-31"),
        ("2026-11-01T04:01:00+00:00", "America/New_York", "2026-11-01"),
        ("2026-11-02T04:59:00+00:00", "America/New_York", "2026-11-01"),
        ("2026-11-02T05:01:00+00:00", "America/New_York", "2026-11-02"),
    ],
)
def test_server_day_uses_configured_zone_including_dst(
    tmp_path, monkeypatch, instant, timezone, day
) -> None:
    from octop.infra.db.repos import project_plan_locks
    from octop.infra.errors import OctopError
    from octop.infra.projects.todo_catalog import ProjectTodoCatalogService
    from octop.infra.projects.todos import ProjectTodoService

    fixed = datetime.fromisoformat(instant)

    class Clock:
        @staticmethod
        def now(zone: Any) -> datetime:
            return fixed.astimezone(zone)

    monkeypatch.setattr(project_plan_locks, "datetime", Clock)
    assert project_plan_locks.server_today(timezone) == day
    pool, projects, todos, owner, pid = _base(tmp_path)
    services = SimpleNamespace(
        config=SimpleNamespace(default_timezone=timezone),
        project_repo=projects,
        project_todo_repo=todos,
        project_todo_catalog_repo=_catalog_repo(pool),
    )
    catalog = ProjectTodoCatalogService(services).get_catalog(pid, user_id=owner)
    assert (catalog["server_timezone"], catalog["server_today"]) == (timezone, day)
    service = ProjectTodoService(services)
    response = service.create_todo(pid, actor_user_id=owner, title="local day", due_date=day)
    assert response.due_date == day
    before = len(_events(pool, pid))
    previous = (datetime.fromisoformat(day) - timedelta(days=1)).date().isoformat()
    with pytest.raises(OctopError) as error:
        service.create_todo(pid, actor_user_id=owner, title="previous day", due_date=previous)
    assert error.value.status == 422 and error.value.details["reason"] == "invalid_dates"
    assert len(_events(pool, pid)) == before


def test_date_validation_reads_today_after_waiting_for_real_sqlite_writer(
    tmp_path, monkeypatch
) -> None:
    from octop.infra.db.repos import project_plan_locks

    pool, _, _, owner, pid = _base(tmp_path)
    second = SqlitePool(pool.path)
    attempted, finished = threading.Event(), threading.Event()
    current = ["2026-09-28"]
    monkeypatch.setattr(project_plan_locks, "server_today", lambda zone: current[0])

    class ObservedPool:
        dialect = "sqlite"

        def connect(self) -> Any:
            return second.connect()

        @contextmanager
        def transaction(self) -> Any:
            attempted.set()
            with second.transaction() as conn:
                yield conn

    result: list[Any] = []

    def create() -> None:
        result.append(
            ProjectTodoRepo(ObservedPool()).create(
                project_id=pid,
                creator_user_id=owner,
                title="late day",
                due_date="2026-09-28",
                timezone="UTC",
            )
        )
        finished.set()

    with pool.transaction():
        thread = threading.Thread(target=create)
        thread.start()
        assert attempted.wait(timeout=5)
        assert not finished.is_set()
        current[0] = "2026-09-29"
    thread.join(timeout=10)
    assert not thread.is_alive()
    assert len(result) == 1 and result[0].outcome == "invalid_dates"
    assert len(_events(pool, pid)) == 1
    second.close()


def test_patch_midnight_after_sqlite_lock_wait_preserves_version_tags_and_events(
    tmp_path, monkeypatch
) -> None:
    from octop.infra.db.repos import project_plan_locks

    pool, _, todos, owner, pid = _base(tmp_path)
    catalog = _catalog_repo(pool)
    original_tag = _create(catalog, pid, owner, 1, name="original").item.option_id
    next_tag = _create(catalog, pid, owner, 2, name="next").item.option_id
    current = [datetime.fromisoformat("2026-09-28T23:59:00+00:00")]

    class Clock:
        @staticmethod
        def now(zone: Any) -> datetime:
            return current[0].astimezone(zone)

    monkeypatch.setattr(project_plan_locks, "datetime", Clock)
    original = todos.create(
        project_id=pid,
        creator_user_id=owner,
        title="before midnight",
        due_date="2026-09-28",
        tag_ids=[original_tag],
        expected_catalog_revision=3,
        timezone="UTC",
    ).row
    assert original is not None
    before_events = len(_events(pool, pid))
    current[0] = datetime.fromisoformat("2026-09-29T23:59:00+00:00")
    second = SqlitePool(pool.path)
    attempted, finished = threading.Event(), threading.Event()

    class ObservedPool:
        dialect = "sqlite"

        @contextmanager
        def transaction(self) -> Any:
            attempted.set()
            with second.transaction() as conn:
                yield conn

    results: list[Any] = []

    def update() -> None:
        results.append(
            ProjectTodoRepo(ObservedPool()).update(
                project_id=pid,
                todo_id=original.todo_id,
                actor_user_id=owner,
                expected_version=1,
                title="after midnight",
                due_date="2026-09-29",
                tag_ids=[next_tag],
                expected_catalog_revision=3,
                timezone="UTC",
            )
        )
        finished.set()

    with pool.transaction():
        thread = threading.Thread(target=update)
        thread.start()
        assert attempted.wait(timeout=5)
        assert not finished.is_set()
        current[0] = datetime.fromisoformat("2026-09-30T00:01:00+00:00")
    thread.join(timeout=10)
    assert not thread.is_alive()
    assert len(results) == 1 and results[0].outcome == "invalid_dates"
    loaded = todos.get(pid, original.todo_id, user_id=owner)
    assert (
        loaded.version,
        loaded.title,
        loaded.due_date,
        loaded.tag_ids,
        loaded.catalog_revision,
    ) == (1, "before midnight", "2026-09-28", [original_tag], 3)
    assert len(_events(pool, pid)) == before_events
    second.close()


def test_bulk_preserves_field_refs_and_request_order_with_current_catalog_revision(
    tmp_path,
) -> None:
    pool, _, todos, owner, pid = _base(tmp_path)
    catalog = _catalog_repo(pool)
    tag = _create(catalog, pid, owner, 1).item.option_id
    priority = catalog.get_catalog(pid, user_id=owner).priorities[0].option_id
    created = [
        todos.create(
            project_id=pid,
            creator_user_id=owner,
            title=f"todo {index}",
            start_date="2000-01-01",
            due_date="9999-12-31",
            priority_id=priority,
            tag_ids=[tag],
            expected_catalog_revision=2,
        ).row
        for index in range(2)
    ]
    renamed = catalog.update_option(
        project_id=pid,
        actor_user_id=owner,
        expected_revision=2,
        kind="tag",
        option_id=tag,
        name="renamed",
    )
    assert renamed.revision == 3
    requested = list(reversed(created))
    result = todos.bulk_update(
        project_id=pid,
        actor_user_id=owner,
        items=[(row.todo_id, 1) for row in requested],
        status="todo",
    )
    assert result.outcome == "updated"
    assert [row.todo_id for row in result.rows] == [row.todo_id for row in requested]
    for row in result.rows:
        assert (
            row.version,
            row.catalog_revision,
            row.start_date,
            row.due_date,
            row.priority_id,
            row.tag_ids,
        ) == (2, 3, "2000-01-01", "9999-12-31", priority, [tag])


def test_read_snapshot_holds_todo_tags_revision_while_second_pool_writes(tmp_path) -> None:
    pool, _, todos, owner, pid = _base(tmp_path)
    catalog = _catalog_repo(pool)
    tag = _create(catalog, pid, owner, 1).item.option_id
    created = todos.create(
        project_id=pid,
        creator_user_id=owner,
        title="before",
        tag_ids=[tag],
        expected_catalog_revision=2,
    ).row
    assert created is not None
    second = SqlitePool(pool.path)
    read_started, write_attempted, read_release = (
        threading.Event(),
        threading.Event(),
        threading.Event(),
    )

    class ObservedConnection:
        def __init__(self, conn: Any) -> None:
            self.conn = conn

        def execute(self, sql: str, params: Any = None) -> Any:
            cursor = self.conn.execute(sql, params)
            if sql.startswith("SELECT todo_id, project_id"):
                read_started.set()
                assert read_release.wait(timeout=5)
            return cursor

    class ObservedReadPool:
        dialect = "sqlite"

        @contextmanager
        def transaction(self) -> Any:
            with pool.transaction() as conn:
                yield ObservedConnection(conn)

    read_rows: list[Any] = []

    def read() -> None:
        read_rows.append(
            ProjectTodoRepo(ObservedReadPool()).get(pid, created.todo_id, user_id=owner)
        )

    def write() -> None:
        write_attempted.set()
        second_catalog = _catalog_repo(second)
        option = _create(second_catalog, pid, owner, 2, name="second").item.option_id
        updated = ProjectTodoRepo(second).update(
            project_id=pid,
            todo_id=created.todo_id,
            actor_user_id=owner,
            expected_version=1,
            title="after",
            tag_ids=[option],
            expected_catalog_revision=3,
        )
        assert updated.outcome == "updated"

    reader = threading.Thread(target=read)
    reader.start()
    assert read_started.wait(timeout=5)
    writer = threading.Thread(target=write)
    writer.start()
    assert write_attempted.wait(timeout=5)
    read_release.set()
    for thread in (reader, writer):
        thread.join(timeout=10)
        assert not thread.is_alive()
    assert len(read_rows) == 1
    assert (
        read_rows[0].version,
        read_rows[0].catalog_revision,
        read_rows[0].title,
        read_rows[0].tag_ids,
    ) == (1, 2, "before", [tag])
    final = todos.get(pid, created.todo_id, user_id=owner)
    assert final.version == 2 and final.catalog_revision == 3 and final.tag_ids != [tag]
    second.close()
