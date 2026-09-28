"""Copy only audited disposable QA evidence; never copy profiles, DBs or raw logs."""
from __future__ import annotations
import hashlib
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent
REPO = ROOT.parents[2] / "030-windows-path-qa"
OLD = ROOT.parent / "ps04b-pg-http-browser-20260928"
DEST = REPO / "docs/xiongbao/evidence/ps04b-pg-http-browser-20260928"
assert REPO.is_dir() and OLD.is_dir()
assert not DEST.exists(), "Archive once; keep prior bytes"
selection = []

def include(root, family, relative):
    source = root / relative
    assert source.is_file(), source
    assert source.resolve().is_relative_to(root.resolve()), source
    selection.append((source, Path(family) / relative))

for name in ("cluster-context.json", "cluster-result.json", "cluster_lease.py",
             "owned_process.py", "ps04b_pg_fixture.py", "run_http_tests.py",
             "smoke_owned_job.py", "smoke_owned_job_v2.py",
             "job-timeout-smoke.json", "job-timeout-smoke-v2.json",
             "tcp/verify_sqlite.py", "tcp/verify_sqlite_v2.py",
             "tcp/probe_launch.py", "tcp/probe_launch.cjs",
             "tcp/launch-normal-environment.json", "tcp/launch-normal-job.json"):
    include(OLD, "http-suite", name)
for phase in ("red-pg", "green-pg"):
    for name in ("command-result.json", "pg-http-bindings.json", "junit.xml"):
        include(OLD, "http-suite", phase + "/" + name)
include(OLD, "http-suite", "sqlite-compat/command-result.json")
for name in ("command-result.json", "junit.xml"):
    include(OLD, "http-suite", "sqlite-compat-v2/" + name)
for phase in ("browser-pg", "browser-pg-attempt-2"):
    for name in ("command-result.json", "server-result.json"):
        include(OLD, "http-suite", phase + "/" + name)
    journey = OLD / phase / "journey/result.json"
    if journey.exists():
        include(OLD, "http-suite", phase + "/journey/result.json")
    for name in ("browser_flow.cjs", "browser_server.py", "run_browser.py", "owned_process.py"):
        include(OLD, "http-suite", phase + "/scripts/" + name)
for name in ("cluster-context.json", "cluster-result.json", "cluster_lease.py",
             "owned_process.py", "browser_flow.cjs", "tcp/browser_server.py", "tcp/run_browser.py"):
    include(ROOT, "tcp-browser", name)
for phase in ("browser-pg", "browser-pg-attempt-2", "browser-pg-attempt-3"):
    for name in ("command-result.json", "server-result.json", "journey/result.json"):
        include(ROOT, "tcp-browser", phase + "/" + name)
    for name in ("browser_flow.cjs", "browser_server.py", "run_browser.py", "owned_process.py"):
        include(ROOT, "tcp-browser", phase + "/scripts/" + name)
    for shot in sorted((ROOT / phase / "journey/screenshots").glob("*.png")):
        include(ROOT, "tcp-browser", str(shot.relative_to(ROOT)).replace("\\", "/"))
include(ROOT, "archive", "archive_ps04b_evidence.py")
assert len(selection) == len({str(item[1]) for item in selection})
jwt = re.compile(rb"eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+")
for source, _ in selection:
    raw = source.read_bytes()
    if source.suffix in (".json", ".xml"):
        assert not jwt.search(raw), "Do not publish raw auth tokens"
        if source.suffix == ".json":
            json.loads(raw)
DEST.mkdir(parents=True)
(DEST / ".gitattributes").write_text(
    "*.json -text\n*.xml -text\n*.py -text\n*.cjs -text\n*.png -text\n", encoding="utf-8")
manifest = []
for source, relative in selection:
    raw = source.read_bytes()
    target = DEST / relative
    assert target.resolve().is_relative_to(DEST.resolve())
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(raw)
    digest = hashlib.sha256(raw).hexdigest()
    assert hashlib.sha256(target.read_bytes()).hexdigest() == digest
    manifest.append({"file": str(relative).replace("\\", "/"), "original": str(source),
                     "bytes": len(raw), "sha256": digest})
(DEST / "manifest.json").write_text(json.dumps({
    "source_head": "e72f72758a4f91c00738127e1a46850c9fd54174",
    "scope": "PS04B adapter plus two test files and bounded TCP browser journey",
    "files": manifest,
    "excluded": ["raw stdout/stderr", "isolated homes", "database files",
                 "private image storage", "browser profiles", "real user configuration"]
}, indent=2) + "\n", encoding="utf-8")
print(json.dumps({"archive": str(DEST), "files": len(manifest),
                  "bytes": sum(item["bytes"] for item in manifest), "byte_copy_verified": True}))

