"""Unit tests for ProjectRepo, ProjectService, and migrations 018/019."""

from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
from pathlib import Path

import pytest

from octop.infra.db.migrate import run_migrations
from octop.infra.db.pool import SqlitePool
from octop.infra.db.repos._base import now_ts
from octop.infra.db.repos.projects import ProjectInviteRow, ProjectRepo
from octop.infra.db.repos.users import UserRepo
from octop.infra.errors import ErrorCode, OctopError
from octop.infra.projects.service import (
    MAX_PROJECT_DESCRIPTION_LENGTH,
    MAX_PROJECT_INSTRUCTIONS_LENGTH,
    MAX_PROJECT_NAME_LENGTH,
    ProjectService,
    hash_invite_token,
    validate_project_name,
)
from octop.infra.utils.ulid import new_ulid


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


@pytest.fixture
def alice_id(users: UserRepo) -> int:
    return users.create(username="alice", password_hash="h", role="user")


@pytest.fixture
def bob_id(users: UserRepo) -> int:
    return users.create(username="bob", password_hash="h", role="user")


class _StubServices:
    """Minimal stand-in for SharedServices exposing only ``project_repo``."""

    def __init__(self, repo: ProjectRepo) -> None:
        self.project_repo = repo


@pytest.fixture
def service(repo: ProjectRepo) -> ProjectService:
    return ProjectService(_StubServices(repo))


def _make_invite(
    repo: ProjectRepo,
    project_id: str,
    *,
    created_by: int,
    token: str,
    requires_approval: bool = False,
    expires_at: int | None = None,
) -> ProjectInviteRow:
    return repo.create_invite(
        invite_id=new_ulid(),
        project_id=project_id,
        token_hash=hash_invite_token(token),
        created_by=created_by,
        role="member",
        requires_approval=requires_approval,
        expires_at=(now_ts() + 7 * 86400) if expires_at is None else expires_at,
    )


def _events(db: SqlitePool, project_id: str) -> list[sqlite3.Row]:
    with db.connect() as conn:
        return conn.execute(
            "SELECT actor_user_id, event_type, object_id, payload_json "
            "FROM project_events WHERE project_id = ? ORDER BY id",
            (project_id,),
        ).fetchall()


# ---------------------------------------------------------------------------
# Migrations 018 + 019
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
        invite_cols = {
            r["name"] for r in conn.execute("PRAGMA table_info(project_invites)").fetchall()
        }
        request_cols = {
            r["name"] for r in conn.execute("PRAGMA table_info(project_join_requests)").fetchall()
        }
    assert v == 22
    assert {
        "project_spaces",
        "project_members",
        "project_events",
        "project_invites",
        "project_join_requests",
    }.issubset(tables)
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
        "invite_id",
        "project_id",
        "token_hash",
        "created_by",
        "role",
        "requires_approval",
        "created_at",
        "expires_at",
        "revoked_at",
        "consumed_at",
        "consumed_by_user_id",
    }.issubset(invite_cols)
    assert {
        "request_id",
        "project_id",
        "invite_id",
        "user_id",
        "status",
        "requested_at",
        "resolved_at",
        "resolved_by",
    }.issubset(request_cols)
    assert {
        "idx_project_spaces_updated",
        "idx_project_members_user",
        "idx_project_events_project",
        "idx_project_invites_project",
        "idx_project_join_requests_project",
        "idx_project_join_requests_pending",
    }.issubset(indexes)


