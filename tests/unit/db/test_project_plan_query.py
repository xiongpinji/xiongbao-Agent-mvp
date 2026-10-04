"""Independent, real SQLite truth tables for bounded shared plan queries."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from octop.infra.db.migrate import run_migrations
from octop.infra.db.pool import SqlitePool
from octop.infra.db.repos.project_plan_query import ProjectPlanQueryRepo
from octop.infra.db.repos.project_todo_catalog import ProjectTodoCatalogRepo
from octop.infra.db.repos.project_todo_views import ProjectTodoViewRepo
from octop.infra.db.repos.project_todos import ProjectTodoRepo
from octop.infra.db.repos.projects import ProjectRepo
from octop.infra.db.repos.users import UserRepo
from octop.infra.projects.plan_definition import default_definition
from octop.infra.projects.plan_query import ProjectPlanQueryService


@pytest.fixture
def query_case(tmp_path):
    pool = SqlitePool(tmp_path / "query.db")
    run_migrations(pool)
    users, projects = UserRepo(pool), ProjectRepo(pool)
    owner = users.create(username="owner", password_hash="h", role="user")
    a = users.create(username="a", password_hash="h", role="user")
    b = users.create(username="b", password_hash="h", role="user")
    outsider = users.create(username="outside", password_hash="h", role="admin")
    pid = projects.create_with_owner(creator_user_id=owner, name="query").project_id
    projects.add_member(pid, a, role="member")
    projects.add_member(pid, b, role="member")
    catalog = ProjectTodoCatalogRepo(pool)
    options = {}
    for kind, name in (("priority", "pA"), ("priority", "pB"), ("tag", "tA"), ("tag", "tB")):
        snapshot = catalog.get_catalog(pid, user_id=owner)
        created = catalog.create_option(
            project_id=pid,
            actor_user_id=owner,
            expected_revision=snapshot.revision,
            kind=kind,
            name=name,
            color="red",
        )
        assert created.outcome == "created"
        options[name] = created.item.option_id
    revision = catalog.get_catalog(pid, user_id=owner).revision
    todos, views = ProjectTodoRepo(pool), ProjectTodoViewRepo(pool)
    rows = {}
    for name, assignee, priority, tags, status in (
        ("A", a, options["pA"], [options["tA"], options["tB"]], "todo"),
        ("B", b, options["pB"], [options["tB"]], "in_progress"),
        ("empty", None, None, [], "done"),
        ("C", owner, options["pA"], [options["tA"]], "todo"),
    ):
        result = todos.create(
            project_id=pid,
            creator_user_id=owner,
            title=name,
            assignee_user_id=assignee,
            priority_id=priority,
            tag_ids=tags,
            expected_catalog_revision=revision,
            status=status,
            ts=100,
        )
        assert result.outcome == "created"
        rows[name] = result.row
    snapshot = views.list_views(pid, user_id=owner)
    service = ProjectPlanQueryService(
        SimpleNamespace(
            db=pool,
            config=SimpleNamespace(default_timezone="Asia/Shanghai"),
            project_todo_view_repo=views,
            project_plan_query_repo=ProjectPlanQueryRepo(pool),
        )
    )
    yield {
        "pool": pool,
        "users": users,
        "projects": projects,
        "todos": todos,
        "views": views,
        "catalog": catalog,
        "pid": pid,
        "owner": owner,
        "a": a,
        "b": b,
        "outsider": outsider,
        "options": options,
        "rows": rows,
        "revision": revision,
        "view_id": snapshot.default_view_id,
        "service": service,
    }
    pool.close()


def query(case, *, filters=(), group_by=None, sort=None, kind="table", **kwargs):
    definition = default_definition(kind)
    definition["filters"] = list(filters)
    definition["group_by"] = group_by
    if sort is not None:
        definition["sort"] = sort
    data = {
        "view_id": case["view_id"],
        "expected_view_version": 1,
        "expected_catalog_revision": case["revision"],
        "override_definition": definition,
    }
    data.update(kwargs)
    return case["service"].query(case["pid"], user_id=case["owner"], data=data)


@pytest.mark.parametrize(
    "field,reference,expected",
    [
        ("assignee", "a", {"B", "C", "empty"}),
        ("priority", "pA", {"B", "empty"}),
    ],
)
def test_nullable_not_in_is_the_full_complement(query_case, field, reference, expected):
    value = query_case[reference] if field == "assignee" else query_case["options"][reference]
    result = query(query_case, filters=[{"field": field, "op": "not_in", "values": [value]}])
    assert {item["title"] for item in result["items"]} == expected
    assert result["total"] == result["matched_total"] == len(expected)


@pytest.mark.parametrize("op,expected", [("any", {"A", "B", "C"}), ("all", {"A"})])
def test_tag_truth_does_not_duplicate_multi_tagged_todos(query_case, op, expected):
    result = query(
        query_case,
        filters=[
            {
                "field": "tags",
                "op": op,
                "values": [query_case["options"]["tA"], query_case["options"]["tB"]],
            }
        ],
    )
    assert {item["title"] for item in result["items"]} == expected
    assert len(result["items"]) == result["total"] == len(expected)


@pytest.mark.parametrize(
    "condition,expected",
    [
        ({"field": "status", "op": "in", "values": ["todo"]}, {"A", "C"}),
        ({"field": "status", "op": "not_in", "values": ["todo"]}, {"B", "empty"}),
        ({"field": "source", "op": "in", "values": ["manual"]}, {"A", "B", "C", "empty"}),
        ({"field": "source", "op": "not_in", "values": ["manual"]}, set()),
        ({"field": "tags", "op": "none_of", "values": ["tA"]}, {"B", "empty"}),
        ({"field": "tags", "op": "is_empty"}, {"empty"}),
        ({"field": "tags", "op": "not_empty"}, {"A", "B", "C"}),
    ],
)
def test_other_public_filter_truth(query_case, condition, expected):
    condition = dict(condition)
    if condition["field"] == "tags" and "values" in condition:
        condition["values"] = [query_case["options"][value] for value in condition["values"]]
    result = query(query_case, filters=[condition])
    assert {item["title"] for item in result["items"]} == expected
    assert result["total"] == len(expected)


@pytest.mark.parametrize(
    "op,expected", [("contains", {"A"}), ("not_contains", {"B", "C", "empty"})]
)
def test_title_search_preserves_literal_spaces_and_escapes(query_case, op, expected):
    from octop.infra.utils.project_plan_keys import normalize_project_plan_key

    values = {
        "A": "prefix  %_\\Ａß  suffix",
        "B": "prefix  ZZＡSS  suffix",
        "C": "prefix %_\\ASS suffix",
        "empty": "no literal here",
    }
    with query_case["pool"].transaction() as conn:
        for name, text in values.items():
            conn.execute(
                "UPDATE project_todos SET title=?,title_search_key=? WHERE todo_id=?",
                (text, normalize_project_plan_key(text), query_case["rows"][name].todo_id),
            )
    result = query(query_case, filters=[{"field": "title", "op": op, "value": "  %_\\ａSS  "}])
    assert {item["todo_id"] for item in result["items"]} == {
        query_case["rows"][name].todo_id for name in expected
    }


@pytest.mark.parametrize(
    "condition,expected",
    [
        ({"field": "due_date", "op": "on", "value": "2027-04-02"}, {"B"}),
        ({"field": "due_date", "op": "before", "value": "2027-04-02"}, {"A", "empty"}),
        ({"field": "start_date", "op": "after", "value": "2027-04-01"}, {"B"}),
        (
            {"field": "due_date", "op": "between", "values": ["2027-04-01", "2027-04-02"]},
            {"A", "B"},
        ),
        ({"field": "start_date", "op": "is_empty"}, {"C", "empty"}),
        ({"field": "due_date", "op": "not_empty"}, {"A", "B", "empty"}),
        ({"field": "due_date", "op": "overdue", "value": True}, {"A"}),
        ({"field": "due_date", "op": "overdue", "value": False}, {"B", "C", "empty"}),
    ],
)
def test_date_and_overdue_filters_use_one_server_calendar_day(
    query_case, monkeypatch, condition, expected
):
    from octop.infra.db.repos import project_plan_locks

    monkeypatch.setattr(project_plan_locks, "server_today", lambda timezone: "2027-04-02")
    with query_case["pool"].transaction() as conn:
        for name, start, due in (
            ("A", "2027-04-01", "2027-04-01"),
            ("B", "2027-04-02", "2027-04-02"),
            ("empty", None, "2027-03-01"),
        ):
            conn.execute(
                "UPDATE project_todos SET start_date=?,due_date=? WHERE todo_id=?",
                (start, due, query_case["rows"][name].todo_id),
            )
    result = query(query_case, filters=[condition])
    assert {item["title"] for item in result["items"]} == expected
    assert result["server_today"] == "2027-04-02"
    assert result["server_timezone"] == "Asia/Shanghai"


@pytest.mark.parametrize("kind", ["status", "assignee", "priority", "tag", "source"])
def test_sql_groups_count_full_matching_set_and_include_empty_groups(query_case, kind):
    zero = query_case["users"].create(username="zero", password_hash="h", role="user")
    query_case["projects"].add_member(query_case["pid"], zero, role="member")
    result = query(query_case, group_by=kind, limit=1)
    counts = {item["key"]["id"]: item["count"] for item in result["groups"]}
    assert all(item["key"]["kind"] == kind for item in result["groups"])
    assert result["total"] == result["matched_total"] == 4
    assert len(result["items"]) == 1
    if kind == "status":
        assert counts == {"todo": 2, "in_progress": 1, "done": 1}
    elif kind == "assignee":
        assert counts == {
            str(query_case["owner"]): 1,
            str(query_case["a"]): 1,
            str(query_case["b"]): 1,
            str(zero): 0,
            None: 1,
        }
    elif kind == "priority":
        assert counts[query_case["options"]["pA"]] == 2
        assert counts[query_case["options"]["pB"]] == counts[None] == 1
    elif kind == "tag":
        assert counts == {query_case["options"]["tA"]: 2, query_case["options"]["tB"]: 2, None: 1}
        assert sum(counts.values()) == 5
    else:
        assert counts == {"manual": 4}


def test_tag_group_scope_changes_items_only(query_case):
    result = query(
        query_case, group_by="tag", group_key={"kind": "tag", "id": query_case["options"]["tA"]}
    )
    assert {item["title"] for item in result["items"]} == {"A", "C"}
    assert len(result["items"]) == 2 and result["total"] == result["matched_total"] == 4
    assert sum(group["count"] for group in result["groups"]) == 5


@pytest.mark.parametrize(
    "group_by,key",
    [
        (None, {"kind": "tag", "id": None}),
        ("status", {"kind": "tag", "id": None}),
        ("status", {"kind": "status", "id": "unknown"}),
        ("assignee", {"kind": "assignee", "id": "0001"}),
        ("assignee", {"kind": "assignee", "id": "999999"}),
        ("source", {"kind": "source", "id": None}),
    ],
)
def test_group_keys_are_validated_current_ids(query_case, group_by, key):
    from octop.infra.errors import OctopError

    with pytest.raises(OctopError) as caught:
        query(query_case, group_by=group_by, group_key=key)
    assert caught.value.status == 422 and caught.value.details == {
        "reason": "invalid_query_request"
    }


def test_archived_catalog_groups_appear_only_when_matching_todos_use_them(query_case):
    # Direct historical catalog states preserve refs; all reads are the real current schema.
    with query_case["pool"].transaction() as conn:
        conn.execute(
            "UPDATE project_todo_tags SET archived_at=100 WHERE project_id=?", (query_case["pid"],)
        )
        conn.execute(
            "INSERT INTO project_todo_tags(tag_id,project_id,name,name_key,color,archived_at,created_at,updated_at) "
            "VALUES(?,?,?,?,?,?,?,?)",
            ("unused-archived", query_case["pid"], "unused", "unused", "red", 100, 100, 100),
        )
    result = query(
        query_case, group_by="tag", filters=[{"field": "status", "op": "in", "values": ["todo"]}]
    )
    assert {group["key"]["id"]: group["count"] for group in result["groups"]} == {
        query_case["options"]["tA"]: 2,
        query_case["options"]["tB"]: 1,
        None: 0,
    }


def create_view(case, kind):
    from octop.infra.projects.todo_views import ProjectTodoViewService

    snapshot = case["views"].list_views(case["pid"], user_id=case["owner"])
    result = ProjectTodoViewService(
        SimpleNamespace(project_todo_view_repo=case["views"])
    ).create_view(
        case["pid"],
        actor_user_id=case["owner"],
        expected_revision=snapshot.revision,
        expected_catalog_revision=case["revision"],
        name=kind,
        view_type=kind,
        definition=default_definition(kind),
    )
    return result["item"]["view_id"]


@pytest.mark.parametrize(
    "kind,basis,bucket,expected,unscheduled",
    [
        ("calendar", "due_date", "scheduled", {"B"}, 2),
        ("calendar", "due_date", "unscheduled", {"C", "empty"}, 2),
        ("calendar", "start_date", "scheduled", {"C"}, 2),
        ("calendar", "start_date", "unscheduled", {"B", "empty"}, 2),
        ("gantt", None, "scheduled", {"A", "B", "C"}, 1),
        ("gantt", None, "unscheduled", {"empty"}, 1),
    ],
)
def test_date_buckets_preserve_full_total_and_unscheduled_sidebars(
    query_case, kind, basis, bucket, expected, unscheduled
):
    with query_case["pool"].transaction() as conn:
        for name, start, due in (
            ("A", "2027-04-01", "2027-04-06"),
            ("B", None, "2027-04-02"),
            ("C", "2027-04-04", None),
        ):
            conn.execute(
                "UPDATE project_todos SET start_date=?,due_date=? WHERE todo_id=?",
                (start, due, query_case["rows"][name].todo_id),
            )
    definition = default_definition(kind)
    if basis is not None:
        definition["calendar"]["date_basis"] = basis
    result = query(
        query_case,
        kind=kind,
        view_id=create_view(query_case, kind),
        override_definition=definition,
        window={"start_date": "2027-04-02", "end_date": "2027-04-04"},
        bucket=bucket,
    )
    assert {item["title"] for item in result["items"]} == expected
    assert result["total"] == 4
    assert result["matched_total"] == len(expected)
    assert result["unscheduled_total"] == unscheduled
    assert result["groups"] == []


@pytest.mark.parametrize(
    "field,direction,expected",
    [
        ("title", "asc", ["A", "B", "C", "empty"]),
        ("status", "asc", ["A", "C", "B", "empty"]),
        ("status", "desc", ["empty", "B", "A", "C"]),
        ("assignee", "asc", ["A", "B", "C", "empty"]),
        ("assignee", "desc", ["C", "B", "A", "empty"]),
        ("due_date", "asc", ["A", "B", "empty", "C"]),
        ("due_date", "desc", ["B", "A", "empty", "C"]),
    ],
)
def test_single_sql_sort_has_business_order_and_nulls_last(query_case, field, direction, expected):
    with query_case["pool"].transaction() as conn:
        for name, day in (("A", "2027-04-01"), ("B", "2027-04-03")):
            conn.execute(
                "UPDATE project_todos SET due_date=? WHERE todo_id=?",
                (day, query_case["rows"][name].todo_id),
            )
    result = query(query_case, sort=[{"field": field, "direction": direction}])
    assert [item["title"] for item in result["items"]] == expected


@pytest.mark.parametrize("direction", ["asc", "desc"])
def test_priority_category_order_does_not_reverse(query_case, direction):
    with query_case["pool"].transaction() as conn:
        conn.execute(
            "UPDATE project_todo_priorities SET archived_at=100 WHERE priority_id=?",
            (query_case["options"]["pA"],),
        )
    result = query(query_case, sort=[{"field": "priority", "direction": direction}])
    assert [item["title"] for item in result["items"]] == ["B", "A", "C", "empty"]


def test_220_todos_use_three_sql_keys_and_bounded_complete_keyset_pages(query_case):
    import unicodedata

    rows = list(query_case["rows"].values())
    for index in range(216):
        result = query_case["todos"].create(
            project_id=query_case["pid"],
            creator_user_id=query_case["owner"],
            title=f"task-{index:03}",
            assignee_user_id=(query_case["a"], query_case["b"], None)[index % 3],
            status=("todo", "in_progress", "done")[index % 3],
            ts=100 + index,
        )
        assert result.outcome == "created"
        rows.append(result.row)
    ranks = {query_case["a"]: 0, query_case["b"]: 1, query_case["owner"]: 2}
    expected = [
        row.todo_id
        for row in sorted(
            rows,
            key=lambda row: (
                {"todo": 0, "in_progress": 1, "done": 2}[row.status],
                row.assignee_user_id is None,
                -ranks.get(row.assignee_user_id, 0),
                unicodedata.normalize("NFKC", row.title).casefold(),
                row.todo_id,
            ),
        )
    ]
    seen, cursor, sizes = [], None, []
    for _ in range(6):
        page = query(
            query_case,
            limit=50,
            cursor=cursor,
            sort=[
                {"field": "status", "direction": "asc"},
                {"field": "assignee", "direction": "desc"},
                {"field": "title", "direction": "asc"},
            ],
        )
        assert page["total"] == page["matched_total"] == 220
        seen.extend(item["todo_id"] for item in page["items"])
        sizes.append(len(page["items"]))
        cursor = page["next_cursor"]
        if cursor is None:
            break
    assert seen == expected
    assert sizes == [50, 50, 50, 50, 20]
    assert cursor is None and len(set(seen)) == 220


def test_full_expanding_title_keys_seek_without_putting_keys_into_cursor(query_case):
    import base64
    import json
    import unicodedata

    titles = {"A": "ﷺ" * 200, "B": "ﷺ" * 199 + "b", "C": "ﷺ" * 199 + "a", "empty": "z"}
    with query_case["pool"].transaction() as conn:
        for name, title in titles.items():
            conn.execute(
                "UPDATE project_todos SET title=?,title_search_key=? WHERE todo_id=?",
                (
                    title,
                    unicodedata.normalize("NFKC", title).casefold(),
                    query_case["rows"][name].todo_id,
                ),
            )
    ordered_names = sorted(
        titles, key=lambda name: unicodedata.normalize("NFKC", titles[name]).casefold()
    )
    ids, cursor = [], None
    for _ in range(5):
        page = query(
            query_case, limit=1, cursor=cursor, sort=[{"field": "title", "direction": "asc"}]
        )
        ids.extend(item["todo_id"] for item in page["items"])
        cursor = page["next_cursor"]
        if cursor is None:
            break
        decoded = json.loads(base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4)))
        assert set(decoded) == {"v", "query_fingerprint", "last_todo_id", "last_display_revision"}
        assert len(cursor) <= 2048 and not cursor.endswith("=")
    assert ids == [query_case["rows"][name].todo_id for name in ordered_names]


@pytest.mark.parametrize("change", ["version", "deleted", "filter", "group", "window", "bucket"])
def test_current_anchor_must_still_match_every_query_scope(query_case, change):
    from octop.infra.errors import OctopError

    kwargs = {"limit": 1, "sort": [{"field": "title", "direction": "asc"}]}
    if change == "filter":
        kwargs["filters"] = [{"field": "status", "op": "in", "values": ["todo"]}]
    elif change == "group":
        kwargs.update(group_by="tag", group_key={"kind": "tag", "id": query_case["options"]["tA"]})
    elif change in ("window", "bucket"):
        kind = "calendar" if change == "window" else "gantt"
        with query_case["pool"].transaction() as conn:
            for name, due in (("A", "2027-04-02"), ("B", "2027-04-03")):
                conn.execute(
                    "UPDATE project_todos SET due_date=? WHERE todo_id=?",
                    (due, query_case["rows"][name].todo_id),
                )
        kwargs.update(
            kind=kind,
            view_id=create_view(query_case, kind),
            window={"start_date": "2027-04-02", "end_date": "2027-04-04"},
            bucket="scheduled",
        )
    first = query(query_case, **kwargs)
    assert first["items"][0]["title"] == "A" and first["next_cursor"] is not None
    anchor = first["items"][0]["todo_id"]
    with query_case["pool"].transaction() as conn:
        if change == "group":
            conn.execute(
                "DELETE FROM project_todo_tag_links WHERE todo_id=? AND tag_id=?",
                (anchor, query_case["options"]["tA"]),
            )
        else:
            assignment = {
                "version": "version=version+1",
                "deleted": "deleted_at=1000",
                "filter": "status='done'",
                "window": "due_date='2027-05-01'",
                "bucket": "start_date=NULL,due_date=NULL",
            }[change]
            conn.execute("UPDATE project_todos SET " + assignment + " WHERE todo_id=?", (anchor,))
            conn.execute(
                "UPDATE project_todo_display_state SET revision=revision+1 WHERE todo_id=?",
                (anchor,),
            )
    with pytest.raises(OctopError) as caught:
        query(query_case, cursor=first["next_cursor"], **kwargs)
    assert caught.value.status == 409 and caught.value.details == {"reason": "query_changed"}


@pytest.mark.parametrize("assignee_sort", [False, True])
def test_member_name_changes_only_invalidate_relevant_cursors(query_case, assignee_sort):
    from octop.infra.errors import OctopError

    sort = [{"field": "assignee" if assignee_sort else "title", "direction": "asc"}]
    first = query(query_case, limit=1, sort=sort)
    query_case["users"].set_display_name(query_case["a"], "changed full name")
    if assignee_sort:
        with pytest.raises(OctopError) as caught:
            query(query_case, limit=1, cursor=first["next_cursor"], sort=sort)
        assert caught.value.status == 409 and caught.value.details == {"reason": "query_changed"}
    else:
        assert (
            query(query_case, limit=1, cursor=first["next_cursor"], sort=sort)["items"][0]["title"]
            == "B"
        )


@pytest.mark.parametrize("field", ["assignee", "priority"])
@pytest.mark.parametrize(
    "op,with_reference,with_null,expected",
    [
        ("in", True, False, {"A", "C"}),
        ("in", True, True, {"A", "C", "empty"}),
        ("in", False, True, {"empty"}),
        ("not_in", True, True, {"B"}),
        ("not_in", False, True, {"A", "B", "C"}),
    ],
)
def test_full_nullable_truth_table(query_case, field, op, with_reference, with_null, expected):
    # Give both fields exactly the A/A/B/null truth table, independently of the compiler.
    if field == "assignee":
        with query_case["pool"].transaction() as conn:
            conn.execute(
                "UPDATE project_todos SET assignee_user_id=? WHERE todo_id=?",
                (query_case["a"], query_case["rows"]["C"].todo_id),
            )
        reference = query_case["a"]
    else:
        reference = query_case["options"]["pA"]
    values = ([reference] if with_reference else []) + ([None] if with_null else [])
    page = query(query_case, filters=[{"field": field, "op": op, "values": values}])
    assert {item["title"] for item in page["items"]} == expected
    assert page["total"] == len(expected)


def test_page_materializes_full_c1_dto_and_tags_once_without_any_persistent_change(query_case):
    tables = [
        "project_todo_views",
        "project_todo_view_state",
        "project_todo_catalog_state",
        "project_events",
        "project_todos",
        "project_todo_tag_links",
        "project_members",
        "users",
    ]

    def snapshot():
        with query_case["pool"].connect() as conn:
            return {
                table: [dict(row) for row in conn.execute("SELECT * FROM " + table).fetchall()]
                for table in tables
            }

    before = snapshot()
    sql = []
    query_case["pool"]._conn.set_trace_callback(sql.append)
    try:
        page = query(query_case, limit=2)
    finally:
        query_case["pool"]._conn.set_trace_callback(None)
    assert snapshot() == before
    assert len(page["items"]) == 2
    assert (
        len(
            [
                text
                for text in sql
                if text.startswith("SELECT todo_id,tag_id FROM project_todo_tag_links")
            ]
        )
        == 1
    )
    assert any(text.startswith("SELECT t.*") and text.endswith("LIMIT 3") for text in sql)
    for item in page["items"]:
        assert set(item) == {
            "todo_id",
            "project_id",
            "creator_user_id",
            "assignee_user_id",
            "title",
            "description",
            "description_format",
            "status",
            "version",
            "created_at",
            "updated_at",
            "start_date",
            "due_date",
            "priority_id",
            "tag_ids",
            "catalog_revision",
            "display_revision",
        }
        assert item["tag_ids"] == sorted(item["tag_ids"])
        assert item["catalog_revision"] == query_case["revision"]


def test_full_long_display_keys_keep_suffix_order_and_bounded_cursors(query_case):
    prefix = "ﷺ" * 1000
    for user, suffix in (
        (query_case["owner"], "z"),
        (query_case["a"], "a"),
        (query_case["b"], "b"),
    ):
        query_case["users"].set_display_name(user, prefix + suffix)
    titles, cursor = [], None
    for _ in range(5):
        page = query(
            query_case, limit=1, cursor=cursor, sort=[{"field": "assignee", "direction": "asc"}]
        )
        titles.extend(item["title"] for item in page["items"])
        cursor = page["next_cursor"]
        if cursor is None:
            break
        assert len(cursor) <= 2048
    assert titles == ["A", "B", "C", "empty"]


def test_unrelated_view_collection_revision_does_not_invalidate_target_cursor(query_case):
    first = query(query_case, limit=1, sort=[{"field": "title", "direction": "asc"}])
    with query_case["pool"].transaction() as conn:
        conn.execute(
            "UPDATE project_todo_views SET name='other view',name_key='other view',version=version+1 WHERE project_id=? AND view_id<>?",
            (query_case["pid"], query_case["view_id"]),
        )
        conn.execute(
            "UPDATE project_todo_view_state SET revision=revision+1 WHERE project_id=?",
            (query_case["pid"],),
        )
    second = query(
        query_case,
        limit=1,
        cursor=first["next_cursor"],
        sort=[{"field": "title", "direction": "asc"}],
    )
    assert second["items"][0]["title"] == "B"
    assert second["query_fingerprint"] == first["query_fingerprint"]


@pytest.mark.parametrize(
    "op,expected", [("contains", set()), ("not_contains", {"A", "B", "C", "empty"})]
)
def test_literal_nul_is_not_a_like_pattern_terminator(query_case, op, expected):
    page = query(query_case, filters=[{"field": "title", "op": op, "value": "\x00"}])
    assert {item["title"] for item in page["items"]} == expected
