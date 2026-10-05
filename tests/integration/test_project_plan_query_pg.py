"""Actual query snapshots and races on root's fresh, dedicated PostgreSQL lease.

The inherited fixture checks loopback identity and owns a unique database per
node. It drains both real pools to zero and drops that database. This module
does not start PostgreSQL, read an application DSN, or retry query operations.
"""

from __future__ import annotations

import base64
import json
import threading
import unicodedata
from contextlib import contextmanager
from functools import cmp_to_key
from types import SimpleNamespace
from typing import Any

import pytest

from octop.infra.db.migrate import run_migrations
from octop.infra.db.pool import SqlitePool
from octop.infra.db.repos import project_plan_locks
from octop.infra.db.repos.project_plan_query import ProjectPlanQueryRepo
from octop.infra.db.repos.project_todo_catalog import ProjectTodoCatalogRepo
from octop.infra.db.repos.project_todo_views import ProjectTodoViewRepo, ViewDefinition
from octop.infra.db.repos.project_todos import ProjectTodoRepo
from octop.infra.db.repos.projects import ProjectRepo
from octop.infra.db.repos.users import UserRepo
from octop.infra.errors import ErrorCode, OctopError
from octop.infra.projects.plan_definition import default_definition
from octop.infra.projects.plan_query import ProjectPlanQueryService
from tests.integration.test_project_todo_fields_pg import _race, _setup
from tests.integration.test_project_todo_fields_pg import postgres_pair as postgres_pair
from tests.integration.test_project_todo_views_pg import _ObservedConnection, _ObservedPool
from tests.integration.test_project_todos_api import _TODO_KEYS


class _QueryObservedConnection(_ObservedConnection):
    def execute(self, sql: str, params: Any = None) -> Any:
        member = "SELECT role FROM project_members" in sql
        if member:
            self._observer.member_requests.append(tuple(params))
        try:
            result = super().execute(sql, params)
        except BaseException as error:
            if getattr(error, "sqlstate", None) == "40001":
                self._observer.serialization_errors.append({"sqlstate": "40001", "sql": sql})
            raise
        if member:
            self._observer.member_completed.append(tuple(params))
        return result


class _QueryObservedPool(_ObservedPool):
    def __init__(self, pool: Any, *, hold_sql: str = "") -> None:
        super().__init__(pool, hold_sql=hold_sql)
        self.member_requests: list[Any] = []
        self.member_completed: list[Any] = []
        self.serialization_errors: list[Any] = []
        self.transactions: list[dict[str, Any]] = []

    @contextmanager
    def transaction(self) -> Any:
        start = len(self.statements)
        record: dict[str, Any] = {"index": len(self.transactions) + 1}
        self.transactions.append(record)
        connection = None
        try:
            with self._pool.transaction() as connection:
                yield _QueryObservedConnection(connection, self)
        finally:
            record["statements"] = self.statements[start:]
            record["transaction_status_after"] = (
                connection.info.transaction_status.name if connection is not None else "NOT_OPENED"
            )


def _service(pool: Any, repo: Any = None) -> ProjectPlanQueryService:
    return ProjectPlanQueryService(
        SimpleNamespace(
            db=pool,
            config=SimpleNamespace(default_timezone="Asia/Shanghai"),
            project_plan_query_repo=ProjectPlanQueryRepo(pool) if repo is None else repo,
            project_todo_view_repo=ProjectTodoViewRepo(pool),
        )
    )


def _request(pool: Any, project_id: str, actor: int, **values: Any) -> dict[str, Any]:
    snapshot = ProjectTodoViewRepo(pool).list_views(project_id, user_id=actor)
    assert snapshot is not None
    view = next(item for item in snapshot.items if item.view_id == snapshot.default_view_id)
    return {
        "view_id": view.view_id,
        "expected_view_version": view.version,
        "expected_catalog_revision": snapshot.catalog_revision,
        **values,
    }


def _definition(kind: str = "table", **values: Any) -> dict[str, Any]:
    return {**default_definition(kind), **values}


def _capture(operation: Any) -> dict[str, Any]:
    try:
        return {"status": 200, "body": operation()}
    except OctopError as error:
        return {
            "status": 404 if error.code == ErrorCode.NOT_FOUND else error.status,
            "details": error.details,
        }


def _todo(pool: Any, pid: str, owner: int, **fields: Any) -> Any:
    result = ProjectTodoRepo(pool).create(project_id=pid, creator_user_id=owner, **fields)
    assert result.outcome == "created" and result.row is not None
    return result.row


def _native_view(pool: Any, pid: str, owner: int, kind: str) -> Any:
    repo = ProjectTodoViewRepo(pool)
    current = repo.list_views(pid, user_id=owner)
    assert current is not None
    mutation = repo.create_view(
        pid,
        actor_user_id=owner,
        expected_revision=current.revision,
        expected_catalog_revision=current.catalog_revision,
        name=kind,
        view_type=kind,
        definition=ViewDefinition((kind,), json.dumps(default_definition(kind))),
    )
    assert mutation.outcome == "created" and mutation.item is not None
    return mutation.item


def _assert_rr(reader: _QueryObservedPool) -> None:
    assert type(reader.pid) is int and reader.pid > 0
    assert reader.statements[0] == "SET TRANSACTION ISOLATION LEVEL REPEATABLE READ"
    assert reader.isolation == "repeatable read"
    assert all(record["transaction_status_after"] == "IDLE" for record in reader.transactions)


def _assert_acl_only_after_rollback(reader: _QueryObservedPool) -> None:
    _assert_rr(reader)
    assert [item["sqlstate"] for item in reader.serialization_errors] == ["40001"]
    assert len(reader.transactions) == 2
    assert reader.transactions[0]["transaction_status_after"] == "IDLE"
    final = reader.transactions[1]["statements"]
    assert len(final) == 1 and final[0].startswith(
        "SELECT 1 FROM project_spaces p JOIN project_members m"
    )
    assert not any(
        name in final[0]
        for name in ("project_todo_views", "project_todos", "catalog", "project_events")
    )
    assert sum("SELECT v.* FROM project_todo_views v" in sql for sql in reader.statements) == 1


