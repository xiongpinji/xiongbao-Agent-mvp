"""Integration tests for the /api/projects router (spaces, members, invites)."""

from __future__ import annotations

import hashlib
import json
from typing import Any

from tests.support.auth import create_user, resolve_user_id

OWNER = "pb_owner"
MEMBER = "pb_member"
OUTSIDER = "pb_outsider"
INVITEE = "pb_invitee"
SECOND = "pb_second"
ADMIN_MEMBER = "pb_adminmember"

_INVITE_KEYS = {
    "invite_id",
    "project_id",
    "role",
    "requires_approval",
    "created_by",
    "created_at",
    "expires_at",
    "revoked_at",
    "consumed_at",
    "consumed_by_user_id",
    "status",
}
_REQUEST_KEYS = {
    "request_id",
    "project_id",
    "invite_id",
    "user_id",
    "username",
    "status",
    "requested_at",
    "resolved_at",
    "resolved_by",
}


async def _auths(env: Any) -> tuple[Any, Any, dict[str, str], dict[str, str], dict[str, str]]:
    client, srv, admin_auth = env
    owner_auth = await create_user(client, admin_auth, username=OWNER)
    member_auth = await create_user(client, admin_auth, username=MEMBER)
    outsider_auth = await create_user(client, admin_auth, username=OUTSIDER)
    return client, srv, owner_auth, member_auth, outsider_auth


async def _create(client: Any, auth: dict[str, str], body: dict[str, Any]) -> Any:
    return await client.post("/api/projects", headers=auth, json=body)


async def _create_invite(
    client: Any, auth: dict[str, str], pid: str, body: dict[str, Any] | None = None
) -> Any:
    return await client.post(f"/api/projects/{pid}/invites", headers=auth, json=body or {})


async def _accept(client: Any, auth: dict[str, str], token: str) -> Any:
    return await client.post("/api/projects/invites/accept", headers=auth, json={"token": token})


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


# ---------------------------------------------------------------------------
# Invites and join requests
# ---------------------------------------------------------------------------


async def test_invite_immediate_join_lifecycle(env) -> None:
    client, _srv, admin_auth = env
    owner_auth = await create_user(client, admin_auth, username=OWNER)
    invitee_auth = await create_user(client, admin_auth, username=INVITEE)
    second_auth = await create_user(client, admin_auth, username=SECOND)
    pid = (await _create(client, owner_auth, {"name": "Invited"})).json()["project_id"]

    created = await _create_invite(client, owner_auth, pid)
    assert created.status_code == 201, created.text
    body = created.json()
    assert set(body) == _INVITE_KEYS | {"token"}
    token = body["token"]
    assert token
    assert body["status"] == "pending"
    assert body["requires_approval"] is False
    assert body["role"] == "member"
    assert body["revoked_at"] is None
    assert body["consumed_at"] is None
    assert body["expires_at"] - body["created_at"] == 7 * 86400

    accepted = await _accept(client, invitee_auth, token)
    assert accepted.status_code == 200, accepted.text
    assert accepted.json() == {"status": "joined", "project_id": pid, "role": "member"}

    # Membership is effective on the invitee's next request.
    detail = await client.get(f"/api/projects/{pid}", headers=invitee_auth)
    assert detail.status_code == 200, detail.text
    assert detail.json()["my_role"] == "member"
    assert detail.json()["member_count"] == 2

    # One-use: a replay by another user is a deterministic 409, never a join.
    replay = await _accept(client, second_auth, token)
    assert replay.status_code == 409, replay.text
    assert replay.json()["error"]["code"] == "INVITE_USED"
    assert replay.json()["error"]["details"]["reason"] == "invite_used"
    outside = await client.get(f"/api/projects/{pid}", headers=second_auth)
    assert outside.status_code == 404

    # List responses omit the token entirely.
    listed = await client.get(f"/api/projects/{pid}/invites", headers=owner_auth)
    assert listed.status_code == 200, listed.text
    items = listed.json()["items"]
    assert len(items) == 1
    assert set(items[0]) == _INVITE_KEYS
    assert items[0]["status"] == "used"
    assert items[0]["consumed_at"] is not None
    assert token not in listed.text


