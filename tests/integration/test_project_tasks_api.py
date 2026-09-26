"""Integration tests for the /api/projects/{id}/tasks router (PS-05 slice 1).

Covers the server half of ``docs/xiongbao/PROJECT_TASK_ACL_CONTRACT.md``:
a member attaches only their own existing Dashboard DM thread; foreign,
unknown, deleted, non-dashboard and non-DM threads are uniform 404s with no
title or id leak; own-thread-linked-elsewhere is 409
PROJECT_TASK_LINK_CONFLICT; listing is owner-scoped with literal title search
and stable server paging; member removal detaches links without touching the
original thread; and no shared project_events row ever exposes a thread_id.
"""

from __future__ import annotations

import json
from typing import Any

from tests.support.auth import create_agent, create_user, resolve_user_id

OWNER = "ptk_owner"
MEMBER = "ptk_member"
OUTSIDER = "ptk_outsider"

_SUMMARY_KEYS = {
    "project_id",
    "thread_id",
    "owner_user_id",
    "agent_id",
    "title",
    "source",
    "last_active",
    "created_at",
    "access",
    "can_read_text",
    "mode",
    "chat_agent_id",
    "source_expert_id",
}


async def _base(env: Any) -> dict[str, Any]:
    """Owner + member + outsider, one project, per-user agents (provider seeded)."""
    client, srv, admin_auth = env
    owner_auth = await create_user(client, admin_auth, username=OWNER)
    member_auth = await create_user(client, admin_auth, username=MEMBER)
    outsider_auth = await create_user(client, admin_auth, username=OUTSIDER)
    owner_uid = await resolve_user_id(client, admin_auth, OWNER)
    member_uid = await resolve_user_id(client, admin_auth, MEMBER)
    outsider_uid = await resolve_user_id(client, admin_auth, OUTSIDER)
    r = await client.post("/api/projects", headers=owner_auth, json={"name": "任务项目"})
    assert r.status_code == 201, r.text
    pid = r.json()["project_id"]
    srv.services.project_repo.add_member(pid, member_uid, role="member")
    owner_agent = await create_agent(client, owner_auth, name="ptk-owner-bot")
    member_agent = await create_agent(client, member_auth, name="ptk-member-bot")
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
        "owner_agent": owner_agent,
        "member_agent": member_agent,
    }


async def _new_thread(
    ctx: dict[str, Any], auth: dict[str, str], agent_id: str, title: str | None = None
) -> str:
    """Create a real Dashboard DM thread through the existing chat API."""
    r = await ctx["client"].post(f"/api/agents/{agent_id}/threads", headers=auth)
    assert r.status_code == 201, r.text
    tid = r.json()["thread_id"]
    if title is not None:
        ctx["srv"].services.thread_repo.update_title(tid, title)
    return tid


async def _attach(
    ctx: dict[str, Any], auth: dict[str, str], thread_id: str, pid: str | None = None
) -> Any:
    return await ctx["client"].post(
        f"/api/projects/{pid or ctx['pid']}/tasks/links",
        headers=auth,
        json={"thread_id": thread_id},
    )


def _links(srv: Any, pid: str | None = None) -> list[dict[str, Any]]:
    with srv.services.db.connect() as conn:
        if pid is None:
            rows = conn.execute(
                "SELECT project_id, thread_id, owner_user_id, source "
                "FROM project_task_links ORDER BY id"
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT project_id, thread_id, owner_user_id, source "
                "FROM project_task_links WHERE project_id = ? ORDER BY id",
                (pid,),
            ).fetchall()
    return [dict(r) for r in rows]


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
            "payload_json": r["payload_json"],
        }
        for r in rows
    ]


def _assert_no_thread_id_leak(srv: Any, pid: str, thread_ids: list[str]) -> None:
    for event in _events(srv, pid):
        blob = json.dumps(event, ensure_ascii=False, default=str)
        for tid in thread_ids:
            assert tid not in blob, blob
        assert "task" not in event["event_type"]


