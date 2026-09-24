"""Integration tests for task-card shares (PS-05B slice 1, schema 024).

Covers the server half of ``docs/xiongbao/PROJECT_TASK_SHARE_CONTRACT.md``:
only the task owner grants/revokes reader access to the card summary; first
grant is 201, active duplicate 200 (untouched), revoke 204 (repeatable),
regrant 200 (row reactivated); ``GET /tasks`` gains ``scope=own|shared|all``
with the private slice-1 default; detail reads carry ``access``; recipients
never gain history/detach rights; membership removal, detach, and revocation
remove access immediately and uniformly (404, no title leak); archived
projects refuse grants (403) but keep reads and revokes; and no
project_events row ever exposes a task or share.
"""

from __future__ import annotations

import json
from typing import Any

from tests.support.auth import create_agent, create_user, resolve_user_id

OWNER = "pts_owner"
RECIPIENT = "pts_recipient"
BYSTANDER = "pts_bystander"
ADMIN = "pts_admin"
OUTSIDER = "pts_outsider"

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
}
_SHARE_KEYS = {"user_id", "role", "granted_at"}


async def _base(env: Any) -> dict[str, Any]:
    """Owner + recipient + bystander + project admin + outsider, one project."""
    client, srv, admin_auth = env
    auths: dict[str, dict[str, str]] = {}
    uids: dict[str, int] = {}
    for name in (OWNER, RECIPIENT, BYSTANDER, ADMIN, OUTSIDER):
        auths[name] = await create_user(client, admin_auth, username=name)
        uids[name] = await resolve_user_id(client, admin_auth, name)
    r = await client.post("/api/projects", headers=auths[OWNER], json={"name": "分享项目"})
    assert r.status_code == 201, r.text
    pid = r.json()["project_id"]
    srv.services.project_repo.add_member(pid, uids[RECIPIENT], role="member")
    srv.services.project_repo.add_member(pid, uids[BYSTANDER], role="member")
    srv.services.project_repo.add_member(pid, uids[ADMIN], role="admin")
    owner_agent = await create_agent(client, auths[OWNER], name="pts-owner-bot")
    recipient_agent = await create_agent(client, auths[RECIPIENT], name="pts-recipient-bot")
    ctx: dict[str, Any] = {
        "client": client,
        "srv": srv,
        "admin_auth": admin_auth,
        "pid": pid,
        "owner_agent": owner_agent,
        "recipient_agent": recipient_agent,
    }
    for name in (OWNER, RECIPIENT, BYSTANDER, ADMIN, OUTSIDER):
        key = name.removeprefix("pts_")
        ctx[f"{key}_auth"] = auths[name]
        ctx[f"{key}_uid"] = uids[name]
    return ctx


async def _new_thread(
    ctx: dict[str, Any], auth: dict[str, str], agent_id: str, title: str | None = None
) -> str:
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


async def _grant(
    ctx: dict[str, Any],
    auth: dict[str, str],
    thread_id: str,
    grantee_uid: int,
    pid: str | None = None,
) -> Any:
    return await ctx["client"].post(
        f"/api/projects/{pid or ctx['pid']}/tasks/{thread_id}/shares",
        headers=auth,
        json={"user_id": grantee_uid},
    )


async def _revoke(
    ctx: dict[str, Any],
    auth: dict[str, str],
    thread_id: str,
    grantee_uid: int,
    pid: str | None = None,
) -> Any:
    return await ctx["client"].delete(
        f"/api/projects/{pid or ctx['pid']}/tasks/{thread_id}/shares/{grantee_uid}",
        headers=auth,
    )


