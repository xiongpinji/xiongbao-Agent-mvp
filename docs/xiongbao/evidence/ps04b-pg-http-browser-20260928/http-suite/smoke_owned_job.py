"""Exercise timeout closure of an owned Python process and its Python child."""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
import time
import traceback
from pathlib import Path

from owned_process import OwnedJob

output = Path(__file__).resolve().parent
result_path = output / "job-timeout-smoke.json"
assert not result_path.exists()
helper = output / "owned_process.py"
fingerprint = hashlib.sha256(helper.read_bytes()).hexdigest()
report: dict = {"state": "STARTING", "helper_sha256": fingerprint, "error": None}
child, job = None, None
try:
    job = OwnedJob()
    source = (
        "import json,os,subprocess,sys,time; "
        "nested=subprocess.Popen([sys.executable,'-c','import time;time.sleep(30)']); "
        "print(json.dumps({'parent_pid':os.getpid(),'nested_pid':nested.pid}),flush=True); "
        "time.sleep(30)"
    )
    child = subprocess.Popen(
        [sys.executable, "-c", source],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        creationflags=subprocess.CREATE_NEW_PROCESS_GROUP | 0x4,
    )
    job.attach_and_resume(child)
    report["parent_pid"] = child.pid
    try:
        child.communicate(timeout=3)
        raise AssertionError("The long-running two-process sample must time out")
    except subprocess.TimeoutExpired:
        report["timeout_observed"] = True
    report["before"] = job.counts()
    assert report["before"]["active"] == 2 and report["before"]["total"] == 2
    job.terminate()
    stdout, stderr = child.communicate(timeout=15)
    identifiers = json.loads(stdout.strip())
    assert identifiers["parent_pid"] == child.pid and identifiers["nested_pid"] != child.pid
    report.update(identifiers)
    report["child_returncode"] = child.returncode
    report["stderr"] = stderr
    deadline = time.monotonic() + 5
    while True:
        counters = job.counts()
        if counters["active"] == 0 or time.monotonic() >= deadline:
            break
        time.sleep(0.05)
    report["after"] = counters
    assert counters["active"] == 0 and child.poll() is not None and child.returncode == 2
    assert hashlib.sha256(helper.read_bytes()).hexdigest() == fingerprint
    report["state"] = "PASS_TIMEOUT_TREE_STOPPED"
except BaseException:
    report["state"] = "INCONCLUSIVE"
    report["error"] = traceback.format_exc()
finally:
    try:
        if job is not None and job.counts()["active"]:
            job.terminate()
            if child is not None:
                child.communicate(timeout=15)
        if child is not None and child.poll() is None:
            child.terminate()
            child.communicate(timeout=15)
    except BaseException:
        report["cleanup_error"] = traceback.format_exc()
    finally:
        if job is not None:
            job.close()
            report["job_closed"] = True
        result_path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
print(json.dumps(report))
raise SystemExit(
    0 if report["state"] == "PASS_TIMEOUT_TREE_STOPPED" and not report.get("cleanup_error") else 2
)
