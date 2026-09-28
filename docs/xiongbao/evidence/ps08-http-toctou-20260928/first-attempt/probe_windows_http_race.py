"""Diagnostic only: real ASGI/runtime, exact Windows I/O seams, synthetic Temp."""

from __future__ import annotations

import asyncio
import hashlib
import importlib.metadata
import inspect
import json
import logging
import os
import platform
import stat
import subprocess
import sys
import tempfile
import threading
import traceback
from pathlib import Path

REPO = Path(os.environ["QA_REPO"]).resolve()
OUTPUT = Path(os.environ["QA_OUTPUT"]).resolve()
EXPECTED = os.environ["QA_EXPECTED_SOURCE"]
assert os.name == "nt", "This diagnostic requires native Windows NTFS"
assert OUTPUT.is_relative_to(REPO.parent / "output" / "qa")
assert not (OUTPUT / "http-race-result.json").exists(), "Never overwrite a prior run"
assert not (OUTPUT / "source-bindings.json").exists(), "Never overwrite prior bindings"
assert not (OUTPUT / "run-context.json").exists(), "Never overwrite a prior context"
OUTPUT.mkdir(parents=True, exist_ok=True)
sys.path[:0] = [str(REPO), str(REPO / "src")]

import pytest  # noqa: E402
from deepagents.backends.filesystem import FilesystemBackend  # noqa: E402
from harness_agent.backends.workspace import BackendWorkspace  # noqa: E402
from harness_agent.llm.factory import ChatModelFactory  # noqa: E402
from tests.integration.test_project_task_file_real_runtime_http import (  # noqa: E402
    INSTRUCTIONS,
    OWNER,
    PASSWORD,
    _assert_internal_forbidden,
    _bootstrap_workspace_project,
    _real_runtime_client,
)
from tests.support.auth import auth_header  # noqa: E402
from tests.unit.agents.test_project_task_file_boundary import _require_junction  # noqa: E402

import octop  # noqa: E402
from octop.api.routers import workspace as workspace_routes  # noqa: E402
from octop.config import load_config  # noqa: E402
from octop.infra.db.pool import SqlitePool  # noqa: E402
from octop.infra.proactive.scheduler import ProactiveCareScheduler  # noqa: E402
from octop.infra.projects import file_tasks  # noqa: E402
from octop.infra.projects.tasks import instructions_sha256  # noqa: E402
from octop.infra.server import OctopServer  # noqa: E402


def source_head():
    return subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=REPO, text=True).strip()


SOURCE = source_head()
assert SOURCE == EXPECTED
assert Path(octop.__file__).resolve() == REPO / "src" / "octop" / "__init__.py"
assert file_tasks.PROJECT_TASK_FILES_MODE_ENABLED is False
assert not subprocess.check_output(
    ["git", "diff", "--name-only", "--", "src", "tests"], cwd=REPO
).strip(), "Tracked business sources must be clean"
BASE = Path(tempfile.mkdtemp(prefix="xiongbao-030-http-toctou-")).resolve()
assert BASE.parent == Path(tempfile.gettempdir()).resolve()
assert BASE.name.startswith("xiongbao-030-http-toctou-")
INSIDE = "XIONGBAO_SYNTHETIC_HTTP_INSIDE"
CANARY = "XIONGBAO_SYNTHETIC_HTTP_OUTSIDE_CANARY"
WRITE = "XIONGBAO_SYNTHETIC_HTTP_OUTSIDE_OVERWRITE"
REAL_GUARD = workspace_routes.project_task_file_io_path
REAL_MATERIALIZE = BackendWorkspace.materialize_local
REAL_RESOLVE = FilesystemBackend._resolve_path
REAL_PATH_OPEN = Path.open
REAL_OS_OPEN = os.open
REAL_START = OctopServer.start
REAL_STOP = OctopServer.stop
REAL_SQLITE_INIT = SqlitePool.__init__
REAL_FACTORY = ChatModelFactory.get_chat_model
REAL_PROACTIVE = {
    name: getattr(ProactiveCareScheduler, name)
    for name in ("ensure_scheduled", "start_all", "_schedule")
}
DATABASE_ENV_KEYS = (
    "OCTOP_DATABASE_URL",
    "OCTOP_DATABASE_DRIVER",
    "OCTOP_DATABASE_SQLITE_PATH",
    "OCTOP_DATABASE_HOST",
    "OCTOP_DATABASE_PORT",
    "OCTOP_DATABASE_NAME",
    "OCTOP_DATABASE_USER",
    "OCTOP_DATABASE_PASSWORD",
)
ENV_KEYS = (
    "HOME",
    "USERPROFILE",
    "OCTOP_HOME",
    "PATH",
    "OCTOP_CAPTCHA_PROVIDER",
    "OCTOP_CAPTCHA_SITE_KEY",
    "OCTOP_CAPTCHA_SECRET",
    "OCTOP_CAPTCHA_V3_MIN_SCORE",
) + tuple(
    sorted(
        set(DATABASE_ENV_KEYS)
        | {key for key in os.environ if key.upper().startswith("OCTOP_DATABASE_")}
    )
)
ENV_BEFORE = {key: os.environ.get(key) for key in ENV_KEYS}
report = {
    "source": SOURCE,
    "repo": str(REPO),
    "octop_module": octop.__file__,
    "platform": platform.platform(),
    "python": sys.version.split()[0],
    "python_executable": sys.executable,
    "deepagents_version": importlib.metadata.version("deepagents"),
    "harness_agent_version": importlib.metadata.version("harness-agent"),
    "temp_base": str(BASE),
    "source_files_enabled": False,
    "fixture_files_enabled": True,
    "transport": "local ASGI only; no TCP/browser",
    "provider": "local recording factory only; no model turn submitted",
    "external_model_requests": 0,
    "external_model_requests_basis": "fixed local model factory, not network capture",
    "cases": [],
    "outcome": "RUNNING",
}


