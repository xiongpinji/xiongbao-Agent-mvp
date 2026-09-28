"""Private TCP app backed by the leased PG cluster; synthetic accounts only."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
from contextlib import ExitStack, redirect_stdout
from io import StringIO
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--port", type=int, required=True)
    args = parser.parse_args()
    repo, output = args.repo.resolve(), args.output.resolve()
    qa = Path(__file__).resolve().parents[1]
    assert output.is_relative_to(qa) and output.name in (
        "browser-pg",
        "browser-pg-attempt-2",
        "browser-pg-attempt-3",
    )
    assert not (output / "server-result.json").exists(), "Run once"
    assert 1024 <= args.port <= 65535 and args.port != 5432
    home = output / "server-home"
    assert not home.exists(), "Fresh app home required"
    home.mkdir(parents=True)
    for key in tuple(os.environ):
        if key.startswith(("OCTOP_DATABASE_", "PG", "OCTOP_CAPTCHA_")):
            os.environ.pop(key, None)
    context = json.loads((qa / "cluster-context.json").read_text(encoding="utf-8"))
    assert context["state"] == "READY" and context["port"] != 5432
    assert context["user"].startswith("ps04b_qa_")
    assert context["cluster"].startswith("/tmp/xiongbao-ps04b-http-")
    database = context["database_prefix"] + "browser_" + uuid4().hex[:8]
    os.environ.update(
        {
            "HOME": str(home),
            "USERPROFILE": str(home),
            "OCTOP_HOME": str(home / ".octop"),
            "OCTOP_DATABASE_DRIVER": "postgresql",
            "OCTOP_DATABASE_HOST": "127.0.0.1",
            "OCTOP_DATABASE_PORT": str(context["port"]),
            "OCTOP_DATABASE_NAME": database,
            "OCTOP_DATABASE_USER": context["user"],
        }
    )
    sys.path[:0] = [str(repo / "src"), str(repo)]
    import httpx
    import psycopg
    import uvicorn
    from psycopg.conninfo import conninfo_to_dict
    from psycopg.sql import SQL, Identifier
    from tests.support.auth import auth_header, bootstrap_admin, create_user
    from tests.support.harness import patch_harness

    import octop
    from octop.api.app import build_app
    from octop.infra.db.pool import PostgresPool, SqlitePool
    from octop.infra.proactive.scheduler import ProactiveCareScheduler
    from octop.infra.server import OctopServer

    imported = Path(octop.__file__).resolve()
    assert imported.is_relative_to(repo / "src" / "octop")
    report: dict = {
        "state": "STARTING",
        "pid": os.getpid(),
        "host": "127.0.0.1",
        "port": args.port,
        "database": database,
        "imported": str(imported),
        "interpreter": sys.executable,
        "fake_harness_manager": True,
        "proactive_disabled_before_start": True,
        "start_complete": False,
        "stop_complete": False,
        "pools": [],
        "error": None,
        "cleanup_error": None,
    }

    def save() -> None:
        target = output / "server-result.json"
        pending = target.with_suffix(".tmp")
        pending.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
        pending.replace(target)

    def identify(conn: object, name: str) -> dict:
        row = conn.execute(
            "SELECT current_database(), current_user, inet_server_port(), "
            "pg_backend_pid(), host(inet_server_addr())"
        ).fetchone()
        directory = str(conn.execute("SHOW data_directory").fetchone()[0])
        assert (row[0], row[1], row[2], row[4]) == (
            name,
            context["user"],
            context["port"],
            "127.0.0.1",
        )
        assert directory == context["cluster"]
        return {
            "database": row[0],
            "user": row[1],
            "port": row[2],
            "backend_pid": row[3],
            "server_address": row[4],
            "data_directory": directory,
        }

    def admin_connection() -> object:
        return psycopg.connect(
            host="127.0.0.1",
            port=context["port"],
            user=context["user"],
            dbname="postgres",
            connect_timeout=3,
            autocommit=True,
        )

    original_init, original_close = PostgresPool.__init__, PostgresPool.close
    pools: list[tuple[PostgresPool, dict]] = []

    def pg_init(self: PostgresPool, conninfo: str, *argv: object, **kwargs: object) -> None:
        config = conninfo_to_dict(conninfo)
        assert config["host"] == "127.0.0.1" and int(config["port"]) == context["port"]
        assert config["dbname"] == database and config["user"] == context["user"]
        assert not config.get("password")
        original_init(self, conninfo, *argv, **kwargs)
        item: dict = {"closes": 0}
        report["pools"].append(item)
        pools.append((self, item))
        with self.connect() as conn:
            item["identity"] = identify(conn, database)

    def pg_close(self: PostgresPool) -> None:
        item = next(item for pool, item in pools if pool is self)
        original_close(self)
        item["closes"] += 1
        item["underlying_closed"] = self._pool.closed
        assert self._pool.closed

    def sqlite_deny(_self: SqlitePool, *_argv: object, **_kwargs: object) -> None:
        raise AssertionError("SQLite control plane is forbidden in this PG browser run")

    async def start_all(_self: ProactiveCareScheduler) -> None:
        return None

    server = None
    watcher = None
    try:
        save()
        with admin_connection() as admin:
            report["admin_identity"] = identify(admin, "postgres")
            admin.execute(SQL("CREATE DATABASE {}").format(Identifier(database)))
        with ExitStack() as stack:
            stack.enter_context(patch.object(Path, "home", return_value=home))
            stack.enter_context(patch.object(PostgresPool, "__init__", pg_init))
            stack.enter_context(patch.object(PostgresPool, "close", pg_close))
            stack.enter_context(patch.object(SqlitePool, "__init__", sqlite_deny))
            stack.enter_context(patch_harness())
            for name in ("ensure_scheduled", "_schedule"):
                stack.enter_context(
                    patch.object(ProactiveCareScheduler, name, lambda _self, _id: None)
                )
            stack.enter_context(patch.object(ProactiveCareScheduler, "start_all", start_all))
            server = OctopServer(home=home / ".octop")
            try:
                with redirect_stdout(StringIO()):
                    await server.start()
                assert server.database_bound and server.services.db.dialect == "postgresql"
                assert server.paths.root.resolve() == (home / ".octop").resolve()
                report["start_complete"] = True
                with server.services.db.connect() as conn:
                    report["identity"] = identify(conn, database)
                    report["schema_version"] = conn.execute(
                        "SELECT version FROM _schema_version"
                    ).fetchone()[0]
                app = build_app(server)
                async with httpx.AsyncClient(
                    transport=httpx.ASGITransport(app=app), base_url="http://testserver"
                ) as client:
                    with redirect_stdout(StringIO()):
                        await bootstrap_admin(client, home / ".octop")
                        admin_auth = await auth_header(client)
                        for username in ("ps04b_owner", "ps04b_member", "ps04b_outsider"):
                            await create_user(client, admin_auth, username=username)
                report["synthetic_accounts_bootstrapped"] = True
                http_server = uvicorn.Server(
                    uvicorn.Config(
                        app,
                        host="127.0.0.1",
                        port=args.port,
                        log_level="warning",
                        access_log=False,
                    )
                )
                started_at = time.monotonic()

                async def watch() -> None:
                    ready = False
                    while not http_server.should_exit:
                        if http_server.started and not ready:
                            report["state"] = "READY"
                            save()
                            print(json.dumps({"state": "TCP_READY", "port": args.port}), flush=True)
                            ready = True
                        if (output / "stop-server").exists():
                            report["stop_requested"] = True
                            http_server.should_exit = True
                        elif time.monotonic() - started_at > 900:
                            report["lease_expired"] = True
                            http_server.should_exit = True
                        await asyncio.sleep(0.1)

                watcher = asyncio.create_task(watch())
                await http_server.serve()
                assert report.get("stop_requested") and not report.get("lease_expired")
                assert report["state"] == "READY"
            except BaseException as exc:
                report["error"] = repr(exc)
                raise
            finally:
                if watcher is not None:
                    watcher.cancel()
                    await asyncio.gather(watcher, return_exceptions=True)
                if server._started:
                    await server.stop()
                    report["stop_complete"] = (
                        server.services is None
                        and server.app_runtime is None
                        and not server._started
                    )
                else:
                    report["half_started"] = True
                for pool, item in pools:
                    if not item["closes"]:
                        report["fallback_pool_close"] = True
                        pg_close(pool)
                if not report["stop_complete"] or report.get("fallback_pool_close"):
                    report["cleanup_error"] = "Application did not complete its normal stop"
                    raise RuntimeError(report["cleanup_error"])
                assert pools and all(item["closes"] == 1 for _pool, item in pools)
        with admin_connection() as admin:
            identify(admin, "postgres")
            deadline = time.monotonic() + 3
            while True:
                count = admin.execute(
                    "SELECT count(*) FROM pg_stat_activity WHERE datname=%s", (database,)
                ).fetchone()[0]
                if count == 0 or time.monotonic() >= deadline:
                    break
                await asyncio.sleep(0.05)
            report["remaining_backend_connections"] = count
            assert count == 0
        report["state"] = "STOPPED"
        return 0
    except BaseException as exc:
        report["state"] = "FAILED_OR_INCONCLUSIVE"
        if report["error"] is None:
            report["error"] = repr(exc)
        elif report["error"] != repr(exc) and report["cleanup_error"] is None:
            report["cleanup_error"] = repr(exc)
        return 2
    finally:
        report["canonical_imports_only"] = all(
            Path(module.__file__).resolve().is_relative_to(repo / "src" / "octop")
            for name, module in tuple(sys.modules.items())
            if name.startswith("octop.") and getattr(module, "__file__", None)
        )
        save()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
