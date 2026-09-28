"""Run uv pytest under a bounded private Windows Job; retain original results."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import time
import traceback
from pathlib import Path

from owned_process import OwnedJob


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", type=Path, required=True)
    parser.add_argument("--phase", choices=("red-pg", "green-pg"), required=True)
    args = parser.parse_args()
    repo, output = args.repo.resolve(), Path(__file__).resolve().parent
    assert output.is_relative_to(repo.parent / "output" / "qa")
    phase = output / args.phase
    assert not phase.exists(), "Run once; preserve original results"
    phase.mkdir()
    isolated = phase / "runner-home"
    isolated.mkdir()
    environment = dict(os.environ)
    for key in tuple(environment):
        if key.startswith(("OCTOP_DATABASE_", "PG", "OCTOP_CAPTCHA_")):
            environment.pop(key, None)
    environment.update(
        {
            "HOME": str(isolated),
            "USERPROFILE": str(isolated),
            "OCTOP_HOME": str(isolated / ".octop"),
            "PYTHONPATH": os.pathsep.join((str(output), str(repo / "src"), str(repo))),
            "XIONGBAO_PS04B_PG_CONTEXT": str(output / "cluster-context.json"),
            "XIONGBAO_PS04B_PG_REPORT": str(phase / "pg-http-bindings.json"),
            "XIONGBAO_PS04B_REPO": str(repo),
            "XIONGBAO_PS04B_PHASE": args.phase,
            "PYTHONIOENCODING": "utf-8",
        }
    )
    uv = Path("C:/Users/canqu/.local/bin/uv.exe")
    assert uv.is_file()
    tests = [
        "tests/integration/test_project_todos_api.py",
        "tests/integration/test_project_todo_comments_api.py",
    ]
    if args.phase == "red-pg":
        tests = [tests[1] + "::test_image_read_authorization_serializes_with_member_revocation"]
    command = [
        str(uv),
        "run",
        "--no-sync",
        "pytest",
        "-p",
        "ps04b_pg_fixture",
        "-q",
        "-x",
        "--basetemp",
        str(phase / "pytest"),
        "--junitxml",
        str(phase / "junit.xml"),
        *tests,
    ]
    paths = sorted(output.glob("*.py"))
    paths += sorted((repo / "src" / "octop").rglob("*.py"))
    paths += sorted((repo / "src" / "octop").rglob("*.sql"))
    paths += sorted((repo / "tests" / "support").rglob("*.py"))
    paths += [
        repo / path
        for path in (
            "tests/conftest.py",
            "tests/integration/conftest.py",
            "tests/integration/test_project_todos_api.py",
            "tests/integration/test_project_todo_comments_api.py",
        )
    ]
    binding = {
        str(path): {
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            "bytes": path.stat().st_size,
        }
        for path in paths
    }
    source_head = subprocess.check_output(
        ["git", "-C", str(repo), "rev-parse", "HEAD"], text=True
    ).strip()
    assert source_head == "e72f72758a4f91c00738127e1a46850c9fd54174"
    changes = subprocess.check_output(
        ["git", "-C", str(repo), "diff", "--name-only"], text=True
    ).splitlines()
    staged = subprocess.check_output(
        ["git", "-C", str(repo), "diff", "--cached", "--name-only"], text=True
    ).splitlines()
    allowed = (
        [] if args.phase == "red-pg" else ["tests/integration/test_project_todo_comments_api.py"]
    )
    assert changes == allowed and not staged
    report: dict = {
        "command": command,
        "repo": str(repo),
        "source_head": source_head,
        "runner_python": sys.version.split()[0],
        "bindings": binding,
        "state": "STARTING",
        "source_worktree_changes": changes,
        "source_index_changes": staged,
    }
    result_path = phase / "command-result.json"
    result_path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    child, job = None, None
    stdout, stderr = "", ""
    return_code, assigned = 2, False
    try:
        job = OwnedJob()
        child = subprocess.Popen(
            command,
            cwd=repo,
            env=environment,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            creationflags=subprocess.CREATE_NEW_PROCESS_GROUP | 0x4,
        )
        report["child_pid"] = child.pid
        job.attach_and_resume(child)
        assigned = True
        report["job_assigned_before_resume"] = True
        result_path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
        stdout, stderr = child.communicate(timeout=600)
        report.update(
            {
                "state": "CHILD_EXITED",
                "returncode": child.returncode,
                "bound_files_unchanged": all(
                    hashlib.sha256(Path(path).read_bytes()).hexdigest() == item["sha256"]
                    for path, item in binding.items()
                ),
            }
        )
        report["source_head_after"] = subprocess.check_output(
            ["git", "-C", str(repo), "rev-parse", "HEAD"], text=True
        ).strip()
        report["source_head_unchanged"] = report["source_head_after"] == source_head
        report["source_worktree_changes_after"] = subprocess.check_output(
            ["git", "-C", str(repo), "diff", "--name-only"], text=True
        ).splitlines()
        report["source_index_changes_after"] = subprocess.check_output(
            ["git", "-C", str(repo), "diff", "--cached", "--name-only"], text=True
        ).splitlines()
        report["source_changes_unchanged"] = (
            report["source_worktree_changes_after"] == changes
            and report["source_index_changes_after"] == staged
        )
        paths_after = sorted(output.glob("*.py")) + sorted((repo / "src" / "octop").rglob("*.py"))
        paths_after += sorted((repo / "src" / "octop").rglob("*.sql"))
        paths_after += sorted((repo / "tests" / "support").rglob("*.py"))
        paths_after += [
            repo / path
            for path in (
                "tests/conftest.py",
                "tests/integration/conftest.py",
                "tests/integration/test_project_todos_api.py",
                "tests/integration/test_project_todo_comments_api.py",
            )
        ]
        report["bound_path_set_unchanged"] = set(map(str, paths_after)) == set(binding)
        if not all(
            report[name]
            for name in (
                "bound_files_unchanged",
                "source_head_unchanged",
                "source_changes_unchanged",
                "bound_path_set_unchanged",
            )
        ):
            report["state"] = "INCONCLUSIVE_SOURCE_DRIFT"
        data = json.loads((phase / "pg-http-bindings.json").read_text(encoding="utf-8"))
        calls = [item for item in data["reports"] if item["when"] == "call"]
        if args.phase == "red-pg":
            failure = calls[0].get("failure", "") if len(calls) == 1 else ""
            location = calls[0].get("failure_location", {}) if len(calls) == 1 else {}
            non_call = [item for item in data["reports"] if item["when"] != "call"]
            valid = (
                child.returncode == 1
                and data["summary"]["collected"] == 1
                and data["summary"]["failed"] == 1
                and len(data["reports"]) == 3
                and data["summary"]["stage_errors"] == 0
                and failure == "AttributeError: 'PostgresPool' object has no attribute 'path'"
                and Path(location.get("path", "")).resolve()
                == (repo / "tests/integration/test_project_todo_comments_api.py").resolve()
                and location.get("line") == 301
                and len(non_call) == 2
                and all(item["outcome"] == "passed" for item in non_call)
            )
        else:
            valid = (
                child.returncode == 0
                and data["summary"]["collected"] == 38
                and len(data["reports"]) == 114
                and all(item["outcome"] == "passed" for item in data["reports"])
            )
        valid = (
            valid and data["expected_collection_matched"] and data["imports_from_requested_source"]
        )
        valid = (
            valid
            and data["fixture_error"] is None
            and all(
                item.get("patches_and_database_env_restored")
                and item.get("logger_snapshots_restored")
                and item.get("remaining_backend_connections") == 0
                and not item["cleanup_error"]
                for item in data["tests"]
            )
        )
        report["expected_phase_shape_matched"] = valid
        if not valid:
            report["state"] = "INCONCLUSIVE_PHASE_SHAPE"
        return_code = child.returncode if report["state"] == "CHILD_EXITED" else 2
    except BaseException as exc:
        report["state"] = (
            "INCONCLUSIVE_TIMEOUT"
            if isinstance(exc, subprocess.TimeoutExpired)
            else "INCONCLUSIVE_EXCEPTION"
        )
        report["initial_error"] = traceback.format_exc()
        if isinstance(exc, subprocess.TimeoutExpired):
            stdout = (
                exc.output.decode("utf-8", "replace")
                if isinstance(exc.output, bytes)
                else exc.output or ""
            )
            stderr = (
                exc.stderr.decode("utf-8", "replace")
                if isinstance(exc.stderr, bytes)
                else exc.stderr or ""
            )
    finally:
        try:
            if job is not None:
                report["job_counts_before_cleanup"] = job.counts()
                if report["job_counts_before_cleanup"]["active"]:
                    if report["state"] == "CHILD_EXITED":
                        report["state"] = "INCONCLUSIVE_CHILDREN_REMAIN"
                        return_code = 2
                    job.terminate()
                    if child is not None:
                        stdout, stderr = child.communicate(timeout=15)
                elif child is not None and not assigned and child.poll() is None:
                    child.terminate()
                    stdout, stderr = child.communicate(timeout=15)
                deadline = time.monotonic() + 15
                while True:
                    counts = job.counts()
                    if counts["active"] == 0 or time.monotonic() >= deadline:
                        break
                    time.sleep(0.05)
                report["job_counts_after_cleanup"] = counts
                assert counts["active"] == 0
        except BaseException as exc:
            report["termination_error"] = repr(exc)
            if report["state"] == "CHILD_EXITED":
                report["state"] = "INCONCLUSIVE_CLEANUP"
            return_code = 2
        finally:
            if job is not None:
                try:
                    job.close()
                    report["job_handle_closed"] = True
                except BaseException as exc:
                    report["job_close_error"] = repr(exc)
                    return_code = 2
        report["child_observed_stopped"] = child is not None and child.poll() is not None
        report["owned_processes_observed_stopped"] = (
            report.get("job_counts_after_cleanup", {}).get("active") == 0
        )
        (phase / "stdout.log").write_text(stdout, encoding="utf-8")
        (phase / "stderr.log").write_text(stderr, encoding="utf-8")
        report["runner_returncode"] = return_code
        result_path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(
        json.dumps(
            {
                "phase": args.phase,
                "state": report["state"],
                "returncode": report.get("returncode"),
                "expected_phase_shape_matched": report.get("expected_phase_shape_matched"),
                "owned_processes_observed_stopped": report["owned_processes_observed_stopped"],
            }
        ),
        flush=True,
    )
    if args.phase == "green-pg" and report.get("expected_phase_shape_matched"):
        print(stdout[-600:], end="")
    return return_code


if __name__ == "__main__":
    raise SystemExit(main())
