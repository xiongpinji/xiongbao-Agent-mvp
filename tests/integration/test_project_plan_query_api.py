"""The plan query contract on the real authenticated ASGI application."""

from __future__ import annotations

import base64
import json
import unicodedata
from typing import Any

from octop.infra.projects.plan_definition import default_definition
from tests.integration.test_project_todo_catalog_api import _catalog, _option
from tests.integration.test_project_todo_views_api import _context, _raw_state
from tests.integration.test_project_todos_api import _TODO_KEYS, _add_user, _base, _create_todo

QUERY_KEYS = {
    "items",
    "next_cursor",
    "total",
    "matched_total",
    "unscheduled_total",
    "groups",
    "view_id",
    "view_version",
    "catalog_revision",
    "server_today",
    "server_timezone",
    "query_fingerprint",
}


def _request(ctx: dict[str, Any], **values: Any) -> dict[str, Any]:
    view = values.pop("view", ctx["views"]["items"][0])
    return {
        "view_id": view["view_id"],
        "expected_view_version": view["version"],
        "expected_catalog_revision": ctx["catalog"]["revision"],
        **values,
    }


def _definition(kind: str = "table", **values: Any) -> dict[str, Any]:
    return {**default_definition(kind), **values}


async def _query(
    ctx: dict[str, Any], *, data: Any = None, auth: Any = None, status: int = 200, **values: Any
) -> Any:
    response = await ctx["client"].post(
        ctx["plan"] + "/query",
        headers=auth or ctx["owner_auth"],
        json=_request(ctx, **values) if data is None else data,
    )
    assert response.status_code == status, response.text
    return response


async def _ok(ctx: dict[str, Any], **values: Any) -> dict[str, Any]:
    body = (await _query(ctx, **values)).json()
    assert set(body) == QUERY_KEYS
    assert all(set(item) == _TODO_KEYS for item in body["items"])
    assert type(body["total"]) is int and type(body["matched_total"]) is int
    assert body["total"] >= 0 and body["matched_total"] >= 0
    assert body["server_timezone"] == ctx["srv"].services.config.default_timezone
    assert len(body["server_today"]) == 10
    assert len(body["query_fingerprint"]) == 64
    assert all(char in "0123456789abcdef" for char in body["query_fingerprint"])
    return body


async def _new_todo(ctx: dict[str, Any], **fields: Any) -> dict[str, Any]:
    response = await ctx["client"].post(
        f"/api/projects/{ctx['pid']}/todos", headers=ctx["owner_auth"], json=fields
    )
    assert response.status_code == 201, response.text
    return response.json()


async def _create_view(ctx: dict[str, Any], kind: str, **values: Any) -> dict[str, Any]:
    current = await ctx["client"].get(ctx["plan"] + "/views", headers=ctx["owner_auth"])
    assert current.status_code == 200, current.text
    response = await ctx["client"].post(
        ctx["plan"] + "/views",
        headers=ctx["owner_auth"],
        json={
            "expected_revision": current.json()["revision"],
            "expected_catalog_revision": ctx["catalog"]["revision"],
            "name": kind,
            "type": kind,
            "definition": _definition(kind, **values),
        },
    )
    assert response.status_code == 201, response.text
    return response.json()["item"]


def _ids(body: dict[str, Any]) -> set[str]:
    identifiers = [item["todo_id"] for item in body["items"]]
    assert len(set(identifiers)) == len(identifiers)
    return set(identifiers)


def _cursor(value: str) -> dict[str, Any]:
    assert len(value.encode("utf-8")) <= 2048 and "=" not in value
    result = json.loads(base64.urlsafe_b64decode(value + "=" * (-len(value) % 4)))
    assert set(result) == {"v", "query_fingerprint", "last_todo_id", "last_version"}
    assert type(result["v"]) is int and result["v"] == 1
    assert type(result["last_version"]) is int and result["last_version"] >= 1
    return result


def _reason(response: Any, reason: str) -> dict[str, Any]:
    error = response.json()["error"]
    assert error["details"]["reason"] == reason, response.text
    assert isinstance(error["message"], str) and error["message"]
    assert all(
        value not in response.text for value in ('"loc"', '"input"', '"ctx"', "psycopg", "sqlite3")
    )
    return error["details"]


async def test_real_query_route_after_catalog_and_view_controls(
    env_with_provider: Any, record_property: Any
) -> None:
    """A mounted-query RED requires working auth, project, C1 and Q3 first."""
    ctx = await _base(env_with_provider)
    client, auth = ctx["client"], ctx["owner_auth"]
    plan = f"/api/projects/{ctx['pid']}/plan"
    todo = await _create_todo(ctx, title="Q4 合法查询控制待办")

    catalog_response = await client.get(plan + "/catalog", headers=auth)
    assert catalog_response.status_code == 200, catalog_response.text
    catalog = catalog_response.json()
    assert catalog["project_id"] == ctx["pid"]
    assert type(catalog["revision"]) is int and catalog["revision"] == 1
    record_property("q4_c1_catalog_status", catalog_response.status_code)

    view_response = await client.get(plan + "/views", headers=auth)
    assert view_response.status_code == 200, view_response.text
    views = view_response.json()
    view = next(item for item in views["items"] if item["view_id"] == views["default_view_id"])
    assert view["project_id"] == ctx["pid"] and view["type"] == "table"
    assert type(view["version"]) is int and view["version"] == 1
    record_property("q4_q3_views_status", view_response.status_code)
    record_property("q4_query_real_view_id", view["view_id"])

    old_get = await client.get(f"/api/projects/{ctx['pid']}/todos", headers=auth)
    assert old_get.status_code == 200, old_get.text
    assert [item["todo_id"] for item in old_get.json()["items"]] == [todo["todo_id"]]
    record_property("q4_c1_old_get_status", old_get.status_code)

    response = await client.post(
        plan + "/query",
        headers=auth,
        json={
            "view_id": view["view_id"],
            "expected_view_version": view["version"],
            "expected_catalog_revision": catalog["revision"],
        },
    )
    record_property("q4_query_status", response.status_code)
    record_property("q4_query_response", response.text)
    # This is the intended first failure while root has not mounted POST query.
    assert response.status_code == 200, response.text
    body = response.json()
    assert [item["todo_id"] for item in body["items"]] == [todo["todo_id"]]
    assert set(body["items"][0]) == _TODO_KEYS
    assert body["total"] == body["matched_total"] == 1
    assert body["unscheduled_total"] is None
    assert body["next_cursor"] is None


