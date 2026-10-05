"""The mounted plan query documents typed contracts and preserves Q3 methods."""

from __future__ import annotations

from typing import Any

from tests.integration.test_project_todos_api import _TODO_KEYS
from tests.support.app import octop_client, write_octop_config

QUERY_KEYS = {
    "items",
    "next_cursor",
    "total",
    "matched_total",
    "unscheduled_total",
    "groups",
    "view_id",
    "view_version",
    "catalog_revision",
    "server_today",
    "server_timezone",
    "query_fingerprint",
}


def _referenced(schema: dict[str, Any], schemas: dict[str, Any]) -> dict[str, Any]:
    return schemas[schema["$ref"].rsplit("/", 1)[1]]


def _non_null(schema: dict[str, Any]) -> dict[str, Any]:
    if "anyOf" not in schema:
        return schema
    choices = [item for item in schema["anyOf"] if item.get("type") != "null"]
    assert len(choices) == 1, choices
    return choices[0]


async def test_actual_openapi_documents_strict_query_and_full_c1_response(
    tmp_octop_home: Any,
) -> None:
    write_octop_config(tmp_octop_home, enable_api_docs=True)
    async with octop_client(tmp_octop_home, patch_llm=False) as (client, _):
        response = await client.get("/api/openapi.json")
        assert response.status_code == 200, response.text
        document = response.json()
    schemas, paths = document["components"]["schemas"], document["paths"]
    path = "/api/projects/{project_id}/plan/query"
    assert "post" in paths[path]
    operation = paths[path]["post"]
    assert isinstance(operation["summary"], str) and operation["summary"].strip()
    assert operation["requestBody"]["required"] is True
    assert {"200", "422"} <= set(operation["responses"])
    request_schema = operation["requestBody"]["content"]["application/json"]["schema"]
    request = _referenced(request_schema, schemas)
    assert request["additionalProperties"] is False
    assert set(request["required"]) == {
        "view_id",
        "expected_view_version",
        "expected_catalog_revision",
    }
    properties = request["properties"]
    assert set(properties) == {
        "view_id",
        "expected_view_version",
        "expected_catalog_revision",
        "override_definition",
        "group_key",
        "window",
        "bucket",
        "limit",
        "cursor",
    }
    for field in ("expected_view_version", "expected_catalog_revision"):
        assert properties[field]["type"] == "integer" and properties[field]["minimum"] == 1
    assert properties["limit"]["type"] == "integer"
    assert properties["limit"]["minimum"] == 1 and properties["limit"]["maximum"] == 100
    assert properties["limit"]["default"] == 50
    assert properties["view_id"]["type"] == "string"
    assert _non_null(properties["cursor"])["type"] == "string"
    assert _non_null(properties["bucket"])["enum"] == ["scheduled", "unscheduled"]

    override = properties["override_definition"]
    alternatives = override.get("anyOf", override.get("oneOf", []))
    definition_names = {item["$ref"].rsplit("/", 1)[1] for item in alternatives if "$ref" in item}
    assert definition_names == {
        "PlanDefinition",
        "PublicTableDefinition",
        "BoardDefinition",
        "GanttDefinition",
        "CalendarDefinition",
    }
    for name in definition_names:
        value = schemas[name]
        assert value["additionalProperties"] is False
        assert {"schema_version", "fields", "group_by", "filters", "sort"} <= set(value["required"])
        assert value["properties"]["filters"]["maxItems"] == 12
        assert value["properties"]["sort"]["maxItems"] == 3

    group = _referenced(_non_null(properties["group_key"]), schemas)
    assert set(group["required"]) == {"kind", "id"}
    assert set(group["properties"]) == {"kind", "id"}
    assert group["additionalProperties"] is False
    assert set(group["properties"]["kind"]["enum"]) == {
        "status",
        "assignee",
        "priority",
        "tag",
        "source",
    }
    assert _non_null(group["properties"]["id"])["type"] == "string"
    window = _referenced(_non_null(properties["window"]), schemas)
    assert set(window["required"]) == {"start_date", "end_date"}
    assert set(window["properties"]) == {"start_date", "end_date"}
    assert window["additionalProperties"] is False
    assert all(window["properties"][name]["type"] == "string" for name in window["properties"])

    response_schema = operation["responses"]["200"]["content"]["application/json"]["schema"]
    result = _referenced(response_schema, schemas)
    assert set(result["properties"]) == set(result["required"]) == QUERY_KEYS
    todo = _referenced(result["properties"]["items"]["items"], schemas)
    assert set(todo["properties"]) == set(todo["required"]) == _TODO_KEYS | {"parent_title"}
    assert _non_null(todo["properties"]["parent_title"])["type"] == "string"
    assert "parent_title" not in schemas["TodoResponse"]["properties"]
    assert todo["properties"]["display_revision"]["type"] == "integer"
    assert todo["properties"]["display_revision"]["minimum"] == 1
    assert todo["properties"]["display_revision"]["maximum"] == 9007199254740991
    for field in ("total", "matched_total", "view_version", "catalog_revision"):
        assert result["properties"][field]["type"] == "integer"
    assert _non_null(result["properties"]["unscheduled_total"])["type"] == "integer"
    for field in ("next_cursor",):
        assert _non_null(result["properties"][field])["type"] == "string"
    for field in ("view_id", "server_today", "server_timezone", "query_fingerprint"):
        assert result["properties"][field]["type"] == "string"
    count_group = _referenced(result["properties"]["groups"]["items"], schemas)
    assert set(count_group["properties"]) == set(count_group["required"]) == {"key", "count"}
    assert count_group["properties"]["count"]["type"] == "integer"
    assert _referenced(count_group["properties"]["key"], schemas) == group

    # Query registration cannot swallow a Q3 static path or weaken its five types.
    root = "/api/projects/{project_id}/plan/views"
    expected = {
        (root, "get"),
        (root, "post"),
        (root + "/order", "put"),
        (root + "/default", "put"),
        (root + "/{view_id}", "get"),
        (root + "/{view_id}", "patch"),
        (root + "/{view_id}/archive", "post"),
        (root + "/{view_id}/restore", "post"),
    }
    assert {
        (entry, method)
        for entry, methods in paths.items()
        if entry.startswith(root)
        for method in methods
    } == expected
    typed_view = paths[root + "/{view_id}"]["get"]["responses"]["200"]["content"][
        "application/json"
    ]["schema"]
    assert typed_view["discriminator"]["propertyName"] == "type"
    assert set(typed_view["discriminator"]["mapping"]) == {
        "list",
        "table",
        "board",
        "gantt",
        "calendar",
    }
    assert len(typed_view["oneOf"]) == 5
