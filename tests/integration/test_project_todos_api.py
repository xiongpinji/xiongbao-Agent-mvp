"""Integration tests for the /api/projects/{id}/todos router (PS-04 plans).

Covers the server half of ``docs/xiongbao/PROJECT_TODO_CONTRACT.md``:
persistence across refresh, membership-gated 404s, privilege rules, optimistic
versioning, atomic bulk updates, LIKE-wildcard-safe search, member-removal
unassignment, and event payloads that never leak title/description text.
"""

from __future__ import annotations

import json
from typing import Any

from tests.support.auth import create_user, resolve_user_id

OWNER = "pt_owner"
MEMBER = "pt_member"
OTHER = "pt_other"
ADMIN_MEMBER = "pt_adminmember"
OUTSIDER = "pt_outsider"

_TODO_KEYS = {
    "todo_id",
    "project_id",
    "title",
    "description",
    "status",
    "creator_user_id",
    "assignee_user_id",
    "version",
    "created_at",
    "updated_at",
}


async def _base(env: Any) -> dict[str, Any]:
    """Owner + member + outsider users, one project owned by OWNER, member seeded."""
    client, srv, admin_auth = env
    owner_auth = await create_user(client, admin_auth, username=OWNER)
    member_auth = await create_user(client, admin_auth, username=MEMBER)
    outsider_auth = await create_user(client, admin_auth, username=OUTSIDER)
    r = await client.post("/api/projects", headers=owner_auth, json={"name": "计划项目"})
    assert r.status_code == 201, r.text
    pid = r.json()["project_id"]
    member_uid = await resolve_user_id(client, admin_auth, MEMBER)
    outsider_uid = await resolve_user_id(client, admin_auth, OUTSIDER)
    owner_uid = await resolve_user_id(client, admin_auth, OWNER)
    srv.services.project_repo.add_member(pid, member_uid, role="member")
    return {
        "client": client,
        "srv": srv,
        "admin_auth": admin_auth,
        "owner_auth": owner_auth,
        "member_auth": member_auth,
        "outsider_auth": outsider_auth,
        "pid": pid,
        "owner_uid": owner_uid,
        "member_uid": member_uid,
        "outsider_uid": outsider_uid,
    }


async def _add_user(ctx: dict[str, Any], username: str, role: str = "member") -> tuple[Any, int]:
    client, srv, admin_auth = ctx["client"], ctx["srv"], ctx["admin_auth"]
    auth = await create_user(client, admin_auth, username=username)
    uid = await resolve_user_id(client, admin_auth, username)
    srv.services.project_repo.add_member(ctx["pid"], uid, role=role)
    return auth, uid


async def _create_todo(
    ctx: dict[str, Any],
    auth: dict[str, str] | None = None,
    *,
    title: str = "任务",
    description: str = "",
    assignee_user_id: int | None = None,
) -> dict[str, Any]:
    client = ctx["client"]
    body: dict[str, Any] = {"title": title, "description": description}
    if assignee_user_id is not None:
        body["assignee_user_id"] = assignee_user_id
    r = await client.post(
        f"/api/projects/{ctx['pid']}/todos", headers=auth or ctx["owner_auth"], json=body
    )
    assert r.status_code == 201, r.text
    return r.json()


async def _list(ctx: dict[str, Any], auth: dict[str, str] | None = None, query: str = "") -> Any:
    client = ctx["client"]
    return await client.get(
        f"/api/projects/{ctx['pid']}/todos{query}", headers=auth or ctx["owner_auth"]
    )


def _events(srv: Any, pid: str) -> list[dict[str, Any]]:
    with srv.services.db.connect() as conn:
        rows = conn.execute(
            "SELECT event_type, object_id, actor_user_id, payload_json "
            "FROM project_events WHERE project_id = ? ORDER BY id",
            (pid,),
        ).fetchall()
    return [
        {
            "event_type": r["event_type"],
            "object_id": r["object_id"],
            "actor_user_id": r["actor_user_id"],
            "payload": json.loads(r["payload_json"]),
        }
        for r in rows
    ]


def _todo_rows(srv: Any, pid: str) -> list[dict[str, Any]]:
    with srv.services.db.connect() as conn:
        rows = conn.execute(
            "SELECT todo_id, title, status, assignee_user_id, version, deleted_at "
            "FROM project_todos WHERE project_id = ? ORDER BY todo_id",
            (pid,),
        ).fetchall()
    return [dict(r) for r in rows]


