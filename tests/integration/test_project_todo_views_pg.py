"""Real two-connection view gates, usable only with root's private PG lease.

No application DSN is consumed. Each node uses the existing UUID database
fixture, records backend PIDs and genuine lock waits, and requires zero
connections and a dropped database on teardown. No PG is started here.
"""

from __future__ import annotations

import json
import threading
from contextlib import contextmanager
from types import SimpleNamespace

import pytest

from octop.infra.db.repos.project_todo_catalog import ProjectTodoCatalogRepo
from octop.infra.db.repos.project_todo_views import ProjectTodoViewRepo, ViewTransactionConflict
from octop.infra.db.repos.projects import ProjectRepo
from octop.infra.errors import OctopError
from octop.infra.projects.plan_definition import default_definition
from octop.infra.projects.todo_views import ProjectTodoViewService
from tests.integration.test_project_todo_fields_pg import _race, _setup
from tests.integration.test_project_todo_fields_pg import postgres_pair as postgres_pair
from tests.unit.db.test_project_todo_views import _definition, _FaultPool


class _ObservedConnection:
    def __init__(self, conn, observer):
        self._conn, self._observer = conn, observer

    def _start(self):
        observer = self._observer
        self._conn.execute("SET LOCAL lock_timeout='4s'")
        self._conn.execute("SET LOCAL statement_timeout='8s'")
        observer.pid = int(self._conn.execute("SELECT pg_backend_pid()").fetchone()[0])
        observer.isolation = self._conn.execute("SHOW transaction_isolation").fetchone()[0]
        observer.statements.extend(
            [
                "SET LOCAL lock_timeout",
                "SET LOCAL statement_timeout",
                "SELECT pg_backend_pid()",
                "SHOW transaction_isolation",
            ]
        )
        observer.started.set()

    def execute(self, sql, params=None):
        observer = self._observer
        if not observer.started.is_set() and sql.startswith("SET TRANSACTION ISOLATION LEVEL"):
            # Execute production RR setup before ANY observer SQL, including PID.
            observer.statements.append(sql)
            result = self._conn.execute(sql, params)
            self._start()
            return result
        if not observer.started.is_set():
            self._start()
        observer.statements.append(sql)
        result = self._conn.execute(sql, params)
        if observer.hold_sql and observer.hold_sql in sql:
            observer.hold_sql = ""
            observer.locked.set()
            assert observer.release.wait(5), "owned SQL checkpoint timed out"
        return result

    def __getattr__(self, name):
        return getattr(self._conn, name)


class _ObservedPool:
    dialect = "postgresql"

    def __init__(self, pool, *, hold_sql=""):
        self._pool, self.hold_sql = pool, hold_sql
        self.locked, self.release, self.started = (
            threading.Event(),
            threading.Event(),
            threading.Event(),
        )
        self.pid, self.isolation, self.statements = 0, "", []

    @contextmanager
    def transaction(self):
        with self._pool.transaction() as conn:
            yield _ObservedConnection(conn, self)

    @contextmanager
    def connect(self):
        with self._pool.connect() as conn:
            yield conn

    def close(self):
        self._pool.close()


def _snapshot(env, pid, owner):
    value = ProjectTodoViewRepo(env["first"]).list_views(pid, user_id=owner)
    assert value is not None
    return value


def _raw(env, pid):
    value = {}
    with env["second"].connect() as conn:
        for table, key in (
            ("project_todo_views", "view_id"),
            ("project_todo_view_state", "project_id"),
            ("project_events", "id"),
        ):
            value[table] = [
                dict(row)
                for row in conn.execute(
                    f"SELECT * FROM {table} WHERE project_id=? ORDER BY {key}", (pid,)
                ).fetchall()
            ]
    return value


def _count_view_events(env, pid):
    with env["first"].connect() as conn:
        return conn.execute(
            "SELECT COUNT(*) FROM project_events WHERE project_id=? AND event_type=?",
            (pid, "project.todo_view_updated"),
        ).fetchone()[0]


def _create(db, pid, actor, *, revision=1, catalog=1, name="new", definition=None):
    return ProjectTodoViewRepo(db).create_view(
        pid,
        actor_user_id=actor,
        expected_revision=revision,
        expected_catalog_revision=catalog,
        name=name,
        view_type="table",
        definition=_definition() if definition is None else definition,
    )


@pytest.mark.parametrize("same_view", [True, False])
def test_real_same_view_one_winner_and_different_views_both_commit(postgres_pair, same_view):
    env = postgres_pair
    owner, _, admin, pid = _setup(env)
    table, board = _snapshot(env, pid, owner).items
    first = _ObservedPool(env["first"], hold_sql="FROM project_todo_view_state")
    second = _ObservedPool(env["second"])
    a, b = _race(
        env,
        first,
        second,
        lambda: ProjectTodoViewRepo(first).update_view(
            pid, table.view_id, actor_user_id=owner, expected_version=1, name="first"
        ),
        lambda: ProjectTodoViewRepo(second).update_view(
            pid,
            table.view_id if same_view else board.view_id,
            actor_user_id=admin,
            expected_version=1,
            name="second",
        ),
    )
    assert (a.outcome, b.outcome) == (
        "updated",
        "view_version_conflict" if same_view else "updated",
    )
    snapshot = _snapshot(env, pid, owner)
    assert snapshot.revision == (2 if same_view else 3)
    assert [(item.name, item.version) for item in snapshot.items] == (
        [("first", 2), ("看板", 1)] if same_view else [("first", 2), ("second", 2)]
    )
    assert first.isolation == second.isolation == "read committed"
    assert _count_view_events(env, pid) == (1 if same_view else 2)


