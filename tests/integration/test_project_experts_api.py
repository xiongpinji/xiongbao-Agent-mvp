"""Integration tests for GET/PUT /api/projects/{id}/experts (029 expert gate).

PS-07 expert selection slice 1: owner/admin save an ordered list of globally
shared single experts; the revision versions only admin PUT writes, while
unshare/disable/hard-delete changes the effective set without bumping it. Covers
role isolation (owner/admin/member/outsider), duplicate/over-limit/invalid
agents, stale revisions with zero writes, stable ordering and revision bumps,
the same-list no-op, profile masking for unavailable agents, archived-project
reads, strict request validation, and the safe-count-only project event.
"""

from __future__ import annotations

import json
from typing import Any

from octop.infra.projects.service import MAX_PROJECT_EXPERTS
from tests.support.auth import create_agent, create_user, resolve_user_id

OWNER = "pe_owner"
ADMIN = "pe_admin"
MEMBER = "pe_member"
OUTSIDER = "pe_outsider"

_ITEM_KEYS = {"agent_id", "name", "description", "status"}


async def _base(env: Any) -> dict[str, Any]:
    client, srv, admin_auth = env
    owner_auth = await create_user(client, admin_auth, username=OWNER)
    admin_member_auth = await create_user(client, admin_auth, username=ADMIN)
    member_auth = await create_user(client, admin_auth, username=MEMBER)
    outsider_auth = await create_user(client, admin_auth, username=OUTSIDER)
    uids = {
        "owner": await resolve_user_id(client, admin_auth, OWNER),
        "admin": await resolve_user_id(client, admin_auth, ADMIN),
        "member": await resolve_user_id(client, admin_auth, MEMBER),
        "outsider": await resolve_user_id(client, admin_auth, OUTSIDER),
    }
    r = await client.post("/api/projects", headers=owner_auth, json={"name": "专家名单项目"})
    assert r.status_code == 201, r.text
    pid = r.json()["project_id"]
    srv.services.project_repo.add_member(pid, uids["admin"], role="admin")
    srv.services.project_repo.add_member(pid, uids["member"], role="member")
    agents = {
        "owner": await create_agent(client, owner_auth, name="pe-owner-bot"),
        "member": await create_agent(client, member_auth, name="pe-member-bot"),
        "outsider": await create_agent(client, outsider_auth, name="pe-outsider-bot"),
    }
    return {
        "client": client,
        "srv": srv,
        "auth": {
            "owner": owner_auth,
            "admin": admin_member_auth,
            "member": member_auth,
            "outsider": outsider_auth,
        },
        "pid": pid,
        "uids": uids,
        "agents": agents,
    }


async def _get(ctx: dict[str, Any], *, role: str = "owner", pid: str | None = None) -> Any:
    return await ctx["client"].get(
        f"/api/projects/{pid or ctx['pid']}/experts", headers=ctx["auth"][role]
    )


async def _put(
    ctx: dict[str, Any],
    *,
    role: str = "owner",
    body: dict[str, Any] | None = None,
    pid: str | None = None,
) -> Any:
    if body is None:
        body = {"expected_revision": 0, "agent_ids": []}
    return await ctx["client"].put(
        f"/api/projects/{pid or ctx['pid']}/experts", headers=ctx["auth"][role], json=body
    )


def _share(srv: Any, agent_id: str, value: int = 1) -> None:
    with srv.services.db.transaction() as conn:
        conn.execute("UPDATE agents SET is_shared = ? WHERE agent_id = ?", (value, agent_id))


def _set_agent(srv: Any, agent_id: str, column: str, value: object) -> None:
    with srv.services.db.transaction() as conn:
        conn.execute(f"UPDATE agents SET {column} = ? WHERE agent_id = ?", (value, agent_id))


def _delete_agent(srv: Any, agent_id: str) -> None:
    with srv.services.db.transaction() as conn:
        conn.execute("DELETE FROM agents WHERE agent_id = ?", (agent_id,))