# ---------------------------------------------------------------- auth & 404s


async def test_todos_require_auth(env) -> None:
    client, _srv, _admin = env
    r = await client.get("/api/projects/whatever/todos")
    assert r.status_code == 401, r.text


async def test_outsider_and_unknown_project_both_404(env) -> None:
    ctx = await _base(env)
    client = ctx["client"]
    todo = await _create_todo(ctx, title="机密")
    outsider, pid = ctx["outsider_auth"], ctx["pid"]
    tid = todo["todo_id"]
    unknown = "01ARZ3NDEKTSV4RRFFQ69G5FAV"

    calls = [
        ("get", f"/api/projects/{pid}/todos", None),
        ("post", f"/api/projects/{pid}/todos", {"title": "x"}),
        ("get", f"/api/projects/{pid}/todos/{tid}", None),
        ("patch", f"/api/projects/{pid}/todos/{tid}", {"expected_version": 1, "status": "done"}),
        ("delete", f"/api/projects/{pid}/todos/{tid}?expected_version=1", None),
        (
            "post",
            f"/api/projects/{pid}/todos/bulk",
            {"items": [{"todo_id": tid, "expected_version": 1}], "status": "done"},
        ),
        ("get", f"/api/projects/{unknown}/todos", None),
        ("post", f"/api/projects/{unknown}/todos", {"title": "x"}),
    ]
    for method, url, body in calls:
        kwargs = {"headers": outsider}
        if body is not None:
            kwargs["json"] = body
        r = await getattr(client, method)(url, **kwargs)
        assert r.status_code == 404, (url, r.text)
        assert r.json()["error"]["code"] == "NOT_FOUND", (url, r.text)
        assert "机密" not in r.text


async def test_cross_project_todo_id_is_404(env) -> None:
    ctx = await _base(env)
    client, owner_auth = ctx["client"], ctx["owner_auth"]
    todo = await _create_todo(ctx)
    other = await client.post("/api/projects", headers=owner_auth, json={"name": "另一个项目"})
    assert other.status_code == 201, other.text
    other_pid = other.json()["project_id"]
    tid = todo["todo_id"]

    r = await client.get(f"/api/projects/{other_pid}/todos/{tid}", headers=owner_auth)
    assert r.status_code == 404, r.text
    r = await client.patch(
        f"/api/projects/{other_pid}/todos/{tid}",
        headers=owner_auth,
        json={"expected_version": 1, "status": "done"},
    )
    assert r.status_code == 404, r.text
    r = await client.delete(
        f"/api/projects/{other_pid}/todos/{tid}?expected_version=1", headers=owner_auth
    )
    assert r.status_code == 404, r.text
    r = await client.post(
        f"/api/projects/{other_pid}/todos/bulk",
        headers=owner_auth,
        json={"items": [{"todo_id": tid, "expected_version": 1}], "status": "done"},
    )
    assert r.status_code == 404, r.text
    # Untouched: still version 1, status todo.
    rows = _todo_rows(ctx["srv"], ctx["pid"])
    assert [(row["version"], row["status"]) for row in rows] == [(1, "todo")]


# ---------------------------------------------------------------- create & list


async def test_owner_creates_and_members_see_same_rows(env) -> None:
    ctx = await _base(env)
    client = ctx["client"]

    first = await _create_todo(ctx, title="写周报")
    second = await _create_todo(ctx, title="评审方案", assignee_user_id=ctx["member_uid"])

    for todo, title in ((first, "写周报"), (second, "评审方案")):
        assert set(todo) == _TODO_KEYS, todo
        assert todo["project_id"] == ctx["pid"]
        assert todo["title"] == title
        assert todo["description"] == ""
        assert todo["status"] == "todo"
        assert todo["creator_user_id"] == ctx["owner_uid"]
        assert todo["version"] == 1
        assert isinstance(todo["created_at"], int)
        assert todo["updated_at"] == todo["created_at"]
    assert first["assignee_user_id"] is None
    assert second["assignee_user_id"] == ctx["member_uid"]

    # Refresh: list is served from the database and identical for both members.
    owner_list = (await _list(ctx)).json()
    member_list = (await _list(ctx, ctx["member_auth"])).json()
    for page in (owner_list, member_list):
        ids = [item["todo_id"] for item in page["items"]]
        # updated_at DESC, todo_id DESC — same-second creates order by ULID.
        assert ids == [second["todo_id"], first["todo_id"]], ids
        assert page["has_more"] is False
        assert page["limit"] == 20
        assert page["offset"] == 0
        for item in page["items"]:
            assert set(item) == _TODO_KEYS

    detail = await client.get(
        f"/api/projects/{ctx['pid']}/todos/{second['todo_id']}", headers=ctx["member_auth"]
    )
    assert detail.status_code == 200, detail.text
    assert detail.json() == second

    events = _events(ctx["srv"], ctx["pid"])
    todo_events = [e for e in events if e["event_type"] == "project.todo_created"]
    assert [e["object_id"] for e in todo_events] == [first["todo_id"], second["todo_id"]]
    assert all("写周报" not in json.dumps(e["payload"], ensure_ascii=False) for e in todo_events)