async def test_invite_routes_auth_and_permission_gates(env) -> None:
    client, srv, admin_auth = env
    owner_auth = await create_user(client, admin_auth, username=OWNER)
    member_auth = await create_user(client, admin_auth, username=MEMBER)
    admin_member_auth = await create_user(client, admin_auth, username=ADMIN_MEMBER)
    outsider_auth = await create_user(client, admin_auth, username=OUTSIDER)
    pid = (await _create(client, owner_auth, {"name": "Gated"})).json()["project_id"]
    member_uid = await resolve_user_id(client, admin_auth, MEMBER)
    admin_uid = await resolve_user_id(client, admin_auth, ADMIN_MEMBER)
    srv.services.project_repo.add_member(pid, member_uid, role="member")
    srv.services.project_repo.add_member(pid, admin_uid, role="admin")

    anonymous = await client.post("/api/projects/invites/accept", json={"token": "x"})
    assert anonymous.status_code == 401

    # Known members without manager rights get 403 on every manager route.
    by_member = await _create_invite(client, member_auth, pid)
    assert by_member.status_code == 403, by_member.text
    assert by_member.json()["error"]["code"] == "FORBIDDEN"

    for auth, code in ((member_auth, 403), (outsider_auth, 404)):
        r = await client.get(f"/api/projects/{pid}/invites", headers=auth)
        assert r.status_code == code, r.text
        r = await _create_invite(client, auth, pid)
        assert r.status_code == code, r.text
        r = await client.post(f"/api/projects/{pid}/invites/whatever/revoke", headers=auth)
        assert r.status_code == code, r.text
        r = await client.get(f"/api/projects/{pid}/join-requests", headers=auth)
        assert r.status_code == code, r.text

    # An admin-role member may manage invites.
    by_admin = await _create_invite(client, admin_member_auth, pid)
    assert by_admin.status_code == 201, by_admin.text
    listed = await client.get(f"/api/projects/{pid}/invites", headers=admin_member_auth)
    assert listed.status_code == 200
    assert len(listed.json()["items"]) == 1


async def test_invite_approval_flow(env) -> None:
    client, srv, admin_auth = env
    owner_auth = await create_user(client, admin_auth, username=OWNER)
    invitee_auth = await create_user(client, admin_auth, username=INVITEE)
    pid = (await _create(client, owner_auth, {"name": "Approval"})).json()["project_id"]

    created = await _create_invite(client, owner_auth, pid, {"requires_approval": True})
    assert created.status_code == 201, created.text
    assert created.json()["requires_approval"] is True

    accepted = await _accept(client, invitee_auth, created.json()["token"])
    assert accepted.status_code == 200, accepted.text
    payload = accepted.json()
    assert set(payload) == {"status", "project_id", "request_id"}
    assert payload["status"] == "pending_approval"
    assert payload["project_id"] == pid
    request_id = payload["request_id"]

    # Still no project visibility before approval.
    blocked = await client.get(f"/api/projects/{pid}", headers=invitee_auth)
    assert blocked.status_code == 404

    listed = await client.get(f"/api/projects/{pid}/join-requests", headers=owner_auth)
    assert listed.status_code == 200, listed.text
    items = listed.json()["items"]
    assert len(items) == 1
    assert set(items[0]) == _REQUEST_KEYS
    assert items[0]["status"] == "pending"
    assert items[0]["username"] == INVITEE
    assert items[0]["request_id"] == request_id
    assert items[0]["resolved_at"] is None

    filtered = await client.get(
        f"/api/projects/{pid}/join-requests", headers=owner_auth, params={"status": "approved"}
    )
    assert filtered.json()["items"] == []
    bogus = await client.get(
        f"/api/projects/{pid}/join-requests", headers=owner_auth, params={"status": "bogus"}
    )
    assert bogus.status_code == 422

    approved = await client.post(
        f"/api/projects/{pid}/join-requests/{request_id}/approve", headers=owner_auth
    )
    assert approved.status_code == 200, approved.text
    body = approved.json()
    assert set(body) == _REQUEST_KEYS
    assert body["status"] == "approved"
    assert body["resolved_at"] is not None
    assert body["resolved_by"] == await resolve_user_id(client, admin_auth, OWNER)

    granted = await client.get(f"/api/projects/{pid}", headers=invitee_auth)
    assert granted.status_code == 200, granted.text
    assert granted.json()["my_role"] == "member"

    activity = await client.get(f"/api/projects/{pid}/activity", headers=invitee_auth)
    assert activity.status_code == 200, activity.text
    assert "project.member_joined" in {item["event_type"] for item in activity.json()["items"]}
    assert request_id not in activity.text

    again = await client.post(
        f"/api/projects/{pid}/join-requests/{request_id}/approve", headers=owner_auth
    )
    assert again.status_code == 409, again.text
    assert again.json()["error"]["code"] == "INVITE_INVALID"
    assert again.json()["error"]["details"]["reason"] == "request_already_resolved"

    with srv.services.db.connect() as conn:
        rows = conn.execute(
            "SELECT event_type FROM project_events WHERE project_id = ? ORDER BY id", (pid,)
        ).fetchall()
    assert [r["event_type"] for r in rows] == [
        "project.created",
        "project.invite_created",
        "project.join_requested",
        "project.member_joined",
        "project.join_approved",
    ]


