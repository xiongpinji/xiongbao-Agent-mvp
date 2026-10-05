"""D2 relationship invariants on an owned synthetic SQLite database."""

from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest

from octop.infra.db.migrate import run_migrations
from octop.infra.db.pool import SqlitePool
from octop.infra.db.repos.project_plan_query import ProjectPlanQueryRepo
from octop.infra.db.repos.project_todo_views import ProjectTodoViewRepo
from octop.infra.db.repos.project_todos import ProjectTodoRepo
from octop.infra.db.repos.projects import ProjectRepo
from octop.infra.db.repos.users import UserRepo
from octop.infra.errors import OctopError
from octop.infra.projects.plan_definition import default_definition
from octop.infra.projects.plan_query import ProjectPlanQueryService
from octop.infra.projects.todos import ProjectTodoService


@pytest.fixture
def scene(tmp_path: Path):
    pool = SqlitePool(tmp_path / "owned.db")
    run_migrations(pool)
    users = UserRepo(pool)
    owner = users.create(username="owner", password_hash="h", role="user")
    member = users.create(username="member", password_hash="h", role="user")
    projects = ProjectRepo(pool)
    pid = projects.create_with_owner(creator_user_id=owner, name="D2 owned").project_id
    projects.add_member(pid, member, role="member")
    repo = ProjectTodoRepo(pool)
    try:
        yield pool, users, repo, pid, owner, member
    finally:
        pool.close()


def test_existing_and_new_rows_have_real_relationship_states(scene):
    pool, _, repo, pid, owner, _ = scene
    row = repo.create(project_id=pid, creator_user_id=owner, title="root").row
    assert row is not None
    with pool.connect() as conn:
        assert (
            conn.execute(
                "SELECT revision FROM project_plan_hierarchy_state WHERE project_id=?", (pid,)
            ).fetchone()[0]
            == 1
        )
        assert (
            conn.execute(
                "SELECT revision FROM project_todo_children_state WHERE todo_id=?", (row.todo_id,)
            ).fetchone()[0]
            == 1
        )
    assert row.parent_todo_id is None
    assert row.children_revision == 1


def service(scene):
    return ProjectTodoService(
        SimpleNamespace(project_todo_repo=scene[2], config=SimpleNamespace(default_timezone="UTC"))
    )


def relationship_query(scene, limit=20):
    pool, _, repo, pid, owner, _ = scene
    views = ProjectTodoViewRepo(pool)
    snapshot = views.list_views(pid, user_id=owner)
    svc = ProjectPlanQueryService(
        SimpleNamespace(
            db=pool,
            config=SimpleNamespace(default_timezone="UTC"),
            project_todo_view_repo=views,
            project_plan_query_repo=ProjectPlanQueryRepo(pool),
        )
    )
    return svc.query(
        pid,
        user_id=owner,
        data={
            "view_id": snapshot.default_view_id,
            "expected_view_version": 1,
            "expected_catalog_revision": 1,
            "override_definition": default_definition("table"),
            "limit": limit,
        },
    )


@pytest.mark.parametrize("kind", ["legacy", "query"])
def test_relationship_page_queries_are_constant_and_project_real_counts(scene, kind):
    pool, _, repo, pid, owner, _ = scene
    roots = [
        repo.create(project_id=pid, creator_user_id=owner, title=f"root {i}", ts=100 + i).row
        for i in range(30)
    ]
    parent = roots[-1]
    child(scene, parent, status="done")
    counts = []
    for limit in (5, 20):
        statements = []
        with pool.connect() as conn:
            conn.set_trace_callback(statements.append)
            try:
                rows = (
                    repo.list_todos(pid, user_id=owner, limit=limit)
                    if kind == "legacy"
                    else relationship_query(scene, limit)["items"]
                )
            finally:
                conn.set_trace_callback(None)
        counts.append(sum(sql.lstrip().upper().startswith("SELECT") for sql in statements))
        if kind == "legacy":
            projected = next(row for row in rows if row.todo_id == parent.todo_id)
            assert (
                projected.children_count,
                projected.done_children_count,
                projected.children_revision,
            ) == (1, 1, 2)
        else:
            # Distinct real body timestamps place this parent first in both APIs.
            projected = next(row for row in rows if row["todo_id"] == parent.todo_id)
            assert (
                projected["children_count"],
                projected["done_children_count"],
                projected["children_revision"],
            ) == (1, 1, 2)
        assert len(rows) == limit + (kind == "legacy")
    assert counts[0] == counts[1]
    if kind == "legacy":
        assert counts[0] <= 5