def _seed_matrix(env: dict[str, Any]) -> tuple[int, int, int, str, list[Any], list[str], list[str]]:
    pool = env["first"]
    owner, member, admin, pid = _setup(env)
    users = UserRepo(pool)
    prefix = "\ufdfa" * 200 + " same prefix " * 30
    users.set_display_name(owner, prefix + "z")
    users.set_display_name(member, prefix + "a")
    catalog = ProjectTodoCatalogRepo(pool)
    tags = []
    for revision, name in enumerate(("T1", "T2", "unused"), start=1):
        result = catalog.create_option(
            project_id=pid,
            actor_user_id=owner,
            expected_revision=revision,
            kind="tag",
            name=name,
            color="blue",
        )
        assert result.outcome == "created" and result.item is not None
        tags.append(result.item.option_id)
    snapshot = ProjectTodoViewRepo(pool).list_views(pid, user_id=owner)
    priorities = [str(item["priority_id"]) for item in snapshot.priorities]
    special_titles = {
        3: "matrix 003 literal % marker",
        4: "matrix 004 literal _ marker",
        5: "matrix 005 literal \\ marker",
        6: "matrix 006 CaseFold MiXeD",
        7: "matrix 007 ＦＵＬＬＷＩＤＴＨ",
        8: "matrix 008 Straße",
    }
    rows = []
    for index in range(220):
        title = (
            "\ufdfa" * 200
            if index == 0
            else "\ufdfa" * 199 + "a"
            if index == 1
            else "\ufdfa" * 199 + "z"
            if index == 2
            else special_titles.get(index, f"matrix {index:03d}")
        )
        start, due = [
            ("9999-01-01", "9999-01-05"),
            ("9999-01-02", None),
            (None, "9999-01-05"),
            (None, None),
        ][index % 4]
        rows.append(
            _todo(
                pool,
                pid,
                owner,
                title=title,
                description="synthetic matrix description",
                status=("todo", "in_progress", "done")[index % 3],
                assignee_user_id=(owner, member, None)[index % 3],
                priority_id=(*priorities, None)[index % 5],
                tag_ids=([], [tags[0]], [tags[1]], tags[:2])[index % 4],
                expected_catalog_revision=4,
                start_date=start,
                due_date=due,
                ts=1700000000 + index,
            )
        )
    archived = catalog.set_archived(
        project_id=pid,
        actor_user_id=owner,
        expected_revision=4,
        kind="priority",
        option_id=priorities[1],
        archived=True,
    )
    assert archived.outcome == "archived" and archived.revision == 5
    return owner, member, admin, pid, rows, priorities, tags


def _clone_native_fixture(source: Any, target: Any) -> None:
    """Copy only native-created synthetic rows, retaining both databases' IDs/keys."""
    tables = (
        "users",
        "project_spaces",
        "project_members",
        "project_todo_catalog_state",
        "project_todo_priorities",
        "project_todo_tags",
        "project_todo_views",
        "project_todo_view_state",
        "project_todos",
        "project_todo_tag_links",
        "project_events",
    )
    with source.connect() as reader, target.transaction() as writer:
        for table in tables:
            rows = reader.execute(f"SELECT * FROM {table}").fetchall()
            for row in rows:
                values = dict(row)
                for column, value in values.items():
                    if isinstance(value, (dict, list)):
                        encoded = json.dumps(value, ensure_ascii=False, allow_nan=False)
                        assert json.loads(encoded) == value, (table, column)
                        values[column] = encoded
                writer.execute(
                    f"INSERT INTO {table} ({','.join(values)}) VALUES ({','.join('?' for _ in values)})",
                    list(values.values()),
                )


def _ordered_ids(
    rows: list[Any], sorts: list[dict[str, str]], names: dict[int, str], priorities: list[str]
) -> list[str]:
    def key(row: Any, field: str) -> Any:
        if field == "title":
            return unicodedata.normalize("NFKC", row.title).casefold()
        if field == "status":
            return {"todo": 0, "in_progress": 1, "done": 2}[row.status]
        if field == "assignee":
            return (
                None
                if row.assignee_user_id is None
                else (
                    unicodedata.normalize("NFKC", names[row.assignee_user_id]).casefold(),
                    row.assignee_user_id,
                )
            )
        if field == "priority":
            if row.priority_id is None:
                return 2, 0, ""
            return (
                (1 if row.priority_id == priorities[1] else 0),
                priorities.index(row.priority_id),
                row.priority_id,
            )
        return getattr(row, field)

    def compare(first: Any, second: Any) -> int:
        for sort in sorts:
            left, right = key(first, sort["field"]), key(second, sort["field"])
            if sort["field"] == "priority":
                # Category and inner public ID remain ascending in both directions.
                difference = (left[0] > right[0]) - (left[0] < right[0])
                if not difference:
                    difference = (left[1] > right[1]) - (left[1] < right[1])
                    if sort["direction"] == "desc":
                        difference = -difference
                if not difference:
                    difference = (left[2] > right[2]) - (left[2] < right[2])
                if difference:
                    return difference
                continue
            if left is None or right is None:
                difference = (left is None) - (right is None)
            else:
                difference = (left > right) - (left < right)
                if sort["direction"] == "desc":
                    difference = -difference
            if difference:
                return difference
        return (first.todo_id > second.todo_id) - (first.todo_id < second.todo_id)

    return [row.todo_id for row in sorted(rows, key=cmp_to_key(compare))]