async def test_attach_own_task_returns_safe_summary_and_is_idempotent(
    env_with_provider: Any,
) -> None:
    ctx = await _base(env_with_provider)
    tid = await _new_thread(ctx, ctx["owner_auth"], ctx["owner_agent"], title="OWNER-SECRET-报告")
    r = await _attach(ctx, ctx["owner_auth"], tid)
    assert r.status_code == 201, r.text
    payload = r.json()
    assert set(payload) == _SUMMARY_KEYS
    assert payload["project_id"] == ctx["pid"]
    assert payload["thread_id"] == tid
    assert payload["owner_user_id"] == ctx["owner_uid"]
    assert payload["agent_id"] == ctx["owner_agent"]
    assert payload["title"] == "OWNER-SECRET-报告"
    assert payload["source"] == "manual"
    for forbidden in ("session_key", "artifacts", "pending_plan_path", "model_ref"):
        assert forbidden not in payload

    # Re-attaching the same project is idempotent: 200, no second row.
    r2 = await _attach(ctx, ctx["owner_auth"], tid)
    assert r2.status_code == 200, r2.text
    assert r2.json() == payload
    assert len([row for row in _links(ctx["srv"]) if row["thread_id"] == tid]) == 1
    _assert_no_thread_id_leak(ctx["srv"], ctx["pid"], [tid])


async def test_attach_foreign_unknown_or_non_dashboard_is_uniform_404(
    env_with_provider: Any,
) -> None:
    ctx = await _base(env_with_provider)
    owner_tid = await _new_thread(
        ctx, ctx["owner_auth"], ctx["owner_agent"], title="OWNER-SECRET-报告"
    )

    # A fellow member cannot attach (or discover) the owner's private thread.
    r = await _attach(ctx, ctx["member_auth"], owner_tid)
    assert r.status_code == 404, r.text
    assert r.json()["error"]["code"] == "NOT_FOUND"
    assert "OWNER-SECRET" not in r.text

    # Outsider: same uniform 404 as an unknown project.
    r = await _attach(ctx, ctx["outsider_auth"], owner_tid)
    assert r.status_code == 404
    assert r.json()["error"]["code"] == "NOT_FOUND"
    assert "OWNER-SECRET" not in r.text

    # Unknown thread id.
    r = await _attach(ctx, ctx["owner_auth"], "ghost-thread")
    assert r.status_code == 404
    assert r.json()["error"]["code"] == "NOT_FOUND"

    # Own thread on a non-dashboard channel.
    ctx["srv"].services.thread_repo.insert(
        thread_id="feishu-thread",
        agent_id=ctx["owner_agent"],
        user_id=ctx["owner_uid"],
        channel_type="feishu",
        session_key=f"{ctx['owner_agent']}:feishu:g1:group",
        title="渠道任务",
    )
    r = await _attach(ctx, ctx["owner_auth"], "feishu-thread")
    assert r.status_code == 404
    assert r.json()["error"]["code"] == "NOT_FOUND"

    # Dashboard channel but a group session key, not the user's :dm session.
    ctx["srv"].services.thread_repo.insert(
        thread_id="group-thread",
        agent_id=ctx["owner_agent"],
        user_id=ctx["owner_uid"],
        channel_type="dashboard",
        session_key=f"{ctx['owner_agent']}:dashboard:{ctx['owner_uid']}:group",
        title="群组任务",
    )
    r = await _attach(ctx, ctx["owner_auth"], "group-thread")
    assert r.status_code == 404
    assert r.json()["error"]["code"] == "NOT_FOUND"

    assert _links(ctx["srv"]) == []


async def test_attach_body_is_validated(env_with_provider: Any) -> None:
    ctx = await _base(env_with_provider)
    r = await ctx["client"].post(
        f"/api/projects/{ctx['pid']}/tasks/links", headers=ctx["owner_auth"], json={}
    )
    assert r.status_code == 422, r.text
    r = await ctx["client"].post(
        f"/api/projects/{ctx['pid']}/tasks/links",
        headers=ctx["owner_auth"],
        json={"thread_id": "x", "source": "cloud", "owner_user_id": 1},
    )
    assert r.status_code == 422, r.text


