"""PG binding observer for existing HTTP tests; no repository or HTTP mocks."""

from __future__ import annotations

import ast
import json
import logging
import os
import sys
import time
from pathlib import Path
from uuid import uuid4

import psycopg
import pytest
import pytest_asyncio
from psycopg.conninfo import conninfo_to_dict
from psycopg.sql import SQL, Identifier

from octop.infra.db.pool import PostgresPool, SqlitePool
from octop.infra.server import OctopServer

_CONTEXT = json.loads(Path(os.environ["XIONGBAO_PS04B_PG_CONTEXT"]).read_text(encoding="utf-8"))
_REPORT_FILE = Path(os.environ["XIONGBAO_PS04B_PG_REPORT"])
assert _CONTEXT["state"] == "READY"
assert _CONTEXT["port"] != 5432 and _CONTEXT["user"].startswith("ps04b_qa_")
assert _CONTEXT["cluster"].startswith("/tmp/xiongbao-ps04b-http-")
_RESULT: dict = {"tests": [], "reports": [], "summary": {}, "fixture_error": None}
_REAL_PG_INIT = PostgresPool.__init__
_REAL_PG_CLOSE = PostgresPool.close
_REAL_START = OctopServer.start
_REAL_STOP = OctopServer.stop
_REAL_SQLITE_INIT = SqlitePool.__init__
_DB_ENV_BASELINE = {
    key: value for key, value in os.environ.items() if key.startswith("OCTOP_DATABASE_")
}
_PATH_BASELINE = os.environ.get("PATH")
_REPO = Path(os.environ["XIONGBAO_PS04B_REPO"]).resolve()
_PHASE = os.environ["XIONGBAO_PS04B_PHASE"]
_DIRECT_SERVICE = {
    "test_image_only_comment_service_persists_private_bytes_and_rechecks_access",
    "test_image_quota_rejection_does_not_leave_bytes_comment_or_charge",
    "test_image_event_failure_rolls_back_database_and_published_object",
    "test_image_read_authorization_serializes_with_member_revocation",
}
_FAULT_CASES = {
    "test_image_quota_rejection_does_not_leave_bytes_comment_or_charge",
    "test_image_event_failure_rolls_back_database_and_published_object",
    "test_comment_event_failure_rolls_back_comment",
}


def save() -> None:
    _REPORT_FILE.parent.mkdir(parents=True, exist_ok=True)
    pending = _REPORT_FILE.with_suffix(".tmp")
    pending.write_text(json.dumps(_RESULT, indent=2) + "\n", encoding="utf-8")
    pending.replace(_REPORT_FILE)


def identify(conn: object, database: str) -> dict:
    row = conn.execute(
        "SELECT current_database(), current_user, inet_server_port(), pg_backend_pid(), host(inet_server_addr())"
    ).fetchone()
    directory = str(conn.execute("SHOW data_directory").fetchone()[0])
    assert (row[0], row[1], row[2]) == (database, _CONTEXT["user"], _CONTEXT["port"])
    assert directory == _CONTEXT["cluster"]
    assert row[4] == "127.0.0.1"
    return {
        "database": row[0],
        "user": row[1],
        "port": row[2],
        "backend_pid": row[3],
        "data_directory": directory,
        "server_address": row[4],
    }


