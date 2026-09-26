"""030A B2: internal ``project_task_files`` runtime lifecycle in AgentManager.

Pins the dedicated creation path (B1 quota registration only, no
``AgentManager.create`` / ``defer_bootstrap``), the task-private managed root
derived from the trusted layout plus the internal runtime id (unsafe ids are
rejected, never basename-mangled), synchronous start with the M0 six-tool
boundary mounted at the real harness config build, source-capability
isolation (backend/skills/MCP/plugins/ACP/media/workspace bytes never
copied), failure compensation including uncertain harness removal and
partially created private roots, restart recovery, the bounded stale boot
cleanup with its per-row marker/age/thread re-check, the narrow
unlinked-cleanup entry for B3, connector / denylist hot-path refusal, and
ordinary-agent non-regression.

No real model is ever called; every test runs on ``tmp_path`` roots.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import stat
import time
from pathlib import Path
from typing import Any

import pytest
from harness_agent import HarnessAgent, HarnessAgentManager
from harness_agent.config import ModelConfig, ProviderConfig
from harness_agent.llm.factory import ChatModelFactory
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, ToolMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from pydantic import PrivateAttr

import octop.infra.agents.manager as manager_module
from octop.config import OctopConfig
from octop.infra.agents.manager import AgentCreateSpec, AgentManager
from octop.infra.agents.middleware.project_instructions import ProjectInstructionsMiddleware
from octop.infra.agents.middleware.token_quota import TokenQuotaMiddleware
from octop.infra.agents.project_task_file_boundary import (
    PROJECT_TASK_FILE_TOOLS,
    ProjectTaskFileToolBoundaryMiddleware,
    project_task_file_tools_disabled,
)
from octop.infra.db.migrate import run_migrations
from octop.infra.db.pool import SqlitePool
from octop.infra.db.repos.agents import RUNTIME_KIND_PROJECT_TASK_FILES, AgentRow
from octop.infra.db.repos.threads import ThreadRepo
from octop.infra.db.services import build_shared_services
from octop.infra.errors import ErrorCode, OctopError
from octop.infra.utils.paths import PathLayout

posix_only = pytest.mark.skipif(os.name != "posix", reason="POSIX permission bits only")

_MODEL_REF = "test-openai/gpt-4o-mini"
_SOURCE_PROMPT = "SOURCE-PERSONA-PROMPT: controlled file task persona."
_STALE_SECONDS = 24 * 60 * 60


# ---------------------------------------------------------------------------
# Recording-model plumbing (mirrors the M0 boundary probe; never hits network)
# ---------------------------------------------------------------------------


def _tool_name(tool: Any) -> str:
    if isinstance(tool, dict):
        fn = tool.get("function")
        if isinstance(fn, dict) and fn.get("name"):
            return str(fn["name"])
        return str(tool.get("name", ""))
    return str(getattr(tool, "name", "") or "")


class _RecordingChatModel(BaseChatModel):
    """Chat model recording the exact tools bound per model call."""

    scripts: list[AIMessage] = []
    invocations: list[list[str]] = []
    _pending: list[str] = PrivateAttr(default_factory=list)

    @property
    def _llm_type(self) -> str:
        return "octop-b2-recording"

    def bind_tools(self, tools: Any, *, tool_choice: Any = None, **kwargs: Any) -> Any:
        self._pending = [_tool_name(tool) for tool in tools]
        return self

    def _generate(
        self, messages: Any, stop: Any = None, run_manager: Any = None, **kwargs: Any
    ) -> ChatResult:
        self.invocations.append(list(self._pending))
        index = min(len(self.invocations) - 1, len(self.scripts) - 1)
        return ChatResult(generations=[ChatGeneration(message=self.scripts[index])])


class _RecordingFactory(ChatModelFactory):
    def __init__(self, model: _RecordingChatModel) -> None:
        super().__init__(
            [
                ProviderConfig(
                    id="fake",
                    base_url="http://127.0.0.1:1",
                    api_key="test-key",
                    models=[ModelConfig(id="m")],
                )
            ]
        )
        self._model = model

    def get_chat_model(self, model_ref: str) -> BaseChatModel:
        return self._model


# ---------------------------------------------------------------------------
# Fixtures & helpers
# ---------------------------------------------------------------------------


@pytest.fixture
def manager(tmp_path: Path) -> AgentManager:
    paths = PathLayout(tmp_path / ".octop")
    paths.ensure_root()
    db = SqlitePool(paths.db)
    run_migrations(db)
    services = build_shared_services(db=db, paths=paths, config=OctopConfig())
    return AgentManager(repos=services.repos, paths=services.paths)


def _seed_provider(manager: AgentManager) -> None:
    manager._repos.provider_repo.create(
        name="test-openai",
        kind="openai",
        base_url="https://api.example.com/v1",
        api_key="sk-test",
        models_json=json.dumps([{"id": "gpt-4o-mini", "name": "gpt-4o-mini", "enabled": True}]),
    )


def _boot_harness(manager: AgentManager) -> None:
    if manager._harness_manager is None:
        manager._harness_manager = HarnessAgentManager(
            providers=manager.providers.build_harness_configs(),
            log_dir=str(manager.paths.logs_dir),
        )


def _shutdown(manager: AgentManager, *agent_ids: str) -> None:
    """Quiesce memory maintenance before close (Windows/xdist close race)."""
    for agent_id in agent_ids:
        manager._quiesce_harness_memory(agent_id)
    harness = manager._harness_manager
    if harness is not None:
        harness.close()
        manager._harness_manager = None


def _make_user(manager: AgentManager, username: str) -> int:
    return manager._repos.user_repo.create(username=username, password_hash="h", role="user")


def _make_source_expert(
    manager: AgentManager,
    *,
    user_id: int | None,
    agent_id: str = "SRC01",
    name: str = "source-expert",
    config: dict[str, Any] | None = None,
    **kwargs: Any,
) -> AgentRow:
    manager._repos.agent_repo.create(
        agent_id=agent_id,
        user_id=user_id,
        name=name,
        default_model=kwargs.pop("default_model", _MODEL_REF),
        system_prompt=kwargs.pop("system_prompt", _SOURCE_PROMPT),
        icon_name="robot",
        color="#123456",
        config_json=json.dumps(config) if config is not None else None,
        mcp_servers=kwargs.pop("mcp_servers", json.dumps(["gmail"])),
        knowledge_base_ids=kwargs.pop("knowledge_base_ids", json.dumps(["kb-1"])),
        skill_package_ids=kwargs.pop("skill_package_ids", json.dumps(["pkg-1"])),
        **kwargs,
    )
    row = manager._repos.agent_repo.get(agent_id)
    assert row is not None
    return row


def _malicious_source_config(tmp_path: Path) -> dict[str, Any]:
    """Source config trying to leak every capability into the runtime."""
    outside = tmp_path / "outside-backend-root"
    outside.mkdir()
    (outside / "host-secret.txt").write_text("host-secret", encoding="utf-8")
    source_ws = tmp_path / "source-workspace"
    (source_ws / ".octop").mkdir(parents=True)
    (source_ws / "AGENTS.md").write_text("SOURCE WORKSPACE BYTE", encoding="utf-8")
    skills = tmp_path / "source-skills"
    skills.mkdir()
    (skills / "evil-skill.md").write_text("evil skill", encoding="utf-8")
    return {
        "backend": {"type": "local_shell", "root_dir": str(outside), "virtual_mode": False},
        "workspace_dir": str(source_ws),
        "skills": [str(skills)],
        "skill_package_ids": ["pkg-1"],
        "plugins": {"evil-plugin": {"enabled": True}},
        "mcp_servers": ["gmail"],
        "acp": {"runners": {"runner": {"command": "evil-acp"}}, "tool_enabled": True},
        "media": {"enabled": True, "provider": "evil"},
        "knowledge_base_ids": ["kb-1"],
        "connectors": {"gmail": {"oauth": True}},
        "browser": {"profiles_dir": str(tmp_path / "browser-profiles")},
        "security": {"hitl": {"enabled": False}},
        "memory": {"memory_enabled": True},
        "tools_disabled": [],
    }


def _internal_rows(manager: AgentManager) -> list[AgentRow]:
    return [
        row
        for row in manager._repos.agent_repo.list_all()
        if row.runtime_kind == RUNTIME_KIND_PROJECT_TASK_FILES
    ]


def _unwrap_backend(backend: Any) -> Any:
    return getattr(backend, "default", backend)


def _quiesce_agent_memory(agent: Any) -> None:
    """Best-effort stop of harness-memory GC before closing (segfault race)."""
    runtime = getattr(agent, "_memory_runtime", None)
    mw = getattr(runtime, "_middleware", None) if runtime is not None else None
    shutdown = getattr(mw, "shutdown", None) if mw is not None else None
    if callable(shutdown):
        with contextlib.suppress(Exception):
            shutdown()


async def _create_runtime(
    manager: AgentManager, *, owner_user_id: int, source: AgentRow
) -> AgentRow:
    _boot_harness(manager)
    return await manager.create_project_task_file_runtime(
        owner_user_id=owner_user_id, source_expert=source
    )


def _seed_thread(manager: AgentManager, *, agent_id: str, user_id: int, thread_id: str) -> None:
    ThreadRepo(manager._repos.thread_repo._db).insert(
        thread_id=thread_id,
        agent_id=agent_id,
        user_id=user_id,
        channel_type="dashboard",
        session_key=f"session-{thread_id}",
    )


def _age_row(manager: AgentManager, agent_id: str, *, created_at: int) -> None:
    with manager._repos.thread_repo._db.transaction() as conn:
        conn.execute("UPDATE agents SET created_at = ? WHERE agent_id = ?", (created_at, agent_id))


# ---------------------------------------------------------------------------
# Creation, registration and synchronous start
# ---------------------------------------------------------------------------


async def test_create_registers_internal_row_and_starts_synchronously(
    manager: AgentManager, tmp_path: Path
) -> None:
    _seed_provider(manager)
    uid = _make_user(manager, "owner")
    source = _make_source_expert(manager, user_id=uid)
    runtime_id: str | None = None
    try:
        row = await _create_runtime(manager, owner_user_id=uid, source=source)
        runtime_id = row.agent_id

        # B1-registered row shape: internal marker, owner-scoped, minimal.
        assert row.runtime_kind == RUNTIME_KIND_PROJECT_TASK_FILES
        assert row.user_id == uid
        assert row.enabled == 1
        assert row.kind == "expert"
        assert row.is_shared == 0
        assert row.config_json is None
        assert row.default_model == _MODEL_REF
        assert row.system_prompt == _SOURCE_PROMPT
        assert row.icon_name == "robot"
        assert row.color == "#123456"

        # Identity is freshly allocated: unguessable id, random non-colliding name.
        assert row.agent_id != source.agent_id
        assert row.name != source.name
        assert source.name not in row.name

        # Synchronously started — never deferred, never "starting".
        assert row.last_state == "running"
        agent = manager.get_agent(row.agent_id)
        assert manager.is_bootstrapped(row.agent_id) is True

        # Task-private managed root from the trusted layout + internal id.
        root = manager.paths.project_task_file_runtime_dir(row.agent_id)
        assert root == manager.paths.root / "project-task-files" / row.agent_id
        assert root.is_dir()
        assert manager.resolve_workspace_dir(row.agent_id) == root
        assert manager.paths.agents_dir not in root.parents
        assert not str(root).startswith(str(manager.paths.agents_dir))

        # config_json stays untouched by the start path.
        assert manager.get_row(row.agent_id).config_json is None
        assert manager.get_config(row.agent_id) == {}

        # No default workspace seeding / builtin skills / bootstrap files.
        assert not agent.workspace.exists("BOOTSTRAP.md")
        assert not agent.workspace.exists("AGENTS.md")
        assert not (root / "_builtin_skills").exists()
        assert not (root / ".octop" / "_builtin_skills").exists()

        # Real backend: pinned virtual FilesystemBackend without execute.
        backend = _unwrap_backend(agent.workspace.backend)
        assert type(backend).__name__ == "FilesystemBackend"
        assert Path(backend.cwd).resolve() == root.resolve()
        assert backend.virtual_mode is True
        assert not hasattr(backend, "execute")
        with pytest.raises(ValueError):
            backend._resolve_path("../../etc/passwd")

        # Strict config: boundary mounted last, prompt active, everything off.
        cfg = agent.config
        assert cfg.bootstrap_enabled is False
        assert cfg.system_prompt == _SOURCE_PROMPT
        assert isinstance(cfg.middleware[-1], ProjectTaskFileToolBoundaryMiddleware)
        # B2 GLM P2: the started chain keeps quota + project-instruction
        # middleware in the built order, ahead of the final boundary, and the
        # strict verifier accepts exactly this shape.
        assert [type(mw) for mw in cfg.middleware] == [
            TokenQuotaMiddleware,
            ProjectInstructionsMiddleware,
            ProjectTaskFileToolBoundaryMiddleware,
        ]
        assert manager._verify_project_task_runtime(row) is True
        assert project_task_file_tools_disabled().issubset(cfg.tools_disabled)
        assert PROJECT_TASK_FILE_TOOLS.isdisjoint(cfg.tools_disabled)
        assert cfg.mcp_server_configs == {}
        assert cfg.acp_runners == {}
        assert cfg.acp_delegate_enabled is False
        assert cfg.subagents is None
        assert cfg.subagents_auto_load is False
        assert cfg.subagents_path is None
        assert cfg.skills_dir is None
        assert cfg.media_generation is None
        assert cfg.team_enabled is False
        assert cfg.ask_user_enabled is False
        assert cfg.todos_enabled is False
        assert cfg.web_search_tools is False
        assert cfg.tools is None
        # No HITL gates: write_file/edit_file must run without approvals and a
        # forged execute must hit the boundary veto, not an interrupt card.
        assert not cfg.interrupt_on
        assert cfg.name == f"ptask_{row.agent_id}"
        assert cfg.memory_namespace == f"ptask_{row.agent_id}"
        assert cfg.backend == {
            "type": "filesystem",
            "root_dir": str(root),
            "virtual_mode": True,
        }
    finally:
        _shutdown(manager, *([runtime_id] if runtime_id else []))


@posix_only
async def test_private_root_created_with_0700_on_posix(
    manager: AgentManager, tmp_path: Path
) -> None:
    _seed_provider(manager)
    uid = _make_user(manager, "owner-perm")
    source = _make_source_expert(manager, user_id=uid, agent_id="SRC-PERM", name="src-perm")
    runtime_id: str | None = None
    try:
        row = await _create_runtime(manager, owner_user_id=uid, source=source)
        runtime_id = row.agent_id
        root = manager.paths.project_task_file_runtime_dir(row.agent_id)
        assert stat.S_IMODE(root.stat().st_mode) == 0o700
    finally:
        _shutdown(manager, *([runtime_id] if runtime_id else []))


def test_project_task_runtime_dir_rejects_unsafe_ids(tmp_path: Path) -> None:
    """A crafted runtime id must never escape the managed parent directory."""
    layout = PathLayout(tmp_path / ".octop")
    for unsafe in ("", ".", "..", "../escape", "..\\escape", "a/b", "a\\b", "/abs", ".\\x"):
        with pytest.raises(ValueError):
            layout.project_task_file_runtime_dir(unsafe)
        with pytest.raises(ValueError):
            layout.ensure_project_task_file_runtime_dir(unsafe)
    managed = layout.project_task_files_dir
    assert not managed.exists()
    # Internal generated ids keep resolving under the managed parent.
    ok = layout.project_task_file_runtime_dir("ptf0123456789ABCDEF")
    assert ok == managed / "ptf0123456789ABCDEF"
    assert ok.parent == managed


async def test_shared_expert_from_other_user_is_accepted(manager: AgentManager) -> None:
    _seed_provider(manager)
    author = _make_user(manager, "author")
    owner = _make_user(manager, "consumer")
    source = _make_source_expert(manager, user_id=author, agent_id="SRC-SH", name="shared-expert")
    manager._repos.agent_repo.set_shared(source.agent_id, True)
    source = manager._repos.agent_repo.get(source.agent_id)
    assert source is not None
    runtime_id: str | None = None
    try:
        row = await _create_runtime(manager, owner_user_id=owner, source=source)
        runtime_id = row.agent_id
        assert row.user_id == owner
        assert row.system_prompt == _SOURCE_PROMPT
    finally:
        _shutdown(manager, *([runtime_id] if runtime_id else []))


# ---------------------------------------------------------------------------
# Source-capability isolation
# ---------------------------------------------------------------------------


async def test_source_capabilities_never_enter_runtime_config_or_workspace(
    manager: AgentManager, tmp_path: Path
) -> None:
    _seed_provider(manager)
    uid = _make_user(manager, "owner-leak")
    config = _malicious_source_config(tmp_path)
    source = _make_source_expert(
        manager, user_id=uid, agent_id="SRC-LEAK", name="leaky-expert", config=config
    )
    runtime_id: str | None = None
    try:
        row = await _create_runtime(manager, owner_user_id=uid, source=source)
        runtime_id = row.agent_id
        agent = manager.get_agent(row.agent_id)
        cfg = agent.config
        root = manager.paths.project_task_file_runtime_dir(row.agent_id)

        # Backend pinned to the private root — never the source backend/root.
        assert cfg.backend["root_dir"] == str(root)
        backend = _unwrap_backend(agent.workspace.backend)
        assert Path(backend.cwd).resolve() == root.resolve()
        assert not (root / "host-secret.txt").exists()

        # No MCP / ACP / plugins / skills / media / knowledge from the source.
        assert cfg.mcp_server_configs == {}
        assert cfg.acp_runners == {}
        assert cfg.acp_delegate_enabled is False
        assert cfg.tools is None
        assert cfg.skills_dir is None
        assert cfg.media_generation is None
        assert not cfg.interrupt_on
        assert row.knowledge_base_ids is None
        assert row.skill_package_ids is None
        assert row.mcp_servers is None

        # Source workspace bytes were not copied; source row is untouched.
        assert not agent.workspace.exists("AGENTS.md")
        assert (tmp_path / "source-workspace" / "AGENTS.md").read_text(encoding="utf-8") == (
            "SOURCE WORKSPACE BYTE"
        )

        # The internal row persisted no config at all.
        stored = manager.get_row(row.agent_id)
        assert stored is not None
        assert stored.config_json is None
    finally:
        _shutdown(manager, *([runtime_id] if runtime_id else []))


async def test_built_config_exposes_six_tools_and_vetoes_forged_calls(
    manager: AgentManager, tmp_path: Path
) -> None:
    """Graph-level proof on the manager-built config: six visible, forged vetoed."""
    _seed_provider(manager)
    uid = _make_user(manager, "owner-graph")
    manager._repos.agent_repo.create_project_task_runtime_with_quota(
        user_id=uid,
        agent_id="ptfgraph01",
        name="task-graph-01",
        default_model=_MODEL_REF,
        system_prompt=_SOURCE_PROMPT,
    )
    row = manager.get_row("ptfgraph01")
    assert row is not None
    cfg = manager._build_harness_config(row)
    # HITL would intercept the forged ``execute`` in after_model BEFORE the
    # boundary's wrap_tool_call veto, and would gate write_file/edit_file —
    # the strict config must carry no interrupt entries at all.
    assert not cfg.interrupt_on

    model = _RecordingChatModel(
        scripts=[
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "task",
                        "args": {"description": "escape"},
                        "id": "call_task",
                        "type": "tool_call",
                    },
                    {
                        "name": "execute",
                        "args": {"command": "rm -rf /"},
                        "id": "call_exec",
                        "type": "tool_call",
                    },
                    {
                        "name": "web_fetch",
                        "args": {"url": "https://evil.example"},
                        "id": "call_web",
                        "type": "tool_call",
                    },
                ],
            ),
            AIMessage(content="done"),
        ]
    )
    probe = HarnessAgent(cfg, model_factory=_RecordingFactory(model))
    try:
        result = await probe.graph.ainvoke(
            {"messages": [{"role": "user", "content": "hi"}]},
            config={"configurable": {"thread_id": "probe-1"}},
        )
    finally:
        _quiesce_agent_memory(probe)
        await probe.aclose()

    assert model.invocations
    assert set(model.invocations[0]) == set(PROJECT_TASK_FILE_TOOLS)
    tool_messages = [m for m in result["messages"] if isinstance(m, ToolMessage)]
    assert {m.name for m in tool_messages} == {"task", "execute", "web_fetch"}
    assert all(m.status == "error" for m in tool_messages)
    assert all("not available" in str(m.content) for m in tool_messages)
    # The private root was created but nothing escaped into it from forged calls.
    root = manager.paths.project_task_file_runtime_dir(row.agent_id)
    assert root.is_dir()


# ---------------------------------------------------------------------------
# Strict post-start verification of the required middleware (B2 GLM P2)
# ---------------------------------------------------------------------------


async def test_verify_rejects_missing_or_misplaced_required_middleware(
    manager: AgentManager,
) -> None:
    """A started runtime that lost quota/instructions or its final boundary
    must never verify as task-capable — each mutation fails closed."""
    _seed_provider(manager)
    uid = _make_user(manager, "owner-verify")
    source = _make_source_expert(manager, user_id=uid, agent_id="SRC-VER", name="src-ver")
    row = await _create_runtime(manager, owner_user_id=uid, source=source)
    runtime_id = row.agent_id
    try:
        agent = manager.get_agent(runtime_id)
        original = list(agent.config.middleware)
        quota, instructions, guard = original
        assert type(quota) is TokenQuotaMiddleware
        assert type(instructions) is ProjectInstructionsMiddleware
        assert type(guard) is ProjectTaskFileToolBoundaryMiddleware
        assert manager._verify_project_task_runtime(row) is True

        cases: dict[str, list[Any]] = {
            "quota removed": [instructions, guard],
            "instructions removed": [quota, guard],
            "boundary not last": [guard, quota, instructions],
            "quota replaced by a second instructions": [instructions, instructions, guard],
            "instructions replaced by a second quota": [quota, quota, guard],
            "quota duplicated": [quota, quota, instructions, guard],
            "instructions duplicated": [quota, instructions, instructions, guard],
            "boundary duplicated": [quota, instructions, guard, guard],
            "everything removed": [],
        }
        try:
            for label, middleware in cases.items():
                agent.config.middleware = middleware
                assert manager._verify_project_task_runtime(row) is False, label
            # The relative order of quota vs project instructions is NOT
            # pinned: they hook disjoint harness phases (before_agent vs
            # wrap_model_call), so either order enforces identically as long
            # as the boundary stays the sole, final entry.
            agent.config.middleware = [instructions, quota, guard]
            assert manager._verify_project_task_runtime(row) is True
        finally:
            agent.config.middleware = original
        assert manager._verify_project_task_runtime(row) is True
    finally:
        _shutdown(manager, runtime_id)


async def test_middleware_loss_after_start_compensates_creation(
    manager: AgentManager, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Middleware stripped between start and strict verification must take the
    existing creation compensation path: no task-capable row, harness runtime
    or private root survives, and the quota seat is released."""
    _seed_provider(manager)
    uid = _make_user(manager, "owner-mw-loss")
    source = _make_source_expert(manager, user_id=uid, agent_id="SRC-MWL", name="src-mwl")
    _boot_harness(manager)
    started_ids: list[str] = []
    real_start = manager._start_agent

    async def _start_then_drop_quota(row: AgentRow, *, init_workspace: bool = True) -> Any:
        agent = await real_start(row, init_workspace=init_workspace)
        if agent is not None:
            started_ids.append(row.agent_id)
            # Simulate a policy/harness transformation removing the quota gate
            # after start but before the strict verification runs.
            agent.config.middleware = [
                mw for mw in agent.config.middleware if not isinstance(mw, TokenQuotaMiddleware)
            ]
        return agent

    monkeypatch.setattr(manager, "_start_agent", _start_then_drop_quota)
    try:
        with pytest.raises(OctopError) as excinfo:
            await manager.create_project_task_file_runtime(owner_user_id=uid, source_expert=source)
        assert excinfo.value.code is ErrorCode.AGENT_FAILED
        assert len(started_ids) == 1
        runtime_id = started_ids[0]
        # Full proven compensation through the existing dedicated path.
        assert _internal_rows(manager) == []
        assert manager.get_row(runtime_id) is None
        assert manager._harness_agent_or_none(runtime_id) is None
        assert not manager.paths.project_task_file_runtime_dir(runtime_id).exists()
        managed = manager.paths.project_task_files_dir
        assert not managed.exists() or list(managed.iterdir()) == []
    finally:
        _shutdown(manager)


