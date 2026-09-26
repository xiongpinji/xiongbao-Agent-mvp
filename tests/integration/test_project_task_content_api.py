"""Integration tests for task conversation TEXT access (PS-05B-2A, schema 025).

Covers the server half of
``docs/xiongbao/PROJECT_TASK_CONTENT_READ_CONTRACT.md``: the owner-only
``POST/DELETE /tasks/{thread_id}/shares/{user_id}/text`` endpoints (201 /
200 duplicate / 204 repeatable, empty request body — client-supplied
values are ignored), ``GET /tasks/{thread_id}/messages`` requiring BOTH an
active 024 card share AND an explicit 025 text grant (uniform 404s
otherwise, no projection-status leaks), the frozen response DTO with safe
projected text only, raw-seq cursor paging, projection-pending and
versioned-history (HistoryArchive) modes always answering pending, the
``can_read_text`` flag on task summaries and owner share metadata (never
auto-upgraded from 024), archived-project rules, membership-removal
cascades, and route registration order.
"""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import MagicMock

from langchain_core.messages import (
    AIMessage,
    HumanMessage,
    ToolMessage,
    message_to_dict,
)

from octop.infra.history.service import HistoryArchive
from tests.support.auth import create_agent, create_user, resolve_user_id

OWNER = "ptc_owner"
RECIPIENT = "ptc_recipient"
BYSTANDER = "ptc_bystander"
ADMIN = "ptc_admin"
OUTSIDER = "ptc_outsider"

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
_TEXT_KEYS = {"user_id", "granted_at"}
_MESSAGES_KEYS = {"status", "items", "has_more", "next_before_seq"}
_ITEM_KEYS = {"seq", "role", "text", "created_at", "truncated"}
_FORBIDDEN_SUBSTRINGS = (
    "message_json",
    "additional_kwargs",
    "tool_calls",
    "usage_metadata",
    "session_key",
    "artifact",
    "path",
)


async def _base(env: Any) -> dict[str, Any]:
    """Owner + recipient + bystander + project admin + outsider, one project."""
    client, srv, admin_auth = env
    auths: dict[str, dict[str, str]] = {}
    uids: dict[str, int] = {}
    for name in (OWNER, RECIPIENT, BYSTANDER, ADMIN, OUTSIDER):
        auths[name] = await create_user(client, admin_auth, username=name)
        uids[name] = await resolve_user_id(client, admin_auth, name)
    r = await client.post("/api/projects", headers=auths[OWNER], json={"name": "正文项目"})
    assert r.status_code == 201, r.text
    pid = r.json()["project_id"]
    srv.services.project_repo.add_member(pid, uids[RECIPIENT], role="member")
    srv.services.project_repo.add_member(pid, uids[BYSTANDER], role="member")
    srv.services.project_repo.add_member(pid, uids[ADMIN], role="admin")
    owner_agent = await create_agent(client, auths[OWNER], name="ptc-owner-bot")
    recipient_agent = await create_agent(client, auths[RECIPIENT], name="ptc-recipient-bot")
    ctx: dict[str, Any] = {
        "client": client,
        "srv": srv,
        "admin_auth": admin_auth,
        "pid": pid,
        "owner_agent": owner_agent,
        "recipient_agent": recipient_agent,
    }
    for name in (OWNER, RECIPIENT, BYSTANDER, ADMIN, OUTSIDER):
        key = name.removeprefix("ptc_")
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


