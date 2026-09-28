"""Internal full Unicode keys track every actual user/title writer."""

from __future__ import annotations

import json
import sqlite3
import unicodedata
from collections.abc import Iterator
from dataclasses import asdict
from pathlib import Path

import pytest

from octop.infra.backup.snapshot import capture_users_from_pool, upsert_users_into_pool
from octop.infra.db.migrate import run_migrations
from octop.infra.db.pool import SqlitePool
from octop.infra.db.repos._base import now_ts
from octop.infra.db.repos.invites import InviteRepo
from octop.infra.db.repos.project_todos import ProjectTodoRepo
from octop.infra.db.repos.projects import ProjectRepo
from octop.infra.db.repos.users import UserRepo


def _key(value: str) -> str:
    return unicodedata.normalize("NFKC", value).casefold()


@pytest.fixture
def pool(tmp_path: Path) -> Iterator[SqlitePool]:
    db = SqlitePool(tmp_path / "keys.db")
    run_migrations(db)
    try:
        yield db
    finally:
        db.close()


def _user_key(pool: SqlitePool, user_id: int) -> str:
    with pool.connect() as conn:
        row = conn.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()
    assert row is not None
    assert "project_plan_display_sort_key" in set(row.keys()), (
        "user writers must store the internal key"
    )
    return str(row["project_plan_display_sort_key"])


def _todo_key(pool: SqlitePool, todo_id: str) -> str:
    with pool.connect() as conn:
        row = conn.execute("SELECT * FROM project_todos WHERE todo_id=?", (todo_id,)).fetchone()
    assert row is not None
    assert "title_search_key" in set(row.keys()), "title writers must store the internal key"
    return str(row["title_search_key"])


@pytest.mark.parametrize(
    "username,display_name",
    [
        ("Ｓtraße", None),
        ("Ｓtraße", ""),
        ("fallback", " "),
        ("e\u0301", "É"),
        ("owner", "\ufdfa" * 2000 + "Ａ"),
        ("\ufdfa" * 200, None),
    ],
)
def test_user_create_persists_complete_display_or_username(
    pool: SqlitePool,
    username: str,
    display_name: str | None,
) -> None:
    repo = UserRepo(pool)
    user = repo.create(username=username, display_name=display_name, role="user")
    assert _user_key(pool, user) == _key(display_name if display_name else username)
    public = repo.get(user)
    assert public is not None
    assert public.username == username and public.display_name == display_name
    assert "project_plan_display_sort_key" not in asdict(public)


@pytest.mark.parametrize("display_name", [None, "", " ", "Ｓtraße", "\ufdfa" * 2000 + "Ｂ"])
def test_set_display_name_uses_final_value_in_same_update(
    pool: SqlitePool,
    display_name: str | None,
) -> None:
    repo = UserRepo(pool)
    user = repo.create(username="Ｆallback", display_name="old", role="user")
    repo.set_display_name(user, display_name)
    assert _user_key(pool, user) == _key(display_name if display_name else "Ｆallback")


@pytest.mark.parametrize("display_name", ["", "Ｓtraße", "\ufdfa" * 2000 + "C"])
def test_sso_profile_writer_maintains_the_final_display_key(
    pool: SqlitePool,
    display_name: str,
) -> None:
    repo = UserRepo(pool)
    user = repo.create(username="Ｆallback", display_name="old", role="user")
    repo.update_sso_profile(user, email="synthetic@example.invalid", display_name=display_name)
    assert _user_key(pool, user) == _key(display_name if display_name else "Ｆallback")
    repo.update_sso_profile(user, email="other@example.invalid")
    assert _user_key(pool, user) == _key(display_name if display_name else "Ｆallback")


@pytest.mark.parametrize("display_name", [None, "", "Ｓtraße", "\ufdfa" * 2000])
def test_invite_direct_user_insert_writes_key_and_consumes_invite_atomically(
    pool: SqlitePool,
    display_name: str | None,
) -> None:
    creator = UserRepo(pool).create(username="admin", role="admin")
    repo = InviteRepo(pool)
    invite = repo.create(code="Q2InviteABC", created_by=creator, expires_at=now_ts() + 3600)
    user, used = repo.redeem_creating_user(
        code=invite.code,
        username="Ｆallback",
        password_hash="synthetic",
        display_name=display_name,
        locale="en",
    )
    assert _user_key(pool, user) == _key(display_name if display_name else "Ｆallback")
    assert used.used_by_user_id == user and used.used_at is not None