async def test_list_status_filter_and_pagination(env) -> None:
    ctx = await _base(env)
    client = ctx["client"]
    a = await _create_todo(ctx, title="A")
    b = await _create_todo(ctx, title="B")
    c = await _create_todo(ctx, title="C", assignee_user_id=ctx["member_uid"])

    r = await client.patch(
        f"/api/projects/{ctx['pid']}/todos/{b['todo_id']}",
        headers=ctx["owner_auth"],
        json={"expected_version": 1, "status": "in_progress"},
    )
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "in_progress"
    assert r.json()["version"] == 2

    board = (await _list(ctx, query="?status=in_progress")).json()
    assert [i["todo_id"] for i in board["items"]] == [b["todo_id"]]
    todo_col = (await _list(ctx, query="?status=todo")).json()
    assert {i["todo_id"] for i in todo_col["items"]} == {a["todo_id"], c["todo_id"]}
    done_col = (await _list(ctx, query="?status=done")).json()
    assert done_col["items"] == []

    assignee = (await _list(ctx, query=f"?assignee_user_id={ctx['member_uid']}")).json()
    assert [i["todo_id"] for i in assignee["items"]] == [c["todo_id"]]

    page1 = (await _list(ctx, query="?limit=2")).json()
    assert len(page1["items"]) == 2
    assert page1["has_more"] is True
    page2 = (await _list(ctx, query="?limit=2&offset=2")).json()
    assert len(page2["items"]) == 1
    assert page2["has_more"] is False
    assert page2["offset"] == 2
    seen = {i["todo_id"] for i in page1["items"]} | {i["todo_id"] for i in page2["items"]}
    assert seen == {a["todo_id"], b["todo_id"], c["todo_id"]}


async def test_search_escapes_like_wildcards(env) -> None:
    ctx = await _base(env)
    await _create_todo(ctx, title="100% 完成")
    await _create_todo(ctx, title="100x 完成")
    await _create_todo(ctx, title="a_b 任务")
    await _create_todo(ctx, title="axb 任务")
    await _create_todo(ctx, title="All Done")

    async def titles(q: str) -> set[str]:
        page = (await _list(ctx, query=f"?q={q}")).json()
        return {i["title"] for i in page["items"]}

    # Literal % and _ must not act as SQL wildcards.
    assert await titles("100%25") == {"100% 完成"}
    assert await titles("a_b") == {"a_b 任务"}
    # Substring, case-insensitive.
    assert await titles("100") == {"100% 完成", "100x 完成"}
    assert await titles("done") == {"All Done"}
    assert await titles("DONE") == {"All Done"}
    # Backslash stays literal and does not break the ESCAPE clause.
    assert await titles("%5C") == set()


# ---------------------------------------------------------------- validation


async def test_create_validation_bounds(env) -> None:
    ctx = await _base(env)
    client = ctx["client"]
    url = f"/api/projects/{ctx['pid']}/todos"

    for bad in ("", "   ", "x" * 201):
        r = await client.post(url, headers=ctx["owner_auth"], json={"title": bad})
        assert r.status_code == 422, (bad, r.text)

    r = await client.post(url, headers=ctx["owner_auth"], json={"title": "x" * 200})
    assert r.status_code == 201, r.text
    assert r.json()["title"] == "x" * 200

    r = await client.post(url, headers=ctx["owner_auth"], json={"title": "  trimmed  "})
    assert r.status_code == 201, r.text
    assert r.json()["title"] == "trimmed"

    r = await client.post(
        url, headers=ctx["owner_auth"], json={"title": "t", "description": "y" * 4001}
    )
    assert r.status_code == 422, r.text
    r = await client.post(
        url, headers=ctx["owner_auth"], json={"title": "t", "description": "y" * 4000}
    )
    assert r.status_code == 201, r.text
    assert r.json()["description"] == "y" * 4000

    r = await client.post(url, headers=ctx["owner_auth"], json={"title": "t", "status": "done"})
    assert r.status_code == 422, r.text  # status is not settable at create time
    r = await client.post(
        url, headers=ctx["owner_auth"], json={"title": "t", "assignee_user_id": 0}
    )
    assert r.status_code == 422, r.text