async def test_query_reads_current_roles_revocation_and_same_project_active_view(
    env_with_provider: Any,
) -> None:
    ctx = await _context(env_with_provider)
    todo = await _create_todo(ctx, title="PRIVATE_Q4_ACL_TITLE")
    project_admin, _ = await _add_user(ctx, "q4_project_admin", "admin")
    before = _raw_state(ctx)
    for auth in (ctx["owner_auth"], project_admin, ctx["member_auth"]):
        result = await _ok(ctx, auth=auth)
        assert _ids(result) == {todo["todo_id"]}
    no_auth = await ctx["client"].post(ctx["plan"] + "/query", json=_request(ctx))
    assert no_auth.status_code == 401, no_auth.text
    for auth in (ctx["outsider_auth"], ctx["admin_auth"]):
        denied = await _query(ctx, auth=auth, status=404)
        assert denied.json()["error"]["code"] == "NOT_FOUND"
        assert "PRIVATE_Q4_ACL_TITLE" not in denied.text

    other_response = await ctx["client"].post(
        "/api/projects", headers=ctx["owner_auth"], json={"name": "query other"}
    )
    assert other_response.status_code == 201, other_response.text
    other_plan = f"/api/projects/{other_response.json()['project_id']}/plan"
    other_views = await ctx["client"].get(other_plan + "/views", headers=ctx["owner_auth"])
    assert other_views.status_code == 200, other_views.text
    cross = await _query(ctx, view=other_views.json()["items"][0], status=404)
    assert "PRIVATE_Q4_ACL_TITLE" not in cross.text
    missing = await _query(ctx, view_id="01ARZ3NDEKTSV4RRFFQ69G5FAV", status=404)
    assert missing.json()["error"]["code"] == "NOT_FOUND"
    assert _raw_state(ctx) == before

    with ctx["srv"].services.db.transaction() as conn:
        conn.execute("UPDATE project_spaces SET archived=1 WHERE project_id=?", (ctx["pid"],))
    assert _ids(await _ok(ctx, auth=ctx["member_auth"])) == {todo["todo_id"]}
    with ctx["srv"].services.db.transaction() as conn:
        conn.execute("UPDATE project_spaces SET archived=0 WHERE project_id=?", (ctx["pid"],))
    removed = ctx["srv"].services.project_repo.remove_member(
        project_id=ctx["pid"], user_id=ctx["member_uid"], actor_user_id=ctx["owner_uid"]
    )
    assert removed.outcome == "removed"
    denied = await _query(ctx, auth=ctx["member_auth"], status=404)
    assert "PRIVATE_Q4_ACL_TITLE" not in denied.text
    table = ctx["views"]["items"][0]
    archive = await ctx["client"].post(
        ctx["plan"] + "/views/" + table["view_id"] + "/archive",
        headers=ctx["owner_auth"],
        json={"expected_version": table["version"]},
    )
    assert archive.status_code == 200, archive.text
    assert (await _query(ctx, status=404)).json()["error"]["code"] == "NOT_FOUND"