@pytest.mark.parametrize("kind", ["legacy", "query"])
def test_relationship_page_missing_state_is_not_a_synthetic_revision(scene, kind):
    pool, _, repo, pid, owner, _ = scene
    parent = repo.create(project_id=pid, creator_user_id=owner, title="root").row
    with pool.transaction() as conn:
        conn.execute(
            "DELETE FROM project_todo_children_state WHERE project_id=? AND todo_id=?",
            (pid, parent.todo_id),
        )
    with pytest.raises(RuntimeError):
        if kind == "legacy":
            repo.list_todos(pid, user_id=owner)
        else:
            relationship_query(scene)


def child(scene, parent, actor=None, key=None, **fields):
    return service(scene).create_child(
        scene[3],
        parent.todo_id,
        actor_user_id=scene[4] if actor is None else actor,
        expected_children_revision=parent.children_revision,
        client_request_id=str(uuid4()) if key is None else key,
        fields={"title": "child", **fields},
    )


def test_idempotency_current_dto_and_default_root_list(scene):
    _, _, repo, pid, owner, _ = scene
    parent = repo.create(project_id=pid, creator_user_id=owner, title="root").row
    key = str(uuid4())
    first = child(scene, parent, key=key)
    assert first["replayed"] is False
    assert first["item"]["parent_todo_id"] == parent.todo_id
    assert (
        first["children_revision"],
        first["parent_display_revision"],
        first["active_count"],
    ) == (2, 2, 1)
    assert [row.todo_id for row in repo.list_todos(pid, user_id=owner)] == [parent.todo_id]
    replay = child(scene, parent, key=key)
    assert replay["replayed"] is True
    assert replay["item"]["todo_id"] == first["item"]["todo_id"]
    assert replay["children_revision"] == 2
    with pytest.raises(OctopError) as error:
        child(scene, parent, key=key, title="different")
    assert error.value.details["reason"] == "idempotency_conflict"


def test_old_writers_parent_counts_and_confirmation(scene):
    _, _, repo, pid, owner, _ = scene
    parent = repo.create(project_id=pid, creator_user_id=owner, title="root").row
    c = child(scene, parent)["item"]
    denied = repo.delete(
        project_id=pid, todo_id=parent.todo_id, actor_user_id=owner, expected_version=parent.version
    )
    assert denied.outcome == "children_confirmation_required"
    result = repo.update(
        project_id=pid,
        todo_id=c["todo_id"],
        actor_user_id=owner,
        expected_version=c["version"],
        status="done",
    )
    assert result.outcome == "updated"
    fresh = repo.get(pid, parent.todo_id, user_id=owner)
    assert (
        fresh.version,
        fresh.display_revision,
        fresh.children_revision,
        fresh.done_children_count,
    ) == (1, 3, 2, 1)
    assert (
        repo.delete(
            project_id=pid, todo_id=c["todo_id"], actor_user_id=owner, expected_version=2
        ).outcome
        == "deleted"
    )
    fresh = repo.get(pid, parent.todo_id, user_id=owner)
    assert (fresh.children_count, fresh.children_revision, fresh.display_revision) == (0, 3, 4)


def test_tree_exact_set_and_empty_branch(scene):
    pool, _, repo, pid, owner, _ = scene
    parent = repo.create(project_id=pid, creator_user_id=owner, title="root").row
    c = child(scene, parent)["item"]
    result = repo.delete_tree(
        project_id=pid,
        parent_todo_id=parent.todo_id,
        actor_user_id=owner,
        expected_version=1,
        expected_children_revision=2,
        children=[],
    )
    assert result.outcome == "children_revision_conflict"
    assert repo.get(pid, parent.todo_id).version == 1
    result = repo.delete_tree(
        project_id=pid,
        parent_todo_id=parent.todo_id,
        actor_user_id=owner,
        expected_version=1,
        expected_children_revision=2,
        children=[(c["todo_id"], 1)],
    )
    assert result.outcome == "deleted"
    assert result.data["deleted_todo_ids"] == [parent.todo_id, c["todo_id"]]
    assert result.data["children_revision"] == 3
    empty = repo.create(project_id=pid, creator_user_id=owner, title="empty").row
    with pool.connect() as conn:
        h = conn.execute(
            "SELECT revision FROM project_plan_hierarchy_state WHERE project_id=?", (pid,)
        ).fetchone()[0]
    result = repo.delete_tree(
        project_id=pid,
        parent_todo_id=empty.todo_id,
        actor_user_id=owner,
        expected_version=1,
        expected_children_revision=1,
        children=[],
    )
    assert result.data["children_revision"] == 1
    assert result.data["hierarchy_revision"] == h