async def test_list_query_bounds(env) -> None:
    ctx = await _base(env)
    for query in ("?limit=0", "?limit=101", "?offset=-1", "?status=bogus"):
        r = await _list(ctx, query=query)
        assert r.status_code == 422, (query, r.text)


# ---------------------------------------------------------------- privileges


async def test_member_assign_privileges(env) -> None:
    ctx = await _base(env)
    client = ctx["client"]
    other_auth, other_uid = await _add_user(ctx, OTHER)
    url = f"/api/projects/{ctx['pid']}/todos"

    # Ordinary member: self or null only.
    r = await client.post(
        url,
        headers=ctx["member_auth"],
        json={"title": "自己的", "assignee_user_id": ctx["member_uid"]},
    )
    assert r.status_code == 201, r.text
    assert r.json()["assignee_user_id"] == ctx["member_uid"]
    r = await client.post(url, headers=ctx["member_auth"], json={"title": "无主的"})
    assert r.status_code == 201, r.text
    assert r.json()["assignee_user_id"] is None

    # Assigning another member is a 403 even for a fellow member.
    r = await client.post(
        url, headers=ctx["member_auth"], json={"title": "别人的", "assignee_user_id": other_uid}
    )
    assert r.status_code == 403, r.text
    assert r.json()["error"]["code"] == "FORBIDDEN"

    # Ordinary members cannot assign anyone else, including a non-member;
    # the same 403 result does not expose whether that user belongs here.
    r = await client.post(
        url,
        headers=ctx["member_auth"],
        json={"title": "外人的", "assignee_user_id": ctx["outsider_uid"]},
    )
    assert r.status_code == 403, r.text
    err = r.json()["error"]
    assert err["code"] == "FORBIDDEN"
    assert err["details"] == {}

    # Owner/admin may assign any current member.
    r = await client.post(
        url, headers=ctx["owner_auth"], json={"title": "指派他人", "assignee_user_id": other_uid}
    )
    assert r.status_code == 201, r.text
    assert r.json()["assignee_user_id"] == other_uid
    admin_auth, _ = await _add_user(ctx, ADMIN_MEMBER, role="admin")
    r = await client.post(
        url, headers=admin_auth, json={"title": "管理员指派", "assignee_user_id": ctx["member_uid"]}
    )
    assert r.status_code == 201, r.text
    assert other_auth  # keeps the member's auth referenced


async def test_member_edit_and_delete_privileges(env) -> None:
    ctx = await _base(env)
    client = ctx["client"]
    other_auth, _other_uid = await _add_user(ctx, OTHER)

    # Todo created and owned outright by MEMBER.
    mine = await _create_todo(ctx, ctx["member_auth"], title="成员自己的")
    tid = mine["todo_id"]
    base = f"/api/projects/{ctx['pid']}/todos/{tid}"

    # Creator edits title/description/status — allowed; assignee — never.
    r = await client.patch(
        base, headers=ctx["member_auth"], json={"expected_version": 1, "title": "改标题"}
    )
    assert r.status_code == 200, r.text
    r = await client.patch(
        base, headers=ctx["member_auth"], json={"expected_version": 2, "assignee_user_id": None}
    )
    assert r.status_code == 403, r.text
    assert r.json()["error"]["code"] == "FORBIDDEN"

    # Unrelated member may not touch it.
    r = await client.patch(base, headers=other_auth, json={"expected_version": 2, "status": "done"})
    assert r.status_code == 403, r.text
    r = await client.delete(f"{base}?expected_version=2", headers=other_auth)
    assert r.status_code == 403, r.text

    # Owner/admin edit anything.
    r = await client.patch(
        base, headers=ctx["owner_auth"], json={"expected_version": 2, "status": "done"}
    )
    assert r.status_code == 200, r.text
    assert r.json()["version"] == 3

    # Assignee (not creator) may edit content but never the assignee.
    assigned = await _create_todo(ctx, title="指派给成员", assignee_user_id=ctx["member_uid"])
    abase = f"/api/projects/{ctx['pid']}/todos/{assigned['todo_id']}"
    r = await client.patch(
        abase, headers=ctx["member_auth"], json={"expected_version": 1, "status": "in_progress"}
    )
    assert r.status_code == 200, r.text
    r = await client.patch(
        abase,
        headers=ctx["member_auth"],
        json={"expected_version": 2, "assignee_user_id": ctx["member_uid"]},
    )
    assert r.status_code == 403, r.text
    # Even repeating the current assignee is forbidden for a regular member.
    r = await client.patch(
        abase, headers=ctx["member_auth"], json={"expected_version": 2, "title": "微调"}
    )
    assert r.status_code == 200, r.text

    # Delete: creator yes, assignee-only no, admin yes.
    r = await client.delete(f"{abase}?expected_version=3", headers=ctx["member_auth"])
    assert r.status_code == 403, r.text
    admin_auth, _ = await _add_user(ctx, ADMIN_MEMBER, role="admin")
    r = await client.delete(f"{abase}?expected_version=3", headers=admin_auth)
    assert r.status_code == 204, r.text
    r = await client.delete(f"{base}?expected_version=3", headers=ctx["member_auth"])
    assert r.status_code == 204, r.text


