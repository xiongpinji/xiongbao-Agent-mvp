"""Integration tests for /api/projects/{id}/activity and /messages (PS-03A).

Covers the server half of ``docs/xiongbao/PROJECT_ACTIVITY_CONTRACT.md``:
members publish and read plain-text messages; the shared timeline is limited
to the event whitelist with members|related scoping filtered in SQL before
paging; strict ``(created_at, id)`` cursor paging survives same-second ties
and concurrent inserts; outsiders, unknown projects, and revoked members get
uniform 404s; invalid cursors map to ``PROJECT_ACTIVITY_CURSOR_INVALID``;
an injected event-insert failure leaves no message behind; and neither
project instructions, invite material, join-request data, raw payloads, nor
021 private task ids/titles ever reach the activity JSON.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from octop.i18n import error_message
from octop.infra.db.repos.project_activity import ACTIVITY_EVENT_TYPES, EVENT_MESSAGE_CREATED
from tests.support.auth import create_agent, create_user, resolve_user_id

OWNER = "pact_owner"
MEMBER = "pact_member"
OUTSIDER = "pact_outsider"

_ITEM_KEYS = {
    "event_id",
    "event_type",
    "actor_user_id",
    "actor_name",
    "object_kind",
    "object_id",
    "message_body",
    "created_at",
}


async def _base(env: Any) -> dict[str, Any]:
    """Owner + member + outsider, one project owned by OWNER with MEMBER added."""
    client, srv, admin_auth = env
    owner_auth = await create_user(client, admin_auth, username=OWNER)
    member_auth = await create_user(client, admin_auth, username=MEMBER)
    outsider_auth = await create_user(client, admin_auth, username=OUTSIDER)
    owner_uid = await resolve_user_id(client, admin_auth, OWNER)
    member_uid = await resolve_user_id(client, admin_auth, MEMBER)
    outsider_uid = await resolve_user_id(client, admin_auth, OUTSIDER)
    r = await client.post("/api/projects", headers=owner_auth, json={"name": "动态项目"})
    assert r.status_code == 201, r.text
    pid = r.json()["project_id"]
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


async def _post_message(
    ctx: dict[str, Any], auth: dict[str, str], body: str, pid: str | None = None
) -> Any:
    return await ctx["client"].post(
        f"/api/projects/{pid or ctx['pid']}/messages", headers=auth, json={"body": body}
    )


async def _get_activity(
    ctx: dict[str, Any],
    auth: dict[str, str],
    *,
    scope: str = "members",
    limit: int = 20,
    cursor: str | None = None,
    pid: str | None = None,
    headers: dict[str, str] | None = None,
) -> Any:
    params: dict[str, Any] = {"scope": scope, "limit": limit}
    if cursor is not None:
        params["cursor"] = cursor
    return await ctx["client"].get(
        f"/api/projects/{pid or ctx['pid']}/activity",
        headers={**auth, **(headers or {})},
        params=params,
    )


def _seed_event(
    srv: Any,
    project_id: str,
    *,
    actor_user_id: int | None,
    event_type: str,
    object_id: str = "",
    payload_json: str = "{}",
    ts: int,
) -> int:
    with srv.services.db.transaction() as conn:
        row = conn.execute(
            "INSERT INTO project_events("
            "project_id, actor_user_id, event_type, object_id, payload_json, created_at"
            ") VALUES (?, ?, ?, ?, ?, ?) RETURNING id",
            (project_id, actor_user_id, event_type, object_id, payload_json, ts),
        ).fetchone()
    return int(row[0])


def _message_rows(srv: Any, pid: str) -> list[dict[str, Any]]:
    with srv.services.db.connect() as conn:
        rows = conn.execute(
            "SELECT message_id, author_user_id, body FROM project_messages "
            "WHERE project_id = ? ORDER BY created_at, message_id",
            (pid,),
        ).fetchall()
    return [dict(r) for r in rows]


# ---------------------------------------------------------------------------
# Publish / read round trip
# ---------------------------------------------------------------------------


async def test_members_publish_and_read_each_others_messages(env_with_provider: Any) -> None:
    ctx = await _base(env_with_provider)
    r1 = await _post_message(ctx, ctx["owner_auth"], "  第一条留言  ")
    assert r1.status_code == 201, r1.text
    item1 = r1.json()
    assert set(item1) == _ITEM_KEYS
    assert item1["event_type"] == EVENT_MESSAGE_CREATED
    assert item1["object_kind"] == "message"
    assert item1["message_body"] == "第一条留言"  # trimmed by the server
    assert item1["actor_user_id"] == ctx["owner_uid"]
    assert item1["actor_name"] == OWNER
    assert item1["object_id"]
    r2 = await _post_message(ctx, ctx["member_auth"], "第二条留言")
    assert r2.status_code == 201, r2.text

    # "After refresh": a fresh GET shows both messages, newest first.
    r = await _get_activity(ctx, ctx["member_auth"], scope="members")
    assert r.status_code == 200, r.text
    payload = r.json()
    assert set(payload) == {"items", "next_cursor"}
    assert payload["next_cursor"] is None
    messages = [i for i in payload["items"] if i["event_type"] == EVENT_MESSAGE_CREATED]
    assert len(messages) == 2
    assert all(set(i) == _ITEM_KEYS for i in payload["items"])
    assert messages[0]["message_body"] == "第二条留言"
    assert messages[0]["actor_name"] == MEMBER
    assert messages[1]["message_body"] == "第一条留言"
    assert messages[1]["actor_name"] == OWNER
    # Whitelist only; the response never carries raw payloads.
    assert {i["event_type"] for i in payload["items"]} <= set(ACTIVITY_EVENT_TYPES)
    assert "payload" not in r.text


async def test_related_scope_is_per_member(env_with_provider: Any) -> None:
    ctx = await _base(env_with_provider)
    await _post_message(ctx, ctx["owner_auth"], "owner 的留言")
    await _post_message(ctx, ctx["member_auth"], "member 的留言")

    r = await _get_activity(ctx, ctx["member_auth"], scope="related")
    assert r.status_code == 200, r.text
    items = r.json()["items"]
    bodies = {i["message_body"] for i in items if i["event_type"] == EVENT_MESSAGE_CREATED}
    assert bodies == {"member 的留言"}  # only own messages enter "related"
    # project.created was acted by the owner: not in member's related feed.
    assert "project.created" not in {i["event_type"] for i in items}

    r = await _get_activity(ctx, ctx["owner_auth"], scope="related")
    items = r.json()["items"]
    bodies = {i["message_body"] for i in items if i["event_type"] == EVENT_MESSAGE_CREATED}
    assert bodies == {"owner 的留言"}
    assert "project.created" in {i["event_type"] for i in items}  # owner acted


# ---------------------------------------------------------------------------
# ACL: outsider / admin-nonmember / revoked member → uniform 404
# ---------------------------------------------------------------------------


async def test_outsider_admin_and_unknown_project_get_uniform_404(env_with_provider: Any) -> None:
    ctx = await _base(env_with_provider)
    for auth in (ctx["outsider_auth"], ctx["admin_auth"]):
        for scope in ("members", "related"):
            r = await _get_activity(ctx, auth, scope=scope)
            assert r.status_code == 404, r.text
            assert r.json()["error"]["code"] == "NOT_FOUND"
        r = await _post_message(ctx, auth, "不该成功")
        assert r.status_code == 404, r.text
        assert r.json()["error"]["code"] == "NOT_FOUND"
    # Unknown project id is indistinguishable from membership failure.
    r = await _get_activity(ctx, ctx["owner_auth"], pid="ghost-project")
    assert r.status_code == 404
    r = await _post_message(ctx, ctx["owner_auth"], "hi", pid="ghost-project")
    assert r.status_code == 404
    assert _message_rows(ctx["srv"], "ghost-project") == []


async def test_removed_member_gets_404_and_history_stays_visible(env_with_provider: Any) -> None:
    ctx = await _base(env_with_provider)
    r = await _post_message(ctx, ctx["member_auth"], "被移除前的留言")
    assert r.status_code == 201, r.text

    r = await ctx["client"].delete(
        f"/api/projects/{ctx['pid']}/members/{ctx['member_uid']}", headers=ctx["owner_auth"]
    )
    assert r.status_code == 204, r.text

    for scope in ("members", "related"):
        r = await _get_activity(ctx, ctx["member_auth"], scope=scope)
        assert r.status_code == 404, r.text
    r = await _post_message(ctx, ctx["member_auth"], "被移除后的留言")
    assert r.status_code == 404, r.text
    assert len(_message_rows(ctx["srv"], ctx["pid"])) == 1

    # Remaining members still see the historical message with its author name.
    r = await _get_activity(ctx, ctx["owner_auth"], scope="members")
    items = r.json()["items"]
    messages = [i for i in items if i["event_type"] == EVENT_MESSAGE_CREATED]
    assert [m["message_body"] for m in messages] == ["被移除前的留言"]
    assert messages[0]["actor_name"] == MEMBER
    # The removal itself is a safe member-kind event with no target id.
    removed = [i for i in items if i["event_type"] == "project.member_removed"]
    assert len(removed) == 1
    assert removed[0]["object_kind"] == "member"
    assert removed[0]["object_id"] is None


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


async def test_post_message_validation_422(env_with_provider: Any) -> None:
    ctx = await _base(env_with_provider)
    client = ctx["client"]
    url = f"/api/projects/{ctx['pid']}/messages"
    auth = ctx["owner_auth"]

    r = await client.post(url, headers=auth, json={})
    assert r.status_code == 422, r.text
    r = await client.post(url, headers=auth, json={"body": "   "})
    assert r.status_code == 422, r.text
    r = await client.post(url, headers=auth, json={"body": "x" * 4001})
    assert r.status_code == 422, r.text
    r = await client.post(url, headers=auth, json={"body": 123})
    assert r.status_code == 422, r.text
    # A spoofed actor or any extra field is rejected outright.
    r = await client.post(
        url, headers=auth, json={"body": "ok", "actor_user_id": ctx["member_uid"]}
    )
    assert r.status_code == 422, r.text
    r = await client.post(url, headers=auth, json={"body": "ok", "event_type": "project.created"})
    assert r.status_code == 422, r.text
    assert _message_rows(ctx["srv"], ctx["pid"]) == []

    # Exactly 4000 characters is allowed.
    r = await client.post(url, headers=auth, json={"body": "y" * 4000})
    assert r.status_code == 201, r.text


async def test_get_activity_validation_and_cursor_error_code(env_with_provider: Any) -> None:
    ctx = await _base(env_with_provider)
    client = ctx["client"]
    auth = ctx["owner_auth"]

    r = await client.get(
        f"/api/projects/{ctx['pid']}/activity", headers=auth, params={"scope": "bogus"}
    )
    assert r.status_code == 422, r.text
    for limit in (0, 51):
        r = await client.get(
            f"/api/projects/{ctx['pid']}/activity", headers=auth, params={"limit": limit}
        )
        assert r.status_code == 422, r.text

    r = await _get_activity(ctx, auth, cursor="!!!not-a-cursor")
    assert r.status_code == 422, r.text
    envelope = r.json()["error"]
    assert envelope["code"] == "PROJECT_ACTIVITY_CURSOR_INVALID"
    assert envelope["message"]

    # Paired backend locales drive the message via Accept-Language.
    r = await _get_activity(ctx, auth, cursor="bad", headers={"Accept-Language": "zh-CN,zh;q=0.9"})
    assert r.status_code == 422
    assert r.json()["error"]["message"] == error_message("PROJECT_ACTIVITY_CURSOR_INVALID", "zh")
    r = await _get_activity(ctx, auth, cursor="bad", headers={"Accept-Language": "en"})
    assert r.status_code == 422
    assert r.json()["error"]["message"] == error_message("PROJECT_ACTIVITY_CURSOR_INVALID", "en")


# ---------------------------------------------------------------------------
# Cursor paging over HTTP
# ---------------------------------------------------------------------------


async def test_cursor_paging_same_second_and_new_inserts(env_with_provider: Any) -> None:
    ctx = await _base(env_with_provider)
    repo = ctx["srv"].services.project_activity_repo
    # Five messages in one artificial second: ties are broken by id DESC.
    posted = []
    for i in range(5):
        created = repo.create_message(
            project_id=ctx["pid"],
            author_user_id=ctx["owner_uid"],
            body=f"同一秒留言 {i}",
            ts=1_500_000_000,
        )
        assert created.outcome == "created" and created.row is not None
        posted.append(created.row.event_id)

    seen: list[int] = []
    cursor: str | None = None
    pages = 0
    while True:
        r = await _get_activity(ctx, ctx["owner_auth"], limit=2, cursor=cursor)
        assert r.status_code == 200, r.text
        data = r.json()
        seen.extend(i["event_id"] for i in data["items"])
        cursor = data["next_cursor"]
        pages += 1
        if cursor is None:
            break
        assert pages <= 10
        # A concurrent newer message between pages must not corrupt the walk.
        if pages == 1:
            late = repo.create_message(
                project_id=ctx["pid"],
                author_user_id=ctx["owner_uid"],
                body="翻页期间的新留言",
                ts=1_600_000_000,
            )
            assert late.outcome == "created"
    # project.created (ts=now) first, then the five same-second messages by
    # id DESC, then the older tail; the late insert never appears mid-walk.
    assert seen[1:6] == list(reversed(posted))
    assert len(seen) == len(set(seen))


async def test_related_full_page_with_interleaved_unrelated_events(env_with_provider: Any) -> None:
    ctx = await _base(env_with_provider)
    pid, srv = ctx["pid"], ctx["srv"]
    related: list[int] = []
    ts = 1_400_000_000
    for _ in range(6):
        for _ in range(5):  # unrelated noise acted by the owner
            ts += 1
            _seed_event(
                srv,
                pid,
                actor_user_id=ctx["owner_uid"],
                event_type="project.updated",
                object_id=pid,
                ts=ts,
            )
        ts += 1
        related.append(
            _seed_event(
                srv,
                pid,
                actor_user_id=ctx["member_uid"],
                event_type="project.updated",
                object_id=pid,
                ts=ts,
            )
        )

    r = await _get_activity(ctx, ctx["member_auth"], scope="related", limit=5)
    assert r.status_code == 200, r.text
    data = r.json()
    assert [i["event_id"] for i in data["items"]] == list(reversed(related))[:5]
    assert data["next_cursor"] is not None
    r = await _get_activity(
        ctx, ctx["member_auth"], scope="related", limit=5, cursor=data["next_cursor"]
    )
    data = r.json()
    assert [i["event_id"] for i in data["items"]] == [related[0]]
    assert data["next_cursor"] is None


# ---------------------------------------------------------------------------
# Leak surface
# ---------------------------------------------------------------------------


async def test_activity_never_leaks_instructions_invites_or_private_tasks(
    env_with_provider: Any,
) -> None:
    ctx = await _base(env_with_provider)
    client, srv = ctx["client"], ctx["srv"]

    # Project instructions must never reach the timeline.
    r = await client.patch(
        f"/api/projects/{ctx['pid']}",
        headers=ctx["owner_auth"],
        json={"instructions": "SECRET-INSTR-9 内部指令"},
    )
    assert r.status_code == 200, r.text

    # Invite material (token + invite_id) and join-request events stay out.
    r = await client.post(
        f"/api/projects/{ctx['pid']}/invites",
        headers=ctx["owner_auth"],
        json={"requires_approval": True},
    )
    assert r.status_code == 201, r.text
    invite = r.json()
    token = invite["token"]
    r = await client.post(
        "/api/projects/invites/accept", headers=ctx["outsider_auth"], json={"token": token}
    )
    assert r.status_code == 200 and r.json()["status"] == "pending_approval", r.text

    # A 021 private task link: thread id and title must not surface.
    member_agent = await create_agent(client, ctx["member_auth"], name="pact-member-bot")
    r = await client.post(f"/api/agents/{member_agent}/threads", headers=ctx["member_auth"])
    assert r.status_code == 201, r.text
    thread_id = r.json()["thread_id"]
    srv.services.thread_repo.update_title(thread_id, "PRIVATE-TASK-标题")
    r = await client.post(
        f"/api/projects/{ctx['pid']}/tasks/links",
        headers=ctx["member_auth"],
        json={"thread_id": thread_id},
    )
    assert r.status_code == 201, r.text

    await _post_message(ctx, ctx["member_auth"], "普通留言 <script>alert(1)</script>")

    for scope in ("members", "related"):
        r = await _get_activity(ctx, ctx["owner_auth"], scope=scope, limit=50)
        assert r.status_code == 200, r.text
        text = r.text
        for secret in (
            "SECRET-INSTR-9",
            token,
            invite["invite_id"],
            thread_id,
            "PRIVATE-TASK-标题",
            "payload_json",
            "project.invite_created",
            "project.invite_revoked",
            "project.join_requested",
            "project.join_approved",
            "project.join_rejected",
            "session_key",
        ):
            assert secret not in text, (scope, secret)
        data = json.loads(text)
        for item in data["items"]:
            assert item["event_type"] in ACTIVITY_EVENT_TYPES
            assert set(item) == _ITEM_KEYS
            # Member-target ids are hidden even though SQL uses them.
            if item["object_kind"] == "member":
                assert item["object_id"] is None
        # The plain-text message body is delivered verbatim (React escapes it).
        if scope == "members":
            bodies = {i["message_body"] for i in data["items"]}
            assert "普通留言 <script>alert(1)</script>" in bodies


# ---------------------------------------------------------------------------
# Atomicity
# ---------------------------------------------------------------------------


async def test_message_write_failure_leaves_no_rows(
    env_with_provider: Any, monkeypatch: Any
) -> None:
    ctx = await _base(env_with_provider)

    def _boom(*args: object, **kwargs: object) -> None:
        raise RuntimeError("injected event failure")

    monkeypatch.setattr("octop.infra.db.repos.project_activity._append_message_event", _boom)
    # The in-process ASGI transport propagates uncaught server exceptions to
    # the test client. The rollback assertion below is the behavior under test.
    with pytest.raises(RuntimeError, match="injected event failure"):
        await _post_message(ctx, ctx["owner_auth"], "会回滚的留言")
    assert _message_rows(ctx["srv"], ctx["pid"]) == []
    with ctx["srv"].services.db.connect() as conn:
        row = conn.execute(
            "SELECT COUNT(*) AS n FROM project_events WHERE project_id = ? AND event_type = ?",
            (ctx["pid"], EVENT_MESSAGE_CREATED),
        ).fetchone()
    assert int(row["n"]) == 0

    # After the injected failure clears, publishing works again.
    monkeypatch.undo()
    r = await _post_message(ctx, ctx["owner_auth"], "恢复后的留言")
    assert r.status_code == 201, r.text
    assert [m["body"] for m in _message_rows(ctx["srv"], ctx["pid"])] == ["恢复后的留言"]
