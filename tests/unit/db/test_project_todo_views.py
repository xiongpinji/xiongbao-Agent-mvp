"""Real SQLite view transactions, independent versions and bounded contention."""

from __future__ import annotations

import json
import threading
from contextlib import contextmanager

import pytest

from octop.infra.db.migrate import run_migrations
from octop.infra.db.pool import SqlitePool
from octop.infra.db.repos import project_plan_locks
from octop.infra.db.repos import project_todo_views as module
from octop.infra.db.repos.project_todo_catalog import ProjectTodoCatalogRepo
from octop.infra.db.repos.project_todo_views import ProjectTodoViewRepo
from octop.infra.db.repos.projects import ProjectRepo
from octop.infra.db.repos.users import UserRepo
from octop.infra.projects.plan_definition import default_definition


@pytest.fixture
def case(tmp_path):
    pool = SqlitePool(tmp_path / "views.db")
    run_migrations(pool)
    users, projects = UserRepo(pool), ProjectRepo(pool)
    owner = users.create(username="owner", password_hash="h", role="user")
    member = users.create(username="member", password_hash="h", role="user")
    admin = users.create(username="admin", password_hash="h", role="admin")
    outsider = users.create(username="outside", password_hash="h", role="admin")
    pid = projects.create_with_owner(creator_user_id=owner, name="views").project_id
    projects.add_member(pid, member, role="member")
    projects.add_member(pid, admin, role="admin")
    yield {
        "pool": pool,
        "repo": ProjectTodoViewRepo(pool),
        "projects": projects,
        "pid": pid,
        "owner": owner,
        "member": member,
        "admin": admin,
        "outsider": outsider,
    }
    pool.close()


def _definition(kind="table", *, assignees=(), priorities=(), tags=()):
    data = default_definition(kind)
    data["filters"] = []
    if assignees:
        data["filters"].append({"field": "assignee", "op": "in", "values": list(assignees)})
    if priorities:
        data["filters"].append({"field": "priority", "op": "in", "values": list(priorities)})
    if tags:
        data["filters"].append({"field": "tags", "op": "any", "values": list(tags)})
    return module.ViewDefinition(
        compatible_types=(kind,),
        definition_json=json.dumps(data, ensure_ascii=False),
        assignee_ids=tuple(assignees),
        priority_ids=tuple(priorities),
        tag_ids=tuple(tags),
    )


def _snapshot(case):
    return case["repo"].list_views(case["pid"], user_id=case["owner"])


def _raw(case):
    with case["pool"].connect() as conn:
        return {
            "views": [
                dict(row)
                for row in conn.execute(
                    "SELECT * FROM project_todo_views WHERE project_id=? ORDER BY view_id",
                    (case["pid"],),
                ).fetchall()
            ],
            "state": [
                dict(row)
                for row in conn.execute(
                    "SELECT * FROM project_todo_view_state WHERE project_id=?", (case["pid"],)
                ).fetchall()
            ],
            "events": [
                dict(row)
                for row in conn.execute(
                    "SELECT * FROM project_events WHERE project_id=? ORDER BY id", (case["pid"],)
                ).fetchall()
            ],
        }


def _create(
    case,
    name="private name",
    kind="table",
    *,
    revision=None,
    catalog_revision=None,
    definition=None,
):
    snapshot = _snapshot(case)
    return case["repo"].create_view(
        case["pid"],
        actor_user_id=case["owner"],
        expected_revision=snapshot.revision if revision is None else revision,
        expected_catalog_revision=snapshot.catalog_revision
        if catalog_revision is None
        else catalog_revision,
        name=name,
        view_type=kind,
        definition=_definition(kind) if definition is None else definition,
    )


