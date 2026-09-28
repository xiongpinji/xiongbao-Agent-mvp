"""Bounded private launch-only Chrome probe, without app, account or database."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from owned_process import OwnedJob  # noqa: E402

output = Path(__file__).with_name("launch-normal-job.json")
assert not output.exists()
job = OwnedJob()
child = None
report = {"state": "STARTING", "error": None}
try:
    child = subprocess.Popen(
        ["C:/Program Files/nodejs/node.exe", str(Path(__file__).with_suffix(".cjs"))],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        creationflags=0x00000004 | subprocess.CREATE_NEW_PROCESS_GROUP,
    )
    report["pid"] = child.pid
    job.attach_and_resume(child)
    stdout, stderr = child.communicate(timeout=45)
    report.update(returncode=child.returncode, job_after=job.counts(), stdout=stdout, stderr=stderr)
    assert child.returncode == 0 and report["job_after"]["active"] == 0
    report["state"] = "PASS"
except BaseException as exc:
    report["state"] = "FAILED_OR_INCONCLUSIVE"
    report["error"] = repr(exc)
finally:
    if child is not None and child.poll() is None:
        if job.counts()["active"]:
            job.terminate()
        else:
            child.terminate()
        stdout, stderr = child.communicate(timeout=10)
        report.update(
            returncode=child.returncode, job_after=job.counts(), stdout=stdout, stderr=stderr
        )
    job.close()
    report["job_closed"] = True
    output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
print(json.dumps({key: report.get(key) for key in ("state", "returncode", "job_after", "error")}))
raise SystemExit(0 if report["state"] == "PASS" else 2)
