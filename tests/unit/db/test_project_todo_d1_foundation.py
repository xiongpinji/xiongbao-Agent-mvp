"""D1 foundation behavior on owned SQLite databases."""

import sqlite3
from pathlib import Path

import pytest

from octop.infra.db import migrate
from octop.infra.db.migrate import run_migrations
from octop.infra.db.pool import SqlitePool
from octop.infra.db.repos.project_todos import ProjectTodoRepo
from octop.infra.db.repos.projects import ProjectRepo
from octop.infra.db.repos.users import UserRepo


@pytest.fixture
def case(tmp_path: Path):
    pool = SqlitePool(tmp_path / "d1.db")
    try:
        run_migrations(pool)
        users, projects, todos = UserRepo(pool), ProjectRepo(pool), ProjectTodoRepo(pool)
        owner = users.create(username="owner", password_hash="h", role="user")
        member = users.create(username="member", password_hash="h", role="user")
        pid = projects.create_with_owner(creator_user_id=owner, name="D1").project_id
        projects.add_member(pid, member, role="member")
        row = todos.create(
            project_id=pid, creator_user_id=owner, title="original", assignee_user_id=member
        ).row
        assert row is not None
        yield pool, users, projects, todos, owner, member, pid, row
    finally:
        pool.close()


def test_create_and_multi_field_update_have_independent_display(case):
    pool, _, _, todos, owner, _, pid, row = case
    assert row.display_revision == 1
    result = todos.update(
        project_id=pid,
        todo_id=row.todo_id,
        actor_user_id=owner,
        expected_version=1,
        title="changed",
        description="new",
    )
    assert result.row is not None
    assert result.row.version == result.row.display_revision == 2
    assert (
        todos.update(
            project_id=pid,
            todo_id=row.todo_id,
            actor_user_id=owner,
            expected_version=2,
            title="changed",
        ).outcome
        == "no_change"
    )
    assert todos.get(pid, row.todo_id).display_revision == 2
    with pool.connect() as conn:
        assert (
            conn.execute(
                "SELECT revision FROM project_todo_attachment_state WHERE todo_id=?", (row.todo_id,)
            ).fetchone()[0]
            == 1
        )
        assert (
            conn.execute(
                "SELECT used_bytes FROM project_todo_attachment_usage WHERE project_id=?", (pid,)
            ).fetchone()[0]
            == 0
        )


def test_user_delete_changes_display_without_changing_body_version(case):
    _, users, _, todos, _, member, pid, row = case
    users.delete(member)
    fresh = todos.get(pid, row.todo_id)
    assert fresh.assignee_user_id is None
    assert fresh.version == row.version
    assert fresh.updated_at == row.updated_at
    assert fresh.display_revision == 2


def test_restart_preserves_display_and_rejects_lost_activated_state(case):
    pool, _, _, _, _, _, _, row = case
    with pool.transaction() as conn:
        conn.execute(
            "UPDATE project_todo_display_state SET revision=7 WHERE todo_id=?", (row.todo_id,)
        )
    run_migrations(pool)
    with pool.connect() as conn:
        assert (
            conn.execute(
                "SELECT revision FROM project_todo_display_state WHERE todo_id=?", (row.todo_id,)
            ).fetchone()[0]
            == 7
        )
    with pool.transaction() as conn:
        conn.execute("DELETE FROM project_todo_display_state WHERE todo_id=?", (row.todo_id,))
    with pytest.raises(RuntimeError):
        run_migrations(pool)