async def test_list_is_owner_scoped_with_literal_search_and_paging(env_with_provider: Any) -> None:
    ctx = await _base(env_with_provider)
    client = ctx["client"]
    t1 = await _new_thread(ctx, ctx["owner_auth"], ctx["owner_agent"], title="Alpha 1005 report")
    t2 = await _new_thread(ctx, ctx["owner_auth"], ctx["owner_agent"], title="alpha_100% done")
    t3 = await _new_thread(ctx, ctx["owner_auth"], ctx["owner_agent"], title="Beta 周报")
    tm = await _new_thread(ctx, ctx["member_auth"], ctx["member_agent"], title="MEMBER-SECRET")
    for tid in (t1, t2, t3):
        assert (await _attach(ctx, ctx["owner_auth"], tid)).status_code == 201
    assert (await _attach(ctx, ctx["member_auth"], tm)).status_code == 201

    # Deterministic activity order: t1 > t2 > t3.
    with ctx["srv"].services.db.transaction() as conn:
        for tid, la in ((t1, 300), (t2, 200), (t3, 100)):
            conn.execute("UPDATE threads SET last_active = ? WHERE thread_id = ?", (la, tid))

    r = await client.get(f"/api/projects/{ctx['pid']}/tasks", headers=ctx["owner_auth"])
    assert r.status_code == 200, r.text
    body = r.json()
    assert [i["thread_id"] for i in body["items"]] == [t1, t2, t3]
    assert body["limit"] == 20 and body["offset"] == 0 and body["has_more"] is False
    # The member's private task is invisible to the owner: no id, no title.
    assert tm not in r.text
    assert "MEMBER-SECRET" not in r.text
    assert all(i["owner_user_id"] == ctx["owner_uid"] for i in body["items"])
    assert all(set(i) == _SUMMARY_KEYS for i in body["items"])

    r = await client.get(f"/api/projects/{ctx['pid']}/tasks", headers=ctx["member_auth"])
    assert r.status_code == 200
    assert [i["thread_id"] for i in r.json()["items"]] == [tm]
    assert t1 not in r.text and "Alpha" not in r.text

    r = await client.get(f"/api/projects/{ctx['pid']}/tasks", headers=ctx["outsider_auth"])
    assert r.status_code == 404
    assert r.json()["error"]["code"] == "NOT_FOUND"
    r = await client.get("/api/projects/ghost/tasks", headers=ctx["owner_auth"])
    assert r.status_code == 404
    assert r.json()["error"]["code"] == "NOT_FOUND"

    # Literal, case-insensitive title search.
    r = await client.get(
        f"/api/projects/{ctx['pid']}/tasks", headers=ctx["owner_auth"], params={"q": "ALPHA"}
    )
    assert {i["thread_id"] for i in r.json()["items"]} == {t1, t2}
    # '%' stays literal: only t2 contains "100%". An unescaped wildcard would
    # also match t1 ("Alpha 1005 report" contains "100").
    r = await client.get(
        f"/api/projects/{ctx['pid']}/tasks", headers=ctx["owner_auth"], params={"q": "100%"}
    )
    assert {i["thread_id"] for i in r.json()["items"]} == {t2}
    # '_' stays literal: only t2 contains "a_1". An unescaped wildcard would
    # also match t1 ("Alpha 1005" contains "a 1").
    r = await client.get(
        f"/api/projects/{ctx['pid']}/tasks", headers=ctx["owner_auth"], params={"q": "a_1"}
    )
    assert {i["thread_id"] for i in r.json()["items"]} == {t2}

    # Server paging.
    r = await client.get(
        f"/api/projects/{ctx['pid']}/tasks", headers=ctx["owner_auth"], params={"limit": 2}
    )
    page = r.json()
    assert [i["thread_id"] for i in page["items"]] == [t1, t2]
    assert page["has_more"] is True
    r = await client.get(
        f"/api/projects/{ctx['pid']}/tasks",
        headers=ctx["owner_auth"],
        params={"limit": 2, "offset": 2},
    )
    page = r.json()
    assert [i["thread_id"] for i in page["items"]] == [t3]
    assert page["has_more"] is False

    # limit is capped at 100 by validation.
    r = await client.get(
        f"/api/projects/{ctx['pid']}/tasks", headers=ctx["owner_auth"], params={"limit": 101}
    )
    assert r.status_code == 422
    r = await client.get(
        f"/api/projects/{ctx['pid']}/tasks", headers=ctx["owner_auth"], params={"limit": 0}
    )
    assert r.status_code == 422


