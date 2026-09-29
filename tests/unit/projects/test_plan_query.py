"""Strict requests, effective definitions and safe snapshot orchestration."""

from __future__ import annotations

import json

import pytest
from tests.unit.db.test_project_plan_query import create_view, query
from tests.unit.db.test_project_plan_query import query_case as query_case

from octop.infra.errors import OctopError
from octop.infra.projects.plan_query import PlanQueryRequest


@pytest.mark.parametrize(
    "extra",
    [
        {"override_definition": None},
        {"window": None},
        {"bucket": None},
        {"window": {"start_date": "2027-01-01", "end_date": "2027-01-01"}},
        {"bucket": "scheduled"},
        {"expected_view_version": 2**100},
        {"expected_catalog_revision": 2**100},
    ],
)
def test_request_presence_and_integer_boundaries_are_strict(query_case, extra):
    with pytest.raises(OctopError) as caught:
        query(query_case, **extra)
    assert caught.value.status == 422
    assert caught.value.details == {"reason": "invalid_query_request"}


@pytest.mark.parametrize(
    "field,values",
    [("assignee", [123456]), ("priority", ["foreign-priority"]), ("tags", ["foreign-tag"])],
)
def test_incoming_override_cannot_use_invalid_current_references(query_case, field, values):
    with pytest.raises(OctopError) as caught:
        query(
            query_case,
            filters=[{"field": field, "op": "any" if field == "tags" else "in", "values": values}],
        )
    assert caught.value.status == 422
    assert caught.value.details == {"reason": "invalid_query_request"}


def test_bad_saved_definition_is_a_safe_integrity_error(query_case):
    with query_case["pool"].transaction() as conn:
        conn.execute(
            "UPDATE project_todo_views SET definition_json=? WHERE view_id=?",
            ('{"private_unknown_field":"SECRET_DEFINITION"}', query_case["view_id"]),
        )
    with pytest.raises(OctopError) as caught:
        query_case["service"].query(
            query_case["pid"],
            user_id=query_case["owner"],
            data={
                "view_id": query_case["view_id"],
                "expected_view_version": 1,
                "expected_catalog_revision": query_case["revision"],
            },
        )
    assert caught.value.status == 500
    assert "SECRET" not in str(caught.value) and "private_unknown_field" not in str(caught.value)


def test_mutated_request_and_nested_definition_are_revalidated(query_case):
    from octop.infra.projects.plan_definition import default_definition

    raw = {
        "view_id": query_case["view_id"],
        "expected_view_version": 1,
        "expected_catalog_revision": query_case["revision"],
        "override_definition": default_definition("table"),
    }
    request = PlanQueryRequest.model_validate(raw)
    request.override_definition.filters.append(
        {"field": "title", "op": "contains", "value": "SECRET", "private_extra": 9}
    )
    with pytest.raises(OctopError) as caught:
        query_case["service"].query(query_case["pid"], user_id=query_case["owner"], data=request)
    assert caught.value.status == 422
    assert "SECRET" not in str(caught.value)
    assert caught.value.__cause__ is None
    assert caught.value.__suppress_context__


@pytest.mark.parametrize(
    "window",
    [
        {"start_date": "2027-04-04", "end_date": "2027-04-02"},
        {"start_date": "2027-01-01", "end_date": "2028-01-02"},
        {"start_date": "２０２７-04-02", "end_date": "2027-04-02"},
        {"start_date": "2027-02-29", "end_date": "2027-04-02"},
        {"start_date": "1899-12-31", "end_date": "1900-01-01"},
    ],
)
def test_date_window_rejects_invalid_calendar_days_and_span(query_case, window):
    with pytest.raises(OctopError) as caught:
        query(
            query_case,
            kind="calendar",
            view_id=create_view(query_case, "calendar"),
            window=window,
            bucket="scheduled",
        )
    assert caught.value.status == 422 and caught.value.details == {
        "reason": "invalid_query_request"
    }