@pytest.mark.parametrize("winner", ["order", "default"])
def test_real_order_default_share_collection_lock_and_revision(postgres_pair, winner):
    env = postgres_pair
    owner, _, admin, pid = _setup(env)
    table, board = _snapshot(env, pid, owner).items
    first = _ObservedPool(env["first"], hold_sql="FROM project_todo_view_state")
    second = _ObservedPool(env["second"])

    def operation(db, actor, kind):
        repo = ProjectTodoViewRepo(db)
        if kind == "order":
            return repo.order_views(
                pid,
                actor_user_id=actor,
                expected_revision=1,
                view_ids=[board.view_id, table.view_id],
            )
        return repo.set_default_view(
            pid, actor_user_id=actor, expected_revision=1, view_id=board.view_id
        )

    a, b = _race(
        env,
        first,
        second,
        lambda: operation(first, owner, winner),
        lambda: operation(second, admin, "default" if winner == "order" else "order"),
    )
    assert (a.outcome, b.outcome) == (
        "ordered" if winner == "order" else "default_changed",
        "view_revision_conflict",
    )
    snapshot = _snapshot(env, pid, owner)
    assert snapshot.revision == 2
    assert [item.version for item in snapshot.items] == ([2, 2] if winner == "order" else [1, 1])
    assert snapshot.default_view_id == (table.view_id if winner == "order" else board.view_id)
    assert _count_view_events(env, pid) == (2 if winner == "order" else 1)


@pytest.mark.parametrize("winner", ["catalog", "definition"])
def test_real_catalog_archive_and_incoming_definition_have_one_snapshot(postgres_pair, winner):
    env = postgres_pair
    owner, _, admin, pid = _setup(env)
    snapshot = _snapshot(env, pid, owner)
    table = snapshot.items[0]
    priority = str(snapshot.priorities[0]["priority_id"])
    first = _ObservedPool(env["first"], hold_sql="FROM project_todo_catalog_state")
    second = _ObservedPool(env["second"])

    def archive(db, actor):
        return ProjectTodoCatalogRepo(db).set_archived(
            project_id=pid,
            actor_user_id=actor,
            expected_revision=1,
            kind="priority",
            option_id=priority,
            archived=True,
        )

    def save(db, actor):
        return ProjectTodoViewRepo(db).update_view(
            pid,
            table.view_id,
            actor_user_id=actor,
            expected_version=1,
            definition=_definition(priorities=(priority,)),
            expected_catalog_revision=1,
        )

    a, b = _race(
        env,
        first,
        second,
        lambda: archive(first, owner) if winner == "catalog" else save(first, owner),
        lambda: save(second, admin) if winner == "catalog" else archive(second, admin),
    )
    assert (a.outcome, b.outcome) == (
        ("archived", "catalog_revision_conflict")
        if winner == "catalog"
        else ("updated", "archived")
    )
    snapshot = _snapshot(env, pid, owner)
    assert snapshot.catalog_revision == 2
    assert snapshot.revision == (1 if winner == "catalog" else 2)
    assert snapshot.items[0].version == (1 if winner == "catalog" else 2)
    assert (
        next(row for row in snapshot.priorities if row["priority_id"] == priority)["archived_at"]
        is not None
    )
    assert json.loads(snapshot.items[0].definition_json)["filters"] == (
        [] if winner == "catalog" else [{"field": "priority", "op": "in", "values": [priority]}]
    )


def test_real_role_downgrade_commits_before_waiting_view_write_rechecks_acl(postgres_pair):
    env = postgres_pair
    owner, _, admin, pid = _setup(env)
    table = _snapshot(env, pid, owner).items[0]
    first = _ObservedPool(env["first"], hold_sql="UPDATE project_members SET role")
    second = _ObservedPool(env["second"])
    a, b = _race(
        env,
        first,
        second,
        lambda: ProjectRepo(first).set_member_role(
            project_id=pid, user_id=admin, role="member", actor_user_id=owner
        ),
        lambda: ProjectTodoViewRepo(second).update_view(
            pid, table.view_id, actor_user_id=admin, expected_version=1, name="denied"
        ),
    )
    assert (a.outcome, b.outcome) == ("changed", "forbidden")
    snapshot = _snapshot(env, pid, owner)
    assert (
        snapshot.revision == 1 and snapshot.items[0] == table and _count_view_events(env, pid) == 0
    )
    assert ProjectTodoViewRepo(env["second"]).get_view(pid, table.view_id, user_id=admin) == table


@pytest.mark.parametrize("reference", [False, True])
def test_real_member_removal_rechecks_actor_or_complete_incoming_member_set(
    postgres_pair, reference
):
    env = postgres_pair
    owner, member, admin, pid = _setup(env)
    table = _snapshot(env, pid, owner).items[0]
    first = _ObservedPool(env["first"], hold_sql="DELETE FROM project_members")
    second = _ObservedPool(env["second"])
    target = member if reference else admin

    def save():
        kwargs = {
            "actor_user_id": owner if reference else admin,
            "expected_version": 1,
            "name": "denied",
        }
        if reference:
            kwargs.update(definition=_definition(assignees=(member,)), expected_catalog_revision=1)
        return ProjectTodoViewRepo(second).update_view(pid, table.view_id, **kwargs)

    a, b = _race(
        env,
        first,
        second,
        lambda: ProjectRepo(first).remove_member(
            project_id=pid, user_id=target, actor_user_id=owner
        ),
        save,
    )
    assert (a.outcome, b.outcome) == ("removed", "invalid_assignee" if reference else "not_member")
    snapshot = _snapshot(env, pid, owner)
    assert (
        snapshot.revision == 1 and snapshot.items[0] == table and _count_view_events(env, pid) == 0
    )
    assert ProjectTodoViewRepo(env["second"]).list_views(pid, user_id=target) is None


@pytest.mark.parametrize("winner", ["read", "remove"])
def test_real_rr_read_revoke_interleaving_preserves_snapshot_or_safe_conflict(
    postgres_pair, winner
):
    env = postgres_pair
    owner, _, admin, pid = _setup(env)
    first = _ObservedPool(
        env["first"],
        hold_sql="FROM project_todo_view_state"
        if winner == "read"
        else "DELETE FROM project_members",
    )
    second = _ObservedPool(env["second"])

    def read(db):
        try:
            return ProjectTodoViewRepo(db).list_views(pid, user_id=admin)
        except ViewTransactionConflict:
            return "transaction_conflict"

    def remove(db):
        return ProjectRepo(db).remove_member(project_id=pid, user_id=admin, actor_user_id=owner)

    a, b = _race(
        env,
        first,
        second,
        lambda: read(first) if winner == "read" else remove(first),
        lambda: remove(second) if winner == "read" else read(second),
    )
    reader = first if winner == "read" else second
    assert reader.statements[0] == "SET TRANSACTION ISOLATION LEVEL REPEATABLE READ"
    assert reader.isolation == "repeatable read"
    if winner == "read":
        assert a.revision == 1 and len(a.items) == 2 and b.outcome == "removed"
    else:
        assert a.outcome == "removed" and b == "transaction_conflict"
    assert ProjectTodoViewRepo(env["second"]).list_views(pid, user_id=admin) is None
    assert _count_view_events(env, pid) == 0
    env["report"]["read_isolation"] = {"level": reader.isolation, "first_sql": reader.statements[0]}


