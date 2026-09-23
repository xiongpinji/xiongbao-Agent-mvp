"""Project task business rules (PS-05 slice 1): private task attribution.

A project member may attach, list, inspect, and detach only their own
existing Dashboard DM thread. Access mirrors
:class:`octop.infra.projects.todos.ProjectTodoService`: non-members get the
same 404 as unknown projects, and foreign/unknown/deleted/non-dashboard/
non-DM threads all collapse into one 404, so neither project existence nor a
private thread id or title is ever leaked. Project membership grants project
metadata access only — never another member's task bodies, and neither
instance-admin nor project-admin identity bypasses task ownership (the
operator is always ``current_user.id``; there is no ``as_user`` here).

Error contract:

* 404 ``NOT_FOUND`` — outsider, unknown project, foreign/unknown/deleted/
  non-dashboard/non-DM thread, missing or cross-project link;
* 409 ``PROJECT_TASK_LINK_CONFLICT`` — the caller's own thread is already
  linked to a different project (deterministic; ``details.project_id`` names
  the caller's other project so the UI can offer to detach it first).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from octop.infra.db.repos.project_tasks import ProjectTaskSummary
from octop.infra.errors import ErrorCode, OctopError
from octop.infra.gateway.threads import ThreadRegistry
from octop.infra.projects.service import DEFAULT_PAGE_LIMIT


@dataclass(frozen=True)
class TaskSummaryView:
    """Public safe summary — exactly the contract's fields, nothing more."""

    project_id: str
    thread_id: str
    owner_user_id: int
    agent_id: str
    title: str | None
    source: str
    last_active: int
    created_at: int


@dataclass(frozen=True)
class TaskListPage:
    items: list[TaskSummaryView]
    limit: int
    offset: int
    has_more: bool


def _project_not_found() -> OctopError:
    # Same message for outsiders and unknown projects: no existence leak.
    return OctopError(ErrorCode.NOT_FOUND, "project not found")


def _task_not_found() -> OctopError:
    # Same message for foreign/unknown/deleted/non-dashboard threads and for
    # missing or cross-project links: no title or existence leak.
    return OctopError(ErrorCode.NOT_FOUND, "project task not found")


def _link_conflict(project_id: str) -> OctopError:
    return OctopError(
        ErrorCode.PROJECT_TASK_LINK_CONFLICT,
        "task is already linked to another project",
        details={"project_id": project_id},
    )


class ProjectTaskService:
    def __init__(self, services: Any) -> None:
        self._services = services

    @property
    def _repo(self) -> Any:
        return self._services.project_task_repo

    @property
    def _project_repo(self) -> Any:
        return self._services.project_repo

    @property
    def _thread_repo(self) -> Any:
        return self._services.thread_repo

    # ------------------------------------------------------------ access

    def _require_membership(self, project_id: str, user_id: int) -> Any:
        """Members only; outsiders get the same 404 as unknown projects."""
        membership = self._project_repo.get_membership(project_id, user_id)
        if membership is None:
            raise _project_not_found()
        return membership

    def _require_own_dashboard_dm(self, thread_id: str, user_id: int) -> Any:
        """The thread must be the caller's own Dashboard ``:dm`` conversation.

        Every failure — foreign, unknown, deleted, other channel type, group
        session — raises the same 404 so no title or existence leaks. The
        repo re-validates all of it inside the write transaction.
        """
        thread = self._thread_repo.get(thread_id)
        if thread is None or int(thread.user_id) != int(user_id):
            raise _task_not_found()
        expected_key = ThreadRegistry.dashboard_key(agent_id=thread.agent_id, user_id=user_id)
        if (
            thread.channel_type != ThreadRegistry.CHANNEL_DASHBOARD
            or thread.session_key != expected_key
        ):
            raise _task_not_found()
        return thread

    @staticmethod
    def _view(summary: ProjectTaskSummary) -> TaskSummaryView:
        return TaskSummaryView(
            project_id=summary.project_id,
            thread_id=summary.thread_id,
            owner_user_id=summary.owner_user_id,
            agent_id=summary.agent_id,
            title=summary.title,
            source=summary.source,
            last_active=summary.last_active,
            created_at=summary.created_at,
        )

    # ------------------------------------------------------------ attach

    def authorize_attach_target(self, project_id: str, *, user_id: int, thread_id: str) -> str:
        """HTTP-layer pre-check before the write; returns the thread's agent id.

        The router runs the existing ``require_agent_row`` ACL check on that
        agent before :meth:`attach_task` writes anything, so agent
        accessibility is never reduced to SQL.
        """
        self._require_membership(project_id, user_id)
        thread = self._require_own_dashboard_dm(thread_id, user_id)
        return str(thread.agent_id)

    def attach_task(
        self, project_id: str, *, user_id: int, thread_id: str
    ) -> tuple[TaskSummaryView, bool]:
        """Attach the caller's own Dashboard DM thread; returns ``(view, created)``.

        Re-attaching the same project is idempotent (``created=False``, no
        second row); a thread already linked to another project is a
        deterministic 409.
        """
        self._require_membership(project_id, user_id)
        self._require_own_dashboard_dm(thread_id, user_id)
        mutation = self._repo.attach(project_id=project_id, user_id=user_id, thread_id=thread_id)
        if mutation.outcome == "not_member":
            raise _project_not_found()
        if mutation.outcome == "invalid_thread":
            raise _task_not_found()
        if mutation.outcome == "conflict":
            raise _link_conflict(mutation.conflict_project_id)
        if mutation.summary is None:  # pragma: no cover - created/duplicate carry a summary
            raise OctopError(ErrorCode.INTERNAL_ERROR, "task summary missing after attach")
        return self._view(mutation.summary), mutation.outcome == "created"

    # ------------------------------------------------------------ reads

    def list_tasks(
        self,
        project_id: str,
        *,
        user_id: int,
        q: str = "",
        limit: int = DEFAULT_PAGE_LIMIT,
        offset: int = 0,
    ) -> TaskListPage:
        """Own tasks in one project. Other members' tasks never appear in the
        query result, total, filter, or paging metadata."""
        self._require_membership(project_id, user_id)
        rows = self._repo.list_for_owner(
            project_id, user_id=user_id, q=q, limit=limit, offset=offset
        )
        has_more = len(rows) > limit
        items = [self._view(row) for row in rows[:limit]]
        return TaskListPage(items=items, limit=limit, offset=offset, has_more=has_more)

    def get_task(self, project_id: str, thread_id: str, *, user_id: int) -> TaskSummaryView:
        self._require_membership(project_id, user_id)
        summary = self._repo.get_for_owner(project_id, thread_id, user_id=user_id)
        if summary is None:
            raise _task_not_found()
        return self._view(summary)

    # ------------------------------------------------------------ detach

    def detach_task(self, project_id: str, thread_id: str, *, user_id: int) -> None:
        """Remove the project attribution only; the original conversation,
        history, attachments, and dashboard session stay with the owner."""
        self._require_membership(project_id, user_id)
        outcome = self._repo.detach(project_id=project_id, thread_id=thread_id, user_id=user_id)
        if outcome == "not_member":
            raise _project_not_found()
        if outcome != "removed":
            raise _task_not_found()
