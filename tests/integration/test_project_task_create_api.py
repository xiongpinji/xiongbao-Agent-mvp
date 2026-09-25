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


# ---------------------------------------------------------------------------
# 029 expert gate on create
# ---------------------------------------------------------------------------


def _share_agent(srv: Any, agent_id: str, value: int = 1) -> None:
    with srv.services.db.transaction() as conn:
        conn.execute("UPDATE agents SET is_shared = ? WHERE agent_id = ?", (value, agent_id))


def _set_agent(srv: Any, agent_id: str, column: str, value: object) -> None:
    with srv.services.db.transaction() as conn:
        conn.execute(f"UPDATE agents SET {column} = ? WHERE agent_id = ?", (value, agent_id))


def _delete_agent(srv: Any, agent_id: str) -> None:
    with srv.services.db.transaction() as conn:
        conn.execute("DELETE FROM agents WHERE agent_id = ?", (agent_id,))


async def _configure_experts(
    ctx: dict[str, Any],
    agent_ids: list[str],
    *,
    expected_revision: int = 0,
    role: str = "owner",
) -> int:
    r = await ctx["client"].put(
        f"/api/projects/{ctx['pid']}/experts",
        headers=ctx["auth"][role],
        json={"expected_revision": expected_revision, "agent_ids": agent_ids},
    )
    assert r.status_code == 200, r.text
    return int(r.json()["revision"])


def _context_revision(srv: Any, thread_id: str) -> int | None:
    with srv.services.db.connect() as conn:
        row = conn.execute(
            "SELECT expert_selection_revision FROM project_task_contexts WHERE thread_id = ?",
            (thread_id,),
        ).fetchone()
    return None if row is None else int(row["expert_selection_revision"])


async def test_nonempty_expert_list_requires_matching_revision(
    env_with_provider: Any,
) -> None:
    ctx = await _base(env_with_provider)
    _share_agent(ctx["srv"], ctx["agents"]["member"])
    revision = await _configure_experts(ctx, [ctx["agents"]["member"]])
    assert revision == 1

    for body in (
        {
            "agent_id": ctx["agents"]["member"],
            "expected_instructions_sha256": instructions_sha256(INSTRUCTIONS),
        },
        {
            "agent_id": ctx["agents"]["member"],
            "expected_instructions_sha256": instructions_sha256(INSTRUCTIONS),
            "expected_experts_revision": 0,
        },
    ):
        r = await _create(ctx, body=body)
        assert r.status_code == 409, r.text
        assert r.json()["error"]["code"] == "PROJECT_EXPERTS_CHANGED"
    _assert_no_create_rows(ctx["srv"], ctx["agents"]["member"], ctx["uids"]["member"])

    r = await _create(
        ctx,
        body={
            "agent_id": ctx["agents"]["member"],
            "expected_instructions_sha256": instructions_sha256(INSTRUCTIONS),
            "expected_experts_revision": revision,
        },
    )
    assert r.status_code == 201, r.text
    payload = r.json()
    assert set(payload) == _SUMMARY_KEYS
    assert payload["agent_id"] == ctx["agents"]["member"]
    assert _context_revision(ctx["srv"], payload["thread_id"]) == 1


async def test_nonempty_expert_list_maps_unavailable_runtime_states(
    env_with_provider: Any,
) -> None:
    """The final runtime check must not leak stopped or failed Agent codes.

    ``status='available'`` in the expert list only covers shared/enabled/kind,
    so a stopped listed expert still reaches ``require_running_agent``. With
    the gate on that refusal is the same recoverable 409 as an unshared one —
    only the empty-list 028 path keeps the raw lifecycle code.
    """
    ctx = await _base(env_with_provider)
    srv = ctx["srv"]
    _share_agent(srv, ctx["agents"]["member"])
    revision = await _configure_experts(ctx, [ctx["agents"]["member"]])
    digest = (await _detail(ctx))["instructions_sha256"]

    r = await ctx["client"].post(
        f"/api/agents/{ctx['agents']['member']}/stop", headers=ctx["auth"]["member"]
    )
    assert r.status_code == 204, r.text

    body = {
        "agent_id": ctx["agents"]["member"],
        "expected_instructions_sha256": digest,
        "expected_experts_revision": revision,
    }
    r = await _create(ctx, body=body)
    assert r.status_code == 409, r.text
    assert r.json()["error"]["code"] == "PROJECT_EXPERT_UNAVAILABLE"
    assert "AGENT_NOT_RUNNING" not in r.text
    _assert_no_create_rows(srv, ctx["agents"]["member"], ctx["uids"]["member"])

    # A failed start is another unavailable runtime state. Do not leak its
    # internal 500 code to a project member or create a partial task.
    _set_agent(srv, ctx["agents"]["member"], "last_state", "failed")
    r = await _create(ctx, body=body)
    assert r.status_code == 409, r.text
    assert r.json()["error"]["code"] == "PROJECT_EXPERT_UNAVAILABLE"
    assert "AGENT_FAILED" not in r.text
    _assert_no_create_rows(srv, ctx["agents"]["member"], ctx["uids"]["member"])
    _set_agent(srv, ctx["agents"]["member"], "last_state", "stopped")

    # Restarting the expert makes the same confirmed request succeed.
    r = await ctx["client"].post(
        f"/api/agents/{ctx['agents']['member']}/start", headers=ctx["auth"]["member"]
    )
    assert r.status_code == 204, r.text
    r = await _create(ctx, body=body)
    assert r.status_code == 201, r.text
    assert r.json()["agent_id"] == ctx["agents"]["member"]


