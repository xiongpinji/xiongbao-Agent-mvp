"""Real owned backup/prune and preimage compensation for D2."""

from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest

from octop.config import DatabaseConfig
from octop.infra.backup import system_archive
from octop.infra.backup.system_archive import create_system_backup, restore_system_backup
from octop.infra.db.migrate import run_migrations, validate_project_todo_d2_in_connection
from octop.infra.db.pool import SqlitePool
from octop.infra.db.repos.project_todos import ProjectTodoRepo
from octop.infra.db.repos.projects import ProjectRepo
from octop.infra.db.repos.users import UserRepo
from octop.infra.projects.todos import ProjectTodoService
from octop.infra.utils.paths import PathLayout


@pytest.mark.parametrize("fail_validation", [False, True])
def test_prune_promotes_saved_child_or_restores_preimage(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fail_validation: bool
) -> None:
    source = PathLayout(tmp_path / "source")
    target = PathLayout(tmp_path / "target")
    source.ensure_root()
    target.ensure_root()
    incoming = SqlitePool(source.db)
    live = SqlitePool(target.db)
    try:
        run_migrations(incoming)
        run_migrations(live)
        for pool in (incoming, live):
            users = UserRepo(pool)
            users.create(username="owner", password_hash="h", role="user")
            users.create(username="saved", password_hash="h", role="user")
        removed = UserRepo(incoming).create(username="removed", password_hash="h", role="user")
        projects = ProjectRepo(incoming)
        pid = projects.create_with_owner(creator_user_id=1, name="incoming").project_id
        projects.add_member(pid, 2, role="admin")
        projects.add_member(pid, removed, role="member")
        repo = ProjectTodoRepo(incoming)
        root = repo.create(project_id=pid, creator_user_id=removed, title="deleted parent").row
        service = ProjectTodoService(
            SimpleNamespace(project_todo_repo=repo, config=SimpleNamespace(default_timezone="UTC"))
        )
        child = service.create_child(
            pid,
            root.todo_id,
            actor_user_id=2,
            expected_children_revision=1,
            client_request_id=str(uuid4()),
            fields={"title": "saved body", "assignee_user_id": removed},
        )["item"]
        original_pid = (
            ProjectRepo(live).create_with_owner(creator_user_id=1, name="preimage").project_id
        )
        original = (
            ProjectTodoRepo(live)
            .create(project_id=original_pid, creator_user_id=1, title="preimage body")
            .row
        )
        archive = tmp_path / "owned.tar.gz"
        create_system_backup(
            paths=source, agent_rows=[], pool=incoming, db_config=DatabaseConfig(), dest=archive
        )
        if fail_validation:

            def fail(conn):
                raise RuntimeError("controlled D2 final validation failure")

            monkeypatch.setattr(system_archive, "validate_project_todo_d2_in_connection", fail)
            with pytest.raises(RuntimeError, match="controlled D2 final validation failure"):
                restore_system_backup(
                    archive,
                    paths=target,
                    pool=live,
                    db_config=DatabaseConfig(),
                    preserve_users=True,
                )
            assert (
                ProjectTodoRepo(live).get(original_pid, original.todo_id).title == "preimage body"
            )
            assert ProjectTodoRepo(live).get(pid, child["todo_id"]) is None
        else:
            restore_system_backup(
                archive, paths=target, pool=live, db_config=DatabaseConfig(), preserve_users=True
            )
            row = ProjectTodoRepo(live).get(pid, child["todo_id"], user_id=2)
            assert (row.title, row.creator_user_id, row.parent_todo_id, row.assignee_user_id) == (
                "saved body",
                2,
                None,
                None,
            )
            assert row.display_revision == child["display_revision"] + 1
            assert row.version == child["version"]
            with live.connect() as conn:
                validate_project_todo_d2_in_connection(conn)
                assert (
                    conn.execute(
                        "SELECT result_state FROM project_todo_child_create_requests"
                    ).fetchone()[0]
                    == "invalidated"
                )
        with live.connect() as conn:
            assert not conn.in_transaction
    finally:
        incoming.close()
        live.close()