def _events(srv: Any, pid: str) -> str:
    with srv.services.db.connect() as conn:
        rows = conn.execute(
            "SELECT event_type, object_id, payload_json FROM project_events WHERE project_id = ?",
            (pid,),
        ).fetchall()
    return json.dumps([dict(r) for r in rows], ensure_ascii=False, default=str)


# ---------------------------------------------------------------------------
# Reads and role isolation
# ---------------------------------------------------------------------------


async def test_owner_saves_ordered_list_and_member_reads_the_same_order(
    env_with_provider: Any,
) -> None:
    ctx = await _base(env_with_provider)
    _share(ctx["srv"], ctx["agents"]["owner"])
    _share(ctx["srv"], ctx["agents"]["member"])
    _set_agent(ctx["srv"], ctx["agents"]["owner"], "description", "共享专家资料")

    r = await _get(ctx)
    assert r.status_code == 200, r.text
    assert r.json() == {"revision": 0, "items": []}

    r = await _put(
        ctx,
        body={
            "expected_revision": 0,
            "agent_ids": [ctx["agents"]["member"], ctx["agents"]["owner"]],
        },
    )
    assert r.status_code == 200, r.text
    payload = r.json()
    assert payload["revision"] == 1
    assert [item["agent_id"] for item in payload["items"]] == [
        ctx["agents"]["member"],
        ctx["agents"]["owner"],
    ]
    assert set(payload["items"][0]) == _ITEM_KEYS
    owner_item = payload["items"][1]
    assert owner_item["name"] == "pe-owner-bot"
    assert owner_item["description"] == "共享专家资料"
    assert owner_item["status"] == "available"

    for role in ("admin", "member"):
        r = await _get(ctx, role=role)
        assert r.status_code == 200, r.text
        assert r.json() == payload

    r = await _get(ctx, role="outsider")
    assert r.status_code == 404, r.text
    assert r.json()["error"]["code"] == "NOT_FOUND"
    r = await _get(ctx, pid="01GHOSTPROJECT029")
    assert r.status_code == 404, r.text
    assert r.json()["error"]["code"] == "NOT_FOUND"


async def test_member_put_forbidden_outsider_404_admin_allowed(
    env_with_provider: Any,
) -> None:
    ctx = await _base(env_with_provider)
    _share(ctx["srv"], ctx["agents"]["owner"])
    body = {"expected_revision": 0, "agent_ids": [ctx["agents"]["owner"]]}

    r = await _put(ctx, role="member", body=body)
    assert r.status_code == 403, r.text
    assert r.json()["error"]["code"] == "FORBIDDEN"

    r = await _put(ctx, role="outsider", body=body)
    assert r.status_code == 404, r.text
    assert r.json()["error"]["code"] == "NOT_FOUND"

    r = await _put(ctx, role="admin", body=body)
    assert r.status_code == 200, r.text
    assert r.json()["revision"] == 1


async def test_demoted_or_removed_actor_is_rechecked_on_put(
    env_with_provider: Any,
) -> None:
    ctx = await _base(env_with_provider)
    _share(ctx["srv"], ctx["agents"]["owner"])
    body = {"expected_revision": 0, "agent_ids": [ctx["agents"]["owner"]]}
    r = await ctx["client"].patch(
        f"/api/projects/{ctx['pid']}/members/{ctx['uids']['admin']}",
        headers=ctx["auth"]["owner"],
        json={"role": "member"},
    )
    assert r.status_code == 200, r.text
    r = await _put(ctx, role="admin", body=body)
    assert r.status_code == 403, r.text

    r = await ctx["client"].delete(
        f"/api/projects/{ctx['pid']}/members/{ctx['uids']['admin']}",
        headers=ctx["auth"]["owner"],
    )
    assert r.status_code == 204, r.text
    r = await _put(ctx, role="admin", body=body)
    assert r.status_code == 404, r.text
    assert r.json()["error"]["code"] == "NOT_FOUND"


# ---------------------------------------------------------------------------
# Validation, revision conflicts, ordering
# ---------------------------------------------------------------------------