def test_physical_parent_delete_promotes_other_creator_once_with_assignee(scene):
    pool, users, repo, pid, owner, member = scene
    parent = repo.create(project_id=pid, creator_user_id=member, title="owned by removed user").row
    # Owner may create child under the other member's parent, then delegate it to that member.
    c = child(scene, parent, actor=owner, assignee_user_id=member)["item"]
    users.delete(member)
    fresh = repo.get(pid, c["todo_id"], user_id=owner)
    assert (
        fresh.parent_todo_id,
        fresh.children_revision,
        fresh.version,
        fresh.display_revision,
        fresh.assignee_user_id,
    ) == (None, 1, 1, c["display_revision"] + 1, None)
    assert (fresh.title, fresh.creator_user_id) == ("child", owner)
    with pool.connect() as conn:
        result = conn.execute(
            "SELECT child_todo_id,result_state FROM project_todo_child_create_requests"
        ).fetchone()
        assert tuple(result) == (c["todo_id"], "invalidated")
        assert (
            conn.execute(
                "SELECT revision FROM project_plan_hierarchy_state WHERE project_id=?", (pid,)
            ).fetchone()[0]
            == 3
        )


def snapshot(pool):
    tables = (
        "users",
        "project_members",
        "project_todos",
        "project_todo_tag_links",
        "project_todo_display_state",
        "project_todo_attachment_state",
        "project_todo_children",
        "project_todo_children_state",
        "project_plan_hierarchy_state",
        "project_todo_child_create_requests",
        "project_events",
    )
    with pool.connect() as conn:
        return {
            table: sorted((tuple(row) for row in conn.execute("SELECT * FROM " + table)), key=repr)
            for table in tables
        }


def test_replay_current_body_invalidated_and_fingerprint_presence(scene):
    pool, _, repo, pid, owner, _ = scene
    parent = repo.create(project_id=pid, creator_user_id=owner, title="root").row
    key = str(uuid4())
    first = child(scene, parent, key=key)["item"]
    updated = repo.update(
        project_id=pid,
        todo_id=first["todo_id"],
        actor_user_id=owner,
        expected_version=1,
        title="edited",
        status="done",
    ).row
    before = snapshot(pool)
    replay = child(scene, parent, key=key)
    assert replay["item"]["version"] == updated.version
    assert replay["item"]["title"] == "edited"
    assert replay["done_count"] == 1
    assert snapshot(pool) == before
    with pytest.raises(OctopError) as error:
        child(scene, parent, key=key, expected_catalog_revision=1)
    assert error.value.details["reason"] == "idempotency_conflict"
    repo.delete(project_id=pid, todo_id=first["todo_id"], actor_user_id=owner, expected_version=2)
    before = snapshot(pool)
    with pytest.raises(OctopError) as error:
        child(scene, parent, key=key)
    assert error.value.details["reason"] == "child_result_invalidated"
    assert snapshot(pool) == before