# ---------------------------------------------------------------------------
# Source validation (defense in depth behind B3's ACL check)
# ---------------------------------------------------------------------------


async def test_create_rejects_invalid_source_rows(manager: AgentManager, tmp_path: Path) -> None:
    _seed_provider(manager)
    uid = _make_user(manager, "owner-reject")
    other = _make_user(manager, "other-reject")

    _make_source_expert(manager, user_id=uid, agent_id="SRC-INT", name="src-int")
    manager._repos.agent_repo.create_project_task_runtime_with_quota(
        user_id=uid, agent_id="ptfinternal1", name="task-internal-1"
    )
    internal_row = manager.get_row("ptfinternal1")
    assert internal_row is not None

    disabled = _make_source_expert(manager, user_id=uid, agent_id="SRC-DIS", name="src-dis")
    manager._repos.agent_repo.set_enabled(disabled.agent_id, False)
    disabled_row = manager.get_row(disabled.agent_id)
    assert disabled_row is not None

    team = _make_source_expert(manager, user_id=uid, agent_id="SRC-TEAM", name="src-team")
    with manager._repos.thread_repo._db.transaction() as conn:
        conn.execute("UPDATE agents SET kind = 'team' WHERE agent_id = ?", (team.agent_id,))
    team_row = manager.get_row(team.agent_id)
    assert team_row is not None

    no_model = _make_source_expert(
        manager, user_id=uid, agent_id="SRC-NOM", name="src-nom", default_model=None
    )
    auto_model = _make_source_expert(
        manager, user_id=uid, agent_id="SRC-AUTO", name="src-auto", default_model="auto"
    )
    dead_model = _make_source_expert(
        manager, user_id=uid, agent_id="SRC-DEAD", name="src-dead", default_model="gone/model"
    )
    foreign = _make_source_expert(manager, user_id=other, agent_id="SRC-FOR", name="src-for")
    ok_source = _make_source_expert(manager, user_id=uid, agent_id="SRC-OK2", name="src-ok2")

    baseline = len(_internal_rows(manager))
    cases: list[tuple[str, AgentRow, int]] = [
        ("internal source", internal_row, uid),
        ("disabled source", disabled_row, uid),
        ("team source", team_row, uid),
        ("missing model", no_model, uid),
        ("auto model", auto_model, uid),
        ("unusable model", dead_model, uid),
        ("foreign unshared source", foreign, uid),
        ("unknown owner", ok_source, 999999),
    ]
    _boot_harness(manager)
    try:
        for label, source_row, owner_user_id in cases:
            with pytest.raises(OctopError) as excinfo:
                await manager.create_project_task_file_runtime(
                    owner_user_id=owner_user_id, source_expert=source_row
                )
            assert excinfo.value.code is ErrorCode.FORBIDDEN, label
        # No runtime row or private directory was created for any refusal.
        assert len(_internal_rows(manager)) == baseline
        managed = manager.paths.project_task_files_dir
        assert not managed.exists() or list(managed.iterdir()) == []
    finally:
        _shutdown(manager)


