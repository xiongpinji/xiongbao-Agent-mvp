"""Integration tests for POST /api/projects/{id}/tasks (028 core create).

PS-05C/PS-07A slice 1: a project member creates a brand-new private Dashboard
DM task whose thread, history projection, ``source='project'`` link, and
immutable project-instruction snapshot commit in ONE transaction. Covers the
authenticated create route, the digest exposed on the member-visible project
detail (never on the list), the stale-digest 409 with zero side effects, the
uniform 404 for outsiders / unknown projects, archived-project and team-host
refusals, the running-agent requirement, owner-private visibility, manual
attach staying snapshot-free, and detach / member-removal cascades that keep
the owner's original thread.
"""

from __future__ import annotations

import json
from typing import Any

from octop.infra.projects.tasks import instructions_sha256
from tests.support.auth import create_agent, create_user, resolve_user_id

OWNER = "ptc_owner"
MEMBER = "ptc_member"
MEMBER2 = "ptc_member2"
OUTSIDER = "ptc_outsider"

INSTRUCTIONS = "项目机密指令-028：先读 SOUL.md，再输出周报。"
CHANGED_INSTRUCTIONS = "项目指令改版-028：先读 README。"

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
}


async def _base(env: Any) -> dict[str, Any]:
    """Owner + two members + outsider, one project, per-user running agents."""
    client, srv, admin_auth = env
    owner_auth = await create_user(client, admin_auth, username=OWNER)
    member_auth = await create_user(client, admin_auth, username=MEMBER)
    member2_auth = await create_user(client, admin_auth, username=MEMBER2)
    outsider_auth = await create_user(client, admin_auth, username=OUTSIDER)
    uids = {
        "owner": await resolve_user_id(client, admin_auth, OWNER),
        "member": await resolve_user_id(client, admin_auth, MEMBER),
        "member2": await resolve_user_id(client, admin_auth, MEMBER2),
        "outsider": await resolve_user_id(client, admin_auth, OUTSIDER),
    }
    r = await client.post(
        "/api/projects",
        headers=owner_auth,
        json={"name": "创建任务项目", "instructions": INSTRUCTIONS},
    )
    assert r.status_code == 201, r.text
    pid = r.json()["project_id"]
    srv.services.project_repo.add_member(pid, uids["member"], role="member")
    srv.services.project_repo.add_member(pid, uids["member2"], role="member")
    agents = {
        "owner": await create_agent(client, owner_auth, name="ptc-owner-bot"),
        "member": await create_agent(client, member_auth, name="ptc-member-bot"),
        "member2": await create_agent(client, member2_auth, name="ptc-member2-bot"),
        "outsider": await create_agent(client, outsider_auth, name="ptc-outsider-bot"),
    }
    return {
        "client": client,
        "srv": srv,
        "admin_auth": admin_auth,
        "auth": {
            "owner": owner_auth,
            "member": member_auth,
            "member2": member2_auth,
            "outsider": outsider_auth,
        },
        "pid": pid,
        "uids": uids,
        "agents": agents,
    }


async def _detail(ctx: dict[str, Any], role: str = "member") -> dict[str, Any]:
    r = await ctx["client"].get(f"/api/projects/{ctx['pid']}", headers=ctx["auth"][role])
    assert r.status_code == 200, r.text
    return r.json()


async def _create(
    ctx: dict[str, Any],
    *,
    role: str = "member",
    agent_id: str | None = None,
    digest: str | None = None,
    pid: str | None = None,
    body: dict[str, Any] | None = None,
) -> Any:
    if body is None:
        body = {
            "agent_id": agent_id or ctx["agents"][role],
            "expected_instructions_sha256": digest or instructions_sha256(INSTRUCTIONS),
        }
    return await ctx["client"].post(
        f"/api/projects/{pid or ctx['pid']}/tasks",
        headers=ctx["auth"][role],
        json=body,
    )


def _count(srv: Any, table: str, where: str, params: tuple[object, ...]) -> int:
    with srv.services.db.connect() as conn:
        return int(
            conn.execute(f"SELECT COUNT(*) AS n FROM {table} WHERE {where}", params).fetchone()["n"]
        )


def _session_key(agent_id: str, uid: int) -> str:
    return f"{agent_id}:dashboard:{uid}:dm"


def _assert_no_create_rows(srv: Any, agent_id: str, uid: int) -> None:
    key = _session_key(agent_id, uid)
    assert _count(srv, "threads", "session_key = ?", (key,)) == 0
    assert _count(srv, "project_task_links", "owner_user_id = ?", (uid,)) == 0
    assert _count(srv, "project_task_contexts", "owner_user_id = ?", (uid,)) == 0


