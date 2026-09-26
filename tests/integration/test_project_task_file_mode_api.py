"""Integration tests for 030A B3 ``mode='files'`` project-task API.

Covers the default-off gate and capability hint, 028/029 chat compatibility,
the quota/unsupported/unavailable error contract, one atomic owner bind with
N+1 compensation, leak-safe owner/shared/other-user projections, the owner-only
minimal internal Agent card, detach-keeps-the-task, and the dedicated
retryable complete delete (including failure → retry and generic-route
refusals). No paid model, live network, or user data is involved: the B2
runtime allocation is replaced by a narrow in-process fake that inserts the
real DB row and managed root.
"""

from __future__ import annotations

from typing import Any

import pytest

from octop.infra.db.repos.agents import PROJECT_TASK_FILES_OWNER_LIMIT
from octop.infra.errors import ErrorCode, OctopError
from octop.infra.projects import file_tasks
from octop.infra.projects.tasks import ProjectTaskService, instructions_sha256
from tests.support.auth import create_agent, create_user, resolve_user_id

OWNER = "ptf_owner"
MEMBER = "ptf_member"
OTHER = "ptf_other"
OUTSIDER = "ptf_outsider"

INSTRUCTIONS = "文件任务指令-030A：只处理本任务目录。"


def _surface(monkeypatch: pytest.MonkeyPatch) -> None:
    """Open the B3 files gate for one test only."""
    monkeypatch.setattr(file_tasks, "PROJECT_TASK_FILES_MODE_ENABLED", True)


async def _base(env: Any) -> dict[str, Any]:
    client, srv, admin_auth = env
    owner_auth = await create_user(client, admin_auth, username=OWNER)
    member_auth = await create_user(client, admin_auth, username=MEMBER)
    other_auth = await create_user(client, admin_auth, username=OTHER)
    outsider_auth = await create_user(client, admin_auth, username=OUTSIDER)
    uids = {
        "owner": await resolve_user_id(client, admin_auth, OWNER),
        "member": await resolve_user_id(client, admin_auth, MEMBER),
        "other": await resolve_user_id(client, admin_auth, OTHER),
        "outsider": await resolve_user_id(client, admin_auth, OUTSIDER),
    }
    r = await client.post(
        "/api/projects",
        headers=owner_auth,
        json={"name": "文件任务项目", "instructions": INSTRUCTIONS},
    )
    assert r.status_code == 201, r.text
    pid = r.json()["project_id"]
    srv.services.project_repo.add_member(pid, uids["member"], role="member")
    srv.services.project_repo.add_member(pid, uids["other"], role="member")
    source = await create_agent(client, owner_auth, name="ptf-source-expert")
    with srv.services.db.transaction() as conn:
        conn.execute("UPDATE agents SET is_shared = 1 WHERE agent_id = ?", (source,))
    return {
        "client": client,
        "srv": srv,
        "admin_auth": admin_auth,
        "auth": {
            "owner": owner_auth,
            "member": member_auth,
            "other": other_auth,
            "outsider": outsider_auth,
        },
        "uid": uids,
        "pid": pid,
        "source": source,
    }