def test_cursor_real_anchor_binding_and_display_watermark(scene, monkeypatch):
    import base64
    import json

    _, _, repo, pid, owner, _ = scene
    import octop.infra.db.repos.project_todos as todo_module

    monkeypatch.setattr(todo_module, "now_ts", lambda: 100)
    parent = repo.create(project_id=pid, creator_user_id=owner, title="root").row
    one = child(scene, parent)["item"]
    parent = repo.get(pid, parent.todo_id)
    child(scene, parent)
    svc = service(scene)
    page = svc.list_children(pid, parent.todo_id, actor_user_id=owner, limit=1)
    token = page["next_cursor"]
    value = json.loads(base64.urlsafe_b64decode(token + "=" * (-len(token) % 4)))

    def encoded(data):
        return (
            base64.urlsafe_b64encode(json.dumps(data, separators=(",", ":")).encode())
            .decode()
            .rstrip("=")
        )

    for key, wrong in (
        ("project_id", "another"),
        ("parent_todo_id", "other"),
        ("last_todo_id", "missing"),
        ("last_created_at", value["last_created_at"] + 1),
    ):
        with pytest.raises(OctopError) as error:
            svc.list_children(
                pid, parent.todo_id, actor_user_id=owner, cursor=encoded({**value, key: wrong})
            )
        assert error.value.details["reason"] == "query_changed"
    for illegal in (
        token + "=",
        "!",
        "a" * 2049,
        encoded({**value, "v": True}),
        encoded({**value, "children_revision": 0}),
    ):
        with pytest.raises(OctopError) as error:
            svc.list_children(pid, parent.todo_id, actor_user_id=owner, cursor=illegal)
        assert error.value.details["reason"] == "invalid_cursor"
    repo.update(
        project_id=pid,
        todo_id=one["todo_id"],
        actor_user_id=owner,
        expected_version=1,
        status="done",
    )
    with pytest.raises(OctopError) as error:
        svc.list_children(pid, parent.todo_id, actor_user_id=owner, cursor=token)
    assert error.value.details["reason"] == "query_changed"


@pytest.mark.parametrize(
    "stage", ["todo", "relation", "result", "children", "hierarchy", "display", "event"]
)
def test_child_create_each_write_failure_rolls_back(scene, stage):
    import sqlite3

    pool, _, repo, pid, owner, _ = scene
    parent = repo.create(project_id=pid, creator_user_id=owner, title="root").row
    tables = {
        "todo": "project_todos",
        "relation": "project_todo_children",
        "result": "project_todo_child_create_requests",
        "children": "project_todo_children_state",
        "hierarchy": "project_plan_hierarchy_state",
        "display": "project_todo_display_state",
        "event": "project_events",
    }
    event = "INSERT" if stage in {"todo", "relation", "result", "event"} else "UPDATE"
    with pool.transaction() as conn:
        conn.execute(
            f"CREATE TEMP TRIGGER fail_child AFTER {event} ON {tables[stage]} BEGIN SELECT RAISE(ABORT,'controlled child failure'); END"
        )
    before = snapshot(pool)
    try:
        with pytest.raises(sqlite3.IntegrityError, match="controlled child failure"):
            child(scene, parent)
        assert snapshot(pool) == before
        assert not pool._conn.in_transaction
    finally:
        with pool.transaction() as conn:
            conn.execute("DROP TRIGGER fail_child")


def test_tree_per_child_creator_acl_and_failure_rollback(scene):
    import sqlite3

    pool, _, repo, pid, owner, member = scene
    parent = repo.create(project_id=pid, creator_user_id=member, title="member root").row
    c = child(scene, parent, actor=owner)["item"]
    before = snapshot(pool)
    denied = repo.delete_tree(
        project_id=pid,
        parent_todo_id=parent.todo_id,
        actor_user_id=member,
        expected_version=1,
        expected_children_revision=2,
        children=[(c["todo_id"], 1)],
    )
    assert denied.outcome == "forbidden"
    assert snapshot(pool) == before
    with pool.transaction() as conn:
        conn.execute(
            "CREATE TEMP TRIGGER fail_tree AFTER UPDATE ON project_todos WHEN NEW.todo_id='"
            + c["todo_id"]
            + "' BEGIN SELECT RAISE(ABORT,'controlled tree failure'); END"
        )
    try:
        with pytest.raises(sqlite3.IntegrityError, match="controlled tree failure"):
            repo.delete_tree(
                project_id=pid,
                parent_todo_id=parent.todo_id,
                actor_user_id=owner,
                expected_version=1,
                expected_children_revision=2,
                children=[(c["todo_id"], 1)],
            )
        assert snapshot(pool) == before
    finally:
        with pool.transaction() as conn:
            conn.execute("DROP TRIGGER fail_tree")