def lexical(value):
    try:
        return os.path.normcase(os.path.abspath(os.fsdecode(value)))
    except TypeError:
        return None


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def ensure_temp(path):
    resolved = path.resolve()
    assert resolved.is_relative_to(BASE), f"Computed target outside synthetic Temp: {path}"
    return resolved


def event(state, name, **data):
    state["events"].append({"event": name, **data})


def plant(child, saved, outside, state):
    ensure_temp(child)
    ensure_temp(saved)
    ensure_temp(outside)
    assert child.is_dir() and not child.is_junction() and not child.is_symlink()
    assert not os.path.lexists(saved)
    child.rename(saved)
    _require_junction(child, outside)
    assert child.is_junction() and child.resolve() == outside.resolve()
    state["junction_installed"] = True
    event(state, "junction_installed", target=str(outside))


def restore(child, saved, outside, state):
    ensure_temp(child.parent)
    ensure_temp(saved)
    ensure_temp(outside)
    if child.is_junction():
        assert child.resolve() == outside.resolve(), "Never remove an unknown junction"
        child.rmdir()
        event(state, "junction_removed")
    if saved.exists():
        assert not os.path.lexists(child), "Never overwrite an unexpected directory"
        assert not saved.is_junction() and not saved.is_symlink()
        saved.rename(child)
        event(state, "child_restored")
    assert child.is_dir() and not child.is_junction() and not child.is_symlink()
    state["child_restored"] = True


def stack():
    return [
        {"file": frame.filename, "line": frame.lineno, "name": frame.name}
        for frame in traceback.extract_stack(limit=12)[:-1]
    ]