async def test_query_request_is_strict_and_private_validation_values_are_never_echoed(
    env_with_provider: Any,
) -> None:
    ctx = await _context(env_with_provider)
    before = _raw_state(ctx)
    invalid = [{}, [], None, "PRIVATE_Q4_BODY"]
    base = _request(ctx)
    for field in ("view_id", "expected_view_version", "expected_catalog_revision"):
        invalid.append({key: value for key, value in base.items() if key != field})
    for field in ("expected_view_version", "expected_catalog_revision", "limit"):
        for value in (True, "1", 1.0, 0, -1, None):
            invalid.append({**base, field: value})
    invalid.extend(
        [
            {**base, "limit": 101},
            {**base, "view_id": 1},
            {**base, "view_id": ""},
            {**base, "PRIVATE_Q4_FIELD": "PRIVATE_Q4_TOKEN"},
            {**base, "cursor": 1},
            {**base, "cursor": "="},
            {**base, "cursor": "x" * 2049},
            {**base, "group_key": {"kind": "status", "id": 1}},
            {**base, "group_key": {"kind": "sql_column", "id": "PRIVATE_Q4_COLUMN"}},
            {**base, "group_key": {"kind": "status"}},
            {**base, "override_definition": {"PRIVATE_Q4_SQL": "SELECT * FROM users"}},
        ]
    )
    for definition in (
        _definition(
            filters=[
                {
                    "field": "title",
                    "op": "contains",
                    "value": "PRIVATE_Q4_LITERAL",
                    "PRIVATE_Q4_EXTRA": "PRIVATE_Q4_TOKEN",
                }
            ]
        ),
        _definition(
            filters=[{"field": "source", "op": "in", "values": ["PRIVATE_Q4_UNSUPPORTED"]}]
        ),
        _definition(filters=[{"field": "assignee", "op": "in", "values": [True]}]),
        _definition(filters=[{"field": "assignee", "op": "in", "values": [2**100]}]),
        _definition(filters=[{"field": "title", "op": "contains", "value": "x"}] * 13),
        _definition(filters=[{"field": "tags", "op": "is_empty", "values": []}]),
        _definition(sort=[{"field": "todo_id", "direction": "asc"}]),
        _definition(fields=["status", "title"]),
    ):
        invalid.append({**base, "override_definition": definition})
    for body in invalid:
        response = await ctx["client"].post(
            ctx["plan"] + "/query", headers=ctx["owner_auth"], json=body
        )
        assert response.status_code == 422, (body, response.text)
        _reason(response, "invalid_query_request")
        assert "PRIVATE_Q4_" not in response.text and "SELECT *" not in response.text
    malformed = await ctx["client"].post(
        ctx["plan"] + "/query",
        headers={**ctx["owner_auth"], "Content-Type": "application/json"},
        content='{"PRIVATE_Q4_BROKEN":',
    )
    assert malformed.status_code == 422, malformed.text
    _reason(malformed, "invalid_query_request")
    assert "PRIVATE_Q4_BROKEN" not in malformed.text
    assert _raw_state(ctx) == before


async def test_member_override_returns_full_c1_dto_and_does_not_save_shared_state(
    env_with_provider: Any,
) -> None:
    ctx = await _context(env_with_provider)
    tag = (await _option(ctx, "tags", "query tag"))["item"]["tag_id"]
    ctx["catalog"] = await _catalog(ctx)
    priority = ctx["catalog"]["priorities"][0]["priority_id"]
    first = await _new_todo(
        ctx,
        title="keep row",
        description="## PRIVATE_Q4_DESCRIPTION",
        description_format="markdown",
        status="in_progress",
        assignee_user_id=ctx["member_uid"],
        priority_id=priority,
        tag_ids=[tag],
        expected_catalog_revision=ctx["catalog"]["revision"],
        start_date="9999-01-01",
        due_date="9999-01-02",
    )
    await _new_todo(ctx, title="other row")
    before = _raw_state(ctx)
    definition = _definition(
        fields=["title"], filters=[{"field": "title", "op": "contains", "value": "keep"}]
    )
    result = await _ok(ctx, auth=ctx["member_auth"], override_definition=definition)
    assert result["items"] == [first]
    assert result["total"] == result["matched_total"] == 1
    assert result["groups"] == [] and result["unscheduled_total"] is None
    assert result["view_version"] == ctx["views"]["items"][0]["version"]
    assert result["catalog_revision"] == ctx["catalog"]["revision"]
    assert _raw_state(ctx) == before
    shared = await ctx["client"].get(ctx["plan"] + "/views", headers=ctx["member_auth"])
    assert shared.status_code == 200 and shared.json() == ctx["views"]
    old = await ctx["client"].get(
        f"/api/projects/{ctx['pid']}/todos?limit=1&offset=1", headers=ctx["member_auth"]
    )
    assert old.status_code == 200, old.text
    assert set(old.json()) == {"items", "limit", "offset", "has_more"}
    assert old.json()["limit"] == 1 and old.json()["offset"] == 1
    assert len(old.json()["items"]) == 1 and set(old.json()["items"][0]) == _TODO_KEYS


async def test_nullable_and_tag_filters_use_full_complements_and_distinct_items(
    env_with_provider: Any,
) -> None:
    ctx = await _context(env_with_provider)
    t1 = (await _option(ctx, "tags", "query T1"))["item"]["tag_id"]
    t2 = (await _option(ctx, "tags", "query T2"))["item"]["tag_id"]
    ctx["catalog"] = await _catalog(ctx)
    p1, p2 = [item["priority_id"] for item in ctx["catalog"]["priorities"][:2]]
    rows = [
        await _new_todo(
            ctx,
            title="A",
            assignee_user_id=ctx["owner_uid"],
            priority_id=p1,
            tag_ids=[t1],
            expected_catalog_revision=3,
        ),
        await _new_todo(
            ctx,
            title="B",
            assignee_user_id=ctx["member_uid"],
            priority_id=p2,
            tag_ids=[t2],
            expected_catalog_revision=3,
        ),
        await _new_todo(
            ctx, title="null", priority_id=None, tag_ids=[], expected_catalog_revision=3
        ),
        await _new_todo(
            ctx, title="both", priority_id=None, tag_ids=[t1, t2], expected_catalog_revision=3
        ),
    ]
    all_ids = {row["todo_id"] for row in rows}
    # Both identifiers and explicit null participate in exact independent truth sets.
    for field, left, right in (
        ("assignee", ctx["owner_uid"], ctx["member_uid"]),
        ("priority", p1, p2),
    ):
        nulls = {rows[2]["todo_id"], rows[3]["todo_id"]}
        for values, expected in (
            ([left], {rows[0]["todo_id"]}),
            ([right], {rows[1]["todo_id"]}),
            ([None], nulls),
            ([left, None], {rows[0]["todo_id"]} | nulls),
        ):
            for operation, wanted in (("in", expected), ("not_in", all_ids - expected)):
                result = await _ok(
                    ctx,
                    override_definition=_definition(
                        filters=[{"field": field, "op": operation, "values": values}]
                    ),
                )
                assert _ids(result) == wanted, (field, operation, values, result)
                assert result["total"] == result["matched_total"] == len(wanted)
    cases = [
        ({"field": "tags", "op": "any", "values": [t1, t2]}, {0, 1, 3}),
        ({"field": "tags", "op": "all", "values": [t1, t2]}, {3}),
        ({"field": "tags", "op": "none_of", "values": [t1]}, {1, 2}),
        ({"field": "tags", "op": "is_empty"}, {2}),
        ({"field": "tags", "op": "not_empty"}, {0, 1, 3}),
    ]
    for clause, indices in cases:
        result = await _ok(ctx, override_definition=_definition(filters=[clause]))
        assert _ids(result) == {rows[index]["todo_id"] for index in indices}
        assert result["total"] == result["matched_total"] == len(indices)
    result = await _ok(
        ctx,
        override_definition=_definition(
            filters=[{"field": "title", "op": "contains", "value": "A"}] * 12
        ),
    )
    assert _ids(result) == {rows[0]["todo_id"]}


