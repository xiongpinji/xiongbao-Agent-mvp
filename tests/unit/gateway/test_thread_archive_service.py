"""Domain errors and registry compatibility use real owned repositories."""

from types import SimpleNamespace

import pytest
from tests.unit.db.test_thread_archive import archive_db as archive_db

from octop.infra.db.repos.sessions import SessionRepo
from octop.infra.errors import ErrorCode, OctopError
from octop.infra.gateway.thread_archive import ThreadArchiveService
from octop.infra.gateway.threads import ThreadRegistry


def test_service_small_receipt_and_unknown_foreign_errors(archive_db):
    _, repo = archive_db
    service = ThreadArchiveService(SimpleNamespace(thread_repo=repo))
    for tid in ("missing", "two"):
        with pytest.raises(OctopError) as error:
            service.set_archive(thread_id=tid, user_id=1, actor_is_admin=True, archived=True)
        assert error.value.code == ErrorCode.NOT_FOUND
    with pytest.raises(OctopError) as error:
        service.list_archived(user_id=1, actor_is_admin=False, as_user="1")
    assert error.value.code == ErrorCode.FORBIDDEN
    state = service.set_archive(thread_id="one", user_id=1, actor_is_admin=False, archived=True)
    assert state.archived_at is not None
    page = service.list_archived(user_id=1, actor_is_admin=False, limit=1)
    assert page.items[0].thread_id == "one" and not page.has_more


def test_registry_default_all_including_search_session_and_binding(archive_db):
    pool, repo = archive_db
    sessions = SessionRepo(pool)
    registry = ThreadRegistry(session_repo=sessions, thread_repo=repo)
    sessions.bind_dashboard_owner(
        session_key="a:dashboard:1:dm", agent_id="a", user_id=1, thread_id="one"
    )
    repo.set_archive_owned(thread_id="one", user_id=1, actor_is_admin=False, archived=True, now=100)
    assert [r.thread_id for r in registry.list_threads(agent_id="a", user_id=1)] == ["one"]
    assert [r.thread_id for r in registry.list_threads(agent_id="a", user_id=1, q="STRASSE")] == [
        "one"
    ]
    assert registry.list_threads(agent_id="a", user_id=1, archived=False) == []
    assert registry.list_threads_for_session(session_key="a:dashboard:1:dm")[0].thread_id == "one"
    assert registry.get_bound_thread_id("a:dashboard:1:dm") == "one"
