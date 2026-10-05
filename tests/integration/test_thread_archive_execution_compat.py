"""Normal synthetic execution through production projection/slash/cron entry points.

No provider or scheduler service is started. Transport is an owned in-memory
sink; actual registry, pending resolution, history SQL and cron bookkeeping run.
"""

import asyncio
import hashlib
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from octop.api.routers.chat.turn import resolve_thread_id
from octop.infra.cron.delivery import CronDeliveryService
from octop.infra.cron.job import CronJob
from octop.infra.db.repos.agents import AgentRepo
from octop.infra.db.repos.audit import AuditRepo
from octop.infra.db.repos.cron import CronJobRepo
from octop.infra.db.repos.knowledge import KnowledgeRepo
from octop.infra.db.repos.sessions import SessionRepo
from octop.infra.db.repos.thread_messages import ThreadMessageRepo
from octop.infra.db.repos.users import UserRepo
from octop.infra.gateway.gateway import Gateway
from octop.infra.gateway.hitl.coordinator import (
    HitlChannelCoordinator,
    HitlSlashOutcome,
    HitlStreamContext,
)
from octop.infra.gateway.process.history_projection import TurnHistoryTracker
from octop.infra.gateway.process.stream_project import project_stream
from octop.infra.gateway.slash import BufferSink, SlashCommand, build_default_dispatcher
from octop.infra.gateway.slash.ctx import SlashCtx, find_thread_by_short
from octop.infra.gateway.threads import ThreadRegistry
from tests.support.fakes import FakeHarnessAgent
from tests.unit.db.test_thread_archive import archive_db as archive_db


@pytest.mark.asyncio
async def test_actual_short_id_switch_explicit_resume_and_delete_accept_archived(archive_db):
    pool, repo = archive_db
    registry = ThreadRegistry(session_repo=SessionRepo(pool), thread_repo=repo)
    key = "a:dashboard:1:dm"
    for tid in ("thr_owned_abc123", "thr_owned_live00"):
        registry.create_thread(
            agent_id="a", user_id=1, channel_type="dashboard", session_key=key, thread_id=tid
        )
    repo.set_archive_owned(
        thread_id="thr_owned_abc123", user_id=1, actor_is_admin=False, archived=True, now=100
    )
    ctx = SlashCtx(
        agent_id="a",
        user_id=1,
        channel_type="dashboard",
        session_key=key,
        thread_registry=registry,
        locale="en",
    )
    dispatcher = build_default_dispatcher()
    assert find_thread_by_short(registry, "a", 1, "abc123").thread_id == "thr_owned_abc123"
    for command in ("switch", "resume"):
        await registry.rebind(session_key=key, thread_id="thr_owned_live00", agent_id="a")
        assert await dispatcher.handle(SlashCommand(command, "abc123"), ctx, BufferSink())
        assert registry.get_bound_thread_id(key) == "thr_owned_abc123"
        assert repo.get("thr_owned_abc123").archived_at == 100
    await registry.rebind(session_key=key, thread_id="thr_owned_live00", agent_id="a")
    assert await dispatcher.handle(SlashCommand("delete", "abc123"), ctx, BufferSink())
    assert repo.get("thr_owned_abc123") is None
    assert registry.get_bound_thread_id(key) == "thr_owned_live00"


class OwnedHarness(FakeHarnessAgent):
    async def aappend_messages(self, thread_id, messages):
        self._thread_messages.setdefault(thread_id, []).extend(messages)
        return messages