@pytest.mark.parametrize(
    "sqlstate,revoked,expected_status",
    [("40001", False, 409), ("40001", True, 404), ("40P01", False, 409), ("55P03", False, 409)],
)
def test_transaction_conflict_rolls_back_before_acl_only_read_without_retry(
    query_case, monkeypatch, sqlstate, revoked, expected_status
):
    repo = query_case["service"]._services.project_plan_query_repo
    calls, acl_sql = [], []
    original_acl = repo.can_read_project

    class DriverConflict(Exception):
        pass

    failure = DriverConflict("SECRET_DSN_AND_DRIVER_DETAIL")
    failure.sqlstate = sqlstate

    def fail_materialization(conn, *args, **kwargs):
        calls.append("materialize")
        conn.execute(
            "UPDATE project_todo_views SET name='rolled back' WHERE view_id=?",
            (query_case["view_id"],),
        )
        raise failure

    def check_acl(project_id, actor):
        calls.append("acl-only")
        assert not query_case["pool"]._conn.in_transaction
        if revoked:
            with query_case["pool"].transaction() as conn:
                conn.execute(
                    "DELETE FROM project_members WHERE project_id=? AND user_id=?",
                    (project_id, actor),
                )
        query_case["pool"]._conn.set_trace_callback(acl_sql.append)
        try:
            return original_acl(project_id, actor)
        finally:
            query_case["pool"]._conn.set_trace_callback(None)

    monkeypatch.setattr(repo, "materialize_in_connection", fail_materialization)
    monkeypatch.setattr(repo, "can_read_project", check_acl)
    with pytest.raises(OctopError) as caught:
        query(query_case)
    assert caught.value.status == expected_status
    assert calls == ["materialize", "acl-only"]
    assert not query_case["pool"]._conn.in_transaction
    assert "SECRET" not in str(caught.value) and caught.value.__cause__ is None
    assert not any("project_todo" in sql or "users" in sql for sql in acl_sql)
    with query_case["pool"].connect() as conn:
        assert (
            conn.execute(
                "SELECT name FROM project_todo_views WHERE view_id=?", (query_case["view_id"],)
            ).fetchone()[0]
            == "表格"
        )


def test_keyboard_interrupt_has_no_open_read_transaction_or_partial_write(query_case, monkeypatch):
    repo = query_case["service"]._services.project_plan_query_repo

    def interrupt(conn, *args, **kwargs):
        conn.execute(
            "UPDATE project_todo_views SET name='interrupted' WHERE view_id=?",
            (query_case["view_id"],),
        )
        raise KeyboardInterrupt

    monkeypatch.setattr(repo, "bootstrap_in_connection", interrupt)
    with pytest.raises(KeyboardInterrupt):
        query(query_case)
    assert not query_case["pool"]._conn.in_transaction
    with query_case["pool"].connect() as conn:
        assert (
            conn.execute(
                "SELECT name FROM project_todo_views WHERE view_id=?", (query_case["view_id"],)
            ).fetchone()[0]
            == "表格"
        )


def test_member_metadata_is_in_the_single_complete_sorted_lock_set_before_today(
    query_case, monkeypatch
):
    from octop.infra.db.repos import project_plan_locks

    captured = []
    original = project_plan_locks.member_roles

    def record(db, conn, project_id, user_ids, *, write):
        captured.append(list(user_ids))
        return original(db, conn, project_id, user_ids, write=write)

    def today(timezone):
        assert captured == [[query_case["owner"], query_case["a"], query_case["b"]]]
        assert timezone == "Asia/Shanghai"
        return "2027-04-02"

    monkeypatch.setattr(project_plan_locks, "member_roles", record)
    monkeypatch.setattr(project_plan_locks, "server_today", today)
    result = query(
        query_case,
        group_by="assignee",
        filters=[{"field": "assignee", "op": "not_in", "values": [query_case["a"]]}],
    )
    assert result["server_today"] == "2027-04-02"
    assert captured == [[query_case["owner"], query_case["a"], query_case["b"]]]