async def test_title_literals_full_normalization_and_old_get_search_are_compatible(
    env_with_provider: Any,
) -> None:
    ctx = await _context(env_with_provider)
    titles = [
        "100% ready",
        "under_score",
        "back\\slash",
        "ordinary",
        "prefix ＡＢＣ Straße suffix",
        "abc",
        "percentX ready",
    ]
    rows = [await _new_todo(ctx, title=title) for title in titles]
    for literal, indices in (
        ("%", {0}),
        ("_", {1}),
        ("\\", {2}),
        ("abc strasse", {4}),
        (" abc", {4}),
        ("abc ", {4}),
    ):
        clause = {"field": "title", "op": "contains", "value": literal}
        expected = {rows[index]["todo_id"] for index in indices}
        result = await _ok(ctx, override_definition=_definition(filters=[clause]))
        assert _ids(result) == expected, (literal, result)
        complement = await _ok(
            ctx, override_definition=_definition(filters=[{**clause, "op": "not_contains"}])
        )
        assert _ids(complement) == {row["todo_id"] for row in rows} - expected
        old = await ctx["client"].get(
            f"/api/projects/{ctx['pid']}/todos",
            headers=ctx["owner_auth"],
            params={"q": literal, "limit": 100},
        )
        assert old.status_code == 200, old.text
        # The legacy GET trims boundary spaces; the new condition preserves them.
        old_expected = (
            {rows[index]["todo_id"] for index in {4, 5}}
            if literal in {" abc", "abc "}
            else expected
        )
        assert {item["todo_id"] for item in old.json()["items"]} == old_expected

    for operation, expected in (
        ("contains", set()),
        ("not_contains", {row["todo_id"] for row in rows}),
    ):
        result = await _ok(
            ctx,
            override_definition=_definition(
                filters=[{"field": "title", "op": operation, "value": "\u0000"}]
            ),
        )
        assert _ids(result) == expected and result["total"] == len(expected)


async def test_220_real_todos_page_once_with_server_totals_and_compact_cursor(
    env_with_provider: Any,
) -> None:
    ctx = await _context(env_with_provider)
    tag_a = (await _option(ctx, "tags", "matrix A"))["item"]["tag_id"]
    tag_b = (await _option(ctx, "tags", "matrix B"))["item"]["tag_id"]
    ctx["catalog"] = await _catalog(ctx)
    priorities = [item["priority_id"] for item in ctx["catalog"]["priorities"][:2]]
    rows = []
    for index in range(220):
        start, due = [
            ("9999-01-01", "9999-01-03"),
            ("9999-01-02", None),
            (None, "9999-01-03"),
            (None, None),
        ][index % 4]
        rows.append(
            await _new_todo(
                ctx,
                title=f"matrix {index:03d}",
                status=("todo", "in_progress", "done")[index % 3],
                assignee_user_id=(ctx["owner_uid"], ctx["member_uid"], None)[index % 3],
                priority_id=(*priorities, None)[index % 3],
                tag_ids=([], [tag_a], [tag_b], [tag_a, tag_b])[index % 4],
                start_date=start,
                due_date=due,
                expected_catalog_revision=ctx["catalog"]["revision"],
            )
        )
    archived = await ctx["client"].post(
        ctx["plan"] + "/priorities/" + priorities[1] + "/archive",
        headers=ctx["owner_auth"],
        json={"expected_revision": ctx["catalog"]["revision"]},
    )
    assert archived.status_code == 200, archived.text
    ctx["catalog"] = await _catalog(ctx)
    assert len(_ids(await _ok(ctx))) == 50
    max_page = await _ok(ctx, limit=100)
    assert len(max_page["items"]) == 100 and max_page["next_cursor"] is not None
    definition = _definition(
        sort=[
            {"field": "status", "direction": "asc"},
            {"field": "title", "direction": "desc"},
            {"field": "due_date", "direction": "asc"},
        ]
    )
    wanted = []
    for status in ("todo", "in_progress", "done"):
        wanted.extend(
            row["todo_id"]
            for row in sorted(
                (row for row in rows if row["status"] == status),
                key=lambda row: row["title"],
                reverse=True,
            )
        )
    observed: list[str] = []
    cursor = None
    for page in range(5):
        options = {"override_definition": definition, "limit": 73}
        if cursor is not None:
            options["cursor"] = cursor
        result = await _ok(ctx, **options)
        assert result["total"] == result["matched_total"] == 220
        assert result["unscheduled_total"] is None and result["groups"] == []
        assert len(result["items"]) <= 73
        observed.extend(item["todo_id"] for item in result["items"])
        cursor = result["next_cursor"]
        if cursor is None:
            assert page == 3
            break
        decoded = _cursor(cursor)
        assert decoded["last_todo_id"] == result["items"][-1]["todo_id"]
        assert decoded["query_fingerprint"] == result["query_fingerprint"]
    else:
        raise AssertionError("bounded query did not reach its final page")
    assert observed == wanted and len(set(observed)) == 220
    old = await ctx["client"].get(
        f"/api/projects/{ctx['pid']}/todos?limit=20&offset=200", headers=ctx["owner_auth"]
    )
    assert old.status_code == 200, old.text
    assert set(old.json()) == {"items", "limit", "offset", "has_more"}
    assert len(old.json()["items"]) == 20 and old.json()["has_more"] is False


