"""HTTP contract for project todo text comments (PS-04B B1)."""

from __future__ import annotations

import json
from typing import Any

import pytest

from tests.integration.test_project_todos_api import _base, _create_todo, _events

REQUEST_ID = "d6e47312-3f3d-4a27-a43a-23c5df13218b"
COMMENT_KEYS = {
    "comment_id",
    "todo_id",
    "author_user_id",
    "author_name",
    "body",
    "images",
    "created_at",
}


def _path(ctx: dict[str, Any], todo_id: str, *, project_id: str | None = None) -> str:
    return f"/api/projects/{project_id or ctx['pid']}/todos/{todo_id}/comments"


async def test_member_comment_retries_are_idempotent_and_do_not_change_todo_version(
    env: Any,
) -> None:
    ctx = await _base(env)
    todo = await _create_todo(ctx)
    path = _path(ctx, todo["todo_id"])
    payload = {"body": "  成员评论  ", "client_request_id": REQUEST_ID}

    first = await ctx["client"].post(path, headers=ctx["member_auth"], json=payload)
    assert first.status_code == 201, first.text
    comment = first.json()
    assert set(comment) == COMMENT_KEYS
    assert comment["body"] == "成员评论"
    assert comment["author_user_id"] == ctx["member_uid"]
    assert comment["author_name"] == "pt_member"
    assert comment["images"] == []
    assert comment["todo_id"] == todo["todo_id"]

    retry = await ctx["client"].post(
        path,
        headers=ctx["member_auth"],
        json={"body": "成员评论", "client_request_id": REQUEST_ID},
    )
    assert retry.status_code == 200, retry.text
    assert retry.json() == comment
    conflict = await ctx["client"].post(
        path,
        headers=ctx["member_auth"],
        json={"body": "不同内容", "client_request_id": REQUEST_ID},
    )
    assert conflict.status_code == 409, conflict.text

    page = await ctx["client"].get(path, headers=ctx["owner_auth"])
    assert page.status_code == 200, page.text
    assert page.json() == {"items": [comment], "next_cursor": None}
    detail = await ctx["client"].get(
        f"/api/projects/{ctx['pid']}/todos/{todo['todo_id']}", headers=ctx["owner_auth"]
    )
    assert detail.json()["version"] == todo["version"]

    events = [
        event
        for event in _events(ctx["srv"], ctx["pid"])
        if event["event_type"] == "project.todo_comment_created"
    ]
    assert len(events) == 1
    assert events[0]["object_id"] == todo["todo_id"]
    assert events[0]["payload"] == {}
    assert "成员评论" not in json.dumps(events, ensure_ascii=False)


async def test_comment_body_request_id_and_actor_validation(env: Any) -> None:
    ctx = await _base(env)
    todo = await _create_todo(ctx)
    path = _path(ctx, todo["todo_id"])
    invalid = (
        {"body": "", "client_request_id": REQUEST_ID},
        {"body": "   ", "client_request_id": REQUEST_ID},
        {"body": "x" * 4001, "client_request_id": REQUEST_ID},
        {"body": "ok", "client_request_id": REQUEST_ID.upper()},
        {"body": "ok", "client_request_id": "not-a-uuid"},
        {"body": "ok", "client_request_id": "d6e47312-3f3d-1a27-a43a-23c5df13218b"},
        {"body": "ok", "client_request_id": REQUEST_ID, "actor_user_id": ctx["owner_uid"]},
    )
    for payload in invalid:
        response = await ctx["client"].post(path, headers=ctx["member_auth"], json=payload)
        assert response.status_code == 422, (payload, response.text)

    too_low = await ctx["client"].get(path, headers=ctx["owner_auth"], params={"limit": 0})
    too_high = await ctx["client"].get(path, headers=ctx["owner_auth"], params={"limit": 51})
    assert [too_low.status_code, too_high.status_code] == [422] * 2
    for cursor in ("!!!", "A", "AA", "eDox", "bm90LWEtY3Vyc29y"):
        bad_cursor = await ctx["client"].get(
            path, headers=ctx["owner_auth"], params={"cursor": cursor}
        )
        assert bad_cursor.status_code == 422, (cursor, bad_cursor.text)
        assert bad_cursor.json()["error"]["code"] == "PROJECT_TODO_COMMENT_CURSOR_INVALID"
    hidden = await ctx["client"].get(path, headers=ctx["outsider_auth"], params={"cursor": "!!!"})
    assert hidden.status_code == 404, hidden.text


