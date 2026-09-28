"""Bound one diagnostic subprocess; preserve timeout as inconclusive evidence."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import traceback
from pathlib import Path

assert os.name == "nt"
repo = Path(os.environ["QA_REPO"]).resolve()
output = Path(os.environ["QA_OUTPUT"]).resolve()
probe = output / "probe_windows_http_race.py"
assert output.is_relative_to(repo.parent / "output" / "qa")
for name in (
    "watchdog-result.json",
    "http-race-result.json",
    "source-bindings.json",
    "run-context.json",
    "stdout.log",
    "stderr.log",
):
    assert not (output / name).exists(), f"Never overwrite prior evidence: {name}"
assert probe.is_file()
report = {
    "source": os.environ["QA_EXPECTED_SOURCE"],
    "probe_sha256": hashlib.sha256(probe.read_bytes()).hexdigest(),
    "watchdog_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
    "hard_timeout_seconds": 300,
    "timed_out": False,
    "child_returncode": None,
    "scope": "one disposable native Python process; timeout is INCONCLUSIVE",
}
process = None
stdout = stderr = b""
try:
    process = subprocess.Popen(
        [sys.executable, str(probe)],
        cwd=repo,
        env=os.environ.copy(),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    report["child_pid"] = process.pid
    print("watchdog_child_pid", process.pid, "hard_timeout_seconds", 300, flush=True)
    try:
        stdout, stderr = process.communicate(timeout=300)
    except subprocess.TimeoutExpired as first_timeout:
        report["timed_out"] = True
        stdout, stderr = first_timeout.output or b"", first_timeout.stderr or b""
        try:
            process.kill()
            stdout, stderr = process.communicate(timeout=15)
        except Exception as termination_error:
            report["termination_error"] = traceback.format_exc()
            if isinstance(termination_error, subprocess.TimeoutExpired):
                stdout = termination_error.output or stdout
                stderr = termination_error.stderr or stderr
except Exception:
    report["watchdog_error"] = traceback.format_exc()
finally:
    report["child_observed_stopped"] = False
    if process is not None:
        try:
            report["child_returncode"] = process.poll()
            report["child_observed_stopped"] = report["child_returncode"] is not None
        except Exception:
            report["poll_error"] = traceback.format_exc()
    if process is None:
        report["outcome"] = "INCONCLUSIVE_LAUNCH_ERROR"
    elif not report["child_observed_stopped"]:
        report["outcome"] = "INCONCLUSIVE_TERMINATION_FAILED"
    elif report.get("termination_error") or report.get("watchdog_error"):
        report["outcome"] = "INCONCLUSIVE_WATCHDOG_ERROR"
    elif report["timed_out"]:
        report["outcome"] = "INCONCLUSIVE_HARD_TIMEOUT"
    else:
        report["outcome"] = "CHILD_EXITED"
    (output / "stdout.log").write_bytes(stdout)
    (output / "stderr.log").write_bytes(stderr)
    (output / "watchdog-result.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
print(stdout.decode("utf-8", errors="replace"), end="", flush=True)
print(stderr.decode("utf-8", errors="replace"), end="", file=sys.stderr, flush=True)
print(
    "watchdog_outcome",
    report["outcome"],
    "child_returncode",
    report["child_returncode"],
    flush=True,
)
raise SystemExit(report["child_returncode"] if report["outcome"] == "CHILD_EXITED" else 2)