def test_read_default_snapshot_acl_and_same_connection_seams(case, monkeypatch):
    repo, pid = case["repo"], case["pid"]
    snapshot = _snapshot(case)
    assert snapshot.revision == snapshot.catalog_revision == 1
    assert [item.name for item in snapshot.items] == ["表格", "看板"]
    assert [item.view_type for item in snapshot.items] == ["table", "board"]
    assert snapshot.default_view_id == snapshot.items[0].view_id
    assert all(item.version == 1 and item.archived_at is None for item in snapshot.items)
    for user in ("member", "admin"):
        assert repo.list_views(pid, user_id=case[user]).items == snapshot.items
    assert repo.list_views(pid, user_id=case["outsider"]) is None
    assert repo.get_view(pid, "missing", user_id=case["owner"]) is None
    assert repo.list_views(pid, user_id=2**100) is None

    with case["pool"].transaction() as conn:

        def no_new_transaction():
            raise AssertionError("internal seam opened another transaction")

        monkeypatch.setattr(case["pool"], "transaction", no_new_transaction)
        peeked = repo.peek_view_in_connection(conn, pid, snapshot.default_view_id)
        same = repo.read_plan_snapshot_in_connection(
            conn, pid, case["owner"], [case["member"]], snapshot.default_view_id
        )
        assert conn.in_transaction
        assert peeked == same.view == snapshot.items[0]
        assert same.catalog_revision == 1 and same.revision == 1
        assert same.member_roles == {case["owner"]: "owner", case["member"]: "member"}
        assert (
            repo.peek_view_in_connection(conn, "another-project", snapshot.default_view_id) is None
        )


@pytest.mark.parametrize("corruption", ["missing_state", "null_default", "archived_default"])
def test_reads_reject_partial_state_without_seeding_or_repair(case, corruption):
    view = _snapshot(case).items[0]
    with case["pool"].transaction() as conn:
        if corruption == "missing_state":
            conn.execute("DELETE FROM project_todo_view_state WHERE project_id=?", (case["pid"],))
        elif corruption == "null_default":
            conn.execute(
                "UPDATE project_todo_view_state SET default_view_id=NULL WHERE project_id=?",
                (case["pid"],),
            )
        else:
            conn.execute(
                "UPDATE project_todo_views SET archived_at=1 WHERE view_id=?", (view.view_id,)
            )
    before = _raw(case)
    with pytest.raises(RuntimeError, match="view"):
        _snapshot(case)
    assert _raw(case) == before


def test_independent_versions_default_and_safe_events(case):
    repo, pid, owner = case["repo"], case["pid"], case["owner"]
    table, board = _snapshot(case).items
    first = repo.update_view(
        pid, table.view_id, actor_user_id=owner, expected_version=1, name="秘密名A"
    )
    second = repo.update_view(
        pid, board.view_id, actor_user_id=owner, expected_version=1, name="秘密名B"
    )
    assert (first.outcome, first.revision, first.item.version) == ("updated", 2, 2)
    assert (second.outcome, second.revision, second.item.version) == ("updated", 3, 2)
    before = _raw(case)
    stale = repo.update_view(
        pid, table.view_id, actor_user_id=owner, expected_version=1, name="lost"
    )
    assert stale.outcome == "view_version_conflict" and _raw(case) == before
    changed = repo.set_default_view(
        pid, actor_user_id=owner, expected_revision=3, view_id=board.view_id
    )
    assert changed.outcome == "default_changed" and changed.revision == 4
    assert changed.default_view_id == board.view_id
    assert [item.version for item in _snapshot(case).items] == [2, 2]
    with case["pool"].connect() as conn:
        events = conn.execute(
            "SELECT payload_json FROM project_events WHERE project_id=? AND event_type=? ORDER BY id",
            (pid, module.EVENT_TODO_VIEW_UPDATED),
        ).fetchall()
    assert len(events) == 3
    for row in events:
        payload = json.loads(row["payload_json"])
        assert set(payload) == {"view_id", "action", "version", "collection_revision", "fields"}
        assert set(payload["fields"]) <= {
            "name",
            "type",
            "definition",
            "order",
            "archived",
            "default_view_id",
        }
        assert "秘密" not in str(payload) and "definition_json" not in str(payload)
    assert json.loads(events[-1]["payload_json"])["version"] == 2
    assert json.loads(events[-1]["payload_json"])["action"] == "default_changed"