async def test_200_fdfa_title_and_long_common_prefix_keep_full_order_and_short_cursor(
    env_with_provider: Any,
) -> None:
    ctx = await _context(env_with_provider)
    titles = ["\ufdfa" * 200, "\ufdfa" * 199 + "z", "\ufdfa" * 199 + "a", "plain"]
    rows = [await _new_todo(ctx, title=title) for title in titles]
    definition = _definition(sort=[{"field": "title", "direction": "asc"}])
    wanted = [
        row["todo_id"]
        for row in sorted(
            rows,
            key=lambda row: (
                unicodedata.normalize("NFKC", row["title"]).casefold(),
                row["todo_id"],
            ),
        )
    ]
    ids: list[str] = []
    cursor = None
    for _ in range(4):
        options = {"override_definition": definition, "limit": 1}
        if cursor is not None:
            options["cursor"] = cursor
        result = await _ok(ctx, **options)
        ids.extend(item["todo_id"] for item in result["items"])
        cursor = result["next_cursor"]
        if cursor is None:
            break
        decoded = _cursor(cursor)
        assert "\ufdfa" not in json.dumps(decoded, ensure_ascii=False)
    assert cursor is None and ids == wanted


async def test_all_group_counts_are_full_project_counts_and_expansion_only_limits_items(
    env_with_provider: Any,
) -> None:
    ctx = await _context(env_with_provider)
    tags = [
        (await _option(ctx, "tags", name))["item"]["tag_id"]
        for name in ("group T1", "group T2", "group unused")
    ]
    ctx["catalog"] = await _catalog(ctx)
    priorities = [item["priority_id"] for item in ctx["catalog"]["priorities"]]
    rows = [
        await _new_todo(
            ctx,
            title="group A",
            status="todo",
            assignee_user_id=ctx["owner_uid"],
            priority_id=priorities[0],
            tag_ids=[tags[0]],
            expected_catalog_revision=4,
        ),
        await _new_todo(
            ctx,
            title="group B",
            status="in_progress",
            assignee_user_id=ctx["member_uid"],
            priority_id=priorities[1],
            tag_ids=[tags[1]],
            expected_catalog_revision=4,
        ),
        await _new_todo(ctx, title="group null", status="done"),
        await _new_todo(
            ctx, title="group both", status="todo", tag_ids=tags[:2], expected_catalog_revision=4
        ),
    ]
    archived = await ctx["client"].post(
        ctx["plan"] + "/priorities/" + priorities[1] + "/archive",
        headers=ctx["owner_auth"],
        json={"expected_revision": 4},
    )
    assert archived.status_code == 200, archived.text
    ctx["catalog"] = await _catalog(ctx)
    counts = {
        "status": {"todo": 2, "in_progress": 1, "done": 1},
        "assignee": {str(ctx["owner_uid"]): 1, str(ctx["member_uid"]): 1, None: 2},
        "priority": {
            priorities[0]: 1,
            priorities[1]: 1,
            priorities[2]: 0,
            priorities[3]: 0,
            None: 2,
        },
        "tag": {tags[0]: 2, tags[1]: 2, tags[2]: 0, None: 1},
        "source": {"manual": 4},
    }
    for kind, expected in counts.items():
        definition = _definition("board", group_by=kind)
        result = await _ok(
            ctx, view=ctx["views"]["items"][1], override_definition=definition, limit=1
        )
        actual = {entry["key"]["id"]: entry["count"] for entry in result["groups"]}
        assert all(entry["key"]["kind"] == kind for entry in result["groups"])
        assert actual == expected, (kind, result)
        assert result["total"] == result["matched_total"] == 4
        assert len(result["items"]) == 1 and result["next_cursor"] is not None
    definition = _definition("board", group_by="tag")
    all_groups = await _ok(ctx, view=ctx["views"]["items"][1], override_definition=definition)
    assert sum(entry["count"] for entry in all_groups["groups"]) == 5 > all_groups["total"]
    for tag, indices in ((tags[0], {0, 3}), (tags[1], {1, 3}), (None, {2})):
        expanded = await _ok(
            ctx,
            view=ctx["views"]["items"][1],
            override_definition=definition,
            group_key={"kind": "tag", "id": tag},
        )
        assert _ids(expanded) == {rows[index]["todo_id"] for index in indices}
        assert expanded["total"] == expanded["matched_total"] == 4
        assert expanded["groups"] == all_groups["groups"]
    for key in (
        {"kind": "status", "id": "todo"},
        {"kind": "tag", "id": "01ARZ3NDEKTSV4RRFFQ69G5FAV"},
    ):
        rejected = await _query(
            ctx,
            view=ctx["views"]["items"][1],
            override_definition=definition,
            group_key=key,
            status=422,
        )
        _reason(rejected, "invalid_query_request")