async def test_invite_reject_flow_and_reapply(env) -> None:
    client, _srv, admin_auth = env
    owner_auth = await create_user(client, admin_auth, username=OWNER)
    invitee_auth = await create_user(client, admin_auth, username=INVITEE)
    pid = (await _create(client, owner_auth, {"name": "Reject"})).json()["project_id"]

    first = await _create_invite(client, owner_auth, pid, {"requires_approval": True})
    accepted = await _accept(client, invitee_auth, first.json()["token"])
    request_id = accepted.json()["request_id"]

    rejected = await client.post(
        f"/api/projects/{pid}/join-requests/{request_id}/reject", headers=owner_auth
    )
    assert rejected.status_code == 200, rejected.text
    assert rejected.json()["status"] == "rejected"
    still_blocked = await client.get(f"/api/projects/{pid}", headers=invitee_auth)
    assert still_blocked.status_code == 404

    repeat = await client.post(
        f"/api/projects/{pid}/join-requests/{request_id}/reject", headers=owner_auth
    )
    assert repeat.status_code == 409
    missing = await client.post(
        f"/api/projects/{pid}/join-requests/nope/approve", headers=owner_auth
    )
    assert missing.status_code == 404

    # After rejection a fresh invite can be accepted again (new pending row).
    second = await _create_invite(client, owner_auth, pid, {"requires_approval": True})
    reaccepted = await _accept(client, invitee_auth, second.json()["token"])
    assert reaccepted.status_code == 200, reaccepted.text
    assert reaccepted.json()["status"] == "pending_approval"
    assert reaccepted.json()["request_id"] != request_id


async def test_invite_revoke_expire_invalid_no_disclosure(env) -> None:
    client, srv, admin_auth = env
    owner_auth = await create_user(client, admin_auth, username=OWNER)
    invitee_auth = await create_user(client, admin_auth, username=INVITEE)
    created_project = await _create(
        client, owner_auth, {"name": "秘密基地", "description": "SECRET-DESC"}
    )
    pid = created_project.json()["project_id"]

    # Unknown tokens are indistinguishable from garbage; nothing is disclosed.
    bogus = await _accept(client, invitee_auth, "not-a-real-token")
    assert bogus.status_code == 400, bogus.text
    assert bogus.json()["error"]["code"] == "INVITE_INVALID"
    assert pid not in bogus.text
    assert "秘密基地" not in bogus.text

    created = await _create_invite(client, owner_auth, pid)
    invite_id = created.json()["invite_id"]
    token = created.json()["token"]

    revoked = await client.post(
        f"/api/projects/{pid}/invites/{invite_id}/revoke", headers=owner_auth
    )
    assert revoked.status_code == 200, revoked.text
    assert set(revoked.json()) == _INVITE_KEYS
    assert revoked.json()["status"] == "revoked"
    assert revoked.json()["revoked_at"] is not None

    used = await _accept(client, invitee_auth, token)
    assert used.status_code == 410, used.text
    assert used.json()["error"]["code"] == "INVITE_REVOKED"
    assert pid not in used.text
    assert "秘密基地" not in used.text
    assert "SECRET-DESC" not in used.text
    still_out = await client.get(f"/api/projects/{pid}", headers=invitee_auth)
    assert still_out.status_code == 404

    again = await client.post(f"/api/projects/{pid}/invites/{invite_id}/revoke", headers=owner_auth)
    assert again.status_code == 409
    assert again.json()["error"]["code"] == "INVITE_INVALID"
    missing = await client.post(f"/api/projects/{pid}/invites/nope/revoke", headers=owner_auth)
    assert missing.status_code == 404

    # Force expiry in the DB (past the 1-day API floor) → 410 INVITE_EXPIRED.
    expiring = await _create_invite(client, owner_auth, pid)
    with srv.services.db.connect() as conn:
        conn.execute("UPDATE project_invites SET expires_at = 1 WHERE project_id = ?", (pid,))
    late = await _accept(client, invitee_auth, expiring.json()["token"])
    assert late.status_code == 410, late.text
    assert late.json()["error"]["code"] == "INVITE_EXPIRED"
    assert pid not in late.text
    assert "秘密基地" not in late.text