def test_bulk_same_value_and_soft_delete_keep_old_events_and_versions(case):
    pool, _, _, todos, owner, _, pid, row = case
    changed = todos.bulk_update(
        project_id=pid, items=[(row.todo_id, 1)], actor_user_id=owner, status="todo"
    )
    assert changed.outcome == "updated"
    assert changed.rows[0].version == changed.rows[0].display_revision == 2
    assert (
        todos.bulk_update(
            project_id=pid, items=[(row.todo_id, 1)], actor_user_id=owner, status="done"
        ).outcome
        == "stale"
    )
    assert todos.get(pid, row.todo_id).display_revision == 2
    assert (
        todos.delete(
            project_id=pid, todo_id=row.todo_id, actor_user_id=owner, expected_version=2
        ).outcome
        == "deleted"
    )
    assert todos.get(pid, row.todo_id) is None
    with pool.connect() as conn:
        assert (
            conn.execute(
                "SELECT revision FROM project_todo_display_state WHERE todo_id=?", (row.todo_id,)
            ).fetchone()[0]
            == 3
        )
        assert (
            conn.execute(
                "SELECT COUNT(*) FROM project_events WHERE object_id=?", (row.todo_id,)
            ).fetchone()[0]
            == 3
        )


def test_member_removal_only_bumps_actual_live_unassignment(case):
    pool, _, projects, todos, owner, member, pid, row = case
    deleted = todos.create(
        project_id=pid, creator_user_id=owner, title="deleted", assignee_user_id=member
    ).row
    todos.delete(project_id=pid, todo_id=deleted.todo_id, actor_user_id=owner, expected_version=1)
    result = projects.remove_member(project_id=pid, user_id=member, actor_user_id=owner)
    assert result.outcome == "removed"
    fresh = todos.get(pid, row.todo_id)
    assert fresh.version == fresh.display_revision == 2
    assert fresh.assignee_user_id is None
    with pool.connect() as conn:
        assert (
            conn.execute(
                "SELECT revision FROM project_todo_display_state WHERE todo_id=?",
                (deleted.todo_id,),
            ).fetchone()[0]
            == 2
        )


@pytest.mark.parametrize("writer", ["patch", "bulk", "member", "user"])
def test_display_exhaustion_rolls_back_entire_writer(case, writer):
    pool, users, projects, todos, owner, member, pid, row = case
    maximum = 9007199254740991
    with pool.transaction() as conn:
        conn.execute(
            "UPDATE project_todo_display_state SET revision=? WHERE todo_id=?",
            (maximum, row.todo_id),
        )
    assert todos.get(pid, row.todo_id).display_revision == maximum
    with pytest.raises((RuntimeError, sqlite3.IntegrityError)):
        if writer == "patch":
            todos.update(
                project_id=pid,
                todo_id=row.todo_id,
                actor_user_id=owner,
                expected_version=1,
                title="never committed",
            )
        elif writer == "bulk":
            todos.bulk_update(
                project_id=pid, items=[(row.todo_id, 1)], actor_user_id=owner, status="done"
            )
        elif writer == "member":
            projects.remove_member(project_id=pid, user_id=member, actor_user_id=owner)
        else:
            users.delete(member)
    fresh = todos.get(pid, row.todo_id)
    assert (
        fresh.title,
        fresh.status,
        fresh.version,
        fresh.display_revision,
        fresh.assignee_user_id,
    ) == (row.title, row.status, 1, maximum, member)
    assert users.get(member) is not None
    assert projects.get_membership(pid, member) is not None
    with pool.connect() as conn:
        assert (
            conn.execute(
                "SELECT COUNT(*) FROM project_events WHERE object_id=?", (row.todo_id,)
            ).fetchone()[0]
            == 1
        )
    with pytest.raises(sqlite3.IntegrityError), pool.transaction() as conn:
        conn.execute(
            "UPDATE project_todo_display_state SET revision=? WHERE todo_id=?",
            (maximum + 1, row.todo_id),
        )


