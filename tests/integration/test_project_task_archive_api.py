"""Owned ASGI project projection and global organization without provider startup."""

from types import SimpleNamespace

import httpx
import pytest

from octop.api.routers.project_tasks import router
from octop.infra.db.repos.project_task_shares import ProjectTaskShareRepo
from octop.infra.db.repos.project_tasks import ProjectTaskRepo
from octop.infra.db.repos.projects import ProjectRepo
from tests.unit.api.test_thread_metadata_api import _insert
from tests.unit.api.test_thread_metadata_api import metadata_api as metadata_api


@pytest.fixture
def project_archive_api(metadata_api):
    api = metadata_api
    projects = ProjectRepo(api.pool)
    tasks = ProjectTaskRepo(api.pool)
    shares = ProjectTaskShareRepo(api.pool)
    project = projects.create_with_owner(creator_user_id=1, name="Archive")
    projects.add_member(project.project_id, 2, role="member")
    api.server.services.project_repo = projects
    api.server.services.project_task_repo = tasks
    api.server.services.project_task_share_repo = shares
    api.server.app_runtime.history_archive = None
    api.app.include_router(router, prefix="/api")
    for tid, owner, aid in (
        ("owner-archived", 1, "expert"),
        ("owner-live", 1, "expert"),
        ("reader-card", 2, "other-expert"),
    ):
        _insert(api, tid, user_id=owner, agent_id=aid)
        assert (
            tasks.attach(project_id=project.project_id, user_id=owner, thread_id=tid).outcome
            == "created"
        )
    assert (
        shares.grant(
            project_id=project.project_id,
            thread_id="owner-archived",
            actor_user_id=1,
            grantee_user_id=2,
        ).outcome
        == "created"
    )
    assert (
        shares.grant(
            project_id=project.project_id,
            thread_id="reader-card",
            actor_user_id=2,
            grantee_user_id=1,
        ).outcome
        == "created"
    )
    return SimpleNamespace(
        api=api, projects=projects, tasks=tasks, shares=shares, pid=project.project_id
    )


@pytest.mark.asyncio
async def test_owner_archived_filter_reader_omit_and_all_pagination(project_archive_api):
    env = project_archive_api
    api = env.api
    base = f"/api/projects/{env.pid}/tasks"
    with api.pool.transaction() as conn:
        conn.execute("UPDATE threads SET last_active=100 WHERE thread_id='owner-archived'")
        conn.execute("UPDATE threads SET last_active=90 WHERE thread_id='reader-card'")
        conn.execute("UPDATE threads SET last_active=80 WHERE thread_id='owner-live'")
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=api.app), base_url="http://owned-asgi"
    ) as client:
        assert (
            await client.post("/api/threads/owner-archived/archive", json={"archived": True})
        ).status_code == 200
        own = (await client.get(base)).json()
        assert [r["thread_id"] for r in own["items"]] == ["owner-live"]
        assert own["items"][0]["archived_at"] is None
        archived = (await client.get(base, params={"archived": "true"})).json()
        assert [r["thread_id"] for r in archived["items"]] == ["owner-archived"]
        detail = (await client.get(base + "/owner-archived")).json()
        assert detail["access"] == "owner" and detail["archived_at"] > 0
        first = (await client.get(base, params={"scope": "all", "limit": 1})).json()
        assert [r["thread_id"] for r in first["items"]] == ["reader-card"] and first["has_more"]
        assert "archived_at" not in first["items"][0]
        assert (
            first["items"][0]["chat_agent_id"] is None
            and first["items"][0]["source_expert_id"] is None
        )
        second = (await client.get(base, params={"scope": "all", "limit": 1, "offset": 1})).json()
        assert [r["thread_id"] for r in second["items"]] == ["owner-live"] and not second[
            "has_more"
        ]
        for scope in ("shared", "all"):
            assert (
                await client.get(base, params={"scope": scope, "archived": "true"})
            ).status_code == 422
        api.user.id = 2
        reader = (await client.get(base + "/owner-archived")).json()
        assert reader["access"] == "reader" and "archived_at" not in reader
        shared = (await client.get(base, params={"scope": "shared"})).json()
        assert [r["thread_id"] for r in shared["items"]] == ["owner-archived"]
        assert "archived_at" not in shared["items"][0]
        assert (
            await client.post("/api/threads/owner-archived/archive", json={"archived": False})
        ).status_code == 404
        env.projects.remove_member(project_id=env.pid, user_id=2, actor_user_id=1)
        assert (await client.get(base + "/owner-archived")).status_code == 404


