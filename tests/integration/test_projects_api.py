"""Integration tests for the /api/projects router (project spaces, first slice)."""

from __future__ import annotations

import json
from typing import Any

from tests.support.auth import create_user, resolve_user_id

OWNER = "pb_owner"
MEMBER = "pb_member"
OUTSIDER = "pb_outsider"


async def _auths(env: Any) -> tuple[Any, Any, dict[str, str], dict[str, str], dict[str, str]]:
    client, srv, admin_auth = env
    owner_auth = await create_user(client, admin_auth, username=OWNER)
    member_auth = await create_user(client, admin_auth, username=MEMBER)
    outsider_auth = await create_user(client, admin_auth, username=OUTSIDER)
    return client, srv, owner_auth, member_auth, outsider_auth


async def _create(client: Any, auth: dict[str, str], body: dict[str, Any]) -> Any:
    return await client.post("/api/projects", headers=auth, json=body)


async def test_projects_require_auth(env) -> None:
    client, _srv, _admin_auth = env
    r = await client.get("/api/projects")
    assert r.status_code == 401, r.text


async def test_owner_create_detail_and_list(env) -> None:
    client, _srv, owner_auth, _member_auth, outsider_auth = await _auths(env)

    r = await _create(client, owner_auth, {"name": "  熊宝项目  "})
    assert r.status_code == 201, r.text
    created = r.json()
    assert created["name"] == "熊宝项目"
    assert created["description"] == ""
    assert created["instructions"] == ""
    assert created["my_role"] == "owner"
    assert created["member_count"] == 1
    assert created["project_id"]
    assert isinstance(created["created_at"], int)
    assert created["updated_at"] == created["created_at"]
    pid = created["project_id"]

    # Refresh: detail is served from the database, not from create-time state.
    detail = await client.get(f"/api/projects/{pid}", headers=owner_auth)
    assert detail.status_code == 200, detail.text
    body = detail.json()
    assert body["project_id"] == pid
    assert body["name"] == "熊宝项目"
    assert body["my_role"] == "owner"
    assert body["member_count"] == 1

    mine = await client.get("/api/projects", headers=owner_auth)
    assert mine.status_code == 200, mine.text
    page = mine.json()
    assert [i["project_id"] for i in page["items"]] == [pid]
    assert page["items"][0]["name"] == "熊宝项目"
    assert page["items"][0]["my_role"] == "owner"
    assert page["items"][0]["member_count"] == 1
    assert page["limit"] == 20
    assert page["offset"] == 0
    assert page["has_more"] is False
    assert "instructions" not in page["items"][0]

    theirs = await client.get("/api/projects", headers=outsider_auth)
    assert theirs.status_code == 200
    assert theirs.json()["items"] == []


async def test_outsider_gets_404_for_known_project(env) -> None:
    client, _srv, owner_auth, _member_auth, outsider_auth = await _auths(env)
    pid = (await _create(client, owner_auth, {"name": "Secret"})).json()["project_id"]

    detail = await client.get(f"/api/projects/{pid}", headers=outsider_auth)
    assert detail.status_code == 404, detail.text
    assert detail.json()["error"]["code"] == "NOT_FOUND"

    members = await client.get(f"/api/projects/{pid}/members", headers=outsider_auth)
    assert members.status_code == 404
    assert members.json()["error"]["code"] == "NOT_FOUND"

    patch = await client.patch(
        f"/api/projects/{pid}", headers=outsider_auth, json={"name": "Taken"}
    )
    assert patch.status_code == 404
    assert patch.json()["error"]["code"] == "NOT_FOUND"

    after = await client.get(f"/api/projects/{pid}", headers=owner_auth)
    assert after.json()["name"] == "Secret"


async def test_global_admin_without_membership_gets_404(env) -> None:
    """Membership — not the global admin role — grants project access."""
    client, _srv, admin_auth, owner_auth, *_ = await _auths(env)
    r = await _create(client, owner_auth, {"name": "Private"})
    pid = r.json()["project_id"]

    detail = await client.get(f"/api/projects/{pid}", headers=admin_auth)
    assert detail.status_code == 404
    listed = await client.get("/api/projects", headers=admin_auth)
    assert listed.json()["items"] == []


async def test_seeded_member_reads_but_cannot_update(env) -> None:
    client, srv, admin_auth = env
    owner_auth = await create_user(client, admin_auth, username=OWNER)
    member_auth = await create_user(client, admin_auth, username=MEMBER)
    pid = (await _create(client, owner_auth, {"name": "Team"})).json()["project_id"]
    member_uid = await resolve_user_id(client, admin_auth, MEMBER)
    srv.services.project_repo.add_member(pid, member_uid, role="member")

    listed = await client.get("/api/projects", headers=member_auth)
    assert listed.status_code == 200, listed.text
    items = listed.json()["items"]
    assert [i["project_id"] for i in items] == [pid]
    assert items[0]["my_role"] == "member"
    assert items[0]["member_count"] == 2

    detail = await client.get(f"/api/projects/{pid}", headers=member_auth)
    assert detail.status_code == 200
    assert detail.json()["my_role"] == "member"

    members = await client.get(f"/api/projects/{pid}/members", headers=member_auth)
    assert members.status_code == 200
    payload = members.json()
    assert [(m["username"], m["role"]) for m in payload] == [
        (OWNER, "owner"),
        (MEMBER, "member"),
    ]
    assert payload[1]["user_id"] == member_uid
    for entry in payload:
        assert set(entry) == {"user_id", "username", "role"}

    patch = await client.patch(
        f"/api/projects/{pid}", headers=member_auth, json={"name": "Renamed"}
    )
    assert patch.status_code == 403, patch.text
    assert patch.json()["error"]["code"] == "FORBIDDEN"

    after = await client.get(f"/api/projects/{pid}", headers=owner_auth)
    assert after.json()["name"] == "Team"