def _matrix_filter_cases(
    rows: list[Any], tags: list[str], today: str
) -> list[tuple[str, dict[str, Any], list[Any]]]:
    """Independent contract predicates over native rows, not the query compiler."""
    cases = []
    for values in (["todo"], ["in_progress"], ["done"], ["todo", "in_progress"]):
        for operation in ("in", "not_in"):
            cases.append(
                (
                    f"status-{operation}-{values}",
                    _definition(filters=[{"field": "status", "op": operation, "values": values}]),
                    [row for row in rows if (row.status in values) == (operation == "in")],
                )
            )
    for operation in ("in", "not_in"):
        cases.append(
            (
                "source-" + operation,
                _definition(filters=[{"field": "source", "op": operation, "values": ["manual"]}]),
                # Native C1-created todos have the contract's sole source, manual.
                [row for row in rows if operation == "in"],
            )
        )
    for (
        field,
        positive_on,
        negative_on,
        positive_before,
        negative_before,
        positive_after,
        negative_after,
        ranges,
    ) in (
        (
            "start_date",
            "9999-01-01",
            "9999-01-03",
            "9999-01-02",
            "9999-01-01",
            "9999-01-01",
            "9999-01-02",
            (("9999-01-01", "9999-01-02"), ("9999-01-03", "9999-01-04")),
        ),
        (
            "due_date",
            "9999-01-05",
            "9999-01-04",
            "9999-01-06",
            "9999-01-05",
            "9999-01-04",
            "9999-01-05",
            (("9999-01-05", "9999-01-06"), ("9999-01-06", "9999-01-07")),
        ),
    ):
        for operation, values in (
            ("on", (positive_on, negative_on)),
            ("before", (positive_before, negative_before)),
            ("after", (positive_after, negative_after)),
        ):
            for label, value in zip(("positive", "empty"), values, strict=True):
                expected = []
                for row in rows:
                    day = getattr(row, field)
                    if day is None:
                        continue
                    if (
                        (operation == "on" and day == value)
                        or (operation == "before" and day < value)
                        or (operation == "after" and day > value)
                    ):
                        expected.append(row)
                assert bool(expected) == (label == "positive")
                cases.append(
                    (
                        f"{field}-{operation}-{label}",
                        _definition(filters=[{"field": field, "op": operation, "value": value}]),
                        expected,
                    )
                )
        for label, values in zip(("positive", "empty"), ranges, strict=True):
            expected = [
                row
                for row in rows
                if getattr(row, field) is not None and values[0] <= getattr(row, field) <= values[1]
            ]
            assert bool(expected) == (label == "positive")
            cases.append(
                (
                    f"{field}-between-{label}",
                    _definition(
                        filters=[{"field": field, "op": "between", "values": list(values)}]
                    ),
                    expected,
                )
            )
        for operation in ("is_empty", "not_empty"):
            cases.append(
                (
                    f"{field}-{operation}",
                    _definition(filters=[{"field": field, "op": operation}]),
                    [
                        row
                        for row in rows
                        if (getattr(row, field) is None) == (operation == "is_empty")
                    ],
                )
            )
    for value in (True, False):
        expected = [
            row
            for row in rows
            if (row.due_date is not None and row.due_date < today and row.status != "done") == value
        ]
        assert 0 < len(expected) < len(rows)
        cases.append(
            (
                f"due_date-overdue-{value}",
                _definition(filters=[{"field": "due_date", "op": "overdue", "value": value}]),
                expected,
            )
        )
    for literal in ("%", "_", "\\", "ＣＡＳＥＦＯＬＤ MIXED", "fullwidth", "STRASSE"):
        needle = unicodedata.normalize("NFKC", literal).casefold()
        for operation in ("contains", "not_contains"):
            expected = [
                row
                for row in rows
                if (needle in unicodedata.normalize("NFKC", row.title).casefold())
                == (operation == "contains")
            ]
            assert 0 < len(expected) < len(rows)
            cases.append(
                (
                    f"title-literal-{operation}-{literal}",
                    _definition(filters=[{"field": "title", "op": operation, "value": literal}]),
                    expected,
                )
            )
    for operation in ("is_empty", "not_empty"):
        cases.append(
            (
                "tags-" + operation,
                _definition(filters=[{"field": "tags", "op": operation}]),
                [row for row in rows if (not row.tag_ids) == (operation == "is_empty")],
            )
        )
    conjunctions = (
        (
            "and-title-status-tags",
            [
                {"field": "title", "op": "contains", "value": "%"},
                {"field": "status", "op": "in", "values": ["todo"]},
                {"field": "tags", "op": "any", "values": tags[:1]},
            ],
            [
                row
                for row in rows
                if "%" in unicodedata.normalize("NFKC", row.title).casefold()
                and row.status == "todo"
                and tags[0] in row.tag_ids
            ],
        ),
        (
            "and-start-due-status",
            [
                {"field": "start_date", "op": "between", "values": ["9999-01-01", "9999-01-02"]},
                {"field": "due_date", "op": "before", "value": "9999-01-06"},
                {"field": "status", "op": "not_in", "values": ["done"]},
            ],
            [
                row
                for row in rows
                if row.start_date is not None
                and "9999-01-01" <= row.start_date <= "9999-01-02"
                and row.due_date is not None
                and row.due_date < "9999-01-06"
                and row.status != "done"
            ],
        ),
    )
    for name, filters, expected in conjunctions:
        assert 0 < len(expected) < len(rows)
        cases.append((name, _definition(filters=filters), expected))
    return cases


def _all_pages(
    service: Any, pid: str, actor: int, request: dict[str, Any]
) -> tuple[list[str], dict[str, Any]]:
    seen = []
    cursor = None
    for _ in range(8):
        body = service.query(
            pid, user_id=actor, data={**request, **({"cursor": cursor} if cursor else {})}
        )
        assert all(set(item) == _TODO_KEYS | {"parent_title"} for item in body["items"])
        assert len(body["items"]) <= request.get("limit", 50)
        seen.extend(item["todo_id"] for item in body["items"])
        cursor = body["next_cursor"]
        if cursor is None:
            assert len(seen) == len(set(seen))
            return seen, body
        assert len(cursor.encode()) <= 2048 and "=" not in cursor
        decoded = json.loads(base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4)))
        assert set(decoded) == {"v", "query_fingerprint", "last_todo_id", "last_version"}
        assert decoded["last_todo_id"] == body["items"][-1]["todo_id"]
    raise AssertionError("220-row matrix did not terminate within eight bounded pages")


