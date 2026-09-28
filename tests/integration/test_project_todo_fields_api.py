"""C1 todo date/reference writes, ACL and transactional response compatibility."""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from tests.integration.test_project_todo_catalog_api import _catalog, _option, _plan
from tests.integration.test_project_todos_api import _base, _create_todo, _events

FIELDS = {"start_date", "due_date", "priority_id", "tag_ids", "catalog_revision"}


async def test_old_create_all_read_paths_and_bulk_carry_five_required_fields(env) -> None:
    ctx = await _base(env)
    todo = await _create_todo(ctx)
    assert set(todo) >= FIELDS, todo
    assert {k: todo[k] for k in FIELDS} == {
        "start_date": None,
        "due_date": None,
        "priority_id": None,
        "tag_ids": [],
        "catalog_revision": 1,
    }
    client, auth = ctx["client"], ctx["owner_auth"]
    path = f"/api/projects/{ctx['pid']}/todos"
    for url in (path, path + f"/{todo['todo_id']}"):
        response = await client.get(url, headers=auth)
        assert response.status_code == 200
        data = response.json()
        assert set(data["items"][0] if "items" in data else data) >= FIELDS
    response = await client.post(
        path + "/bulk",
        headers=auth,
        json={"items": [{"todo_id": todo["todo_id"], "expected_version": 1}], "status": "todo"},
    )
    assert response.status_code == 200, response.text
    assert set(response.json()["items"][0]) >= FIELDS
    assert response.json()["items"][0]["version"] == 2


async def test_create_status_dates_priority_tags_and_patch_omit_null_roundtrip(env) -> None:
    ctx = await _base(env)
    tag = await _option(ctx, "tags", "testing")
    catalog = await _catalog(ctx)
    today = catalog["server_today"]
    priority = catalog["priorities"][0]["priority_id"]
    tid = tag["item"]["tag_id"]
    path = f"/api/projects/{ctx['pid']}/todos"
    response = await ctx["client"].post(
        path,
        headers=ctx["member_auth"],
        json={
            "title": "fields",
            "status": "in_progress",
            "start_date": "1900-01-01",
            "due_date": today,
            "priority_id": priority,
            "tag_ids": [tid, tid],
            "expected_catalog_revision": catalog["revision"],
        },
    )
    assert response.status_code == 201, response.text
    todo = response.json()
    assert todo["status"] == "in_progress" and todo["tag_ids"] == [tid]
    assert todo["priority_id"] == priority and todo["due_date"] == today
    url = path + f"/{todo['todo_id']}"
    response = await ctx["client"].patch(
        url, headers=ctx["member_auth"], json={"expected_version": 1, "title": "changed"}
    )
    assert response.status_code == 200
    kept = response.json()
    assert all(kept[key] == todo[key] for key in FIELDS)
    response = await ctx["client"].patch(
        url,
        headers=ctx["member_auth"],
        json={
            "expected_version": 2,
            "start_date": None,
            "due_date": None,
            "priority_id": None,
            "tag_ids": [],
            "expected_catalog_revision": catalog["revision"],
        },
    )
    assert response.status_code == 200, response.text
    assert response.json()["start_date"] is None and response.json()["due_date"] is None
    assert response.json()["priority_id"] is None and response.json()["tag_ids"] == []
    event = [
        e for e in _events(ctx["srv"], ctx["pid"]) if e["event_type"] == "project.todo_updated"
    ][-1]
    assert set(event["payload"]["fields"]) == {"start_date", "due_date", "priority_id", "tag_ids"}
    assert today not in str(event["payload"]) and "testing" not in str(event["payload"])


@pytest.mark.parametrize(
    "field,value",
    [
        ("start_date", "1899-12-31"),
        ("start_date", "2024-02-30"),
        ("due_date", "2100-02-29"),
        ("due_date", "2026-09-28T00:00:00Z"),
        ("start_date", "2026-9-28"),
        ("due_date", "10000-01-01"),
        ("tag_ids", None),
        ("expected_catalog_revision", True),
    ],
)
async def test_new_todo_inputs_are_strict(env, field, value) -> None:
    ctx = await _base(env)
    response = await ctx["client"].post(
        f"/api/projects/{ctx['pid']}/todos",
        headers=ctx["owner_auth"],
        json={"title": "invalid", field: value},
    )
    assert response.status_code == 422, response.text