async def run_case(label, read, stable):
    assert file_tasks.PROJECT_TASK_FILES_MODE_ENABLED is False
    print("case", label, "begin", flush=True)
    case = BASE / label
    isolated_user = case / "user-home"
    home = isolated_user / ".octop"
    outside = case / "outside"
    home.mkdir(parents=True)
    outside.mkdir()
    canary = outside / "target.txt"
    canary.write_text(CANARY, encoding="utf-8")
    before_hash = digest(canary)
    outside_names = sorted(path.name for path in outside.iterdir())
    method = "GET" if read else "PUT"
    relative = "child/target.txt"
    state = {
        "label": label,
        "method": method,
        "stable_plant": stable,
        "home": str(home),
        "outside": str(outside),
        "baseline_pass": False,
        "acl": [],
        "guard_started": 0,
        "guard_passed": 0,
        "guard_refused": 0,
        "backend_checked": 0,
        "target_open_hits": 0,
        "real_open_delegated": 0,
        "junction_installed": False,
        "swap_fired": False,
        "events": [],
        "classification": "INCONCLUSIVE",
        "http_status": None,
        "outside_canary_in_response": False,
        "outside_overwrite_observed": False,
        "outside_hash_before": before_hash,
        "outside_hash_after": None,
        "exception": None,
        "restore_error": None,
        "stop_error": None,
        "server_stop_completed": False,
        "local_model_invocations": None,
    }
    report["cases"].append(state)
    holder = {}
    loggers = [
        logging.getLogger(name)
        for name in ("", "uvicorn", "uvicorn.access", "uvicorn.error", "httpx", "httpcore")
    ]
    logger_before = [(logger, list(logger.handlers), logger.level) for logger in loggers]
    child = saved = root = None
    route_pass = threading.Event()
    backend_pass = threading.Event()

    async def observe_start(self):
        assert self.paths.root.resolve() == home.resolve()
        holder["server"] = self
        assert not any(os.environ.get(key) for key in DATABASE_ENV_KEYS)
        config = load_config(self.paths.config)
        assert config.database.is_sqlite
        ensure_temp(config.database.resolve_sqlite_path(home))
        assert config.database.resolve_sqlite_path(home).resolve().is_relative_to(home.resolve())
        await REAL_START(self)
        state["server_start_completed"] = True

    def fenced_sqlite_init(self, path):
        resolved = ensure_temp(Path(path))
        assert resolved.is_relative_to(home.resolve()), (
            "Refuse any database outside this fresh home before open"
        )
        REAL_SQLITE_INIT(self, path)
        state.setdefault("sqlite_paths_before_open", []).append(str(resolved))

    async def no_start_all(self):
        return None

    async def observe_stop(self):
        state["server_stop_calls"] = state.get("server_stop_calls", 0) + 1
        try:
            await REAL_STOP(self)
        except BaseException:
            state["stop_error"] = traceback.format_exc()
            raise
        assert self.services is None and self.app_runtime is None and not self._started
        state["server_stop_completed"] = True

    def traced_guard(checked_root, raw, **kwargs):
        matched = lexical(checked_root) == lexical(root) and raw == relative
        if matched:
            state["guard_started"] += 1
            event(state, "route_guard_started")
        try:
            result = REAL_GUARD(checked_root, raw, **kwargs)
        except BaseException:
            if matched:
                state["guard_refused"] += 1
                event(state, "route_guard_refused")
            raise
        if matched:
            assert kwargs["from_workspace"] is True and kwargs["kind"] == "file"
            state["guard_passed"] += 1
            route_pass.set()
            event(state, "route_guard_passed", result=result)
        return result

    def traced_materialize(self, path, **kwargs):
        result = REAL_MATERIALIZE(self, path, **kwargs)
        if result is not None and lexical(result) == lexical(child / "target.txt"):
            assert route_pass.is_set()
            state["backend_checked"] += 1
            backend_pass.set()
            event(state, "materialize_returned", path=str(result))
        return result

    def traced_resolve(self, path):
        result = REAL_RESOLVE(self, path)
        if lexical(result) == lexical(child / "target.txt"):
            assert route_pass.is_set()
            state["backend_checked"] += 1
            backend_pass.set()
            event(state, "backend_resolve_returned", path=str(result))
        return result

    def before_open(mode=None, flags=None):
        state["target_open_hits"] += 1
        assert route_pass.is_set() and backend_pass.is_set()
        assert state["target_open_hits"] == 1, "Injection must be exactly once"
        event(state, "target_open_wrapper_entered", mode=mode, flags=flags, stack=stack())
        if not stable:
            assert not state["swap_fired"]
            state["swap_fired"] = True
            plant(child, saved, outside, state)

    def traced_path_open(self, *args, **kwargs):
        if lexical(self) == lexical(child / "target.txt"):
            mode = args[0] if args else kwargs.get("mode", "r")
            assert read and mode == "r"
            before_open(mode=mode)
            state["real_open_delegated"] += 1
        return REAL_PATH_OPEN(self, *args, **kwargs)

    def traced_os_open(path, flags, *args, **kwargs):
        if lexical(path) == lexical(child / "target.txt"):
            assert not read and flags & os.O_WRONLY and flags & os.O_TRUNC
            before_open(flags=int(flags))
            state["real_open_delegated"] += 1
        return REAL_OS_OPEN(path, flags, *args, **kwargs)

    try:
        with pytest.MonkeyPatch.context() as mp:
            mp.setenv("HOME", str(isolated_user))
            mp.setenv("USERPROFILE", str(isolated_user))
            mp.setenv("OCTOP_HOME", str(home))
            mp.setenv("PATH", os.environ.get("PATH", ""))
            for key in ENV_KEYS[4:]:
                mp.delenv(key, raising=False)
            mp.setattr(file_tasks, "PROJECT_TASK_FILES_MODE_ENABLED", True)
            mp.setattr(OctopServer, "start", observe_start)
            mp.setattr(OctopServer, "stop", observe_stop)
            mp.setattr(SqlitePool, "__init__", fenced_sqlite_init)
            mp.setattr(ProactiveCareScheduler, "ensure_scheduled", lambda self, agent_id: None)
            mp.setattr(ProactiveCareScheduler, "start_all", no_start_all)
            mp.setattr(ProactiveCareScheduler, "_schedule", lambda self, agent_id: None)
            try:
                async with _real_runtime_client(home, mp) as (client, srv, model):
                    assert Path.home().resolve() == isolated_user.resolve()
                    assert isinstance(srv.services.db, SqlitePool)
                    assert srv.config.database.is_sqlite
                    with srv.services.db.connect() as conn:
                        main_db = next(
                            row[2]
                            for row in conn.execute("PRAGMA database_list")
                            if row[1] == "main"
                        )
                    assert Path(main_db).resolve() == srv.services.db.path.resolve()
                    ensure_temp(Path(main_db))
                    state["sqlite_identity_verified"] = True
                    ctx = await _bootstrap_workspace_project(client, srv, home)
                    created = await client.post(
                        f"/api/projects/{ctx['project_id']}/tasks",
                        headers=ctx["member_auth"],
                        json={
                            "agent_id": ctx["source_agent"],
                            "mode": "files",
                            "expected_instructions_sha256": instructions_sha256(INSTRUCTIONS),
                        },
                    )
                    assert created.status_code == 201, created.text
                    payload = created.json()
                    runtime_id = payload["chat_agent_id"]
                    assert runtime_id and runtime_id != ctx["source_agent"]
                    assert payload["mode"] == "files"
                    row = srv.services.agent_repo.get(runtime_id)
                    assert row is not None and row.user_id == ctx["member_uid"]
                    assert row.runtime_kind == "project_task_files" and row.last_state == "running"
                    root = srv.services.paths.project_task_file_runtime_dir(runtime_id)
                    ensure_temp(root)
                    assert root.is_dir() and not root.is_junction()
                    child = root / "child"
                    saved = root / "child_saved"
                    child.mkdir()
                    (child / "target.txt").write_text(INSIDE, encoding="utf-8")
                    state.update(runtime_id=runtime_id, root=str(root), create_http_status=201)
                    endpoint = f"/api/agents/{runtime_id}/workspace/file"
                    target_params = {"path": relative, "from_workspace": "true"}

                    async def request(auth, params):
                        kwargs = {"headers": auth, "params": params}
                        if not read:
                            kwargs["json"] = {"content": WRITE}
                        return await client.request(method, endpoint, **kwargs)

                    try:
                        if read:
                            baseline = await client.get(
                                endpoint, headers=ctx["member_auth"], params=target_params
                            )
                            assert (
                                baseline.status_code == 200 and baseline.json()["content"] == INSIDE
                            )
                        else:
                            baseline = await client.put(
                                endpoint,
                                headers=ctx["member_auth"],
                                params={"path": "child/baseline.txt", "from_workspace": "true"},
                                json={"content": INSIDE},
                            )
                            assert baseline.status_code == 200, baseline.text
                            assert (child / "baseline.txt").read_text(encoding="utf-8") == INSIDE
                        assert digest(canary) == before_hash
                        state["baseline_pass"] = True
                        owner_auth = await auth_header(client, username=OWNER, password=PASSWORD)
                        admin_auth = await auth_header(client)
                        with pytest.MonkeyPatch.context() as hooks:
                            hooks.setattr(
                                workspace_routes, "project_task_file_io_path", traced_guard
                            )
                            if read:
                                hooks.setattr(
                                    BackendWorkspace, "materialize_local", traced_materialize
                                )
                                hooks.setattr(Path, "open", traced_path_open)
                            else:
                                hooks.setattr(FilesystemBackend, "_resolve_path", traced_resolve)
                                hooks.setattr(os, "open", traced_os_open)
                            for name, auth, params in (
                                ("project_owner", owner_auth, target_params),
                                ("admin", admin_auth, target_params),
                                ("outsider", ctx["outsider_auth"], target_params),
                                (
                                    "owner_as_user",
                                    ctx["member_auth"],
                                    {**target_params, "as_user": ctx["member_uid"]},
                                ),
                            ):
                                denied = await request(auth, params)
                                _assert_internal_forbidden(denied)
                                assert (
                                    state["guard_started"]
                                    == state["backend_checked"]
                                    == state["target_open_hits"]
                                    == 0
                                )
                                state["acl"].append(
                                    {"identity": name, "http_status": 403, "target_open_hits": 0}
                                )
                            if stable:
                                plant(child, saved, outside, state)
                            response = await request(ctx["member_auth"], target_params)
                            state["http_status"] = response.status_code
                            if stable:
                                _assert_internal_forbidden(response)
                                assert state["guard_refused"] == 1 and state["guard_passed"] == 0
                                assert (
                                    state["backend_checked"]
                                    == state["target_open_hits"]
                                    == state["real_open_delegated"]
                                    == 0
                                )
                            else:
                                assert (
                                    state["guard_passed"]
                                    == state["backend_checked"]
                                    == state["target_open_hits"]
                                    == state["real_open_delegated"]
                                    == 1
                                )
                                assert state["junction_installed"] and state["swap_fired"]
                                assert response.status_code == 200, response.text
                        state["outside_canary_in_response"] = (
                            read
                            and response.status_code == 200
                            and response.json()["content"] == CANARY
                        )
                        state["outside_overwrite_observed"] = (
                            not read and canary.read_text(encoding="utf-8") == WRITE
                        )
                        state["outside_hash_after"] = digest(canary)
                        assert sorted(path.name for path in outside.iterdir()) == outside_names
                        if stable:
                            assert digest(canary) == before_hash
                            assert (
                                not state["outside_canary_in_response"]
                                and not state["outside_overwrite_observed"]
                            )
                            state["classification"] = "STABLE_PLANT_REFUSED"
                        elif read:
                            assert (
                                state["outside_canary_in_response"]
                                and digest(canary) == before_hash
                            )
                            state["classification"] = "CHECK_USE_READ_ESCAPE_CONFIRMED"
                        else:
                            assert (
                                state["outside_overwrite_observed"]
                                and digest(canary) != before_hash
                            )
                            state["classification"] = "CHECK_USE_OVERWRITE_ESCAPE_CONFIRMED"
                    finally:
                        if child is not None:
                            try:
                                restore(child, saved, outside, state)
                                assert (child / "target.txt").read_text(encoding="utf-8") == INSIDE
                                state["inside_original_unchanged"] = True
                            except BaseException:
                                state["restore_error"] = traceback.format_exc()
                                raise
                        state["local_model_invocations"] = len(model.invocations)
                        assert not model.invocations, "No model turn is part of this probe"
            finally:
                observed = holder.get("server")
                if observed is not None and not state["server_stop_completed"]:
                    await observe_stop(observed)
    except BaseException:
        state["exception"] = traceback.format_exc()
        state["classification"] = "DIAGNOSTIC_ERROR"
    finally:
        added = set()
        for logger, prior_handlers, prior_level in logger_before:
            for handler in list(logger.handlers):
                if handler not in prior_handlers:
                    logger.removeHandler(handler)
                    added.add(handler)
            logger.setLevel(prior_level)
        for handler in added:
            handler.close()
        state["diagnostic_log_handlers_closed"] = len(added)
        assert all(
            logger.handlers == prior_handlers and logger.level == prior_level
            for logger, prior_handlers, prior_level in logger_before
        )
    print("case", label, state["classification"], flush=True)
    return state["classification"] != "DIAGNOSTIC_ERROR"