def test_order_only_bumps_actual_positions_once_and_rejects_incomplete_arrays(case):
    repo, pid, owner = case["repo"], case["pid"], case["owner"]
    created = _create(case)
    active = _snapshot(case).items
    before = _raw(case)
    for ids in (
        [active[0].view_id],
        [item.view_id for item in active] + [active[0].view_id],
        ["foreign"],
    ):
        result = repo.order_views(pid, actor_user_id=owner, expected_revision=2, view_ids=ids)
        assert result.outcome == "invalid_order" and _raw(case) == before
    same = repo.order_views(
        pid, actor_user_id=owner, expected_revision=2, view_ids=[item.view_id for item in active]
    )
    assert same.outcome == "no_change" and _raw(case) == before
    ids = [active[1].view_id, active[0].view_id, created.item.view_id]
    ordered = repo.order_views(pid, actor_user_id=owner, expected_revision=2, view_ids=ids)
    assert ordered.outcome == "ordered" and ordered.revision == 3
    assert [item.view_id for item in ordered.items] == ids
    assert [item.position for item in ordered.items] == [0, 1, 2]
    assert [item.version for item in ordered.items] == [2, 2, 1]
    events = [
        row for row in _raw(case)["events"] if row["event_type"] == module.EVENT_TODO_VIEW_UPDATED
    ]
    assert len(events) == 3  # create plus the two actual position changes
    assert all(json.loads(row["payload_json"])["collection_revision"] == 3 for row in events[-2:])


def test_archive_default_last_active_restore_name_reuse_and_gapped_tail(case):
    repo, pid, owner = case["repo"], case["pid"], case["owner"]
    table, board = _snapshot(case).items
    archived = repo.archive_view(pid, table.view_id, actor_user_id=owner, expected_version=1)
    assert (archived.outcome, archived.revision, archived.item.version) == ("archived", 2, 2)
    assert archived.default_view_id == board.view_id
    assert repo.get_view(pid, table.view_id, user_id=owner).archived_at is not None
    before = _raw(case)
    last = repo.archive_view(pid, board.view_id, actor_user_id=owner, expected_version=1)
    assert last.outcome == "last_active_view" and _raw(case) == before
    reuse = _create(case, "表格")
    assert reuse.item.view_id != table.view_id
    conflict = repo.restore_view(pid, table.view_id, actor_user_id=owner, expected_version=2)
    assert conflict.outcome == "name_conflict"
    renamed = repo.update_view(
        pid, table.view_id, actor_user_id=owner, expected_version=2, name="另一表格"
    )
    assert renamed.item.archived_at is not None and renamed.item.version == 3
    with case["pool"].transaction() as conn:
        conn.execute(
            "UPDATE project_todo_views SET position=8 WHERE view_id=?", (reuse.item.view_id,)
        )
    restored = repo.restore_view(pid, table.view_id, actor_user_id=owner, expected_version=3)
    assert restored.outcome == "restored" and restored.item.position == 9
    assert restored.item.version == 4 and restored.default_view_id == board.view_id
    next_view = _create(case, "尾部")
    assert next_view.item.position == 10
    before = _raw(case)
    invalid = repo.restore_view(pid, table.view_id, actor_user_id=owner, expected_version=4)
    assert invalid.outcome == "invalid_lifecycle" and _raw(case) == before


def test_full_nfkc_casefold_name_conflicts_and_archived_edit_rules(case):
    repo, pid, owner = case["repo"], case["pid"], case["owner"]
    created = _create(case, "ＡＳＳ")
    before = _raw(case)
    assert _create(case, "ass").outcome == "name_conflict"
    assert _raw(case) == before
    archived = repo.archive_view(pid, created.item.view_id, actor_user_id=owner, expected_version=1)
    new = _create(case, "ass")
    assert new.item.view_id != created.item.view_id
    edited = repo.update_view(
        pid, archived.item.view_id, actor_user_id=owner, expected_version=2, name="Ass"
    )
    assert edited.outcome == "updated" and edited.item.archived_at is not None
    assert (
        repo.restore_view(pid, edited.item.view_id, actor_user_id=owner, expected_version=3).outcome
        == "name_conflict"
    )


@pytest.mark.parametrize("actor", ["member", "outsider"])
def test_member_and_outsider_cannot_write_but_missing_target_stays404(case, actor):
    repo, pid, user = case["repo"], case["pid"], case[actor]
    table = _snapshot(case).items[0]
    before = _raw(case)
    result = repo.update_view(
        pid, table.view_id, actor_user_id=user, expected_version=1, name="denied"
    )
    assert result.outcome == ("forbidden" if actor == "member" else "not_member")
    assert _raw(case) == before
    unknown = repo.update_view(
        pid, "unknown", actor_user_id=user, expected_version=1, name="denied"
    )
    assert unknown.outcome in ("missing", "not_member") and _raw(case) == before