async def test_invite_expiry_bounds(env) -> None:
    client, _srv, admin_auth = env
    owner_auth = await create_user(client, admin_auth, username=OWNER)
    pid = (await _create(client, owner_auth, {"name": "Bounds"})).json()["project_id"]

    for bad in (0, 8, -1):
        r = await _create_invite(client, owner_auth, pid, {"expires_in_days": bad})
        assert r.status_code == 422, (bad, r.text)

    one = await _create_invite(client, owner_auth, pid, {"expires_in_days": 1})
    assert one.status_code == 201, one.text
    assert one.json()["expires_at"] - one.json()["created_at"] == 86400
    seven = await _create_invite(client, owner_auth, pid, {"expires_in_days": 7})
    assert seven.status_code == 201, seven.text
    assert seven.json()["expires_at"] - seven.json()["created_at"] == 7 * 86400


async def test_invite_and_member_path_bounds_are_validation_errors(env) -> None:
    client, _srv, admin_auth = env
    owner_auth = await create_user(client, admin_auth, username=OWNER)
    pid = (await _create(client, owner_auth, {"name": "InputBounds"})).json()["project_id"]

    oversized_token = await _accept(client, owner_auth, "x" * 201)
    assert oversized_token.status_code == 422, oversized_token.text

    oversized_user_id = 2**63
    role = await client.patch(
        f"/api/projects/{pid}/members/{oversized_user_id}",
        headers=owner_auth,
        json={"role": "member"},
    )
    removal = await client.delete(
        f"/api/projects/{pid}/members/{oversized_user_id}", headers=owner_auth
    )
    assert role.status_code == 422, role.text
    assert removal.status_code == 422, removal.text


async def test_member_role_change_owner_only(env) -> None:
    client, srv, admin_auth = env
    owner_auth = await create_user(client, admin_auth, username=OWNER)
    member_auth = await create_user(client, admin_auth, username=MEMBER)
    admin_member_auth = await create_user(client, admin_auth, username=ADMIN_MEMBER)
    pid = (await _create(client, owner_auth, {"name": "Roles"})).json()["project_id"]
    member_uid = await resolve_user_id(client, admin_auth, MEMBER)
    admin_uid = await resolve_user_id(client, admin_auth, ADMIN_MEMBER)
    owner_uid = await resolve_user_id(client, admin_auth, OWNER)
    srv.services.project_repo.add_member(pid, member_uid, role="member")
    srv.services.project_repo.add_member(pid, admin_uid, role="admin")

    # An admin-role member cannot change roles — owner only.
    blocked = await client.patch(
        f"/api/projects/{pid}/members/{member_uid}",
        headers=admin_member_auth,
        json={"role": "admin"},
    )
    assert blocked.status_code == 403, blocked.text
    assert blocked.json()["error"]["code"] == "FORBIDDEN"

    promoted = await client.patch(
        f"/api/projects/{pid}/members/{member_uid}", headers=owner_auth, json={"role": "admin"}
    )
    assert promoted.status_code == 200, promoted.text
    assert promoted.json() == {"user_id": member_uid, "username": MEMBER, "role": "admin"}

    # Effective on the next request: the promoted member can now edit.
    edit = await client.patch(
        f"/api/projects/{pid}", headers=member_auth, json={"description": "by admin"}
    )
    assert edit.status_code == 200, edit.text

    demoted = await client.patch(
        f"/api/projects/{pid}/members/{member_uid}", headers=owner_auth, json={"role": "member"}
    )
    assert demoted.status_code == 200
    assert demoted.json()["role"] == "member"
    denied = await client.patch(
        f"/api/projects/{pid}", headers=member_auth, json={"description": "nope"}
    )
    assert denied.status_code == 403

    # The owner role cannot be changed or assigned; unknown targets are 404.
    owner_target = await client.patch(
        f"/api/projects/{pid}/members/{owner_uid}", headers=owner_auth, json={"role": "member"}
    )
    assert owner_target.status_code == 403
    escalate = await client.patch(
        f"/api/projects/{pid}/members/{member_uid}", headers=owner_auth, json={"role": "owner"}
    )
    assert escalate.status_code == 422
    missing = await client.patch(
        f"/api/projects/{pid}/members/999999", headers=owner_auth, json={"role": "admin"}
    )
    assert missing.status_code == 404