def _install_runtime_fake(ctx: dict[str, Any], monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """Replace B2 allocation with a narrow fake: real row + real managed root."""
    srv = ctx["srv"]
    manager = srv.app_runtime.agent_registry
    created: list[str] = []

    async def fake_create(*, owner_user_id: int, source_expert: Any) -> Any:
        index = len(created) + 1
        agent_id = f"ptffake{index:012d}"
        srv.services.agent_repo.create_project_task_runtime_with_quota(
            user_id=owner_user_id,
            agent_id=agent_id,
            name=f"task-file-fake-{index:03d}",
            default_model=source_expert.default_model,
            system_prompt=source_expert.system_prompt,
        )
        root = srv.services.paths.ensure_project_task_file_runtime_dir(agent_id)
        (root / "seed.txt").write_text("private bytes", encoding="utf-8")
        created.append(agent_id)
        return srv.services.agent_repo.get(agent_id)

    monkeypatch.setattr(manager, "create_project_task_file_runtime", fake_create)
    ctx["manager"] = manager
    ctx["created_runtimes"] = created
    return ctx


def _count(srv: Any, table: str, where: str, params: tuple[object, ...]) -> int:
    with srv.services.db.connect() as conn:
        return int(
            conn.execute(f"SELECT COUNT(*) AS n FROM {table} WHERE {where}", params).fetchone()["n"]
        )


def _digest() -> str:
    return instructions_sha256(INSTRUCTIONS)


async def _create_files(ctx: dict[str, Any], *, role: str = "member", **overrides: Any) -> Any:
    body: dict[str, Any] = {
        "agent_id": ctx["source"],
        "expected_instructions_sha256": _digest(),
        "mode": "files",
    }
    body.update(overrides)
    return await ctx["client"].post(
        f"/api/projects/{ctx['pid']}/tasks",
        headers=ctx["auth"][role],
        json=body,
    )


async def _create_chat(
    ctx: dict[str, Any], *, role: str = "member", agent: str | None = None
) -> Any:
    return await ctx["client"].post(
        f"/api/projects/{ctx['pid']}/tasks",
        headers=ctx["auth"][role],
        json={
            "agent_id": agent or ctx["source"],
            "expected_instructions_sha256": _digest(),
        },
    )


def _share(srv: Any, thread_id: str, actor_uid: int, grantee_uid: int, pid: str) -> None:
    srv.services.project_task_share_repo.grant(
        project_id=pid,
        thread_id=thread_id,
        actor_user_id=actor_uid,
        grantee_user_id=grantee_uid,
    )


def _set_archived(srv: Any, pid: str) -> None:
    with srv.services.db.transaction() as conn:
        conn.execute("UPDATE project_spaces SET archived = 1 WHERE project_id = ?", (pid,))


# ---------------------------------------------------------------------------
# Gate + capability + chat compatibility
# ---------------------------------------------------------------------------


async def test_files_mode_is_closed_by_default_with_capability_hint(
    env_with_provider: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    ctx = await _base(env_with_provider)
    client = ctx["client"]

    r = await client.get("/api/projects/task-capabilities", headers=ctx["auth"]["member"])
    assert r.status_code == 200, r.text
    assert r.json() == {"files": {"available": False, "reason": "disabled"}}

    r = await _create_files(ctx)
    assert r.status_code == 422, r.text
    assert r.json()["error"]["code"] == "PROJECT_TASK_FILES_UNSUPPORTED"
    # The closed gate is refused before any runtime/thread/link is written.
    assert _count(ctx["srv"], "project_task_links", "project_id = ?", (ctx["pid"],)) == 0

    _surface(monkeypatch)
    r = await client.get("/api/projects/task-capabilities", headers=ctx["auth"]["member"])
    assert r.status_code == 200, r.text
    assert r.json() == {"files": {"available": True, "reason": None}}


async def test_omitted_mode_keeps_028_chat_behavior(
    env_with_provider: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    _surface(monkeypatch)
    ctx = await _base(env_with_provider)
    r = await _create_chat(ctx)
    assert r.status_code == 201, r.text
    payload = r.json()
    assert payload["mode"] == "chat"
    assert payload["agent_id"] == ctx["source"]
    assert payload["chat_agent_id"] == ctx["source"]
    assert payload["source_expert_id"] is None


# ---------------------------------------------------------------------------
# 030A error contract and refusal side effects
# ---------------------------------------------------------------------------


async def test_files_refusals_leave_no_rows(
    env_with_provider: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    _surface(monkeypatch)
    ctx = await _base(env_with_provider)
    srv = ctx["srv"]

    # Outsider/unknown project share one 404.
    r = await _create_files(ctx, role="outsider")
    assert r.status_code == 404, r.text
    assert r.json()["error"]["code"] == "NOT_FOUND"

    # Archived project → 403.
    _set_archived(srv, ctx["pid"])
    r = await _create_files(ctx)
    assert r.status_code == 403, r.text
    assert r.json()["error"]["code"] == "FORBIDDEN"
    with srv.services.db.transaction() as conn:
        conn.execute("UPDATE project_spaces SET archived = 0 WHERE project_id = ?", (ctx["pid"],))

    # Stale digest → 409 with no runtime allocated.
    r = await _create_files(ctx, expected_instructions_sha256=instructions_sha256("stale"))
    assert r.status_code == 409, r.text
    assert r.json()["error"]["code"] == "PROJECT_INSTRUCTIONS_CHANGED"
    assert _count(srv, "threads", "user_id = ?", (ctx["uid"]["member"],)) == 0
    assert srv.services.agent_repo.count_project_task_runtimes(user_id=ctx["uid"]["member"]) == 0


async def test_files_quota_is_409_owner_scope_before_any_runtime(
    env_with_provider: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    _surface(monkeypatch)
    ctx = await _base(env_with_provider)
    srv = ctx["srv"]
    member_uid = ctx["uid"]["member"]
    for i in range(PROJECT_TASK_FILES_OWNER_LIMIT):
        srv.services.agent_repo.create_project_task_runtime_with_quota(
            user_id=member_uid, agent_id=f"ptfq{i:013d}", name=f"task-file-q-{i:03d}"
        )

    r = await _create_files(ctx)
    assert r.status_code == 409, r.text
    body = r.json()["error"]
    assert body["code"] == "PROJECT_TASK_FILES_QUOTA"
    assert body["details"] == {"scope": "owner"}
    assert _count(srv, "threads", "user_id = ?", (member_uid,)) == 0


async def test_runtime_start_failure_maps_503_without_rows(
    env_with_provider: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    _surface(monkeypatch)
    ctx = await _base(env_with_provider)
    srv = ctx["srv"]
    manager = srv.app_runtime.agent_registry

    async def boom(**_kwargs: Any) -> Any:
        raise OctopError(ErrorCode.AGENT_FAILED, "start failed")

    monkeypatch.setattr(manager, "create_project_task_file_runtime", boom)
    r = await _create_files(ctx)
    assert r.status_code == 503, r.text
    assert r.json()["error"]["code"] == "PROJECT_TASK_FILES_UNAVAILABLE"
    assert _count(srv, "threads", "user_id = ?", (ctx["uid"]["member"],)) == 0
    assert srv.services.agent_repo.count_project_task_runtimes(user_id=ctx["uid"]["member"]) == 0


async def test_bind_failure_compensates_the_created_runtime(
    env_with_provider: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    _surface(monkeypatch)
    ctx = _install_runtime_fake(await _base(env_with_provider), monkeypatch)
    srv = ctx["srv"]

    def boom(*_args: Any, **_kwargs: Any) -> Any:
        raise OctopError(ErrorCode.PROJECT_TASK_FILES_UNAVAILABLE, "bind lost the race")

    monkeypatch.setattr(ProjectTaskService, "bind_files_task", boom)
    r = await _create_files(ctx)
    assert r.status_code == 503, r.text
    runtime_id = ctx["created_runtimes"][0]
    # Compensation removed the fresh runtime row AND its private root; a
    # partial cleanup would have kept the row as a retryable marker instead.
    assert srv.services.agent_repo.get(runtime_id) is None
    assert not srv.services.paths.project_task_file_runtime_dir(runtime_id).exists()
    assert _count(srv, "threads", "user_id = ?", (ctx["uid"]["member"],)) == 0


# ---------------------------------------------------------------------------
# Owner projection, shared projection, and the minimal Agent card
# ---------------------------------------------------------------------------


async def test_files_bind_owner_and_shared_projection_is_leak_safe(
    env_with_provider: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    _surface(monkeypatch)
    ctx = _install_runtime_fake(await _base(env_with_provider), monkeypatch)
    srv = ctx["srv"]
    client = ctx["client"]
    r = await _create_files(ctx)
    assert r.status_code == 201, r.text
    payload = r.json()
    tid = payload["thread_id"]
    runtime_id = payload["chat_agent_id"]
    assert runtime_id == ctx["created_runtimes"][0]
    assert payload["mode"] == "files"
    assert payload["agent_id"] == ctx["source"]
    assert payload["source_expert_id"] == ctx["source"]
    assert payload["access"] == "owner"

    # Owner list/detail expose the runtime binding only to the owner.
    r = await client.get(f"/api/projects/{ctx['pid']}/tasks", headers=ctx["auth"]["member"])
    assert r.status_code == 200, r.text
    card = r.json()["items"][0]
    assert card["chat_agent_id"] == runtime_id and card["source_expert_id"] == ctx["source"]

    # Other members never see it; outsiders get a uniform 404.
    r = await client.get(f"/api/projects/{ctx['pid']}/tasks", headers=ctx["auth"]["other"])
    assert r.status_code == 200 and r.json()["items"] == []
    r = await client.get(f"/api/projects/{ctx['pid']}/tasks/{tid}", headers=ctx["auth"]["other"])
    assert r.status_code == 404
    r = await client.get(f"/api/projects/{ctx['pid']}/tasks/{tid}", headers=ctx["auth"]["outsider"])
    assert r.status_code == 404

    # Reader card: mode only, source expert as agent_id, runtime/source NULL.
    _share(srv, tid, ctx["uid"]["member"], ctx["uid"]["other"], ctx["pid"])
    r = await client.get(
        f"/api/projects/{ctx['pid']}/tasks?scope=shared", headers=ctx["auth"]["other"]
    )
    assert r.status_code == 200, r.text
    reader = r.json()["items"][0]
    assert reader["mode"] == "files"
    assert reader["agent_id"] == ctx["source"]
    assert reader["chat_agent_id"] is None
    assert reader["source_expert_id"] is None
    r = await client.get(
        f"/api/projects/{ctx['pid']}/tasks?scope=all", headers=ctx["auth"]["other"]
    )
    merged = r.json()["items"][0]
    assert merged["chat_agent_id"] is None and merged["source_expert_id"] is None

    # Owner merged card keeps the binding.
    r = await client.get(
        f"/api/projects/{ctx['pid']}/tasks?scope=all", headers=ctx["auth"]["member"]
    )
    own = r.json()["items"][0]
    assert own["chat_agent_id"] == runtime_id


async def test_owner_gets_minimal_internal_agent_card_others_do_not(
    env_with_provider: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    _surface(monkeypatch)
    ctx = _install_runtime_fake(await _base(env_with_provider), monkeypatch)
    client = ctx["client"]
    r = await _create_files(ctx)
    assert r.status_code == 201, r.text
    runtime_id = r.json()["chat_agent_id"]

    r = await client.get("/api/agents", headers=ctx["auth"]["member"])
    assert r.status_code == 200, r.text
    cards = [a for a in r.json() if a["agent_id"] == runtime_id]
    assert len(cards) == 1
    card = cards[0]
    assert card["internal"] is True
    assert card["is_owner"] is True
    assert card["state"] in {"running", "stopped", "created", "failed", "unknown"}
    for leaked in ("config", "system_prompt", "default_model", "mcp_servers"):
        assert leaked not in card
    # The minimal card is appended after ordinary agents, never first.
    assert r.json()[-1]["agent_id"] == runtime_id

    for role in ("other", "outsider"):
        r = await client.get("/api/agents", headers=ctx["auth"][role])
        assert runtime_id not in r.text

    r = await client.get("/api/agents?scope=all", headers=ctx["admin_auth"])
    assert r.status_code == 200, r.text
    assert runtime_id not in r.text

    # Ordinary management surface refuses the internal runtime.
    for method, path in (
        ("get", f"/api/agents/{runtime_id}"),
        ("delete", f"/api/agents/{runtime_id}"),
        ("post", f"/api/agents/{runtime_id}/start"),
        ("post", f"/api/agents/{runtime_id}/stop"),
        ("post", f"/api/agents/{runtime_id}/reload"),
    ):
        resp = await getattr(client, method)(path, headers=ctx["auth"]["member"])
        assert resp.status_code == 403, (method, path, resp.text)
        assert resp.json()["error"]["code"] == "FORBIDDEN"


# ---------------------------------------------------------------------------
# Detach vs. dedicated complete delete
# ---------------------------------------------------------------------------


async def test_detach_keeps_task_then_dedicated_delete_completes(
    env_with_provider: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    _surface(monkeypatch)
    ctx = _install_runtime_fake(await _base(env_with_provider), monkeypatch)
    srv, client = ctx["srv"], ctx["client"]
    r = await _create_files(ctx)
    assert r.status_code == 201, r.text
    tid = r.json()["thread_id"]
    runtime_id = r.json()["chat_agent_id"]
    root = srv.services.paths.project_task_file_runtime_dir(runtime_id)
    assert root.exists()

    # Project detach is detach-only: thread + runtime + bytes survive.
    r = await client.delete(
        f"/api/projects/{ctx['pid']}/tasks/{tid}", headers=ctx["auth"]["member"]
    )
    assert r.status_code == 204, r.text
    assert _count(srv, "project_task_links", "thread_id = ?", (tid,)) == 0
    assert srv.services.thread_repo.get(tid) is not None
    assert srv.services.agent_repo.get(runtime_id) is not None
    assert root.exists()

    # Dedicated delete is independent of project membership and completes.
    r = await client.delete(f"/api/project-task-files/{tid}", headers=ctx["auth"]["member"])
    assert r.status_code == 204, r.text
    assert srv.services.thread_repo.get(tid) is None
    assert srv.services.agent_repo.get(runtime_id) is None
    assert not root.exists()
    assert _count(srv, "sessions", "thread_id = ?", (tid,)) == 0
    assert _count(srv, "project_task_contexts", "thread_id = ?", (tid,)) == 0

    # Second complete delete and non-owner attempts are uniform 404s.
    r = await client.delete(f"/api/project-task-files/{tid}", headers=ctx["auth"]["member"])
    assert r.status_code == 404, r.text


async def test_dedicated_delete_refuses_non_owner_and_generic_routes(
    env_with_provider: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    _surface(monkeypatch)
    ctx = _install_runtime_fake(await _base(env_with_provider), monkeypatch)
    srv, client = ctx["srv"], ctx["client"]
    r = await _create_files(ctx)
    tid = r.json()["thread_id"]
    runtime_id = r.json()["chat_agent_id"]

    for role in ("other", "outsider"):
        resp = await client.delete(f"/api/project-task-files/{tid}", headers=ctx["auth"][role])
        assert resp.status_code == 404, (role, resp.text)
    assert srv.services.thread_repo.get(tid) is not None

    # Generic Agent delete refuses the internal runtime.
    resp = await client.delete(f"/api/agents/{runtime_id}", headers=ctx["auth"]["member"])
    assert resp.status_code == 403, resp.text
    assert resp.json()["error"]["code"] == "FORBIDDEN"
    assert srv.services.agent_repo.get(runtime_id) is not None

    # Generic thread delete refuses and points at the dedicated route.
    resp = await client.delete(
        f"/api/agents/{runtime_id}/threads/{tid}", headers=ctx["auth"]["member"]
    )
    assert resp.status_code == 403, resp.text
    assert resp.json()["error"]["details"]["route"] == f"/api/project-task-files/{tid}"
    assert srv.services.thread_repo.get(tid) is not None

    # The dedicated route still completes after the refusals.
    resp = await client.delete(f"/api/project-task-files/{tid}", headers=ctx["auth"]["member"])
    assert resp.status_code == 204, resp.text


async def test_dedicated_delete_failure_is_retryable_never_false_204(
    env_with_provider: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    import octop.infra.agents.manager as manager_mod

    _surface(monkeypatch)
    ctx = _install_runtime_fake(await _base(env_with_provider), monkeypatch)
    srv, client = ctx["srv"], ctx["client"]
    r = await _create_files(ctx)
    tid = r.json()["thread_id"]
    runtime_id = r.json()["chat_agent_id"]
    root = srv.services.paths.project_task_file_runtime_dir(runtime_id)

    original = manager_mod.shutil.rmtree

    def boom(*_args: Any, **_kwargs: Any) -> None:
        raise OSError("disk busy")

    monkeypatch.setattr(manager_mod.shutil, "rmtree", boom)
    resp = await client.delete(f"/api/project-task-files/{tid}", headers=ctx["auth"]["member"])
    assert resp.status_code == 503, resp.text
    assert resp.json()["error"]["code"] == "PROJECT_TASK_FILES_UNAVAILABLE"
    # Retryable state: the thread, link, runtime and bytes all survive.
    assert srv.services.thread_repo.get(tid) is not None
    assert srv.services.agent_repo.get(runtime_id) is not None
    assert root.exists()

    monkeypatch.setattr(manager_mod.shutil, "rmtree", original)
    resp = await client.delete(f"/api/project-task-files/{tid}", headers=ctx["auth"]["member"])
    assert resp.status_code == 204, resp.text
    assert srv.services.thread_repo.get(tid) is None
    assert srv.services.agent_repo.get(runtime_id) is None
    assert not root.exists()