# ---------------------------------------------------------------------------
# Failed start compensation
# ---------------------------------------------------------------------------


async def test_start_failure_compensates_row_and_directory(
    manager: AgentManager, monkeypatch: pytest.MonkeyPatch
) -> None:
    _seed_provider(manager)
    uid = _make_user(manager, "owner-fail")
    source = _make_source_expert(manager, user_id=uid, agent_id="SRC-FAIL", name="src-fail")
    _boot_harness(manager)

    def _raiser(row: AgentRow) -> Any:
        raise RuntimeError("boom: simulated harness config failure")

    monkeypatch.setattr(manager, "_agent_runtime_bundle", _raiser)
    try:
        with pytest.raises(OctopError) as excinfo:
            await manager.create_project_task_file_runtime(owner_user_id=uid, source_expert=source)
        assert excinfo.value.code is ErrorCode.AGENT_FAILED
        # Fully compensated: no row, no directory, quota seat released.
        assert _internal_rows(manager) == []
        managed = manager.paths.project_task_files_dir
        assert not managed.exists() or list(managed.iterdir()) == []
    finally:
        _shutdown(manager)


async def test_start_failure_keeps_retryable_row_when_directory_removal_fails(
    manager: AgentManager, monkeypatch: pytest.MonkeyPatch
) -> None:
    _seed_provider(manager)
    uid = _make_user(manager, "owner-retry")
    source = _make_source_expert(manager, user_id=uid, agent_id="SRC-RET", name="src-ret")
    _boot_harness(manager)

    def _raiser(row: AgentRow) -> Any:
        raise RuntimeError("boom: simulated start failure")

    monkeypatch.setattr(manager, "_agent_runtime_bundle", _raiser)
    fail_rmtree = {"active": True}
    real_rmtree = manager_module.shutil.rmtree

    def _fake_rmtree(path: Any, *args: Any, **kwargs: Any) -> None:
        if fail_rmtree["active"]:
            raise OSError("simulated directory removal failure")
        real_rmtree(path, *args, **kwargs)

    monkeypatch.setattr(manager_module.shutil, "rmtree", _fake_rmtree)
    try:
        with pytest.raises(OctopError) as excinfo:
            await manager.create_project_task_file_runtime(owner_user_id=uid, source_expert=source)
        assert excinfo.value.code is ErrorCode.AGENT_FAILED
        # Directory removal failed → the DB record is KEPT for retry, marked failed.
        leftovers = _internal_rows(manager)
        assert len(leftovers) == 1
        kept = leftovers[0]
        assert kept.last_state == "failed"
        root = manager.paths.project_task_file_runtime_dir(kept.agent_id)
        assert root.is_dir()

        # Retryable: once removal works, the narrow cleanup deletes both.
        fail_rmtree["active"] = False
        assert await manager.cleanup_unlinked_project_task_runtime(kept.agent_id) is True
        assert _internal_rows(manager) == []
        assert not root.exists()
    finally:
        fail_rmtree["active"] = False
        _shutdown(manager)