def test_creator_cascade_retains_metadata_and_project_usage(case):
    pool, users, _, todos, _, member, pid, _ = case
    row = todos.create(project_id=pid, creator_user_id=member, title="creator cascade").row
    attachment_id = "01ARZ3NDEKTSV4RRFFQ69G5FAY"
    key = pid + "/" + attachment_id
    with pool.transaction() as conn:
        conn.execute(
            "INSERT INTO project_todo_attachments(attachment_id,project_id,todo_id,uploader_user_id,display_name,size_bytes,sha256,object_key,created_at,client_request_id,request_fingerprint) VALUES (?,?,?,?,?,7,?,?,1,?,?)",
            (
                attachment_id,
                pid,
                row.todo_id,
                member,
                "memo.txt",
                "a" * 64,
                key,
                "00000000-0000-4000-8000-000000000001",
                "b" * 64,
            ),
        )
        conn.execute(
            "UPDATE project_todo_attachment_usage SET used_bytes=7 WHERE project_id=?", (pid,)
        )
    users.delete(member)
    with pool.connect() as conn:
        assert (
            conn.execute(
                "SELECT COUNT(*) FROM project_todos WHERE todo_id=?", (row.todo_id,)
            ).fetchone()[0]
            == 0
        )
        assert (
            conn.execute(
                "SELECT COUNT(*) FROM project_todo_display_state WHERE todo_id=?", (row.todo_id,)
            ).fetchone()[0]
            == 0
        )
        assert (
            conn.execute(
                "SELECT object_key,size_bytes FROM project_todo_attachment_retained_objects"
            ).fetchone()["object_key"]
            == key
        )
        assert (
            conn.execute(
                "SELECT used_bytes FROM project_todo_attachment_usage WHERE project_id=?", (pid,)
            ).fetchone()[0]
            == 7
        )
        migrate.validate_project_todo_d1_in_connection(conn)
    run_migrations(pool)


@pytest.mark.parametrize("previous", [35, 36])
def test_genuine_old_schema_upgrade_initializes_all_todos(tmp_path, monkeypatch, previous):
    pool = SqlitePool(tmp_path / "legacy.db")
    discover = migrate._discover
    try:
        with monkeypatch.context() as patch:
            patch.setattr(
                migrate,
                "_discover",
                lambda dialect="sqlite": [(n, p) for n, p in discover(dialect) if n <= previous],
            )
            run_migrations(pool)
        with pool.transaction() as conn:
            conn.execute(
                "INSERT INTO users(username,password_hash,role,created_at) VALUES ('owner','h','user',1)"
            )
            conn.execute(
                "INSERT INTO project_spaces(project_id,creator_user_id,name,created_at,updated_at) VALUES ('legacy',1,'legacy',1,1)"
            )
            for todo_id, deleted_at in (("live", None), ("deleted", 10)):
                conn.execute(
                    "INSERT INTO project_todos(todo_id,project_id,creator_user_id,title,version,created_at,updated_at,deleted_at) VALUES (?,'legacy',1,'old',9,1,2,?)",
                    (todo_id, deleted_at),
                )
        run_migrations(pool)
        with pool.connect() as conn:
            assert [
                tuple(row)
                for row in conn.execute(
                    "SELECT todo_id,revision FROM project_todo_display_state ORDER BY todo_id"
                )
            ] == [("deleted", 1), ("live", 1)]
            assert [row[0] for row in conn.execute("SELECT version FROM project_todos")] == [9, 9]
            assert (
                conn.execute("SELECT used_bytes FROM project_todo_attachment_usage").fetchone()[0]
                == 0
            )
            assert conn.execute("SELECT version FROM _schema_version").fetchone()[
                0
            ] == migrate._max_discovered_version("sqlite")
    finally:
        pool.close()


def test_higher_watermark_preserves_revisions_and_missing_schema_fails_closed(case):
    pool, _, _, _, _, _, _, row = case
    with pool.transaction() as conn:
        conn.execute("UPDATE _schema_version SET version=77")
        conn.execute(
            "UPDATE project_todo_display_state SET revision=17 WHERE todo_id=?", (row.todo_id,)
        )
    run_migrations(pool)
    with pool.connect() as conn:
        assert conn.execute("SELECT version FROM _schema_version").fetchone()[0] == 77
        assert (
            conn.execute(
                "SELECT revision FROM project_todo_display_state WHERE todo_id=?", (row.todo_id,)
            ).fetchone()[0]
            == 17
        )
        conn.execute("DROP TABLE project_todo_display_state")
    with pytest.raises((RuntimeError, sqlite3.OperationalError)):
        run_migrations(pool)
    with pool.connect() as conn:
        assert (
            conn.execute(
                "SELECT name FROM sqlite_master WHERE name='project_todo_display_state'"
            ).fetchone()
            is None
        )


