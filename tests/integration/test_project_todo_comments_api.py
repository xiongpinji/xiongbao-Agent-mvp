"""HTTP contract for project todo text comments (PS-04B B1)."""

from __future__ import annotations

import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from io import BytesIO
from typing import Any

import pytest
from PIL import Image

from octop.infra.db.factory import open_database
from octop.infra.db.repos.projects import ProjectRepo
from octop.infra.projects.todo_comments import ProjectTodoCommentService
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


def _png_bytes() -> bytes:
    out = BytesIO()
    Image.new("RGB", (4, 4), "red").save(out, format="PNG")
    return out.getvalue()


async def test_image_only_comment_service_persists_private_bytes_and_rechecks_access(
    env: Any,
) -> None:
    ctx = await _base(env)
    todo = await _create_todo(ctx)
    service = ProjectTodoCommentService(ctx["srv"].services)
    from octop.infra.projects.todo_comments import CommentImageUpload

    created, fresh = service.post_comment(
        ctx["pid"],
        todo["todo_id"],
        user_id=ctx["member_uid"],
        body="",
        client_request_id=REQUEST_ID,
        images=[CommentImageUpload(_png_bytes(), "image/png")],
    )
    assert fresh is True
    assert created.body == ""
    assert len(created.images) == 1
    assert set(created.images[0]) == {"image_id", "media_type", "size_bytes", "position"}
    image_id = created.images[0]["image_id"]
    download = service.get_image(
        ctx["pid"],
        todo["todo_id"],
        created.comment_id,
        image_id,
        user_id=ctx["owner_uid"],
    )
    assert download.data == _png_bytes()
    assert download.media_type == "image/png"
    assert service.list_comments(ctx["pid"], todo["todo_id"], user_id=ctx["owner_uid"]).items == [
        created
    ]

    with pytest.raises(Exception) as denied:
        service.get_image(
            ctx["pid"],
            todo["todo_id"],
            created.comment_id,
            image_id,
            user_id=ctx["outsider_uid"],
        )
    assert getattr(denied.value, "status", None) == 404