def test_real_220_row_sqlite_pg_matrix_has_identical_ids_counts_groups_and_pages(
    postgres_pair: Any, tmp_path: Any, monkeypatch: Any
) -> None:
    env = postgres_pair
    owner, member, admin, pid, rows, priorities, tags = _seed_matrix(env)
    date_views = {}
    for kind in ("calendar", "gantt"):
        date_views[kind] = _native_view(env["first"], pid, owner, kind)
    sqlite = SqlitePool(tmp_path / "q4-cloned-native-matrix.sqlite3")
    report = []
    try:
        run_migrations(sqlite)
        _clone_native_fixture(env["first"], sqlite)
        services = [(_service(sqlite), "sqlite"), (_service(env["first"]), "postgresql")]
        base = _request(env["first"], pid, owner)
        with env["first"].connect() as conn:
            names = {
                int(row["id"]): str(row["display_name"] or row["username"])
                for row in conn.execute("SELECT id,username,display_name FROM users").fetchall()
            }
        matrix_today = "9999-01-06"
        clock_reader = _QueryObservedPool(env["first"])
        clock_probe, samples = {"active": False}, []

        def today(timezone: str) -> str:
            assert timezone == "Asia/Shanghai"
            if clock_probe["active"]:
                assert (
                    clock_reader.statements[0] == "SET TRANSACTION ISOLATION LEVEL REPEATABLE READ"
                )
                assert (
                    clock_reader.member_requests == clock_reader.member_completed == [(pid, owner)]
                )
                samples.append(
                    {
                        "timezone": timezone,
                        "day": matrix_today,
                        "completed_member_locks": list(clock_reader.member_completed),
                        "statements_before_today": list(clock_reader.statements),
                    }
                )
            return matrix_today

        monkeypatch.setattr(project_plan_locks, "server_today", today)
        clock_probe["active"] = True
        clock_result = _service(clock_reader).query(
            pid,
            user_id=owner,
            data={
                **base,
                "override_definition": _definition(
                    filters=[{"field": "due_date", "op": "overdue", "value": True}]
                ),
                "limit": 100,
            },
        )
        clock_probe["active"] = False
        assert len(samples) == 1
        assert clock_result["server_today"] == matrix_today
        assert clock_result["server_timezone"] == "Asia/Shanghai"
        _assert_rr(clock_reader)
        report.append(
            {
                "case": "controlled-today-after-pg-member-locks",
                "dialect": "postgresql",
                "reader_pid": clock_reader.pid,
                "sample_calls": samples,
                "first_sql": clock_reader.statements[0],
            }
        )
        cases = []
        for field, left in (("assignee", owner), ("priority", priorities[0])):
            for values in ([left], [left, None], [None]):
                for operation in ("in", "not_in"):
                    expected = []
                    for row in rows:
                        value = row.assignee_user_id if field == "assignee" else row.priority_id
                        if (value in values) == (operation == "in"):
                            expected.append(row)
                    cases.append(
                        (
                            f"{field}-{operation}-{values}",
                            _definition(
                                filters=[{"field": field, "op": operation, "values": values}]
                            ),
                            expected,
                        )
                    )
        for operation, wanted in (
            ("any", lambda row: bool(set(row.tag_ids) & set(tags[:2]))),
            ("all", lambda row: set(tags[:2]) <= set(row.tag_ids)),
            ("none_of", lambda row: tags[0] not in row.tag_ids),
        ):
            selected = tags[:2] if operation != "none_of" else tags[:1]
            cases.append(
                (
                    "tags-" + operation,
                    _definition(filters=[{"field": "tags", "op": operation, "values": selected}]),
                    [row for row in rows if wanted(row)],
                )
            )
        cases.extend(_matrix_filter_cases(rows, tags, matrix_today))
        for operation, expected in (("contains", []), ("not_contains", rows)):
            cases.append(
                (
                    "title-nul-" + operation,
                    _definition(filters=[{"field": "title", "op": operation, "value": "\u0000"}]),
                    expected,
                )
            )
        for sorts in (
            [{"field": "title", "direction": "asc"}],
            [
                {"field": "status", "direction": "desc"},
                {"field": "title", "direction": "asc"},
                {"field": "due_date", "direction": "desc"},
            ],
            [
                {"field": "assignee", "direction": "desc"},
                {"field": "start_date", "direction": "desc"},
                {"field": "title", "direction": "asc"},
            ],
            [{"field": "priority", "direction": "asc"}, {"field": "title", "direction": "asc"}],
            [
                {"field": "priority", "direction": "desc"},
                {"field": "assignee", "direction": "asc"},
                {"field": "title", "direction": "asc"},
            ],
        ):
            cases.append(("full-order-" + json.dumps(sorts), _definition(sort=sorts), rows))
        for name, definition, expected in cases:
            expected_ids = _ordered_ids(expected, definition["sort"], names, priorities)
            outputs = []
            for service, dialect in services:
                actual, body = _all_pages(
                    service, pid, owner, {**base, "override_definition": definition, "limit": 47}
                )
                assert body["server_today"] == matrix_today
                assert body["server_timezone"] == "Asia/Shanghai"
                assert actual == expected_ids, (name, dialect)
                assert body["total"] == body["matched_total"] == len(expected)
                assert body["unscheduled_total"] is None and body["groups"] == []
                outputs.append(
                    {
                        "dialect": dialect,
                        "ordered_ids": actual,
                        "total": body["total"],
                        "matched_total": body["matched_total"],
                        "unscheduled_total": body["unscheduled_total"],
                        "groups": body["groups"],
                    }
                )
            assert {key: value for key, value in outputs[0].items() if key != "dialect"} == {
                key: value for key, value in outputs[1].items() if key != "dialect"
            }
            report.append({"case": name, "expected_ids": expected_ids, "outputs": outputs})
        group_counts = {
            "status": {"todo": 74, "in_progress": 73, "done": 73},
            "assignee": {str(owner): 74, str(member): 73, str(admin): 0, None: 73},
            "priority": {
                priorities[0]: 44,
                priorities[1]: 44,
                priorities[2]: 44,
                priorities[3]: 44,
                None: 44,
            },
            "tag": {tags[0]: 110, tags[1]: 110, tags[2]: 0, None: 55},
            "source": {"manual": 220},
        }
        for kind, expected in group_counts.items():
            for service, dialect in services:
                body = service.query(
                    pid,
                    user_id=owner,
                    data={**base, "override_definition": _definition(group_by=kind), "limit": 1},
                )
                assert {item["key"]["id"]: item["count"] for item in body["groups"]} == expected
                assert body["total"] == body["matched_total"] == 220 and len(body["items"]) == 1
                report.append(
                    {
                        "case": "group-" + kind,
                        "dialect": dialect,
                        "groups": body["groups"],
                        "total": body["total"],
                    }
                )
        for kind, identifier, expected in (
            ("tag", tags[0], [row for row in rows if tags[0] in row.tag_ids]),
            ("tag", None, [row for row in rows if not row.tag_ids]),
            ("assignee", str(member), [row for row in rows if row.assignee_user_id == member]),
            ("priority", priorities[1], [row for row in rows if row.priority_id == priorities[1]]),
            ("status", "todo", [row for row in rows if row.status == "todo"]),
        ):
            definition = _definition(group_by=kind, sort=[{"field": "title", "direction": "asc"}])
            wanted = _ordered_ids(expected, definition["sort"], names, priorities)
            for service, dialect in services:
                actual, body = _all_pages(
                    service,
                    pid,
                    owner,
                    {
                        **base,
                        "override_definition": definition,
                        "group_key": {"kind": kind, "id": identifier},
                        "limit": 47,
                    },
                )
                assert actual == wanted
                assert body["total"] == body["matched_total"] == 220
                assert {
                    entry["key"]["id"]: entry["count"] for entry in body["groups"]
                } == group_counts[kind]
                report.append(
                    {
                        "case": f"expanded-{kind}-{identifier}",
                        "dialect": dialect,
                        "expected_ids": wanted,
                        "actual_ids": actual,
                        "groups": body["groups"],
                        "total": 220,
                    }
                )
        window = {"start_date": "9999-01-02", "end_date": "9999-01-05"}
        for kind, basis in (("gantt", None), ("calendar", "start_date"), ("calendar", "due_date")):
            view = date_views[kind]
            definition = _definition(kind, sort=[{"field": "title", "direction": "asc"}])
            if basis is not None:
                definition["calendar"]["date_basis"] = basis
            unscheduled, scheduled = [], []
            for row in rows:
                if kind == "calendar":
                    day = getattr(row, basis)
                    if day is None:
                        unscheduled.append(row)
                    elif window["start_date"] <= day <= window["end_date"]:
                        scheduled.append(row)
                elif row.start_date is None and row.due_date is None:
                    unscheduled.append(row)
                elif (row.start_date or row.due_date) <= window["end_date"] and (
                    row.due_date or row.start_date
                ) >= window["start_date"]:
                    scheduled.append(row)
            for bucket, expected in (("scheduled", scheduled), ("unscheduled", unscheduled)):
                wanted = _ordered_ids(expected, definition["sort"], names, priorities)
                for service, dialect in services:
                    actual, result = _all_pages(
                        service,
                        pid,
                        owner,
                        {
                            **base,
                            "view_id": view.view_id,
                            "expected_view_version": view.version,
                            "override_definition": definition,
                            "window": window,
                            "bucket": bucket,
                            "limit": 47,
                        },
                    )
                    assert actual == wanted and result["total"] == 220
                    assert result["matched_total"] == len(expected) and result[
                        "unscheduled_total"
                    ] == len(unscheduled)
                    assert result["groups"] == []
                    report.append(
                        {
                            "case": f"{kind}-{basis}-{bucket}",
                            "dialect": dialect,
                            "expected_ids": wanted,
                            "actual_ids": actual,
                            "total": 220,
                            "matched_total": len(expected),
                            "unscheduled_total": len(unscheduled),
                            "groups": [],
                        }
                    )
        env["report"]["query_cross_backend_matrix"] = {
            "native_rows": 220,
            "clone_preserves_native_ids_and_keys": True,
            "cases": report,
        }
    finally:
        sqlite.close()