def _events_blob(srv: Any, pid: str) -> str:
    with srv.services.db.connect() as conn:
        rows = conn.execute(
            "SELECT event_type, object_id, payload_json FROM project_events WHERE project_id = ?",
            (pid,),
        ).fetchall()
    return json.dumps([dict(r) for r in rows], ensure_ascii=False, default=str)


# ---------------------------------------------------------------------------
# Happy path + digest surface
# ---------------------------------------------------------------------------


async def test_member_create_commits_thread_link_snapshot_in_one_transaction(
    env_with_provider: Any,
) -> None:
    ctx = await _base(env_with_provider)
    detail = await _detail(ctx)
    assert detail["instructions"] == INSTRUCTIONS
    assert detail["instructions_sha256"] == instructions_sha256(INSTRUCTIONS)

    r = await _create(ctx, digest=detail["instructions_sha256"])
    assert r.status_code == 201, r.text
    payload = r.json()
    assert set(payload) == _SUMMARY_KEYS
    assert payload["project_id"] == ctx["pid"]
    assert payload["owner_user_id"] == ctx["uids"]["member"]
    assert payload["agent_id"] == ctx["agents"]["member"]
    assert payload["source"] == "project"
    assert payload["title"] is None
    assert payload["access"] == "owner"
    # The response is a safe summary only: never the project instruction text.
    assert INSTRUCTIONS not in r.text
    for forbidden in ("session_key", "instructions_snapshot", "instructions"):
        assert forbidden not in payload

    tid = payload["thread_id"]
    uid = ctx["uids"]["member"]
    agent_id = ctx["agents"]["member"]
    key = _session_key(agent_id, uid)
    with ctx["srv"].services.db.connect() as conn:
        thread = conn.execute(
            "SELECT agent_id, user_id, channel_type, session_key, title, last_active "
            "FROM threads WHERE thread_id = ?",
            (tid,),
        ).fetchone()
        link = conn.execute(
            "SELECT project_id, owner_user_id, source FROM project_task_links WHERE thread_id = ?",
            (tid,),
        ).fetchone()
        ctx_row = conn.execute(
            "SELECT * FROM project_task_contexts WHERE thread_id = ?", (tid,)
        ).fetchone()
        projection = conn.execute(
            "SELECT status FROM thread_history_projection WHERE thread_id = ?", (tid,)
        ).fetchone()
        session = conn.execute(
            "SELECT COUNT(*) AS n FROM sessions WHERE session_key = ?", (key,)
        ).fetchone()
    assert thread is not None
    assert str(thread["agent_id"]) == agent_id
    assert int(thread["user_id"]) == uid
    assert str(thread["channel_type"]) == "dashboard"
    assert str(thread["session_key"]) == key
    assert int(thread["last_active"]) == 0
    assert link is not None
    assert str(link["project_id"]) == ctx["pid"]
    assert int(link["owner_user_id"]) == uid
    assert str(link["source"]) == "project"
    assert ctx_row is not None
    assert str(ctx_row["instructions_snapshot"]) == INSTRUCTIONS
    assert int(ctx_row["snapshot_version"]) == 1
    assert str(ctx_row["instructions_sha256"]) == instructions_sha256(INSTRUCTIONS)
    assert int(ctx_row["captured_at"]) > 0
    assert projection is not None and str(projection["status"]) == "ready"
    assert int(session["n"]) == 0  # no reset/rebind of the active session
    # No shared project event may leak the private thread id.
    blob = _events_blob(ctx["srv"], ctx["pid"])
    assert tid not in blob


async def test_project_detail_exposes_digest_but_list_never_does(
    env_with_provider: Any,
) -> None:
    ctx = await _base(env_with_provider)
    detail = await _detail(ctx)
    assert detail["instructions_sha256"] == instructions_sha256(INSTRUCTIONS)

    r = await ctx["client"].get("/api/projects", headers=ctx["auth"]["member"])
    assert r.status_code == 200, r.text
    item = next(i for i in r.json()["items"] if i["project_id"] == ctx["pid"])
    assert "instructions" not in item
    assert "instructions_sha256" not in item

    r = await ctx["client"].patch(
        f"/api/projects/{ctx['pid']}",
        headers=ctx["auth"]["owner"],
        json={"instructions": CHANGED_INSTRUCTIONS},
    )
    assert r.status_code == 200, r.text
    assert r.json()["instructions_sha256"] == instructions_sha256(CHANGED_INSTRUCTIONS)
    detail = await _detail(ctx)
    assert detail["instructions"] == CHANGED_INSTRUCTIONS
    assert detail["instructions_sha256"] == instructions_sha256(CHANGED_INSTRUCTIONS)


# ---------------------------------------------------------------------------
# Conflicts / refusals with zero side effects
# ---------------------------------------------------------------------------


