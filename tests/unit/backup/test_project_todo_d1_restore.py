"""D1 restoration failures use the existing database preimage compensation."""

import pytest
from tests.unit.backup.test_system_archive import _make_migration_backup

from octop.config import DatabaseConfig
from octop.infra.backup import snapshot, system_archive
from octop.infra.db.migrate import run_migrations
from octop.infra.db.pool import SqlitePool
from octop.infra.db.repos.project_todos import ProjectTodoRepo
from octop.infra.db.repos.projects import ProjectRepo
from octop.infra.db.repos.users import UserRepo
from octop.infra.utils.paths import PathLayout


@pytest.mark.parametrize("failure", ["upsert", "remap", "prune", "usage"])
def test_preservation_failure_restores_database_images_and_keeps_config(
    tmp_path, monkeypatch, failure
):
    source = PathLayout(tmp_path / "source")
    target = PathLayout(tmp_path / "target")
    archive = tmp_path / "backup.tar.gz"
    source_pool = _make_migration_backup(source, archive)
    target_pool = SqlitePool(target.db)
    try:
        run_migrations(source_pool)
        run_migrations(target_pool)
        current = UserRepo(target_pool).create(username="current", password_hash="h", role="admin")
        pid = (
            ProjectRepo(target_pool)
            .create_with_owner(creator_user_id=current, name="preimage")
            .project_id
        )
        todo = (
            ProjectTodoRepo(target_pool)
            .create(project_id=pid, creator_user_id=current, title="preserve me")
            .row
        )
        with target_pool.transaction() as conn:
            conn.execute(
                "UPDATE project_todo_display_state SET revision=23 WHERE todo_id=?", (todo.todo_id,)
            )
            conn.execute("INSERT INTO settings(key,value) VALUES ('preimage','current')")
        old_image = target.project_todo_comment_images / "old-object"
        old_image.parent.mkdir(parents=True)
        old_image.write_bytes(b"owned old image tree")
        target.config.write_text('{"port": 8888}', encoding="utf-8")
        if failure == "usage":
            real_validate = system_archive.validate_project_todo_d1_in_connection

            def corrupt_and_validate(conn):
                # Materialize a real inconsistent project usage after users were
                # already upserted, ownership remapped and backup users pruned.
                conn.execute(
                    "INSERT INTO project_spaces(project_id,creator_user_id,name,created_at,updated_at) VALUES ('usage-check',?,'check',1,1)",
                    (current,),
                )
                conn.execute("INSERT INTO project_todo_attachment_usage VALUES ('usage-check',1,1)")
                real_validate(conn)

            monkeypatch.setattr(
                system_archive, "validate_project_todo_d1_in_connection", corrupt_and_validate
            )
        else:
            attribute = {
                "upsert": "upsert_users_into_pool",
                "remap": "remap_ownership_to_user",
                "prune": "prune_users_not_in",
            }[failure]
            real_stage = getattr(system_archive, attribute)

            def fail_after_stage(*args, **kwargs):
                real_stage(*args, **kwargs)
                raise RuntimeError("owned preservation failure")

            monkeypatch.setattr(system_archive, attribute, fail_after_stage)
        with pytest.raises(RuntimeError):
            system_archive.restore_system_backup(
                archive,
                paths=target,
                pool=target_pool,
                db_config=DatabaseConfig(),
                preserve_users=True,
            )
        with target_pool.connect() as conn:
            assert [row[0] for row in conn.execute("SELECT username FROM users ORDER BY id")] == [
                "current"
            ]
            preimage = conn.execute("SELECT value FROM settings WHERE key='preimage'").fetchone()
            assert preimage is not None and preimage[0] == "current"
            assert (
                conn.execute(
                    "SELECT revision FROM project_todo_display_state WHERE todo_id=?",
                    (todo.todo_id,),
                ).fetchone()[0]
                == 23
            )
            assert conn.execute("SELECT COUNT(*) FROM agents").fetchone()[0] == 0
        assert old_image.read_bytes() == b"owned old image tree"
        assert target.config.read_text(encoding="utf-8") == '{"port": 8888}'
    finally:
        source_pool.close()
        target_pool.close()


