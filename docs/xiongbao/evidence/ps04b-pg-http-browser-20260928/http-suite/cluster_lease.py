"""Own one fresh, loopback-only PG cluster until a stop file or bounded deadline."""

from __future__ import annotations

import argparse
import json
import os
import signal
import socket
import subprocess
import tempfile
import time
import traceback
from pathlib import Path
from uuid import uuid4

import psycopg


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--repo", type=Path, required=True)
    parser.add_argument("--lease-seconds", type=int, default=1800)
    args = parser.parse_args()
    output, repo = args.output.resolve(), args.repo.resolve()
    assert output.is_relative_to(repo.parent / "output" / "qa")
    assert output.name == "ps04b-pg-http-browser-20260928"
    assert os.name == "posix" and 300 <= args.lease_seconds <= 3600
    assert not (output / "cluster-context.json").exists(), "Never resume an old cluster"
    assert not (output / "stop-cluster").exists()
    output.mkdir(parents=True, exist_ok=True)
    base = Path(tempfile.mkdtemp(prefix="xiongbao-ps04b-http-")).resolve()
    assert base.parent == Path("/tmp") and base.name.startswith("xiongbao-ps04b-http-")
    cluster = base / "cluster"
    tools = Path("/usr/lib/postgresql/18/bin")
    assert all((tools / name).is_file() for name in ("initdb", "pg_ctl"))
    for name in tuple(os.environ):
        if name.startswith("PG") or name.startswith("OCTOP_DATABASE_"):
            os.environ.pop(name, None)
    with socket.socket() as pick:
        pick.bind(("127.0.0.1", 0))
        port = pick.getsockname()[1]
    assert port != 5432
    run_id = uuid4().hex[:12]
    pg_user = "ps04b_qa_" + run_id
    report: dict = {
        "state": "INITIALIZING",
        "base": str(base),
        "cluster": str(cluster),
        "port": port,
        "user": pg_user,
        "database_prefix": "ps04b_" + run_id + "_",
        "repo": str(repo),
        "pid": os.getpid(),
        "lease_seconds": args.lease_seconds,
        "cleanup": {},
        "error": None,
    }

    def save() -> None:
        target = output / "cluster-result.json"
        pending = target.with_suffix(".tmp")
        pending.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
        pending.replace(target)

    def pg(tool: str, *argv: object) -> subprocess.CompletedProcess:
        return subprocess.run(
            [str(tools / tool), *map(str, argv)],
            capture_output=True,
            text=True,
            timeout=45,
            check=False,
        )

    def interrupt(_signum: int, _frame: object) -> None:
        raise KeyboardInterrupt("Cluster lease interrupted")

    signal.signal(signal.SIGTERM, interrupt)
    signal.signal(signal.SIGINT, interrupt)
    initialized = False
    successful_stop_request = False
    try:
        initialized = True
        initialized_result = pg(
            "initdb", "-D", cluster, "-A", "trust", "-U", pg_user, "--no-instructions"
        )
        assert initialized_result.returncode == 0, initialized_result.stderr
        started = pg(
            "pg_ctl",
            "-D",
            cluster,
            "-o",
            f"-h 127.0.0.1 -k {base} -p {port}",
            "-l",
            base / "postgres.log",
            "-w",
            "start",
        )
        assert started.returncode == 0, started.stderr
        with psycopg.connect(
            host="127.0.0.1", port=port, user=pg_user, dbname="postgres", connect_timeout=3
        ) as conn:
            identity = conn.execute(
                "SELECT current_database(), current_user, inet_server_port(), pg_backend_pid()"
            ).fetchone()
            data_directory = conn.execute("SHOW data_directory").fetchone()[0]
            version = conn.execute("SHOW server_version").fetchone()[0]
            assert identity[:3] == ("postgres", pg_user, port)
            assert Path(data_directory).resolve() == cluster
        report["identity"] = {
            "database": identity[0],
            "user": identity[1],
            "port": identity[2],
            "backend_pid": identity[3],
            "data_directory": data_directory,
        }
        report["postgresql"] = version
        report["state"] = "READY"
        (output / "cluster-context.json").write_text(
            json.dumps(report, indent=2) + "\n", encoding="utf-8"
        )
        save()
        print(json.dumps({"state": "READY", "port": port, "pid": os.getpid()}), flush=True)
        deadline = time.monotonic() + args.lease_seconds
        while time.monotonic() < deadline:
            if (output / "stop-cluster").is_file():
                successful_stop_request = True
                break
            time.sleep(0.5)
        if not successful_stop_request:
            report["state"] = "LEASE_EXPIRED"
            raise TimeoutError("PG lease elapsed")
    except BaseException:
        report["error"] = traceback.format_exc()
        if report["state"] != "LEASE_EXPIRED":
            report["state"] = "FAILED"
    finally:
        if initialized:
            try:
                before = pg("pg_ctl", "-D", cluster, "status")
                report["cleanup"]["status_before"] = before.returncode
                if before.returncode == 0:
                    stopped = pg("pg_ctl", "-D", cluster, "-m", "fast", "-w", "stop")
                    report["cleanup"]["stop_code"] = stopped.returncode
                    assert stopped.returncode == 0, stopped.stderr
                after = pg("pg_ctl", "-D", cluster, "status")
                report["cleanup"]["status_after"] = after.returncode
                assert after.returncode == 3
                with socket.socket() as check:
                    report["cleanup"]["port_closed"] = check.connect_ex(("127.0.0.1", port)) != 0
                assert report["cleanup"]["port_closed"]
            except BaseException:
                report["cleanup"]["error"] = traceback.format_exc()
        if (
            successful_stop_request
            and report["error"] is None
            and not report["cleanup"].get("error")
        ):
            report["state"] = "STOPPED"
        save()
        print(json.dumps({"state": report["state"], "cleanup": report["cleanup"]}), flush=True)
    return 0 if report["state"] == "STOPPED" else 1


if __name__ == "__main__":
    raise SystemExit(main())