def test_bulk_parent_and_two_children_bump_parent_display_once(scene):
    _, _, repo, pid, owner, _ = scene
    parent = repo.create(project_id=pid, creator_user_id=owner, title="root").row
    c1 = child(scene, parent)["item"]
    parent = repo.get(pid, parent.todo_id)
    c2 = child(scene, parent)["item"]
    parent = repo.get(pid, parent.todo_id)
    result = repo.bulk_update(
        project_id=pid,
        actor_user_id=owner,
        items=[(c2["todo_id"], 1), (parent.todo_id, 1), (c1["todo_id"], 1)],
        status="done",
    )
    assert result.outcome == "updated"
    assert [row.todo_id for row in result.rows] == [c2["todo_id"], parent.todo_id, c1["todo_id"]]
    fresh = repo.get(pid, parent.todo_id)
    assert (
        fresh.version,
        fresh.display_revision,
        fresh.children_revision,
        fresh.done_children_count,
    ) == (2, parent.display_revision + 1, 3, 2)


def test_actual_active_and_retained_hard_limits_and_replay(scene):
    pool, _, repo, pid, owner, _ = scene
    parent = repo.create(project_id=pid, creator_user_id=owner, title="root").row
    key = str(uuid4())
    first = child(scene, parent, key=key)["item"]
    for _ in range(99):
        parent = repo.get(pid, parent.todo_id)
        child(scene, parent)
    parent = repo.get(pid, parent.todo_id)
    before = snapshot(pool)
    with pytest.raises(OctopError) as error:
        child(scene, parent)
    assert error.value.details["reason"] == "children_active_limit"
    assert child(scene, parent, key=key)["replayed"] is True
    assert snapshot(pool) == before
    # Keep precisely 500 genuine retained relations using normal create/delete.
    children = service(scene).list_children(pid, parent.todo_id, actor_user_id=owner, limit=100)[
        "items"
    ]
    before = snapshot(pool)
    with pytest.raises(ValueError, match="invalid child set"):
        repo.delete_tree(
            project_id=pid,
            parent_todo_id=parent.todo_id,
            actor_user_id=owner,
            expected_version=1,
            expected_children_revision=parent.children_revision,
            children=[(item["todo_id"], 1) for item in children] + [("extra", 1)],
        )
    assert snapshot(pool) == before
    for item in children:
        assert (
            repo.delete(
                project_id=pid, todo_id=item["todo_id"], actor_user_id=owner, expected_version=1
            ).outcome
            == "deleted"
        )
    for _ in range(400):
        parent = repo.get(pid, parent.todo_id)
        c = child(scene, parent)["item"]
        assert (
            repo.delete(
                project_id=pid, todo_id=c["todo_id"], actor_user_id=owner, expected_version=1
            ).outcome
            == "deleted"
        )
    parent = repo.get(pid, parent.todo_id)
    assert parent.children_count == 0
    with pool.connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM project_todo_children").fetchone()[0] == 500
    before = snapshot(pool)
    with pytest.raises(OctopError) as error:
        child(scene, parent)
    assert error.value.details["reason"] == "children_retained_limit"
    assert snapshot(pool) == before
    assert first["todo_id"] in {row[5] for row in before["project_todo_child_create_requests"]}


def test_multi_parent_physical_delete_once_and_project_fk_rollback(scene):
    import sqlite3

    pool, users, repo, pid, owner, member = scene
    parents = [
        repo.create(project_id=pid, creator_user_id=member, title=str(i)).row for i in range(2)
    ]
    cs = [child(scene, parent, actor=owner)["item"] for parent in parents]
    # Existing creator-project FK still rejects, including already-planned D2 writes.
    ProjectRepo(pool).create_with_owner(creator_user_id=member, name="FK held")
    held = snapshot(pool)
    with pytest.raises(sqlite3.IntegrityError):
        users.delete(member)
    assert snapshot(pool) == held
    with pool.transaction() as conn:
        conn.execute("DELETE FROM project_spaces WHERE creator_user_id=?", (member,))
    users.delete(member)
    for item in cs:
        fresh = repo.get(pid, item["todo_id"])
        assert fresh.parent_todo_id is None
        assert fresh.version == 1
        assert fresh.display_revision == item["display_revision"] + 1
    with pool.connect() as conn:
        assert (
            conn.execute(
                "SELECT revision FROM project_plan_hierarchy_state WHERE project_id=?", (pid,)
            ).fetchone()[0]
            == 4
        )