async def test_detail_is_owner_scoped(env_with_provider: Any) -> None:
    ctx = await _base(env_with_provider)
    client = ctx["client"]
    owner_tid = await _new_thread(ctx, ctx["owner_auth"], ctx["owner_agent"], title="OWNER-SECRET")
    member_tid = await _new_thread(
        ctx, ctx["member_auth"], ctx["member_agent"], title="MEMBER-SECRET"
    )
    assert (await _attach(ctx, ctx["owner_auth"], owner_tid)).status_code == 201
    assert (await _attach(ctx, ctx["member_auth"], member_tid)).status_code == 201

    r = await client.get(f"/api/projects/{ctx['pid']}/tasks/{owner_tid}", headers=ctx["owner_auth"])
    assert r.status_code == 200, r.text
    assert set(r.json()) == _SUMMARY_KEYS
    assert r.json()["title"] == "OWNER-SECRET"

    # Fellow member requesting the owner's task: uniform 404, no title leak.
    r = await client.get(
        f"/api/projects/{ctx['pid']}/tasks/{owner_tid}", headers=ctx["member_auth"]
    )
    assert r.status_code == 404
    assert r.json()["error"]["code"] == "NOT_FOUND"
    assert "OWNER-SECRET" not in r.text

    # Owner requesting the member's task: symmetric.
    r = await client.get(
        f"/api/projects/{ctx['pid']}/tasks/{member_tid}", headers=ctx["owner_auth"]
    )
    assert r.status_code == 404
    assert "MEMBER-SECRET" not in r.text

    # Unknown thread id and outsider are the same 404.
    r = await client.get(f"/api/projects/{ctx['pid']}/tasks/ghost", headers=ctx["owner_auth"])
    assert r.status_code == 404
    r = await client.get(
        f"/api/projects/{ctx['pid']}/tasks/{owner_tid}", headers=ctx["outsider_auth"]
    )
    assert r.status_code == 404
    assert r.json()["error"]["code"] == "NOT_FOUND"

    # Cross-project: the link exists in ctx pid only.
    r = await client.post("/api/projects", headers=ctx["owner_auth"], json={"name": "第二个项目"})
    pid_b = r.json()["project_id"]
    r = await client.get(f"/api/projects/{pid_b}/tasks/{owner_tid}", headers=ctx["owner_auth"])
    assert r.status_code == 404


async def test_cross_project_attach_conflicts_with_409(env_with_provider: Any) -> None:
    ctx = await _base(env_with_provider)
    client = ctx["client"]
    tid = await _new_thread(ctx, ctx["owner_auth"], ctx["owner_agent"], title="冲突任务")
    r = await client.post("/api/projects", headers=ctx["owner_auth"], json={"name": "项目B"})
    assert r.status_code == 201, r.text
    pid_b = r.json()["project_id"]

    assert (await _attach(ctx, ctx["owner_auth"], tid, pid=pid_b)).status_code == 201
    r = await _attach(ctx, ctx["owner_auth"], tid)
    assert r.status_code == 409, r.text
    assert r.json()["error"]["code"] == "PROJECT_TASK_LINK_CONFLICT"
    assert _links(ctx["srv"], ctx["pid"]) == []

    # After detaching from B the same thread attaches to A.
    r = await client.delete(f"/api/projects/{pid_b}/tasks/{tid}", headers=ctx["owner_auth"])
    assert r.status_code == 204, r.text
    assert (await _attach(ctx, ctx["owner_auth"], tid)).status_code == 201
    _assert_no_thread_id_leak(ctx["srv"], ctx["pid"], [tid])
    _assert_no_thread_id_leak(ctx["srv"], pid_b, [tid])


async def test_detach_keeps_thread_and_history_owner_readable(env_with_provider: Any) -> None:
    ctx = await _base(env_with_provider)
    client = ctx["client"]
    tid = await _new_thread(ctx, ctx["owner_auth"], ctx["owner_agent"], title="保持历史")
    assert (await _attach(ctx, ctx["owner_auth"], tid)).status_code == 201

    # A fellow member cannot detach the owner's link.
    r = await client.delete(f"/api/projects/{ctx['pid']}/tasks/{tid}", headers=ctx["member_auth"])
    assert r.status_code == 404, r.text
    assert len(_links(ctx["srv"], ctx["pid"])) == 1

    # Owner detaches: 204, link gone, thread untouched.
    r = await client.delete(f"/api/projects/{ctx['pid']}/tasks/{tid}", headers=ctx["owner_auth"])
    assert r.status_code == 204, r.text
    assert _links(ctx["srv"], ctx["pid"]) == []

    # Repeat delete is a 404 with no extra writes.
    r = await client.delete(f"/api/projects/{ctx['pid']}/tasks/{tid}", headers=ctx["owner_auth"])
    assert r.status_code == 404

    # The original conversation is still owner-readable …
    assert ctx["srv"].services.thread_repo.get(tid) is not None
    r = await client.get(
        f"/api/agents/{ctx['owner_agent']}/threads/{tid}/history", headers=ctx["owner_auth"]
    )
    assert r.status_code == 200, r.text
    # … and project membership never relaxed the thread-level ACL.
    r = await client.get(
        f"/api/agents/{ctx['owner_agent']}/threads/{tid}/history", headers=ctx["member_auth"]
    )
    assert r.status_code == 403, r.text