@pytest_asyncio.fixture(autouse=True)
async def ps04b_pg_binding(
    request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch, tmp_octop_home: Path
) -> object:
    assert request.node.nodeid.startswith(
        (
            "tests/integration/test_project_todos_api.py::",
            "tests/integration/test_project_todo_comments_api.py::",
        )
    )
    database = _CONTEXT["database_prefix"] + uuid4().hex[:12]
    record: dict = {
        "nodeid": request.node.nodeid,
        "database": database,
        "pools": [],
        "servers": [],
        "cleanup_error": None,
    }
    _RESULT["tests"].append(record)
    logger_snapshots = [
        (logger, list(logger.handlers), logger.level, logger.propagate)
        for logger in (
            logging.getLogger(),
            logging.getLogger("uvicorn"),
            logging.getLogger("uvicorn.error"),
            logging.getLogger("uvicorn.access"),
            logging.getLogger("httpx"),
            logging.getLogger("httpcore"),
        )
    ]
    with psycopg.connect(
        host="127.0.0.1",
        port=_CONTEXT["port"],
        user=_CONTEXT["user"],
        dbname="postgres",
        connect_timeout=3,
        autocommit=True,
    ) as admin:
        record["admin_identity"] = identify(admin, "postgres")
        admin.execute(SQL("CREATE DATABASE {}").format(Identifier(database)))
    for key in tuple(os.environ):
        if key.startswith("OCTOP_DATABASE_"):
            monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("PATH", os.environ.get("PATH", ""))
    monkeypatch.setenv("OCTOP_HOME", str(tmp_octop_home))
    for key, value in {
        "OCTOP_DATABASE_DRIVER": "postgresql",
        "OCTOP_DATABASE_HOST": "127.0.0.1",
        "OCTOP_DATABASE_PORT": str(_CONTEXT["port"]),
        "OCTOP_DATABASE_NAME": database,
        "OCTOP_DATABASE_USER": _CONTEXT["user"],
    }.items():
        monkeypatch.setenv(key, value)
    live_pools: list[tuple[PostgresPool, dict]] = []
    server_holders: list[tuple[OctopServer, dict]] = []

    def pg_init(self: PostgresPool, conninfo: str, *args: object, **kwargs: object) -> None:
        config = conninfo_to_dict(conninfo)
        assert config["host"] == "127.0.0.1" and int(config["port"]) == _CONTEXT["port"]
        assert config["dbname"] == database and config["user"] == _CONTEXT["user"]
        assert not config.get("password")
        _REAL_PG_INIT(self, conninfo, *args, **kwargs)
        pool_record: dict = {"closes": 0}
        record["pools"].append(pool_record)
        live_pools.append((self, pool_record))
        with self.connect() as conn:
            pool_record["identity"] = identify(conn, database)

    def pg_close(self: PostgresPool) -> None:
        matches = [entry for pool, entry in live_pools if pool is self]
        assert len(matches) == 1
        _REAL_PG_CLOSE(self)
        matches[0]["closes"] += 1
        matches[0]["underlying_closed"] = self._pool.closed
        assert self._pool.closed

    def sqlite_init(self: SqlitePool, *args: object, **kwargs: object) -> None:
        raise AssertionError("Control-plane SQLite pool attempted in the PG HTTP matrix")

    async def start(self: OctopServer) -> None:
        assert self.paths.root.resolve() == tmp_octop_home.resolve()
        server_record: dict = {
            "object_id": id(self),
            "start_complete": False,
            "stops": 0,
            "stop_complete": False,
        }
        record["servers"].append(server_record)
        server_holders.append((self, server_record))
        await _REAL_START(self)
        assert self.database_bound and self.services is not None
        assert self.services.db.dialect == "postgresql"
        with self.services.db.connect() as conn:
            identity = identify(conn, database)
            version = conn.execute("SELECT version FROM _schema_version").fetchone()[0]
        server_record.update(
            {"identity": identity, "schema_version": version, "start_complete": True}
        )

    async def stop(self: OctopServer) -> None:
        found = [item for item in record["servers"] if item["object_id"] == id(self)]
        assert len(found) == 1
        found[0]["stops"] += 1
        await _REAL_STOP(self)
        found[0]["stop_complete"] = (
            self.services is None and self.app_runtime is None and not self._started
        )
        assert found[0]["stop_complete"]

    monkeypatch.setattr(PostgresPool, "__init__", pg_init)
    monkeypatch.setattr(PostgresPool, "close", pg_close)
    monkeypatch.setattr(SqlitePool, "__init__", sqlite_init)
    monkeypatch.setattr(OctopServer, "start", start)
    monkeypatch.setattr(OctopServer, "stop", stop)
    try:
        yield
    finally:
        try:
            for server, item in server_holders:
                if not item["stops"]:
                    record["fallback_stop_required"] = True
                    if server._started:
                        await stop(server)
                    else:
                        record["inconclusive_half_start"] = True
                        raise RuntimeError("Partial server start; exit this disposable test child")
            assert record["servers"] and all(
                item["stops"] == 1 and item["stop_complete"] for item in record["servers"]
            )
            assert live_pools and all(item["closes"] == 1 for _pool, item in live_pools)
            with psycopg.connect(
                host="127.0.0.1",
                port=_CONTEXT["port"],
                user=_CONTEXT["user"],
                dbname="postgres",
                connect_timeout=3,
                autocommit=True,
            ) as admin:
                identify(admin, "postgres")
                deadline = time.monotonic() + 3
                while True:
                    remaining = admin.execute(
                        "SELECT count(*) FROM pg_stat_activity WHERE datname = %s", (database,)
                    ).fetchone()[0]
                    if remaining == 0 or time.monotonic() >= deadline:
                        break
                    time.sleep(0.05)
                record["remaining_backend_connections"] = remaining
                assert remaining == 0
        except BaseException as exc:
            record["cleanup_error"] = repr(exc)
            _RESULT["fixture_error"] = repr(exc)
            for pool, item in live_pools:
                if not item["closes"]:
                    pg_close(pool)
            raise
        finally:
            closed_handlers = set()
            for logger, handlers, level, propagate in logger_snapshots:
                for handler in list(logger.handlers):
                    if handler not in handlers and handler not in closed_handlers:
                        handler.close()
                        closed_handlers.add(handler)
                logger.handlers[:] = handlers
                logger.setLevel(level)
                logger.propagate = propagate
            record["new_logger_handlers_closed"] = len(closed_handlers)
            record["logger_snapshots_restored"] = all(
                logger.handlers == handlers
                and logger.level == level
                and logger.propagate == propagate
                for logger, handlers, level, propagate in logger_snapshots
            )
            assert record["logger_snapshots_restored"]
            save()