def test_real_restore_keeps_high_display_and_prune_advances_surviving_assignee(tmp_path):
    source, target = PathLayout(tmp_path / "source"), PathLayout(tmp_path / "target")
    source_pool, target_pool = SqlitePool(source.db), SqlitePool(target.db)
    try:
        run_migrations(source_pool)
        run_migrations(target_pool)
        users = UserRepo(source_pool)
        owner = users.create(username="owner", password_hash="h", role="user")
        member = users.create(username="removed", password_hash="h", role="user")
        UserRepo(target_pool).create(username="owner", password_hash="h", role="user")
        projects = ProjectRepo(source_pool)
        pid = projects.create_with_owner(creator_user_id=owner, name="restore").project_id
        projects.add_member(pid, member, role="member")
        todo = (
            ProjectTodoRepo(source_pool)
            .create(project_id=pid, creator_user_id=owner, assignee_user_id=member, title="high")
            .row
        )
        with source_pool.transaction() as conn:
            conn.execute(
                "UPDATE project_todo_display_state SET revision=27 WHERE todo_id=?", (todo.todo_id,)
            )
        archive = tmp_path / "display.tar.gz"
        system_archive.create_system_backup(
            paths=source, agent_rows=[], pool=source_pool, db_config=DatabaseConfig(), dest=archive
        )
        system_archive.restore_system_backup(
            archive, paths=target, pool=target_pool, db_config=DatabaseConfig(), preserve_users=True
        )
        row = ProjectTodoRepo(target_pool).get(pid, todo.todo_id)
        assert row.assignee_user_id is None
        assert row.version == todo.version
        assert row.display_revision == 28
        run_migrations(target_pool)
        assert ProjectTodoRepo(target_pool).get(pid, todo.todo_id).display_revision == 28
    finally:
        source_pool.close()
        target_pool.close()


@pytest.mark.parametrize("interrupt", [KeyboardInterrupt, SystemExit])
@pytest.mark.parametrize("stage", ["upsert", "usage"])
def test_interrupt_inside_write_transaction_restores_preimage(
    tmp_path, monkeypatch, interrupt, stage
):
    source, target = PathLayout(tmp_path / "source"), PathLayout(tmp_path / "target")
    archive = tmp_path / "interrupt.tar.gz"
    source_pool = _make_migration_backup(source, archive)
    target_pool = SqlitePool(target.db)
    observed = []
    interrupted_transactions = []
    try:
        run_migrations(target_pool)
        current = UserRepo(target_pool).create(username="current", password_hash="h", role="admin")
        pid = (
            ProjectRepo(target_pool)
            .create_with_owner(creator_user_id=current, name="preimage")
            .project_id
        )
        todo = (
            ProjectTodoRepo(target_pool)
            .create(project_id=pid, creator_user_id=current, title="preimage")
            .row
        )
        with target_pool.transaction() as conn:
            conn.execute("INSERT INTO settings(key,value) VALUES ('preimage','current')")
            conn.execute(
                "UPDATE project_todo_display_state SET revision=23 WHERE todo_id=?", (todo.todo_id,)
            )
        old_image = target.project_todo_comment_images / "old-object"
        old_image.parent.mkdir(parents=True)
        old_image.write_bytes(b"preimage image")
        target.config.write_text('{"port": 8888}', encoding="utf-8")

        def interrupt_inside_transaction(conn):
            conn.execute("INSERT INTO settings(key,value) VALUES ('interrupt','uncommitted')")
            assert conn.in_transaction
            interrupted_transactions.append(conn.in_transaction)
            raise interrupt("owned transaction interruption")

        real_restore = system_archive.restore_sqlite_into_pool

        def restore_with_transaction_precondition(backup, pool):
            with pool.connect() as live:
                observed.append(live.in_transaction)
                # SQLite backup into a write transaction can retry indefinitely.
                # Bound the RED check at the actual destination precondition.
                if live.in_transaction:
                    raise RuntimeError("backup destination still has a write transaction")
            real_restore(backup, pool)

        if stage == "usage":
            monkeypatch.setattr(
                system_archive,
                "validate_project_todo_d1_in_connection",
                interrupt_inside_transaction,
            )
        else:

            def interrupt_user_key(*args):
                # The real upsert calls this helper inside its transaction.
                with target_pool.connect() as conn:
                    interrupt_inside_transaction(conn)

            monkeypatch.setattr(snapshot, "project_plan_display_sort_key", interrupt_user_key)
        monkeypatch.setattr(
            system_archive, "restore_sqlite_into_pool", restore_with_transaction_precondition
        )
        with pytest.raises(interrupt, match="owned transaction interruption"):
            system_archive.restore_system_backup(
                archive,
                paths=target,
                pool=target_pool,
                db_config=DatabaseConfig(),
                preserve_users=True,
            )
        assert observed == [False, False]
        assert interrupted_transactions == [True]
        with target_pool.connect() as conn:
            assert not conn.in_transaction
            assert (
                conn.execute("SELECT value FROM settings WHERE key='preimage'").fetchone()[0]
                == "current"
            )
            assert (
                conn.execute("SELECT value FROM settings WHERE key='interrupt'").fetchone() is None
            )
            assert [row[0] for row in conn.execute("SELECT username FROM users")] == ["current"]
            assert conn.execute("SELECT COUNT(*) FROM agents").fetchone()[0] == 0
            assert (
                conn.execute(
                    "SELECT revision FROM project_todo_display_state WHERE todo_id=?",
                    (todo.todo_id,),
                ).fetchone()[0]
                == 23
            )
        assert old_image.read_bytes() == b"preimage image"
        assert target.config.read_text(encoding="utf-8") == '{"port": 8888}'
    finally:
        source_pool.close()
        target_pool.close()