def test_real_rr_reader_and_view_writer_do_not_mix_revision_default_and_items(postgres_pair):
    env = postgres_pair
    owner, _, admin, pid = _setup(env)
    table = _snapshot(env, pid, owner).items[0]
    first = _ObservedPool(env["first"], hold_sql="FROM project_todo_view_state")
    second = _ObservedPool(env["second"])
    a, b = _race(
        env,
        first,
        second,
        lambda: ProjectTodoViewRepo(first).list_views(pid, user_id=owner),
        lambda: ProjectTodoViewRepo(second).update_view(
            pid, table.view_id, actor_user_id=admin, expected_version=1, name="new"
        ),
    )
    assert a.revision == 1 and a.default_view_id == table.view_id and a.items[0] == table
    assert b.outcome == "updated" and b.revision == 2 and b.item.version == 2
    current = _snapshot(env, pid, owner)
    assert (
        current.revision == 2 and current.items[0].name == "new" and current.items[0].version == 2
    )
    assert first.statements[0] == "SET TRANSACTION ISOLATION LEVEL REPEATABLE READ"
    assert first.isolation == "repeatable read" and second.isolation == "read committed"
    env["report"]["read_isolation"] = {"level": first.isolation, "first_sql": first.statements[0]}


@pytest.mark.parametrize("limit", ["name", "active"])
def test_real_restore_and_create_recheck_normalized_name_and_active_quota(postgres_pair, limit):
    env = postgres_pair
    owner, _, admin, pid = _setup(env)
    repo = ProjectTodoViewRepo(env["first"])
    table = _snapshot(env, pid, owner).items[0]
    archived = repo.archive_view(pid, table.view_id, actor_user_id=owner, expected_version=1)
    assert archived.outcome == "archived" and archived.revision == 2
    if limit == "active":
        with env["first"].transaction() as conn:
            for index in range(28):
                conn.execute(
                    "INSERT INTO project_todo_views(view_id,project_id,name,name_key,view_type,definition_json,version,position,created_at,updated_at) VALUES(?,?,?,?,?,?,1,?,1,1)",
                    (
                        f"fixture{index}",
                        pid,
                        f"Fixture{index}",
                        f"fixture{index}",
                        "table",
                        json.dumps(default_definition("table")),
                        index + 2,
                    ),
                )
    first = _ObservedPool(env["first"], hold_sql="FROM project_todo_view_state")
    second = _ObservedPool(env["second"])
    a, b = _race(
        env,
        first,
        second,
        lambda: _create(
            first, pid, owner, revision=2, name=table.name if limit == "name" else "last"
        ),
        lambda: ProjectTodoViewRepo(second).restore_view(
            pid, table.view_id, actor_user_id=admin, expected_version=2
        ),
    )
    assert (a.outcome, b.outcome) == (
        "created",
        "name_conflict" if limit == "name" else "active_limit",
    )
    snapshot = _snapshot(env, pid, owner)
    assert snapshot.revision == 3 and snapshot.default_view_id == archived.default_view_id
    stored = next(row for row in snapshot.items if row.view_id == table.view_id)
    assert stored == archived.item and stored.version == 2 and stored.archived_at is not None
    assert len([row for row in snapshot.items if row.archived_at is None]) == (
        2 if limit == "name" else 30
    )


def test_real_pg_membership_integer_max_and_oversize_never_bind_invalid_id(postgres_pair):
    env = postgres_pair
    owner, _, _, pid = _setup(env)
    maximum = 2**31 - 1
    with env["first"].transaction() as conn:
        conn.execute(
            "INSERT INTO users(id,username,password_hash,role,created_at,project_plan_display_sort_key) VALUES(?,?,?,'user',1,?)",
            (maximum, "max-id", "synthetic", "max-id"),
        )
    ProjectRepo(env["first"]).add_member(pid, maximum)
    repo = ProjectTodoViewRepo(env["first"])
    service = ProjectTodoViewService(SimpleNamespace(project_todo_view_repo=repo))
    definition = default_definition("table")
    definition["filters"] = [{"field": "assignee", "op": "in", "values": [maximum]}]
    created = service.create_view(
        pid,
        actor_user_id=owner,
        expected_revision=1,
        expected_catalog_revision=1,
        name="max member",
        view_type="table",
        definition=definition,
    )
    assert created["item"]["definition"]["filters"][0]["values"] == [maximum]
    before = _raw(env, pid)
    original = repo._db

    def forbidden():
        raise AssertionError("invalid identifier reached PG transaction/bind")

    repo._db = SimpleNamespace(dialect="postgresql", transaction=forbidden)
    try:
        for value in (maximum + 1, 2**100):
            definition["filters"][0]["values"] = [value]
            with pytest.raises(OctopError) as invalid:
                service.create_view(
                    pid,
                    actor_user_id=owner,
                    expected_revision=2,
                    expected_catalog_revision=1,
                    name="invalid",
                    view_type="table",
                    definition=definition,
                )
            assert invalid.value.status == 422 and invalid.value.details == {
                "reason": "invalid_assignee"
            }
    finally:
        repo._db = original
    assert _raw(env, pid) == before


class _PgFaultPool(_FaultPool):
    dialect = "postgresql"