async def test_put_rejects_duplicates_over_limit_and_invalid_agents(
    env_with_provider: Any,
) -> None:
    ctx = await _base(env_with_provider)
    srv = ctx["srv"]
    _share(srv, ctx["agents"]["owner"])
    _share(srv, ctx["agents"]["member"])
    shared_ids = [ctx["agents"]["owner"], ctx["agents"]["member"]]
    for index in range(MAX_PROJECT_EXPERTS):
        extra = await create_agent(ctx["client"], ctx["auth"]["owner"], name=f"pe-fill-{index:02d}")
        _share(srv, extra)
        shared_ids.append(extra)
    over_limit = shared_ids[: MAX_PROJECT_EXPERTS + 1]
    assert len(over_limit) == MAX_PROJECT_EXPERTS + 1

    team_agent = await create_agent(ctx["client"], ctx["auth"]["owner"], name="pe-team-bot")
    _share(srv, team_agent)
    _set_agent(srv, team_agent, "kind", "team")
    disabled_agent = await create_agent(ctx["client"], ctx["auth"]["owner"], name="pe-disabled-bot")
    _share(srv, disabled_agent)
    _set_agent(srv, disabled_agent, "enabled", 0)
    private_agent = ctx["agents"]["outsider"]
    _share(srv, private_agent, 0)

    for bad in (
        [ctx["agents"]["owner"], ctx["agents"]["owner"]],
        over_limit,
        [private_agent],
        [team_agent],
        [disabled_agent],
        ["ag_ghost"],
        [ctx["agents"]["owner"], "ag_ghost"],
    ):
        r = await _put(ctx, body={"expected_revision": 0, "agent_ids": bad})
        assert r.status_code == 422, r.text
        assert r.json()["error"]["code"] == "PROJECT_EXPERT_INVALID"
        # No id or private profile is echoed in the error envelope.
        assert "ag_" not in r.text

    r = await _get(ctx)
    assert r.json() == {"revision": 0, "items": []}


async def test_stale_revision_is_409_without_changing_the_list(
    env_with_provider: Any,
) -> None:
    ctx = await _base(env_with_provider)
    _share(ctx["srv"], ctx["agents"]["owner"])
    _share(ctx["srv"], ctx["agents"]["member"])
    r = await _put(ctx, body={"expected_revision": 0, "agent_ids": [ctx["agents"]["owner"]]})
    assert r.status_code == 200 and r.json()["revision"] == 1

    r = await _put(ctx, body={"expected_revision": 0, "agent_ids": [ctx["agents"]["member"]]})
    assert r.status_code == 409, r.text
    assert r.json()["error"]["code"] == "PROJECT_EXPERTS_CHANGED"
    r = await _get(ctx)
    assert r.json()["revision"] == 1
    assert [item["agent_id"] for item in r.json()["items"]] == [ctx["agents"]["owner"]]


async def test_same_list_is_noop_but_reorder_increments(env_with_provider: Any) -> None:
    ctx = await _base(env_with_provider)
    _share(ctx["srv"], ctx["agents"]["owner"])
    _share(ctx["srv"], ctx["agents"]["member"])
    first = [ctx["agents"]["owner"], ctx["agents"]["member"]]
    r = await _put(ctx, body={"expected_revision": 0, "agent_ids": first})
    assert r.status_code == 200 and r.json()["revision"] == 1

    r = await _put(ctx, body={"expected_revision": 1, "agent_ids": first})
    assert r.status_code == 200 and r.json()["revision"] == 1

    r = await _put(ctx, body={"expected_revision": 1, "agent_ids": list(reversed(first))})
    assert r.status_code == 200 and r.json()["revision"] == 2
    assert [item["agent_id"] for item in r.json()["items"]] == list(reversed(first))

    # Clearing the list is a real change too.
    r = await _put(ctx, body={"expected_revision": 2, "agent_ids": []})
    assert r.status_code == 200
    assert r.json() == {"revision": 3, "items": []}