async def _grant_card(
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


async def _revoke_card(
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


async def _grant_text(
    ctx: dict[str, Any],
    auth: dict[str, str],
    thread_id: str,
    grantee_uid: int,
    pid: str | None = None,
) -> Any:
    return await ctx["client"].post(
        f"/api/projects/{pid or ctx['pid']}/tasks/{thread_id}/shares/{grantee_uid}/text",
        headers=auth,
    )


async def _revoke_text(
    ctx: dict[str, Any],
    auth: dict[str, str],
    thread_id: str,
    grantee_uid: int,
    pid: str | None = None,
) -> Any:
    return await ctx["client"].delete(
        f"/api/projects/{pid or ctx['pid']}/tasks/{thread_id}/shares/{grantee_uid}/text",
        headers=auth,
    )


async def _messages(
    ctx: dict[str, Any],
    auth: dict[str, str],
    thread_id: str,
    params: dict[str, Any] | None = None,
    pid: str | None = None,
) -> Any:
    return await ctx["client"].get(
        f"/api/projects/{pid or ctx['pid']}/tasks/{thread_id}/messages",
        headers=auth,
        params=params or {},
    )


def _content_rows(srv: Any) -> list[dict[str, Any]]:
    with srv.services.db.connect() as conn:
        rows = conn.execute(
            "SELECT project_id, thread_id, grantee_user_id, granted_by_user_id, granted_at "
            "FROM project_task_content_grants ORDER BY grantee_user_id"
        ).fetchall()
    return [dict(r) for r in rows]


def _events(srv: Any, pid: str) -> list[dict[str, Any]]:
    with srv.services.db.connect() as conn:
        rows = conn.execute(
            "SELECT event_type, object_id, actor_user_id, payload_json "
            "FROM project_events WHERE project_id = ? ORDER BY id",
            (pid,),
        ).fetchall()
    return [dict(r) for r in rows]


def _assert_no_task_or_text_events(srv: Any, pid: str, thread_ids: list[str]) -> None:
    for event in _events(srv, pid):
        blob = json.dumps(event, ensure_ascii=False, default=str)
        for tid in thread_ids:
            assert tid not in blob, blob
        for word in ("task", "share", "text"):
            assert word not in str(event["event_type"])


def _wire(message: Any) -> str:
    return json.dumps(message_to_dict(message), ensure_ascii=False, default=str)


def _seed_messages(ctx: dict[str, Any], tid: str, rows: tuple[tuple[int, str, str], ...]) -> None:
    with ctx["srv"].services.db.transaction() as conn:
        for seq, role, message_json in rows:
            conn.execute(
                "INSERT INTO thread_messages("
                "thread_id, seq, message_id, role, message_json, created_at"
                ") VALUES (?, ?, ?, ?, ?, ?)",
                (tid, seq, f"m{seq}", role, message_json, 1000 + seq),
            )


async def _setup_shared_task(ctx: dict[str, Any], title: str = "OWNER-CARD") -> str:
    """Owner task with an active card share to RECIPIENT; returns thread id."""
    tid = await _new_thread(ctx, ctx["owner_auth"], ctx["owner_agent"], title=title)
    assert (await _attach(ctx, ctx["owner_auth"], tid)).status_code == 201
    assert (await _grant_card(ctx, ctx["owner_auth"], tid, ctx["recipient_uid"])).status_code == 201
    return tid


def _archive(ctx: dict[str, Any]) -> None:
    with ctx["srv"].services.db.transaction() as conn:
        conn.execute("UPDATE project_spaces SET archived = 1 WHERE project_id = ?", (ctx["pid"],))


# ---------------------------------------------------------------------------
# Text grant / revoke / regrant HTTP semantics
# ---------------------------------------------------------------------------


async def test_text_grant_revoke_regrant_http_semantics(env_with_provider: Any) -> None:
    ctx = await _base(env_with_provider)
    client = ctx["client"]
    tid = await _new_thread(ctx, ctx["owner_auth"], ctx["owner_agent"], title="正文任务")
    assert (await _attach(ctx, ctx["owner_auth"], tid)).status_code == 201

    # Text BEFORE the card: the same uniform 404 as any invalid target.
    r = await _grant_text(ctx, ctx["owner_auth"], tid, ctx["recipient_uid"])
    assert r.status_code == 404, r.text
    assert r.json()["error"]["code"] == "NOT_FOUND"
    assert _content_rows(ctx["srv"]) == []

    assert (await _grant_card(ctx, ctx["owner_auth"], tid, ctx["recipient_uid"])).status_code == 201
    r = await _grant_text(ctx, ctx["owner_auth"], tid, ctx["recipient_uid"])
    assert r.status_code == 201, r.text
    payload = r.json()
    assert set(payload) == _TEXT_KEYS
    assert payload["user_id"] == ctx["recipient_uid"]
    assert payload["granted_at"] > 0
    rows = _content_rows(ctx["srv"])
    assert len(rows) == 1
    assert rows[0]["granted_by_user_id"] == ctx["owner_uid"]

    # The endpoint takes NO request body: client-supplied values are ignored
    # and the response stays server-derived (duplicate → 200, unchanged).
    r2 = await client.post(
        f"/api/projects/{ctx['pid']}/tasks/{tid}/shares/{ctx['recipient_uid']}/text",
        headers=ctx["owner_auth"],
        json={"role": "writer", "granted_at": 1, "granted_by_user_id": ctx["outsider_uid"]},
    )
    assert r2.status_code == 200, r2.text
    assert r2.json() == payload
    assert len(_content_rows(ctx["srv"])) == 1

    # Revoke: 204, repeatable; the row is DELETED (not stamped).
    r3 = await _revoke_text(ctx, ctx["owner_auth"], tid, ctx["recipient_uid"])
    assert r3.status_code == 204, r3.text
    r4 = await _revoke_text(ctx, ctx["owner_auth"], tid, ctx["recipient_uid"])
    assert r4.status_code == 204
    assert _content_rows(ctx["srv"]) == []
    # Text revoke never touches the card itself.
    assert (
        await client.get(f"/api/projects/{ctx['pid']}/tasks/{tid}", headers=ctx["recipient_auth"])
    ).status_code == 200

    # Regrant after revoke: a fresh 201.
    r5 = await _grant_text(ctx, ctx["owner_auth"], tid, ctx["recipient_uid"])
    assert r5.status_code == 201, r5.text
    assert len(_content_rows(ctx["srv"])) == 1
    _assert_no_task_or_text_events(ctx["srv"], ctx["pid"], [tid])


async def test_text_endpoints_are_task_owner_only(env_with_provider: Any) -> None:
    ctx = await _base(env_with_provider)
    client = ctx["client"]
    tid = await _setup_shared_task(ctx, title="OWNER-SECRET")
    assert (await _grant_text(ctx, ctx["owner_auth"], tid, ctx["recipient_uid"])).status_code == 201

    # Project admin, bystander member, the grantee, and outsiders: uniform
    # 404 on POST text and DELETE text. Only the grantee already holding both
    # grants can read messages; project role never overrides task ownership.
    for auth in (
        ctx["admin_auth"],
        ctx["bystander_auth"],
        ctx["recipient_auth"],
        ctx["outsider_auth"],
    ):
        r = await _grant_text(ctx, auth, tid, ctx["recipient_uid"])
        assert r.status_code == 404, r.text
        assert r.json()["error"]["code"] == "NOT_FOUND"
        assert "OWNER-SECRET" not in r.text
        r = await _revoke_text(ctx, auth, tid, ctx["recipient_uid"])
        assert r.status_code == 404
        r = await _messages(ctx, auth, tid)
        assert r.status_code == (200 if auth == ctx["recipient_auth"] else 404)

    # Self and non-member recipients: uniform 404s, nothing written.
    r = await _grant_text(ctx, ctx["owner_auth"], tid, ctx["owner_uid"])
    assert r.status_code == 404
    r = await _grant_text(ctx, ctx["owner_auth"], tid, ctx["outsider_uid"])
    assert r.status_code == 404
    r = await _grant_text(ctx, ctx["owner_auth"], tid, ctx["bystander_uid"])
    assert r.status_code == 404
    r = await _grant_text(ctx, ctx["owner_auth"], "ghost-thread", ctx["recipient_uid"])
    assert r.status_code == 404
    r = await _grant_text(ctx, ctx["outsider_auth"], tid, ctx["recipient_uid"])
    assert r.status_code == 404
    r = await _grant_text(ctx, ctx["owner_auth"], tid, ctx["recipient_uid"], pid="ghost")
    assert r.status_code == 404
    rows = _content_rows(ctx["srv"])
    assert len(rows) == 1 and rows[0]["grantee_user_id"] == ctx["recipient_uid"]
    await client.get(f"/api/projects/{ctx['pid']}/tasks/{tid}/messages", headers=ctx["owner_auth"])


async def test_messages_requires_card_and_text_and_flags_follow(
    env_with_provider: Any,
) -> None:
    ctx = await _base(env_with_provider)
    client = ctx["client"]
    tid = await _setup_shared_task(ctx)
    _seed_messages(
        ctx,
        tid,
        (
            (1, "human", _wire(HumanMessage(content="你好"))),
            (2, "ai", _wire(AIMessage(content="hi"))),
        ),
    )
    detail_url = f"/api/projects/{ctx['pid']}/tasks/{tid}"
    shared_params = {"scope": "shared"}

    # Card only: messages 404 for the reader, can_read_text False everywhere.
    r = await _messages(ctx, ctx["recipient_auth"], tid)
    assert r.status_code == 404, r.text
    assert r.json()["error"]["code"] == "NOT_FOUND"
    r = await client.get(detail_url, headers=ctx["recipient_auth"])
    assert r.status_code == 200 and r.json()["can_read_text"] is False
    r = await client.get(
        f"/api/projects/{ctx['pid']}/tasks", headers=ctx["recipient_auth"], params=shared_params
    )
    assert r.status_code == 200
    assert [i["can_read_text"] for i in r.json()["items"]] == [False]
    r = await client.get(f"{detail_url}/shares", headers=ctx["owner_auth"])
    assert r.status_code == 200
    assert r.json()["items"][0]["can_read_text"] is False

    # Explicit text grant: 200 messages, can_read_text True in every view.
    assert (await _grant_text(ctx, ctx["owner_auth"], tid, ctx["recipient_uid"])).status_code == 201
    r = await _messages(ctx, ctx["recipient_auth"], tid)
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "ready"
    assert [(i["seq"], i["role"], i["text"]) for i in r.json()["items"]] == [
        (2, "assistant", "hi"),
        (1, "user", "你好"),
    ]
    r = await client.get(detail_url, headers=ctx["recipient_auth"])
    assert r.status_code == 200
    assert set(r.json()) == _SUMMARY_KEYS
    assert r.json()["can_read_text"] is True and r.json()["access"] == "reader"
    # A manual/chat share exposes the mode but redacts the private binding.
    assert r.json()["mode"] == "chat"
    assert r.json()["chat_agent_id"] is None and r.json()["source_expert_id"] is None
    r = await client.get(
        f"/api/projects/{ctx['pid']}/tasks", headers=ctx["recipient_auth"], params=shared_params
    )
    assert [i["can_read_text"] for i in r.json()["items"]] == [True]
    r = await client.get(f"{detail_url}/shares", headers=ctx["owner_auth"])
    item = r.json()["items"][0]
    assert set(item) == {"user_id", "role", "granted_at", "can_read_text"}
    assert item["role"] == "reader" and item["can_read_text"] is True
    # Owner views: always can_read_text True, owner reads messages directly.
    r = await client.get(detail_url, headers=ctx["owner_auth"])
    assert set(r.json()) == _SUMMARY_KEYS
    assert r.json()["can_read_text"] is True and r.json()["access"] == "owner"
    assert r.json()["mode"] == "chat"
    assert r.json()["chat_agent_id"] == ctx["owner_agent"]
    assert r.json()["source_expert_id"] is None
    r = await _messages(ctx, ctx["owner_auth"], tid)
    assert r.status_code == 200 and len(r.json()["items"]) == 2

    # Text-only revoke: card stays (detail 200), text closes (messages 404).
    assert (
        await _revoke_text(ctx, ctx["owner_auth"], tid, ctx["recipient_uid"])
    ).status_code == 204
    r = await client.get(detail_url, headers=ctx["recipient_auth"])
    assert r.status_code == 200 and r.json()["can_read_text"] is False
    r = await _messages(ctx, ctx["recipient_auth"], tid)
    assert r.status_code == 404

    # Card revoke deletes the text row too; everything closes at once.
    assert (await _grant_text(ctx, ctx["owner_auth"], tid, ctx["recipient_uid"])).status_code == 201
    assert len(_content_rows(ctx["srv"])) == 1
    assert (
        await _revoke_card(ctx, ctx["owner_auth"], tid, ctx["recipient_uid"])
    ).status_code == 204
    assert _content_rows(ctx["srv"]) == []
    r = await client.get(detail_url, headers=ctx["recipient_auth"])
    assert r.status_code == 404
    r = await _messages(ctx, ctx["recipient_auth"], tid)
    assert r.status_code == 404

    # Card regrant alone revives the card with text CLOSED; the fresh text
    # grant is a 201 again.
    r = await _grant_card(ctx, ctx["owner_auth"], tid, ctx["recipient_uid"])
    assert r.status_code == 200, r.text  # reactivation of the 024 row
    r = await client.get(detail_url, headers=ctx["recipient_auth"])
    assert r.status_code == 200 and r.json()["can_read_text"] is False
    r = await _messages(ctx, ctx["recipient_auth"], tid)
    assert r.status_code == 404
    assert (await _grant_text(ctx, ctx["owner_auth"], tid, ctx["recipient_uid"])).status_code == 201
    r = await _messages(ctx, ctx["recipient_auth"], tid)
    assert r.status_code == 200
    _assert_no_task_or_text_events(ctx["srv"], ctx["pid"], [tid])


# ---------------------------------------------------------------------------
# Messages DTO and safe projection
# ---------------------------------------------------------------------------


async def test_messages_dto_and_safe_projection(env_with_provider: Any) -> None:
    ctx = await _base(env_with_provider)
    tid = await _setup_shared_task(ctx)
    assert (await _grant_text(ctx, ctx["owner_auth"], tid, ctx["recipient_uid"])).status_code == 201

    _seed_messages(
        ctx,
        tid,
        (
            (1, "human", _wire(HumanMessage(content="第一句话"))),
            (2, "ai", _wire(AIMessage(content="回答"))),
            (3, "tool", _wire(ToolMessage(content="TOOL-SECRET", tool_call_id="c1"))),
            (
                4,
                "ai",
                _wire(
                    AIMessage(
                        content="CALLING-SECRET",
                        tool_calls=[{"name": "s", "args": {"q": "ARGS-SECRET"}, "id": "c1"}],
                    )
                ),
            ),
            (
                5,
                "ai",
                _wire(
                    AIMessage(
                        content="ERR-STACK-SECRET",
                        additional_kwargs={"octop_stream_error": True, "error_code": "E"},
                    )
                ),
            ),
            (
                6,
                "human",
                _wire(
                    HumanMessage(
                        content=[
                            {"type": "text", "text": "A"},
                            {"type": "image_url", "image_url": {"url": "https://IMG-SECRET/i"}},
                            "B",
                        ]
                    )
                ),
            ),
            (7, "human", _wire(HumanMessage(content="z" * 40_000))),
            (8, "human", _wire(HumanMessage(content="x" * 300_000))),
        ),
    )

    r = await _messages(ctx, ctx["recipient_auth"], tid)
    assert r.status_code == 200, r.text
    body = r.json()
    assert set(body) == _MESSAGES_KEYS
    assert body["status"] == "ready"
    assert body["has_more"] is False
    assert body["next_before_seq"] is None
    # Only whitelisted rows survive, newest first; the 40k row truncates and
    # the >256 KiB raw row disappears entirely.
    assert [(i["seq"], i["role"], i["truncated"]) for i in body["items"]] == [
        (7, "user", True),
        (6, "user", False),
        (2, "assistant", False),
        (1, "user", False),
    ]
    for item in body["items"]:
        assert set(item) == _ITEM_KEYS
        assert item["role"] in {"user", "assistant"}
        assert isinstance(item["text"], str)
        assert isinstance(item["seq"], int) and item["seq"] >= 1
        assert isinstance(item["created_at"], int) and item["created_at"] > 0
        assert isinstance(item["truncated"], bool)
    by_seq = {i["seq"]: i for i in body["items"]}
    assert by_seq[6]["text"] == "AB"
    assert len(by_seq[7]["text"].encode("utf-8")) <= 32768
    # No raw storage shapes, no provider metadata, no dangerous content.
    for secret in (
        "TOOL-SECRET",
        "CALLING-SECRET",
        "ARGS-SECRET",
        "ERR-STACK-SECRET",
        "IMG-SECRET",
        "message_json",
        "additional_kwargs",
        "tool_calls",
        "usage_metadata",
        "session_key",
        "artifact",
    ):
        assert secret not in r.text, secret
    assert "x" * 100 not in r.text

    # The owner sees the identical projection through the same endpoint.
    r_owner = await _messages(ctx, ctx["owner_auth"], tid)
    assert r_owner.status_code == 200
    assert r_owner.json() == body


async def test_messages_pagination_and_query_validation(env_with_provider: Any) -> None:
    ctx = await _base(env_with_provider)
    tid = await _setup_shared_task(ctx)
    assert (await _grant_text(ctx, ctx["owner_auth"], tid, ctx["recipient_uid"])).status_code == 201
    _seed_messages(
        ctx,
        tid,
        tuple((seq, "human", _wire(HumanMessage(content=f"m{seq}"))) for seq in range(1, 6)),
    )

    r = await _messages(ctx, ctx["recipient_auth"], tid, params={"limit": 2})
    page = r.json()
    assert [(i["seq"], i["text"]) for i in page["items"]] == [(5, "m5"), (4, "m4")]
    assert page["has_more"] is True and page["next_before_seq"] == 4
    r = await _messages(
        ctx, ctx["recipient_auth"], tid, params={"limit": 2, "before_seq": page["next_before_seq"]}
    )
    page = r.json()
    assert [(i["seq"], i["text"]) for i in page["items"]] == [(3, "m3"), (2, "m2")]
    assert page["has_more"] is True and page["next_before_seq"] == 2
    r = await _messages(ctx, ctx["recipient_auth"], tid, params={"limit": 2, "before_seq": 2})
    page = r.json()
    assert [(i["seq"], i["text"]) for i in page["items"]] == [(1, "m1")]
    assert page["has_more"] is False and page["next_before_seq"] is None

    # Default page (no params): limit defaults to 50 → everything at once.
    r = await _messages(ctx, ctx["recipient_auth"], tid)
    page = r.json()
    assert [i["seq"] for i in page["items"]] == [5, 4, 3, 2, 1]
    assert page["has_more"] is False and page["next_before_seq"] is None

    # limit is clamped to 1..100 and before_seq must be positive.
    for params in (
        {"limit": 0},
        {"limit": 101},
        {"limit": -1},
        {"before_seq": 0},
        {"before_seq": -1},
        {"limit": "abc"},
    ):
        r = await _messages(ctx, ctx["recipient_auth"], tid, params=params)
        assert r.status_code == 422, params
    r = await _messages(ctx, ctx["recipient_auth"], tid, params={"limit": 1})
    assert r.status_code == 200
    r = await _messages(ctx, ctx["recipient_auth"], tid, params={"limit": 100})
    assert r.status_code == 200


# ---------------------------------------------------------------------------
# Pending projection and versioned-history mode
# ---------------------------------------------------------------------------


async def test_messages_pending_projection_is_honest(env_with_provider: Any) -> None:
    ctx = await _base(env_with_provider)
    tid = await _setup_shared_task(ctx)
    assert (await _grant_text(ctx, ctx["owner_auth"], tid, ctx["recipient_uid"])).status_code == 201
    _seed_messages(ctx, tid, ((1, "human", _wire(HumanMessage(content="hello"))),))
    ctx["srv"].services.thread_message_repo.mark_projection(tid, "pending")

    for auth in (ctx["recipient_auth"], ctx["owner_auth"]):
        r = await _messages(ctx, auth, tid)
        assert r.status_code == 200, r.text
        assert r.json() == {
            "status": "pending",
            "items": [],
            "has_more": False,
            "next_before_seq": None,
        }
    # Pending never changes authorization: unauthorized readers stay 404.
    for auth in (ctx["bystander_auth"], ctx["admin_auth"], ctx["outsider_auth"]):
        assert (await _messages(ctx, auth, tid)).status_code == 404


async def test_history_archive_mode_always_pending(
    env_with_provider: Any, monkeypatch: Any
) -> None:
    ctx = await _base(env_with_provider)
    tid = await _setup_shared_task(ctx)
    assert (await _grant_text(ctx, ctx["owner_auth"], tid, ctx["recipient_uid"])).status_code == 201
    _seed_messages(ctx, tid, ((1, "human", _wire(HumanMessage(content="hello"))),))

    # Versioned-history deployments answer pending with no items — never
    # stale current-projection rows, never an archive read — for owner and
    # authorized reader alike.
    monkeypatch.setattr(ctx["srv"].app_runtime, "history_archive", MagicMock(spec=HistoryArchive))
    for auth in (ctx["recipient_auth"], ctx["owner_auth"]):
        r = await _messages(ctx, auth, tid)
        assert r.status_code == 200, r.text
        assert r.json() == {
            "status": "pending",
            "items": [],
            "has_more": False,
            "next_before_seq": None,
        }
    # Authorization still runs first: outsiders get 404, not pending.
    assert (await _messages(ctx, ctx["outsider_auth"], tid)).status_code == 404
    # Management endpoints keep working in archive mode.
    assert (
        await _revoke_text(ctx, ctx["owner_auth"], tid, ctx["recipient_uid"])
    ).status_code == 204

    monkeypatch.setattr(ctx["srv"].app_runtime, "history_archive", None)
    assert (await _grant_text(ctx, ctx["owner_auth"], tid, ctx["recipient_uid"])).status_code == 201
    r = await _messages(ctx, ctx["recipient_auth"], tid)
    assert r.status_code == 200 and r.json()["status"] == "ready"


# ---------------------------------------------------------------------------
# Archived project and membership cascades
# ---------------------------------------------------------------------------


async def test_archived_project_text_rules(env_with_provider: Any) -> None:
    ctx = await _base(env_with_provider)
    client = ctx["client"]
    tid = await _setup_shared_task(ctx)
    _archive(ctx)

    # New text grants are refused while archived …
    r = await _grant_text(ctx, ctx["owner_auth"], tid, ctx["recipient_uid"])
    assert r.status_code == 403, r.text
    assert r.json()["error"]["code"] == "FORBIDDEN"
    assert _content_rows(ctx["srv"]) == []

    # … but with a grant made BEFORE archiving, reads stay allowed and
    # revoking (tightening) still works.
    with ctx["srv"].services.db.transaction() as conn:
        conn.execute("UPDATE project_spaces SET archived = 0 WHERE project_id = ?", (ctx["pid"],))
    assert (await _grant_text(ctx, ctx["owner_auth"], tid, ctx["recipient_uid"])).status_code == 201
    _seed_messages(ctx, tid, ((1, "human", _wire(HumanMessage(content="hello"))),))
    _archive(ctx)
    r = await _messages(ctx, ctx["recipient_auth"], tid)
    assert r.status_code == 200 and r.json()["status"] == "ready"
    # Duplicate grant is refused too (archive beats duplicate).
    r = await _grant_text(ctx, ctx["owner_auth"], tid, ctx["recipient_uid"])
    assert r.status_code == 403
    assert r.json()["error"]["code"] == "FORBIDDEN"
    r = await _revoke_text(ctx, ctx["owner_auth"], tid, ctx["recipient_uid"])
    assert r.status_code == 204, r.text
    r = await _messages(ctx, ctx["recipient_auth"], tid)
    assert r.status_code == 404
    r = await _grant_text(ctx, ctx["owner_auth"], tid, ctx["recipient_uid"])
    assert r.status_code == 403
    # The card itself is untouched by text rules.
    r = await client.get(f"/api/projects/{ctx['pid']}/tasks/{tid}", headers=ctx["recipient_auth"])
    assert r.status_code == 200 and r.json()["can_read_text"] is False


async def test_member_removal_cascades_text_grants(env_with_provider: Any) -> None:
    ctx = await _base(env_with_provider)
    client = ctx["client"]
    tid = await _setup_shared_task(ctx)
    assert (await _grant_text(ctx, ctx["owner_auth"], tid, ctx["recipient_uid"])).status_code == 201
    _seed_messages(ctx, tid, ((1, "human", _wire(HumanMessage(content="hello"))),))
    assert len(_content_rows(ctx["srv"])) == 1

    r = await client.delete(
        f"/api/projects/{ctx['pid']}/members/{ctx['recipient_uid']}", headers=ctx["owner_auth"]
    )
    assert r.status_code == 204, r.text
    # Membership → card → text all cascade away immediately.
    assert _content_rows(ctx["srv"]) == []
    r = await _messages(ctx, ctx["recipient_auth"], tid)
    assert r.status_code == 404

    # Re-added membership resurrects nothing; card + text are fresh 201s.
    ctx["srv"].services.project_repo.add_member(ctx["pid"], ctx["recipient_uid"], role="member")
    r = await _grant_text(ctx, ctx["owner_auth"], tid, ctx["recipient_uid"])
    assert r.status_code == 404  # still no card
    r = await _grant_card(ctx, ctx["owner_auth"], tid, ctx["recipient_uid"])
    assert r.status_code == 201, r.text
    r = await _grant_text(ctx, ctx["owner_auth"], tid, ctx["recipient_uid"])
    assert r.status_code == 201, r.text
    r = await _messages(ctx, ctx["recipient_auth"], tid)
    assert r.status_code == 200 and len(r.json()["items"]) == 1


# ---------------------------------------------------------------------------
# Route registration
# ---------------------------------------------------------------------------


def test_route_registration_includes_text_and_messages() -> None:
    """/tasks/links stays before /tasks/{thread_id}; text + messages exist."""
    from octop.api.routers import project_tasks

    paths = [getattr(route, "path", "") for route in project_tasks.router.routes]
    links = paths.index("/projects/{project_id}/tasks/links")
    dynamic = paths.index("/projects/{project_id}/tasks/{thread_id}")
    assert links < dynamic
    assert "/projects/{project_id}/tasks/{thread_id}/shares/{user_id}/text" in paths
    assert "/projects/{project_id}/tasks/{thread_id}/messages" in paths
