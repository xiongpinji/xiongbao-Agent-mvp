"""Actual mounted OpenAPI preserves typed view requests and response contracts."""

from __future__ import annotations

from tests.integration.test_project_todo_views_api import VIEW_KEYS
from tests.support.app import octop_client, write_octop_config


async def test_view_openapi_has_eight_methods_strict_versions_and_real_definitions(tmp_octop_home):
    write_octop_config(tmp_octop_home, enable_api_docs=True)
    async with octop_client(tmp_octop_home, patch_llm=False) as (client, _):
        response = await client.get("/api/openapi.json")
        assert response.status_code == 200, response.text
        doc = response.json()
    schemas, paths = doc["components"]["schemas"], doc["paths"]
    root = "/api/projects/{project_id}/plan/views"
    methods = {
        (root, "get"): ("200", "ViewListResponse"),
        (root, "post"): ("201", "ViewMutationResponse"),
        (root + "/order", "put"): ("200", "ViewOrderResponse"),
        (root + "/default", "put"): ("200", "ViewDefaultResponse"),
        (root + "/{view_id}", "get"): ("200", None),
        (root + "/{view_id}", "patch"): ("200", "ViewMutationResponse"),
        (root + "/{view_id}/archive", "post"): ("200", "ViewMutationResponse"),
        (root + "/{view_id}/restore", "post"): ("200", "ViewMutationResponse"),
    }
    assert {
        (path, method)
        for path, routes in paths.items()
        if path.startswith(root)
        for method in routes
    } == set(methods)
    for (path, method), (status, schema) in methods.items():
        operation = paths[path][method]
        assert operation["summary"] and status in operation["responses"]
        payload = operation["responses"][status]["content"]["application/json"]["schema"]
        if schema:
            assert payload["$ref"].endswith("/" + schema)
        else:
            assert payload["discriminator"]["propertyName"] == "type" and len(payload["oneOf"]) == 5
    for kind in ("List", "Table", "Board", "Gantt", "Calendar"):
        body = schemas["Create" + kind + "ViewBody"]
        assert set(body["required"]) == {
            "expected_revision",
            "expected_catalog_revision",
            "name",
            "type",
            "definition",
        }
        assert body["additionalProperties"] is False
        for field in ("expected_revision", "expected_catalog_revision"):
            assert (
                body["properties"][field]["type"] == "integer"
                and body["properties"][field]["minimum"] == 1
            )
        definition_ref = body["properties"]["definition"]["$ref"].rsplit("/", 1)[1]
        assert definition_ref in (
            "PlanDefinition",
            "PublicTableDefinition",
            "BoardDefinition",
            "GanttDefinition",
            "CalendarDefinition",
        )
        dto = schemas[kind + "ViewResponse"]
        assert set(dto["required"]) == VIEW_KEYS and set(dto["properties"]) == VIEW_KEYS
        assert dto["properties"]["definition"]["$ref"].endswith("/" + definition_ref)
    post_schema = paths[root]["post"]["requestBody"]["content"]["application/json"]["schema"]
    assert post_schema["discriminator"]["propertyName"] == "type" and len(post_schema["oneOf"]) == 5
    assert set(post_schema["discriminator"]["mapping"]) == {
        "list",
        "table",
        "board",
        "gantt",
        "calendar",
    }
    assert set(schemas["ViewListResponse"]["required"]) == {
        "project_id",
        "revision",
        "default_view_id",
        "items",
    }
    assert set(schemas["ViewMutationResponse"]["required"]) == {
        "revision",
        "default_view_id",
        "item",
    }
    assert set(schemas["ViewOrderResponse"]["required"]) == {"revision", "default_view_id", "items"}
    assert set(schemas["ViewDefaultResponse"]["required"]) == {"revision", "default_view_id"}
    for schema, required in (
        ("UpdateViewBody", "expected_version"),
        ("ViewVersionBody", "expected_version"),
        ("OrderViewsBody", "expected_revision"),
        ("DefaultViewBody", "expected_revision"),
    ):
        value = schemas[schema]
        assert required in value["required"] and value["additionalProperties"] is False
        assert (
            value["properties"][required]["type"] == "integer"
            and value["properties"][required]["minimum"] == 1
        )
    for schema in (
        "PlanDefinition",
        "PublicTableDefinition",
        "BoardDefinition",
        "GanttDefinition",
        "CalendarDefinition",
    ):
        value = schemas[schema]
        assert set(value["required"]) >= {"schema_version", "fields", "group_by", "filters", "sort"}
        assert value["additionalProperties"] is False
        assert (
            value["properties"]["fields"]["items"]["enum"]
            and value["properties"]["filters"]["items"]["anyOf"]
        )
        assert value["properties"]["sort"]["items"]["$ref"].endswith("/SortSpec")
    assert schemas["CalendarDefinition"]["properties"]["calendar"]["$ref"].endswith(
        "/CalendarSettings"
    )
    assert schemas["GanttDefinition"]["properties"]["gantt"]["$ref"].endswith("/GanttSettings")

    table = schemas["PublicTableDefinition"]["properties"]
    assert table["show_subtodos"]["type"] == "boolean"
    assert table["show_subtodos"]["default"] is False
    assert table["fields"]["maxItems"] == 10
    assert "attachments" not in table["fields"]["items"]["enum"]
    for name in ("PlanDefinition", "BoardDefinition", "GanttDefinition", "CalendarDefinition"):
        assert "show_subtodos" not in schemas[name]["properties"]