@pytest.mark.asyncio
async def test_global_files_restore_does_not_restore_project_access(project_archive_api):
    env = project_archive_api
    api = env.api
    _insert(api, "file-task", user_id=2, agent_id="other-expert")
    # A sole valid file runtime is a real prior record; no feature flag is enabled.
    with api.pool.transaction() as conn:
        conn.execute("DELETE FROM project_task_links WHERE thread_id='reader-card'")
        conn.execute("DELETE FROM threads WHERE thread_id='reader-card'")
        conn.execute(
            "UPDATE agents SET runtime_kind='project_task_files' WHERE agent_id='other-expert'"
        )
    env.projects.remove_member(project_id=env.pid, user_id=2, actor_user_id=1)
    api.user.id = 2
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=api.app), base_url="http://owned-asgi"
    ) as client:
        assert (
            await client.post("/api/threads/file-task/archive", json={"archived": True})
        ).status_code == 200
        page = (await client.get("/api/threads/archived")).json()
        assert page["items"][0]["mode"] == "files" and "project_name" not in page["items"][0]
        assert (
            await client.post("/api/threads/file-task/archive", json={"archived": False})
        ).status_code == 200
        assert (await client.get(f"/api/projects/{env.pid}/tasks")).status_code == 404
    assert not (api.home / "project-task-files").exists()


@pytest.mark.asyncio
async def test_three_own_three_shared_merge_filters_before_common_paging(project_archive_api):
    env = project_archive_api
    api = env.api
    for tid, owner, aid in (
        ("owner-more", 1, "expert"),
        ("reader-two", 2, "other-expert"),
        ("reader-three", 2, "other-expert"),
    ):
        _insert(api, tid, user_id=owner, agent_id=aid)
        assert (
            env.tasks.attach(project_id=env.pid, user_id=owner, thread_id=tid).outcome == "created"
        )
        if owner == 2:
            assert (
                env.shares.grant(
                    project_id=env.pid, thread_id=tid, actor_user_id=2, grantee_user_id=1
                ).outcome
                == "created"
            )
    for tid, activity in (
        ("owner-archived", 70),
        ("reader-three", 60),
        ("reader-two", 50),
        ("reader-card", 40),
        ("owner-more", 30),
        ("owner-live", 20),
    ):
        with api.pool.transaction() as conn:
            conn.execute("UPDATE threads SET last_active=? WHERE thread_id=?", (activity, tid))
    api.threads.set_archive_owned(
        thread_id="owner-archived", user_id=1, actor_is_admin=False, archived=True, now=100
    )
    api.threads.set_archive_owned(
        thread_id="reader-two", user_id=2, actor_is_admin=False, archived=True, now=100
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=api.app), base_url="http://owned-asgi"
    ) as client:
        collected = []
        for offset, expected, more in (
            (0, ["reader-three", "reader-two"], True),
            (2, ["reader-card", "owner-more"], True),
            (4, ["owner-live"], False),
        ):
            response = await client.get(
                f"/api/projects/{env.pid}/tasks",
                params={"scope": "all", "limit": 2, "offset": offset},
            )
            assert response.status_code == 200, response.text
            body = response.json()
            assert [row["thread_id"] for row in body["items"]] == expected and body[
                "has_more"
            ] == more
            collected.extend(body["items"])
        assert len(collected) == 5
        for row in collected:
            if row["access"] == "reader":
                assert "archived_at" not in row and row["chat_agent_id"] is None
            else:
                assert row["archived_at"] is None