# ---------------------------------------------------------------- concurrency


async def test_patch_expected_version_conflict_single_winner(env) -> None:
    ctx = await _base(env)
    client = ctx["client"]
    todo = await _create_todo(ctx, title="原始")
    base = f"/api/projects/{ctx['pid']}/todos/{todo['todo_id']}"

    first = await client.patch(
        base, headers=ctx["owner_auth"], json={"expected_version": 1, "title": "赢家"}
    )
    assert first.status_code == 200, first.text
    assert first.json()["version"] == 2

    second = await client.patch(
        base, headers=ctx["owner_auth"], json={"expected_version": 1, "status": "done"}
    )
    assert second.status_code == 409, second.text
    err = second.json()["error"]
    assert err["details"]["reason"] == "version_conflict"

    # Loser wrote nothing: title from winner, status untouched, version 2.
    current = (await client.get(base, headers=ctx["owner_auth"])).json()
    assert current["title"] == "赢家"
    assert current["status"] == "todo"
    assert current["version"] == 2

    events = [
        e for e in _events(ctx["srv"], ctx["pid"]) if e["event_type"] == "project.todo_updated"
    ]
    assert len(events) == 1  # no event for the rejected write

    # Missing/invalid expected_version → 422.
    r = await client.patch(base, headers=ctx["owner_auth"], json={"title": "无版本"})
    assert r.status_code == 422, r.text
    r = await client.patch(
        base, headers=ctx["owner_auth"], json={"expected_version": 0, "title": "x"}
    )
    assert r.status_code == 422, r.text


async def test_patch_requires_actual_change(env) -> None:
    ctx = await _base(env)
    client = ctx["client"]
    todo = await _create_todo(ctx, title="不变")
    base = f"/api/projects/{ctx['pid']}/todos/{todo['todo_id']}"

    r = await client.patch(
        base, headers=ctx["owner_auth"], json={"expected_version": 1, "title": "不变"}
    )
    assert r.status_code == 400, r.text
    assert r.json()["error"]["details"]["reason"] == "no_change"
    r = await client.patch(base, headers=ctx["owner_auth"], json={"expected_version": 1})
    assert r.status_code == 400, r.text
    assert r.json()["error"]["details"]["reason"] == "no_change"

    current = (await client.get(base, headers=ctx["owner_auth"])).json()
    assert current["version"] == 1  # rejected no-ops do not bump


async def test_assignee_null_clears_and_owner_reassigns(env) -> None:
    ctx = await _base(env)
    client = ctx["client"]
    todo = await _create_todo(ctx, assignee_user_id=ctx["member_uid"])
    base = f"/api/projects/{ctx['pid']}/todos/{todo['todo_id']}"

    r = await client.patch(
        base, headers=ctx["owner_auth"], json={"expected_version": 1, "assignee_user_id": None}
    )
    assert r.status_code == 200, r.text
    assert r.json()["assignee_user_id"] is None
    assert r.json()["version"] == 2

    r = await client.patch(
        base,
        headers=ctx["owner_auth"],
        json={"expected_version": 2, "assignee_user_id": ctx["outsider_uid"]},
    )
    assert r.status_code == 400, r.text
    assert r.json()["error"]["details"]["reason"] == "invalid_assignee"
    current = (await client.get(base, headers=ctx["owner_auth"])).json()
    assert current["assignee_user_id"] is None and current["version"] == 2  # rejected: unchanged


