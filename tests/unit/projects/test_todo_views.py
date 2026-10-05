"""View service strict definitions, presence, safe errors and SQL ID bounds."""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest
from tests.unit.db.test_project_todo_views import _raw
from tests.unit.db.test_project_todo_views import case as case

from octop.infra.errors import ErrorCode, OctopError
from octop.infra.projects import todo_views as module
from octop.infra.projects.plan_definition import default_definition
from octop.infra.projects.todo_views import ProjectTodoViewService, view_error


@pytest.fixture
def service_case(case):
    case["service"] = ProjectTodoViewService(SimpleNamespace(project_todo_view_repo=case["repo"]))
    return case


def _create(case, *, name="new", kind="table", definition=None, revision=1, catalog=1):
    return case["service"].create_view(
        case["pid"],
        actor_user_id=case["owner"],
        expected_revision=revision,
        expected_catalog_revision=catalog,
        name=name,
        view_type=kind,
        definition=default_definition(kind) if definition is None else definition,
    )


@pytest.mark.parametrize("kind", ["list", "table", "board", "gantt", "calendar"])
def test_all_five_types_call_accepted_q1_and_return_only_public_dto(
    service_case, kind, monkeypatch
):
    calls = []
    parse = module.parse_definition

    def record(view_type, value):
        calls.append(view_type)
        return parse(view_type, value)

    monkeypatch.setattr(module, "parse_definition", record)
    result = _create(service_case, kind=kind)
    assert kind in calls
    assert set(result) == {"revision", "default_view_id", "item"}
    assert set(result["item"]) == {
        "view_id",
        "project_id",
        "name",
        "type",
        "definition",
        "version",
        "position",
        "archived_at",
        "created_at",
        "updated_at",
    }
    assert result["item"]["type"] == kind
    assert result["item"]["definition"] == default_definition(kind)
    assert result["revision"] == 2 and result["item"]["version"] == 1
    assert "name_key" not in result["item"] and "definition_json" not in result["item"]


@pytest.mark.parametrize("name", ["", "  ", "x" * 41, "\x00safe", "safe\nname", 1, True, None])
def test_invalid_raw_names_do_not_change_any_persisted_state(service_case, name):
    before = _raw(service_case)
    with pytest.raises(OctopError) as caught:
        _create(service_case, name=name)
    assert caught.value.status == 422
    assert caught.value.details == {"reason": "invalid_view_request"}
    assert _raw(service_case) == before


@pytest.mark.parametrize("field", ["expected_revision", "expected_catalog_revision"])
@pytest.mark.parametrize("value", [None, True, False, "1", 1.0, 0, -1])
def test_create_versions_strict_positive_without_bool_coercion(service_case, field, value):
    before = _raw(service_case)
    kwargs = {"revision": value} if field == "expected_revision" else {"catalog": value}
    with pytest.raises(OctopError) as caught:
        _create(service_case, **kwargs)
    assert caught.value.status == 422 and _raw(service_case) == before


def test_name_trim_is_distinct_from_literal_filter_preservation_and_null_array_order(service_case):
    value = default_definition("table")
    value["filters"] = [
        {"field": "title", "op": "contains", "value": "  %_\\Ａß  "},
        {"field": "assignee", "op": "not_in", "values": [None, service_case["member"]]},
        {"field": "priority", "op": "in", "values": [None]},
    ]
    result = _create(service_case, name="  trimmed  ", definition=value)
    assert result["item"]["name"] == "trimmed"
    assert result["item"]["definition"] == value
    stored = service_case["repo"].get_view(
        service_case["pid"], result["item"]["view_id"], user_id=service_case["owner"]
    )
    assert json.loads(stored.definition_json) == value


@pytest.mark.parametrize(
    "bad",
    [
        {
            "schema_version": 1,
            "fields": ["title"],
            "group_by": None,
            "filters": [
                {
                    "field": "title",
                    "op": "contains",
                    "value": "private-body",
                    "private-nested-key": "secret",
                }
            ],
            "sort": [],
        },
        {
            "schema_version": 1,
            "fields": ["title", "attachment"],
            "group_by": None,
            "filters": [],
            "sort": [],
        },
        {"schema_version": True, "fields": ["title"], "group_by": None, "filters": [], "sort": []},
        None,
    ],
)
def test_definition_parse_errors_are_safe_fixed_reasons_without_raw_chain(service_case, bad):
    before = _raw(service_case)
    # None is an explicit invalid incoming definition here, not helper omission.
    with pytest.raises(OctopError) as caught:
        service_case["service"].create_view(
            service_case["pid"],
            actor_user_id=service_case["owner"],
            expected_revision=1,
            expected_catalog_revision=1,
            name="safe",
            view_type="table",
            definition=bad,
        )
    error = caught.value
    assert error.status == 422 and error.details == {"reason": "invalid_definition"}
    assert error.__cause__ is None
    assert "private-body" not in str(error) and "private-nested-key" not in str(error)
    assert _raw(service_case) == before