def test_partial_old_upgrade_preserves_existing_state_and_rejects_wrong_schema(
    tmp_path, monkeypatch
):
    pool = SqlitePool(tmp_path / "partial.db")
    discover = migrate._discover
    try:
        with monkeypatch.context() as patch:
            patch.setattr(
                migrate,
                "_discover",
                lambda dialect="sqlite": [(n, p) for n, p in discover(dialect) if n <= 36],
            )
            run_migrations(pool)
        canonical = Path(migrate.__file__).parent / "migrations/037_project_todo_d1_foundation.sql"
        statements = migrate._d1_statements(canonical.read_text(encoding="utf-8"), dialect="sqlite")
        with pool.transaction() as conn:
            conn.execute(
                "INSERT INTO users(username,password_hash,role,created_at) VALUES ('owner','h','user',1)"
            )
            conn.execute(
                "INSERT INTO project_spaces(project_id,creator_user_id,name,created_at,updated_at) VALUES ('partial',1,'partial',1,1)"
            )
            conn.execute(
                "INSERT INTO project_todos(todo_id,project_id,creator_user_id,title,created_at,updated_at) VALUES ('old','partial',1,'old',1,1)"
            )
            conn.execute(statements[0])
            conn.execute("INSERT INTO project_todo_display_state VALUES ('partial','old',13,1)")
        run_migrations(pool)
        with pool.connect() as conn:
            assert (
                conn.execute("SELECT revision FROM project_todo_display_state").fetchone()[0] == 13
            )
            conn.execute("DROP INDEX idx_project_todo_attachments_active")
            conn.execute(
                "CREATE INDEX idx_project_todo_attachments_active ON project_todo_attachments(todo_id)"
            )
        with pytest.raises(RuntimeError, match="schema definition differs"):
            run_migrations(pool)
    finally:
        pool.close()


def test_sqlite_failed_activation_rolls_back_all_new_ddl_and_watermark(tmp_path, monkeypatch):
    pool = SqlitePool(tmp_path / "rollback.db")
    discover = migrate._discover
    try:
        with monkeypatch.context() as patch:
            patch.setattr(
                migrate,
                "_discover",
                lambda dialect="sqlite": [(n, p) for n, p in discover(dialect) if n <= 36],
            )
            run_migrations(pool)
        with pool.connect() as conn:
            conn.execute(
                "CREATE TABLE project_todo_display_state(project_id TEXT,todo_id TEXT,revision INTEGER,updated_at INTEGER)"
            )
        with pytest.raises(RuntimeError, match="schema definition differs"):
            run_migrations(pool)
        with pool.connect() as conn:
            assert conn.execute("SELECT version FROM _schema_version").fetchone()[0] == 36
            assert (
                conn.execute(
                    "SELECT name FROM sqlite_master WHERE name='project_todo_attachments'"
                ).fetchone()
                is None
            )
            assert not conn.in_transaction
            assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1
    finally:
        pool.close()


