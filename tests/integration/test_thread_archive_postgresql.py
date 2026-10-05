"""Real PostgreSQL contract, gated by the existing dedicated-test DSN convention.

Each test creates and drops only its own UUID schema. The archive author did
not run this file; a matching separately authorized PostgreSQL run is required.
"""

import os
import uuid

import pytest

from octop.infra.db import migrate
from octop.infra.db.pool import PostgresPool
from octop.infra.db.repos.agents import AgentRepo
from octop.infra.db.repos.sessions import SessionRepo
from octop.infra.db.repos.threads import ThreadRepo
from octop.infra.db.repos.users import UserRepo
from tests.support.postgresql import requires_postgresql


@requires_postgresql
@pytest.mark.postgresql
@pytest.mark.parametrize("prior", [None, 36, 37])
def test_real_pg_fresh_upgrade_reentry_constraint_eligibility_and_drift(prior, monkeypatch):
    from psycopg.conninfo import make_conninfo

    schema = "archive_owned_" + uuid.uuid4().hex
    control = PostgresPool(os.environ["OCTOP_TEST_DATABASE_URL"], min_size=1, max_size=1)
    pool = None
    try:
        with control.transaction() as conn:
            conn.execute(f'CREATE SCHEMA "{schema}"')
        pool = PostgresPool(
            make_conninfo(
                os.environ["OCTOP_TEST_DATABASE_URL"], options=f"-c search_path={schema}"
            ),
            min_size=1,
            max_size=1,
        )
        discover = migrate._discover
        if prior is not None:
            with monkeypatch.context() as patch:
                patch.setattr(
                    migrate,
                    "_discover",
                    lambda dialect: [(v, p) for v, p in discover(dialect) if v <= prior],
                )
                migrate.run_migrations(pool)
        else:
            migrate.run_migrations(pool)
        uid = UserRepo(pool).create(username="owned", password_hash="synthetic", role="user")
        AgentRepo(pool).create(agent_id="a", user_id=uid, name="Stopped")
        repo = ThreadRepo(pool)
        repo.insert(
            thread_id="t",
            agent_id="a",
            user_id=uid,
            channel_type="dashboard",
            session_key=f"a:dashboard:{uid}:dm",
            title="Straße %_",
        )
        SessionRepo(pool).bind_dashboard_owner(
            session_key=f"a:dashboard:{uid}:dm", agent_id="a", user_id=uid, thread_id="t"
        )
        migrate.run_migrations(pool)
        assert repo.get("t").archived_at is None
        assert (
            repo.set_archive_owned(
                thread_id="t", user_id=uid, actor_is_admin=False, archived=True, now=100
            ).archived_at
            == 100
        )
        assert repo.list_by_agent_user(agent_id="a", user_id=uid) == []
        assert (
            repo.list_archived_by_user(user_id=uid, actor_is_admin=False, q="STRASSE %_")[
                0
            ].thread_id
            == "t"
        )
        assert (
            repo.set_archive_owned(
                thread_id="t", user_id=uid + 1, actor_is_admin=True, archived=False, now=200
            )
            is None
        )
        for bad in (0, -1, 9007199254740992):
            with pytest.raises(Exception) as error, pool.transaction() as conn:
                conn.execute("UPDATE threads SET archived_at=? WHERE thread_id='t'", (bad,))
            assert getattr(error.value, "sqlstate", None) == "23514"
        migrate.run_migrations(pool)
        assert repo.get("t").archived_at == 100
        assert SessionRepo(pool).get(f"a:dashboard:{uid}:dm").thread_id == "t"
        with pool.transaction() as conn:
            conn.execute("DROP INDEX idx_threads_user_archive")
        with pytest.raises(RuntimeError, match="archive"):
            migrate.run_migrations(pool)
        with pool.connect() as conn:
            assert (
                conn.execute("SELECT to_regclass('idx_threads_user_archive')").fetchone()[0] is None
            )
    finally:
        if pool is not None:
            pool.close()
        try:
            with control.transaction() as conn:
                conn.execute(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')
        finally:
            control.close()