def test_patch_presence_type_candidates_and_explicit_catalog_pair(service_case):
    service, pid, owner = service_case["service"], service_case["pid"], service_case["owner"]
    table, board = service.list_views(pid, user_id=owner)["items"]
    before = _raw(service_case)
    with pytest.raises(OctopError) as missing_revision:
        service.update_view(
            pid,
            table["view_id"],
            actor_user_id=owner,
            expected_version=1,
            definition=default_definition("table"),
        )
    assert missing_revision.value.status == 422 and _raw(service_case) == before
    changed = default_definition("table")
    changed["fields"] = ["title", "due_date"]
    result = service.update_view(
        pid,
        table["view_id"],
        actor_user_id=owner,
        expected_version=1,
        definition=changed,
        expected_catalog_revision=1,
    )
    assert result["item"]["type"] == "table" and result["item"]["version"] == 2
    assert result["item"]["definition"] == changed
    before = _raw(service_case)
    with pytest.raises(OctopError) as incompatible:
        service.update_view(
            pid,
            board["view_id"],
            actor_user_id=owner,
            expected_version=1,
            definition=changed,
            expected_catalog_revision=1,
        )
    assert (
        incompatible.value.details == {"reason": "invalid_definition"}
        and _raw(service_case) == before
    )
    for fields in (
        {"view_type": "calendar"},
        {"name": None},
        {"definition": None},
        {"view_type": None},
        {"expected_catalog_revision": None},
    ):
        with pytest.raises(OctopError):
            service.update_view(
                pid, table["view_id"], actor_user_id=owner, expected_version=2, **fields
            )
        assert _raw(service_case) == before
    switched = service.update_view(
        pid,
        table["view_id"],
        actor_user_id=owner,
        expected_version=2,
        view_type="calendar",
        definition=default_definition("calendar"),
        expected_catalog_revision=1,
    )
    assert switched["item"]["type"] == "calendar" and switched["item"]["version"] == 3


@pytest.mark.parametrize("user_id", [2**31, 2**63 - 1])
def test_sqlite_accepts_real_current_member_above_pg_int32_without_truncation(
    service_case, user_id
):
    with service_case["pool"].transaction() as conn:
        conn.execute(
            "INSERT INTO users(id,username,password_hash,role,project_plan_display_sort_key,created_at) VALUES(?,?,?,?,?,1)",
            (user_id, f"large{user_id}", "h", "user", f"large{user_id}"),
        )
    service_case["projects"].add_member(service_case["pid"], user_id, role="member")
    value = default_definition("table")
    value["filters"] = [{"field": "assignee", "op": "in", "values": [user_id, None]}]
    result = _create(service_case, definition=value)
    assert result["item"]["definition"]["filters"][0]["values"] == [user_id, None]


@pytest.mark.parametrize(
    "dialect,value",
    [("sqlite", 2**63), ("sqlite", 2**100), ("postgresql", 2**31), ("postgresql", 2**100)],
)
def test_storage_range_rejected_before_any_sql_binding(service_case, dialect, value):
    before = _raw(service_case)

    def forbidden():
        raise AssertionError("invalid ID reached SQL transaction")

    original = service_case["repo"]._db
    service_case["repo"]._db = SimpleNamespace(dialect=dialect, transaction=forbidden)
    definition = default_definition("table")
    definition["filters"] = [{"field": "assignee", "op": "in", "values": [value]}]
    try:
        with pytest.raises(OctopError) as caught:
            _create(service_case, definition=definition)
        assert caught.value.status == 422
        assert caught.value.details == {"reason": "invalid_assignee"}
        assert str(value) not in str(caught.value)
    finally:
        service_case["repo"]._db = original
    assert _raw(service_case) == before


def test_saved_reference_can_be_read_renamed_archived_and_repaired_after_member_removed(
    service_case,
):
    value = default_definition("table")
    value["filters"] = [{"field": "assignee", "op": "in", "values": [service_case["member"]]}]
    created = _create(service_case, definition=value)
    service_case["projects"].remove_member(
        project_id=service_case["pid"],
        user_id=service_case["member"],
        actor_user_id=service_case["owner"],
    )
    service, pid, owner, vid = (
        service_case["service"],
        service_case["pid"],
        service_case["owner"],
        created["item"]["view_id"],
    )
    assert service.get_view(pid, vid, user_id=owner)["definition"] == value
    renamed = service.update_view(
        pid, vid, actor_user_id=owner, expected_version=1, name="retained"
    )
    assert renamed["item"]["version"] == 2
    archived = service.archive_view(pid, vid, actor_user_id=owner, expected_version=2)
    restored = service.restore_view(pid, vid, actor_user_id=owner, expected_version=3)
    assert archived["item"]["definition"] == restored["item"]["definition"] == value
    before = _raw(service_case)
    with pytest.raises(OctopError) as invalid:
        service.update_view(
            pid,
            vid,
            actor_user_id=owner,
            expected_version=4,
            definition=value,
            expected_catalog_revision=1,
        )
    assert invalid.value.details == {"reason": "invalid_assignee"} and _raw(service_case) == before
    fixed = service.update_view(
        pid,
        vid,
        actor_user_id=owner,
        expected_version=4,
        definition=default_definition("table"),
        expected_catalog_revision=1,
    )
    assert fixed["item"]["version"] == 5 and fixed["item"]["view_id"] == vid