async def test_catalog_revision_pairing_old_expected_version_only_noop_and_stale(env) -> None:
    ctx = await _base(env)
    todo = await _create_todo(ctx)
    url = f"/api/projects/{ctx['pid']}/todos/{todo['todo_id']}"
    for body, expected in (
        ({"expected_version": 1}, 400),
        ({"expected_version": 1, "expected_catalog_revision": 1}, 422),
        ({"expected_version": 1, "priority_id": None}, 422),
        ({"expected_version": 1, "tag_ids": []}, 422),
        ({"expected_version": True, "status": "done"}, 422),
        ({"expected_version": 1, "priority_id": None, "expected_catalog_revision": True}, 422),
    ):
        response = await ctx["client"].patch(url, headers=ctx["owner_auth"], json=body)
        assert response.status_code == expected, response.text
    response = await ctx["client"].post(
        f"/api/projects/{ctx['pid']}/todos",
        headers=ctx["owner_auth"],
        json={"title": "stale", "expected_catalog_revision": 2},
    )
    assert response.status_code == 409, response.text
    await _option(ctx, "tags", "bump")
    response = await ctx["client"].patch(
        url,
        headers=ctx["owner_auth"],
        json={
            "expected_version": 1,
            "status": "done",
            "tag_ids": [],
            "expected_catalog_revision": 1,
        },
    )
    assert (
        response.status_code == 409
        and response.json()["error"]["details"]["reason"] == "catalog_revision_conflict"
    )
    loaded = await ctx["client"].get(url, headers=ctx["owner_auth"])
    assert loaded.json()["version"] == 1 and loaded.json()["catalog_revision"] == 2


async def test_dates_merged_range_and_unchanged_overdue_are_enforced(env) -> None:
    ctx = await _base(env)
    catalog = await _catalog(ctx)
    today = date.fromisoformat(catalog["server_today"])
    path = f"/api/projects/{ctx['pid']}/todos"
    response = await ctx["client"].post(
        path,
        headers=ctx["owner_auth"],
        json={
            "title": "old",
            "start_date": "1900-01-01",
            "due_date": str(today - timedelta(days=1)),
        },
    )
    assert response.status_code == 422, response.text
    response = await ctx["client"].post(
        path,
        headers=ctx["owner_auth"],
        json={
            "title": "date",
            "start_date": str(today),
            "due_date": str(today + timedelta(days=1)),
        },
    )
    assert response.status_code == 201, response.text
    todo = response.json()
    url = path + f"/{todo['todo_id']}"
    response = await ctx["client"].patch(
        url,
        headers=ctx["owner_auth"],
        json={"expected_version": 1, "start_date": str(today + timedelta(days=2))},
    )
    assert response.status_code == 422
    with ctx["srv"].services.db.transaction() as conn:
        conn.execute(
            "UPDATE project_todos SET start_date = ?, due_date = ? WHERE todo_id = ?",
            ("2000-01-01", "2000-02-01", todo["todo_id"]),
        )
    response = await ctx["client"].patch(
        url,
        headers=ctx["owner_auth"],
        json={"expected_version": 1, "due_date": "2000-02-01", "status": "done"},
    )
    assert response.status_code == 200, response.text
    response = await ctx["client"].patch(
        url, headers=ctx["owner_auth"], json={"expected_version": 2, "due_date": "2000-02-02"}
    )
    assert response.status_code == 422
    response = await ctx["client"].patch(
        url, headers=ctx["owner_auth"], json={"expected_version": 2, "due_date": None}
    )
    assert response.status_code == 200