async def test_admin_role_member_can_update(env) -> None:
    client, srv, admin_auth = env
    owner_auth = await create_user(client, admin_auth, username=OWNER)
    member_auth = await create_user(client, admin_auth, username=MEMBER)
    pid = (await _create(client, owner_auth, {"name": "Editable"})).json()["project_id"]
    member_uid = await resolve_user_id(client, admin_auth, MEMBER)
    srv.services.project_repo.add_member(pid, member_uid, role="admin")

    patch = await client.patch(
        f"/api/projects/{pid}", headers=member_auth, json={"description": "updated"}
    )
    assert patch.status_code == 200, patch.text
    body = patch.json()
    assert body["description"] == "updated"
    assert body["name"] == "Editable"
    assert body["my_role"] == "admin"
    assert body["member_count"] == 2


async def test_name_validation_rejects_bad_names_without_writes(env) -> None:
    client, srv, admin_auth = env
    owner_auth = await create_user(client, admin_auth, username=OWNER)

    for bad in ["", "   ", "a" * 16, "熊" * 16]:
        r = await _create(client, owner_auth, {"name": bad})
        assert r.status_code == 422, (bad, r.text)
    missing = await _create(client, owner_auth, {"description": "no name"})
    assert missing.status_code == 422

    with srv.services.db.connect() as conn:
        spaces = conn.execute("SELECT COUNT(*) FROM project_spaces").fetchone()[0]
        events = conn.execute("SELECT COUNT(*) FROM project_events").fetchone()[0]
    assert spaces == 0
    assert events == 0

    ok = await _create(client, owner_auth, {"name": "熊" * 15})
    assert ok.status_code == 201, ok.text
    assert ok.json()["name"] == "熊" * 15


async def test_duplicate_names_are_allowed(env) -> None:
    client, _srv, admin_auth = env
    owner_auth = await create_user(client, admin_auth, username=OWNER)
    a = await _create(client, owner_auth, {"name": "熊宝项目"})
    b = await _create(client, owner_auth, {"name": "熊宝项目"})
    assert a.status_code == 201 and b.status_code == 201
    assert a.json()["project_id"] != b.json()["project_id"]


async def test_update_records_event_without_instructions(env) -> None:
    client, srv, admin_auth = env
    owner_auth = await create_user(client, admin_auth, username=OWNER)
    created = (
        await _create(client, owner_auth, {"name": "熊宝项目", "instructions": "TOP-SECRET-KEY"})
    ).json()
    pid = created["project_id"]

    r = await client.patch(
        f"/api/projects/{pid}",
        headers=owner_auth,
        json={"name": "新名字", "instructions": "ROTATED-SECRET"},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["name"] == "新名字"
    assert body["instructions"] == "ROTATED-SECRET"
    assert body["my_role"] == "owner"
    assert body["updated_at"] >= created["updated_at"]

    with srv.services.db.connect() as conn:
        rows = conn.execute(
            "SELECT actor_user_id, event_type, object_id, payload_json "
            "FROM project_events WHERE project_id = ? ORDER BY id",
            (pid,),
        ).fetchall()
    assert [row["event_type"] for row in rows] == ["project.created", "project.updated"]
    owner_uid = await resolve_user_id(client, admin_auth, OWNER)
    assert rows[1]["actor_user_id"] == owner_uid
    assert rows[1]["object_id"] == pid
    assert json.loads(rows[1]["payload_json"]) == {"fields": ["instructions", "name"]}
    dumped = "".join(str(row["payload_json"]) for row in rows)
    assert "SECRET" not in dumped
    assert "新名字" not in dumped


async def test_empty_patch_is_noop_without_event(env) -> None:
    client, srv, admin_auth = env
    owner_auth = await create_user(client, admin_auth, username=OWNER)
    pid = (await _create(client, owner_auth, {"name": "Same"})).json()["project_id"]

    r = await client.patch(f"/api/projects/{pid}", headers=owner_auth, json={})
    assert r.status_code == 200, r.text
    assert r.json()["name"] == "Same"

    with srv.services.db.connect() as conn:
        n = conn.execute(
            "SELECT COUNT(*) FROM project_events WHERE project_id = ?", (pid,)
        ).fetchone()[0]
    assert n == 1


async def test_list_pagination_search_and_bounds(env) -> None:
    client, _srv, admin_auth = env
    owner_auth = await create_user(client, admin_auth, username=OWNER)
    for i in range(3):
        r = await _create(client, owner_auth, {"name": f"Project {i}"})
        assert r.status_code == 201
    r = await _create(client, owner_auth, {"name": "熊宝专属"})
    assert r.status_code == 201

    page1 = (
        await client.get("/api/projects", headers=owner_auth, params={"limit": 2, "offset": 0})
    ).json()
    assert len(page1["items"]) == 2
    assert page1["has_more"] is True
    page2 = (
        await client.get("/api/projects", headers=owner_auth, params={"limit": 2, "offset": 2})
    ).json()
    assert len(page2["items"]) == 2
    assert page2["has_more"] is False
    assert {i["project_id"] for i in page1["items"]}.isdisjoint(
        i["project_id"] for i in page2["items"]
    )

    hits = (await client.get("/api/projects", headers=owner_auth, params={"q": " 熊宝 "})).json()
    assert [i["name"] for i in hits["items"]] == ["熊宝专属"]

    assert (
        await client.get("/api/projects", headers=owner_auth, params={"limit": 0})
    ).status_code == 422
    assert (
        await client.get("/api/projects", headers=owner_auth, params={"limit": 101})
    ).status_code == 422
    assert (
        await client.get("/api/projects", headers=owner_auth, params={"offset": -1})
    ).status_code == 422