async def test_empty_expert_list_keeps_028_behavior_and_checks_supplied_revision(
    env_with_provider: Any,
) -> None:
    ctx = await _base(env_with_provider)
    digest = (await _detail(ctx))["instructions_sha256"]

    r = await _create(ctx, digest=digest)
    assert r.status_code == 201, r.text
    assert _context_revision(ctx["srv"], r.json()["thread_id"]) == 0

    r = await _create(
        ctx,
        body={
            "agent_id": ctx["agents"]["member"],
            "expected_instructions_sha256": digest,
            "expected_experts_revision": 0,
        },
    )
    assert r.status_code == 201, r.text
    assert _context_revision(ctx["srv"], r.json()["thread_id"]) == 0

    r = await _create(
        ctx,
        body={
            "agent_id": ctx["agents"]["member"],
            "expected_instructions_sha256": digest,
            "expected_experts_revision": 5,
        },
    )
    assert r.status_code == 409, r.text
    assert r.json()["error"]["code"] == "PROJECT_EXPERTS_CHANGED"
    assert _count(ctx["srv"], "threads", "user_id = ?", (ctx["uids"]["member"],)) == 2


async def test_expert_gate_rejects_unshared_disabled_and_outside_agents(
    env_with_provider: Any,
) -> None:
    ctx = await _base(env_with_provider)
    srv = ctx["srv"]
    _share_agent(srv, ctx["agents"]["member"])
    _share_agent(srv, ctx["agents"]["owner"])
    revision = await _configure_experts(ctx, [ctx["agents"]["owner"], ctx["agents"]["member"]])

    digest = (await _detail(ctx))["instructions_sha256"]
    body = {
        "agent_id": ctx["agents"]["member"],
        "expected_instructions_sha256": digest,
        "expected_experts_revision": revision,
    }

    # The member's agent is no longer shared even though the list still names it.
    _share_agent(srv, ctx["agents"]["member"], 0)
    r = await _create(ctx, body=body)
    assert r.status_code == 409, r.text
    assert r.json()["error"]["code"] == "PROJECT_EXPERT_UNAVAILABLE"
    assert "pe-" not in r.text
    _assert_no_create_rows(srv, ctx["agents"]["member"], ctx["uids"]["member"])

    _share_agent(srv, ctx["agents"]["member"])
    _set_agent(srv, ctx["agents"]["member"], "enabled", 0)
    r = await _create(ctx, body=body)
    assert r.status_code == 409, r.text
    assert r.json()["error"]["code"] == "PROJECT_EXPERT_UNAVAILABLE"

    _set_agent(srv, ctx["agents"]["member"], "enabled", 1)
    # An accessible but unlisted agent is refused uniformly (member2 owns it).
    r = await _create(
        ctx,
        role="member",
        body={**body, "agent_id": ctx["agents"]["member2"]},
    )
    assert r.status_code == 409, r.text
    assert r.json()["error"]["code"] == "PROJECT_EXPERT_UNAVAILABLE"

    # A ghost id is refused with the same shape — never AGENT_NOT_FOUND.
    r = await _create(ctx, body={**body, "agent_id": "ag_ghost"})
    assert r.status_code == 409, r.text
    assert r.json()["error"]["code"] == "PROJECT_EXPERT_UNAVAILABLE"
    _assert_no_create_rows(srv, ctx["agents"]["member"], ctx["uids"]["member"])