def test_migration_upgrades_from_v17(tmp_path: Path) -> None:
    """A DB at watermark 17 must gain the project tables by re-running migrations."""
    pool = SqlitePool(tmp_path / "octop.db")
    run_migrations(pool)
    with pool.connect() as conn:
        conn.executescript(
            """
            DROP TABLE project_join_requests;
            DROP TABLE project_invites;
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
    assert v == 22


def test_migration_upgrades_from_v18(tmp_path: Path) -> None:
    """A DB at watermark 18 must gain the invite/request tables by re-running."""
    pool = SqlitePool(tmp_path / "octop.db")
    run_migrations(pool)
    with pool.connect() as conn:
        conn.executescript(
            """
            DROP TABLE project_join_requests;
            DROP TABLE project_invites;
            UPDATE _schema_version SET version = 18;
            """
        )
    run_migrations(pool)
    repo = ProjectRepo(pool)
    owner = UserRepo(pool).create(username="owner", password_hash="h", role="user")
    project = repo.create_with_owner(creator_user_id=owner, name="升级邀请")
    invite = _make_invite(repo, project.project_id, created_by=owner, token="tok-upgrade")
    assert invite.invite_id
    with pool.connect() as conn:
        v = conn.execute("SELECT version FROM _schema_version").fetchone()[0]
        tables = {
            r["name"] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
    assert v == 22
    assert {"project_invites", "project_join_requests"}.issubset(tables)


def test_migration_018_is_idempotent(db: SqlitePool) -> None:
    """Retry after a partially-applied migration must not fail (IF NOT EXISTS)."""
    sql = (
        Path(__file__).resolve().parents[3] / "src/octop/infra/db/migrations/018_project_spaces.sql"
    ).read_text(encoding="utf-8")
    with db.connect() as conn:
        conn.executescript(sql)
        v = conn.execute("SELECT version FROM _schema_version").fetchone()[0]
    assert v == 18


def test_migration_019_is_idempotent(db: SqlitePool) -> None:
    """Retry after a partially-applied migration must not fail (IF NOT EXISTS)."""
    sql = (
        Path(__file__).resolve().parents[3]
        / "src/octop/infra/db/migrations/019_project_membership.sql"
    ).read_text(encoding="utf-8")
    with db.connect() as conn:
        conn.executescript(sql)
        v = conn.execute("SELECT version FROM _schema_version").fetchone()[0]
    assert v == 19


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


# ---------------------------------------------------------------------------
# Repo: invite creation, listing, revocation
# ---------------------------------------------------------------------------


def test_create_invite_stores_only_hash_and_appends_event(
    repo: ProjectRepo, db: SqlitePool, owner_id: int
) -> None:
    project = repo.create_with_owner(creator_user_id=owner_id, name="Invited")
    invite = _make_invite(repo, project.project_id, created_by=owner_id, token="plain-token")

    assert invite.invite_id
    assert invite.project_id == project.project_id
    assert invite.token_hash == hash_invite_token("plain-token")
    assert invite.token_hash != "plain-token"
    assert invite.role == "member"
    assert invite.requires_approval is False
    assert invite.revoked_at is None
    assert invite.consumed_at is None
    assert invite.consumed_by_user_id is None
    assert invite.status() == "pending"
    assert invite.expires_at > invite.created_at

    events = _events(db, project.project_id)
    assert [e["event_type"] for e in events] == ["project.created", "project.invite_created"]
    assert events[1]["actor_user_id"] == owner_id
    assert events[1]["object_id"] == invite.invite_id
    payload = json.loads(events[1]["payload_json"])
    assert payload == {"role": "member", "requires_approval": False}
    dumped = "".join(str(e["payload_json"]) for e in events)
    assert "plain-token" not in dumped
    assert invite.token_hash not in dumped

    fetched = repo.get_invite(project.project_id, invite.invite_id)
    assert fetched == invite
    assert repo.get_invite(project.project_id, "missing") is None
    assert repo.get_invite("other-project", invite.invite_id) is None
    assert repo.list_invites(project.project_id) == [invite]


def test_revoke_invite_is_guarded_and_evented(
    repo: ProjectRepo, db: SqlitePool, owner_id: int, member_id: int
) -> None:
    project = repo.create_with_owner(creator_user_id=owner_id, name="Revoke")
    invite = _make_invite(repo, project.project_id, created_by=owner_id, token="tok-revoke")

    assert repo.revoke_invite(project.project_id, invite.invite_id, actor_user_id=owner_id) == (
        "revoked"
    )
    revoked = repo.get_invite(project.project_id, invite.invite_id)
    assert revoked is not None
    assert revoked.revoked_at is not None
    assert revoked.status() == "revoked"
    events = _events(db, project.project_id)
    assert events[-1]["event_type"] == "project.invite_revoked"
    assert events[-1]["actor_user_id"] == owner_id
    assert events[-1]["object_id"] == invite.invite_id

    # Repeat revocation is a deterministic conflict without a second event.
    assert repo.revoke_invite(project.project_id, invite.invite_id, actor_user_id=owner_id) == (
        "conflict:revoked"
    )
    assert len(_events(db, project.project_id)) == len(events)

    assert repo.revoke_invite(project.project_id, "missing", actor_user_id=owner_id) == "missing"

    # Consumed invites cannot be revoked.
    other = _make_invite(repo, project.project_id, created_by=owner_id, token="tok-consumed")
    result = repo.redeem_invite(token_hash=hash_invite_token("tok-consumed"), user_id=member_id)
    assert result.outcome == "joined"
    assert repo.revoke_invite(project.project_id, other.invite_id, actor_user_id=owner_id) == (
        "conflict:used"
    )


# ---------------------------------------------------------------------------
# Repo: one-use redemption (immediate join and approval request)
# ---------------------------------------------------------------------------


def test_redeem_immediate_invite_joins_atomically(
    repo: ProjectRepo, db: SqlitePool, owner_id: int, alice_id: int
) -> None:
    project = repo.create_with_owner(creator_user_id=owner_id, name="Join")
    invite = _make_invite(repo, project.project_id, created_by=owner_id, token="tok-join")

    result = repo.redeem_invite(token_hash=hash_invite_token("tok-join"), user_id=alice_id)
    assert result.outcome == "joined"
    assert result.project_id == project.project_id
    assert result.role == "member"
    assert result.invite_id == invite.invite_id

    membership = repo.get_membership(project.project_id, alice_id)
    assert membership is not None
    assert membership.role == "member"
    consumed = repo.get_invite(project.project_id, invite.invite_id)
    assert consumed is not None
    assert consumed.consumed_at is not None
    assert consumed.consumed_by_user_id == alice_id
    assert consumed.status() == "used"

    events = _events(db, project.project_id)
    assert [e["event_type"] for e in events] == [
        "project.created",
        "project.invite_created",
        "project.member_joined",
    ]
    assert events[2]["actor_user_id"] == alice_id
    assert events[2]["object_id"] == str(alice_id)
    assert json.loads(events[2]["payload_json"]) == {
        "user_id": alice_id,
        "role": "member",
        "invite_id": invite.invite_id,
    }


def test_redeem_is_one_use(
    repo: ProjectRepo, db: SqlitePool, owner_id: int, alice_id: int, bob_id: int
) -> None:
    project = repo.create_with_owner(creator_user_id=owner_id, name="Once")
    _make_invite(repo, project.project_id, created_by=owner_id, token="tok-once")
    token_hash = hash_invite_token("tok-once")

    first = repo.redeem_invite(token_hash=token_hash, user_id=alice_id)
    second = repo.redeem_invite(token_hash=token_hash, user_id=bob_id)
    assert first.outcome == "joined"
    assert second.outcome == "used"
    assert repo.get_membership(project.project_id, bob_id) is None
    assert repo.count_members(project.project_id) == 2
    joined = [
        e for e in _events(db, project.project_id) if e["event_type"] == "project.member_joined"
    ]
    assert len(joined) == 1


def test_redeem_concurrent_threads_single_winner(
    repo: ProjectRepo, db: SqlitePool, owner_id: int, alice_id: int, bob_id: int
) -> None:
    """Racing two acceptors on one token must yield exactly one membership."""
    project = repo.create_with_owner(creator_user_id=owner_id, name="Race")
    _make_invite(repo, project.project_id, created_by=owner_id, token="tok-race")
    token_hash = hash_invite_token("tok-race")

    barrier = threading.Barrier(2)
    outcomes: list[str] = []
    lock = threading.Lock()

    def _accept(user_id: int) -> None:
        barrier.wait(timeout=10)
        result = repo.redeem_invite(token_hash=token_hash, user_id=user_id)
        with lock:
            outcomes.append(result.outcome)

    threads = [threading.Thread(target=_accept, args=(uid,)) for uid in (alice_id, bob_id)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=10)

    assert sorted(outcomes) == ["joined", "used"]
    assert repo.count_members(project.project_id) == 2
    joined = [
        e for e in _events(db, project.project_id) if e["event_type"] == "project.member_joined"
    ]
    assert len(joined) == 1
    with db.connect() as conn:
        consumed = conn.execute(
            "SELECT COUNT(*) FROM project_invites WHERE consumed_at IS NOT NULL"
        ).fetchone()[0]
    assert consumed == 1


def test_redeem_approval_invite_creates_pending_request_only(
    repo: ProjectRepo, db: SqlitePool, owner_id: int, alice_id: int
) -> None:
    project = repo.create_with_owner(creator_user_id=owner_id, name="Approval")
    invite = _make_invite(
        repo, project.project_id, created_by=owner_id, token="tok-approval", requires_approval=True
    )

    result = repo.redeem_invite(token_hash=hash_invite_token("tok-approval"), user_id=alice_id)
    assert result.outcome == "requested"
    assert result.request_id
    assert result.project_id == project.project_id

    # No membership before approval.
    assert repo.get_membership(project.project_id, alice_id) is None
    assert repo.count_members(project.project_id) == 1

    requests = repo.list_join_requests(project.project_id)
    assert len(requests) == 1
    row = requests[0]
    assert row.request_id == result.request_id
    assert row.user_id == alice_id
    assert row.username == "alice"
    assert row.status == "pending"
    assert row.invite_id == invite.invite_id
    assert row.invite_role == "member"
    assert row.resolved_at is None and row.resolved_by is None

    consumed = repo.get_invite(project.project_id, invite.invite_id)
    assert consumed is not None
    assert consumed.consumed_at is not None
    assert consumed.consumed_by_user_id == alice_id

    events = _events(db, project.project_id)
    assert events[-1]["event_type"] == "project.join_requested"
    assert events[-1]["actor_user_id"] == alice_id
    assert events[-1]["object_id"] == result.request_id
    assert json.loads(events[-1]["payload_json"]) == {
        "user_id": alice_id,
        "invite_id": invite.invite_id,
    }


def test_redeem_second_pending_request_conflicts_without_consuming(
    repo: ProjectRepo, owner_id: int, alice_id: int
) -> None:
    project = repo.create_with_owner(creator_user_id=owner_id, name="DupRequest")
    _make_invite(
        repo, project.project_id, created_by=owner_id, token="tok-first", requires_approval=True
    )
    second = _make_invite(
        repo, project.project_id, created_by=owner_id, token="tok-second", requires_approval=True
    )

    first = repo.redeem_invite(token_hash=hash_invite_token("tok-first"), user_id=alice_id)
    assert first.outcome == "requested"
    dup = repo.redeem_invite(token_hash=hash_invite_token("tok-second"), user_id=alice_id)
    assert dup.outcome == "pending_exists"
    assert dup.request_id == first.request_id
    # The second invite was never consumed and stays usable/revocable.
    untouched = repo.get_invite(project.project_id, second.invite_id)
    assert untouched is not None
    assert untouched.consumed_at is None
    assert untouched.status() == "pending"
    assert len(repo.list_join_requests(project.project_id)) == 1


def test_pending_requests_unique_index_blocks_duplicates(
    repo: ProjectRepo, db: SqlitePool, owner_id: int, alice_id: int
) -> None:
    project = repo.create_with_owner(creator_user_id=owner_id, name="IndexGuard")
    _make_invite(
        repo, project.project_id, created_by=owner_id, token="tok-guard", requires_approval=True
    )
    repo.redeem_invite(token_hash=hash_invite_token("tok-guard"), user_id=alice_id)
    with pytest.raises(sqlite3.IntegrityError), db.transaction() as conn:
        conn.execute(
            "INSERT INTO project_join_requests("
            "request_id, project_id, invite_id, user_id, status, requested_at"
            ") VALUES (?, ?, NULL, ?, 'pending', ?)",
            (new_ulid(), project.project_id, alice_id, now_ts()),
        )


def test_redeem_rejects_invalid_revoked_and_expired(
    repo: ProjectRepo, db: SqlitePool, owner_id: int, alice_id: int
) -> None:
    project = repo.create_with_owner(creator_user_id=owner_id, name="Denied")

    assert (
        repo.redeem_invite(token_hash=hash_invite_token("nope"), user_id=alice_id).outcome
        == "invalid"
    )

    revoked = _make_invite(repo, project.project_id, created_by=owner_id, token="tok-denied-rev")
    repo.revoke_invite(project.project_id, revoked.invite_id, actor_user_id=owner_id)
    assert (
        repo.redeem_invite(token_hash=hash_invite_token("tok-denied-rev"), user_id=alice_id).outcome
        == "revoked"
    )

    _make_invite(
        repo,
        project.project_id,
        created_by=owner_id,
        token="tok-denied-exp",
        expires_at=now_ts() - 1,
    )
    assert (
        repo.redeem_invite(token_hash=hash_invite_token("tok-denied-exp"), user_id=alice_id).outcome
        == "expired"
    )

    assert repo.get_membership(project.project_id, alice_id) is None
    types = {e["event_type"] for e in _events(db, project.project_id)}
    assert "project.member_joined" not in types
    assert "project.join_requested" not in types


def test_redeem_already_member_conflicts_without_consuming(
    repo: ProjectRepo, owner_id: int, alice_id: int
) -> None:
    project = repo.create_with_owner(creator_user_id=owner_id, name="Already")
    repo.add_member(project.project_id, alice_id, role="member")
    invite = _make_invite(repo, project.project_id, created_by=owner_id, token="tok-already")

    result = repo.redeem_invite(token_hash=hash_invite_token("tok-already"), user_id=alice_id)
    assert result.outcome == "already_member"
    fresh = repo.get_invite(project.project_id, invite.invite_id)
    assert fresh is not None
    assert fresh.consumed_at is None


def test_redeem_rolls_back_when_event_insert_fails(
    repo: ProjectRepo, db: SqlitePool, owner_id: int, alice_id: int
) -> None:
    """A failure inside redemption must leave no membership and no consumption."""
    project = repo.create_with_owner(creator_user_id=owner_id, name="Rollback")
    invite = _make_invite(repo, project.project_id, created_by=owner_id, token="tok-rollback")
    with db.connect() as conn:
        conn.executescript("ALTER TABLE project_events RENAME TO project_events_backup;")
    try:
        with pytest.raises(sqlite3.OperationalError):
            repo.redeem_invite(token_hash=hash_invite_token("tok-rollback"), user_id=alice_id)
    finally:
        with db.connect() as conn:
            conn.executescript("ALTER TABLE project_events_backup RENAME TO project_events;")
    assert repo.get_membership(project.project_id, alice_id) is None
    fresh = repo.get_invite(project.project_id, invite.invite_id)
    assert fresh is not None
    assert fresh.consumed_at is None
    assert fresh.status() == "pending"


# ---------------------------------------------------------------------------
# Repo: join-request resolution
# ---------------------------------------------------------------------------


def test_resolve_join_request_approve(
    repo: ProjectRepo, db: SqlitePool, owner_id: int, alice_id: int
) -> None:
    project = repo.create_with_owner(creator_user_id=owner_id, name="Approve")
    _make_invite(
        repo, project.project_id, created_by=owner_id, token="tok-approve", requires_approval=True
    )
    redeemed = repo.redeem_invite(token_hash=hash_invite_token("tok-approve"), user_id=alice_id)
    assert redeemed.outcome == "requested"

    resolution = repo.resolve_join_request(
        project_id=project.project_id,
        request_id=redeemed.request_id,
        resolver_user_id=owner_id,
        approve=True,
    )
    assert resolution.outcome == "approved"
    assert resolution.request is not None
    assert resolution.request.status == "approved"
    assert resolution.request.resolved_at is not None
    assert resolution.request.resolved_by == owner_id
    assert resolution.request.username == "alice"

    membership = repo.get_membership(project.project_id, alice_id)
    assert membership is not None
    assert membership.role == "member"

    events = _events(db, project.project_id)
    assert events[-1]["event_type"] == "project.join_approved"
    assert events[-1]["actor_user_id"] == owner_id
    assert events[-1]["object_id"] == redeemed.request_id
    assert json.loads(events[-1]["payload_json"]) == {"user_id": alice_id, "role": "member"}

    # Repeated approval is a deterministic conflict without duplicate effects.
    again = repo.resolve_join_request(
        project_id=project.project_id,
        request_id=redeemed.request_id,
        resolver_user_id=owner_id,
        approve=True,
    )
    assert again.outcome == "resolved"
    assert repo.count_members(project.project_id) == 2
    approved = [
        e for e in _events(db, project.project_id) if e["event_type"] == "project.join_approved"
    ]
    assert len(approved) == 1


def test_resolve_join_request_reject(
    repo: ProjectRepo, db: SqlitePool, owner_id: int, alice_id: int
) -> None:
    project = repo.create_with_owner(creator_user_id=owner_id, name="Reject")
    _make_invite(
        repo, project.project_id, created_by=owner_id, token="tok-reject", requires_approval=True
    )
    redeemed = repo.redeem_invite(token_hash=hash_invite_token("tok-reject"), user_id=alice_id)

    resolution = repo.resolve_join_request(
        project_id=project.project_id,
        request_id=redeemed.request_id,
        resolver_user_id=owner_id,
        approve=False,
    )
    assert resolution.outcome == "rejected"
    assert resolution.request is not None
    assert resolution.request.status == "rejected"
    assert repo.get_membership(project.project_id, alice_id) is None

    events = _events(db, project.project_id)
    assert events[-1]["event_type"] == "project.join_rejected"
    assert json.loads(events[-1]["payload_json"]) == {"user_id": alice_id}

    again = repo.resolve_join_request(
        project_id=project.project_id,
        request_id=redeemed.request_id,
        resolver_user_id=owner_id,
        approve=False,
    )
    assert again.outcome == "resolved"
    rejected = [
        e for e in _events(db, project.project_id) if e["event_type"] == "project.join_rejected"
    ]
    assert len(rejected) == 1


def test_resolve_join_request_missing_and_wrong_project(
    repo: ProjectRepo, owner_id: int, alice_id: int
) -> None:
    project = repo.create_with_owner(creator_user_id=owner_id, name="Missing")
    other = repo.create_with_owner(creator_user_id=owner_id, name="Other")
    _make_invite(
        repo, project.project_id, created_by=owner_id, token="tok-missing", requires_approval=True
    )
    redeemed = repo.redeem_invite(token_hash=hash_invite_token("tok-missing"), user_id=alice_id)

    missing = repo.resolve_join_request(
        project_id=project.project_id,
        request_id="no-such-request",
        resolver_user_id=owner_id,
        approve=True,
    )
    assert missing.outcome == "missing"
    cross = repo.resolve_join_request(
        project_id=other.project_id,
        request_id=redeemed.request_id,
        resolver_user_id=owner_id,
        approve=True,
    )
    assert cross.outcome == "missing"
    assert repo.get_membership(project.project_id, alice_id) is None


def test_resolve_approve_conflicts_when_user_already_member(
    repo: ProjectRepo, owner_id: int, alice_id: int
) -> None:
    project = repo.create_with_owner(creator_user_id=owner_id, name="AlreadyMember")
    _make_invite(
        repo, project.project_id, created_by=owner_id, token="tok-already2", requires_approval=True
    )
    redeemed = repo.redeem_invite(token_hash=hash_invite_token("tok-already2"), user_id=alice_id)
    repo.add_member(project.project_id, alice_id, role="member")

    resolution = repo.resolve_join_request(
        project_id=project.project_id,
        request_id=redeemed.request_id,
        resolver_user_id=owner_id,
        approve=True,
    )
    assert resolution.outcome == "already_member"
    # Request stays pending; the manager must reject or the member stays joined.
    pending = repo.list_join_requests(project.project_id, status="pending")
    assert [r.request_id for r in pending] == [redeemed.request_id]


def test_list_join_requests_status_filter(
    repo: ProjectRepo, owner_id: int, alice_id: int, bob_id: int
) -> None:
    project = repo.create_with_owner(creator_user_id=owner_id, name="Filters")
    for user, token in ((alice_id, "tok-filter-a"), (bob_id, "tok-filter-b")):
        _make_invite(
            repo, project.project_id, created_by=owner_id, token=token, requires_approval=True
        )
        redeemed = repo.redeem_invite(token_hash=hash_invite_token(token), user_id=user)
        if user == bob_id:
            repo.resolve_join_request(
                project_id=project.project_id,
                request_id=redeemed.request_id,
                resolver_user_id=owner_id,
                approve=False,
            )

    assert len(repo.list_join_requests(project.project_id)) == 2
    pending = repo.list_join_requests(project.project_id, status="pending")
    assert [r.user_id for r in pending] == [alice_id]
    rejected = repo.list_join_requests(project.project_id, status="rejected")
    assert [r.user_id for r in rejected] == [bob_id]
    assert repo.list_join_requests(project.project_id, status="approved") == []


# ---------------------------------------------------------------------------
# Repo: member role changes and removal
# ---------------------------------------------------------------------------


def test_set_member_role_changes_and_events(
    repo: ProjectRepo, db: SqlitePool, owner_id: int, member_id: int
) -> None:
    project = repo.create_with_owner(creator_user_id=owner_id, name="Promote")
    repo.add_member(project.project_id, member_id, role="member")

    result = repo.set_member_role(
        project_id=project.project_id,
        user_id=member_id,
        role="admin",
        actor_user_id=owner_id,
        forbid_roles=("owner",),
    )
    assert result.outcome == "changed"
    assert result.role == "admin"
    assert repo.get_membership(project.project_id, member_id) is not None
    assert repo.get_membership(project.project_id, member_id).role == "admin"

    events = _events(db, project.project_id)
    assert events[-1]["event_type"] == "project.member_role_changed"
    assert events[-1]["actor_user_id"] == owner_id
    assert events[-1]["object_id"] == str(member_id)
    assert json.loads(events[-1]["payload_json"]) == {
        "user_id": member_id,
        "from_role": "member",
        "to_role": "admin",
    }

    same = repo.set_member_role(
        project_id=project.project_id,
        user_id=member_id,
        role="admin",
        actor_user_id=owner_id,
        forbid_roles=("owner",),
    )
    assert same.outcome == "noop"
    assert len(_events(db, project.project_id)) == len(events)

    owner_target = repo.set_member_role(
        project_id=project.project_id,
        user_id=owner_id,
        role="member",
        actor_user_id=owner_id,
        forbid_roles=("owner",),
    )
    assert owner_target.outcome == "forbidden_role"
    assert owner_target.role == "owner"
    assert repo.get_membership(project.project_id, owner_id).role == "owner"

    missing = repo.set_member_role(
        project_id=project.project_id,
        user_id=999_999,
        role="admin",
        actor_user_id=owner_id,
        forbid_roles=("owner",),
    )
    assert missing.outcome == "missing"


def test_remove_member_deletes_and_events(
    repo: ProjectRepo, db: SqlitePool, owner_id: int, member_id: int
) -> None:
    project = repo.create_with_owner(creator_user_id=owner_id, name="Remove")
    repo.add_member(project.project_id, member_id, role="member")

    result = repo.remove_member(
        project_id=project.project_id,
        user_id=member_id,
        actor_user_id=owner_id,
        forbid_roles=("owner",),
    )
    assert result.outcome == "removed"
    assert result.role == "member"
    assert repo.get_membership(project.project_id, member_id) is None

    events = _events(db, project.project_id)
    assert events[-1]["event_type"] == "project.member_removed"
    assert events[-1]["actor_user_id"] == owner_id
    assert events[-1]["object_id"] == str(member_id)
    assert json.loads(events[-1]["payload_json"]) == {"user_id": member_id, "role": "member"}

    missing = repo.remove_member(
        project_id=project.project_id,
        user_id=member_id,
        actor_user_id=owner_id,
        forbid_roles=("owner",),
    )
    assert missing.outcome == "missing"

    owner_target = repo.remove_member(
        project_id=project.project_id,
        user_id=owner_id,
        actor_user_id=owner_id,
        forbid_roles=("owner",),
    )
    assert owner_target.outcome == "forbidden_role"
    assert repo.get_membership(project.project_id, owner_id) is not None


def test_remove_member_honors_forbid_roles_for_admins(
    repo: ProjectRepo, owner_id: int, member_id: int, admin_member_id: int
) -> None:
    project = repo.create_with_owner(creator_user_id=owner_id, name="AdminLimits")
    repo.add_member(project.project_id, member_id, role="member")
    repo.add_member(project.project_id, admin_member_id, role="admin")

    blocked = repo.remove_member(
        project_id=project.project_id,
        user_id=admin_member_id,
        actor_user_id=admin_member_id,
        forbid_roles=("owner", "admin"),
    )
    assert blocked.outcome == "forbidden_role"
    assert blocked.role == "admin"
    assert repo.get_membership(project.project_id, admin_member_id) is not None

    allowed = repo.remove_member(
        project_id=project.project_id,
        user_id=member_id,
        actor_user_id=admin_member_id,
        forbid_roles=("owner", "admin"),
    )
    assert allowed.outcome == "removed"


# ---------------------------------------------------------------------------
# Service: invite lifecycle, token hygiene, and authorization
# ---------------------------------------------------------------------------


def test_service_create_invite_returns_plaintext_token_once(
    service: ProjectService, repo: ProjectRepo, db: SqlitePool, owner_id: int
) -> None:
    project = repo.create_with_owner(creator_user_id=owner_id, name="Token")
    created = service.create_invite(project.project_id, actor_user_id=owner_id)

    assert created.token
    assert created.status == "pending"
    assert created.role == "member"
    assert created.requires_approval is False
    assert created.created_by == owner_id
    assert created.expires_at - created.created_at == 7 * 86400

    with db.connect() as conn:
        stored = conn.execute(
            "SELECT token_hash FROM project_invites WHERE invite_id = ?", (created.invite_id,)
        ).fetchone()[0]
        payloads = "".join(
            str(r[0]) for r in conn.execute("SELECT payload_json FROM project_events").fetchall()
        )
    assert stored == hashlib.sha256(created.token.encode("utf-8")).hexdigest()
    assert created.token not in payloads
    assert stored not in payloads

    listed = service.list_invites(project.project_id, actor_user_id=owner_id)
    assert len(listed) == 1
    view = listed[0]
    assert view.invite_id == created.invite_id
    assert not hasattr(view, "token")
    assert not hasattr(view, "token_hash")
    assert view.status == "pending"


def test_service_create_invite_validates_expiry_bounds(
    service: ProjectService, repo: ProjectRepo, owner_id: int
) -> None:
    project = repo.create_with_owner(creator_user_id=owner_id, name="Bounds")
    for bad in (0, 8, -1):
        with pytest.raises(ValueError):
            service.create_invite(project.project_id, actor_user_id=owner_id, expires_in_days=bad)
    one = service.create_invite(project.project_id, actor_user_id=owner_id, expires_in_days=1)
    assert one.expires_at - one.created_at == 86400
    seven = service.create_invite(project.project_id, actor_user_id=owner_id, expires_in_days=7)
    assert seven.expires_at - seven.created_at == 7 * 86400


def test_service_invite_management_requires_owner_or_admin(
    service: ProjectService,
    repo: ProjectRepo,
    owner_id: int,
    member_id: int,
    admin_member_id: int,
    outsider_id: int,
) -> None:
    project = repo.create_with_owner(creator_user_id=owner_id, name="Gates")
    repo.add_member(project.project_id, member_id, role="member")
    repo.add_member(project.project_id, admin_member_id, role="admin")
    created = service.create_invite(project.project_id, actor_user_id=admin_member_id)

    for actor in (member_id, outsider_id):
        expected = ErrorCode.NOT_FOUND if actor == outsider_id else ErrorCode.FORBIDDEN
        with pytest.raises(OctopError) as exc:
            service.create_invite(project.project_id, actor_user_id=actor)
        assert exc.value.code is expected
        with pytest.raises(OctopError):
            service.list_invites(project.project_id, actor_user_id=actor)
        with pytest.raises(OctopError):
            service.revoke_invite(project.project_id, created.invite_id, actor_user_id=actor)
        with pytest.raises(OctopError):
            service.list_join_requests(project.project_id, actor_user_id=actor)

    revoked = service.revoke_invite(
        project.project_id, created.invite_id, actor_user_id=admin_member_id
    )
    assert revoked.status == "revoked"
    assert revoked.revoked_at is not None

    with pytest.raises(OctopError) as exc:
        service.revoke_invite(project.project_id, created.invite_id, actor_user_id=owner_id)
    assert exc.value.code is ErrorCode.INVITE_INVALID
    assert exc.value.status == 409
    with pytest.raises(OctopError) as exc:
        service.revoke_invite(project.project_id, "missing", actor_user_id=owner_id)
    assert exc.value.code is ErrorCode.NOT_FOUND


def test_service_accept_invite_maps_outcomes_without_leaking_project(
    service: ProjectService, repo: ProjectRepo, owner_id: int, alice_id: int, bob_id: int
) -> None:
    project = repo.create_with_owner(creator_user_id=owner_id, name="秘密项目")

    with pytest.raises(OctopError) as exc:
        service.accept_invite("totally-wrong-token", user_id=alice_id)
    assert exc.value.code is ErrorCode.INVITE_INVALID
    assert exc.value.status == 400
    assert project.project_id not in str(exc.value.details)
    assert "秘密项目" not in exc.value.message

    with pytest.raises(OctopError) as exc:
        service.accept_invite("   ", user_id=alice_id)
    assert exc.value.code is ErrorCode.INVITE_INVALID

    revoked = service.create_invite(project.project_id, actor_user_id=owner_id)
    service.revoke_invite(project.project_id, revoked.invite_id, actor_user_id=owner_id)
    with pytest.raises(OctopError) as exc:
        service.accept_invite(revoked.token, user_id=alice_id)
    assert exc.value.code is ErrorCode.INVITE_REVOKED
    assert exc.value.status == 410
    assert project.project_id not in str(exc.value.details)

    expired = service.create_invite(project.project_id, actor_user_id=owner_id, expires_in_days=1)
    repo_expire_invite(service, project.project_id, expired.invite_id)
    with pytest.raises(OctopError) as exc:
        service.accept_invite(expired.token, user_id=alice_id)
    assert exc.value.code is ErrorCode.INVITE_EXPIRED
    assert exc.value.status == 410

    used = service.create_invite(project.project_id, actor_user_id=owner_id)
    joined = service.accept_invite(used.token, user_id=alice_id)
    assert joined.status == "joined"
    assert joined.project_id == project.project_id
    assert joined.role == "member"
    with pytest.raises(OctopError) as exc:
        service.accept_invite(used.token, user_id=bob_id)
    assert exc.value.code is ErrorCode.INVITE_USED
    assert exc.value.status == 409
    assert project.project_id not in str(exc.value.details)

    another = service.create_invite(project.project_id, actor_user_id=owner_id)
    with pytest.raises(OctopError) as exc:
        service.accept_invite(another.token, user_id=alice_id)
    assert exc.value.code is ErrorCode.INVITE_USED
    assert exc.value.status == 409
    assert exc.value.details.get("reason") == "already_member"


def repo_expire_invite(service: ProjectService, project_id: str, invite_id: str) -> None:
    """Force an invite into the past (test helper — bypasses the 1-day floor)."""
    repo = service._repo  # noqa: SLF001 — test-only shortcut
    with repo._db.connect() as conn:  # noqa: SLF001
        conn.execute(
            "UPDATE project_invites SET expires_at = 1 WHERE project_id = ? AND invite_id = ?",
            (project_id, invite_id),
        )


def test_service_accept_approval_invite_flow(
    service: ProjectService, repo: ProjectRepo, owner_id: int, alice_id: int, outsider_id: int
) -> None:
    project = repo.create_with_owner(creator_user_id=owner_id, name="Flow")
    created = service.create_invite(
        project.project_id, actor_user_id=owner_id, requires_approval=True
    )

    accepted = service.accept_invite(created.token, user_id=alice_id)
    assert accepted.status == "pending_approval"
    assert accepted.request_id
    assert accepted.project_id == project.project_id

    # No project read permission before approval.
    with pytest.raises(OctopError) as exc:
        service.get_view(project.project_id, user_id=alice_id)
    assert exc.value.code is ErrorCode.NOT_FOUND

    pending = service.list_join_requests(project.project_id, actor_user_id=owner_id)
    assert len(pending) == 1
    assert pending[0].username == "alice"
    assert pending[0].status == "pending"
    assert pending[0].request_id == accepted.request_id

    approved = service.approve_join_request(
        project.project_id, accepted.request_id, actor_user_id=owner_id
    )
    assert approved.status == "approved"
    assert approved.resolved_by == owner_id
    view = service.get_view(project.project_id, user_id=alice_id)
    assert view.my_role == "member"

    with pytest.raises(OctopError) as exc:
        service.approve_join_request(
            project.project_id, accepted.request_id, actor_user_id=owner_id
        )
    assert exc.value.code is ErrorCode.INVITE_INVALID
    assert exc.value.status == 409

    # A rejected requester stays outside; rejection is repeatable-safe.
    second_project = repo.create_with_owner(creator_user_id=owner_id, name="Flow2")
    invite2 = service.create_invite(
        second_project.project_id, actor_user_id=owner_id, requires_approval=True
    )
    accepted2 = service.accept_invite(invite2.token, user_id=outsider_id)
    rejected = service.reject_join_request(
        second_project.project_id, accepted2.request_id, actor_user_id=owner_id
    )
    assert rejected.status == "rejected"
    with pytest.raises(OctopError):
        service.get_view(second_project.project_id, user_id=outsider_id)
    with pytest.raises(OctopError) as exc:
        service.reject_join_request(
            second_project.project_id, accepted2.request_id, actor_user_id=owner_id
        )
    assert exc.value.code is ErrorCode.INVITE_INVALID
    assert exc.value.status == 409
    with pytest.raises(OctopError) as exc:
        service.approve_join_request(second_project.project_id, "missing", actor_user_id=owner_id)
    assert exc.value.code is ErrorCode.NOT_FOUND


def test_service_join_request_management_requires_owner_or_admin(
    service: ProjectService, repo: ProjectRepo, owner_id: int, member_id: int, alice_id: int
) -> None:
    project = repo.create_with_owner(creator_user_id=owner_id, name="RequestGates")
    created = service.create_invite(
        project.project_id, actor_user_id=owner_id, requires_approval=True
    )
    accepted = service.accept_invite(created.token, user_id=alice_id)
    repo.add_member(project.project_id, member_id)

    with pytest.raises(OctopError) as exc:
        service.list_join_requests(project.project_id, actor_user_id=member_id)
    assert exc.value.code is ErrorCode.FORBIDDEN
    with pytest.raises(OctopError):
        service.approve_join_request(
            project.project_id, accepted.request_id, actor_user_id=member_id
        )
    with pytest.raises(OctopError):
        service.reject_join_request(
            project.project_id, accepted.request_id, actor_user_id=member_id
        )
    # The request survived the forbidden attempts untouched.
    pending = service.list_join_requests(project.project_id, actor_user_id=owner_id)
    assert [r.status for r in pending] == ["pending"]


def test_service_role_change_is_owner_only(
    service: ProjectService,
    repo: ProjectRepo,
    owner_id: int,
    member_id: int,
    admin_member_id: int,
    outsider_id: int,
) -> None:
    project = repo.create_with_owner(creator_user_id=owner_id, name="RoleChange")
    repo.add_member(project.project_id, member_id, role="member")
    repo.add_member(project.project_id, admin_member_id, role="admin")

    # Only owner may promote/demote; admin and member actors are forbidden.
    for actor in (admin_member_id, member_id):
        with pytest.raises(OctopError) as exc:
            service.set_member_role(
                project.project_id, member_id, role="admin", actor_user_id=actor
            )
        assert exc.value.code is ErrorCode.FORBIDDEN
    with pytest.raises(OctopError) as exc:
        service.set_member_role(
            project.project_id, member_id, role="admin", actor_user_id=outsider_id
        )
    assert exc.value.code is ErrorCode.NOT_FOUND

    promoted = service.set_member_role(
        project.project_id, member_id, role="admin", actor_user_id=owner_id
    )
    assert promoted.role == "admin"
    assert promoted.username == "member"
    demoted = service.set_member_role(
        project.project_id, member_id, role="member", actor_user_id=owner_id
    )
    assert demoted.role == "member"

    with pytest.raises(OctopError) as exc:
        service.set_member_role(project.project_id, owner_id, role="member", actor_user_id=owner_id)
    assert exc.value.code is ErrorCode.FORBIDDEN
    with pytest.raises(OctopError) as exc:
        service.set_member_role(project.project_id, 999_999, role="admin", actor_user_id=owner_id)
    assert exc.value.code is ErrorCode.NOT_FOUND
    with pytest.raises(ValueError):
        service.set_member_role(project.project_id, member_id, role="owner", actor_user_id=owner_id)


def test_service_remove_member_rules(
    service: ProjectService,
    repo: ProjectRepo,
    owner_id: int,
    member_id: int,
    admin_member_id: int,
    outsider_id: int,
) -> None:
    project = repo.create_with_owner(creator_user_id=owner_id, name="Removal")
    repo.add_member(project.project_id, member_id, role="member")
    repo.add_member(project.project_id, admin_member_id, role="admin")

    # Regular members cannot remove anyone.
    with pytest.raises(OctopError) as exc:
        service.remove_member(project.project_id, admin_member_id, actor_user_id=member_id)
    assert exc.value.code is ErrorCode.FORBIDDEN
    # Outsiders get the same 404 as for unknown projects.
    with pytest.raises(OctopError) as exc:
        service.remove_member(project.project_id, member_id, actor_user_id=outsider_id)
    assert exc.value.code is ErrorCode.NOT_FOUND

    # Admin may remove a member but not an admin or the owner.
    service.remove_member(project.project_id, member_id, actor_user_id=admin_member_id)
    assert repo.get_membership(project.project_id, member_id) is None
    # Removal takes effect immediately for the removed user.
    with pytest.raises(OctopError) as exc:
        service.get_view(project.project_id, user_id=member_id)
    assert exc.value.code is ErrorCode.NOT_FOUND
    with pytest.raises(OctopError) as exc:
        service.list_members(project.project_id, user_id=member_id)
    assert exc.value.code is ErrorCode.NOT_FOUND

    with pytest.raises(OctopError) as exc:
        service.remove_member(project.project_id, owner_id, actor_user_id=admin_member_id)
    assert exc.value.code is ErrorCode.FORBIDDEN
    repo.add_member(project.project_id, member_id, role="admin")
    with pytest.raises(OctopError) as exc:
        service.remove_member(project.project_id, member_id, actor_user_id=admin_member_id)
    assert exc.value.code is ErrorCode.FORBIDDEN

    # Owner may remove an admin; owner cannot self-delete.
    service.remove_member(project.project_id, member_id, actor_user_id=owner_id)
    assert repo.get_membership(project.project_id, member_id) is None
    with pytest.raises(OctopError) as exc:
        service.remove_member(project.project_id, owner_id, actor_user_id=owner_id)
    assert exc.value.code is ErrorCode.FORBIDDEN
    assert repo.get_membership(project.project_id, owner_id) is not None

    with pytest.raises(OctopError) as exc:
        service.remove_member(project.project_id, 999_999, actor_user_id=owner_id)
    assert exc.value.code is ErrorCode.NOT_FOUND


def test_service_never_stores_or_returns_plaintext_token_after_create(
    service: ProjectService, repo: ProjectRepo, db: SqlitePool, owner_id: int, alice_id: int
) -> None:
    project = repo.create_with_owner(creator_user_id=owner_id, name="Hygiene")
    created = service.create_invite(
        project.project_id, actor_user_id=owner_id, requires_approval=True
    )
    accepted = service.accept_invite(created.token, user_id=alice_id)
    service.approve_join_request(project.project_id, accepted.request_id, actor_user_id=owner_id)

    with db.connect() as conn:
        all_payloads = "".join(
            str(r[0]) for r in conn.execute("SELECT payload_json FROM project_events").fetchall()
        )
        stored_hash = conn.execute(
            "SELECT token_hash FROM project_invites WHERE invite_id = ?", (created.invite_id,)
        ).fetchone()[0]
    assert created.token not in all_payloads
    assert stored_hash not in all_payloads
    assert stored_hash == hash_invite_token(created.token)

    for view in service.list_invites(project.project_id, actor_user_id=owner_id):
        assert "token" not in vars(view)
        assert "token_hash" not in vars(view)
