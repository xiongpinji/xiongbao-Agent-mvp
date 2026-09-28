"""Own private TCP API, Vite and headless Chrome children for one PG journey."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import socket
import subprocess
import sys
import time
from pathlib import Path
from urllib.request import urlopen

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from owned_process import OwnedJob  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", type=Path, required=True)
    parser.add_argument("--attempt", choices=("1", "2", "3"), default="1")
    args = parser.parse_args()
    repo = args.repo.resolve()
    qa = Path(__file__).resolve().parents[1]
    assert qa.is_relative_to(repo.parent / "output" / "qa")
    output = qa / ("browser-pg" if args.attempt == "1" else "browser-pg-attempt-" + args.attempt)
    assert not output.exists(), "Run once; preserve original evidence"
    output.mkdir()
    isolated = output / "runner-home"
    isolated.mkdir()
    environment = dict(os.environ)
    for key in tuple(environment):
        if key.startswith(("OCTOP_DATABASE_", "PG", "OCTOP_CAPTCHA_", "XIONGBAO_PS04B_")):
            environment.pop(key, None)
    environment.update(
        HOME=str(isolated),
        USERPROFILE=str(isolated),
        OCTOP_HOME=str(isolated / ".octop"),
        PYTHONIOENCODING="utf-8",
        PYTHONPATH=os.pathsep.join((str(repo / "src"), str(repo))),
        BASE_URL="",
    )
    node = Path("C:/Program Files/nodejs/node.exe")
    uv = Path("C:/Users/canqu/.local/bin/uv.exe")
    flow = qa / "browser_flow.cjs"
    assert node.is_file() and uv.is_file() and flow.is_file()
    sockets = [socket.socket(), socket.socket()]
    try:
        for pick in sockets:
            pick.bind(("127.0.0.1", 0))
        api_port, web_port = [pick.getsockname()[1] for pick in sockets]
    finally:
        for pick in sockets:
            pick.close()
    assert api_port != web_port and 5432 not in (api_port, web_port)
    environment.update(
        VITE_API_PORT=str(api_port),
        VITE_DEV_PORT=str(web_port),
        VITE_HMR_CLIENT_PORT=str(web_port),
        PS04B_API=f"http://127.0.0.1:{api_port}",
        PS04B_WEB=f"http://127.0.0.1:{web_port}",
        PS04B_BROWSER_OUTPUT=str(output / "journey"),
        PS04B_REPO=str(repo),
    )

    def git(*argv: str) -> str:
        return subprocess.check_output(["git", "-C", str(repo), *argv], text=True).strip()

    def binding() -> dict:
        paths = sorted((repo / "src" / "octop").rglob("*.py"))
        paths += sorted((repo / "src" / "octop").rglob("*.sql"))
        paths += sorted((repo / "tests" / "support").rglob("*.py"))
        paths += [p for p in sorted((repo / "dashboard" / "src").rglob("*")) if p.is_file()]
        paths += [p for p in sorted((repo / "dashboard" / "public").rglob("*")) if p.is_file()]
        paths += [
            repo / "dashboard" / p
            for p in ("vite.config.ts", "package.json", "package-lock.json", "index.html")
        ]
        paths += [
            qa / "owned_process.py",
            flow,
            Path(__file__).resolve(),
            qa / "tcp/browser_server.py",
        ]
        return {
            str(path): {
                "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                "bytes": path.stat().st_size,
            }
            for path in paths
        }

    before = binding()
    snapshots = output / "scripts"
    snapshots.mkdir()
    for script in (
        flow,
        Path(__file__).resolve(),
        qa / "tcp/browser_server.py",
        qa / "owned_process.py",
    ):
        (snapshots / script.name).write_bytes(script.read_bytes())
    head, changed, untracked = (
        git("rev-parse", "HEAD"),
        git("diff", "--name-only"),
        git("ls-files", "--others", "--exclude-standard"),
    )
    assert head == "e72f72758a4f91c00738127e1a46850c9fd54174"
    assert changed == "tests/integration/test_project_todo_comments_api.py"
    assert not git("diff", "--cached", "--name-only")
    report: dict = {
        "state": "STARTING",
        "source_head": head,
        "binding_before": before,
        "api_port": api_port,
        "web_port": web_port,
        "children": {},
        "error": None,
        "cleanup_errors": [],
    }
    children: dict = {}

    def save() -> None:
        (output / "command-result.json").write_text(
            json.dumps(report, indent=2) + "\n", encoding="utf-8"
        )

    def start(name: str, command: list[str], cwd: Path) -> subprocess.Popen:
        job = OwnedJob()
        stdout = (output / (name + "-stdout.log")).open("w", encoding="utf-8")
        stderr = (output / (name + "-stderr.log")).open("w", encoding="utf-8")
        children[name] = {"job": job, "process": None, "streams": (stdout, stderr)}
        child = subprocess.Popen(
            command,
            cwd=cwd,
            env=environment,
            stdout=stdout,
            stderr=stderr,
            creationflags=0x00000004 | subprocess.CREATE_NEW_PROCESS_GROUP,
        )
        children[name]["process"] = child
        report["children"][name] = {"pid": child.pid, "command": command}
        job.attach_and_resume(child)
        save()
        return child

    try:
        start(
            "api",
            [
                str(uv),
                "run",
                "--no-sync",
                "python",
                str(qa / "tcp/browser_server.py"),
                "--repo",
                str(repo),
                "--output",
                str(output),
                "--port",
                str(api_port),
            ],
            repo,
        )
        start(
            "web",
            [
                str(node),
                str(repo / "dashboard/node_modules/vite/bin/vite.js"),
                "--host",
                "127.0.0.1",
                "--port",
                str(web_port),
                "--strictPort",
            ],
            repo / "dashboard",
        )
        deadline = time.monotonic() + 90
        while True:
            assert all(child["process"].poll() is None for child in children.values()), (
                "QA service exited before ready"
            )
            ready = output / "server-result.json"
            if ready.exists():
                server = json.loads(ready.read_text(encoding="utf-8"))
                if server["state"] == "READY":
                    try:
                        with urlopen(
                            environment["PS04B_WEB"] + "/api/auth/captcha", timeout=2
                        ) as response:
                            captcha = json.load(response)
                        with urlopen(environment["PS04B_WEB"], timeout=2) as response:
                            html = response.read().decode("utf-8")
                        assert captcha["provider"] == "none" and "/@vite/client" in html
                        report["same_origin_api_proxy_verified"] = True
                        break
                    except (OSError, ValueError):
                        pass
            assert time.monotonic() < deadline, "QA startup exceeded 90 seconds"
            time.sleep(0.1)
        flow_child = start("browser", [str(node), str(flow)], repo)
        try:
            flow_child.wait(timeout=480)
        except subprocess.TimeoutExpired:
            report["browser_timeout"] = True
            children["browser"]["job"].terminate()
            flow_child.wait(timeout=15)
            raise
        report["browser_returncode"] = flow_child.returncode
        assert flow_child.returncode == 0
        journey_file = output / "journey/result.json"
        assert journey_file.is_file(), "Browser must persist its own result"
        journey = json.loads(journey_file.read_text(encoding="utf-8"))
        assert journey["result"] == "PASS", "Browser result did not pass"
        report["journey_result"] = journey
        assert before == binding() and head == git("rev-parse", "HEAD")
        assert changed == git("diff", "--name-only") and untracked == git(
            "ls-files", "--others", "--exclude-standard"
        )
        assert not git("diff", "--cached", "--name-only")
        report["source_unchanged"] = True
        report["state"] = "JOURNEY_PASSED"
    except BaseException as exc:
        report["state"] = "FAILED_OR_INCONCLUSIVE"
        report["error"] = repr(exc)
    finally:
        report["source_unchanged"] = False
        (output / "stop-server").write_text("Owned QA run completed\n", encoding="utf-8")
        for name in ("browser", "api", "web"):
            if name not in children:
                continue
            item, outcome = children[name], report["children"].get(name, {})
            child, job = item["process"], item["job"]
            try:
                if child is not None:
                    if name == "api" and child.poll() is None:
                        child.wait(timeout=20)
                    deadline = time.monotonic() + 3
                    while name != "web" and job.counts()["active"] and time.monotonic() < deadline:
                        time.sleep(0.05)
                    if job.counts()["active"]:
                        if name != "web":
                            report["cleanup_errors"].append(name + " required forced termination")
                        job.terminate()
                    elif child.poll() is None:
                        child.terminate()
                    child.wait(timeout=15)
                    outcome["returncode"] = child.returncode
                    outcome["job_after"] = job.counts()
                    assert outcome["job_after"]["active"] == 0
            except BaseException as exc:
                report["cleanup_errors"].append(name + ": " + repr(exc))
            finally:
                job.close()
                outcome["job_closed"] = True
                for stream in item["streams"]:
                    stream.close()
        try:
            server = json.loads((output / "server-result.json").read_text(encoding="utf-8"))
            assert (
                server["state"] == "STOPPED"
                and server["start_complete"]
                and server["stop_complete"]
            )
            assert server["canonical_imports_only"] and server["remaining_backend_connections"] == 0
            assert server["error"] is None and server["cleanup_error"] is None
            assert report["children"]["api"]["returncode"] == 0
            report["server_normal_stop_verified"] = True
            for port in (api_port, web_port):
                with socket.socket() as probe:
                    probe.settimeout(1)
                    assert probe.connect_ex(("127.0.0.1", port)) != 0
            report["own_ports_closed"] = True
            report["source_unchanged"] = (
                before == binding()
                and head == git("rev-parse", "HEAD")
                and changed == git("diff", "--name-only")
                and untracked == git("ls-files", "--others", "--exclude-standard")
                and not git("diff", "--cached", "--name-only")
            )
            assert report["source_unchanged"], "Source drift during QA shutdown"
        except BaseException as exc:
            report["cleanup_errors"].append("Final gate: " + repr(exc))
        if report["state"] == "JOURNEY_PASSED" and not report["cleanup_errors"]:
            report["state"] = "PASS"
        elif report["cleanup_errors"]:
            report["state"] = "FAILED_OR_INCONCLUSIVE"
        save()
    print(
        json.dumps(
            {
                key: report.get(key)
                for key in ("state", "browser_returncode", "error", "cleanup_errors")
            }
        )
    )
    return 0 if report["state"] == "PASS" else 2


if __name__ == "__main__":
    raise SystemExit(main())