def test_archived_project_write_conflicts_read_still_works(case):
    with case["pool"].transaction() as conn:
        conn.execute("UPDATE project_spaces SET archived=1 WHERE project_id=?", (case["pid"],))
    before = _raw(case)
    assert _snapshot(case).project_archived is True
    assert _create(case).outcome == "project_archived"
    assert _raw(case) == before


def test_complete_member_set_locked_once_and_reference_catalog_pairing(case, monkeypatch):
    calls = []
    original = project_plan_locks.member_roles

    def record(db, conn, pid, ids, *, write):
        calls.append((tuple(ids), write))
        return original(db, conn, pid, ids, write=write)

    monkeypatch.setattr(project_plan_locks, "member_roles", record)
    created = case["repo"].create_view(
        case["pid"],
        actor_user_id=case["admin"],
        expected_revision=1,
        expected_catalog_revision=1,
        name="locks",
        view_type="table",
        definition=_definition(assignees=(case["member"],)),
    )
    assert created.outcome == "created"
    assert calls == [((case["member"], case["admin"]), True)]
    before = _raw(case)
    for user in (case["outsider"], 2**100):
        invalid = _create(case, "bad", definition=_definition(assignees=(user,)))
        assert invalid.outcome == "invalid_assignee" and _raw(case) == before
    tag = ProjectTodoCatalogRepo(case["pool"]).create_option(
        project_id=case["pid"],
        actor_user_id=case["owner"],
        expected_revision=1,
        kind="tag",
        name="historical",
        color="blue",
    )
    assert _create(case, "stale", catalog_revision=1).outcome == "catalog_revision_conflict"
    assert (
        _create(case, "badtag", definition=_definition(tags=("absent",))).outcome
        == "invalid_catalog_reference"
    )
    ProjectTodoCatalogRepo(case["pool"]).set_archived(
        project_id=case["pid"],
        actor_user_id=case["owner"],
        expected_revision=2,
        kind="tag",
        option_id=tag.item.option_id,
        archived=True,
    )
    valid = _create(case, "archived reference", definition=_definition(tags=(tag.item.option_id,)))
    assert valid.outcome == "created"
    rename = case["repo"].update_view(
        case["pid"],
        valid.item.view_id,
        actor_user_id=case["owner"],
        expected_version=1,
        name="catalog-independent rename",
        expected_catalog_revision=1,
    )
    assert rename.outcome == "updated"


@pytest.mark.parametrize("limit", ["active", "total"])
def test_real_active_total_quotas_preserve_every_row_state_and_event(case, limit):
    snapshot = _snapshot(case)
    active = 30 if limit == "active" else 29
    total = 30 if limit == "active" else 100
    with case["pool"].transaction() as conn:
        for index in range(2, total):
            conn.execute(
                "INSERT INTO project_todo_views(view_id,project_id,name,name_key,view_type,definition_json,version,position,archived_at,created_at,updated_at) VALUES(?,?,?,?,?,?,1,?,?,1,1)",
                (
                    f"view{index:03}",
                    case["pid"],
                    f"Name{index}",
                    f"name{index}",
                    "table",
                    json.dumps(default_definition("table")),
                    index,
                    None if index < active else 1,
                ),
            )
    before = _raw(case)
    assert _create(case, "overflow").outcome == f"{limit}_limit"
    assert _raw(case) == before
    assert _snapshot(case).revision == snapshot.revision


class _FaultConnection:
    def __init__(self, connection, fragment, error):
        self.connection, self.fragment, self.error = connection, fragment, error

    def execute(self, sql, params=None):
        result = self.connection.execute(sql, params or ())
        if self.fragment and self.fragment in sql:
            self.fragment = ""
            raise self.error("actual SQL installed before fault")
        return result

    def __getattr__(self, name):
        return getattr(self.connection, name)


class _FaultPool:
    dialect = "sqlite"

    def __init__(self, pool, fragment, error):
        self.pool, self.fragment, self.error = pool, fragment, error

    @contextmanager
    def transaction(self):
        with self.pool.transaction() as conn:
            yield _FaultConnection(conn, self.fragment, self.error)

    @contextmanager
    def connect(self):
        with self.pool.connect() as conn:
            yield conn

    def close(self):
        self.pool.close()


FAULTS = [
    (op, stage)
    for op in ("create", "update", "order", "archive", "restore")
    for stage in ("item", "state", "event")
] + [("default", "state"), ("default", "event")]