def nonfollowing_reparse_inventory():
    pending = [BASE]
    links = []
    count = 0
    while pending:
        current = pending.pop()
        ensure_temp(current)
        for entry in os.scandir(current):
            count += 1
            assert count <= 10000, "Synthetic Temp inventory bound exceeded"
            metadata = entry.stat(follow_symlinks=False)
            if getattr(metadata, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT:
                links.append(entry.path)
            elif stat.S_ISDIR(metadata.st_mode):
                pending.append(Path(entry.path))
    return {"entries_examined": count, "remaining_reparse_points": links}


async def main():
    (OUTPUT / "run-context.json").write_text(
        json.dumps(
            {
                "source": SOURCE,
                "temp_base": str(BASE),
                "scope": "synthetic native Windows ASGI only",
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    bindings = [
        Path(__file__).resolve(),
        Path(inspect.getfile(FilesystemBackend)),
        Path(inspect.getfile(BackendWorkspace)),
    ]
    bindings += [
        REPO / relative
        for relative in (
            "src/octop/api/routers/workspace.py",
            "src/octop/api/common/workspace.py",
            "src/octop/api/middleware/project_task_file_gate.py",
            "src/octop/infra/backend/project_task_file_paths.py",
            "src/octop/infra/projects/file_tasks.py",
            "src/octop/config.py",
            "src/octop/infra/db/pool.py",
            "src/octop/infra/proactive/scheduler.py",
            "src/octop/infra/server.py",
            "tests/integration/test_project_task_file_real_runtime_http.py",
            "tests/support/app.py",
            "tests/support/auth.py",
            "tests/unit/agents/test_project_task_file_boundary.py",
        )
    ]
    files = [
        {"path": str(path), "bytes": len(path.read_bytes()), "sha256": digest(path)}
        for path in bindings
    ]
    (OUTPUT / "source-bindings.json").write_text(
        json.dumps({"source": SOURCE, "files": files}, indent=2), encoding="utf-8"
    )
    try:
        for label, read, stable in (
            ("stable-read", True, True),
            ("race-read", True, False),
            ("stable-write", False, True),
            ("race-write", False, False),
        ):
            if not await run_case(label, read, stable):
                raise RuntimeError(
                    "Stop this disposable diagnostic process after any inconclusive case"
                )
        assert len(report["cases"]) == 4
        assert all(
            state["exception"] is None
            and state["restore_error"] is None
            and state["stop_error"] is None
            and state["server_stop_completed"]
            for state in report["cases"]
        )
        report["cleanup"] = nonfollowing_reparse_inventory()
        assert not report["cleanup"]["remaining_reparse_points"]
        assert source_head() == SOURCE
        assert file_tasks.PROJECT_TASK_FILES_MODE_ENABLED is False
        assert {key: os.environ.get(key) for key in ENV_KEYS} == ENV_BEFORE
        assert workspace_routes.project_task_file_io_path is REAL_GUARD
        assert BackendWorkspace.materialize_local is REAL_MATERIALIZE
        assert FilesystemBackend._resolve_path is REAL_RESOLVE
        assert Path.open is REAL_PATH_OPEN and os.open is REAL_OS_OPEN
        assert OctopServer.start is REAL_START and OctopServer.stop is REAL_STOP
        assert SqlitePool.__init__ is REAL_SQLITE_INIT
        assert ChatModelFactory.get_chat_model is REAL_FACTORY
        assert all(
            getattr(ProactiveCareScheduler, name) is method
            for name, method in REAL_PROACTIVE.items()
        )
        for entry in files:
            path = Path(entry["path"])
            assert len(path.read_bytes()) == entry["bytes"] and digest(path) == entry["sha256"]
        report["source_and_patch_restoration_verified"] = True
        report["outcome"] = "COMPLETE_WITH_HTTP_CHECK_USE_ESCAPE"
    except BaseException:
        report["exception"] = traceback.format_exc()
        report["outcome"] = "DIAGNOSTIC_ERROR"
    finally:
        (OUTPUT / "http-race-result.json").write_text(
            json.dumps(report, indent=2), encoding="utf-8"
        )
    print("outcome", report["outcome"], flush=True)
    return 0 if report["outcome"] == "COMPLETE_WITH_HTTP_CHECK_USE_ESCAPE" else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