# ---------------------------------------------------------------- delete


async def test_delete_soft_and_invisible_everywhere(env) -> None:
    ctx = await _base(env)
    client = ctx["client"]
    todo = await _create_todo(ctx, title="待删除")
    tid = todo["todo_id"]
    base = f"/api/projects/{ctx['pid']}/todos/{tid}"

    # Wrong version first: rejected, still fully visible.
    r = await client.delete(f"{base}?expected_version=5", headers=ctx["owner_auth"])
    assert r.status_code == 409, r.text
    assert r.json()["error"]["details"]["reason"] == "version_conflict"
    assert (await client.get(base, headers=ctx["owner_auth"])).status_code == 200

    # DELETE carries expected_version as a query parameter (no request body).
    r = await client.delete(f"{base}?expected_version=1", headers=ctx["owner_auth"])
    assert r.status_code == 204, r.text
    assert not r.content

    assert (await client.get(base, headers=ctx["owner_auth"])).status_code == 404
    page = (await _list(ctx)).json()
    assert page["items"] == []
    r = await client.patch(
        base, headers=ctx["owner_auth"], json={"expected_version": 2, "title": "x"}
    )
    assert r.status_code == 404, r.text
    r = await client.delete(f"{base}?expected_version=2", headers=ctx["owner_auth"])
    assert r.status_code == 404, r.text

    # Row is soft-deleted in the database with a version bump, never resurrected.
    rows = _todo_rows(ctx["srv"], ctx["pid"])
    assert len(rows) == 1
    assert rows[0]["deleted_at"] is not None
    assert rows[0]["version"] == 2

    deleted = [
        e for e in _events(ctx["srv"], ctx["pid"]) if e["event_type"] == "project.todo_deleted"
    ]
    assert [e["object_id"] for e in deleted] == [tid]
    assert "待删除" not in json.dumps(deleted[0]["payload"], ensure_ascii=False)

    r = await client.delete(base, headers=ctx["owner_auth"])
    assert r.status_code == 422, r.text  # expected_version query param is required


# ---------------------------------------------------------------- bulk


async def test_bulk_requires_manager(env) -> None:
    ctx = await _base(env)
    client = ctx["client"]
    todo = await _create_todo(ctx)
    body = {"items": [{"todo_id": todo["todo_id"], "expected_version": 1}], "status": "done"}

    r = await client.post(
        f"/api/projects/{ctx['pid']}/todos/bulk", headers=ctx["member_auth"], json=body
    )
    assert r.status_code == 403, r.text
    assert r.json()["error"]["code"] == "FORBIDDEN"
    assert _todo_rows(ctx["srv"], ctx["pid"])[0]["version"] == 1