@pytest.mark.parametrize("winner", ["read", "remove"])
def test_real_read_revoke_lock_interleaving_linearizes_or_rolls_back_acl_only(
    postgres_pair: Any, winner: str
) -> None:
    env = postgres_pair
    owner, member, _admin, pid = _setup(env)
    todo = _todo(env["first"], pid, owner, title="private query revoke row")
    body = _request(env["first"], pid, member)
    first = _QueryObservedPool(
        env["first"],
        hold_sql="FROM project_todo_view_state"
        if winner == "read"
        else "DELETE FROM project_members",
    )
    second = _QueryObservedPool(env["second"])

    def query(pool: Any) -> Any:
        return _capture(lambda: _service(pool).query(pid, user_id=member, data=body))

    def remove(pool: Any) -> Any:
        return ProjectRepo(pool).remove_member(project_id=pid, user_id=member, actor_user_id=owner)

    left, right = _race(
        env,
        first,
        second,
        lambda: query(first) if winner == "read" else remove(first),
        lambda: remove(second) if winner == "read" else query(second),
    )
    reader = first if winner == "read" else second
    _assert_rr(reader)
    if winner == "read":
        assert left["status"] == 200 and [item["todo_id"] for item in left["body"]["items"]] == [
            todo.todo_id
        ]
        assert (
            left["body"]["total"] == left["body"]["matched_total"] == 1
            and right.outcome == "removed"
        )
        assert not reader.serialization_errors
    else:
        assert left.outcome == "removed" and right["status"] == 404 and "body" not in right
        _assert_acl_only_after_rollback(reader)
    fresh = _capture(lambda: _service(env["second"]).query(pid, user_id=member, data=body))
    assert fresh["status"] == 404 and "body" not in fresh
    env["report"]["query_read_revoke"] = {
        "winner": winner,
        "reader_pid": reader.pid,
        "first_sql": reader.statements[0],
        "isolation": reader.isolation,
        "transactions": reader.transactions,
        "actual_serialization_errors": reader.serialization_errors,
        "private_response_returned": winner == "read",
        "fresh_status": 404,
    }


def test_real_revoke_committed_before_fresh_query_never_reads_private_view_or_todos(
    postgres_pair: Any,
) -> None:
    env = postgres_pair
    owner, member, _admin, pid = _setup(env)
    _todo(env["first"], pid, owner, title="private committed revoke")
    body = _request(env["first"], pid, member)
    removed = ProjectRepo(env["first"]).remove_member(
        project_id=pid, user_id=member, actor_user_id=owner
    )
    assert removed.outcome == "removed"
    reader = _QueryObservedPool(env["second"])
    result = _capture(lambda: _service(reader).query(pid, user_id=member, data=body))
    assert result["status"] == 404 and "body" not in result
    _assert_rr(reader)
    assert not any(
        "FROM project_todos" in sql
        or "FROM project_todo_catalog_state" in sql
        or "FROM project_todo_view_state" in sql
        for sql in reader.statements
    )
    assert reader.member_requests == [] and len(reader.transactions) == 1
    env["report"]["query_revoke_first"] = {
        "writer_committed_before_query": True,
        "reader_pid": reader.pid,
        "writer_pid": env["report"]["pool_0_pid"],
        "first_sql": reader.statements[0],
        "private_response_returned": False,
        "transactions": reader.transactions,
    }


def _commit_while_reader_paused(
    env: dict[str, Any],
    reader: _QueryObservedPool,
    writer: _QueryObservedPool,
    read: Any,
    write: Any,
    *,
    name: str,
) -> tuple[Any, Any]:
    """The real second transaction must finish before a no-lock checkpoint resumes."""
    values, errors = {}, []

    def run() -> None:
        try:
            values["read"] = read()
        except BaseException as error:
            errors.append(error)

    thread = threading.Thread(target=run, daemon=True)
    committed = False
    try:
        thread.start()
        assert reader.locked.wait(5), "reader never reached its exact SQL checkpoint"
        values["write"] = write()
        assert writer.transactions and writer.transactions[-1]["transaction_status_after"] == "IDLE"
        assert reader.pid > 0 and writer.pid > 0 and reader.pid != writer.pid
        assert thread.is_alive(), "reader returned before the independent commit"
        committed = True
    finally:
        reader.release.set()
        thread.join(10)
        env["report"].setdefault("independent_commits", []).append(
            {
                "name": name,
                "reader_pid": reader.pid,
                "writer_pid": writer.pid,
                "writer_committed_before_reader_resume": committed,
                "reader_alive_after_join": thread.is_alive(),
                "errors": [repr(error) for error in errors],
            }
        )
    assert committed and not thread.is_alive() and not errors, errors
    return values["read"], values["write"]