async def test_calendar_and_gantt_window_buckets_preserve_total_and_unscheduled_counts(
    env_with_provider: Any,
) -> None:
    ctx = await _context(env_with_provider)
    dates = [
        ("9999-01-01", "9999-01-10"),
        ("9999-01-05", None),
        (None, "9999-01-07"),
        (None, None),
        ("9999-01-06", "9999-01-06"),
        ("9999-01-08", "9999-01-09"),
    ]
    rows = [
        await _new_todo(ctx, title=f"window {index}", start_date=start, due_date=due)
        for index, (start, due) in enumerate(dates)
    ]
    calendar = await _create_view(ctx, "calendar")
    gantt = await _create_view(ctx, "gantt")
    window = {"start_date": "9999-01-05", "end_date": "9999-01-06"}
    scenarios = [
        (
            calendar,
            _definition("calendar", calendar={"date_basis": "due_date", "mode": "month"}),
            {4},
            {1, 3},
        ),
        (
            calendar,
            _definition("calendar", calendar={"date_basis": "start_date", "mode": "week"}),
            {1, 4},
            {2, 3},
        ),
        (gantt, _definition("gantt"), {0, 1, 4}, {3}),
    ]
    for view, definition, scheduled, unscheduled in scenarios:
        for bucket, expected in (("scheduled", scheduled), ("unscheduled", unscheduled)):
            result = await _ok(
                ctx, view=view, override_definition=definition, window=window, bucket=bucket
            )
            assert _ids(result) == {rows[index]["todo_id"] for index in expected}, (
                view["type"],
                bucket,
                result,
            )
            assert result["total"] == 6 and result["matched_total"] == len(expected)
            assert result["unscheduled_total"] == len(unscheduled) and result["groups"] == []
    for valid in (
        {"start_date": "1900-01-01", "end_date": "1900-01-01"},
        {"start_date": "9999-12-31", "end_date": "9999-12-31"},
        {"start_date": "2024-01-01", "end_date": "2024-12-31"},
    ):
        result = await _ok(ctx, view=gantt, window=valid, bucket="scheduled")
        assert (
            result["total"] == 6
            and result["matched_total"] == 0
            and result["unscheduled_total"] == 1
        )
    bad_windows = [
        {"start_date": "1899-12-31", "end_date": "1900-01-01"},
        {"start_date": "2024-02-30", "end_date": "2024-03-01"},
        {"start_date": "２０２４-01-01", "end_date": "2024-01-02"},
        {"start_date": "2024-1-01", "end_date": "2024-01-02"},
        {"start_date": "2024-01-02", "end_date": "2024-01-01"},
        {"start_date": "2024-01-01", "end_date": "2025-01-01"},
        {"start_date": "9999-12-31", "end_date": "10000-01-01"},
        {**window, "PRIVATE_Q4_EXTRA": "PRIVATE_Q4_SECRET"},
    ]
    for value in bad_windows:
        rejected = await _query(ctx, view=gantt, window=value, bucket="scheduled", status=422)
        _reason(rejected, "invalid_query_request")
        assert "PRIVATE_Q4_" not in rejected.text
    for values in (
        {},
        {"window": window},
        {"bucket": "scheduled"},
        {"window": window, "bucket": "unknown"},
        {"window": window, "bucket": "scheduled", "group_key": {"kind": "status", "id": "todo"}},
    ):
        rejected = await _query(ctx, view=calendar, status=422, **values)
        _reason(rejected, "invalid_query_request")
    for view in ctx["views"]["items"]:
        for values in (
            {"window": window},
            {"bucket": "scheduled"},
            {"window": window, "bucket": "scheduled"},
        ):
            _reason(await _query(ctx, view=view, status=422, **values), "invalid_query_request")
    bad_type = await _query(
        ctx,
        view=gantt,
        override_definition=_definition("calendar"),
        window=window,
        bucket="scheduled",
        status=422,
    )
    _reason(bad_type, "invalid_query_request")


async def test_cursor_target_versions_catalog_and_unrelated_view_collection_are_distinct(
    env_with_provider: Any,
) -> None:
    ctx = await _context(env_with_provider)
    for title in ("A", "B", "C"):
        await _new_todo(ctx, title=title)
    definition = _definition(sort=[{"field": "title", "direction": "asc"}])
    first = await _ok(ctx, override_definition=definition, limit=1)
    old_cursor = first["next_cursor"]
    assert old_cursor is not None
    table, board = ctx["views"]["items"]
    changed = await ctx["client"].patch(
        ctx["plan"] + "/views/" + board["view_id"],
        headers=ctx["owner_auth"],
        json={"expected_version": 1, "name": "unrelated changed"},
    )
    assert changed.status_code == 200, changed.text
    second = await _ok(ctx, override_definition=definition, limit=1, cursor=old_cursor)
    assert second["items"][0]["title"] == "B"
    assert second["query_fingerprint"] == first["query_fingerprint"]
    changed = await ctx["client"].patch(
        ctx["plan"] + "/views/" + table["view_id"],
        headers=ctx["owner_auth"],
        json={"expected_version": 1, "name": "target changed"},
    )
    assert changed.status_code == 200, changed.text
    _reason(await _query(ctx, override_definition=definition, status=409), "query_changed")
    ctx["views"]["items"][0] = changed.json()["item"]
    _reason(
        await _query(ctx, override_definition=definition, limit=1, cursor=old_cursor, status=409),
        "query_changed",
    )
    fresh = await _ok(ctx, override_definition=definition, limit=1)
    assert fresh["query_fingerprint"] != first["query_fingerprint"]
    await _option(ctx, "tags", "catalog changed")
    _reason(await _query(ctx, override_definition=definition, status=409), "query_changed")
    ctx["catalog"] = await _catalog(ctx)
    _reason(
        await _query(
            ctx, override_definition=definition, limit=1, cursor=fresh["next_cursor"], status=409
        ),
        "query_changed",
    )
    fresh = await _ok(ctx, override_definition=definition, limit=1)
    alternative = _definition(sort=[{"field": "title", "direction": "desc"}])
    _reason(
        await _query(ctx, override_definition=alternative, cursor=fresh["next_cursor"], status=409),
        "query_changed",
    )