async def test_archived_references_keep_remove_cannot_add_and_rename_no_todo_bump(env) -> None:
    ctx = await _base(env)
    tag = (await _option(ctx, "tags", "retired"))["item"]["tag_id"]
    catalog = await _catalog(ctx)
    priority = catalog["priorities"][0]["priority_id"]
    path = f"/api/projects/{ctx['pid']}/todos"
    response = await ctx["client"].post(
        path,
        headers=ctx["owner_auth"],
        json={
            "title": "referenced",
            "priority_id": priority,
            "tag_ids": [tag],
            "expected_catalog_revision": catalog["revision"],
        },
    )
    assert response.status_code == 201, response.text
    todo = response.json()
    url = path + f"/{todo['todo_id']}"
    for kind, identifier in (("priorities", priority), ("tags", tag)):
        before = await _catalog(ctx)
        response = await ctx["client"].post(
            _plan(ctx) + f"/{kind}/{identifier}/archive",
            headers=ctx["owner_auth"],
            json={"expected_revision": before["revision"]},
        )
        assert response.status_code == 200
    catalog = await _catalog(ctx)
    response = await ctx["client"].patch(
        url,
        headers=ctx["owner_auth"],
        json={
            "expected_version": 1,
            "title": "kept",
            "priority_id": priority,
            "tag_ids": [tag],
            "expected_catalog_revision": catalog["revision"],
        },
    )
    assert response.status_code == 200, response.text
    for fields in ({"priority_id": priority}, {"tag_ids": [tag]}):
        response = await ctx["client"].post(
            path,
            headers=ctx["owner_auth"],
            json={
                "title": "invalid new",
                "expected_catalog_revision": catalog["revision"],
                **fields,
            },
        )
        assert response.status_code == 422, response.text
    response = await ctx["client"].patch(
        _plan(ctx) + f"/tags/{tag}",
        headers=ctx["owner_auth"],
        json={"expected_revision": catalog["revision"], "name": "archived renamed"},
    )
    assert response.status_code == 200
    loaded = (await ctx["client"].get(url, headers=ctx["owner_auth"])).json()
    assert loaded["version"] == 2 and loaded["tag_ids"] == [tag]
    response = await ctx["client"].patch(
        url,
        headers=ctx["owner_auth"],
        json={
            "expected_version": 2,
            "priority_id": None,
            "tag_ids": [],
            "expected_catalog_revision": loaded["catalog_revision"],
        },
    )
    assert response.status_code == 200
    response = await ctx["client"].patch(
        url,
        headers=ctx["owner_auth"],
        json={
            "expected_version": 3,
            "tag_ids": [tag],
            "expected_catalog_revision": loaded["catalog_revision"],
        },
    )
    assert response.status_code == 422


async def test_fields_follow_creator_assignee_acl_member_removal_and_cross_ref_422(env) -> None:
    ctx = await _base(env)
    own = await _create_todo(ctx, auth=ctx["member_auth"])
    other = await _create_todo(ctx)
    for todo, expected in ((own, 200), (other, 403)):
        response = await ctx["client"].patch(
            f"/api/projects/{ctx['pid']}/todos/{todo['todo_id']}",
            headers=ctx["member_auth"],
            json={"expected_version": 1, "start_date": "2024-02-29"},
        )
        assert response.status_code == expected, response.text
    second = await ctx["client"].post(
        "/api/projects", headers=ctx["owner_auth"], json={"name": "foreign"}
    )
    foreign = await ctx["client"].get(
        f"/api/projects/{second.json()['project_id']}/plan/catalog", headers=ctx["owner_auth"]
    )
    response = await ctx["client"].patch(
        f"/api/projects/{ctx['pid']}/todos/{own['todo_id']}",
        headers=ctx["member_auth"],
        json={
            "expected_version": 2,
            "priority_id": foreign.json()["priorities"][0]["priority_id"],
            "expected_catalog_revision": 1,
        },
    )
    assert response.status_code == 422, response.text
    response = await ctx["client"].get(
        f"/api/projects/{ctx['pid']}/todos/{own['todo_id']}", headers=ctx["member_auth"]
    )
    assert response.status_code == 200 and response.json()["version"] == 2