@pytest.mark.parametrize("change", ["revoke", "catalog"])
def test_real_snapshot_before_lock_change_gets_40001_and_only_fresh_acl_recheck(
    postgres_pair: Any, change: str
) -> None:
    env = postgres_pair
    owner, member, _admin, pid = _setup(env)
    _todo(env["first"], pid, owner, title="private early snapshot")
    body = _request(env["first"], pid, member)
    reader = _QueryObservedPool(env["first"], hold_sql="SELECT v.* FROM project_todo_views v")
    writer = _QueryObservedPool(env["second"])
    repo = ProjectPlanQueryRepo(reader)
    real_acl = repo.can_read_project
    acl_calls = []

    def acl_after_rollback(project_id: str, actor: int) -> bool:
        assert len(reader.transactions) == 1
        assert reader.transactions[0]["transaction_status_after"] == "IDLE"
        acl_calls.append([project_id, actor])
        return real_acl(project_id, actor)

    repo.can_read_project = acl_after_rollback

    def commit() -> Any:
        assert reader.member_requests == []  # Real bootstrap has no member row lock.
        if change == "revoke":
            result = ProjectRepo(writer).remove_member(
                project_id=pid, user_id=member, actor_user_id=owner
            )
            assert result.outcome == "removed"
        else:
            result = ProjectTodoCatalogRepo(writer).create_option(
                project_id=pid,
                actor_user_id=owner,
                expected_revision=1,
                kind="tag",
                name="committed early",
                color="blue",
            )
            assert result.outcome == "created" and result.revision == 2
        return result

    result, _ = _commit_while_reader_paused(
        env,
        reader,
        writer,
        lambda: _capture(lambda: _service(reader, repo).query(pid, user_id=member, data=body)),
        commit,
        name="prelock-" + change,
    )
    _assert_acl_only_after_rollback(reader)
    assert acl_calls == [[pid, member]]
    assert result["status"] == (404 if change == "revoke" else 409) and "body" not in result
    if change == "catalog":
        assert result["details"] == {"reason": "transaction_conflict"}
    env["report"]["query_prelock_conflict"] = {
        "change": change,
        "reader_pid": reader.pid,
        "writer_pid": writer.pid,
        "bootstrap_count": 1,
        "acl_only_calls": acl_calls,
        "transactions": reader.transactions,
        "actual_serialization_errors": reader.serialization_errors,
        "response": result,
        "business_retry_count": 0,
    }


def test_real_query_locks_incoming_member_below_actor_once_in_complete_sorted_set(
    postgres_pair: Any, monkeypatch: Any
) -> None:
    env = postgres_pair
    owner, member, admin, pid = _setup(env)
    assert owner < member < admin
    todo = _todo(env["first"], pid, owner, title="member filtered", assignee_user_id=member)
    body = _request(
        env["first"],
        pid,
        admin,
        override_definition=_definition(
            filters=[{"field": "assignee", "op": "in", "values": [member, None]}]
        ),
    )
    reader = _QueryObservedPool(env["first"], hold_sql="FROM project_todo_view_state")
    writer = _QueryObservedPool(env["second"])
    original = project_plan_locks.member_roles
    calls = []

    def observed(db: Any, conn: Any, project_id: str, user_ids: Any, *, write: bool) -> Any:
        if db is reader:
            calls.append({"user_ids": list(user_ids), "write": write})
        return original(db, conn, project_id, user_ids, write=write)

    monkeypatch.setattr(project_plan_locks, "member_roles", observed)
    result, removed = _race(
        env,
        reader,
        writer,
        lambda: _service(reader).query(pid, user_id=admin, data=body),
        lambda: ProjectRepo(writer).remove_member(
            project_id=pid, user_id=member, actor_user_id=owner
        ),
    )
    assert [item["todo_id"] for item in result["items"]] == [
        todo.todo_id
    ] and removed.outcome == "removed"
    assert calls == [{"user_ids": [member, admin], "write": False}]
    assert reader.member_requests == reader.member_completed == [(pid, member), (pid, admin)]
    _assert_rr(reader)
    env["report"]["query_complete_member_locks"] = {
        "actor_id": admin,
        "incoming_ids": [member],
        "complete_calls": calls,
        "actual_sql_member_requests": [list(item) for item in reader.member_requests],
        "actual_sql_completed": [list(item) for item in reader.member_completed],
        "reader_pid": reader.pid,
        "writer_pid": writer.pid,
        "first_sql": reader.statements[0],
    }


def test_real_member_name_commit_after_metadata_discovery_keeps_one_rr_snapshot_and_changes_fingerprint(
    postgres_pair: Any,
) -> None:
    env = postgres_pair
    owner, member, admin, pid = _setup(env)
    users = UserRepo(env["first"])
    prefix = "\ufdfa" * 200 + " same prefix " * 30
    users.set_display_name(owner, prefix + "z")
    users.set_display_name(member, prefix + "a")
    owner_row = _todo(env["first"], pid, owner, title="owner", assignee_user_id=owner)
    member_row = _todo(env["first"], pid, owner, title="member", assignee_user_id=member)
    null_row = _todo(env["first"], pid, owner, title="none")
    definition = _definition(group_by="assignee", sort=[{"field": "assignee", "direction": "asc"}])
    body = _request(env["first"], pid, owner, override_definition=definition, limit=1)
    reader = _QueryObservedPool(env["first"], hold_sql="u.project_plan_display_sort_key")
    writer = _QueryObservedPool(env["second"])
    first, _ = _commit_while_reader_paused(
        env,
        reader,
        writer,
        lambda: _service(reader).query(pid, user_id=owner, data=body),
        lambda: UserRepo(writer).set_display_name(owner, prefix + "0"),
        name="display-name-after-metadata",
    )
    _assert_rr(reader)
    assert [item["todo_id"] for item in first["items"]] == [member_row.todo_id]
    assert first["total"] == first["matched_total"] == 3
    assert {item["key"]["id"]: item["count"] for item in first["groups"]} == {
        str(owner): 1,
        str(member): 1,
        str(admin): 0,
        None: 1,
    }
    cursor = first["next_cursor"]
    assert cursor is not None and len(cursor.encode()) <= 2048
    decoded = json.loads(base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4)))
    assert set(decoded) == {"v", "query_fingerprint", "last_todo_id", "last_version"}
    assert "\ufdfa" not in json.dumps(decoded, ensure_ascii=False)
    fresh = _service(env["second"]).query(pid, user_id=owner, data={**body, "limit": 100})
    assert [item["todo_id"] for item in fresh["items"]] == [
        owner_row.todo_id,
        member_row.todo_id,
        null_row.todo_id,
    ]
    assert fresh["query_fingerprint"] != first["query_fingerprint"]
    failed = _capture(
        lambda: _service(env["second"]).query(pid, user_id=owner, data={**body, "cursor": cursor})
    )
    assert failed == {"status": 409, "details": {"reason": "query_changed"}}
    env["report"]["query_metadata_rr"] = {
        "first_sql": reader.statements[0],
        "isolation": reader.isolation,
        "reader_pid": reader.pid,
        "writer_pid": writer.pid,
        "before_first_id": member_row.todo_id,
        "fresh_order": [owner_row.todo_id, member_row.todo_id, null_row.todo_id],
        "old_fingerprint": first["query_fingerprint"],
        "fresh_fingerprint": fresh["query_fingerprint"],
        "groups": first["groups"],
        "cursor_invalidated_after_commit": True,
    }