async def test_bulk_atomic_all_or_nothing(env) -> None:
    ctx = await _base(env)
    client = ctx["client"]
    a = await _create_todo(ctx, title="A")
    b = await _create_todo(ctx, title="B")
    c = await _create_todo(ctx, title="C")
    url = f"/api/projects/{ctx['pid']}/todos/bulk"

    # One stale version poisons the batch: 409, zero rows touched, zero events.
    stale = {
        "items": [
            {"todo_id": a["todo_id"], "expected_version": 1},
            {"todo_id": b["todo_id"], "expected_version": 1},
            {"todo_id": c["todo_id"], "expected_version": 99},
        ],
        "status": "done",
    }
    r = await client.post(url, headers=ctx["owner_auth"], json=stale)
    assert r.status_code == 409, r.text
    assert r.json()["error"]["details"]["reason"] == "version_conflict"
    assert r.json()["error"]["details"]["todo_id"] == c["todo_id"]
    rows = _todo_rows(ctx["srv"], ctx["pid"])
    assert [(row["version"], row["status"]) for row in rows] == [(1, "todo")] * 3
    assert not [
        e for e in _events(ctx["srv"], ctx["pid"]) if e["event_type"] == "project.todo_updated"
    ]

    # All current: single transaction updates every row.
    ok = {
        "items": [
            {"todo_id": a["todo_id"], "expected_version": 1},
            {"todo_id": b["todo_id"], "expected_version": 1},
            {"todo_id": c["todo_id"], "expected_version": 1},
        ],
        "status": "done",
        "assignee_user_id": ctx["member_uid"],
    }
    r = await client.post(url, headers=ctx["owner_auth"], json=ok)
    assert r.status_code == 200, r.text
    items = r.json()["items"]
    assert {i["todo_id"] for i in items} == {a["todo_id"], b["todo_id"], c["todo_id"]}
    for item in items:
        assert set(item) == _TODO_KEYS
        assert item["status"] == "done"
        assert item["version"] == 2
        assert item["assignee_user_id"] == ctx["member_uid"]
    updated = [
        e for e in _events(ctx["srv"], ctx["pid"]) if e["event_type"] == "project.todo_updated"
    ]
    assert len(updated) == 3
    assert sorted(e["payload"]["fields"] for e in updated) == [["assignee_user_id", "status"]] * 3

    # A foreign-project id (or deleted/unknown) 404s and changes nothing.
    foreign = await client.post("/api/projects", headers=ctx["owner_auth"], json={"name": "别的"})
    foreign_pid = foreign.json()["project_id"]
    r = await client.post(
        f"/api/projects/{foreign_pid}/todos/bulk",
        headers=ctx["owner_auth"],
        json={"items": [{"todo_id": a["todo_id"], "expected_version": 2}], "status": "todo"},
    )
    assert r.status_code == 404, r.text
    rows = _todo_rows(ctx["srv"], ctx["pid"])
    assert all(row["version"] == 2 for row in rows)

    # Bulk-clear the assignee with an explicit null.
    r = await client.post(
        url,
        headers=ctx["owner_auth"],
        json={
            "items": [{"todo_id": a["todo_id"], "expected_version": 2}],
            "assignee_user_id": None,
        },
    )
    assert r.status_code == 200, r.text
    assert r.json()["items"][0]["assignee_user_id"] is None
    assert r.json()["items"][0]["status"] == "done"  # untouched field preserved


async def test_bulk_request_validation(env) -> None:
    ctx = await _base(env)
    client = ctx["client"]
    todo = await _create_todo(ctx)
    url = f"/api/projects/{ctx['pid']}/todos/bulk"
    item = {"todo_id": todo["todo_id"], "expected_version": 1}

    r = await client.post(url, headers=ctx["owner_auth"], json={"items": [], "status": "done"})
    assert r.status_code == 422, r.text  # empty batch
    r = await client.post(
        url, headers=ctx["owner_auth"], json={"items": [item, item], "status": "done"}
    )
    assert r.status_code == 422, r.text  # duplicate ids
    r = await client.post(url, headers=ctx["owner_auth"], json={"items": [item]})
    assert r.status_code == 422, r.text  # neither status nor assignee
    big = {
        "items": [{"todo_id": f"T{i:04d}", "expected_version": 1} for i in range(51)],
        "status": "done",
    }
    r = await client.post(url, headers=ctx["owner_auth"], json=big)
    assert r.status_code == 422, r.text  # over 50 items
    r = await client.post(
        url,
        headers=ctx["owner_auth"],
        json={"items": [item], "assignee_user_id": ctx["outsider_uid"]},
    )
    assert r.status_code == 400, r.text  # non-member assignee
    assert r.json()["error"]["details"]["reason"] == "invalid_assignee"
    assert _todo_rows(ctx["srv"], ctx["pid"])[0]["version"] == 1  # rejected batch wrote nothing


# ---------------------------------------------------------------- member removal