@pytest.mark.parametrize(
    "operation,stage,error",
    [
        ("create", "item", RuntimeError),
        ("create", "state", RuntimeError),
        ("create", "event", RuntimeError),
        ("update", "event", RuntimeError),
        ("update", "event", KeyboardInterrupt),
        ("order", "event", RuntimeError),
        ("default", "state", RuntimeError),
        ("default", "event", RuntimeError),
        ("archive", "event", RuntimeError),
        ("archive", "event", KeyboardInterrupt),
        ("restore", "event", RuntimeError),
    ],
)
def test_real_pg_actual_item_state_event_fault_rolls_back_on_both_connections(
    postgres_pair, operation, stage, error
):
    env = postgres_pair
    owner, _, _, pid = _setup(env)
    original = ProjectTodoViewRepo(env["first"])
    table, board = _snapshot(env, pid, owner).items
    version, revision = 1, 1
    if operation == "restore":
        archived = original.archive_view(
            pid, table.view_id, actor_user_id=owner, expected_version=1
        )
        assert archived.outcome == "archived"
        version, revision = archived.item.version, archived.revision
    before = _raw(env, pid)
    fragment = {
        "item": "INSERT INTO project_todo_views",
        "state": "UPDATE project_todo_view_state",
        "event": "INSERT INTO project_events",
    }[stage]
    repo = ProjectTodoViewRepo(_PgFaultPool(env["first"], fragment, error))
    with pytest.raises(error, match="actual SQL installed"):
        if operation == "create":
            _create(repo._db, pid, owner, revision=revision, name="fault")
        elif operation == "update":
            repo.update_view(
                pid, table.view_id, actor_user_id=owner, expected_version=version, name="fault"
            )
        elif operation == "order":
            repo.order_views(
                pid,
                actor_user_id=owner,
                expected_revision=revision,
                view_ids=[board.view_id, table.view_id],
            )
        elif operation == "default":
            repo.set_default_view(
                pid, actor_user_id=owner, expected_revision=revision, view_id=board.view_id
            )
        else:
            getattr(repo, operation + "_view")(
                pid, table.view_id, actor_user_id=owner, expected_version=version
            )
    with env["first"].connect() as conn:
        assert conn.info.transaction_status == 0
    assert _raw(env, pid) == before
    assert _snapshot(env, pid, owner).revision == revision


def _safe_outcome(operation):
    try:
        return operation()
    except OctopError as error:
        assert error.details is not None
        assert set(error.details) == {"reason"}
        return {"status": error.status, "details": error.details}


def _view_event(view_id, action, version, revision, fields):
    return {
        "view_id": view_id,
        "action": action,
        "version": version,
        "collection_revision": revision,
        "fields": fields,
    }


def _assert_full_afterimages(
    before, after, *, changed=(), added=(), revision, default, events=(), other_events=()
):
    old_rows = {row["view_id"]: row for row in before["project_todo_views"]}
    new_rows = {row["view_id"]: row for row in after["project_todo_views"]}
    changed_rows = {row.view_id: row for row in changed}
    added_rows = {row.view_id: row for row in added}
    field_columns = {
        "name": {"name", "name_key"},
        "type": {"view_type"},
        "definition": {"definition_json"},
        "order": {"position"},
        "archived": {"archived_at", "position"},
        "default_view_id": set(),
    }
    assert set(new_rows) == set(old_rows) | set(added_rows)
    assert not set(added_rows) & set(old_rows)
    for view_id, old in old_rows.items():
        expected = dict(old)
        if view_id in changed_rows:
            new_values = vars(changed_rows[view_id])
            row_events = [event for event in events if event["view_id"] == view_id]
            allowed = {"version", "updated_at"} if row_events else set()
            for event in row_events:
                for field in event["fields"]:
                    allowed |= field_columns[field]
                if event["action"] == "archived":
                    allowed.discard("position")
            for column, value in new_values.items():
                if column not in allowed:
                    assert value == old[column]
            if row_events:
                assert new_values["version"] == row_events[-1]["version"]
            expected.update(new_values)
        assert new_rows[view_id] == expected
    for view_id, row in added_rows.items():
        new = dict(new_rows[view_id])
        new_id = new.pop("id")
        assert type(new_id) is int and new_id > 0
        assert new_id not in {old["id"] for old in old_rows.values()}
        assert new == vars(row)
    old_events = before["project_events"]
    new_events = after["project_events"]
    assert new_events[: len(old_events)] == old_events
    inserted = new_events[len(old_events) :]
    actual_view_events = [
        row for row in inserted if row["event_type"] == "project.todo_view_updated"
    ]
    actual_other_events = [
        row for row in inserted if row["event_type"] != "project.todo_view_updated"
    ]
    assert [json.loads(row["payload_json"]) for row in actual_view_events] == list(events)
    assert [row["event_type"] for row in actual_other_events] == list(other_events)
    assert len(inserted) == len(events) + len(other_events)
    old_state = before["project_todo_view_state"]
    assert len(old_state) == len(after["project_todo_view_state"]) == 1
    expected_state = dict(old_state[0])
    expected_state.update(revision=revision, default_view_id=default)
    if events:
        expected_state["updated_at"] = actual_view_events[-1]["created_at"]
    assert after["project_todo_view_state"] == [expected_state]
    assert new_rows[default]["archived_at"] is None


def _assert_write_lock_order(observer):
    state = next(
        index
        for index, sql in enumerate(observer.statements)
        if "FROM project_todo_view_state" in sql and "FOR UPDATE" in sql
    )
    item = next(
        index
        for index, sql in enumerate(observer.statements)
        if "FROM project_todo_views" in sql and "FOR UPDATE" in sql
    )
    assert state < item
    assert "ORDER BY view_id" in observer.statements[item]
    assert observer.isolation == "read committed"


def test_real_archive_default_then_competing_last_active_archive_preserves_loser(postgres_pair):
    env = postgres_pair
    owner, _, admin, pid = _setup(env)
    table, board = _snapshot(env, pid, owner).items
    before = _raw(env, pid)
    first = _ObservedPool(env["first"], hold_sql="FROM project_todo_view_state")
    second = _ObservedPool(env["second"])
    a, b = _race(
        env,
        first,
        second,
        lambda: ProjectTodoViewRepo(first).archive_view(
            pid, table.view_id, actor_user_id=owner, expected_version=1
        ),
        lambda: ProjectTodoViewRepo(second).archive_view(
            pid, board.view_id, actor_user_id=admin, expected_version=1
        ),
    )
    assert (a.outcome, b.outcome) == ("archived", "last_active_view")
    assert a.revision == 2 and a.item.version == 2 and a.default_view_id == board.view_id
    assert a.item.archived_at is not None
    _assert_full_afterimages(
        before,
        _raw(env, pid),
        changed=(a.item,),
        revision=2,
        default=board.view_id,
        events=(_view_event(table.view_id, "archived", 2, 2, ["archived", "default_view_id"]),),
    )
    _assert_write_lock_order(first)
    _assert_write_lock_order(second)