def test_real_query_finishes_while_all_project_todo_rows_remain_locked_elsewhere(
    postgres_pair: Any,
) -> None:
    env = postgres_pair
    owner, _member, _admin, pid = _setup(env)
    rows = [_todo(env["first"], pid, owner, title=f"locked todo {index}") for index in range(3)]
    body = _request(env["first"], pid, owner)
    reader = _QueryObservedPool(env["second"])
    results, errors = {}, []

    def query() -> None:
        try:
            results["body"] = _service(reader).query(pid, user_id=owner, data=body)
        except BaseException as error:
            errors.append(error)

    thread = threading.Thread(target=query, daemon=True)
    finished_before_commit = False
    writer_pid, locked = 0, []
    try:
        with env["first"].transaction() as conn:
            conn.execute("SET LOCAL lock_timeout='4s'")
            conn.execute("SET LOCAL statement_timeout='8s'")
            locked = conn.execute(
                "SELECT todo_id FROM project_todos WHERE project_id=? ORDER BY todo_id FOR UPDATE",
                (pid,),
            ).fetchall()
            assert {str(row["todo_id"]) for row in locked} == {row.todo_id for row in rows}
            writer_pid = int(conn.execute("SELECT pg_backend_pid()").fetchone()[0])
            thread.start()
            assert reader.started.wait(3)
            thread.join(3)
            finished_before_commit = not thread.is_alive()
    finally:
        if thread.ident is not None:
            # The outer transaction has ended even on a failed checkpoint.
            thread.join(10)
        env["report"]["query_no_all_todo_locks"] = {
            "reader_pid": reader.pid,
            "writer_pid": writer_pid,
            "all_todo_rows_locked": len(locked),
            "reader_finished_before_writer_commit": finished_before_commit,
            "alive_after_join": thread.is_alive(),
            "errors": [repr(error) for error in errors],
        }
    assert not thread.is_alive() and not errors and finished_before_commit
    _assert_rr(reader)
    assert reader.pid != writer_pid and writer_pid > 0
    assert {item["todo_id"] for item in results["body"]["items"]} == {row.todo_id for row in rows}
    assert results["body"]["total"] == results["body"]["matched_total"] == 3
    assert not any(
        "FROM project_todos" in sql and ("FOR UPDATE" in sql or "FOR SHARE" in sql)
        for sql in reader.statements
    )


def test_real_query_project_locks_do_not_delay_other_project_native_write(
    postgres_pair: Any,
) -> None:
    env = postgres_pair
    owner, _member, _admin, pid = _setup(env)
    other = ProjectRepo(env["first"]).create_with_owner(creator_user_id=owner, name="project B")
    row = _todo(env["first"], pid, owner, title="first project only")
    body = _request(env["first"], pid, owner)
    reader = _QueryObservedPool(env["first"], hold_sql="FROM project_todo_view_state")
    writer = _QueryObservedPool(env["second"])
    result, second_row = _commit_while_reader_paused(
        env,
        reader,
        writer,
        lambda: _service(reader).query(pid, user_id=owner, data=body),
        lambda: _todo(writer, other.project_id, owner, title="second project committed"),
        name="other-project-native-todo-write",
    )
    assert [item["todo_id"] for item in result["items"]] == [row.todo_id]
    assert result["total"] == 1
    other_body = _request(env["second"], other.project_id, owner)
    fresh = _service(env["second"]).query(other.project_id, user_id=owner, data=other_body)
    assert [item["todo_id"] for item in fresh["items"]] == [second_row.todo_id] and fresh[
        "total"
    ] == 1
    _assert_rr(reader)
    env["report"]["query_project_independence"] = {
        "reader_pid": reader.pid,
        "writer_pid": writer.pid,
        "first_project_id": pid,
        "second_project_id": other.project_id,
        "writer_committed_before_reader_resume": True,
    }


def test_real_today_is_sampled_once_after_member_lock_wait_and_drives_overdue(
    postgres_pair: Any, monkeypatch: Any
) -> None:
    env = postgres_pair
    owner, _member, _admin, pid = _setup(env)
    todo = _todo(env["first"], pid, owner, title="legacy leap-day due")
    with env["first"].transaction() as conn:
        conn.execute(
            "UPDATE project_todos SET due_date='2024-02-29' WHERE todo_id=?", (todo.todo_id,)
        )
    body = _request(
        env["first"],
        pid,
        owner,
        override_definition=_definition(
            filters=[{"field": "due_date", "op": "overdue", "value": True}]
        ),
    )
    writer = _QueryObservedPool(env["first"], hold_sql="SELECT role FROM project_members")
    reader = _QueryObservedPool(env["second"])
    clock, calls = {"day": "2024-02-29"}, []

    def today(timezone: str) -> str:
        calls.append({"timezone": timezone, "day": clock["day"]})
        return clock["day"]

    monkeypatch.setattr(project_plan_locks, "server_today", today)

    def hold_member() -> str:
        with writer.transaction() as conn:
            assert (
                conn.execute(
                    "SELECT role FROM project_members WHERE project_id=? AND user_id=? FOR UPDATE",
                    (pid, owner),
                ).fetchone()[0]
                == "owner"
            )
        return "member_lock_committed"

    def while_waiting() -> None:
        assert calls == []
        clock["day"] = "2024-03-01"

    committed, result = _race(
        env,
        writer,
        reader,
        hold_member,
        lambda: _service(reader).query(pid, user_id=owner, data=body),
        on_wait=while_waiting,
    )
    assert committed == "member_lock_committed"
    assert calls == [{"timezone": "Asia/Shanghai", "day": "2024-03-01"}]
    assert result["server_today"] == "2024-03-01" and result["server_timezone"] == "Asia/Shanghai"
    assert [item["todo_id"] for item in result["items"]] == [todo.todo_id] and result["total"] == 1
    _assert_rr(reader)
    env["report"]["query_today_after_wait"] = {
        "reader_pid": reader.pid,
        "writer_pid": writer.pid,
        "clock_is_controlled": True,
        "sample_calls": calls,
        "no_sampling_before_lock_wait_ended": True,
        "first_sql": reader.statements[0],
        "overdue_ids": [todo.todo_id],
    }