def test_invite_after_user_insert_failure_rolls_back_key_and_invite(
    pool: SqlitePool,
) -> None:
    creator = UserRepo(pool).create(username="admin", role="admin")
    repo = InviteRepo(pool)
    invite = repo.create(code="Q2InviteBAD", created_by=creator, expires_at=now_ts() + 3600)
    assert _user_key(pool, creator) == "admin"
    with pool.connect() as conn:
        conn.execute(
            "CREATE TRIGGER fail_invite AFTER UPDATE ON user_invites "
            "BEGIN SELECT RAISE(ABORT,'after actual invite update'); END"
        )
        before = tuple(conn.iterdump())
    with pytest.raises(sqlite3.IntegrityError, match="after actual invite update"):
        repo.redeem_creating_user(
            code=invite.code,
            username="new",
            password_hash="synthetic",
            display_name="Ｓtraße",
            locale="en",
        )
    with pool.connect() as conn:
        assert tuple(conn.iterdump()) == before


@pytest.mark.parametrize(
    "insert", [False, True], ids=["final-username-update", "final-user-insert"]
)
def test_snapshot_upsert_derives_final_restored_name_without_changing_capture_tuple(
    pool: SqlitePool,
    insert: bool,
) -> None:
    repo = UserRepo(pool)
    user = repo.create(username="archive-name", display_name="archive display", role="admin")
    saved = capture_users_from_pool(pool)
    assert len(saved[0]) == 11  # existing public credential tuple
    values = list(saved[0])
    values[0] = user + 10 if insert else user
    values[1], values[4] = "Ｆinal-user", "\ufdfa" * 2000 + "Straße"
    upsert_users_into_pool(pool, [tuple(values)])
    assert _user_key(pool, int(values[0])) == _key(str(values[4]))
    values[4] = ""
    values[1] = "Ｓtraße"
    upsert_users_into_pool(pool, [tuple(values)])
    assert _user_key(pool, int(values[0])) == "strasse"
    assert all(len(row) == 11 for row in capture_users_from_pool(pool))


@pytest.mark.parametrize("writer", ["display", "sso", "snapshot"])
def test_name_key_failure_does_not_commit_a_half_name(
    pool: SqlitePool,
    writer: str,
) -> None:
    repo = UserRepo(pool)
    user = repo.create(username="fallback", display_name="before", role="admin")
    assert _user_key(pool, user) == "before"
    with pool.connect() as conn:
        conn.execute(
            "CREATE TRIGGER fail_name AFTER UPDATE ON users "
            "BEGIN SELECT RAISE(ABORT,'after actual user update'); END"
        )
        before = tuple(conn.iterdump())
    with pytest.raises(sqlite3.IntegrityError, match="after actual user update"):
        if writer == "display":
            repo.set_display_name(user, "Ｓtraße")
        elif writer == "sso":
            repo.update_sso_profile(user, display_name="Ｓtraße", email="new@example.invalid")
        else:
            row = list(capture_users_from_pool(pool)[0])
            row[1], row[4] = "new-username", "Ｓtraße"
            upsert_users_into_pool(pool, [tuple(row)])
    with pool.connect() as conn:
        assert tuple(conn.iterdump()) == before


def _todos(pool: SqlitePool) -> tuple[ProjectTodoRepo, int, str]:
    owner = UserRepo(pool).create(username="owner", role="admin")
    project = ProjectRepo(pool).create_with_owner(creator_user_id=owner, name="keys")
    return ProjectTodoRepo(pool), owner, project.project_id