async def test_harness_removal_failure_keeps_retryable_row_and_root(
    manager: AgentManager, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Uncertain harness removal never drops the marker/root nor reports success."""
    _seed_provider(manager)
    uid = _make_user(manager, "owner-hrm")
    source = _make_source_expert(manager, user_id=uid, agent_id="SRC-HRM", name="src-hrm")
    row = await _create_runtime(manager, owner_user_id=uid, source=source)
    runtime_id = row.agent_id
    root = manager.paths.project_task_file_runtime_dir(runtime_id)
    harness = manager._harness_manager
    assert harness is not None
    real_aremove = harness.aremove_agent
    fail_removal = {"active": True}

    async def _fake_aremove(agent_id: str) -> None:
        if fail_removal["active"]:
            raise RuntimeError("simulated harness removal failure")
        await real_aremove(agent_id)

    monkeypatch.setattr(harness, "aremove_agent", _fake_aremove)
    try:
        # The live harness may still hold the private root: keep everything,
        # report failure, and never claim the runtime is gone (or stopped).
        assert await manager.cleanup_unlinked_project_task_runtime(runtime_id) is False
        kept = manager.get_row(runtime_id)
        assert kept is not None
        assert kept.runtime_kind == RUNTIME_KIND_PROJECT_TASK_FILES
        assert kept.last_state == "failed"
        assert root.is_dir()

        # Once removal works, the narrow cleanup retries and removes both.
        fail_removal["active"] = False
        assert await manager.cleanup_unlinked_project_task_runtime(runtime_id) is True
        assert manager.get_row(runtime_id) is None
        assert _internal_rows(manager) == []
        assert not root.exists()
    finally:
        fail_removal["active"] = False
        _shutdown(manager)


async def test_db_delete_failure_marks_unlinked_runtime_failed(
    manager: AgentManager, monkeypatch: pytest.MonkeyPatch
) -> None:
    _seed_provider(manager)
    uid = _make_user(manager, "owner-delete-fail")
    source = _make_source_expert(manager, user_id=uid, agent_id="SRC-DEL", name="src-del")
    row = await _create_runtime(manager, owner_user_id=uid, source=source)
    root = manager.paths.project_task_file_runtime_dir(row.agent_id)

    def _fail_delete(agent_id: str) -> None:
        raise RuntimeError(f"simulated DB deletion failure: {agent_id}")

    try:
        with monkeypatch.context() as patcher:
            patcher.setattr(manager._repos.agent_repo, "delete", _fail_delete)
            assert await manager.cleanup_unlinked_project_task_runtime(row.agent_id) is False
        kept = manager.get_row(row.agent_id)
        assert kept is not None
        assert kept.last_state == "failed"
        assert not root.exists()
        assert manager._harness_agent_or_none(row.agent_id) is None

        # A young failed row cannot become task-capable on restart.
        manager._repos.agent_repo.set_state(source.agent_id, "stopped")
        _shutdown(manager)
        restarted = _rebuild_manager(manager)
        try:
            await restarted.boot()
            recovered = restarted.get_row(row.agent_id)
            assert recovered is not None and recovered.last_state == "failed"
            assert restarted._harness_agent_or_none(row.agent_id) is None
            assert not root.exists()
        finally:
            _shutdown(restarted)
    finally:
        _shutdown(manager)


async def test_audit_failure_compensates_verified_runtime(
    manager: AgentManager, monkeypatch: pytest.MonkeyPatch
) -> None:
    _seed_provider(manager)
    uid = _make_user(manager, "owner-audit-fail")
    source = _make_source_expert(manager, user_id=uid, agent_id="SRC-AUD", name="src-aud")
    _boot_harness(manager)
    created_id: list[str] = []

    def _fail_audit(*, actor: str, action: str, target: str, payload: str) -> None:
        created_id.append(target)
        raise RuntimeError("simulated audit write failure")

    monkeypatch.setattr(manager._repos.audit_repo, "write", _fail_audit)
    try:
        with pytest.raises(OctopError) as excinfo:
            await manager.create_project_task_file_runtime(owner_user_id=uid, source_expert=source)
        assert excinfo.value.code is ErrorCode.AGENT_FAILED
        assert len(created_id) == 1
        assert manager.get_row(created_id[0]) is None
        assert manager._harness_agent_or_none(created_id[0]) is None
        assert not manager.paths.project_task_file_runtime_dir(created_id[0]).exists()
    finally:
        _shutdown(manager)


async def test_final_row_disappearance_compensates_verified_runtime(
    manager: AgentManager, monkeypatch: pytest.MonkeyPatch
) -> None:
    _seed_provider(manager)
    uid = _make_user(manager, "owner-final-row")
    source = _make_source_expert(manager, user_id=uid, agent_id="SRC-FINAL", name="src-final")
    _boot_harness(manager)
    created_id: list[str] = []

    def _remove_row_after_audit(*, actor: str, action: str, target: str, payload: str) -> None:
        created_id.append(target)
        manager._repos.agent_repo.delete(target)

    monkeypatch.setattr(manager._repos.audit_repo, "write", _remove_row_after_audit)
    try:
        with pytest.raises(OctopError) as excinfo:
            await manager.create_project_task_file_runtime(owner_user_id=uid, source_expert=source)
        assert excinfo.value.code is ErrorCode.AGENT_FAILED
        assert len(created_id) == 1
        assert manager._harness_agent_or_none(created_id[0]) is None
        assert not manager.paths.project_task_file_runtime_dir(created_id[0]).exists()
    finally:
        _shutdown(manager)


async def test_root_creation_failure_removes_partial_directory(
    manager: AgentManager, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A failed root creation runs the compensation: no orphan, no leftover row."""
    _seed_provider(manager)
    uid = _make_user(manager, "owner-mkdir")
    source = _make_source_expert(manager, user_id=uid, agent_id="SRC-MK", name="src-mk")
    _boot_harness(manager)

    def _partial_ensure(self: PathLayout, agent_id: str) -> Path:
        # Simulate a partially created private root whose setup then fails.
        root = self.project_task_file_runtime_dir(agent_id)
        root.mkdir(parents=True, exist_ok=True)
        (root / "partial.txt").write_text("partial", encoding="utf-8")
        raise OSError("simulated private root creation failure")

    monkeypatch.setattr(PathLayout, "ensure_project_task_file_runtime_dir", _partial_ensure)
    try:
        with pytest.raises(OctopError) as excinfo:
            await manager.create_project_task_file_runtime(owner_user_id=uid, source_expert=source)
        assert excinfo.value.code is ErrorCode.AGENT_FAILED
        # The partial directory was compensated, not orphaned; quota seat freed.
        assert _internal_rows(manager) == []
        managed = manager.paths.project_task_files_dir
        assert not managed.exists() or list(managed.iterdir()) == []
    finally:
        _shutdown(manager)


async def test_root_creation_failure_keeps_failed_row_when_dir_removal_fails(
    manager: AgentManager, monkeypatch: pytest.MonkeyPatch
) -> None:
    """When even the compensation cannot remove the dir, keep a retryable row."""
    _seed_provider(manager)
    uid = _make_user(manager, "owner-mkdir2")
    source = _make_source_expert(manager, user_id=uid, agent_id="SRC-MK2", name="src-mk2")
    _boot_harness(manager)

    def _partial_ensure(self: PathLayout, agent_id: str) -> Path:
        root = self.project_task_file_runtime_dir(agent_id)
        root.mkdir(parents=True, exist_ok=True)
        raise OSError("simulated private root creation failure")

    monkeypatch.setattr(PathLayout, "ensure_project_task_file_runtime_dir", _partial_ensure)
    fail_rmtree = {"active": True}
    real_rmtree = manager_module.shutil.rmtree

    def _fake_rmtree(path: Any, *args: Any, **kwargs: Any) -> None:
        if fail_rmtree["active"]:
            raise OSError("simulated directory removal failure")
        real_rmtree(path, *args, **kwargs)

    monkeypatch.setattr(manager_module.shutil, "rmtree", _fake_rmtree)
    try:
        with pytest.raises(OctopError) as excinfo:
            await manager.create_project_task_file_runtime(owner_user_id=uid, source_expert=source)
        assert excinfo.value.code is ErrorCode.AGENT_FAILED
        # No false success: the partial root is kept, tracked by a failed row.
        leftovers = _internal_rows(manager)
        assert len(leftovers) == 1
        kept = leftovers[0]
        assert kept.last_state == "failed"
        root = manager.paths.project_task_file_runtime_dir(kept.agent_id)
        assert root.is_dir()

        # Bounded retry: once removal works, the narrow cleanup deletes both.
        fail_rmtree["active"] = False
        assert await manager.cleanup_unlinked_project_task_runtime(kept.agent_id) is True
        assert _internal_rows(manager) == []
        assert not root.exists()
    finally:
        fail_rmtree["active"] = False
        _shutdown(manager)


# ---------------------------------------------------------------------------
# Restart recovery and stale boot cleanup
# ---------------------------------------------------------------------------


def _rebuild_manager(manager: AgentManager) -> AgentManager:
    """Fresh AgentManager over the same DB file + layout (simulated restart)."""
    db = SqlitePool(manager.paths.db)
    services = build_shared_services(db=db, paths=manager.paths, config=OctopConfig())
    return AgentManager(repos=services.repos, paths=services.paths)


async def test_restart_recovers_internal_runtime_with_same_root(
    manager: AgentManager, tmp_path: Path
) -> None:
    _seed_provider(manager)
    uid = _make_user(manager, "owner-restart")
    source = _make_source_expert(manager, user_id=uid, agent_id="SRC-RST", name="src-rst")
    row = await _create_runtime(manager, owner_user_id=uid, source=source)
    runtime_id = row.agent_id
    root = manager.paths.project_task_file_runtime_dir(runtime_id)
    (root / "task-notes.md").write_text("persisted task bytes", encoding="utf-8")
    # Keep the restart focused: the source expert stays out of boot.
    manager._repos.agent_repo.set_state(source.agent_id, "stopped")
    _seed_thread(manager, agent_id=runtime_id, user_id=uid, thread_id="th-restart-linked")
    _shutdown(manager, runtime_id)

    manager2 = _rebuild_manager(manager)
    try:
        await manager2.boot()
        row2 = manager2.get_row(runtime_id)
        assert row2 is not None
        assert row2.last_state == "running"
        agent = manager2.get_agent(runtime_id)

        # Same deterministic private root, bytes preserved.
        assert manager2.resolve_workspace_dir(runtime_id) == root
        assert (root / "task-notes.md").read_text(encoding="utf-8") == "persisted task bytes"

        # Same strict config after restart.
        cfg = agent.config
        assert cfg.bootstrap_enabled is False
        assert cfg.system_prompt == _SOURCE_PROMPT
        assert isinstance(cfg.middleware[-1], ProjectTaskFileToolBoundaryMiddleware)
        assert project_task_file_tools_disabled().issubset(cfg.tools_disabled)
        assert cfg.mcp_server_configs == {}
        assert not cfg.interrupt_on
        backend = _unwrap_backend(agent.workspace.backend)
        assert type(backend).__name__ == "FilesystemBackend"
        assert Path(backend.cwd).resolve() == root.resolve()
        assert backend.virtual_mode is True
        assert not hasattr(backend, "execute")
    finally:
        _shutdown(manager2, runtime_id)


async def test_boot_cleanup_removes_only_stale_unreferenced_internal_runtimes(
    manager: AgentManager, tmp_path: Path
) -> None:
    _seed_provider(manager)
    uid = _make_user(manager, "owner-scan")
    now = int(time.time())
    stale = now - _STALE_SECONDS - 600

    def _seed_internal(agent_id: str, name: str) -> None:
        manager._repos.agent_repo.create_project_task_runtime_with_quota(
            user_id=uid, agent_id=agent_id, name=name
        )

    _seed_internal("ptfstale01", "task-stale-01")
    _seed_internal("ptflinked01", "task-linked-01")
    _seed_internal("ptffresh01", "task-fresh-01")
    for agent_id in ("ptfstale01", "ptflinked01"):
        _age_row(manager, agent_id, created_at=stale)
        manager.paths.ensure_project_task_file_runtime_dir(agent_id)
    (manager.paths.project_task_file_runtime_dir("ptfstale01") / "leftover.txt").write_text(
        "x", encoding="utf-8"
    )
    manager.paths.ensure_project_task_file_runtime_dir("ptffresh01")
    _seed_thread(manager, agent_id="ptflinked01", user_id=uid, thread_id="th-linked-1")

    # An old ordinary agent with no threads must never be scanned or deleted.
    manager._repos.agent_repo.create(agent_id="STD-OLD", user_id=uid, name="std-old")
    _age_row(manager, "STD-OLD", created_at=stale)
    std_ws = manager.paths.ensure_agent_workspace("STD-OLD")
    (std_ws / "keep.md").write_text("user data", encoding="utf-8")

    removed = await manager.cleanup_stale_project_task_runtimes()

    assert removed == 1
    assert manager.get_row("ptfstale01") is None
    assert not manager.paths.project_task_file_runtime_dir("ptfstale01").exists()
    # Linked (private thread) internal runtime survives even detached from projects.
    assert manager.get_row("ptflinked01") is not None
    assert manager.paths.project_task_file_runtime_dir("ptflinked01").is_dir()
    # Fresh unreferenced runtime survives (24h grace).
    assert manager.get_row("ptffresh01") is not None
    # Ordinary agent and its workspace untouched.
    assert manager.get_row("STD-OLD") is not None
    assert (std_ws / "keep.md").read_text(encoding="utf-8") == "user data"


async def test_boot_cleanup_rechecks_marker_age_and_threads_per_row(
    manager: AgentManager, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A row that changes between the scan and its turn is skipped, not deleted."""
    _seed_provider(manager)
    uid = _make_user(manager, "owner-race")
    now = int(time.time())
    stale = now - _STALE_SECONDS - 600
    for agent_id in ("ptfraceold", "ptfracemark", "ptfraceyoung", "ptfracethread"):
        manager._repos.agent_repo.create_project_task_runtime_with_quota(
            user_id=uid, agent_id=agent_id, name=f"task-{agent_id}"
        )
        _age_row(manager, agent_id, created_at=stale)
        manager.paths.ensure_project_task_file_runtime_dir(agent_id)

    real_list = manager._repos.agent_repo.list_unreferenced_project_task_runtimes

    def _racy_list(*, created_before: int, limit: int) -> list[AgentRow]:
        rows = real_list(created_before=created_before, limit=limit)
        # Race: every selected row changes before the per-row check runs — it
        # loses the internal marker, becomes young again or gains a thread.
        with manager._repos.thread_repo._db.transaction() as conn:
            conn.execute(
                "UPDATE agents SET runtime_kind = 'standard' WHERE agent_id = 'ptfracemark'"
            )
        _age_row(manager, "ptfraceyoung", created_at=now)
        _seed_thread(manager, agent_id="ptfracethread", user_id=uid, thread_id="th-race-1")
        return rows

    monkeypatch.setattr(
        manager._repos.agent_repo, "list_unreferenced_project_task_runtimes", _racy_list
    )

    removed = await manager.cleanup_stale_project_task_runtimes()

    assert removed == 1
    # The unchanged stale row was fully removed.
    assert manager.get_row("ptfraceold") is None
    assert not manager.paths.project_task_file_runtime_dir("ptfraceold").exists()
    # Lost the internal marker → now a standard row; never touched.
    assert manager.get_row("ptfracemark") is not None
    assert manager.paths.project_task_file_runtime_dir("ptfracemark").is_dir()
    # Became young again → the 24h grace restarts; never touched.
    assert manager.get_row("ptfraceyoung") is not None
    assert manager.paths.project_task_file_runtime_dir("ptfraceyoung").is_dir()
    # Gained a private thread → never deleted.
    assert manager.get_row("ptfracethread") is not None
    assert manager.paths.project_task_file_runtime_dir("ptfracethread").is_dir()


async def test_boot_runs_bounded_stale_cleanup(manager: AgentManager) -> None:
    _seed_provider(manager)
    uid = _make_user(manager, "owner-boot-scan")
    stale = int(time.time()) - _STALE_SECONDS - 60
    manager._repos.agent_repo.create_project_task_runtime_with_quota(
        user_id=uid, agent_id="ptfboot01", name="task-boot-01"
    )
    _age_row(manager, "ptfboot01", created_at=stale)
    manager.paths.ensure_project_task_file_runtime_dir("ptfboot01")

    manager2 = _rebuild_manager(manager)
    try:
        await manager2.boot()
        assert manager2.get_row("ptfboot01") is None
        assert not manager2.paths.project_task_file_runtime_dir("ptfboot01").exists()
    finally:
        _shutdown(manager2)


async def test_boot_does_not_restart_young_failed_unlinked_runtime(manager: AgentManager) -> None:
    _seed_provider(manager)
    uid = _make_user(manager, "owner-young-failed")
    source = _make_source_expert(manager, user_id=uid, agent_id="SRC-YOUNG", name="src-young")
    row = await _create_runtime(manager, owner_user_id=uid, source=source)
    manager._repos.agent_repo.set_state(row.agent_id, "failed", error="incomplete cleanup")
    manager._repos.agent_repo.set_state(source.agent_id, "stopped")
    root = manager.paths.project_task_file_runtime_dir(row.agent_id)
    _shutdown(manager, row.agent_id)

    manager2 = _rebuild_manager(manager)
    try:
        await manager2.boot()
        kept = manager2.get_row(row.agent_id)
        assert kept is not None and kept.last_state == "failed"
        assert manager2._harness_agent_or_none(row.agent_id) is None
        assert root.is_dir()
    finally:
        _shutdown(manager2)


async def test_stale_cleanup_is_bounded_per_boot(manager: AgentManager) -> None:
    _seed_provider(manager)
    uid = _make_user(manager, "owner-bound")
    stale = int(time.time()) - _STALE_SECONDS - 60
    total = 35
    with manager._repos.thread_repo._db.transaction() as conn:
        for i in range(total):
            conn.execute(
                "INSERT INTO agents(agent_id, user_id, name, runtime_kind, created_at, updated_at) "
                "VALUES (?, ?, ?, 'project_task_files', ?, ?)",
                (f"ptfb{i:03}", uid, f"task-b-{i}", stale - i, stale - i),
            )
    for i in range(total):
        manager.paths.ensure_project_task_file_runtime_dir(f"ptfb{i:03}")

    removed_first = await manager.cleanup_stale_project_task_runtimes()
    assert removed_first == manager_module._PROJECT_TASK_BOOT_CLEANUP_LIMIT == 32
    remaining = len(_internal_rows(manager))
    assert remaining == total - removed_first

    removed_second = await manager.cleanup_stale_project_task_runtimes()
    assert removed_second == remaining
    assert _internal_rows(manager) == []


# ---------------------------------------------------------------------------
# Narrow unlinked cleanup for B3's failed transactions
# ---------------------------------------------------------------------------


async def test_cleanup_unlinked_runtime_refuses_linked_and_standard_rows(
    manager: AgentManager, tmp_path: Path
) -> None:
    _seed_provider(manager)
    uid = _make_user(manager, "owner-unlink")

    # Standard agent: never a cleanup target, workspace never touched.
    std = _make_source_expert(manager, user_id=uid, agent_id="STD-KEEP", name="std-keep")
    std_ws = manager.paths.ensure_agent_workspace(std.agent_id)
    (std_ws / "keep.md").write_text("user data", encoding="utf-8")
    with pytest.raises(OctopError) as std_exc:
        await manager.cleanup_unlinked_project_task_runtime(std.agent_id)
    assert std_exc.value.code is ErrorCode.FORBIDDEN
    assert manager.get_row(std.agent_id) is not None
    assert (std_ws / "keep.md").is_file()

    # Internal runtime WITH a private thread: refused even though it exists.
    manager._repos.agent_repo.create_project_task_runtime_with_quota(
        user_id=uid, agent_id="ptflinked1", name="task-linked-x"
    )
    manager.paths.ensure_project_task_file_runtime_dir("ptflinked1")
    _seed_thread(manager, agent_id="ptflinked1", user_id=uid, thread_id="th-keep-1")
    with pytest.raises(OctopError) as linked_exc:
        await manager.cleanup_unlinked_project_task_runtime("ptflinked1")
    assert linked_exc.value.code is ErrorCode.FORBIDDEN
    assert manager.get_row("ptflinked1") is not None
    assert manager.paths.project_task_file_runtime_dir("ptflinked1").is_dir()

    # Unknown id: idempotent no-op.
    assert await manager.cleanup_unlinked_project_task_runtime("ptfmissing0") is False

    # Unlinked internal runtime: removed with its directory.
    manager._repos.agent_repo.create_project_task_runtime_with_quota(
        user_id=uid, agent_id="ptfunlink1", name="task-unlink-1"
    )
    root = manager.paths.ensure_project_task_file_runtime_dir("ptfunlink1")
    assert await manager.cleanup_unlinked_project_task_runtime("ptfunlink1") is True
    assert manager.get_row("ptfunlink1") is None
    assert not root.exists()


async def test_cleanup_rechecks_internal_marker_after_lock_wait(manager: AgentManager) -> None:
    _seed_provider(manager)
    uid = _make_user(manager, "owner-marker-race")
    runtime_id = "ptfmarkrace"
    manager._repos.agent_repo.create_project_task_runtime_with_quota(
        user_id=uid, agent_id=runtime_id, name="task-marker-race"
    )
    root = manager.paths.ensure_project_task_file_runtime_dir(runtime_id)

    async with manager._lock:
        cleanup = asyncio.create_task(manager.cleanup_unlinked_project_task_runtime(runtime_id))
        await asyncio.sleep(0)
        with manager._repos.thread_repo._db.transaction() as conn:
            conn.execute(
                "UPDATE agents SET runtime_kind = 'standard' WHERE agent_id = ?", (runtime_id,)
            )

    with pytest.raises(OctopError) as excinfo:
        await cleanup
    assert excinfo.value.code is ErrorCode.FORBIDDEN
    assert manager.get_row(runtime_id) is not None
    assert root.is_dir()


# ---------------------------------------------------------------------------
# Hot paths must not re-attach capabilities to a running internal runtime
# ---------------------------------------------------------------------------


async def test_connector_and_denylist_hot_paths_skip_internal_runtime(
    manager: AgentManager, tmp_path: Path
) -> None:
    _seed_provider(manager)
    uid = _make_user(manager, "owner-hot")
    source = _make_source_expert(manager, user_id=uid, agent_id="SRC-HOT", name="src-hot")
    row = await _create_runtime(manager, owner_user_id=uid, source=source)
    runtime_id = row.agent_id
    try:
        assert await manager.prepare_chat_mcp(runtime_id, ["gmail"]) == []
        agent = manager.get_agent(runtime_id)
        assert agent.config.mcp_server_configs == {}
        assert runtime_id not in manager._connector_user_override

        await manager.reload_connectors(runtime_id, connector_user_id=uid)
        agent = manager.get_agent(runtime_id)
        assert agent.config.mcp_server_configs == {}
        assert isinstance(agent.config.middleware[-1], ProjectTaskFileToolBoundaryMiddleware)

        # Denylist hot-syncs cannot loosen the strict runtime denylist.
        manager.sync_tools_disabled(runtime_id, set())
        manager.sync_effective_tools_disabled(runtime_id)
        manager.sync_skills_disabled(runtime_id, set())
        agent = manager.get_agent(runtime_id)
        assert project_task_file_tools_disabled().issubset(agent.config.tools_disabled)
        assert PROJECT_TASK_FILE_TOOLS.isdisjoint(agent.config.tools_disabled)
        assert agent.config.skills_dir is None
    finally:
        _shutdown(manager, runtime_id)


# ---------------------------------------------------------------------------
# Ordinary agents keep their existing behavior
# ---------------------------------------------------------------------------


async def test_ordinary_agent_creation_and_start_are_unchanged(
    manager: AgentManager, tmp_path: Path
) -> None:
    _seed_provider(manager)
    uid = _make_user(manager, "owner-plain")
    _boot_harness(manager)
    row = await manager.create(
        AgentCreateSpec(
            name="ordinary-expert",
            user_id=uid,
            config={"memory": {"memory_enabled": False}},
        )
    )
    try:
        assert row.runtime_kind == "standard"
        assert manager.paths.project_task_files_dir not in (
            manager.resolve_workspace_dir(row.agent_id).parents
        )
        agent = manager.get_agent(row.agent_id)
        cfg = agent.config
        assert cfg.bootstrap_enabled is True
        assert cfg.name == f"agent_{row.agent_id}"
        assert not any(
            isinstance(mw, ProjectTaskFileToolBoundaryMiddleware) for mw in (cfg.middleware or [])
        )
        assert agent.workspace.exists("BOOTSTRAP.md")
        assert agent.workspace.exists("AGENTS.md")
        workspace = manager.resolve_workspace_dir(row.agent_id)
        assert workspace.resolve() == manager.paths.ensure_agent_workspace(row.agent_id).resolve()
    finally:
        _shutdown(manager, row.agent_id)
