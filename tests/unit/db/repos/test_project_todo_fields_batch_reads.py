"""Real SQLite page reads keep tag associations bounded and in one snapshot."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from octop.infra.db.migrate import run_migrations
from octop.infra.db.pool import SqlitePool
from octop.infra.db.repos.project_todos import ProjectTodoRepo
from octop.infra.db.repos.projects import ProjectRepo
from octop.infra.db.repos.users import UserRepo
from octop.infra.utils.ulid import new_ulid


@pytest.fixture
def page_case(tmp_path: Path) -> Iterator[dict[str, Any]]:
    pool = SqlitePool(tmp_path / "batch-refs.db")
    try:
        run_migrations(pool)
        users = UserRepo(pool)
        owner = users.create(username="page-owner", password_hash="synthetic", role="user")
        outsider = users.create(username="page-outsider", password_hash="synthetic", role="user")
        projects = ProjectRepo(pool)
        pid = projects.create_with_owner(creator_user_id=owner, name="page").project_id
        foreign = projects.create_with_owner(creator_user_id=owner, name="foreign").project_id
        tag_ids = sorted(new_ulid() for _ in range(3))
        foreign_tag, foreign_todo = new_ulid(), new_ulid()
        todo_ids = [new_ulid() for _ in range(125)]
        expected: dict[str, list[str]] = {}
        with pool.transaction() as conn:
            priority = conn.execute(
                "SELECT priority_id FROM project_todo_priorities WHERE project_id=? "
                "ORDER BY position LIMIT 1",
                (pid,),
            ).fetchone()[0]
            conn.execute(
                "UPDATE project_todo_priorities SET archived_at=37 WHERE priority_id=?", (priority,)
            )
            conn.execute(
                "UPDATE project_todo_catalog_state SET revision=17 WHERE project_id=?", (pid,)
            )
            for index, tag_id in enumerate([*tag_ids, foreign_tag]):
                conn.execute(
                    "INSERT INTO project_todo_tags(tag_id,project_id,name,name_key,color,"
                    "archived_at,created_at,updated_at) VALUES (?,?,?,?,'blue',?,1,1)",
                    (
                        tag_id,
                        foreign if index == 3 else pid,
                        f"tag{index}",
                        f"tag{index}",
                        37 if index == 1 else None,
                    ),
                )
            for index, todo_id in enumerate(todo_ids):
                conn.execute(
                    "INSERT INTO project_todos(todo_id,project_id,creator_user_id,title,status,"
                    "version,created_at,updated_at,start_date,due_date,priority_id) "
                    "VALUES (?,?,?,?,'todo',3,1,?,'2000-02-29','9999-12-31',?)",
                    (todo_id, pid, owner, f"row {index}", index + 1, priority),
                )
                refs = ([], [tag_ids[0]], list(reversed(tag_ids)))[index % 3]
                expected[todo_id] = sorted(refs)
                for tag_id in refs:
                    conn.execute(
                        "INSERT INTO project_todo_tag_links(project_id,todo_id,tag_id) VALUES (?,?,?)",
                        (pid, todo_id, tag_id),
                    )
            conn.execute(
                "INSERT INTO project_todos(todo_id,project_id,creator_user_id,title,"
                "created_at,updated_at) VALUES (?,?,?,'foreign',1,99999)",
                (foreign_todo, foreign, owner),
            )
            conn.execute(
                "INSERT INTO project_todo_tag_links(project_id,todo_id,tag_id) VALUES (?,?,?)",
                (foreign, foreign_todo, foreign_tag),
            )
            conn.execute(
                "INSERT INTO project_todo_display_state(project_id,todo_id,revision,updated_at) "
                "SELECT project_id,todo_id,1,updated_at FROM project_todos"
            )
            conn.execute(
                "INSERT INTO project_todo_attachment_state(project_id,todo_id,revision,updated_at) "
                "SELECT project_id,todo_id,1,updated_at FROM project_todos"
            )
        yield {
            "pool": pool,
            "repo": ProjectTodoRepo(pool),
            "owner": owner,
            "outsider": outsider,
            "pid": pid,
            "foreign": foreign,
            "foreign_tag": foreign_tag,
            "foreign_todo": foreign_todo,
            "todo_ids": todo_ids,
            "expected": expected,
            "priority": priority,
            "archived_tag": tag_ids[1],
        }
    finally:
        pool.close()


def _trace_page(case: dict[str, Any], **kwargs: Any) -> tuple[Any, list[tuple[str, bool]]]:
    calls: list[tuple[str, bool]] = []
    with case["pool"].connect() as conn:
        conn.set_trace_callback(lambda sql: calls.append((sql, conn.in_transaction)))
        try:
            rows = case["repo"].list_todos(
                kwargs.pop("project_id", case["pid"]),
                user_id=kwargs.pop("user_id", case["owner"]),
                **kwargs,
            )
        finally:
            conn.set_trace_callback(None)
    return rows, calls


def _links(calls: list[tuple[str, bool]]) -> list[tuple[str, bool]]:
    return [
        (sql, active)
        for sql, active in calls
        if sql.lstrip().upper().startswith("SELECT") and "project_todo_tag_links" in sql
    ]


def test_page_tag_query_count_is_constant_for_20_and_100_rows(page_case: dict[str, Any]) -> None:
    traced = [_trace_page(page_case, limit=limit) for limit in (20, 100)]
    assert [len(rows) for rows, _ in traced] == [21, 101]
    link_counts = [len(_links(calls)) for _, calls in traced]
    select_counts = [
        sum(sql.lstrip().upper().startswith("SELECT") for sql, _ in calls) for _, calls in traced
    ]
    assert link_counts == [1, 1], (link_counts, select_counts)
    assert select_counts[0] == select_counts[1] and max(select_counts) <= 5
    for _, calls in traced:
        assert all(active for _, active in _links(calls))
        assert sum(sql == "BEGIN IMMEDIATE" for sql, _ in calls) == 1
        assert sum(sql == "COMMIT" for sql, _ in calls) == 1


@pytest.mark.parametrize("limit,offset", [(20, 0), (100, 5)])
def test_page_tags_keep_sorted_archived_empty_refs_and_limit_plus_one(
    page_case: dict[str, Any], limit: int, offset: int
) -> None:
    rows, calls = _trace_page(page_case, limit=limit, offset=offset)
    expected_ids = list(reversed(page_case["todo_ids"]))[offset : offset + limit + 1]
    assert [row.todo_id for row in rows] == expected_ids
    assert any(not row.tag_ids for row in rows)
    assert any(page_case["archived_tag"] in row.tag_ids for row in rows)
    for row in rows:
        assert row.tag_ids == page_case["expected"][row.todo_id]
        assert (
            row.start_date,
            row.due_date,
            row.priority_id,
            row.catalog_revision,
            row.version,
        ) == ("2000-02-29", "9999-12-31", page_case["priority"], 17, 3)
    assert len(_links(calls)) == 1 and all(active for _, active in _links(calls))


def test_empty_page_skips_tag_query(page_case: dict[str, Any]) -> None:
    rows, calls = _trace_page(page_case, limit=100, offset=999)
    assert rows == [] and _links(calls) == []
    assert sum(sql.lstrip().upper().startswith("SELECT") for sql, _ in calls) <= 4


def test_page_rejects_missing_display_state_without_hiding_the_todo(
    page_case: dict[str, Any],
) -> None:
    selected = page_case["todo_ids"][-1]
    with page_case["pool"].transaction() as conn:
        conn.execute(
            "DELETE FROM project_todo_display_state WHERE project_id=? AND todo_id=?",
            (page_case["pid"], selected),
        )
    with pytest.raises(RuntimeError):
        _trace_page(page_case, limit=20)


def test_page_refs_are_project_scoped_and_outsider_cannot_read(page_case: dict[str, Any]) -> None:
    rows, calls = _trace_page(page_case, limit=100)
    assert all(row.project_id == page_case["pid"] for row in rows)
    assert all(page_case["foreign_tag"] not in row.tag_ids for row in rows)
    foreign, _ = _trace_page(page_case, project_id=page_case["foreign"], limit=100)
    assert [(row.todo_id, row.tag_ids) for row in foreign] == [
        (page_case["foreign_todo"], [page_case["foreign_tag"]])
    ]
    denied, denied_calls = _trace_page(page_case, user_id=page_case["outsider"], limit=100)
    assert denied is None and _links(denied_calls) == []
    assert len(_links(calls)) == 1