def test_fixed_view_error_namespace_and_unknown_reason_never_echoes_private_data():
    error = view_error("name_conflict", status=409)
    assert error.status == 409 and error.details == {"reason": "name_conflict"}
    assert error.localized_message("en") and error.localized_message("zh")
    with pytest.raises(OctopError) as unknown:
        view_error("private-user-input")
    assert unknown.value.code == ErrorCode.INTERNAL_ERROR
    assert "private-user-input" not in str(unknown.value)


@pytest.mark.parametrize("typed", [False, True])
def test_table_patch_omission_preserves_locked_true_and_explicit_false_changes(service_case, typed):
    from octop.api.routers.project_todo_views import UpdateViewBody

    created = _create(
        service_case, definition=dict(default_definition("table"), show_subtodos=True)
    )
    item = created["item"]
    legacy = default_definition("table")
    legacy.pop("show_subtodos")
    legacy["sort"] = []
    body = UpdateViewBody.model_validate(
        {"expected_version": 1, "definition": legacy, "expected_catalog_revision": 1}
    )
    payload = body.definition if typed else legacy
    changed = service_case["service"].update_view(
        service_case["pid"],
        item["view_id"],
        actor_user_id=service_case["owner"],
        expected_version=1,
        definition=payload,
        expected_catalog_revision=1,
    )["item"]
    assert changed["definition"]["show_subtodos"] is True
    before = _raw(service_case)
    with pytest.raises(OctopError) as caught:
        service_case["service"].update_view(
            service_case["pid"],
            item["view_id"],
            actor_user_id=service_case["owner"],
            expected_version=1,
            definition=dict(legacy, show_subtodos=False),
            expected_catalog_revision=1,
        )
    assert caught.value.status == 409
    assert caught.value.details["reason"] == "view_version_conflict"
    assert _raw(service_case) == before
    renamed = service_case["service"].update_view(
        service_case["pid"],
        item["view_id"],
        actor_user_id=service_case["owner"],
        expected_version=2,
        name="renamed",
    )["item"]
    assert renamed["definition"]["show_subtodos"] is True
    changed = service_case["service"].update_view(
        service_case["pid"],
        item["view_id"],
        actor_user_id=service_case["owner"],
        expected_version=3,
        definition=dict(legacy, show_subtodos=False),
        expected_catalog_revision=1,
    )["item"]
    assert changed["definition"]["show_subtodos"] is False


def test_table_flag_merge_occurs_only_after_locked_acl_version_snapshot(service_case, monkeypatch):
    created = _create(service_case)
    target = created["item"]["view_id"]
    original = service_case["repo"]._write_snapshot

    def locked_snapshot(conn, *args, **kwargs):
        current = conn.execute(
            "SELECT definition_json FROM project_todo_views WHERE view_id=?", (target,)
        ).fetchone()
        stored = json.loads(current[0])
        stored["show_subtodos"] = True
        conn.execute(
            "UPDATE project_todo_views SET definition_json=? WHERE view_id=?",
            (json.dumps(stored), target),
        )
        return original(conn, *args, **kwargs)

    monkeypatch.setattr(service_case["repo"], "_write_snapshot", locked_snapshot)
    legacy = default_definition("table")
    legacy.pop("show_subtodos")
    legacy["sort"] = []
    result = service_case["service"].update_view(
        service_case["pid"],
        target,
        actor_user_id=service_case["owner"],
        expected_version=1,
        definition=legacy,
        expected_catalog_revision=1,
    )
    assert result["item"]["definition"]["show_subtodos"] is True


def test_table_type_switch_requires_clean_definition_and_defaults_false(service_case):
    item = _create(service_case, definition=dict(default_definition("table"), show_subtodos=True))[
        "item"
    ]
    with pytest.raises(OctopError):
        service_case["service"].update_view(
            service_case["pid"],
            item["view_id"],
            actor_user_id=service_case["owner"],
            expected_version=1,
            view_type="list",
            definition=item["definition"],
            expected_catalog_revision=1,
        )
    item = service_case["service"].update_view(
        service_case["pid"],
        item["view_id"],
        actor_user_id=service_case["owner"],
        expected_version=1,
        view_type="list",
        definition=default_definition("list"),
        expected_catalog_revision=1,
    )["item"]
    assert "show_subtodos" not in item["definition"]
    item = service_case["service"].update_view(
        service_case["pid"],
        item["view_id"],
        actor_user_id=service_case["owner"],
        expected_version=2,
        view_type="table",
        definition=default_definition("list"),
        expected_catalog_revision=1,
    )["item"]
    assert item["definition"]["show_subtodos"] is False


def test_legacy_table_read_payload_normalizes_flag_without_storage_write(service_case):
    before = _raw(service_case)
    item = service_case["service"].list_views(service_case["pid"], user_id=service_case["owner"])[
        "items"
    ][0]
    assert item["definition"]["show_subtodos"] is False
    assert _raw(service_case) == before