def test_real_account_deletion_blocks_incoming_reference_save_and_preserves_saved_condition(
    postgres_pair,
):
    from octop.infra.db.repos.users import UserRepo

    env = postgres_pair
    owner, member, admin, pid = _setup(env)
    assert member < admin
    default = _snapshot(env, pid, owner).default_view_id
    saved = _create(
        env["first"],
        pid,
        owner,
        name="saved deleted account",
        definition=_definition(assignees=(member,)),
    )
    assert saved.outcome == "created" and saved.revision == 2 and saved.item.version == 1
    view_id = saved.item.view_id
    definition = json.loads(saved.item.definition_json)
    before = _raw(env, pid)
    first = _ObservedPool(env["first"], hold_sql="DELETE FROM users")
    second = _MemberParameterPool(env["second"])
    incoming = default_definition("table")
    incoming["filters"] = [{"field": "assignee", "op": "in", "values": [member, None]}]
    waiting_service = ProjectTodoViewService(
        SimpleNamespace(project_todo_view_repo=ProjectTodoViewRepo(second))
    )

    def while_waiting():
        # Native DELETE and ON DELETE CASCADE have already completed in transaction A.
        # Transaction B is actually blocked acquiring the deleted membership row.
        assert second.member_requests == [(pid, member)]
        assert second.member_queries_completed == []

    a, b = _race(
        env,
        first,
        second,
        lambda: UserRepo(first).delete(member),
        lambda: _safe_outcome(
            lambda: waiting_service.update_view(
                pid,
                view_id,
                actor_user_id=admin,
                expected_version=1,
                definition=incoming,
                expected_catalog_revision=1,
            )
        ),
        on_wait=while_waiting,
    )
    assert a is None and b == {"status": 422, "details": {"reason": "invalid_assignee"}}
    assert (
        second.member_requests == second.member_queries_completed == [(pid, member), (pid, admin)]
    )
    after_rejection = _raw(env, pid)
    _assert_full_afterimages(before, after_rejection, revision=2, default=default)
    assert UserRepo(env["second"]).get(member) is None
    assert ProjectRepo(env["second"]).get_membership(pid, member) is None
    assert ProjectRepo(env["second"]).get_membership(pid, admin).role == "admin"
    with env["second"].connect() as conn:
        assert conn.execute("SELECT id FROM users WHERE id=?", (member,)).fetchone() is None

    # The deleted account is an invalid historical reference, not permission to
    # erase the saved filter. Read/rename/lifecycle must retain it until repair.
    service = ProjectTodoViewService(
        SimpleNamespace(project_todo_view_repo=ProjectTodoViewRepo(env["second"]))
    )
    read = service.get_view(pid, view_id, user_id=admin)
    assert read["view_id"] == view_id and read["version"] == 1 and read["definition"] == definition
    renamed = service.update_view(
        pid,
        view_id,
        actor_user_id=admin,
        expected_version=1,
        name="retained deleted account condition",
    )
    archived = service.archive_view(pid, view_id, actor_user_id=admin, expected_version=2)
    restored = service.restore_view(pid, view_id, actor_user_id=admin, expected_version=3)
    for version, revision, value in ((2, 3, renamed), (3, 4, archived), (4, 5, restored)):
        assert value["revision"] == revision and value["default_view_id"] == default
        assert value["item"]["view_id"] == view_id and value["item"]["version"] == version
        assert value["item"]["definition"] == definition
    assert archived["item"]["archived_at"] is not None
    assert restored["item"]["archived_at"] is None
    repaired_definition = default_definition("table")
    repaired = service.update_view(
        pid,
        view_id,
        actor_user_id=admin,
        expected_version=4,
        definition=repaired_definition,
        expected_catalog_revision=1,
    )
    assert repaired["revision"] == 6 and repaired["default_view_id"] == default
    assert repaired["item"]["view_id"] == view_id and repaired["item"]["version"] == 5
    assert repaired["item"]["definition"] == repaired_definition
    final = ProjectTodoViewRepo(env["second"]).get_view(pid, view_id, user_id=admin)
    _assert_full_afterimages(
        before,
        _raw(env, pid),
        changed=(final,),
        revision=6,
        default=default,
        events=(
            _view_event(view_id, "updated", 2, 3, ["name"]),
            _view_event(view_id, "archived", 3, 4, ["archived"]),
            _view_event(view_id, "restored", 4, 5, ["archived"]),
            _view_event(view_id, "updated", 5, 6, ["definition"]),
        ),
    )
    env["report"]["account_deletion_reference_save"] = {
        "deleted_user_id": member,
        "actor_id": admin,
        "first_pid": first.pid,
        "second_pid": second.pid,
        "actual_sql_member_requests": [list(params) for params in second.member_requests],
        "actual_sql_completed": [list(params) for params in second.member_queries_completed],
        "user_absent_after_commit": True,
        "membership_absent_after_commit": True,
        "outcome": b,
        "rejected_view_state_default_events_unchanged": after_rejection == before,
        "historical_reference_retained_versions": [1, 2, 3, 4],
        "repaired_version": repaired["item"]["version"],
        "final_revision": repaired["revision"],
    }


@pytest.mark.parametrize("winner", ["archive", "default"])
def test_real_archive_default_interleaving_keeps_default_active_and_independent_item_version(
    postgres_pair, winner
):
    env = postgres_pair
    owner, _, admin, pid = _setup(env)
    table, board = _snapshot(env, pid, owner).items
    before = _raw(env, pid)
    first = _ObservedPool(env["first"], hold_sql="FROM project_todo_view_state")
    second = _ObservedPool(env["second"])

    def operation(db, actor, kind):
        repo = ProjectTodoViewRepo(db)
        if kind == "archive":
            return repo.archive_view(pid, table.view_id, actor_user_id=actor, expected_version=1)
        return repo.set_default_view(
            pid, actor_user_id=actor, expected_revision=1, view_id=board.view_id
        )

    a, b = _race(
        env,
        first,
        second,
        lambda: operation(first, owner, winner),
        lambda: operation(second, admin, "default" if winner == "archive" else "archive"),
    )
    if winner == "archive":
        assert (a.outcome, b.outcome) == ("archived", "view_revision_conflict")
        changed, revision = (a.item,), 2
        events = (_view_event(table.view_id, "archived", 2, 2, ["archived", "default_view_id"]),)
    else:
        assert (a.outcome, b.outcome) == ("default_changed", "archived")
        assert a.revision == 2 and b.revision == 3 and b.item.version == 2
        changed, revision = (b.item,), 3
        events = (
            _view_event(board.view_id, "default_changed", 1, 2, ["default_view_id"]),
            _view_event(table.view_id, "archived", 2, 3, ["archived"]),
        )
    _assert_full_afterimages(
        before,
        _raw(env, pid),
        changed=changed,
        revision=revision,
        default=board.view_id,
        events=events,
    )
    _assert_write_lock_order(first)
    _assert_write_lock_order(second)