async def test_image_quota_rejection_does_not_leave_bytes_comment_or_charge(
    env: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ctx = await _base(env)
    todo = await _create_todo(ctx)
    service = ProjectTodoCommentService(ctx["srv"].services)
    from octop.infra.projects.todo_comments import CommentImageUpload

    data = _png_bytes()
    monkeypatch.setattr(
        "octop.infra.db.repos.project_todo_comments.MAX_PROJECT_COMMENT_IMAGE_BYTES",
        len(data) - 1,
    )
    with pytest.raises(Exception) as rejected:
        service.post_comment(
            ctx["pid"],
            todo["todo_id"],
            user_id=ctx["member_uid"],
            body="",
            client_request_id=REQUEST_ID,
            images=[CommentImageUpload(data, "image/png")],
        )
    assert getattr(rejected.value, "status", None) == 409
    with ctx["srv"].services.db.connect() as conn:
        comments = conn.execute("SELECT COUNT(*) FROM project_todo_comments").fetchone()[0]
        images = conn.execute("SELECT COUNT(*) FROM project_todo_comment_images").fetchone()[0]
        usage = conn.execute(
            "SELECT used_bytes FROM project_todo_comment_image_usage WHERE project_id = ?",
            (ctx["pid"],),
        ).fetchone()[0]
    assert (comments, images, usage) == (0, 0, 0)
    assert not [path for path in service._storage.root.rglob("*") if path.is_file()]


async def test_image_event_failure_rolls_back_database_and_published_object(
    env: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ctx = await _base(env)
    todo = await _create_todo(ctx)
    service = ProjectTodoCommentService(ctx["srv"].services)
    from octop.infra.projects.todo_comments import CommentImageUpload

    def fail_event(*args: object) -> None:
        raise RuntimeError("injected event write failure")

    monkeypatch.setattr(
        "octop.infra.db.repos.project_todo_comments._append_comment_event", fail_event
    )
    with pytest.raises(RuntimeError, match="injected event write failure"):
        service.post_comment(
            ctx["pid"],
            todo["todo_id"],
            user_id=ctx["member_uid"],
            body="",
            client_request_id=REQUEST_ID,
            images=[CommentImageUpload(_png_bytes(), "image/png")],
        )
    with ctx["srv"].services.db.connect() as conn:
        counts = (
            conn.execute("SELECT COUNT(*) FROM project_todo_comments").fetchone()[0],
            conn.execute("SELECT COUNT(*) FROM project_todo_comment_images").fetchone()[0],
            conn.execute("SELECT COUNT(*) FROM project_todo_comment_image_usage").fetchone()[0],
        )
    assert counts == (0, 0, 0)
    assert not [path for path in service._storage.root.rglob("*") if path.is_file()]


def _path(ctx: dict[str, Any], todo_id: str, *, project_id: str | None = None) -> str:
    return f"/api/projects/{project_id or ctx['pid']}/todos/{todo_id}/comments"


async def test_multipart_image_comment_and_private_inline_get(env: Any) -> None:
    ctx = await _base(env)
    todo = await _create_todo(ctx)
    path = _path(ctx, todo["todo_id"])
    files = [
        ("client_request_id", (None, REQUEST_ID)),
        ("body", (None, "")),
        ("images", ("paw.png", _png_bytes(), "image/png")),
    ]
    first = await ctx["client"].post(path, headers=ctx["member_auth"], files=files)
    assert first.status_code == 201, first.text
    comment = first.json()
    assert comment["body"] == ""
    assert len(comment["images"]) == 1
    assert set(comment["images"][0]) == {"image_id", "media_type", "size_bytes", "position"}
    image_path = f"{path}/{comment['comment_id']}/images/{comment['images'][0]['image_id']}"
    image = await ctx["client"].get(image_path, headers=ctx["owner_auth"])
    assert image.status_code == 200, image.text
    assert image.content == _png_bytes()
    assert image.headers["content-type"] == "image/png"
    assert image.headers["cache-control"] == "private, no-store"
    assert image.headers["x-content-type-options"] == "nosniff"
    retry = await ctx["client"].post(path, headers=ctx["member_auth"], files=files)
    assert retry.status_code == 200, retry.text
    assert retry.json() == comment
    with ctx["srv"].services.db.connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM project_todo_comment_images").fetchone()[0] == 1
        assert conn.execute(
            "SELECT used_bytes FROM project_todo_comment_image_usage WHERE project_id = ?",
            (ctx["pid"],),
        ).fetchone()[0] == len(_png_bytes())
    store = ProjectTodoCommentService(ctx["srv"].services)._storage
    assert len([item for item in store.root.rglob("*") if item.is_file()]) == 1


async def test_multipart_limits_and_fixed_fields_reject_before_comment_write(env: Any) -> None:
    ctx = await _base(env)
    todo = await _create_todo(ctx)
    path = _path(ctx, todo["todo_id"])
    base = [("client_request_id", (None, REQUEST_ID))]
    cases = (
        (base + [("images", (f"{i}.png", _png_bytes(), "image/png")) for i in range(6)], 422),
        (base + [("images", ("oversize.png", b"x" * (8 * 1024 * 1024 + 1), "image/png"))], 413),
        (
            base
            + [
                ("images", ("a.png", b"x" * (7 * 1024 * 1024), "image/png")),
                ("images", ("b.png", b"x" * (7 * 1024 * 1024), "image/png")),
                ("images", ("c.png", b"x" * (6 * 1024 * 1024 + 1), "image/png")),
            ],
            413,
        ),
        (
            base
            + [("unknown", (None, "ignored")), ("images", ("x.png", _png_bytes(), "image/png"))],
            422,
        ),
        (base + [("images", ("fake.png", _png_bytes(), "image/jpeg"))], 422),
    )
    for files, expected_status in cases:
        response = await ctx["client"].post(path, headers=ctx["member_auth"], files=files)
        assert response.status_code == expected_status, (expected_status, response.text)
    with ctx["srv"].services.db.connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM project_todo_comments").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM project_todo_comment_images").fetchone()[0] == 0


async def test_image_get_checks_member_project_todo_comment_and_image_on_every_request(
    env: Any,
) -> None:
    ctx = await _base(env)
    todo = await _create_todo(ctx)
    other_todo = await _create_todo(ctx, title="另一待办")
    path = _path(ctx, todo["todo_id"])
    created = await ctx["client"].post(
        path,
        headers=ctx["member_auth"],
        files=[
            ("client_request_id", (None, REQUEST_ID)),
            ("images", ("paw.png", _png_bytes(), "image/png")),
        ],
    )
    assert created.status_code == 201, created.text
    comment = created.json()
    image_id = comment["images"][0]["image_id"]
    correct = f"{path}/{comment['comment_id']}/images/{image_id}"
    other_project = await ctx["client"].post(
        "/api/projects", headers=ctx["owner_auth"], json={"name": "另一个项目"}
    )
    assert other_project.status_code == 201, other_project.text
    wrong = (
        f"{_path(ctx, todo['todo_id'], project_id=other_project.json()['project_id'])}/{comment['comment_id']}/images/{image_id}",
        f"{_path(ctx, other_todo['todo_id'])}/{comment['comment_id']}/images/{image_id}",
        f"{path}/not-the-comment/images/{image_id}",
        f"{path}/{comment['comment_id']}/images/not-the-image",
    )
    for target in wrong:
        response = await ctx["client"].get(target, headers=ctx["owner_auth"])
        assert response.status_code == 404, (target, response.text)
    outsider = await ctx["client"].get(correct, headers=ctx["outsider_auth"])
    assert outsider.status_code == 404
    unauthorized_post = await ctx["client"].post(
        path, headers=ctx["outsider_auth"], files=[("unknown", (None, "x"))]
    )
    assert unauthorized_post.status_code == 404

    removed = ctx["srv"].services.project_repo.remove_member(
        project_id=ctx["pid"], user_id=ctx["member_uid"], actor_user_id=ctx["owner_uid"]
    )
    assert removed is not None
    revoked = await ctx["client"].get(correct, headers=ctx["member_auth"])
    assert revoked.status_code == 404
    still_owner = await ctx["client"].get(correct, headers=ctx["owner_auth"])
    assert still_owner.status_code == 200
    deleted = await ctx["client"].delete(
        f"/api/projects/{ctx['pid']}/todos/{todo['todo_id']}?expected_version={todo['version']}",
        headers=ctx["owner_auth"],
    )
    assert deleted.status_code == 204, deleted.text
    after_delete = await ctx["client"].get(correct, headers=ctx["owner_auth"])
    assert after_delete.status_code == 404


async def test_image_read_authorization_serializes_with_member_revocation(
    env: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An already-authorized read may finish; every read after revoke is 404."""
    ctx = await _base(env)
    todo = await _create_todo(ctx)
    service = ProjectTodoCommentService(ctx["srv"].services)
    from octop.infra.projects.todo_comments import CommentImageUpload

    comment, _ = service.post_comment(
        ctx["pid"],
        todo["todo_id"],
        user_id=ctx["member_uid"],
        body="",
        client_request_id=REQUEST_ID,
        images=[CommentImageUpload(_png_bytes(), "image/png")],
    )
    repo = ctx["srv"].services.project_todo_comment_repo
    original = repo._has_access
    checked = threading.Event()
    release = threading.Event()
    second_pool = open_database(ctx["srv"].config, ctx["srv"].services.paths)

    def pause_after_access(
        conn: Any, project_id: str, todo_id: str, user_id: int, *, write: bool
    ) -> bool:
        allowed = original(conn, project_id, todo_id, user_id, write=write)
        if user_id == ctx["member_uid"] and not write:
            checked.set()
            assert release.wait(timeout=10)
        return allowed

    monkeypatch.setattr(repo, "_has_access", pause_after_access)
    try:
        with ThreadPoolExecutor(max_workers=2) as workers:
            reading = workers.submit(
                service.get_image,
                ctx["pid"],
                todo["todo_id"],
                comment.comment_id,
                comment.images[0]["image_id"],
                user_id=ctx["member_uid"],
            )
            assert checked.wait(timeout=10)
            revoking = workers.submit(
                ProjectRepo(second_pool).remove_member,
                project_id=ctx["pid"],
                user_id=ctx["member_uid"],
                actor_user_id=ctx["owner_uid"],
            )
            time.sleep(0.1)
            assert not revoking.done(), "revocation bypassed the read's membership lock"
            release.set()
            assert reading.result(timeout=10).data == _png_bytes()
            assert revoking.result(timeout=10) is not None
    finally:
        release.set()
        second_pool.close()
    with pytest.raises(Exception) as denied:
        service.get_image(
            ctx["pid"],
            todo["todo_id"],
            comment.comment_id,
            comment.images[0]["image_id"],
            user_id=ctx["member_uid"],
        )
    assert getattr(denied.value, "status", None) == 404


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