async def test_remove_member_unassigns_todos_atomically(env) -> None:
    ctx = await _base(env)
    client, srv = ctx["client"], ctx["srv"]

    assigned = await _create_todo(ctx, title="SECRET-标题", assignee_user_id=ctx["member_uid"])
    unassigned = await _create_todo(ctx, title="无主")
    gone = await _create_todo(ctx, title="已删", assignee_user_id=ctx["member_uid"])
    r = await client.delete(
        f"/api/projects/{ctx['pid']}/todos/{gone['todo_id']}?expected_version=1",
        headers=ctx["owner_auth"],
    )
    assert r.status_code == 204, r.text

    r = await client.delete(
        f"/api/projects/{ctx['pid']}/members/{ctx['member_uid']}", headers=ctx["owner_auth"]
    )
    assert r.status_code == 204, r.text

    page = (await _list(ctx)).json()
    by_id = {i["todo_id"]: i for i in page["items"]}
    assert gone["todo_id"] not in by_id  # deleted stays deleted
    assert by_id[assigned["todo_id"]]["assignee_user_id"] is None
    assert by_id[assigned["todo_id"]]["version"] == 2  # bumped so stale writes fail
    assert by_id[assigned["todo_id"]]["title"] == "SECRET-标题"
    assert by_id[unassigned["todo_id"]]["version"] == 1

    rows = {row["todo_id"]: row for row in _todo_rows(srv, ctx["pid"])}
    assert rows[gone["todo_id"]]["deleted_at"] is not None
    # The soft-deleted row keeps its historical assignee and version.
    assert rows[gone["todo_id"]]["assignee_user_id"] == ctx["member_uid"]
    assert rows[gone["todo_id"]]["version"] == 2  # bumped by the earlier delete only

    unassign_events = [
        e
        for e in _events(srv, ctx["pid"])
        if e["event_type"] == "project.todo_updated" and e["object_id"] == assigned["todo_id"]
    ]
    assert len(unassign_events) == 1
    payload = unassign_events[0]["payload"]
    assert payload["fields"] == ["assignee_user_id"]
    assert payload["from_assignee_user_id"] == ctx["member_uid"]
    assert payload["to_assignee_user_id"] is None
    dumped = json.dumps(_events(srv, ctx["pid"]), ensure_ascii=False)
    assert "SECRET" not in dumped  # events never carry title/description

    # The removed member now gets the same 404 as an outsider.
    r = await _list(ctx, ctx["member_auth"])
    assert r.status_code == 404, r.text
    r = await client.get(
        f"/api/projects/{ctx['pid']}/todos/{assigned['todo_id']}", headers=ctx["member_auth"]
    )
    assert r.status_code == 404, r.text


async def test_forbidden_member_removal_leaves_todos_untouched(env) -> None:
    ctx = await _base(env)
    client = ctx["client"]
    admin_auth, admin_uid = await _add_user(ctx, ADMIN_MEMBER, role="admin")
    await _create_todo(ctx, assignee_user_id=admin_uid)

    # A plain member cannot remove an admin; nothing may change.
    r = await client.delete(
        f"/api/projects/{ctx['pid']}/members/{admin_uid}", headers=ctx["member_auth"]
    )
    assert r.status_code == 403, r.text
    rows = _todo_rows(ctx["srv"], ctx["pid"])
    assert rows[0]["assignee_user_id"] == admin_uid
    assert rows[0]["version"] == 1
    assert not [
        e for e in _events(ctx["srv"], ctx["pid"]) if e["event_type"] == "project.todo_updated"
    ]
    assert admin_auth


# ---------------------------------------------------------------- event hygiene


async def test_events_only_carry_safe_fields(env) -> None:
    ctx = await _base(env)
    client = ctx["client"]
    todo = await _create_todo(ctx, title="SECRET-TITLE", description="SECRET-DESC")
    base = f"/api/projects/{ctx['pid']}/todos/{todo['todo_id']}"

    await client.patch(
        base,
        headers=ctx["owner_auth"],
        json={
            "expected_version": 1,
            "title": "SECRET-TITLE-2",
            "status": "in_progress",
            "assignee_user_id": ctx["member_uid"],
        },
    )
    await client.delete(f"{base}?expected_version=2", headers=ctx["owner_auth"])

    events = [
        e for e in _events(ctx["srv"], ctx["pid"]) if e["event_type"].startswith("project.todo")
    ]
    assert [e["event_type"] for e in events] == [
        "project.todo_created",
        "project.todo_updated",
        "project.todo_deleted",
    ]
    allowed = {
        "fields",
        "from_status",
        "to_status",
        "from_assignee_user_id",
        "to_assignee_user_id",
        "creator_user_id",
        "assignee_user_id",
    }
    for event in events:
        assert set(event["payload"]) <= allowed, event
        assert event["actor_user_id"] == ctx["owner_uid"]
    dumped = json.dumps(events, ensure_ascii=False)
    assert "SECRET" not in dumped
    updated = events[1]["payload"]
    assert updated["fields"] == ["assignee_user_id", "status", "title"]
    assert updated["from_status"] == "todo"
    assert updated["to_status"] == "in_progress"
    assert updated["from_assignee_user_id"] is None
    assert updated["to_assignee_user_id"] == ctx["member_uid"]
