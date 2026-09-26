"""030A B4 CLI offline-operation gates for internal project-task file runtimes.

Runs the real offline service stack (SQLite under ``tmp_octop_home``) so the
refusals are proven against real rows and real bytes: agent delete, thread
create/update/delete, cron create, channel create, and the dashboard-session
registry guard must all refuse a DB-marked ``project_task_files`` runtime
BEFORE any destructive or creating side effect, while ordinary agents keep
working exactly as before.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from click.testing import CliRunner

from octop.cli.main import cli
from octop.cli.support.db import open_cli_services
from octop.cli.support.offline_ops import (
    _ensure_dashboard_session,
    _install_registry_guards,
    create_channel_offline,
    create_cron_offline,
    create_thread_offline,
    delete_agent_offline,
    delete_thread_offline,
    list_cron_offline,
    update_thread_offline,
)
from octop.infra.db.repos.agents import AgentRepo
from octop.infra.db.repos.users import UserRepo
from octop.infra.errors import ErrorCode, OctopError
from octop.infra.gateway.threads import ThreadRegistry

INTERNAL_ID = "ptf-cli-1"
ORDINARY_ID = "ag-ordinary"
BOUND_THREAD = "thr_ptf_bound"


@pytest.fixture
def cli_env(tmp_octop_home: Path) -> Iterator[tuple[Path, int]]:
    result = CliRunner().invoke(
        cli,
        ["init", "--admin-username", "alice", "--admin-password", "TestPass12", "--yes"],
    )
    assert result.exit_code == 0, result.output
    with open_cli_services(home=tmp_octop_home) as svc:
        user = UserRepo(svc.db).get_by_username("alice")
        assert user is not None
        user_id = int(user.id)
        AgentRepo(svc.db).create(agent_id=ORDINARY_ID, user_id=user_id, name="ordinary-cli")
        AgentRepo(svc.db).create_project_task_runtime_with_quota(
            user_id=user_id, agent_id=INTERNAL_ID, name="task-file-cli-001"
        )
        root = svc.paths.ensure_project_task_file_runtime_dir(INTERNAL_ID)
        (root / "marker.txt").write_text("private bytes", encoding="utf-8")
        svc.thread_repo.insert(
            thread_id=BOUND_THREAD,
            agent_id=INTERNAL_ID,
            user_id=user_id,
            channel_type="dashboard",
            session_key=f"{INTERNAL_ID}:dashboard:{user_id}:dm",
            last_active=0,
        )
    yield tmp_octop_home, user_id


def _refused(excinfo: pytest.ExceptionInfo[OctopError]) -> None:
    assert excinfo.value.code == ErrorCode.FORBIDDEN
    assert excinfo.value.details == {"internal": True}


def test_delete_agent_offline_refuses_internal_runtime(cli_env: tuple[Path, int]) -> None:
    home, _uid = cli_env
    with pytest.raises(OctopError) as excinfo:
        delete_agent_offline(INTERNAL_ID, home=home)
    _refused(excinfo)
    with open_cli_services(home=home) as svc:
        # Row kept, managed private root and its bytes untouched — the refusal
        # fires before workspace resolution / rmtree / row delete.
        assert svc.agent_repo.get(INTERNAL_ID) is not None
        root = svc.paths.project_task_file_runtime_dir(INTERNAL_ID)
        assert (root / "marker.txt").read_text(encoding="utf-8") == "private bytes"


def test_delete_agent_offline_still_deletes_ordinary(cli_env: tuple[Path, int]) -> None:
    home, _uid = cli_env
    delete_agent_offline(ORDINARY_ID, home=home)
    with open_cli_services(home=home) as svc:
        assert svc.agent_repo.get(ORDINARY_ID) is None


def test_create_thread_offline_refuses_internal_runtime(cli_env: tuple[Path, int]) -> None:
    home, uid = cli_env
    with pytest.raises(OctopError) as excinfo:
        create_thread_offline(agent_id=INTERNAL_ID, user_id=uid, home=home)
    _refused(excinfo)
    with open_cli_services(home=home) as svc:
        rows = svc.thread_repo.list_by_agent(agent_id=INTERNAL_ID, limit=10)
        # Only the pre-seeded bound thread; no implicit CLI/dashboard thread.
        assert [r.thread_id for r in rows] == [BOUND_THREAD]


def test_update_thread_offline_refuses_internal_runtime(cli_env: tuple[Path, int]) -> None:
    home, _uid = cli_env
    with pytest.raises(OctopError) as excinfo:
        update_thread_offline(INTERNAL_ID, BOUND_THREAD, title="renamed", home=home)
    _refused(excinfo)
    with open_cli_services(home=home) as svc:
        row = svc.thread_repo.get(BOUND_THREAD)
        assert row is not None
        assert row.title != "renamed"


def test_delete_thread_offline_refuses_internal_runtime(cli_env: tuple[Path, int]) -> None:
    home, _uid = cli_env
    with pytest.raises(OctopError) as excinfo:
        delete_thread_offline(INTERNAL_ID, BOUND_THREAD, home=home)
    _refused(excinfo)
    with open_cli_services(home=home) as svc:
        # The bound thread survives: only the dedicated complete-delete route
        # may remove it.
        assert svc.thread_repo.get(BOUND_THREAD) is not None


def test_create_cron_offline_refuses_internal_runtime(cli_env: tuple[Path, int]) -> None:
    home, uid = cli_env
    with pytest.raises(OctopError) as excinfo:
        create_cron_offline(
            agent_id=INTERNAL_ID,
            user_id=uid,
            trigger="cron:0 9 * * *",
            prompt="daily file task",
            home=home,
        )
    _refused(excinfo)
    assert list_cron_offline(INTERNAL_ID, home=home) == []


def test_create_channel_offline_refuses_internal_runtime(cli_env: tuple[Path, int]) -> None:
    home, uid = cli_env
    with pytest.raises(OctopError) as excinfo:
        create_channel_offline(
            agent_id=INTERNAL_ID,
            user_id=uid,
            kind="telegram",
            name="alt-channel",
            config={},
            home=home,
        )
    _refused(excinfo)


def test_registry_guards_classify_rows_from_db(cli_env: tuple[Path, int]) -> None:
    home, _uid = cli_env
    with open_cli_services(home=home) as svc:
        registry = _install_registry_guards(
            ThreadRegistry(session_repo=svc.session_repo, thread_repo=svc.thread_repo),
            svc,
        )
        assert registry.is_internal_runtime_agent(INTERNAL_ID) is True
        assert registry.is_internal_runtime_agent(ORDINARY_ID) is False


async def test_ensure_dashboard_session_refuses_internal(cli_env: tuple[Path, int]) -> None:
    home, uid = cli_env
    with open_cli_services(home=home) as svc:
        with pytest.raises(OctopError) as excinfo:
            await _ensure_dashboard_session(
                svc,
                agent_id=INTERNAL_ID,
                user_id=uid,
                session_key=f"{INTERNAL_ID}:dashboard:{uid}:dm",
            )
        _refused(excinfo)
        assert svc.session_repo.get(f"{INTERNAL_ID}:dashboard:{uid}:dm") is None


def test_ordinary_thread_flows_keep_working(cli_env: tuple[Path, int]) -> None:
    home, uid = cli_env
    created: dict[str, Any] = create_thread_offline(agent_id=ORDINARY_ID, user_id=uid, home=home)
    tid = created["thread_id"]
    assert tid.startswith("thr_")
    updated = update_thread_offline(ORDINARY_ID, tid, title="ok", home=home)
    assert updated["title"] == "ok"
    delete_thread_offline(ORDINARY_ID, tid, home=home)
    with open_cli_services(home=home) as svc:
        assert svc.thread_repo.get(tid) is None