@pytest.mark.parametrize("operation,stage", FAULTS)
@pytest.mark.parametrize("error", [RuntimeError, KeyboardInterrupt])
def test_actual_sql_then_fault_rolls_back_views_state_defaults_and_events(
    case, operation, stage, error
):
    snapshot = _snapshot(case)
    table, board = snapshot.items
    version, revision = 1, 1
    if operation == "restore":
        result = case["repo"].archive_view(
            case["pid"], table.view_id, actor_user_id=case["owner"], expected_version=1
        )
        version, revision = result.item.version, result.revision
    before = _raw(case)
    fragment = {
        "item": "INSERT INTO project_todo_views"
        if operation == "create"
        else "UPDATE project_todo_views",
        "state": "UPDATE project_todo_view_state",
        "event": "INSERT INTO project_events",
    }[stage]
    repo = ProjectTodoViewRepo(_FaultPool(case["pool"], fragment, error))
    params = {"actor_user_id": case["owner"]}
    with pytest.raises(error, match="actual SQL"):
        if operation == "create":
            repo.create_view(
                case["pid"],
                **params,
                expected_revision=revision,
                expected_catalog_revision=1,
                name="fault",
                view_type="table",
                definition=_definition(),
            )
        elif operation == "update":
            repo.update_view(
                case["pid"], table.view_id, **params, expected_version=version, name="fault"
            )
        elif operation == "order":
            repo.order_views(
                case["pid"],
                **params,
                expected_revision=revision,
                view_ids=[board.view_id, table.view_id],
            )
        elif operation == "default":
            repo.set_default_view(
                case["pid"], **params, expected_revision=revision, view_id=board.view_id
            )
        else:
            getattr(repo, operation + "_view")(
                case["pid"], table.view_id, **params, expected_version=version
            )
    assert _raw(case) == before
    with case["pool"].connect() as conn:
        assert not conn.in_transaction


@pytest.mark.parametrize("same_view", [True, False])
def test_real_two_sqlite_pools_same_view_one_winner_different_views_both_win(case, same_view):
    second = SqlitePool(case["pool"].path)
    rows = _snapshot(case).items
    repos = (case["repo"], ProjectTodoViewRepo(second))
    barrier = threading.Barrier(2)
    results, errors = [], []

    def worker(index):
        try:
            barrier.wait(5)
            row = rows[0] if same_view else rows[index]
            results.append(
                repos[index].update_view(
                    case["pid"],
                    row.view_id,
                    actor_user_id=case["owner"],
                    expected_version=1,
                    name=f"writer{index}",
                )
            )
        except BaseException as error:
            errors.append(error)

    threads = [threading.Thread(target=worker, args=(index,)) for index in range(2)]
    try:
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(8)
        assert not any(thread.is_alive() for thread in threads)
        assert errors == []
        outcomes = sorted(result.outcome for result in results)
        assert outcomes == (
            ["updated", "view_version_conflict"] if same_view else ["updated", "updated"]
        )
        assert _snapshot(case).revision == (2 if same_view else 3)
    finally:
        second.close()


@pytest.mark.parametrize("sqlstate", ["40001", "40P01", "55P03"])
def test_database_conflict_mapping_rolls_back_without_hidden_retry_or_raw_chain(case, sqlstate):
    class BackendConflict(RuntimeError):
        pass

    BackendConflict.sqlstate = sqlstate

    class CountingFaultPool(_FaultPool):
        calls = 0

        @contextmanager
        def transaction(self):
            self.calls += 1
            with super().transaction() as conn:
                yield conn

    table = _snapshot(case).items[0]
    before = _raw(case)
    db = CountingFaultPool(case["pool"], "UPDATE project_todo_views", BackendConflict)
    repo = ProjectTodoViewRepo(db)
    result = repo.update_view(
        case["pid"], table.view_id, actor_user_id=case["owner"], expected_version=1, name="fault"
    )
    assert result.outcome == "transaction_conflict" and db.calls == 1
    assert _raw(case) == before
    db = CountingFaultPool(case["pool"], "SELECT role FROM project_members", BackendConflict)
    with pytest.raises(module.ViewTransactionConflict) as caught:
        ProjectTodoViewRepo(db).list_views(case["pid"], user_id=case["owner"])
    assert caught.value.__cause__ is None and caught.value.__suppress_context__
    assert str(caught.value) == "" and db.calls == 1 and _raw(case) == before