async def test_member_removal_rules(env) -> None:
    client, srv, admin_auth = env
    owner_auth = await create_user(client, admin_auth, username=OWNER)
    member_auth = await create_user(client, admin_auth, username=MEMBER)
    admin_member_auth = await create_user(client, admin_auth, username=ADMIN_MEMBER)
    pid = (await _create(client, owner_auth, {"name": "Removal"})).json()["project_id"]
    member_uid = await resolve_user_id(client, admin_auth, MEMBER)
    admin_uid = await resolve_user_id(client, admin_auth, ADMIN_MEMBER)
    owner_uid = await resolve_user_id(client, admin_auth, OWNER)
    srv.services.project_repo.add_member(pid, member_uid, role="member")
    srv.services.project_repo.add_member(pid, admin_uid, role="admin")

    # Regular members cannot remove anyone.
    by_member = await client.delete(f"/api/projects/{pid}/members/{admin_uid}", headers=member_auth)
    assert by_member.status_code == 403, by_member.text

    # An admin may remove a member; removal bites on the next request.
    removed = await client.delete(
        f"/api/projects/{pid}/members/{member_uid}", headers=admin_member_auth
    )
    assert removed.status_code == 204, removed.text
    assert removed.content == b""
    denied = await client.get(f"/api/projects/{pid}", headers=member_auth)
    assert denied.status_code == 404
    denied_members = await client.get(f"/api/projects/{pid}/members", headers=member_auth)
    assert denied_members.status_code == 404

    # An admin may not remove another admin or the owner.
    srv.services.project_repo.add_member(pid, member_uid, role="admin")
    peer = await client.delete(
        f"/api/projects/{pid}/members/{member_uid}", headers=admin_member_auth
    )
    assert peer.status_code == 403
    owner_hit = await client.delete(
        f"/api/projects/{pid}/members/{owner_uid}", headers=admin_member_auth
    )
    assert owner_hit.status_code == 403

    # The owner may remove an admin but never themself.
    by_owner = await client.delete(f"/api/projects/{pid}/members/{member_uid}", headers=owner_auth)
    assert by_owner.status_code == 204
    self_delete = await client.delete(
        f"/api/projects/{pid}/members/{owner_uid}", headers=owner_auth
    )
    assert self_delete.status_code == 403
    assert (await client.get(f"/api/projects/{pid}", headers=owner_auth)).status_code == 200
    missing = await client.delete(f"/api/projects/{pid}/members/999999", headers=owner_auth)
    assert missing.status_code == 404


async def test_invite_token_and_hash_never_leak(env) -> None:
    client, srv, admin_auth = env
    owner_auth = await create_user(client, admin_auth, username=OWNER)
    invitee_auth = await create_user(client, admin_auth, username=INVITEE)
    pid = (await _create(client, owner_auth, {"name": "Hygiene"})).json()["project_id"]

    created = (await _create_invite(client, owner_auth, pid)).json()
    token = created["token"]
    accepted = await _accept(client, invitee_auth, token)
    assert accepted.status_code == 200, accepted.text
    assert token not in accepted.text

    listed = await client.get(f"/api/projects/{pid}/invites", headers=owner_auth)
    members = await client.get(f"/api/projects/{pid}/members", headers=owner_auth)
    detail = await client.get(f"/api/projects/{pid}", headers=owner_auth)
    for response in (listed, members, detail):
        assert response.status_code == 200, response.text
        assert token not in response.text

    token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
    with srv.services.db.connect() as conn:
        stored = conn.execute(
            "SELECT token_hash FROM project_invites WHERE invite_id = ?",
            (created["invite_id"],),
        ).fetchone()[0]
        payloads = "".join(
            str(r[0]) for r in conn.execute("SELECT payload_json FROM project_events").fetchall()
        )
    assert stored == token_hash
    assert token not in payloads
    assert token_hash not in payloads
