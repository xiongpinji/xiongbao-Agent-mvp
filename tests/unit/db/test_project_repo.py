"""Unit tests for ProjectRepo, ProjectService, and migration 018."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from octop.infra.db.migrate import run_migrations
from octop.infra.db.pool import SqlitePool
from octop.infra.db.repos.projects import ProjectRepo
from octop.infra.db.repos.users import UserRepo
from octop.infra.errors import ErrorCode, OctopError
from octop.infra.projects.service import (
    MAX_PROJECT_DESCRIPTION_LENGTH,
    MAX_PROJECT_INSTRUCTIONS_LENGTH,
    MAX_PROJECT_NAME_LENGTH,
    ProjectService,
    validate_project_name,
)


@pytest.fixture
def db(tmp_path: Path) -> SqlitePool:
    pool = SqlitePool(tmp_path / "octop.db")
    run_migrations(pool)
    return pool


@pytest.fixture
def repo(db: SqlitePool) -> ProjectRepo:
    return ProjectRepo(db)


@pytest.fixture
def users(db: SqlitePool) -> UserRepo:
    return UserRepo(db)


@pytest.fixture
def owner_id(users: UserRepo) -> int:
    return users.create(username="owner", password_hash="h", role="user")


@pytest.fixture
def member_id(users: UserRepo) -> int:
    return users.create(username="member", password_hash="h", role="user")


@pytest.fixture
def admin_member_id(users: UserRepo) -> int:
    return users.create(username="adminmember", password_hash="h", role="user")


@pytest.fixture
def outsider_id(users: UserRepo) -> int:
    return users.create(username="outsider", password_hash="h", role="user")


class _StubServices:
    """Minimal stand-in for SharedServices exposing only ``project_repo``."""

    def __init__(self, repo: ProjectRepo) -> None:
        self.project_repo = repo


@pytest.fixture
def service(repo: ProjectRepo) -> ProjectService:
    return ProjectService(_StubServices(repo))


# ---------------------------------------------------------------------------
# Migration 018
# ---------------------------------------------------------------------------


def test_project_tables_migrated(db: SqlitePool) -> None:
    with db.connect() as conn:
        tables = {
            r["name"] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
        indexes = {
            r["name"] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='index'")
        }
        v = conn.execute("SELECT version FROM _schema_version").fetchone()[0]
        space_cols = {
            r["name"] for r in conn.execute("PRAGMA table_info(project_spaces)").fetchall()
        }
        member_cols = {
            r["name"] for r in conn.execute("PRAGMA table_info(project_members)").fetchall()
        }
        event_cols = {
            r["name"] for r in conn.execute("PRAGMA table_info(project_events)").fetchall()
        }
    assert v == 18
    assert {"project_spaces", "project_members", "project_events"}.issubset(tables)
    assert {
        "project_id",
        "creator_user_id",
        "name",
        "description",
        "instructions",
        "archived",
        "created_at",
        "updated_at",
    }.issubset(space_cols)
    assert {"project_id", "user_id", "role", "joined_at"}.issubset(member_cols)
    assert {
        "project_id",
        "actor_user_id",
        "event_type",
        "object_id",
        "payload_json",
        "created_at",
    }.issubset(event_cols)
    assert {
        "idx_project_spaces_updated",
        "idx_project_members_user",
        "idx_project_events_project",
    }.issubset(indexes)


def test_migration_upgrades_from_v17(tmp_path: Path) -> None:
    """A DB at watermark 17 must gain the project tables by re-running migrations."""
    pool = SqlitePool(tmp_path / "octop.db")
    run_migrations(pool)
    with pool.connect() as conn:
        conn.executescript(
            """
            DROP TABLE project_events;
            DROP TABLE project_members;
            DROP TABLE project_spaces;
            UPDATE _schema_version SET version = 17;
            """
        )
    run_migrations(pool)
    repo = ProjectRepo(pool)
    owner = UserRepo(pool).create(username="owner", password_hash="h", role="user")
    row = repo.create_with_owner(creator_user_id=owner, name="升级后项目")
    assert row.project_id
    with pool.connect() as conn:
        v = conn.execute("SELECT version FROM _schema_version").fetchone()[0]
    assert v == 18


def test_migration_018_is_idempotent(db: SqlitePool) -> None:
    """Retry after a partially-applied migration must not fail (IF NOT EXISTS)."""
    sql = (
        Path(__file__).resolve().parents[3] / "src/octop/infra/db/migrations/018_project_spaces.sql"
    ).read_text(encoding="utf-8")
    with db.connect() as conn:
        conn.executescript(sql)
        v = conn.execute("SELECT version FROM _schema_version").fetchone()[0]
    assert v == 18


# ---------------------------------------------------------------------------
# Atomic create: project + owner membership + creation event
# ---------------------------------------------------------------------------


def test_create_with_owner_writes_project_membership_and_event(
    repo: ProjectRepo, db: SqlitePool, owner_id: int
) -> None:
    row = repo.create_with_owner(
        creator_user_id=owner_id, name="熊宝项目", description="描述", instructions="说明"
    )
    assert row.project_id
    assert row.name == "熊宝项目"
    assert row.description == "描述"
    assert row.instructions == "说明"
    assert row.archived is False
    assert row.created_at == row.updated_at

    membership = repo.get_membership(row.project_id, owner_id)
    assert membership is not None
    assert membership.role == "owner"
    assert membership.joined_at == row.created_at
    assert repo.count_members(row.project_id) == 1

    with db.connect() as conn:
        events = conn.execute(
            "SELECT actor_user_id, event_type, object_id, payload_json "
            "FROM project_events WHERE project_id = ?",
            (row.project_id,),
        ).fetchall()
    assert len(events) == 1
    assert events[0]["event_type"] == "project.created"
    assert events[0]["actor_user_id"] == owner_id
    assert events[0]["object_id"] == row.project_id
    assert json.loads(events[0]["payload_json"]) == {}


def test_create_with_owner_rolls_back_when_event_insert_fails(
    repo: ProjectRepo, db: SqlitePool, owner_id: int
) -> None:
    """A failure on the third statement must leave no project and no membership."""
    with db.connect() as conn:
        conn.executescript("ALTER TABLE project_events RENAME TO project_events_backup;")
    try:
        with pytest.raises(sqlite3.OperationalError):
            repo.create_with_owner(creator_user_id=owner_id, name="熊宝项目")
    finally:
        with db.connect() as conn:
            conn.executescript("ALTER TABLE project_events_backup RENAME TO project_events;")
    with db.connect() as conn:
        spaces = conn.execute("SELECT COUNT(*) FROM project_spaces").fetchone()[0]
        members = conn.execute("SELECT COUNT(*) FROM project_members").fetchone()[0]
        events = conn.execute("SELECT COUNT(*) FROM project_events").fetchone()[0]
    assert spaces == 0
    assert members == 0
    assert events == 0


def test_create_with_owner_rejects_unknown_creator(repo: ProjectRepo, db: SqlitePool) -> None:
    with pytest.raises(sqlite3.IntegrityError):
        repo.create_with_owner(creator_user_id=999_999, name="Ghost")
    with db.connect() as conn:
        spaces = conn.execute("SELECT COUNT(*) FROM project_spaces").fetchone()[0]
        members = conn.execute("SELECT COUNT(*) FROM project_members").fetchone()[0]
    assert spaces == 0
    assert members == 0


def test_rows_persist_across_repo_instances(db: SqlitePool, owner_id: int) -> None:
    first = ProjectRepo(db)
    row = first.create_with_owner(creator_user_id=owner_id, name="Persist")
    second = ProjectRepo(db)
    assert second.get_project(row.project_id) == row
    membership = second.get_membership(row.project_id, owner_id)
    assert membership is not None
    assert membership.role == "owner"
    assert second.get_project("missing-id") is None


# ---------------------------------------------------------------------------
# Member seeding and listing
# ---------------------------------------------------------------------------


def test_add_member_and_list_members(
    repo: ProjectRepo, owner_id: int, member_id: int, outsider_id: int
) -> None:
    project = repo.create_with_owner(creator_user_id=owner_id, name="Team")
    repo.add_member(project.project_id, member_id, role="member")

    members = repo.list_members(project.project_id)
    assert [(m.username, m.role) for m in members] == [("owner", "owner"), ("member", "member")]
    assert repo.count_members(project.project_id) == 2
    assert repo.get_membership(project.project_id, outsider_id) is None


def test_add_member_rejects_duplicates(repo: ProjectRepo, owner_id: int, member_id: int) -> None:
    project = repo.create_with_owner(creator_user_id=owner_id, name="Dup")
    repo.add_member(project.project_id, member_id)
    with pytest.raises(sqlite3.IntegrityError):
        repo.add_member(project.project_id, member_id)


# ---------------------------------------------------------------------------
# User-filtered list: joins membership, ordering, search, pagination
# ---------------------------------------------------------------------------


def test_list_for_user_filters_by_membership(
    repo: ProjectRepo, owner_id: int, member_id: int, outsider_id: int
) -> None:
    owned = repo.create_with_owner(creator_user_id=owner_id, name="Alpha")
    theirs = repo.create_with_owner(creator_user_id=member_id, name="Beta")
    repo.add_member(owned.project_id, member_id)

    owner_rows = repo.list_for_user(owner_id)
    member_rows = repo.list_for_user(member_id)
    outsider_rows = repo.list_for_user(outsider_id)

    assert {r.project_id for r in owner_rows} == {owned.project_id}
    assert owner_rows[0].my_role == "owner"
    assert owner_rows[0].member_count == 2
    assert {r.project_id for r in member_rows} == {owned.project_id, theirs.project_id}
    roles = {r.project_id: r.my_role for r in member_rows}
    assert roles[owned.project_id] == "member"
    assert roles[theirs.project_id] == "owner"
    assert outsider_rows == []


def test_list_for_user_orders_by_updated_at_desc_then_id(
    repo: ProjectRepo, db: SqlitePool, owner_id: int
) -> None:
    older = repo.create_with_owner(creator_user_id=owner_id, name="Older")
    newer = repo.create_with_owner(creator_user_id=owner_id, name="Newer")
    # Bump deterministically: now_ts() has 1s granularity, so avoid same-second ties.
    with db.connect() as conn:
        conn.execute(
            "UPDATE project_spaces SET updated_at = updated_at + 10 WHERE project_id = ?",
            (older.project_id,),
        )
    ids = [r.project_id for r in repo.list_for_user(owner_id)]
    assert ids == [older.project_id, newer.project_id]

    # Equal updated_at falls back to a stable project_id DESC tie-break
    # (ULIDs are monotonic, so the later-created project sorts first).
    with db.connect() as conn:
        conn.execute("UPDATE project_spaces SET updated_at = 1000")
    ids = [r.project_id for r in repo.list_for_user(owner_id)]
    assert ids == sorted([older.project_id, newer.project_id], reverse=True)
    assert ids[0] == newer.project_id


def test_list_for_user_search_is_trimmed_and_case_insensitive(
    repo: ProjectRepo, owner_id: int
) -> None:
    repo.create_with_owner(creator_user_id=owner_id, name="Xiongbao Plan")
    repo.create_with_owner(creator_user_id=owner_id, name="Other")
    rows = repo.list_for_user(owner_id, q="  xIONGBAO  ")
    assert [r.name for r in rows] == ["Xiongbao Plan"]


def test_list_for_user_search_matches_cjk(repo: ProjectRepo, owner_id: int) -> None:
    repo.create_with_owner(creator_user_id=owner_id, name="熊宝项目")
    repo.create_with_owner(creator_user_id=owner_id, name="其他")
    rows = repo.list_for_user(owner_id, q="熊宝")
    assert [r.name for r in rows] == ["熊宝项目"]


def test_list_for_user_search_escapes_like_wildcards(repo: ProjectRepo, owner_id: int) -> None:
    repo.create_with_owner(creator_user_id=owner_id, name="a_c")
    repo.create_with_owner(creator_user_id=owner_id, name="abc")
    rows = repo.list_for_user(owner_id, q="a_c")
    assert [r.name for r in rows] == ["a_c"]
    pct = repo.list_for_user(owner_id, q="%")
    assert pct == []


def test_list_for_user_fetches_one_extra_row_for_has_more(repo: ProjectRepo, owner_id: int) -> None:
    for i in range(5):
        repo.create_with_owner(creator_user_id=owner_id, name=f"P{i}")
    assert len(repo.list_for_user(owner_id, limit=2, offset=0)) == 3
    assert len(repo.list_for_user(owner_id, limit=2, offset=4)) == 1
    assert len(repo.list_for_user(owner_id, limit=100, offset=0)) == 5


# ---------------------------------------------------------------------------
# Update: fields + event, never instructions content
# ---------------------------------------------------------------------------


def test_update_project_writes_fields_and_safe_event(
    repo: ProjectRepo, db: SqlitePool, owner_id: int
) -> None:
    project = repo.create_with_owner(
        creator_user_id=owner_id, name="Before", instructions="SECRET-INSTRUCTIONS"
    )
    updated = repo.update_project(
        project.project_id, actor_user_id=owner_id, name="After", instructions="NEW-SECRET"
    )
    assert updated is not None
    assert updated.name == "After"
    assert updated.instructions == "NEW-SECRET"
    assert updated.description == ""
    assert updated.updated_at >= project.updated_at

    with db.connect() as conn:
        events = conn.execute(
            "SELECT event_type, actor_user_id, object_id, payload_json "
            "FROM project_events WHERE project_id = ? ORDER BY id",
            (project.project_id,),
        ).fetchall()
    assert [e["event_type"] for e in events] == ["project.created", "project.updated"]
    assert events[1]["actor_user_id"] == owner_id
    assert events[1]["object_id"] == project.project_id
    assert json.loads(events[1]["payload_json"]) == {"fields": ["instructions", "name"]}
    dumped = "".join(str(e["payload_json"]) for e in events)
    assert "SECRET-INSTRUCTIONS" not in dumped
    assert "NEW-SECRET" not in dumped


def test_update_project_without_fields_is_a_noop(
    repo: ProjectRepo, db: SqlitePool, owner_id: int
) -> None:
    project = repo.create_with_owner(creator_user_id=owner_id, name="Same")
    assert repo.update_project(project.project_id, actor_user_id=owner_id) == project
    with db.connect() as conn:
        n = conn.execute(
            "SELECT COUNT(*) FROM project_events WHERE project_id = ?", (project.project_id,)
        ).fetchone()[0]
    assert n == 1


def test_update_project_missing_returns_none(repo: ProjectRepo, owner_id: int) -> None:
    assert repo.update_project("missing-id", actor_user_id=owner_id, name="X") is None
    assert repo.update_project("missing-id", actor_user_id=owner_id) is None


# ---------------------------------------------------------------------------
# Service layer: validation, ACL, pagination bounds
# ---------------------------------------------------------------------------


def test_validate_project_name_rules() -> None:
    assert validate_project_name("  熊宝项目  ") == "熊宝项目"
    assert validate_project_name("a" * MAX_PROJECT_NAME_LENGTH) == "a" * 15
    assert validate_project_name("熊" * MAX_PROJECT_NAME_LENGTH) == "熊" * 15
    for bad in ["", "   ", "a" * 16, "熊" * (MAX_PROJECT_NAME_LENGTH + 1)]:
        with pytest.raises(ValueError):
            validate_project_name(bad)


def test_service_create_sets_owner_role_and_single_member(
    service: ProjectService, owner_id: int
) -> None:
    view = service.create_project(creator_user_id=owner_id, name="  熊宝项目 ")
    assert view.name == "熊宝项目"
    assert view.my_role == "owner"
    assert view.member_count == 1
    assert view.description == ""
    assert view.instructions == ""
    assert view.created_at == view.updated_at


def test_service_create_rejects_overlong_texts_before_any_write(
    service: ProjectService, db: SqlitePool, owner_id: int
) -> None:
    with pytest.raises(ValueError):
        service.create_project(
            creator_user_id=owner_id,
            name="ok",
            description="x" * (MAX_PROJECT_DESCRIPTION_LENGTH + 1),
        )
    with pytest.raises(ValueError):
        service.create_project(
            creator_user_id=owner_id,
            name="ok",
            instructions="x" * (MAX_PROJECT_INSTRUCTIONS_LENGTH + 1),
        )
    with db.connect() as conn:
        n = conn.execute("SELECT COUNT(*) FROM project_spaces").fetchone()[0]
    assert n == 0


def test_service_get_view_acl(
    service: ProjectService,
    repo: ProjectRepo,
    owner_id: int,
    member_id: int,
    outsider_id: int,
) -> None:
    project = repo.create_with_owner(creator_user_id=owner_id, name="ACL")
    repo.add_member(project.project_id, member_id)

    owner_view = service.get_view(project.project_id, user_id=owner_id)
    assert owner_view.my_role == "owner"
    assert owner_view.member_count == 2
    member_view = service.get_view(project.project_id, user_id=member_id)
    assert member_view.my_role == "member"

    # Non-members get NOT_FOUND even for a known project id (no existence leak).
    with pytest.raises(OctopError) as exc:
        service.get_view(project.project_id, user_id=outsider_id)
    assert exc.value.code is ErrorCode.NOT_FOUND
    with pytest.raises(OctopError) as exc:
        service.get_view("missing-id", user_id=owner_id)
    assert exc.value.code is ErrorCode.NOT_FOUND


def test_service_update_roles(
    service: ProjectService,
    repo: ProjectRepo,
    owner_id: int,
    member_id: int,
    admin_member_id: int,
    outsider_id: int,
) -> None:
    project = repo.create_with_owner(creator_user_id=owner_id, name="Roles")
    repo.add_member(project.project_id, member_id, role="member")
    repo.add_member(project.project_id, admin_member_id, role="admin")

    with pytest.raises(OctopError) as exc:
        service.update_project(project.project_id, actor_user_id=member_id, name="Hacked")
    assert exc.value.code is ErrorCode.FORBIDDEN
    with pytest.raises(OctopError) as exc:
        service.update_project(project.project_id, actor_user_id=outsider_id, name="X")
    assert exc.value.code is ErrorCode.NOT_FOUND
    assert service.get_view(project.project_id, user_id=owner_id).name == "Roles"

    by_admin = service.update_project(
        project.project_id, actor_user_id=admin_member_id, name="By admin"
    )
    assert by_admin.name == "By admin"
    assert by_admin.my_role == "admin"
    by_owner = service.update_project(
        project.project_id, actor_user_id=owner_id, description="owner edit"
    )
    assert by_owner.description == "owner edit"
    assert by_owner.my_role == "owner"


def test_service_list_pagination(service: ProjectService, repo: ProjectRepo, owner_id: int) -> None:
    for i in range(5):
        repo.create_with_owner(creator_user_id=owner_id, name=f"P{i}")

    page = service.list_projects(user_id=owner_id, limit=2, offset=0)
    assert len(page.items) == 2
    assert page.has_more is True
    assert page.limit == 2
    assert page.offset == 0

    last = service.list_projects(user_id=owner_id, limit=2, offset=4)
    assert len(last.items) == 1
    assert last.has_more is False

    with pytest.raises(ValueError):
        service.list_projects(user_id=owner_id, limit=0)
    with pytest.raises(ValueError):
        service.list_projects(user_id=owner_id, limit=101)
    with pytest.raises(ValueError):
        service.list_projects(user_id=owner_id, offset=-1)


def test_service_list_members_not_found_for_outsider(
    service: ProjectService, repo: ProjectRepo, owner_id: int, outsider_id: int
) -> None:
    project = repo.create_with_owner(creator_user_id=owner_id, name="Members")
    members = service.list_members(project.project_id, user_id=owner_id)
    assert [(m.user_id, m.username, m.role) for m in members] == [(owner_id, "owner", "owner")]
    with pytest.raises(OctopError) as exc:
        service.list_members(project.project_id, user_id=outsider_id)
    assert exc.value.code is ErrorCode.NOT_FOUND
    with pytest.raises(OctopError) as exc:
        service.list_members("missing-id", user_id=owner_id)
    assert exc.value.code is ErrorCode.NOT_FOUND
