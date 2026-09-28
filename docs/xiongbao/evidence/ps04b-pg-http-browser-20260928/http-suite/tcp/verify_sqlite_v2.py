"""Bounded uv pytest SQLite compatibility run, preserving one-shot evidence."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from owned_process import OwnedJob  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", type=Path, required=True)
    args = parser.parse_args()
    repo = args.repo.resolve()
    qa = Path(__file__).resolve().parents[1]
    assert qa.is_relative_to(repo.parent / "output" / "qa")
    output = qa / "sqlite-compat-v2"
    assert not output.exists(), "Run once; preserve original results"
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
        PYTHONPATH=os.pathsep.join((str(repo / "src"), str(repo))),
        PYTHONIOENCODING="utf-8",
    )
    targets = [
        "tests/integration/test_project_todos_api.py",
        "tests/integration/test_project_todo_comments_api.py",
    ]

    def source_binding() -> dict:
        paths = sorted((repo / "src" / "octop").rglob("*.py"))
        paths += sorted((repo / "src" / "octop").rglob("*.sql"))
        paths += sorted((repo / "tests" / "support").rglob("*.py"))
        paths += [repo / value for value in targets]
        paths += [repo / "tests/conftest.py", repo / "tests/integration/conftest.py"]
        paths += [Path(__file__).resolve(), qa / "owned_process.py"]
        return {str(path): hashlib.sha256(path.read_bytes()).hexdigest() for path in paths}

    def git(*argv: str) -> str:
        return subprocess.check_output(["git", "-C", str(repo), *argv], text=True).strip()

    binding = source_binding()
    head = git("rev-parse", "HEAD")
    changed = git("diff", "--name-only")
    assert head == "e72f72758a4f91c00738127e1a46850c9fd54174"
    assert changed == targets[1] and not git("diff", "--cached", "--name-only")
    command = [
        "C:/Users/canqu/.local/bin/uv.exe",
        "run",
        "--no-sync",
        "pytest",
        "-q",
        "-x",
        "--basetemp",
        str(output / "pytest"),
        "--junitxml",
        str(output / "junit.xml"),
        *targets,
    ]
    report: dict = {
        "state": "STARTING",
        "source_head": head,
        "binding": binding,
        "command": command,
        "error": None,
        "job_closed": False,
    }
    child = None
    job = OwnedJob()
    try:
        child = subprocess.Popen(
            command,
            cwd=repo,
            env=environment,
            text=True,
            encoding="utf-8",
            errors="replace",
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            creationflags=(0x00000004 | subprocess.CREATE_NEW_PROCESS_GROUP),
        )
        report["pid"] = child.pid
        job.attach_and_resume(child)
        stdout, stderr = child.communicate(timeout=600)
        (output / "stdout.log").write_text(stdout, encoding="utf-8")
        (output / "stderr.log").write_text(stderr, encoding="utf-8")
        report["returncode"] = child.returncode
        report["job_after"] = job.counts()
        assert child.returncode == 0 and report["job_after"]["active"] == 0
        suites = ET.parse(output / "junit.xml").getroot().findall("testsuite")
        counts = {
            key: sum(int(suite.attrib[key]) for suite in suites)
            for key in ("tests", "errors", "failures", "skipped")
        }
        report["junit"] = counts
        assert counts == {"tests": 38, "errors": 0, "failures": 0, "skipped": 0}
        report["source_unchanged"] = (
            binding == source_binding()
            and head == git("rev-parse", "HEAD")
            and changed == git("diff", "--name-only")
            and not git("diff", "--cached", "--name-only")
        )
        assert report["source_unchanged"]
        sqlite_files = []
        for path in sorted((output / "pytest").rglob("*.db")):
            with path.open("rb") as stream:
                if stream.read(16) == b"SQLite format 3\x00":
                    sqlite_files.append(str(path.relative_to(output)))
        report["sqlite_files_with_actual_header"] = sqlite_files
        assert sqlite_files
        report["state"] = "PASS"
    except BaseException as exc:
        report["state"] = "FAILED_OR_INCONCLUSIVE"
        report["error"] = repr(exc)
        if child is not None:
            if job.counts()["active"]:
                job.terminate()
            elif child.poll() is None:
                child.terminate()
            try:
                stdout, stderr = child.communicate(timeout=15)
                (output / "stdout.log").write_text(stdout or "", encoding="utf-8")
                (output / "stderr.log").write_text(stderr or "", encoding="utf-8")
                report["returncode"] = child.returncode
                report["job_after"] = job.counts()
            except BaseException as cleanup_exc:
                report["cleanup_error"] = repr(cleanup_exc)
    finally:
        job.close()
        report["job_closed"] = True
        (output / "command-result.json").write_text(
            json.dumps(report, indent=2) + "\n", encoding="utf-8"
        )
    print(json.dumps({key: report.get(key) for key in ("state", "returncode", "junit", "error")}))
    return 0 if report["state"] == "PASS" else 2


if __name__ == "__main__":
    raise SystemExit(main())