async def test_stale_digest_is_409_with_no_rows_then_new_digest_succeeds(
    env_with_provider: Any,
) -> None:
    ctx = await _base(env_with_provider)
    stale = instructions_sha256(INSTRUCTIONS)
    r = await ctx["client"].patch(
        f"/api/projects/{ctx['pid']}",
        headers=ctx["auth"]["owner"],
        json={"instructions": CHANGED_INSTRUCTIONS},
    )
    assert r.status_code == 200, r.text

    r = await _create(ctx, digest=stale)
    assert r.status_code == 409, r.text
    assert r.json()["error"]["code"] == "PROJECT_INSTRUCTIONS_CHANGED"
    assert INSTRUCTIONS not in r.text and CHANGED_INSTRUCTIONS not in r.text
    _assert_no_create_rows(ctx["srv"], ctx["agents"]["member"], ctx["uids"]["member"])

    # The frontend flow: refresh the detail, confirm the new digest, retry.
    fresh = (await _detail(ctx))["instructions_sha256"]
    r = await _create(ctx, digest=fresh)
    assert r.status_code == 201, r.text
    assert r.json()["source"] == "project"


async def test_outsider_unknown_project_and_unknown_agent_are_uniform_404(
    env_with_provider: Any,
) -> None:
    ctx = await _base(env_with_provider)
    digest = (await _detail(ctx))["instructions_sha256"]

    r = await _create(ctx, role="outsider", digest=digest)
    assert r.status_code == 404, r.text
    assert r.json()["error"]["code"] == "NOT_FOUND"

    r = await _create(ctx, pid="01GHOSTPROJECT028", digest=digest)
    assert r.status_code == 404, r.text
    assert r.json()["error"]["code"] == "NOT_FOUND"

    r = await _create(ctx, agent_id="ghost-agent", digest=digest)
    assert r.status_code == 404, r.text
    assert r.json()["error"]["code"] == "AGENT_NOT_FOUND"

    # A member may not use a fellow member's private agent.
    r = await _create(ctx, agent_id=ctx["agents"]["owner"], digest=digest)
    assert r.status_code == 403, r.text
    assert r.json()["error"]["code"] == "FORBIDDEN"
    _assert_no_create_rows(ctx["srv"], ctx["agents"]["member"], ctx["uids"]["member"])


async def test_archived_project_refuses_create_without_rows(env_with_provider: Any) -> None:
    ctx = await _base(env_with_provider)
    digest = (await _detail(ctx))["instructions_sha256"]
    with ctx["srv"].services.db.transaction() as conn:
        conn.execute("UPDATE project_spaces SET archived = 1 WHERE project_id = ?", (ctx["pid"],))

    r = await _create(ctx, digest=digest)
    assert r.status_code == 403, r.text
    assert r.json()["error"]["code"] == "FORBIDDEN"
    _assert_no_create_rows(ctx["srv"], ctx["agents"]["member"], ctx["uids"]["member"])


async def test_team_host_is_rejected_before_entering_inheritance(
    env_with_provider: Any,
) -> None:
    ctx = await _base(env_with_provider)
    digest = (await _detail(ctx))["instructions_sha256"]
    with ctx["srv"].services.db.transaction() as conn:
        conn.execute(
            "UPDATE agents SET kind = 'team' WHERE agent_id = ?", (ctx["agents"]["member"],)
        )

    r = await _create(ctx, digest=digest)
    assert r.status_code == 403, r.text
    assert r.json()["error"]["code"] == "FORBIDDEN"
    assert INSTRUCTIONS not in r.text
    _assert_no_create_rows(ctx["srv"], ctx["agents"]["member"], ctx["uids"]["member"])


async def test_stopped_agent_is_refused_without_rows(env_with_provider: Any) -> None:
    ctx = await _base(env_with_provider)
    digest = (await _detail(ctx))["instructions_sha256"]
    r = await ctx["client"].post(
        f"/api/agents/{ctx['agents']['member']}/stop", headers=ctx["auth"]["member"]
    )
    assert r.status_code == 204, r.text

    r = await _create(ctx, digest=digest)
    assert r.status_code == 409, r.text
    assert r.json()["error"]["code"] == "AGENT_NOT_RUNNING"
    _assert_no_create_rows(ctx["srv"], ctx["agents"]["member"], ctx["uids"]["member"])


# ---------------------------------------------------------------------------
# Private task ACL and unchanged manual attach
# ---------------------------------------------------------------------------