def test_soft_tree_keeps_relation_and_never_promotes(scene):
    pool, _, repo, pid, owner, member = scene
    parent = repo.create(project_id=pid, creator_user_id=member, title="root").row
    c = child(scene, parent, actor=owner)["item"]
    assert (
        repo.delete_tree(
            project_id=pid,
            parent_todo_id=parent.todo_id,
            actor_user_id=owner,
            expected_version=1,
            expected_children_revision=2,
            children=[(c["todo_id"], 1)],
        ).outcome
        == "deleted"
    )
    assert repo.get(pid, c["todo_id"]) is None
    with pool.connect() as conn:
        assert (
            conn.execute(
                "SELECT parent_todo_id FROM project_todo_children WHERE child_todo_id=?",
                (c["todo_id"],),
            ).fetchone()[0]
            == parent.todo_id
        )


@pytest.mark.parametrize(
    "table,event",
    [
        ("project_todo_children", "DELETE"),
        ("project_todo_child_create_requests", "UPDATE"),
        ("project_plan_hierarchy_state", "UPDATE"),
        ("project_todo_display_state", "UPDATE"),
        ("users", "DELETE"),
    ],
)
def test_physical_delete_planned_mutations_rollback_on_real_sql_error(scene, table, event):
    import sqlite3

    pool, users, repo, pid, owner, member = scene
    parent = repo.create(project_id=pid, creator_user_id=member, title="root").row
    child(scene, parent, actor=owner, assignee_user_id=member)
    with pool.transaction() as conn:
        conn.execute(
            f"CREATE TEMP TRIGGER fail_remove AFTER {event} ON {table} BEGIN SELECT RAISE(ABORT,'controlled remove failure'); END"
        )
    before = snapshot(pool)
    try:
        with pytest.raises(sqlite3.IntegrityError, match="controlled remove failure"):
            users.delete(member)
        assert snapshot(pool) == before
        assert not pool._conn.in_transaction
    finally:
        with pool.transaction() as conn:
            conn.execute("DROP TRIGGER fail_remove")


def test_v39_upgrade_reentry_and_wrong_high_watermark_fail_closed(tmp_path, monkeypatch):
    import shutil

    import octop.infra.db.migrate as migration

    old_dir = tmp_path / "v39"
    old_dir.mkdir()
    actual = migration._MIGRATIONS_DIR
    for path in actual.iterdir():
        if path.name.endswith(".sql") and int(path.name.split("_")[0]) <= 39:
            shutil.copyfile(path, old_dir / path.name)
    pool = SqlitePool(tmp_path / "upgrade.db")
    try:
        monkeypatch.setattr(migration, "_MIGRATIONS_DIR", old_dir)
        run_migrations(pool)
        users = UserRepo(pool)
        owner = users.create(username="old", password_hash="h", role="user")
        pid = ProjectRepo(pool).create_with_owner(creator_user_id=owner, name="old").project_id
        # True old-schema row; the new repository correctly requires D2 state.
        with pool.transaction() as conn:
            conn.execute(
                "INSERT INTO project_todos(todo_id,project_id,creator_user_id,title,title_search_key,description,status,version,created_at,updated_at) VALUES('oldtodo',?,?,'old','old','','todo',1,1,1)",
                (pid, owner),
            )
            conn.execute(
                "INSERT INTO project_todo_display_state(project_id,todo_id,revision,updated_at) VALUES(?,'oldtodo',1,1)",
                (pid,),
            )
            conn.execute(
                "INSERT INTO project_todo_attachment_state(project_id,todo_id,revision,updated_at) VALUES(?,'oldtodo',1,1)",
                (pid,),
            )
        monkeypatch.setattr(migration, "_MIGRATIONS_DIR", actual)
        run_migrations(pool)
        row = ProjectTodoRepo(pool).get(pid, "oldtodo")
        assert row.children_revision == 1
        before = snapshot(pool)
        run_migrations(pool)
        assert snapshot(pool) == before
        with pool.transaction() as conn:
            conn.execute("UPDATE _schema_version SET version=99")
            conn.execute("DELETE FROM project_todo_children_state WHERE todo_id='oldtodo'")
        with pytest.raises(RuntimeError):
            run_migrations(pool)
        with pool.connect() as conn:
            assert conn.execute("SELECT version FROM _schema_version").fetchone()[0] == 99
            assert (
                conn.execute("SELECT COUNT(*) FROM project_todo_children_state").fetchone()[0] == 0
            )
    finally:
        pool.close()