async def test_anchor_revalidation_includes_version_deletion_filters_group_and_window(
    env_with_provider: Any,
) -> None:
    ctx = await _context(env_with_provider)
    calendar = await _create_view(ctx, "calendar")
    for mutation in ("version", "delete", "filter", "group", "window"):
        first = await _new_todo(
            ctx, title=f"anchor {mutation} A", start_date="9999-01-01", due_date="9999-01-02"
        )
        await _new_todo(
            ctx, title=f"anchor {mutation} B", start_date="9999-01-01", due_date="9999-01-02"
        )
        kind = "calendar" if mutation == "window" else "table"
        definition = _definition(
            kind,
            filters=[{"field": "title", "op": "contains", "value": f"anchor {mutation}"}],
            sort=[{"field": "title", "direction": "asc"}],
        )
        options: dict[str, Any] = {"override_definition": definition, "limit": 1}
        if mutation == "group":
            definition["group_by"] = "status"
            options["group_key"] = {"kind": "status", "id": "todo"}
        if mutation == "window":
            options.update(
                view=calendar,
                window={"start_date": "9999-01-01", "end_date": "9999-01-03"},
                bucket="scheduled",
            )
        page = await _ok(ctx, **options)
        assert [item["todo_id"] for item in page["items"]] == [first["todo_id"]]
        cursor = page["next_cursor"]
        assert cursor is not None
        path = f"/api/projects/{ctx['pid']}/todos/{first['todo_id']}"
        if mutation == "delete":
            changed = await ctx["client"].delete(
                path + "?expected_version=1", headers=ctx["owner_auth"]
            )
            assert changed.status_code == 204, changed.text
        else:
            field = {
                "version": {"description": "anchor changed"},
                "filter": {"title": "no longer matching"},
                "group": {"status": "in_progress"},
                "window": {"due_date": "9999-02-01"},
            }[mutation]
            changed = await ctx["client"].patch(
                path, headers=ctx["owner_auth"], json={"expected_version": 1, **field}
            )
            assert changed.status_code == 200, changed.text
        _reason(await _query(ctx, **options, cursor=cursor, status=409), "query_changed")
        if mutation in ("filter", "group", "window"):
            # A current row version alone cannot bypass its complete query predicate.
            decoded = _cursor(cursor)
            decoded["last_version"] = changed.json()["version"]
            forged = (
                base64.urlsafe_b64encode(json.dumps(decoded, separators=(",", ":")).encode())
                .decode()
                .rstrip("=")
            )
            _reason(await _query(ctx, **options, cursor=forged, status=409), "query_changed")


async def test_saved_lost_member_filter_is_readable_and_explicit_member_override_repairs_query_only(
    env_with_provider: Any,
) -> None:
    ctx = await _context(env_with_provider)
    survivor_auth, _ = await _add_user(ctx, "q4_surviving_member")
    await _create_todo(ctx, title="PRIVATE_Q4_SAVED_VIEW_ROW")
    definition = _definition(
        filters=[{"field": "assignee", "op": "in", "values": [ctx["member_uid"]]}]
    )
    view = await _create_view(ctx, "list", filters=definition["filters"])
    removed = ctx["srv"].services.project_repo.remove_member(
        project_id=ctx["pid"], user_id=ctx["member_uid"], actor_user_id=ctx["owner_uid"]
    )
    assert removed.outcome == "removed"
    before = _raw_state(ctx)
    for auth in (ctx["owner_auth"], survivor_auth):
        failed = await _query(ctx, view=view, auth=auth, status=409)
        assert _reason(failed, "filter_reference_unavailable") == {
            "reason": "filter_reference_unavailable",
            "condition_indices": [0],
        }
        assert (
            str(ctx["member_uid"]) not in failed.text
            and "PRIVATE_Q4_SAVED_VIEW_ROW" not in failed.text
        )
    denied = await _query(ctx, view=view, auth=ctx["outsider_auth"], status=404)
    assert "condition_indices" not in denied.text and "PRIVATE_Q4_" not in denied.text
    explicit_invalid = await _query(
        ctx,
        view=view,
        auth=survivor_auth,
        override_definition=_definition("list", filters=definition["filters"]),
        status=422,
    )
    assert _reason(explicit_invalid, "invalid_query_request") == {"reason": "invalid_query_request"}
    result = await _ok(
        ctx, view=view, auth=survivor_auth, override_definition=_definition("list", filters=[])
    )
    assert result["total"] == 1 and result["items"][0]["title"] == "PRIVATE_Q4_SAVED_VIEW_ROW"
    current = await ctx["client"].get(
        ctx["plan"] + "/views/" + view["view_id"], headers=survivor_auth
    )
    assert (
        current.status_code == 200
        and current.json()["definition"]["filters"] == definition["filters"]
    )
    assert current.json()["version"] == view["version"] and _raw_state(ctx) == before