def test_real_total_99_to_100_competing_create_then_current_revision_capacity_recheck(
    postgres_pair,
):
    env = postgres_pair
    owner, _, admin, pid = _setup(env)
    table = _snapshot(env, pid, owner).items[0]
    with env["first"].transaction() as conn:
        for index in range(97):
            conn.execute(
                "INSERT INTO project_todo_views(view_id,project_id,name,name_key,view_type,definition_json,version,position,archived_at,created_at,updated_at) VALUES(?,?,?,?,?,?,1,?,1,1,1)",
                (
                    f"total{index:03}",
                    pid,
                    f"Total{index}",
                    f"total{index}",
                    "table",
                    json.dumps(default_definition("table")),
                    index + 2,
                ),
            )
    before = _raw(env, pid)
    assert len(before["project_todo_views"]) == 99
    first = _ObservedPool(env["first"], hold_sql="FROM project_todo_view_state")
    second = _ObservedPool(env["second"])
    a, b = _race(
        env,
        first,
        second,
        lambda: _create(first, pid, owner, name="last slot"),
        lambda: _create(second, pid, admin, name="no second slot"),
    )
    assert (a.outcome, b.outcome) == ("created", "view_revision_conflict")
    assert a.item.name == "last slot" and a.item.view_type == "table"
    assert a.item.position == 2 and a.item.version == 1
    assert json.loads(a.item.definition_json) == default_definition("table")
    committed = _raw(env, pid)
    _assert_full_afterimages(
        before,
        committed,
        added=(a.item,),
        revision=2,
        default=table.view_id,
        events=(_view_event(a.item.view_id, "created", 1, 2, ["name", "type", "definition"]),),
    )
    fresh = ProjectTodoViewRepo(env["second"]).list_views(pid, user_id=admin)
    assert fresh.revision == 2 and len(fresh.items) == 100
    service = ProjectTodoViewService(
        SimpleNamespace(project_todo_view_repo=ProjectTodoViewRepo(env["second"]))
    )
    retry = _safe_outcome(
        lambda: service.create_view(
            pid,
            actor_user_id=admin,
            expected_revision=fresh.revision,
            expected_catalog_revision=fresh.catalog_revision,
            name="valid current R but full",
            view_type="table",
            definition=default_definition("table"),
        )
    )
    assert retry == {"status": 409, "details": {"reason": "total_limit"}}
    assert _raw(env, pid) == committed
    env["report"]["capacity_recheck"] = {
        "actual_read_revision": fresh.revision,
        "submitted_revision": fresh.revision,
        "actual_total": len(fresh.items),
        "outcome": retry,
        "afterimages_unchanged": True,
    }


def test_real_project_archive_commit_rechecked_by_waiting_view_service_patch(postgres_pair):
    from octop.infra.db.repos import project_plan_locks

    env = postgres_pair
    owner, _, admin, pid = _setup(env)
    table = _snapshot(env, pid, owner).items[0]
    before = _raw(env, pid)
    project_before = ProjectRepo(env["second"]).get_project(pid)
    first = _ObservedPool(env["first"], hold_sql="UPDATE project_spaces SET archived")
    second = _ObservedPool(env["second"])

    def archive_project():
        # Existing project APIs have no archive mutation; this private fixture
        # uses the same real membership/project lock order, then actual SQL.
        with first.transaction() as conn:
            assert (
                project_plan_locks.member_roles(first, conn, pid, [owner], write=True)[owner]
                == "owner"
            )
            assert project_plan_locks.project_row(first, conn, pid, write=True) is not None
            result = conn.execute("UPDATE project_spaces SET archived=1 WHERE project_id=?", (pid,))
            assert result.rowcount == 1
        return "project_archived"

    service = ProjectTodoViewService(
        SimpleNamespace(project_todo_view_repo=ProjectTodoViewRepo(second))
    )
    a, b = _race(
        env,
        first,
        second,
        archive_project,
        lambda: _safe_outcome(
            lambda: service.update_view(
                pid, table.view_id, actor_user_id=admin, expected_version=1, name="must remain old"
            )
        ),
    )
    assert a == "project_archived" and b == {
        "status": 409,
        "details": {"reason": "project_archived"},
    }
    assert _raw(env, pid) == before
    project_after = ProjectRepo(env["second"]).get_project(pid)
    expected = vars(project_before) | {"archived": True}
    assert vars(project_after) == expected
    assert _snapshot(env, pid, owner).items[0] == table