async def test_outsider_never_learns_about_private_agents_with_expert_list(
    env_with_provider: Any,
) -> None:
    ctx = await _base(env_with_provider)
    _share_agent(ctx["srv"], ctx["agents"]["owner"])
    revision = await _configure_experts(ctx, [ctx["agents"]["owner"]])
    digest = (await _detail(ctx))["instructions_sha256"]
    assert revision == 1

    for agent_id in ("ag_ghost", ctx["agents"]["outsider"]):
        r = await _create(
            ctx,
            role="outsider",
            body={
                "agent_id": agent_id,
                "expected_instructions_sha256": digest,
                "expected_experts_revision": 1,
            },
        )
        assert r.status_code == 404, r.text
        assert r.json()["error"]["code"] == "NOT_FOUND"
        assert "ag_" not in r.text and "pe-outsider" not in r.text


async def test_hard_deleted_listed_agent_is_uniformly_unavailable(
    env_with_provider: Any,
) -> None:
    ctx = await _base(env_with_provider)
    srv = ctx["srv"]
    _share_agent(srv, ctx["agents"]["owner"])
    _share_agent(srv, ctx["agents"]["member"])
    revision = await _configure_experts(ctx, [ctx["agents"]["owner"], ctx["agents"]["member"]])
    digest = (await _detail(ctx))["instructions_sha256"]

    _delete_agent(srv, ctx["agents"]["member"])
    r = await _create(
        ctx,
        body={
            "agent_id": ctx["agents"]["member"],
            "expected_instructions_sha256": digest,
            "expected_experts_revision": revision,
        },
    )
    assert r.status_code == 409, r.text
    assert r.json()["error"]["code"] == "PROJECT_EXPERT_UNAVAILABLE"
    _assert_no_create_rows(srv, ctx["agents"]["member"], ctx["uids"]["member"])

    # Deleting the whole list cascades back to the empty-list 028 path.
    _delete_agent(srv, ctx["agents"]["owner"])
    r = await _create(
        ctx,
        body={
            "agent_id": ctx["agents"]["member"],
            "expected_instructions_sha256": digest,
            "expected_experts_revision": revision,
        },
    )
    assert r.status_code == 404, r.text
    assert r.json()["error"]["code"] == "AGENT_NOT_FOUND"
    assert _count(srv, "project_task_links", "owner_user_id = ?", (ctx["uids"]["member"],)) == 0


async def test_prior_private_task_survives_expert_list_change(
    env_with_provider: Any,
) -> None:
    ctx = await _base(env_with_provider)
    srv = ctx["srv"]
    _share_agent(srv, ctx["agents"]["member"])
    _share_agent(srv, ctx["agents"]["owner"])
    revision = await _configure_experts(ctx, [ctx["agents"]["member"]])
    digest = (await _detail(ctx))["instructions_sha256"]

    r = await _create(
        ctx,
        body={
            "agent_id": ctx["agents"]["member"],
            "expected_instructions_sha256": digest,
            "expected_experts_revision": revision,
        },
    )
    assert r.status_code == 201, r.text
    tid = r.json()["thread_id"]

    await _configure_experts(ctx, [ctx["agents"]["owner"]], expected_revision=revision)

    r = await ctx["client"].get(
        f"/api/projects/{ctx['pid']}/tasks/{tid}", headers=ctx["auth"]["member"]
    )
    assert r.status_code == 200, r.text
    assert r.json()["agent_id"] == ctx["agents"]["member"]
    r = await ctx["client"].get(
        f"/api/agents/{ctx['agents']['member']}/threads/{tid}/history",
        headers=ctx["auth"]["member"],
    )
    assert r.status_code == 200, r.text
    assert _count(srv, "project_task_links", "thread_id = ?", (tid,)) == 1
    assert _count(srv, "project_task_contexts", "thread_id = ?", (tid,)) == 1
    assert _context_revision(srv, tid) == 1


async def test_expected_experts_revision_body_is_validated(
    env_with_provider: Any,
) -> None:
    ctx = await _base(env_with_provider)
    digest = (await _detail(ctx))["instructions_sha256"]
    endpoint = f"/api/projects/{ctx['pid']}/tasks"
    headers = ctx["auth"]["member"]
    for value in (-1, "x", 1.5):
        r = await ctx["client"].post(
            endpoint,
            headers=headers,
            json={
                "agent_id": ctx["agents"]["member"],
                "expected_instructions_sha256": digest,
                "expected_experts_revision": value,
            },
        )
        assert r.status_code == 422, (value, r.text)
    _assert_no_create_rows(ctx["srv"], ctx["agents"]["member"], ctx["uids"]["member"])