@pytest.mark.parametrize("title", ["ＡＢＣ", "Straße", "e\u0301", "\ufdfa" * 200])
def test_todo_create_and_title_patch_store_complete_key_without_public_or_event_leak(
    pool: SqlitePool,
    title: str,
) -> None:
    repo, owner, project = _todos(pool)
    created = repo.create(project_id=project, creator_user_id=owner, title=title, ts=11)
    assert created.row is not None
    assert _todo_key(pool, created.row.todo_id) == _key(title)
    assert "title_search_key" not in asdict(created.row)
    changed_title = title[:-1] + "Ｚ"
    updated = repo.update(
        project_id=project,
        todo_id=created.row.todo_id,
        actor_user_id=owner,
        expected_version=1,
        title=changed_title,
        ts=12,
    )
    assert updated.outcome == "updated" and updated.row is not None
    assert updated.row.title == changed_title and updated.row.version == 2
    assert _todo_key(pool, created.row.todo_id) == _key(changed_title)
    with pool.connect() as conn:
        payload = json.loads(
            conn.execute(
                "SELECT payload_json FROM project_events WHERE event_type='project.todo_updated'"
            ).fetchone()[0]
        )
    assert payload["fields"] == ["title"]
    assert "title_search_key" not in json.dumps(payload)


def test_unrelated_noop_and_conflicting_todo_writes_do_not_rewrite_title_key(
    pool: SqlitePool,
) -> None:
    repo, owner, project = _todos(pool)
    created = repo.create(project_id=project, creator_user_id=owner, title="Straße")
    assert created.row is not None
    assert _todo_key(pool, created.row.todo_id) == "strasse"
    with pool.connect() as conn:
        conn.execute("UPDATE project_todos SET title_search_key='unchanged sentinel'")
    for version, patch, outcome in (
        (1, {"title": "Straße"}, "no_change"),
        (3, {"title": "new"}, "stale"),
        (1, {"status": "done"}, "updated"),
    ):
        result = repo.update(
            project_id=project,
            todo_id=created.row.todo_id,
            actor_user_id=owner,
            expected_version=version,
            **patch,
        )
        assert result.outcome == outcome
        assert _todo_key(pool, created.row.todo_id) == "unchanged sentinel"


def test_title_update_failure_rolls_back_title_key_version_and_event(pool: SqlitePool) -> None:
    repo, owner, project = _todos(pool)
    created = repo.create(project_id=project, creator_user_id=owner, title="before")
    assert created.row is not None and _todo_key(pool, created.row.todo_id) == "before"
    with pool.connect() as conn:
        conn.execute(
            "CREATE TRIGGER fail_title AFTER UPDATE ON project_todos "
            "BEGIN SELECT RAISE(ABORT,'after actual todo update'); END"
        )
        before = tuple(conn.iterdump())
    with pytest.raises(sqlite3.IntegrityError, match="after actual todo update"):
        repo.update(
            project_id=project,
            todo_id=created.row.todo_id,
            actor_user_id=owner,
            expected_version=1,
            title="Ｓtraße",
        )
    with pool.connect() as conn:
        assert tuple(conn.iterdump()) == before


@pytest.mark.parametrize(
    "q,expected",
    [
        ("strasse", ["Straße"]),
        ("abc", ["ＡＢＣ"]),
        ("é", ["e\u0301"]),
        ("%", ["literal%"]),
        ("_", ["literal_"]),
        ("\\", ["literal\\"]),
        ("   ", ["literal\\", "literal_", "literal%", "e\u0301", "ＡＢＣ", "Straße"]),
    ],
)
def test_compatible_get_keeps_literal_query_and_offset_contract(
    pool: SqlitePool,
    q: str,
    expected: list[str],
) -> None:
    repo, owner, project = _todos(pool)
    for stamp, title in enumerate(
        ["Straße", "ＡＢＣ", "e\u0301", "literal%", "literal_", "literal\\"], 1
    ):
        created = repo.create(project_id=project, creator_user_id=owner, title=title, ts=stamp)
        assert created.row is not None
    rows = repo.list_todos(project, user_id=owner, q=q, limit=20)
    assert rows is not None
    assert [r.title for r in rows] == expected
    offset = repo.list_todos(project, user_id=owner, q="", limit=1, offset=1)
    assert offset is not None and [r.title for r in offset] == ["literal_", "literal%"]