@pytest.mark.asyncio
async def test_inflight_dashboard_projection_and_authorized_hitl_resume_preserve_archive(
    archive_db, tmp_path
):
    pool, repo = archive_db
    sessions = SessionRepo(pool)
    registry = ThreadRegistry(session_repo=sessions, thread_repo=repo)
    key = "a:dashboard:1:dm"
    sessions.bind_dashboard_owner(session_key=key, agent_id="a", user_id=1, thread_id="one")
    fake = OwnedHarness(workspace_dir=tmp_path / "owned-workspace")
    fake.chunks = [{"type": "token", "content": "normal completion"}]
    started, release = asyncio.Event(), asyncio.Event()

    async def stream(_aid, request):
        started.set()
        await asyncio.wait_for(release.wait(), 5)
        async for chunk in fake.stream(request):
            yield chunk

    async def resume(_aid, tid, decisions):
        assert tid == "one" and decisions == [{"type": "approve"}]
        async for chunk in fake.stream({"thread_id": tid, "messages": []}):
            yield chunk

    manager = SimpleNamespace(stream=stream, resume_hitl=resume, get_agent=lambda aid: fake)
    request = {"thread_id": "one", "messages": [{"role": "user", "content": "hello"}]}
    tracker = TurnHistoryTracker.from_request(request)

    async def run():
        return [
            event async for event in project_stream(manager, "a", request, history_tracker=tracker)
        ]

    pending = asyncio.create_task(run())
    try:
        await asyncio.wait_for(started.wait(), 5)
        await asyncio.to_thread(
            fake.workspace.upload_bytes, "outbound/report.txt", b"owned artifact"
        )
        before = hashlib.sha256(
            await asyncio.to_thread(fake.workspace.download_bytes, "outbound/report.txt")
        ).hexdigest()
        checkpoint_before = json.dumps(
            (await fake.graph.aget_state({"configurable": {"thread_id": "one"}})).values,
            sort_keys=True,
        )
        sessions_before = sessions.get(key)
        repo.set_archive_owned(
            thread_id="one", user_id=1, actor_is_admin=False, archived=True, now=100
        )
        assert sessions.get(key) == sessions_before
        assert (
            json.dumps(
                (await fake.graph.aget_state({"configurable": {"thread_id": "one"}})).values,
                sort_keys=True,
            )
            == checkpoint_before
        )
        assert (
            hashlib.sha256(
                await asyncio.to_thread(fake.workspace.download_bytes, "outbound/report.txt")
            ).hexdigest()
            == before
        )
        release.set()
        assert await asyncio.wait_for(pending, 5)
        assert ThreadMessageRepo(pool).append_if_ready("one", tracker.inputs) == 2
        assert repo.get("one").archived_at == 100
        assert await resolve_thread_id(
            agent_id="a", user_id=1, thread_registry=registry, thread_id="one", session_key=key
        ) == ("one", key)
        coordinator = HitlChannelCoordinator()
        hitl = coordinator.register_from_request(
            {
                "action_requests": [
                    {"name": "write_file", "args": {"path": "outbound/approved.txt"}}
                ]
            },
            ctx=HitlStreamContext("one", "a", 1, key, "dashboard"),
        )
        assert (
            coordinator.store.resolve_pending_for_thread("one", agent_id="a", user_id=1).pending_id
            == hitl.pending_id
        )
        repo.set_archive_owned(
            thread_id="one", user_id=1, actor_is_admin=False, archived=False, now=200
        )
        repo.set_archive_owned(
            thread_id="one", user_id=1, actor_is_admin=False, archived=True, now=300
        )
        assert (
            coordinator.store.resolve_pending_for_thread("one", agent_id="a", user_id=1).pending_id
            == hitl.pending_id
        )
        outcome = HitlSlashOutcome()
        ctx = SlashCtx(
            agent_id="a",
            user_id=1,
            channel_type="dashboard",
            session_key=key,
            thread_registry=registry,
        )
        fake.chunks = [{"type": "token", "content": "resumed normally"}]
        events = [
            event
            async for event in coordinator.iter_slash_resolution(
                SlashCommand("approve", hitl.pending_id),
                ctx,
                agent_manager=manager,
                locale="en",
                outcome=outcome,
            )
        ]
        assert events and outcome.completed_turn
        assert coordinator.store.resolve_pending_for_thread("one", agent_id="a", user_id=1) is None
        assert repo.get("one").archived_at == 300 and registry.get_bound_thread_id(key) == "one"
    finally:
        release.set()
        if not pending.done():
            pending.cancel()
            await asyncio.gather(pending, return_exceptions=True)
        fake.close()
    assert fake.closed