async def test_comment_access_revocation_cross_project_and_deleted_todo_are_404(env: Any) -> None:
    ctx = await _base(env)
    todo = await _create_todo(ctx)
    path = _path(ctx, todo["todo_id"])
    outsider = ctx["outsider_auth"]
    for method in ("get", "post"):
        kwargs = {} if method == "get" else {"json": {"body": "x", "client_request_id": REQUEST_ID}}
        response = await getattr(ctx["client"], method)(path, headers=outsider, **kwargs)
        assert response.status_code == 404, response.text

    other = await ctx["client"].post(
        "/api/projects", headers=ctx["owner_auth"], json={"name": "另一个项目"}
    )
    foreign = _path(ctx, todo["todo_id"], project_id=other.json()["project_id"])
    for method in ("get", "post"):
        kwargs = {} if method == "get" else {"json": {"body": "x", "client_request_id": REQUEST_ID}}
        response = await getattr(ctx["client"], method)(
            foreign, headers=ctx["owner_auth"], **kwargs
        )
        assert response.status_code == 404, response.text

    published = await ctx["client"].post(
        path,
        headers=ctx["member_auth"],
        json={"body": "撤权前", "client_request_id": REQUEST_ID},
    )
    assert published.status_code == 201, published.text
    removed = await ctx["client"].delete(
        f"/api/projects/{ctx['pid']}/members/{ctx['member_uid']}",
        headers=ctx["owner_auth"],
    )
    assert removed.status_code == 204, removed.text
    for method in ("get", "post"):
        kwargs = (
            {} if method == "get" else {"json": {"body": "撤权后", "client_request_id": REQUEST_ID}}
        )
        response = await getattr(ctx["client"], method)(path, headers=ctx["member_auth"], **kwargs)
        assert response.status_code == 404, response.text

    deleted = await ctx["client"].delete(
        f"/api/projects/{ctx['pid']}/todos/{todo['todo_id']}",
        headers=ctx["owner_auth"],
        params={"expected_version": todo["version"]},
    )
    assert deleted.status_code == 204, deleted.text
    for method in ("get", "post"):
        kwargs = (
            {} if method == "get" else {"json": {"body": "已删", "client_request_id": REQUEST_ID}}
        )
        response = await getattr(ctx["client"], method)(path, headers=ctx["owner_auth"], **kwargs)
        assert response.status_code == 404, response.text


async def test_comments_page_stably_when_timestamps_tie(env: Any) -> None:
    ctx = await _base(env)
    todo = await _create_todo(ctx)
    path = _path(ctx, todo["todo_id"])
    repo = ctx["srv"].services.project_todo_comment_repo
    created = []
    for index in range(5):
        outcome = repo.create_comment(
            project_id=ctx["pid"],
            todo_id=todo["todo_id"],
            author_user_id=ctx["owner_uid"],
            body=f"评论 {index}",
            client_request_id=f"d6e4731{index}-3f3d-4a27-a43a-23c5df13218b",
            ts=1_700_000_000,
        )
        assert outcome.outcome == "created"
        assert outcome.row is not None
        created.append(outcome.row.comment_id)

    seen: list[str] = []
    cursor: str | None = None
    while True:
        params = {"limit": 2}
        if cursor:
            params["cursor"] = cursor
        response = await ctx["client"].get(path, headers=ctx["member_auth"], params=params)
        assert response.status_code == 200, response.text
        payload = response.json()
        seen.extend(item["comment_id"] for item in payload["items"])
        cursor = payload["next_cursor"]
        if cursor is None:
            break
    assert seen == sorted(created, reverse=True)
    assert len(seen) == len(set(seen))


async def test_comment_activity_is_safe_and_related_only_to_author(env: Any) -> None:
    ctx = await _base(env)
    todo = await _create_todo(ctx)
    created = await ctx["client"].post(
        _path(ctx, todo["todo_id"]),
        headers=ctx["member_auth"],
        json={"body": "SECRET-COMMENT-TEXT", "client_request_id": REQUEST_ID},
    )
    assert created.status_code == 201, created.text
    for auth, scope, expected in (
        (ctx["owner_auth"], "members", 1),
        (ctx["owner_auth"], "related", 0),
        (ctx["member_auth"], "related", 1),
    ):
        response = await ctx["client"].get(
            f"/api/projects/{ctx['pid']}/activity",
            headers=auth,
            params={"scope": scope},
        )
        assert response.status_code == 200, response.text
        assert "SECRET-COMMENT-TEXT" not in response.text
        comment_events = [
            item
            for item in response.json()["items"]
            if item["event_type"] == "project.todo_comment_created"
        ]
        assert len(comment_events) == expected
        for item in comment_events:
            assert item["object_kind"] == "todo"
            assert item["object_id"] == todo["todo_id"]
            assert item["message_body"] is None


async def test_comment_event_failure_rolls_back_comment(env: Any, monkeypatch: Any) -> None:
    ctx = await _base(env)
    todo = await _create_todo(ctx)
    path = _path(ctx, todo["todo_id"])

    def fail_event(*args: object, **kwargs: object) -> None:
        raise RuntimeError("injected comment event failure")

    monkeypatch.setattr(
        "octop.infra.db.repos.project_todo_comments._append_comment_event", fail_event
    )
    with pytest.raises(RuntimeError, match="injected comment event failure"):
        await ctx["client"].post(
            path,
            headers=ctx["owner_auth"],
            json={"body": "不应残留", "client_request_id": REQUEST_ID},
        )
    with ctx["srv"].services.db.connect() as conn:
        comments = conn.execute(
            "SELECT COUNT(*) FROM project_todo_comments WHERE todo_id = ?",
            (todo["todo_id"],),
        ).fetchone()[0]
        events = conn.execute(
            "SELECT COUNT(*) FROM project_events WHERE project_id = ? "
            "AND event_type = 'project.todo_comment_created'",
            (ctx["pid"],),
        ).fetchone()[0]
    assert comments == events == 0
