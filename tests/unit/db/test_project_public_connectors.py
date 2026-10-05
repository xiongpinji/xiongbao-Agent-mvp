from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from dataclasses import fields
from pathlib import Path
from typing import Any

import pytest

from octop.infra.db.migrate import run_migrations
from octop.infra.db.pool import SqlitePool


def test_repo_bundle_wires_storage(tmp_path: Path) -> None:
    from octop.infra.db.repos.project_public_connectors import ProjectPublicConnectorRepo
    from octop.infra.db.services import RepoBundle

    pool = SqlitePool(tmp_path / "public.db")
    try:
        run_migrations(pool)
        assert isinstance(
            RepoBundle.from_pool(pool).project_public_connector_repo, ProjectPublicConnectorRepo
        )
    finally:
        pool.close()


def test_old_bundle_positional_and_keyword_constructors(tmp_path: Path) -> None:
    from octop.infra.db.services import RepoBundle

    pool = SqlitePool(tmp_path / "bundle.db")
    try:
        bundle = RepoBundle.from_pool(pool)
        old = {field.name: getattr(bundle, field.name) for field in fields(bundle) if field.init}
        assert RepoBundle(**old).project_public_connector_repo._db is pool
        assert RepoBundle(*old.values()).project_public_connector_repo._db is pool
    finally:
        pool.close()


def test_two_sqlite_connections_same_cas_one_commit(tmp_path: Path) -> None:
    from octop.infra.db.repos.project_public_connectors import (
        ProjectPublicConnectorRepo,
        PublicConnectorFailure,
    )
    from octop.infra.db.repos.projects import ProjectRepo
    from octop.infra.db.repos.users import UserRepo
    from octop.infra.projects.connectors import ProjectPublicConnectorService

    first = SqlitePool(tmp_path / "race.db")
    second = SqlitePool(tmp_path / "race.db")
    try:
        run_migrations(first)
        actor = UserRepo(first).create(username="owner", password_hash="h", role="user")
        pid = ProjectRepo(first).create_with_owner(creator_user_id=actor, name="CAS").project_id

        def save(pool: SqlitePool) -> str:
            service = ProjectPublicConnectorService(ProjectPublicConnectorRepo(pool))
            try:
                service.create(
                    pid,
                    actor,
                    expected_project_revision=1,
                    display_name="safe",
                    description="",
                    credential={
                        "endpoint": "https://synthetic.invalid/mcp",
                        "bearer_token": "synthetic-only",
                    },
                )
                return "saved"
            except PublicConnectorFailure as exc:
                return exc.reason

        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(save, [first, second]))
        assert sorted(results) == ["saved", "stale_revision"]
        with first.connect() as conn:
            assert conn.execute("SELECT COUNT(*) FROM project_public_connectors").fetchone()[0] == 1
            assert (
                conn.execute("SELECT COUNT(*) FROM project_public_connector_ids").fetchone()[0] == 1
            )
            assert (
                conn.execute(
                    "SELECT COUNT(*) FROM project_events WHERE event_type='public_connector_created'"
                ).fetchone()[0]
                == 1
            )
    finally:
        second.close()
        first.close()


def test_rollback_failure_keeps_primary_and_unknown(tmp_path: Path) -> None:
    from octop.infra.db.repos.project_public_connectors import ProjectPublicConnectorRepo

    pool = SqlitePool(tmp_path / "rollback.db")

    class Connection:
        def __init__(self, actual: Any) -> None:
            self.actual = actual

        @property
        def in_transaction(self) -> bool:
            return bool(self.actual.in_transaction)

        def execute(self, sql: str) -> Any:
            if sql == "ROLLBACK":
                raise OSError("synthetic cleanup")
            return self.actual.execute(sql)

    class WrappedPool:
        dialect = "sqlite"

        @contextmanager
        def connect(self) -> Iterator[Any]:
            with pool.connect() as conn:
                yield Connection(conn)

        def transaction(self) -> Any:
            return pool.transaction()

        def close(self) -> None:
            pass

    primary = KeyboardInterrupt("synthetic primary")
    try:
        repo = ProjectPublicConnectorRepo(WrappedPool())
        with pytest.raises(KeyboardInterrupt) as exc, repo._transaction():
            raise primary
        assert exc.value is primary
        assert primary.__notes__ == ["public_connector_cleanup_UNKNOWN:OSError"]
        with pool.connect() as conn:
            assert conn.in_transaction
            conn.rollback()  # owned test recovery, not an adapter success claim
    finally:
        pool.close()