@pytest.mark.asyncio
@pytest.mark.parametrize("task_type", ["text", "agent"])
async def test_real_cron_text_job_preserves_archived_binding_and_appends_history(
    archive_db, tmp_path, task_type
):
    pool, repo = archive_db
    sessions = SessionRepo(pool)
    key = "a:dashboard:1:dm"
    sessions.bind_dashboard_owner(session_key=key, agent_id="a", user_id=1, thread_id="one")
    repo.set_archive_owned(thread_id="one", user_id=1, actor_is_admin=False, archived=True, now=100)
    fake = OwnedHarness(workspace_dir=tmp_path / "cron-workspace")
    fake.chunks = [{"type": "token", "content": "Normal text"}]

    async def stream(aid, request):
        assert aid == "a" and request["thread_id"] == "one"
        async for chunk in fake.stream(request):
            yield chunk

    manager = SimpleNamespace(
        get_agent=lambda aid: fake,
        stream=stream,
        get_row=AgentRepo(pool).get,
        default_mcp_servers=lambda aid: [],
        merge_turn_mcp_servers=lambda *args, **kwargs: [],
        default_knowledge_base_ids=lambda aid: [],
    )
    repos = SimpleNamespace(
        session_repo=sessions,
        thread_repo=repo,
        thread_message_repo=ThreadMessageRepo(pool),
        user_repo=UserRepo(pool),
        cron_repo=CronJobRepo(pool),
        audit_repo=AuditRepo(pool),
        knowledge_repo=KnowledgeRepo(pool),
    )
    gateway = Gateway(agent_manager=manager, repos=repos)
    lock = asyncio.Lock()

    async def locked(_channel, _key, operation):
        async with lock:
            await operation()

    gateway._channel_manager = SimpleNamespace(run_in_session=locked)
    gateway.push_text = AsyncMock()
    delivery = CronDeliveryService(gateway=gateway, agent_manager=manager, repos=repos)
    repos.cron_repo.create(
        cron_id="owned-cron",
        agent_id="a",
        user_id=1,
        trigger="0 9 * * *",
        prompt="Normal text",
        session_key=key,
        task_type=task_type,
        fresh_thread=False,
    )
    try:
        await CronJob.from_row(
            repos.cron_repo.get("owned-cron"),
            delivery_service=delivery,
            cron_repo=repos.cron_repo,
            audit_repo=repos.audit_repo,
        ).run(raise_on_error=True)
        assert repos.cron_repo.get("owned-cron").last_status == "ok"
        assert repos.thread_message_repo.head("one") == 2
        assert sessions.get(key).thread_id == "one" and sessions.get(key).unread_count == 1
        assert repo.get("one").archived_at == 100
        if task_type == "text":
            assert len(await fake.aget_history("one")) == 2
        else:
            assert fake.last_request["thread_id"] == "one"
        gateway.push_text.assert_awaited_once()
    finally:
        fake.close()


@pytest.mark.asyncio
async def test_im_bound_archived_thread_is_reused_by_normal_projection(archive_db, tmp_path):
    pool, repo = archive_db
    registry = ThreadRegistry(session_repo=SessionRepo(pool), thread_repo=repo)
    key = "a:telegram:1:dm"
    tid = await registry.get_or_create_by_key(
        session_key=key, agent_id="a", user_id=1, channel_type="telegram"
    )
    before = registry.get_session(key)
    fake = OwnedHarness(
        workspace_dir=tmp_path / "im-workspace",
        chunks=[{"type": "token", "content": "normal IM reply"}],
    )

    async def stream(aid, request):
        assert aid == "a" and request["thread_id"] == tid
        async for chunk in fake.stream(request):
            yield chunk

    manager = SimpleNamespace(stream=stream, get_agent=lambda aid: fake)
    try:
        repo.set_archive_owned(
            thread_id=tid, user_id=1, actor_is_admin=False, archived=True, now=100
        )
        assert (
            await registry.get_or_create_by_key(
                session_key=key, agent_id="a", user_id=1, channel_type="telegram"
            )
            == tid
        )
        request = {"thread_id": tid, "messages": [{"role": "user", "content": "normal IM input"}]}
        tracker = TurnHistoryTracker.from_request(request)
        assert [
            event async for event in project_stream(manager, "a", request, history_tracker=tracker)
        ]
        assert ThreadMessageRepo(pool).append_if_ready(tid, tracker.inputs) == 2
        assert registry.get_session(key) == before and repo.get(tid).archived_at == 100
    finally:
        fake.close()