def test_real_definition_only_service_patch_checks_stale_version_then_locked_current_type(
    postgres_pair, monkeypatch
):
    env = postgres_pair
    owner, _, admin, pid = _setup(env)
    table = _snapshot(env, pid, owner).items[0]
    before = _raw(env, pid)
    first = _ObservedPool(env["first"], hold_sql="FROM project_todo_view_state")
    second = _ObservedPool(env["second"])
    first_service = ProjectTodoViewService(
        SimpleNamespace(project_todo_view_repo=ProjectTodoViewRepo(first))
    )
    second_service = ProjectTodoViewService(
        SimpleNamespace(project_todo_view_repo=ProjectTodoViewRepo(second))
    )
    candidate = default_definition("table")
    candidate["fields"] = ["title", "due_date"]
    public_get_calls = []

    def forbidden(*args, **kwargs):
        public_get_calls.append(True)
        raise AssertionError("definition-only mutation used a separate public GET")

    with monkeypatch.context() as patch:
        patch.setattr(ProjectTodoViewRepo, "get_view", forbidden)
        patch.setattr(ProjectTodoViewRepo, "list_views", forbidden)
        a, b = _race(
            env,
            first,
            second,
            lambda: first_service.update_view(
                pid,
                table.view_id,
                actor_user_id=owner,
                expected_version=1,
                view_type="calendar",
                definition=default_definition("calendar"),
                expected_catalog_revision=1,
            ),
            lambda: _safe_outcome(
                lambda: second_service.update_view(
                    pid,
                    table.view_id,
                    actor_user_id=admin,
                    expected_version=1,
                    definition=candidate,
                    expected_catalog_revision=1,
                )
            ),
        )
    assert a["revision"] == 2 and a["item"]["version"] == 2 and a["item"]["type"] == "calendar"
    assert b == {"status": 409, "details": {"reason": "view_version_conflict"}}
    fresh = _snapshot(env, pid, owner)
    current = next(row for row in fresh.items if row.view_id == table.view_id)
    assert current.version == 2 and current.view_type == "calendar"
    committed = _raw(env, pid)
    _assert_full_afterimages(
        before,
        committed,
        changed=(current,),
        revision=2,
        default=table.view_id,
        events=(_view_event(table.view_id, "updated", 2, 2, ["type", "definition"]),),
    )
    with monkeypatch.context() as patch:
        patch.setattr(ProjectTodoViewRepo, "get_view", forbidden)
        patch.setattr(ProjectTodoViewRepo, "list_views", forbidden)
        retry = _safe_outcome(
            lambda: second_service.update_view(
                pid,
                table.view_id,
                actor_user_id=admin,
                expected_version=current.version,
                definition=candidate,
                expected_catalog_revision=fresh.catalog_revision,
            )
        )
    assert retry == {"status": 422, "details": {"reason": "invalid_definition"}}
    assert public_get_calls == [] and _raw(env, pid) == committed
    env["report"]["definition_only_recheck"] = {
        "current_type": current.view_type,
        "current_version": current.version,
        "stale_outcome": b,
        "fresh_outcome": retry,
        "public_pre_get_calls": len(public_get_calls),
    }


class _AfterCommitPool(_ObservedPool):
    def __init__(self, pool):
        super().__init__(pool)
        self.committed = threading.Event()

    @contextmanager
    def transaction(self):
        with self._pool.transaction() as conn:
            yield _ObservedConnection(conn, self)
        # Reached only after the real native transaction and pool contexts exit.
        # A second connection must observe its committed image before release.
        self.committed.set()
        assert self.release.wait(5), "owned post-commit return checkpoint timed out"


@pytest.mark.parametrize("layer", ["repo", "service"])
def test_real_original_mutation_snapshot_return_survives_later_committed_version(
    postgres_pair, layer
):
    from octop.infra.projects.todo_views import view_payload

    env = postgres_pair
    owner, _, admin, pid = _setup(env)
    table = _snapshot(env, pid, owner).items[0]
    before = _raw(env, pid)
    first = _AfterCommitPool(env["first"])
    second = _ObservedPool(env["second"])
    results, errors = {}, []

    def original_mutation():
        try:
            repo = ProjectTodoViewRepo(first)
            if layer == "repo":
                results["original"] = repo.update_view(
                    pid,
                    table.view_id,
                    actor_user_id=owner,
                    expected_version=1,
                    name="first committed",
                )
            else:
                service = ProjectTodoViewService(SimpleNamespace(project_todo_view_repo=repo))
                results["original"] = service.update_view(
                    pid,
                    table.view_id,
                    actor_user_id=owner,
                    expected_version=1,
                    name="first committed",
                )
        except BaseException as error:
            errors.append(error)

    worker = threading.Thread(target=original_mutation, daemon=True)
    first_visible = None
    second_visible = None
    later = None
    try:
        worker.start()
        assert first.committed.wait(5), "first real transaction did not commit"
        first_visible = ProjectTodoViewRepo(env["second"]).list_views(pid, user_id=admin)
        first_row = next(row for row in first_visible.items if row.view_id == table.view_id)
        assert first_visible.revision == 2 and first_visible.default_view_id == table.view_id
        assert first_row.name == "first committed" and first_row.version == 2
        assert "original" not in results
        later = ProjectTodoViewRepo(second).update_view(
            pid,
            table.view_id,
            actor_user_id=admin,
            expected_version=first_row.version,
            name="second committed",
        )
        assert later.outcome == "updated" and later.revision == 3 and later.item.version == 3
        second_visible = ProjectTodoViewRepo(env["first"]).list_views(pid, user_id=owner)
        assert second_visible.revision == 3
        assert (
            next(row for row in second_visible.items if row.view_id == table.view_id) == later.item
        )
        assert first.pid != second.pid and "original" not in results
        assert first.isolation == second.isolation == "read committed"
    finally:
        first.release.set()
        worker.join(10)
        env["report"]["after_commit_returns"] = [
            {
                "layer": layer,
                "first_pid": first.pid,
                "second_pid": second.pid,
                "first_visible_revision": None if first_visible is None else first_visible.revision,
                "first_visible_version": None
                if first_visible is None
                else next(
                    row.version for row in first_visible.items if row.view_id == table.view_id
                ),
                "second_visible_revision": None
                if second_visible is None
                else second_visible.revision,
                "second_visible_version": None if later is None else later.item.version,
                "second_commit_before_release": second_visible is not None
                and second_visible.revision == 3,
                "alive": [worker.is_alive()],
                "errors": [repr(error) for error in errors],
            }
        ]
    assert not worker.is_alive() and errors == []
    original = results["original"]
    if layer == "repo":
        assert original.outcome == "updated" and original.revision == 2
        assert original.default_view_id == table.view_id and original.item == first_row
    else:
        assert original == {
            "revision": 2,
            "default_view_id": table.view_id,
            "item": view_payload(first_row),
        }
    _assert_full_afterimages(
        before,
        _raw(env, pid),
        changed=(later.item,),
        revision=3,
        default=table.view_id,
        events=(
            _view_event(table.view_id, "updated", 2, 2, ["name"]),
            _view_event(table.view_id, "updated", 3, 3, ["name"]),
        ),
    )


class _MemberParameterConnection(_ObservedConnection):
    def execute(self, sql, params=None):
        if "SELECT role FROM project_members" in sql:
            self._observer.member_requests.append(tuple(params))
        result = super().execute(sql, params)
        if "SELECT role FROM project_members" in sql:
            self._observer.member_queries_completed.append(tuple(params))
        return result