@pytest.mark.parametrize("mutation", ["version", "delete", "filter", "group", "window"])
def test_real_committed_anchor_change_rechecks_version_deletion_and_complete_predicate(
    postgres_pair: Any, mutation: str
) -> None:
    env = postgres_pair
    owner, _member, _admin, pid = _setup(env)
    first = _todo(
        env["first"], pid, owner, title="anchor A", start_date="9999-01-01", due_date="9999-01-02"
    )
    second = _todo(
        env["first"], pid, owner, title="anchor B", start_date="9999-01-01", due_date="9999-01-02"
    )
    kind = "calendar" if mutation == "window" else "table"
    definition = _definition(
        kind,
        filters=[{"field": "title", "op": "contains", "value": "anchor"}],
        sort=[{"field": "title", "direction": "asc"}],
    )
    values: dict[str, Any] = {"override_definition": definition, "limit": 1}
    if mutation == "group":
        definition["group_by"] = "status"
        values["group_key"] = {"kind": "status", "id": "todo"}
    if mutation == "window":
        view = _native_view(env["first"], pid, owner, kind)
        values.update(
            view_id=view.view_id,
            expected_view_version=view.version,
            window={"start_date": "9999-01-01", "end_date": "9999-01-03"},
            bucket="scheduled",
        )
    body = _request(env["first"], pid, owner, **values)
    reader = _QueryObservedPool(env["first"])
    service = _service(reader)
    page = service.query(pid, user_id=owner, data=body)
    assert [item["todo_id"] for item in page["items"]] == [first.todo_id]
    assert page["total"] == page["matched_total"] == 2
    cursor = page["next_cursor"]
    assert cursor is not None and len(cursor.encode()) <= 2048
    decoded = json.loads(base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4)))
    assert set(decoded) == {"v", "query_fingerprint", "last_todo_id", "last_version"}
    assert decoded["last_todo_id"] == first.todo_id and decoded["last_version"] == first.version
    writer = _QueryObservedPool(env["second"])
    todos = ProjectTodoRepo(writer)
    if mutation == "delete":
        changed = todos.delete(
            project_id=pid,
            todo_id=first.todo_id,
            actor_user_id=owner,
            expected_version=first.version,
        )
        assert changed.outcome == "deleted"
    else:
        fields = {
            "version": {"description": "changed anchor description"},
            "filter": {"title": "outside query"},
            "group": {"status": "in_progress"},
            "window": {"due_date": "9999-02-01"},
        }[mutation]
        changed = todos.update(
            project_id=pid,
            todo_id=first.todo_id,
            actor_user_id=owner,
            expected_version=first.version,
            **fields,
        )
        assert changed.outcome == "updated" and changed.row is not None
        assert changed.row.version == first.version + 1
    assert type(writer.pid) is int and writer.pid > 0
    assert writer.transactions[-1]["transaction_status_after"] == "IDLE"
    stale = _capture(lambda: service.query(pid, user_id=owner, data={**body, "cursor": cursor}))
    assert stale == {"status": 409, "details": {"reason": "query_changed"}}
    predicate_rechecked = mutation in {"filter", "group", "window"}
    if predicate_rechecked:
        decoded["last_version"] = changed.row.version
        rewritten = (
            base64.urlsafe_b64encode(json.dumps(decoded, separators=(",", ":")).encode())
            .decode()
            .rstrip("=")
        )
        current_version = _capture(
            lambda: service.query(pid, user_id=owner, data={**body, "cursor": rewritten})
        )
        assert current_version == {"status": 409, "details": {"reason": "query_changed"}}
    fresh = service.query(pid, user_id=owner, data={**body, "limit": 100})
    expected = [first.todo_id, second.todo_id] if mutation == "version" else [second.todo_id]
    assert [item["todo_id"] for item in fresh["items"]] == expected
    assert fresh["query_fingerprint"] == page["query_fingerprint"]
    _assert_rr(reader)
    env["report"]["query_anchor_change"] = {
        "mutation": mutation,
        "reader_pid": reader.pid,
        "writer_pid": writer.pid,
        "writer_committed_before_next_query": True,
        "first_sql": reader.statements[0],
        "original_anchor_id": first.todo_id,
        "original_anchor_version": first.version,
        "actual_native_outcome": changed.outcome,
        "stale_cursor_outcome": stale,
        "current_version_full_predicate_rechecked": predicate_rechecked,
        "fresh_ids": expected,
    }


def test_tabletrue_parent_snapshot_hierarchy_cursor_and_constant_budget(postgres_pair: Any) -> None:
    from uuid import uuid4

    from octop.infra.projects.todos import ProjectTodoService

    env = postgres_pair
    owner, _member, _admin, pid = _setup(env)
    parent = _todo(env["first"], pid, owner, title="parent outside filter")
    svc = ProjectTodoService(
        SimpleNamespace(
            project_todo_repo=ProjectTodoRepo(env["first"]),
            config=SimpleNamespace(default_timezone="UTC"),
        )
    )

    def create_child(index: int) -> Any:
        row = ProjectTodoRepo(env["first"]).get(pid, parent.todo_id, user_id=owner)
        assert row is not None
        return svc.create_child(
            pid,
            parent.todo_id,
            actor_user_id=owner,
            expected_children_revision=row.children_revision,
            client_request_id=str(uuid4()),
            fields={"title": f"child {index}"},
        )["item"]

    create_child(1)
    definition = _definition(
        show_subtodos=True, filters=[{"field": "title", "op": "contains", "value": "child"}]
    )
    base = _request(env["first"], pid, owner, override_definition=definition)
    reader = _QueryObservedPool(env["first"])
    first = _service(reader).query(pid, user_id=owner, data=base)
    count_first = sum(sql.lstrip().upper().startswith("SELECT") for sql in reader.statements)
    assert first["total"] == 1 and first["items"][0]["parent_title"] == parent.title
    for index in range(2, 7):
        create_child(index)
    second_reader = _QueryObservedPool(env["first"])
    second = _service(second_reader).query(pid, user_id=owner, data=base)
    count_second = sum(
        sql.lstrip().upper().startswith("SELECT") for sql in second_reader.statements
    )
    assert second["total"] == 6 and len(second["items"]) == 6
    assert count_first == count_second
    _assert_rr(reader)
    _assert_rr(second_reader)
    first_true = _service(env["first"]).query(pid, user_id=owner, data={**base, "limit": 1})
    false_base = {
        **base,
        "override_definition": _definition(sort=[{"field": "title", "direction": "asc"}]),
    }
    _todo(env["first"], pid, owner, title="another root")
    first_false = _service(env["first"]).query(pid, user_id=owner, data={**false_base, "limit": 1})
    create_child(7)
    with pytest.raises(OctopError) as error:
        _service(env["first"]).query(
            pid, user_id=owner, data={**base, "cursor": first_true["next_cursor"]}
        )
    assert error.value.status == 409 and error.value.details == {"reason": "query_changed"}
    # Use an unmodified root anchor, so only the hierarchy fingerprint can change.
    with env["first"].transaction() as conn:
        conn.execute(
            "UPDATE project_plan_hierarchy_state SET revision=revision+1 WHERE project_id=?", (pid,)
        )
    false_after = _service(env["first"]).query(
        pid, user_id=owner, data={**false_base, "cursor": first_false["next_cursor"]}
    )
    assert false_after["query_fingerprint"] == first_false["query_fingerprint"]
    env["report"]["tabletrue_query_budget"] = {
        "first_selects": count_first,
        "second_selects": count_second,
        "parent_snapshot": True,
    }