def _share_rows(srv: Any) -> list[dict[str, Any]]:
    with srv.services.db.connect() as conn:
        rows = conn.execute(
            "SELECT project_id, thread_id, grantee_user_id, granted_by_user_id, "
            "role, granted_at, revoked_at FROM project_task_shares ORDER BY id"
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
        assert "share" not in event["event_type"]


def _set_active(ctx: dict[str, Any], pairs: tuple[tuple[str, int], ...]) -> None:
    with ctx["srv"].services.db.transaction() as conn:
        for tid, la in pairs:
            conn.execute("UPDATE threads SET last_active = ? WHERE thread_id = ?", (la, tid))


def _archive(ctx: dict[str, Any]) -> None:
    with ctx["srv"].services.db.transaction() as conn:
        conn.execute("UPDATE project_spaces SET archived = 1 WHERE project_id = ?", (ctx["pid"],))


async def test_grant_revoke_regrant_http_semantics(env_with_provider: Any) -> None:
    ctx = await _base(env_with_provider)
    tid = await _new_thread(ctx, ctx["owner_auth"], ctx["owner_agent"], title="OWNER-CARD-报告")
    assert (await _attach(ctx, ctx["owner_auth"], tid)).status_code == 201

    r = await _grant(ctx, ctx["owner_auth"], tid, ctx["recipient_uid"])
    assert r.status_code == 201, r.text
    payload = r.json()
    assert set(payload) == _SHARE_KEYS
    assert payload["user_id"] == ctx["recipient_uid"]
    assert payload["role"] == "reader"
    assert payload["granted_at"] > 0

    # Active duplicate: 200, identical payload, no row update.
    r2 = await _grant(ctx, ctx["owner_auth"], tid, ctx["recipient_uid"])
    assert r2.status_code == 200, r2.text
    assert r2.json() == payload
    rows = _share_rows(ctx["srv"])
    assert len(rows) == 1 and rows[0]["revoked_at"] is None

    # Revoke: 204, repeatable; the row survives with revoked_at stamped.
    r3 = await _revoke(ctx, ctx["owner_auth"], tid, ctx["recipient_uid"])
    assert r3.status_code == 204, r3.text
    r4 = await _revoke(ctx, ctx["owner_auth"], tid, ctx["recipient_uid"])
    assert r4.status_code == 204
    rows = _share_rows(ctx["srv"])
    assert len(rows) == 1 and rows[0]["revoked_at"] is not None

    # Regrant after revoke: 200 (reactivation), still exactly one row.
    r5 = await _grant(ctx, ctx["owner_auth"], tid, ctx["recipient_uid"])
    assert r5.status_code == 200, r5.text
    assert r5.json()["user_id"] == ctx["recipient_uid"]
    rows = _share_rows(ctx["srv"])
    assert len(rows) == 1 and rows[0]["revoked_at"] is None
    _assert_no_thread_id_leak(ctx["srv"], ctx["pid"], [tid])


async def test_grant_body_validation_and_invalid_recipients(env_with_provider: Any) -> None:
    ctx = await _base(env_with_provider)
    client = ctx["client"]
    tid = await _new_thread(ctx, ctx["owner_auth"], ctx["owner_agent"], title="T")
    assert (await _attach(ctx, ctx["owner_auth"], tid)).status_code == 201
    url = f"/api/projects/{ctx['pid']}/tasks/{tid}/shares"

    r = await client.post(url, headers=ctx["owner_auth"], json={})
    assert r.status_code == 422, r.text
    r = await client.post(url, headers=ctx["owner_auth"], json={"user_id": 0})
    assert r.status_code == 422, r.text
    r = await client.post(url, headers=ctx["owner_auth"], json={"user_id": "abc"})
    assert r.status_code == 422, r.text
    r = await client.post(
        url, headers=ctx["owner_auth"], json={"user_id": ctx["recipient_uid"], "role": "writer"}
    )
    assert r.status_code == 422, r.text

    # Self-grant, non-member recipient, unknown task, outsider actor, and
    # unknown project are all uniform 404s — nothing written.
    r = await _grant(ctx, ctx["owner_auth"], tid, ctx["owner_uid"])
    assert r.status_code == 404 and r.json()["error"]["code"] == "NOT_FOUND"
    r = await _grant(ctx, ctx["owner_auth"], tid, ctx["outsider_uid"])
    assert r.status_code == 404 and r.json()["error"]["code"] == "NOT_FOUND"
    r = await _grant(ctx, ctx["owner_auth"], "ghost-thread", ctx["recipient_uid"])
    assert r.status_code == 404
    r = await _grant(ctx, ctx["outsider_auth"], tid, ctx["recipient_uid"])
    assert r.status_code == 404
    r = await _grant(ctx, ctx["owner_auth"], tid, ctx["recipient_uid"], pid="ghost")
    assert r.status_code == 404
    assert _share_rows(ctx["srv"]) == []


async def test_share_management_is_task_owner_only(env_with_provider: Any) -> None:
    ctx = await _base(env_with_provider)
    client = ctx["client"]
    tid = await _new_thread(ctx, ctx["owner_auth"], ctx["owner_agent"], title="OWNER-SECRET")
    assert (await _attach(ctx, ctx["owner_auth"], tid)).status_code == 201
    assert (await _grant(ctx, ctx["owner_auth"], tid, ctx["recipient_uid"])).status_code == 201

    # Project admin, bystander member, the grantee, and outsiders: uniform
    # 404 on every share-management route, no title leak. Project role never
    # overrides task ownership.
    for auth in (
        ctx["admin_auth"],
        ctx["bystander_auth"],
        ctx["recipient_auth"],
        ctx["outsider_auth"],
    ):
        r = await client.get(f"/api/projects/{ctx['pid']}/tasks/{tid}/shares", headers=auth)
        assert r.status_code == 404, r.text
        assert r.json()["error"]["code"] == "NOT_FOUND"
        assert "OWNER-SECRET" not in r.text
        r = await _grant(ctx, auth, tid, ctx["bystander_uid"])
        assert r.status_code == 404
        assert "OWNER-SECRET" not in r.text
        r = await _revoke(ctx, auth, tid, ctx["recipient_uid"])
        assert r.status_code == 404

    # The grant survived every foreign attempt.
    rows = _share_rows(ctx["srv"])
    assert len(rows) == 1 and rows[0]["revoked_at"] is None

    # The owner lists active grant metadata only.
    r = await client.get(
        f"/api/projects/{ctx['pid']}/tasks/{tid}/shares", headers=ctx["owner_auth"]
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert set(body) == {"items"}
    assert len(body["items"]) == 1
    assert set(body["items"][0]) == _SHARE_KEYS
    assert body["items"][0]["user_id"] == ctx["recipient_uid"]


async def test_scope_own_shared_all_and_invalid_scope(env_with_provider: Any) -> None:
    ctx = await _base(env_with_provider)
    client = ctx["client"]
    t_o = await _new_thread(ctx, ctx["owner_auth"], ctx["owner_agent"], title="OWNER-CARD")
    t_r = await _new_thread(
        ctx, ctx["recipient_auth"], ctx["recipient_agent"], title="RECIPIENT-CARD"
    )
    assert (await _attach(ctx, ctx["owner_auth"], t_o)).status_code == 201
    assert (await _attach(ctx, ctx["recipient_auth"], t_r)).status_code == 201
    assert (await _grant(ctx, ctx["owner_auth"], t_o, ctx["recipient_uid"])).status_code == 201
    _set_active(ctx, ((t_o, 200), (t_r, 100)))

    base = f"/api/projects/{ctx['pid']}/tasks"
    # Default stays the private slice-1 behavior: own tasks only.
    r = await client.get(base, headers=ctx["recipient_auth"])
    assert r.status_code == 200, r.text
    assert [i["thread_id"] for i in r.json()["items"]] == [t_r]
    assert all(i["access"] == "owner" for i in r.json()["items"])
    assert t_o not in r.text

    r = await client.get(base, headers=ctx["recipient_auth"], params={"scope": "own"})
    assert [i["thread_id"] for i in r.json()["items"]] == [t_r]

    r = await client.get(base, headers=ctx["recipient_auth"], params={"scope": "shared"})
    assert r.status_code == 200, r.text
    items = r.json()["items"]
    assert [i["thread_id"] for i in items] == [t_o]
    assert items[0]["access"] == "reader"
    assert items[0]["title"] == "OWNER-CARD"
    assert all(set(i) == _SUMMARY_KEYS for i in items)

    r = await client.get(base, headers=ctx["recipient_auth"], params={"scope": "all"})
    assert r.status_code == 200, r.text
    assert [(i["thread_id"], i["access"]) for i in r.json()["items"]] == [
        (t_o, "reader"),
        (t_r, "owner"),
    ]
    assert r.json()["has_more"] is False

    # Literal search applies to the shared scope too.
    r = await client.get(
        base, headers=ctx["recipient_auth"], params={"scope": "shared", "q": "OWNER"}
    )
    assert [i["thread_id"] for i in r.json()["items"]] == [t_o]
    r = await client.get(
        base, headers=ctx["recipient_auth"], params={"scope": "shared", "q": "zzz"}
    )
    assert r.json()["items"] == []

    # Invalid scope → 422; outsider → 404; uninvolved member → empty page.
    r = await client.get(base, headers=ctx["recipient_auth"], params={"scope": "bogus"})
    assert r.status_code == 422, r.text
    r = await client.get(base, headers=ctx["outsider_auth"], params={"scope": "all"})
    assert r.status_code == 404
    r = await client.get(base, headers=ctx["bystander_auth"], params={"scope": "shared"})
    assert r.status_code == 200 and r.json()["items"] == []
    # The owner sees nothing "shared" — nobody granted them anything.
    r = await client.get(base, headers=ctx["owner_auth"], params={"scope": "shared"})
    assert r.status_code == 200 and r.json()["items"] == []
    # RECIPIENT's private card never leaks to the bystander in any scope.
    r = await client.get(base, headers=ctx["bystander_auth"], params={"scope": "all"})
    assert "RECIPIENT-CARD" not in r.text and t_r not in r.text


async def test_all_scope_pagination_crosses_own_shared_boundary(
    env_with_provider: Any,
) -> None:
    ctx = await _base(env_with_provider)
    client = ctx["client"]
    r1 = await _new_thread(ctx, ctx["recipient_auth"], ctx["recipient_agent"], title="R1")
    r2 = await _new_thread(ctx, ctx["recipient_auth"], ctx["recipient_agent"], title="R2")
    o1 = await _new_thread(ctx, ctx["owner_auth"], ctx["owner_agent"], title="O1")
    o2 = await _new_thread(ctx, ctx["owner_auth"], ctx["owner_agent"], title="O2")
    for tid, auth in ((r1, "recipient_auth"), (r2, "recipient_auth")):
        assert (await _attach(ctx, ctx[auth], tid)).status_code == 201
    for tid in (o1, o2):
        assert (await _attach(ctx, ctx["owner_auth"], tid)).status_code == 201
        assert (await _grant(ctx, ctx["owner_auth"], tid, ctx["recipient_uid"])).status_code == 201
    _set_active(ctx, ((r1, 400), (o1, 300), (r2, 200), (o2, 100)))

    base = f"/api/projects/{ctx['pid']}/tasks"
    r = await client.get(base, headers=ctx["recipient_auth"], params={"scope": "all", "limit": 2})
    page = r.json()
    assert [(i["thread_id"], i["access"]) for i in page["items"]] == [
        (r1, "owner"),
        (o1, "reader"),
    ]
    assert page["has_more"] is True
    r = await client.get(
        base,
        headers=ctx["recipient_auth"],
        params={"scope": "all", "limit": 2, "offset": 2},
    )
    page = r.json()
    assert [(i["thread_id"], i["access"]) for i in page["items"]] == [
        (r2, "owner"),
        (o2, "reader"),
    ]
    assert page["has_more"] is False


async def test_detail_access_reader_and_immediate_404_after_revoke(
    env_with_provider: Any,
) -> None:
    ctx = await _base(env_with_provider)
    client = ctx["client"]
    tid = await _new_thread(ctx, ctx["owner_auth"], ctx["owner_agent"], title="OWNER-CARD")
    assert (await _attach(ctx, ctx["owner_auth"], tid)).status_code == 201
    url = f"/api/projects/{ctx['pid']}/tasks/{tid}"

    r = await client.get(url, headers=ctx["owner_auth"])
    assert r.status_code == 200 and r.json()["access"] == "owner"
    # Before any grant: bystander and recipient are uniform 404s.
    for auth in (ctx["bystander_auth"], ctx["recipient_auth"], ctx["outsider_auth"]):
        r = await client.get(url, headers=auth)
        assert r.status_code == 404
        assert r.json()["error"]["code"] == "NOT_FOUND"
        assert "OWNER-CARD" not in r.text

    assert (await _grant(ctx, ctx["owner_auth"], tid, ctx["recipient_uid"])).status_code == 201
    r = await client.get(url, headers=ctx["recipient_auth"])
    assert r.status_code == 200, r.text
    body = r.json()
    assert set(body) == _SUMMARY_KEYS
    assert body["access"] == "reader"
    assert body["title"] == "OWNER-CARD"
    assert body["owner_user_id"] == ctx["owner_uid"]
    # Bystander is still locked out while the grant is active.
    r = await client.get(url, headers=ctx["bystander_auth"])
    assert r.status_code == 404 and "OWNER-CARD" not in r.text

    # Revoke removes the reader's detail access immediately.
    assert (await _revoke(ctx, ctx["owner_auth"], tid, ctx["recipient_uid"])).status_code == 204
    r = await client.get(url, headers=ctx["recipient_auth"])
    assert r.status_code == 404
    assert r.json()["error"]["code"] == "NOT_FOUND"
    assert "OWNER-CARD" not in r.text
    r = await client.get(
        f"/api/projects/{ctx['pid']}/tasks",
        headers=ctx["recipient_auth"],
        params={"scope": "shared"},
    )
    assert r.json()["items"] == []

    # Cross-project grant attempt on the same thread: uniform 404.
    r = await client.post("/api/projects", headers=ctx["owner_auth"], json={"name": "第二个项目"})
    pid_b = r.json()["project_id"]
    r = await _grant(ctx, ctx["owner_auth"], tid, ctx["recipient_uid"], pid=pid_b)
    assert r.status_code == 404


async def test_shared_card_does_not_unlock_history_or_detach(env_with_provider: Any) -> None:
    ctx = await _base(env_with_provider)
    client = ctx["client"]
    tid = await _new_thread(ctx, ctx["owner_auth"], ctx["owner_agent"], title="OWNER-CARD")
    assert (await _attach(ctx, ctx["owner_auth"], tid)).status_code == 201
    assert (await _grant(ctx, ctx["owner_auth"], tid, ctx["recipient_uid"])).status_code == 201

    # The card grant never relaxes the thread-level ACL: history stays 403.
    r = await client.get(
        f"/api/agents/{ctx['owner_agent']}/threads/{tid}/history",
        headers=ctx["recipient_auth"],
    )
    assert r.status_code == 403, r.text
    # Recipients cannot detach or re-attach the owner's link.
    r = await client.delete(
        f"/api/projects/{ctx['pid']}/tasks/{tid}", headers=ctx["recipient_auth"]
    )
    assert r.status_code == 404
    r = await _attach(ctx, ctx["recipient_auth"], tid)
    assert r.status_code == 404
    # The owner's link and grant are untouched; owner history still works.
    assert len(_share_rows(ctx["srv"])) == 1
    r = await client.get(
        f"/api/agents/{ctx['owner_agent']}/threads/{tid}/history", headers=ctx["owner_auth"]
    )
    assert r.status_code == 200, r.text


async def test_recipient_member_removal_kills_share_and_readd_regrants_201(
    env_with_provider: Any,
) -> None:
    ctx = await _base(env_with_provider)
    client = ctx["client"]
    tid = await _new_thread(ctx, ctx["owner_auth"], ctx["owner_agent"], title="OWNER-CARD")
    assert (await _attach(ctx, ctx["owner_auth"], tid)).status_code == 201
    assert (await _grant(ctx, ctx["owner_auth"], tid, ctx["recipient_uid"])).status_code == 201

    r = await client.delete(
        f"/api/projects/{ctx['pid']}/members/{ctx['recipient_uid']}", headers=ctx["owner_auth"]
    )
    assert r.status_code == 204, r.text
    # The grant row is gone with the membership; the owner's card survives.
    assert _share_rows(ctx["srv"]) == []
    r = await client.get(f"/api/projects/{ctx['pid']}/tasks/{tid}", headers=ctx["owner_auth"])
    assert r.status_code == 200
    # The removed member's project reads are uniform 404s again.
    r = await client.get(
        f"/api/projects/{ctx['pid']}/tasks",
        headers=ctx["recipient_auth"],
        params={"scope": "shared"},
    )
    assert r.status_code == 404

    # Re-added membership does NOT resurrect the grant; regranting is a 201.
    ctx["srv"].services.project_repo.add_member(ctx["pid"], ctx["recipient_uid"], role="member")
    r = await client.get(
        f"/api/projects/{ctx['pid']}/tasks",
        headers=ctx["recipient_auth"],
        params={"scope": "shared"},
    )
    assert r.status_code == 200 and r.json()["items"] == []
    r = await _grant(ctx, ctx["owner_auth"], tid, ctx["recipient_uid"])
    assert r.status_code == 201, r.text
    assert len(_share_rows(ctx["srv"])) == 1
    r = await client.get(f"/api/projects/{ctx['pid']}/tasks/{tid}", headers=ctx["recipient_auth"])
    assert r.status_code == 200 and r.json()["access"] == "reader"


async def test_archived_project_grant_403_reads_and_revoke_ok(env_with_provider: Any) -> None:
    ctx = await _base(env_with_provider)
    client = ctx["client"]
    tid = await _new_thread(ctx, ctx["owner_auth"], ctx["owner_agent"], title="OWNER-CARD")
    assert (await _attach(ctx, ctx["owner_auth"], tid)).status_code == 201
    assert (await _grant(ctx, ctx["owner_auth"], tid, ctx["recipient_uid"])).status_code == 201
    _archive(ctx)

    # Duplicate grant and new grant are both refused while archived.
    r = await _grant(ctx, ctx["owner_auth"], tid, ctx["recipient_uid"])
    assert r.status_code == 403, r.text
    assert r.json()["error"]["code"] == "FORBIDDEN"
    r = await _grant(ctx, ctx["owner_auth"], tid, ctx["bystander_uid"])
    assert r.status_code == 403

    # Reads stay allowed for the active recipient and the owner.
    r = await client.get(f"/api/projects/{ctx['pid']}/tasks/{tid}", headers=ctx["recipient_auth"])
    assert r.status_code == 200 and r.json()["access"] == "reader"
    r = await client.get(
        f"/api/projects/{ctx['pid']}/tasks",
        headers=ctx["recipient_auth"],
        params={"scope": "shared"},
    )
    assert r.status_code == 200 and [i["thread_id"] for i in r.json()["items"]] == [tid]
    r = await client.get(
        f"/api/projects/{ctx['pid']}/tasks/{tid}/shares", headers=ctx["owner_auth"]
    )
    assert r.status_code == 200 and len(r.json()["items"]) == 1

    # Revoking (tightening access) still works while archived.
    r = await _revoke(ctx, ctx["owner_auth"], tid, ctx["recipient_uid"])
    assert r.status_code == 204, r.text
    r = await client.get(f"/api/projects/{ctx['pid']}/tasks/{tid}", headers=ctx["recipient_auth"])
    assert r.status_code == 404
    # Regrant stays refused after the revoke.
    r = await _grant(ctx, ctx["owner_auth"], tid, ctx["recipient_uid"])
    assert r.status_code == 403


async def test_detach_cascades_shares(env_with_provider: Any) -> None:
    ctx = await _base(env_with_provider)
    client = ctx["client"]
    tid = await _new_thread(ctx, ctx["owner_auth"], ctx["owner_agent"], title="OWNER-CARD")
    assert (await _attach(ctx, ctx["owner_auth"], tid)).status_code == 201
    assert (await _grant(ctx, ctx["owner_auth"], tid, ctx["recipient_uid"])).status_code == 201

    r = await client.delete(f"/api/projects/{ctx['pid']}/tasks/{tid}", headers=ctx["owner_auth"])
    assert r.status_code == 204, r.text
    assert _share_rows(ctx["srv"]) == []
    r = await client.get(
        f"/api/projects/{ctx['pid']}/tasks",
        headers=ctx["recipient_auth"],
        params={"scope": "shared"},
    )
    assert r.status_code == 200 and r.json()["items"] == []

    # Re-attach + regrant is a fresh 201/201 pair; the thread never moved.
    assert (await _attach(ctx, ctx["owner_auth"], tid)).status_code == 201
    r = await _grant(ctx, ctx["owner_auth"], tid, ctx["recipient_uid"])
    assert r.status_code == 201, r.text
    assert len(_share_rows(ctx["srv"])) == 1
    assert ctx["srv"].services.thread_repo.get(tid) is not None


async def test_no_share_events_or_thread_id_leaks(env_with_provider: Any) -> None:
    ctx = await _base(env_with_provider)
    client = ctx["client"]
    t_o = await _new_thread(ctx, ctx["owner_auth"], ctx["owner_agent"], title="OWNER-CARD")
    t_r = await _new_thread(
        ctx, ctx["recipient_auth"], ctx["recipient_agent"], title="RECIPIENT-CARD"
    )
    assert (await _attach(ctx, ctx["owner_auth"], t_o)).status_code == 201
    assert (await _attach(ctx, ctx["recipient_auth"], t_r)).status_code == 201
    assert (await _grant(ctx, ctx["owner_auth"], t_o, ctx["recipient_uid"])).status_code == 201
    await client.get(
        f"/api/projects/{ctx['pid']}/tasks",
        headers=ctx["recipient_auth"],
        params={"scope": "all"},
    )
    await client.get(f"/api/projects/{ctx['pid']}/tasks/{t_o}", headers=ctx["recipient_auth"])
    await client.get(f"/api/projects/{ctx['pid']}/tasks/{t_o}/shares", headers=ctx["owner_auth"])
    assert (await _revoke(ctx, ctx["owner_auth"], t_o, ctx["recipient_uid"])).status_code == 204

    _assert_no_thread_id_leak(ctx["srv"], ctx["pid"], [t_o, t_r])
    types = {e["event_type"] for e in _events(ctx["srv"], ctx["pid"])}
    assert not [t for t in types if "task" in t or "share" in t]


def test_route_registration_order_includes_shares() -> None:
    """/tasks/links must precede /tasks/{thread_id}; share routes registered."""
    from octop.api.routers import project_tasks

    paths = [getattr(route, "path", "") for route in project_tasks.router.routes]
    links = paths.index("/projects/{project_id}/tasks/links")
    dynamic = paths.index("/projects/{project_id}/tasks/{thread_id}")
    assert links < dynamic
    assert "/projects/{project_id}/tasks/{thread_id}/shares" in paths
    assert "/projects/{project_id}/tasks/{thread_id}/shares/{user_id}" in paths
