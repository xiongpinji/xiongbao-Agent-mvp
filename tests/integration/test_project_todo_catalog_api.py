"""C1 project-owned catalog contract through the authenticated HTTP surface."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from tests.integration.test_project_todos_api import _add_user, _base, _create_todo, _events
from tests.support.app import octop_client, write_octop_config

CATALOG_KEYS = {"project_id", "revision", "server_today", "server_timezone", "priorities", "tags"}
PRIORITY_KEYS = {
    "priority_id",
    "name",
    "color",
    "position",
    "archived_at",
    "created_at",
    "updated_at",
}
TAG_KEYS = {"tag_id", "name", "color", "archived_at", "created_at", "updated_at"}


def _plan(ctx: dict[str, Any]) -> str:
    return f"/api/projects/{ctx['pid']}/plan"


async def _catalog(ctx: dict[str, Any], auth: Any = None) -> dict[str, Any]:
    response = await ctx["client"].get(_plan(ctx) + "/catalog", headers=auth or ctx["owner_auth"])
    assert response.status_code == 200, response.text
    return response.json()


async def _option(ctx: dict[str, Any], kind: str, name: str, color: str = "blue") -> dict[str, Any]:
    catalog = await _catalog(ctx)
    response = await ctx["client"].post(
        _plan(ctx) + f"/{kind}",
        headers=ctx["owner_auth"],
        json={"expected_revision": catalog["revision"], "name": name, "color": color},
    )
    assert response.status_code == 201, response.text
    assert set(response.json()) == {"revision", "item"}
    return response.json()


async def test_catalog_defaults_fixed_shapes_member_read_timezone(env) -> None:
    ctx = await _base(env)
    catalog = await _catalog(ctx, ctx["member_auth"])
    assert set(catalog) == CATALOG_KEYS
    assert catalog["project_id"] == ctx["pid"] and catalog["revision"] == 1
    assert catalog["server_timezone"] == ctx["srv"].services.config.default_timezone
    assert len(catalog["server_today"]) == 10
    assert [(p["name"], p["color"], p["position"]) for p in catalog["priorities"]] == [
        ("紧急", "red", 0),
        ("高", "orange", 1),
        ("中", "blue", 2),
        ("低", "gray", 3),
    ]
    assert all(set(p) == PRIORITY_KEYS and p["archived_at"] is None for p in catalog["priorities"])
    assert catalog["tags"] == []
    response = await ctx["client"].get(_plan(ctx) + "/catalog", headers=ctx["outsider_auth"])
    assert response.status_code == 404
    response = await ctx["client"].get(
        "/api/projects/missing/plan/catalog", headers=ctx["owner_auth"]
    )
    assert response.status_code == 404


async def test_all_nine_catalog_writes_and_safe_events(env) -> None:
    ctx = await _base(env)
    priority = await _option(ctx, "priorities", "业务急件", "purple")
    tag = await _option(ctx, "tags", "秘密标签正文", "green")
    assert set(priority["item"]) == PRIORITY_KEYS and set(tag["item"]) == TAG_KEYS
    for kind, option in (("priorities", priority), ("tags", tag)):
        identifier = option["item"]["priority_id" if kind == "priorities" else "tag_id"]
        for method, suffix, fields in (
            ("patch", "", {"name": "新目录名称", "color": "orange"}),
            ("post", "/archive", {}),
            ("post", "/restore", {}),
        ):
            before = await _catalog(ctx)
            response = await getattr(ctx["client"], method)(
                _plan(ctx) + f"/{kind}/{identifier}{suffix}",
                headers=ctx["owner_auth"],
                json={"expected_revision": before["revision"], **fields},
            )
            assert response.status_code == 200, response.text
            assert response.json()["revision"] == before["revision"] + 1
            assert (
                response.json()["item"]["archived_at"] is not None
                if suffix == "/archive"
                else response.json()["item"]["archived_at"] is None
            )
    before = await _catalog(ctx)
    identifiers = [p["priority_id"] for p in reversed(before["priorities"])]
    response = await ctx["client"].put(
        _plan(ctx) + "/priorities/order",
        headers=ctx["owner_auth"],
        json={"expected_revision": before["revision"], "priority_ids": identifiers},
    )
    assert response.status_code == 200, response.text
    assert [p["priority_id"] for p in response.json()["priorities"]] == identifiers
    assert [p["position"] for p in response.json()["priorities"]] == list(range(len(identifiers)))
    events = [
        e
        for e in _events(ctx["srv"], ctx["pid"])
        if e["event_type"] == "project.todo_catalog_updated"
    ]
    assert len(events) == 9
    for event in events:
        payload = event["payload"]
        assert set(payload) <= {"catalog_revision", "catalog_kind", "option_id", "action", "fields"}
        assert set(payload["fields"]) <= {"name", "color", "order", "archived"}
        assert "秘密标签正文" not in str(payload) and "新目录名称" not in str(payload)
    feed = await ctx["client"].get(
        f"/api/projects/{ctx['pid']}/activity", headers=ctx["owner_auth"]
    )
    assert feed.status_code == 200, feed.text
    directory_events = [
        e for e in feed.json()["items"] if e["event_type"] == "project.todo_catalog_updated"
    ]
    assert len(directory_events) == 9
    assert all("catalog_revision" in e and "fields" in e for e in directory_events)
    assert "秘密标签正文" not in feed.text and "payload_json" not in feed.text
    related = await ctx["client"].get(
        f"/api/projects/{ctx['pid']}/activity?scope=related", headers=ctx["member_auth"]
    )
    assert not [
        e for e in related.json()["items"] if e["event_type"] == "project.todo_catalog_updated"
    ]


@pytest.mark.parametrize(
    "payload",
    [
        {"expected_revision": True, "name": "x", "color": "blue"},
        {"expected_revision": "1", "name": "x", "color": "blue"},
        {"expected_revision": 1, "name": "x", "color": "#000"},
        {"expected_revision": 1, "name": "x\x00", "color": "blue"},
        {"expected_revision": 1, "name": "x" * 41, "color": "blue"},
        {"expected_revision": 1, "name": "   ", "color": "blue"},
        {"expected_revision": 1, "name": "x", "color": "blue", "actor_user_id": 1},
    ],
)
async def test_catalog_request_structure_is_strict(env, payload) -> None:
    ctx = await _base(env)
    response = await ctx["client"].post(
        _plan(ctx) + "/tags", headers=ctx["owner_auth"], json=payload
    )
    assert response.status_code == 422, response.text
    assert (await _catalog(ctx))["revision"] == 1


async def test_catalog_acl_stale_revision_archive_and_cross_project_ids(env) -> None:
    ctx = await _base(env)
    body = {"expected_revision": 1, "name": "x", "color": "blue"}
    for auth, expected in ((ctx["member_auth"], 403), (ctx["outsider_auth"], 404)):
        response = await ctx["client"].post(_plan(ctx) + "/tags", headers=auth, json=body)
        assert response.status_code == expected, response.text
    admin_auth, _ = await _add_user(ctx, "catalog_admin", "admin")
    response = await ctx["client"].post(_plan(ctx) + "/tags", headers=admin_auth, json=body)
    assert response.status_code == 201, response.text
    response = await ctx["client"].post(
        _plan(ctx) + "/tags", headers=ctx["owner_auth"], json={**body, "name": "stale"}
    )
    assert (
        response.status_code == 409
        and response.json()["error"]["details"]["reason"] == "catalog_revision_conflict"
    )
    other = await ctx["client"].post(
        "/api/projects", headers=ctx["owner_auth"], json={"name": "other"}
    )
    identifier = response_id = (await _catalog(ctx))["tags"][0]["tag_id"]
    response = await ctx["client"].patch(
        f"/api/projects/{other.json()['project_id']}/plan/tags/{response_id}",
        headers=ctx["owner_auth"],
        json={"expected_revision": 1, "name": "cross"},
    )
    assert response.status_code == 404, response.text
    with ctx["srv"].services.db.transaction() as conn:
        conn.execute("UPDATE project_spaces SET archived = 1 WHERE project_id = ?", (ctx["pid"],))
    response = await ctx["client"].post(
        _plan(ctx) + f"/tags/{identifier}/archive",
        headers=ctx["owner_auth"],
        json={"expected_revision": 2},
    )
    assert (
        response.status_code == 409
        and response.json()["error"]["details"]["reason"] == "project_archived"
    )
    assert (await _catalog(ctx))["revision"] == 2
    assert (await _create_todo(ctx))["version"] == 1


async def test_catalog_nfkc_casefold_conflicts_archive_rename_restore_noops(env) -> None:
    ctx = await _base(env)
    first = await _option(ctx, "tags", "ＡＢＣ")
    identifier = first["item"]["tag_id"]
    url = _plan(ctx) + f"/tags/{identifier}"
    response = await ctx["client"].post(
        _plan(ctx) + "/tags",
        headers=ctx["owner_auth"],
        json={"expected_revision": 2, "name": "abc", "color": "red"},
    )
    assert (
        response.status_code == 409
        and response.json()["error"]["details"]["reason"] == "name_conflict"
    )
    response = await ctx["client"].patch(
        url, headers=ctx["owner_auth"], json={"expected_revision": 2, "name": " ＡＢＣ "}
    )
    assert response.status_code == 422
    response = await ctx["client"].post(
        url + "/restore", headers=ctx["owner_auth"], json={"expected_revision": 2}
    )
    assert response.status_code == 422
    response = await ctx["client"].post(
        url + "/archive", headers=ctx["owner_auth"], json={"expected_revision": 2}
    )
    assert response.status_code == 200
    response = await ctx["client"].post(
        url + "/archive", headers=ctx["owner_auth"], json={"expected_revision": 3}
    )
    assert response.status_code == 422
    await _option(ctx, "tags", "abc")
    response = await ctx["client"].post(
        url + "/restore", headers=ctx["owner_auth"], json={"expected_revision": 4}
    )
    assert response.status_code == 409
    response = await ctx["client"].patch(
        url, headers=ctx["owner_auth"], json={"expected_revision": 4, "name": "another"}
    )
    assert response.status_code == 200 and response.json()["item"]["archived_at"] is not None
    response = await ctx["client"].post(
        url + "/restore", headers=ctx["owner_auth"], json={"expected_revision": 5}
    )
    assert response.status_code == 200


async def test_priority_order_requires_exact_active_set(env) -> None:
    ctx = await _base(env)
    ids = [p["priority_id"] for p in (await _catalog(ctx))["priorities"]]
    for requested in (ids[:-1], ids + [ids[0]], ids[:-1] + ["foreign"]):
        response = await ctx["client"].put(
            _plan(ctx) + "/priorities/order",
            headers=ctx["owner_auth"],
            json={"expected_revision": 1, "priority_ids": requested},
        )
        assert response.status_code == 422, response.text
    response = await ctx["client"].put(
        _plan(ctx) + "/priorities/order",
        headers=ctx["owner_auth"],
        json={"expected_revision": 1, "priority_ids": ids},
    )
    assert response.status_code == 422
    assert (await _catalog(ctx))["revision"] == 1


async def test_each_catalog_write_rechecks_manager_acl_and_archived_project(env) -> None:
    ctx = await _base(env)
    tag = (await _option(ctx, "tags", "matrix"))["item"]["tag_id"]
    catalog = await _catalog(ctx)
    priority = catalog["priorities"][0]["priority_id"]
    base = {"expected_revision": catalog["revision"]}
    writes = [
        ("post", "/priorities", {**base, "name": "new priority", "color": "blue"}),
        ("patch", f"/priorities/{priority}", {**base, "name": "new name"}),
        (
            "put",
            "/priorities/order",
            {**base, "priority_ids": [p["priority_id"] for p in reversed(catalog["priorities"])]},
        ),
        ("post", f"/priorities/{priority}/archive", base),
        ("post", f"/priorities/{priority}/restore", base),
        ("post", "/tags", {**base, "name": "new tag", "color": "gray"}),
        ("patch", f"/tags/{tag}", {**base, "color": "purple"}),
        ("post", f"/tags/{tag}/archive", base),
        ("post", f"/tags/{tag}/restore", base),
    ]
    before = len(_events(ctx["srv"], ctx["pid"]))
    for auth, expected in ((ctx["member_auth"], 403), (ctx["outsider_auth"], 404)):
        for method, suffix, body in writes:
            response = await getattr(ctx["client"], method)(
                _plan(ctx) + suffix, headers=auth, json=body
            )
            assert response.status_code == expected, (suffix, response.text)
    with ctx["srv"].services.db.transaction() as conn:
        conn.execute("UPDATE project_spaces SET archived = 1 WHERE project_id = ?", (ctx["pid"],))
    for method, suffix, body in writes:
        response = await getattr(ctx["client"], method)(
            _plan(ctx) + suffix, headers=ctx["owner_auth"], json=body
        )
        assert (
            response.status_code == 409
            and response.json()["error"]["details"]["reason"] == "project_archived"
        ), (suffix, response.text)
    assert (await _catalog(ctx))["revision"] == catalog["revision"]
    assert len(_events(ctx["srv"], ctx["pid"])) == before


async def test_activity_explicit_projection_discards_raw_catalog_and_todo_payload_values(
    env,
) -> None:
    ctx = await _base(env)
    option = (await _option(ctx, "tags", "safe"))["item"]["tag_id"]
    todo = await _create_todo(ctx)
    catalog_payload = {
        "catalog_revision": 2,
        "catalog_kind": "tag",
        "option_id": option,
        "action": "updated",
        "fields": ["name", "color", "title", "description", "body", 42, {"name": "RAW_SECRET"}],
        "name": "RAW_SECRET",
        "color": "RAW_COLOR",
        "body": "RAW_BODY",
        "payload": {"token": "RAW_TOKEN"},
    }
    todo_payload = {
        "fields": ["due_date", "tag_ids", "title", "UNKNOWN_SECRET"],
        "due_date": "RAW_DATE",
        "description": "RAW_DESCRIPTION",
    }
    with ctx["srv"].services.db.transaction() as conn:
        for event_type, object_id, payload in (
            ("project.todo_catalog_updated", option, catalog_payload),
            ("project.todo_updated", todo["todo_id"], todo_payload),
        ):
            conn.execute(
                "INSERT INTO project_events(project_id,actor_user_id,event_type,object_id,payload_json,created_at) VALUES (?,?,?,?,?,?)",
                (
                    ctx["pid"],
                    ctx["owner_uid"],
                    event_type,
                    object_id,
                    json.dumps(payload),
                    9999999999,
                ),
            )
    response = await ctx["client"].get(
        f"/api/projects/{ctx['pid']}/activity", headers=ctx["owner_auth"]
    )
    assert response.status_code == 200, response.text
    assert (
        "RAW_" not in response.text
        and "UNKNOWN_SECRET" not in response.text
        and "payload_json" not in response.text
    )
    items = response.json()["items"][:2]
    assert items[0]["fields"] == ["due_date", "tag_ids", "title"]
    assert items[1]["fields"] == ["color", "name"]
    assert set(items[1]) == {
        "event_id",
        "event_type",
        "actor_user_id",
        "actor_name",
        "object_kind",
        "object_id",
        "message_body",
        "created_at",
        "catalog_revision",
        "catalog_kind",
        "option_id",
        "action",
        "fields",
    }


async def test_catalog_and_todo_openapi_responses_have_required_contract_shapes(
    tmp_octop_home: Path,
) -> None:
    write_octop_config(tmp_octop_home, enable_api_docs=True)
    async with octop_client(tmp_octop_home, patch_llm=False) as (client, _):
        response = await client.get("/api/openapi.json")
        assert response.status_code == 200, response.text
        doc = response.json()
        schemas = doc["components"]["schemas"]
        assert set(schemas["CatalogResponse"]["required"]) == CATALOG_KEYS
        assert set(schemas["PriorityResponse"]["required"]) == PRIORITY_KEYS
        assert set(schemas["TagResponse"]["required"]) == TAG_KEYS
        assert set(schemas["TodoResponse"]["required"]) >= {
            "start_date",
            "due_date",
            "priority_id",
            "tag_ids",
            "catalog_revision",
        }
        prefix = "/api/projects/{project_id}/plan"
        expected = {
            prefix + path
            for path in (
                "/catalog",
                "/priorities",
                "/priorities/order",
                "/priorities/{priority_id}",
                "/priorities/{priority_id}/archive",
                "/priorities/{priority_id}/restore",
                "/tags",
                "/tags/{tag_id}",
                "/tags/{tag_id}/archive",
                "/tags/{tag_id}/restore",
            )
        }
        plan_paths = {path: methods for path, methods in doc["paths"].items() if path in expected}
        assert len(plan_paths) == 10
        assert all(
            route.get("summary") and route["responses"]
            for methods in plan_paths.values()
            for route in methods.values()
        )
        docs = await client.get("/api/docs")
        assert docs.status_code == 200 and "scalar" in docs.text.lower()
