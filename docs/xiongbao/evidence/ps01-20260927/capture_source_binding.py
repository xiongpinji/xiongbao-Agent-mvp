"""Capture only non-secret source bindings for this disposable PS-01 run."""

from __future__ import annotations

import json
import os
from pathlib import Path
import re
import subprocess


CHECKOUT = Path("/home/canqu/work/xiongbao-ps01-pg-current")
SOURCE = CHECKOUT / "src"
DASHBOARD = CHECKOUT / "dashboard"
HOME = Path("/home/canqu/work/xiongbao-ps01-browser-home-20260927")
EXPECTED_SHA = "0cbe14960c06654e946dcc7ecc1c706b8ce56c61"


def port_pid(port: int) -> int:
    listing = subprocess.check_output(["ss", "-lptn"], text=True)
    match = re.search(rf"127\.0\.0\.1:{port}\b[^\n]*?pid=(\d+)", listing)
    assert match, f"No loopback listener on port {port}"
    return int(match.group(1))


def process_env(pid: int) -> dict[str, str]:
    pairs = Path(f"/proc/{pid}/environ").read_bytes().split(b"\0")
    return dict(
        pair.decode("utf-8", errors="replace").split("=", 1)
        for pair in pairs
        if b"=" in pair
    )


def main() -> None:
    sha = subprocess.check_output(["git", "-C", str(CHECKOUT), "rev-parse", "HEAD"], text=True).strip()
    assert sha == EXPECTED_SHA
    backend_pid = port_pid(18771)
    frontend_pid = port_pid(5173)
    backend_env = process_env(backend_pid)
    frontend_env = process_env(frontend_pid)
    backend_cwd = Path(os.readlink(f"/proc/{backend_pid}/cwd"))
    frontend_cwd = Path(os.readlink(f"/proc/{frontend_pid}/cwd"))
    assert backend_cwd == CHECKOUT
    assert frontend_cwd == DASHBOARD
    assert backend_env.get("PYTHONPATH") == str(SOURCE)
    assert backend_env.get("OCTOP_HOME") == str(HOME)
    assert frontend_env.get("VITE_API_PORT") == "18771"
    cmdline = Path(f"/proc/{backend_pid}/cmdline").read_bytes().split(b"\0")
    python = cmdline[0].decode("utf-8")
    assert Path(python).name == "python"
    assert b"octop" in cmdline and b"run" in cmdline
    import_env = os.environ.copy()
    import_env["PYTHONPATH"] = backend_env["PYTHONPATH"]
    origin = subprocess.check_output(
        [python, "-c", "import octop; print(octop.__file__)"],
        env=import_env,
        text=True,
    ).strip()
    assert origin == str(SOURCE / "octop" / "__init__.py")
    print(json.dumps({
        "result": "PASS",
        "sha": sha,
        "backend_pid": backend_pid,
        "backend_cwd": str(backend_cwd),
        "backend_pythonpath": backend_env["PYTHONPATH"],
        "backend_module_origin": origin,
        "backend_octop_home": backend_env["OCTOP_HOME"],
        "frontend_pid": frontend_pid,
        "frontend_cwd": str(frontend_cwd),
        "frontend_api_port": frontend_env["VITE_API_PORT"],
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