def pytest_runtest_logreport(report: pytest.TestReport) -> None:
    item = {"nodeid": report.nodeid, "when": report.when, "outcome": report.outcome}
    if report.failed and getattr(report.longrepr, "reprcrash", None):
        item["failure"] = report.longrepr.reprcrash.message
        item["failure_location"] = {
            "path": report.longrepr.reprcrash.path,
            "line": report.longrepr.reprcrash.lineno,
        }
    _RESULT["reports"].append(item)
    save()


def pytest_collection_finish(session: pytest.Session) -> None:
    expected = set()
    for filename in ("test_project_todos_api.py", "test_project_todo_comments_api.py"):
        relative = "tests/integration/" + filename
        tree = ast.parse((_REPO / relative).read_text(encoding="utf-8"))
        for node in tree.body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name.startswith(
                "test_"
            ):
                expected.add(relative + "::" + node.name)
    if _PHASE == "red-pg":
        expected = {
            "tests/integration/test_project_todo_comments_api.py::test_image_read_authorization_serializes_with_member_revocation"
        }
    collected = {item.nodeid for item in session.items}
    _RESULT["collected_nodeids"] = sorted(collected)
    _RESULT["expected_collection_matched"] = expected == collected
    save()
    assert expected == collected


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_teardown(item: pytest.Item, nextitem: pytest.Item | None) -> object:
    yield
    current_env = {
        key: value for key, value in os.environ.items() if key.startswith("OCTOP_DATABASE_")
    }
    assert current_env == _DB_ENV_BASELINE
    assert os.environ.get("PATH") == _PATH_BASELINE
    assert PostgresPool.__init__ is _REAL_PG_INIT and PostgresPool.close is _REAL_PG_CLOSE
    assert SqlitePool.__init__ is _REAL_SQLITE_INIT
    assert OctopServer.start is _REAL_START and OctopServer.stop is _REAL_STOP
    _RESULT["tests"][-1]["patches_and_database_env_restored"] = True
    save()


def pytest_sessionfinish(session: pytest.Session, exitstatus: int) -> None:
    calls = [report for report in _RESULT["reports"] if report["when"] == "call"]
    _RESULT["summary"] = {
        "exitstatus": int(exitstatus),
        "collected": session.testscollected,
        "passed": sum(report["outcome"] == "passed" for report in calls),
        "failed": sum(report["outcome"] == "failed" for report in calls),
        "skipped": sum(report["outcome"] == "skipped" for report in calls),
        "stage_errors": sum(
            report["outcome"] == "failed" and report["when"] != "call"
            for report in _RESULT["reports"]
        ),
        "fault_injection_nodeids": sorted(
            report["nodeid"] for report in calls if report["nodeid"].split("::")[-1] in _FAULT_CASES
        ),
    }
    for category, is_service in (("asgi_http", False), ("direct_service", True)):
        selected = [
            report
            for report in calls
            if (report["nodeid"].split("::")[-1] in _DIRECT_SERVICE) == is_service
        ]
        _RESULT["summary"][category] = {
            "nodeids": sorted(report["nodeid"] for report in selected),
            "passed": sum(report["outcome"] == "passed" for report in selected),
            "failed": sum(report["outcome"] == "failed" for report in selected),
        }
    _RESULT["child_runtime"] = {
        "python": sys.version.split()[0],
        "executable": sys.executable,
        "imported_octop": sys.modules["octop"].__file__,
    }
    imports = {
        name: str(Path(module.__file__).resolve())
        for name, module in tuple(sys.modules.items())
        if name.startswith("octop") and getattr(module, "__file__", None)
    }
    _RESULT["imported_project_modules"] = imports
    assert all(Path(path).is_relative_to(_REPO / "src" / "octop") for path in imports.values())
    _RESULT["imports_from_requested_source"] = True
    save()