def test_shared_invalid_member_reference_is_preserved_and_valid_override_wins(query_case):
    from octop.infra.projects.plan_definition import default_definition

    definition = default_definition("table")
    definition["filters"] = [{"field": "assignee", "op": "in", "values": [query_case["a"]]}]
    stored = json.dumps(definition)
    with query_case["pool"].transaction() as conn:
        conn.execute(
            "UPDATE project_todo_views SET definition_json=? WHERE view_id=?",
            (stored, query_case["view_id"]),
        )
        conn.execute(
            "DELETE FROM project_members WHERE project_id=? AND user_id=?",
            (query_case["pid"], query_case["a"]),
        )
    request = {
        "view_id": query_case["view_id"],
        "expected_view_version": 1,
        "expected_catalog_revision": query_case["revision"],
    }
    with pytest.raises(OctopError) as caught:
        query_case["service"].query(query_case["pid"], user_id=query_case["b"], data=request)
    assert caught.value.status == 409
    assert caught.value.details == {
        "reason": "filter_reference_unavailable",
        "condition_indices": [0],
    }
    assert query(query_case)["total"] == 4
    with query_case["pool"].connect() as conn:
        assert (
            conn.execute(
                "SELECT definition_json,version FROM project_todo_views WHERE view_id=?",
                (query_case["view_id"],),
            ).fetchone()[0]
            == stored
        )
    with pytest.raises(OctopError) as unauthorized:
        query_case["service"].query(query_case["pid"], user_id=query_case["outsider"], data=request)
    assert unauthorized.value.status == 404 and not unauthorized.value.details


def test_normal_already_typed_request_preserves_omitted_optional_fields(query_case):
    from octop.infra.projects.plan_definition import default_definition

    request = PlanQueryRequest.model_validate(
        {
            "view_id": query_case["view_id"],
            "expected_view_version": 1,
            "expected_catalog_revision": query_case["revision"],
            "override_definition": default_definition("table"),
        }
    )
    assert not request.model_fields_set & {"window", "bucket"}
    result = query_case["service"].query(
        query_case["pid"], user_id=query_case["owner"], data=request
    )
    assert result["total"] == 4


@pytest.mark.parametrize(
    "window",
    [
        {"start_date": "1900-01-01", "end_date": "1900-01-01"},
        {"start_date": "9999-12-31", "end_date": "9999-12-31"},
        {"start_date": "2000-01-01", "end_date": "2000-12-31"},
        {"start_date": "2000-02-29", "end_date": "2000-02-29"},
    ],
)
def test_valid_calendar_boundary_windows_are_inclusive(query_case, window):
    result = query(
        query_case,
        kind="calendar",
        view_id=create_view(query_case, "calendar"),
        window=window,
        bucket="unscheduled",
    )
    assert result["total"] == result["matched_total"] == result["unscheduled_total"] == 4


@pytest.mark.parametrize("overdue", [False, True])
def test_today_is_sampled_once_and_only_invalidates_overdue_queries(
    query_case, monkeypatch, overdue
):
    from octop.infra.db.repos import project_plan_locks

    day, calls = ["2027-04-01"], []

    def today(timezone):
        calls.append(timezone)
        return day[0]

    monkeypatch.setattr(project_plan_locks, "server_today", today)
    filters = [{"field": "due_date", "op": "overdue", "value": False}] if overdue else []
    first = query(query_case, limit=1, filters=filters)
    assert first["next_cursor"] is not None and calls == ["Asia/Shanghai"]
    day[0] = "2027-04-02"
    if overdue:
        with pytest.raises(OctopError) as caught:
            query(query_case, limit=1, cursor=first["next_cursor"], filters=filters)
        assert caught.value.status == 409 and caught.value.details == {"reason": "query_changed"}
    else:
        assert (
            query(query_case, limit=1, cursor=first["next_cursor"], filters=filters)["server_today"]
            == "2027-04-02"
        )
    assert calls == ["Asia/Shanghai", "Asia/Shanghai"]