async def test_created_task_stays_owner_private(env_with_provider: Any) -> None:
    ctx = await _base(env_with_provider)
    digest = (await _detail(ctx))["instructions_sha256"]
    r = await _create(ctx, digest=digest)
    assert r.status_code == 201, r.text
    tid = r.json()["thread_id"]

    for role in ("owner", "member2"):
        r = await ctx["client"].get(f"/api/projects/{ctx['pid']}/tasks", headers=ctx["auth"][role])
        assert r.status_code == 200, r.text
        assert tid not in r.text
        r = await ctx["client"].get(
            f"/api/projects/{ctx['pid']}/tasks/{tid}", headers=ctx["auth"][role]
        )
        assert r.status_code == 404, r.text

    # Project membership never relaxes the thread-level history ACL.
    r = await ctx["client"].get(
        f"/api/agents/{ctx['agents']['member']}/threads/{tid}/history",
        headers=ctx["auth"]["member2"],
    )
    assert r.status_code == 403, r.text
    r = await ctx["client"].get(
        f"/api/agents/{ctx['agents']['member']}/threads/{tid}/history",
        headers=ctx["auth"]["member"],
    )
    assert r.status_code == 200, r.text


async def test_manual_attach_still_works_and_stays_snapshot_free(
    env_with_provider: Any,
) -> None:
    ctx = await _base(env_with_provider)
    r = await ctx["client"].post(
        f"/api/agents/{ctx['agents']['member']}/threads", headers=ctx["auth"]["member"]
    )
    assert r.status_code == 201, r.text
    tid = r.json()["thread_id"]

    r = await ctx["client"].post(
        f"/api/projects/{ctx['pid']}/tasks/links",
        headers=ctx["auth"]["member"],
        json={"thread_id": tid},
    )
    assert r.status_code == 201, r.text
    assert r.json()["source"] == "manual"
    assert _count(ctx["srv"], "project_task_contexts", "thread_id = ?", (tid,)) == 0


async def test_detach_and_member_removal_keep_the_owner_thread(
    env_with_provider: Any,
) -> None:
    ctx = await _base(env_with_provider)
    digest = (await _detail(ctx))["instructions_sha256"]
    uid = ctx["uids"]["member"]
    agent_id = ctx["agents"]["member"]

    r = await _create(ctx, digest=digest)
    assert r.status_code == 201, r.text
    tid = r.json()["thread_id"]
    r = await ctx["client"].delete(
        f"/api/projects/{ctx['pid']}/tasks/{tid}", headers=ctx["auth"]["member"]
    )
    assert r.status_code == 204, r.text
    assert _count(ctx["srv"], "project_task_links", "thread_id = ?", (tid,)) == 0
    assert _count(ctx["srv"], "project_task_contexts", "thread_id = ?", (tid,)) == 0
    r = await ctx["client"].get(
        f"/api/agents/{agent_id}/threads/{tid}/history", headers=ctx["auth"]["member"]
    )
    assert r.status_code == 200, r.text

    # Second task, then the owner removes the member.
    r = await _create(ctx, digest=digest)
    assert r.status_code == 201, r.text
    tid2 = r.json()["thread_id"]
    r = await ctx["client"].delete(
        f"/api/projects/{ctx['pid']}/members/{uid}", headers=ctx["auth"]["owner"]
    )
    assert r.status_code == 204, r.text
    assert _count(ctx["srv"], "project_task_links", "thread_id = ?", (tid2,)) == 0
    assert _count(ctx["srv"], "project_task_contexts", "thread_id = ?", (tid2,)) == 0
    r = await ctx["client"].get(
        f"/api/agents/{agent_id}/threads/{tid2}/history", headers=ctx["auth"]["member"]
    )
    assert r.status_code == 200, r.text
    r = await ctx["client"].get(f"/api/projects/{ctx['pid']}", headers=ctx["auth"]["member"])
    assert r.status_code == 404, r.text


async def test_create_body_is_strictly_validated(env_with_provider: Any) -> None:
    ctx = await _base(env_with_provider)
    digest = (await _detail(ctx))["instructions_sha256"]
    endpoint = f"/api/projects/{ctx['pid']}/tasks"
    headers = ctx["auth"]["member"]

    r = await ctx["client"].post(endpoint, headers=headers, json={})
    assert r.status_code == 422, r.text
    # Client-supplied user/source/instruction text is never accepted.
    r = await ctx["client"].post(
        endpoint,
        headers=headers,
        json={
            "agent_id": ctx["agents"]["member"],
            "expected_instructions_sha256": digest,
            "source": "cloud",
            "owner_user_id": 1,
            "instructions": "injected",
        },
    )
    assert r.status_code == 422, r.text
    r = await ctx["client"].post(
        endpoint,
        headers=headers,
        json={"agent_id": ctx["agents"]["member"], "expected_instructions_sha256": "not-a-digest"},
    )
    assert r.status_code == 422, r.text
    r = await ctx["client"].post(
        endpoint,
        headers=headers,
        json={"agent_id": "", "expected_instructions_sha256": digest},
    )
    assert r.status_code == 422, r.text
    _assert_no_create_rows(ctx["srv"], ctx["agents"]["member"], ctx["uids"]["member"])