async def test_member_removal_detaches_links_atomically(env_with_provider: Any) -> None:
    ctx = await _base(env_with_provider)
    client = ctx["client"]
    member_tid = await _new_thread(
        ctx, ctx["member_auth"], ctx["member_agent"], title="MEMBER-SECRET"
    )
    owner_tid = await _new_thread(ctx, ctx["owner_auth"], ctx["owner_agent"], title="OWNER task")
    assert (await _attach(ctx, ctx["member_auth"], member_tid)).status_code == 201
    assert (await _attach(ctx, ctx["owner_auth"], owner_tid)).status_code == 201

    r = await client.delete(
        f"/api/projects/{ctx['pid']}/members/{ctx['member_uid']}", headers=ctx["owner_auth"]
    )
    assert r.status_code == 204, r.text

    assert [row["thread_id"] for row in _links(ctx["srv"], ctx["pid"])] == [owner_tid]
    # The removed member's project reads are now uniform 404s.
    r = await client.get(f"/api/projects/{ctx['pid']}/tasks", headers=ctx["member_auth"])
    assert r.status_code == 404
    r = await client.get(
        f"/api/projects/{ctx['pid']}/tasks/{member_tid}", headers=ctx["member_auth"]
    )
    assert r.status_code == 404
    # Their original thread is untouched and still owner-readable.
    r = await client.get(
        f"/api/agents/{ctx['member_agent']}/threads/{member_tid}/history",
        headers=ctx["member_auth"],
    )
    assert r.status_code == 200, r.text
    # No shared event exposes the removed member's private thread id.
    _assert_no_thread_id_leak(ctx["srv"], ctx["pid"], [member_tid, owner_tid])


async def test_thread_delete_cascades_the_link(env_with_provider: Any) -> None:
    ctx = await _base(env_with_provider)
    client = ctx["client"]
    tid = await _new_thread(ctx, ctx["owner_auth"], ctx["owner_agent"], title="级联")
    assert (await _attach(ctx, ctx["owner_auth"], tid)).status_code == 201
    r = await client.delete(
        f"/api/agents/{ctx['owner_agent']}/threads/{tid}", headers=ctx["owner_auth"]
    )
    assert r.status_code == 204, r.text
    assert _links(ctx["srv"], ctx["pid"]) == []
    r = await client.get(f"/api/projects/{ctx['pid']}/tasks/{tid}", headers=ctx["owner_auth"])
    assert r.status_code == 404


async def test_no_task_link_events_are_written(env_with_provider: Any) -> None:
    ctx = await _base(env_with_provider)
    client = ctx["client"]
    tid = await _new_thread(ctx, ctx["owner_auth"], ctx["owner_agent"], title="事件卫生")
    assert (await _attach(ctx, ctx["owner_auth"], tid)).status_code == 201
    assert (await _attach(ctx, ctx["owner_auth"], tid)).status_code == 200
    await client.get(f"/api/projects/{ctx['pid']}/tasks", headers=ctx["owner_auth"])
    await client.get(f"/api/projects/{ctx['pid']}/tasks/{tid}", headers=ctx["owner_auth"])
    r = await client.delete(f"/api/projects/{ctx['pid']}/tasks/{tid}", headers=ctx["owner_auth"])
    assert r.status_code == 204
    _assert_no_thread_id_leak(ctx["srv"], ctx["pid"], [tid])
    assert not [e for e in _events(ctx["srv"], ctx["pid"]) if "task" in e["event_type"]]


def test_links_route_registered_before_dynamic() -> None:
    """/tasks/links must precede /tasks/{thread_id} for safe route matching."""
    from octop.api.routers import project_tasks

    paths = [getattr(route, "path", "") for route in project_tasks.router.routes]
    links = paths.index("/projects/{project_id}/tasks/links")
    dynamic = paths.index("/projects/{project_id}/tasks/{thread_id}")
    assert links < dynamic