def test_pg_canonical_statements_keep_functions_whole_and_temporary_fks_complete():
    path = Path(migrate.__file__).parent / "migrations/037_project_todo_d1_foundation.pg.sql"
    statements = migrate._d1_statements(path.read_text(encoding="utf-8"), dialect="postgresql")
    assert len(statements) == 18
    functions = [
        statement for statement in statements if statement.startswith("CREATE OR REPLACE FUNCTION")
    ]
    assert len(functions) == 2
    assert all(
        statement.count("$d1$") == 2 and statement.endswith("$d1$;") for statement in functions
    )
    for statement in statements[:5]:
        name, temporary = migrate._d1_pg_temporary_table_ddl(statement)
        assert temporary.startswith("CREATE TEMP TABLE __d1_expected_" + name)
        assert temporary.endswith("ON COMMIT DROP;")
        assert "REFERENCES project_todos(" not in temporary
        assert "REFERENCES project_spaces(" not in temporary
        original_body = statement[statement.index("(") :].rstrip(";")
        temporary_body = temporary[temporary.index("(") :].removesuffix(" ON COMMIT DROP;")
        temporary_body = temporary_body.replace("REFERENCES __d1_expected_", "REFERENCES ")
        assert temporary_body == original_body
    assert "9007199254740991" in statements[0]
    assert "9223372036854775807" in statements[1]


def test_users_delete_bumps_soft_deleted_survivor_and_fk_refusal_rolls_back(case):
    pool, users, projects, todos, owner, member, pid, row = case
    todos.delete(project_id=pid, todo_id=row.todo_id, actor_user_id=owner, expected_version=1)
    users.delete(member)
    with pool.connect() as conn:
        raw = conn.execute(
            "SELECT assignee_user_id,version,updated_at FROM project_todos WHERE todo_id=?",
            (row.todo_id,),
        ).fetchone()
        assert raw["assignee_user_id"] is None and raw["version"] == 2
        assert (
            conn.execute(
                "SELECT revision FROM project_todo_display_state WHERE todo_id=?", (row.todo_id,)
            ).fetchone()[0]
            == 3
        )
    other = users.create(username="other", password_hash="h", role="user")
    projects.add_member(pid, other, role="admin")
    created = todos.create(
        project_id=pid, creator_user_id=other, title="owner assigned", assignee_user_id=owner
    )
    assert created.outcome == "created" and created.row is not None
    surviving = created.row
    with pytest.raises(sqlite3.IntegrityError):
        users.delete(owner)
    fresh = todos.get(pid, surviving.todo_id)
    assert fresh.assignee_user_id == owner and fresh.display_revision == 1 and fresh.version == 1
    assert users.get(owner) is not None


@pytest.mark.parametrize(
    "changed",
    [
        {"provolatile": "s"},
        {"provolatile": "i"},
        {"result_type": "void"},
        {"lanname": "sql"},
        {"prosecdef": True},
        {"proconfig": ["search_path=public"]},
        {"prosrc": "BEGIN RETURN NULL; END"},
        {},
    ],
)
def test_pg_existing_function_requires_exact_canonical_attributes(changed):
    """Static adapter test; this does not execute a PostgreSQL function."""
    path = Path(migrate.__file__).parent / "migrations/037_project_todo_d1_foundation.pg.sql"
    sql = path.read_text(encoding="utf-8")
    functions = {
        statement.split()[4].split("(")[0] + "()": statement.split("$d1$")[1].strip()
        for statement in migrate._d1_statements(sql, dialect="postgresql")
        if statement.startswith("CREATE OR REPLACE FUNCTION")
    }

    class CatalogConnection:
        row = None

        def execute(self, statement, params=()):
            if statement == "SELECT version FROM _schema_version":
                self.row = (36,)
            elif "FROM pg_proc p" in statement:
                self.row = {
                    "prosrc": functions[params[0]],
                    "result_type": "trigger",
                    "prosecdef": False,
                    "proconfig": None,
                    "lanname": "plpgsql",
                    "provolatile": "v",
                } | changed
            elif "FROM pg_trigger" in statement:
                self.row = None
            elif statement.startswith("CREATE TEMP TABLE"):
                raise ValueError("function definitions accepted")
            return self

        def fetchone(self):
            return self.row

    if changed:
        with pytest.raises(RuntimeError, match="D1 PostgreSQL function differs"):
            migrate._apply_project_todo_d1_v37(CatalogConnection(), sql, dialect="postgresql")
    else:
        with pytest.raises(ValueError, match="function definitions accepted"):
            migrate._apply_project_todo_d1_v37(CatalogConnection(), sql, dialect="postgresql")