async def test_unshare_disable_delete_masks_profile_and_removes_the_row(
    env_with_provider: Any,
) -> None:
    ctx = await _base(env_with_provider)
    srv = ctx["srv"]
    agent_id = ctx["agents"]["owner"]
    _share(srv, agent_id)
    _set_agent(srv, agent_id, "description", "机密资料")
    r = await _put(ctx, body={"expected_revision": 0, "agent_ids": [agent_id]})
    assert r.status_code == 200, r.text
    assert r.json()["items"][0]["description"] == "机密资料"

    _share(srv, agent_id, 0)
    r = await _get(ctx, role="member")
    assert r.status_code == 200, r.text
    item = r.json()["items"][0]
    assert item["name"] is None
    assert item["description"] is None
    assert item["status"] == "unavailable"
    assert "机密资料" not in r.text
    # GET is live even at the same revision.
    assert r.json()["revision"] == 1

    _share(srv, agent_id)
    _set_agent(srv, agent_id, "enabled", 0)
    item = (await _get(ctx)).json()["items"][0]
    assert item["name"] is None and item["description"] is None
    assert item["status"] == "unavailable"

    _set_agent(srv, agent_id, "enabled", 1)
    item = (await _get(ctx)).json()["items"][0]
    assert item["name"] == "pe-owner-bot" and item["status"] == "available"

    # Hard deletion cascades the row without a revision bump.
    _delete_agent(srv, agent_id)
    assert (await _get(ctx)).json() == {"revision": 1, "items": []}


async def test_archived_project_refuses_put_but_allows_reads(
    env_with_provider: Any,
) -> None:
    ctx = await _base(env_with_provider)
    _share(ctx["srv"], ctx["agents"]["owner"])
    r = await _put(ctx, body={"expected_revision": 0, "agent_ids": [ctx["agents"]["owner"]]})
    assert r.status_code == 200
    with ctx["srv"].services.db.transaction() as conn:
        conn.execute("UPDATE project_spaces SET archived = 1 WHERE project_id = ?", (ctx["pid"],))

    r = await _put(ctx, body={"expected_revision": 1, "agent_ids": []})
    assert r.status_code == 403, r.text
    assert r.json()["error"]["code"] == "FORBIDDEN"

    r = await _get(ctx, role="member")
    assert r.status_code == 200, r.text
    assert r.json()["items"][0]["agent_id"] == ctx["agents"]["owner"]


async def test_expert_event_records_actor_time_and_safe_count_only(
    env_with_provider: Any,
) -> None:
    ctx = await _base(env_with_provider)
    _share(ctx["srv"], ctx["agents"]["owner"])
    r = await _put(ctx, body={"expected_revision": 0, "agent_ids": [ctx["agents"]["owner"]]})
    assert r.status_code == 200

    with ctx["srv"].services.db.connect() as conn:
        rows = conn.execute(
            "SELECT actor_user_id, payload_json, object_id FROM project_events "
            "WHERE project_id = ? AND event_type = 'project.experts_updated'",
            (ctx["pid"],),
        ).fetchall()
    assert len(rows) == 1
    assert int(rows[0]["actor_user_id"]) == ctx["uids"]["owner"]
    assert json.loads(str(rows[0]["payload_json"])) == {"count": 1}
    blob = _events(ctx["srv"], ctx["pid"])
    assert ctx["agents"]["owner"] not in blob
    assert "pe-owner-bot" not in blob


async def test_put_body_is_strictly_validated(env_with_provider: Any) -> None:
    ctx = await _base(env_with_provider)
    _share(ctx["srv"], ctx["agents"]["owner"])
    endpoint = f"/api/projects/{ctx['pid']}/experts"
    headers = ctx["auth"]["owner"]

    for body in (
        {"agent_ids": []},
        {"expected_revision": -1, "agent_ids": []},
        {"expected_revision": "x", "agent_ids": []},
        {"expected_revision": 0, "agent_ids": "not-a-list"},
        {"expected_revision": 0, "agent_ids": [], "extra": True},
    ):
        r = await ctx["client"].put(endpoint, headers=headers, json=body)
        assert r.status_code == 422, (body, r.text)
    r = await _get(ctx)
    assert r.json() == {"revision": 0, "items": []}