async def test_explicit_override_references_are_current_project_only_and_safe_422(
    env_with_provider: Any,
) -> None:
    ctx = await _context(env_with_provider)
    await _create_todo(ctx, title="PRIVATE_Q4_REFERENCE_ROW")
    created = await ctx["client"].post(
        "/api/projects", headers=ctx["owner_auth"], json={"name": "reference other"}
    )
    assert created.status_code == 201, created.text
    other = {**ctx, "pid": created.json()["project_id"]}
    other_catalog = await _catalog(other)
    other_tag = (await _option(other, "tags", "PRIVATE_Q4_REF"))["item"]["tag_id"]
    clauses = [
        {"field": "assignee", "op": "in", "values": [ctx["outsider_uid"]]},
        {"field": "assignee", "op": "not_in", "values": [2**31 - 1]},
        {
            "field": "priority",
            "op": "in",
            "values": [other_catalog["priorities"][0]["priority_id"]],
        },
        {"field": "priority", "op": "not_in", "values": ["PRIVATE_Q4_MISSING_PRIORITY"]},
        {"field": "tags", "op": "any", "values": [other_tag]},
        {"field": "tags", "op": "none_of", "values": ["PRIVATE_Q4_MISSING_TAG"]},
    ]
    before = _raw_state(ctx)
    for clause in clauses:
        for auth in (ctx["owner_auth"], ctx["member_auth"]):
            failed = await _query(
                ctx, auth=auth, override_definition=_definition(filters=[clause]), status=422
            )
            assert _reason(failed, "invalid_query_request") == {"reason": "invalid_query_request"}
            assert "condition_indices" not in failed.text and "PRIVATE_Q4_" not in failed.text
        denied = await _query(
            ctx,
            auth=ctx["outsider_auth"],
            override_definition=_definition(filters=[clause]),
            status=404,
        )
        assert "condition_indices" not in denied.text and "PRIVATE_Q4_" not in denied.text
    assert _raw_state(ctx) == before
    valid = await _ok(ctx, auth=ctx["member_auth"], override_definition=_definition(filters=[]))
    assert valid["total"] == 1 and valid["items"][0]["title"] == "PRIVATE_Q4_REFERENCE_ROW"


async def test_member_display_digest_only_invalidates_assignee_sort_or_group_queries(
    env_with_provider: Any,
) -> None:
    ctx = await _context(env_with_provider)
    users = ctx["srv"].services.user_repo
    long = "\ufdfa" * 200 + " same prefix " * 30
    users.set_display_name(ctx["owner_uid"], long + "z")
    users.set_display_name(ctx["member_uid"], long + "a")
    owner_row = await _new_todo(ctx, title="owner", assignee_user_id=ctx["owner_uid"])
    member_row = await _new_todo(ctx, title="member", assignee_user_id=ctx["member_uid"])
    null_row = await _new_todo(ctx, title="none")
    sensitive = _definition(sort=[{"field": "assignee", "direction": "asc"}])
    unaffected = _definition(sort=[{"field": "title", "direction": "asc"}])
    first = await _ok(ctx, override_definition=sensitive, limit=1)
    assert _ids(first) == {member_row["todo_id"]}
    plain = await _ok(ctx, override_definition=unaffected, limit=1)
    assert "\ufdfa" not in json.dumps(_cursor(first["next_cursor"]), ensure_ascii=False)
    users.set_display_name(ctx["owner_uid"], long + "0")
    _reason(
        await _query(
            ctx, override_definition=sensitive, limit=1, cursor=first["next_cursor"], status=409
        ),
        "query_changed",
    )
    fresh = await _ok(ctx, override_definition=sensitive)
    assert [item["todo_id"] for item in fresh["items"]] == [
        owner_row["todo_id"],
        member_row["todo_id"],
        null_row["todo_id"],
    ]
    continued = await _ok(ctx, override_definition=unaffected, limit=1, cursor=plain["next_cursor"])
    assert continued["query_fingerprint"] == plain["query_fingerprint"]


async def test_query_errors_use_the_current_users_zh_and_en_locale(env_with_provider: Any) -> None:
    ctx = await _context(env_with_provider)
    messages = []
    for locale in ("zh", "en"):
        changed = await ctx["client"].patch(
            "/api/auth/me", headers=ctx["owner_auth"], json={"locale": locale}
        )
        assert changed.status_code == 200, changed.text
        assert changed.json()["locale"] == locale
        current = await ctx["client"].get("/api/auth/me", headers=ctx["owner_auth"])
        assert current.status_code == 200 and current.json()["locale"] == locale
        response = await _query(
            ctx,
            auth={**ctx["owner_auth"], "Accept-Language": locale},
            expected_view_version=2,
            status=409,
        )
        _reason(response, "query_changed")
        messages.append(response.json()["error"]["message"])
    assert messages[0] != messages[1]
    assert any("\u4e00" <= character <= "\u9fff" for character in messages[0])
    assert all(not "\u4e00" <= character <= "\u9fff" for character in messages[1])