class _MemberParameterPool(_ObservedPool):
    def __init__(self, pool):
        super().__init__(pool)
        self.member_requests, self.member_queries_completed = [], []

    @contextmanager
    def transaction(self):
        with self._pool.transaction() as conn:
            yield _MemberParameterConnection(conn, self)


def test_real_incoming_member_below_actor_is_locked_once_in_complete_sorted_set(
    postgres_pair, monkeypatch
):
    from octop.infra.db.repos import project_plan_locks

    env = postgres_pair
    owner, member, admin, pid = _setup(env)
    assert member < admin
    table = _snapshot(env, pid, owner).items[0]
    before = _raw(env, pid)
    first = _ObservedPool(env["first"], hold_sql="DELETE FROM project_members")
    second = _MemberParameterPool(env["second"])
    calls = []
    original_lock = project_plan_locks.member_roles

    def observed(db, conn, project_id, user_ids, *, write):
        if db is second:
            calls.append((tuple(user_ids), write))
        return original_lock(db, conn, project_id, user_ids, write=write)

    value = default_definition("table")
    value["filters"] = [{"field": "assignee", "op": "in", "values": [member, None]}]
    service = ProjectTodoViewService(
        SimpleNamespace(project_todo_view_repo=ProjectTodoViewRepo(second))
    )
    with monkeypatch.context() as patch:
        patch.setattr(project_plan_locks, "member_roles", observed)

        def while_waiting():
            assert second.member_requests == [(pid, member)]
            assert second.member_queries_completed == []

        a, b = _race(
            env,
            first,
            second,
            lambda: ProjectRepo(first).remove_member(
                project_id=pid, user_id=member, actor_user_id=owner
            ),
            lambda: _safe_outcome(
                lambda: service.update_view(
                    pid,
                    table.view_id,
                    actor_user_id=admin,
                    expected_version=1,
                    definition=value,
                    expected_catalog_revision=1,
                )
            ),
            on_wait=while_waiting,
        )
    assert a.outcome == "removed" and b == {
        "status": 422,
        "details": {"reason": "invalid_assignee"},
    }
    assert calls == [((member, admin), True)]
    assert (
        second.member_requests == second.member_queries_completed == [(pid, member), (pid, admin)]
    )
    _assert_full_afterimages(
        before,
        _raw(env, pid),
        revision=1,
        default=table.view_id,
        other_events=("project.member_removed",),
    )
    assert ProjectRepo(env["second"]).get_membership(pid, member) is None
    assert ProjectRepo(env["second"]).get_membership(pid, admin).role == "admin"
    env["report"]["member_lock_set"] = {
        "actor_id": admin,
        "incoming_ids": [member],
        "complete_calls": [{"user_ids": list(ids), "write": write} for ids, write in calls],
        "actual_sql_member_requests": [list(params) for params in second.member_requests],
        "actual_sql_completed": [list(params) for params in second.member_queries_completed],
        "outcome": b,
    }


@pytest.mark.parametrize(
    "static_kind,winner,target",
    [
        ("order", "static", "changed"),
        ("order", "static", "unchanged"),
        ("order", "item", "changed"),
        ("default", "static", "changed"),
        ("default", "item", "changed"),
    ],
)
def test_real_order_default_vs_item_patch_both_directions_preserve_independent_v_r(
    postgres_pair, static_kind, winner, target
):
    env = postgres_pair
    owner, _, admin, pid = _setup(env)
    if target == "unchanged":
        assert _create(env["first"], pid, owner, name="unmoved").outcome == "created"
    initial = _snapshot(env, pid, owner)
    table, board = initial.items[:2]
    item = initial.items[-1] if target == "unchanged" else table
    before = _raw(env, pid)
    first = _ObservedPool(env["first"], hold_sql="FROM project_todo_view_state")
    second = _ObservedPool(env["second"])

    def static(db, actor):
        repo = ProjectTodoViewRepo(db)
        if static_kind == "order":
            return repo.order_views(
                pid,
                actor_user_id=actor,
                expected_revision=initial.revision,
                view_ids=[
                    board.view_id,
                    table.view_id,
                    *[row.view_id for row in initial.items[2:]],
                ],
            )
        return repo.set_default_view(
            pid, actor_user_id=actor, expected_revision=initial.revision, view_id=board.view_id
        )

    def dynamic(db, actor):
        return ProjectTodoViewRepo(db).update_view(
            pid, item.view_id, actor_user_id=actor, expected_version=item.version, name="item patch"
        )

    a, b = _race(
        env,
        first,
        second,
        lambda: static(first, owner) if winner == "static" else dynamic(first, owner),
        lambda: dynamic(second, admin) if winner == "static" else static(second, admin),
    )
    r = initial.revision
    if winner == "item":
        assert (a.outcome, b.outcome) == ("updated", "view_revision_conflict")
        changed, revision, default = (a.item,), r + 1, table.view_id
        events = (_view_event(item.view_id, "updated", 2, r + 1, ["name"]),)
    elif static_kind == "default":
        assert (a.outcome, b.outcome) == ("default_changed", "updated")
        assert a.revision == r + 1 and b.revision == r + 2 and b.item.version == 2
        changed, revision, default = (b.item,), r + 2, board.view_id
        events = (
            _view_event(board.view_id, "default_changed", 1, r + 1, ["default_view_id"]),
            _view_event(item.view_id, "updated", 2, r + 2, ["name"]),
        )
    else:
        assert a.outcome == "ordered" and a.revision == r + 1
        assert b.outcome == ("updated" if target == "unchanged" else "view_version_conflict")
        events = (
            _view_event(board.view_id, "ordered", 2, r + 1, ["order"]),
            _view_event(table.view_id, "ordered", 2, r + 1, ["order"]),
        )
        changed, revision, default = a.items, r + 1, table.view_id
        if target == "unchanged":
            assert b.revision == r + 2 and b.item.version == 2
            changed = [row for row in a.items if row.view_id != item.view_id] + [b.item]
            revision = r + 2
            events += (_view_event(item.view_id, "updated", 2, r + 2, ["name"]),)
    _assert_full_afterimages(
        before, _raw(env, pid), changed=changed, revision=revision, default=default, events=events
    )
    _assert_write_lock_order(first)
    _assert_write_lock_order(second)