def test_physical_child_delete_surviving_parent_count_and_assignee_once(scene):
    pool, users, repo, pid, owner, member = scene
    parent = repo.create(
        project_id=pid, creator_user_id=owner, title="root", assignee_user_id=member
    ).row
    c = child(scene, parent, actor=member)["item"]
    parent = repo.get(pid, parent.todo_id)
    users.delete(member)
    fresh = repo.get(pid, parent.todo_id)
    assert (
        fresh.version,
        fresh.children_count,
        fresh.children_revision,
        fresh.display_revision,
        fresh.assignee_user_id,
    ) == (1, 0, 3, parent.display_revision + 1, None)
    assert repo.get(pid, c["todo_id"]) is None
    with pool.connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM project_todo_children").fetchone()[0] == 0
        result = conn.execute(
            "SELECT child_todo_id,result_state FROM project_todo_child_create_requests"
        ).fetchone()
        assert tuple(result) == (c["todo_id"], "invalidated")


def test_result_null_safe_identity_guard_and_tombstone_immutable(scene):
    import sqlite3

    pool, _, repo, pid, owner, _ = scene
    parent = repo.create(project_id=pid, creator_user_id=owner, title="root").row
    c = child(scene, parent)["item"]
    before = snapshot(pool)
    for assignment in (
        "child_todo_id=NULL",
        "request_fingerprint='" + "0" * 64 + "'",
        "parent_todo_id='other'",
        "actor_user_id=999",
    ):
        with pytest.raises(sqlite3.IntegrityError), pool.transaction() as conn:
            conn.execute("UPDATE project_todo_child_create_requests SET " + assignment)
        assert snapshot(pool) == before
    with pool.transaction() as conn:
        conn.execute("DELETE FROM project_todos WHERE todo_id=?", (c["todo_id"],))
    invalidated = snapshot(pool)
    with pytest.raises(sqlite3.IntegrityError), pool.transaction() as conn:
        conn.execute(
            "UPDATE project_todo_child_create_requests SET result_state='recorded',invalidated_at=NULL"
        )
    assert snapshot(pool) == invalidated


def test_exact_tree_of_100_children_commits_once(scene):
    pool, _, repo, pid, owner, _ = scene
    parent = repo.create(project_id=pid, creator_user_id=owner, title="root").row
    items = []
    for _ in range(100):
        items.append(child(scene, parent)["item"])
        parent = repo.get(pid, parent.todo_id)
    receipt = repo.delete_tree(
        project_id=pid,
        parent_todo_id=parent.todo_id,
        actor_user_id=owner,
        expected_version=1,
        expected_children_revision=101,
        children=[(row["todo_id"], 1) for row in reversed(items)],
    )
    assert receipt.outcome == "deleted"
    assert receipt.data == {
        "deleted_todo_ids": [parent.todo_id, *sorted(row["todo_id"] for row in items)],
        "children_revision": 102,
        "hierarchy_revision": 102,
    }
    with pool.connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM project_todo_children").fetchone()[0] == 100
        assert (
            conn.execute("SELECT COUNT(*) FROM project_todos WHERE deleted_at IS NULL").fetchone()[
                0
            ]
            == 0
        )


def test_sorted_tags_and_trimmed_body_share_the_same_fingerprint(scene):
    from octop.infra.db.repos.project_todo_catalog import ProjectTodoCatalogRepo

    pool, _, repo, pid, owner, _ = scene
    catalog = ProjectTodoCatalogRepo(pool)
    ids = []
    for name in ("one", "two"):
        current = catalog.get_catalog(pid, user_id=owner)
        item = catalog.create_option(
            project_id=pid,
            actor_user_id=owner,
            expected_revision=current.revision,
            kind="tag",
            name=name,
            color="red",
        )
        ids.append(item.item.option_id)
    current = catalog.get_catalog(pid, user_id=owner)
    parent = repo.create(project_id=pid, creator_user_id=owner, title="root").row
    key = str(uuid4())
    first = child(
        scene,
        parent,
        key=key,
        title=" child ",
        description=" body ",
        tag_ids=ids,
        expected_catalog_revision=current.revision,
    )
    before = snapshot(pool)
    replay = child(
        scene,
        parent,
        key=key.upper(),
        title="child",
        description="body",
        tag_ids=list(reversed(ids)),
        expected_catalog_revision=current.revision,
    )
    assert replay["replayed"] is True
    assert replay["item"]["todo_id"] == first["item"]["todo_id"]
    assert replay["item"]["tag_ids"] == sorted(ids)
    assert snapshot(pool) == before
