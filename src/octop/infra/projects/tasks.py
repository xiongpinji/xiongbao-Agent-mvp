"""Project task business rules: private attribution (PS-05) + card shares.

A project member may attach, list, inspect, and detach only their own
existing Dashboard DM thread. Access mirrors
:class:`octop.infra.projects.todos.ProjectTodoService`: non-members get the
same 404 as unknown projects, and foreign/unknown/deleted/non-dashboard/
non-DM threads all collapse into one 404, so neither project existence nor a
private thread id or title is ever leaked. Project membership grants project
metadata access only — never another member's task bodies, and neither
instance-admin nor project-admin identity bypasses task ownership (the
operator is always ``current_user.id``; there is no ``as_user`` here).

PS-05B slice 1 adds explicit owner-granted sharing of the task CARD SUMMARY
only: the task owner (and nobody else — project owner/admin roles included)
may grant same-project members revocable ``reader`` access. Readers see the
granted card in ``scope=shared|all`` and in the detail read; they never gain
history, file, download, stream, or read-status access. Grant metadata is
limited to grantee id, role, and grant time.

Error contract:

* 404 ``NOT_FOUND`` — outsider, unknown project, foreign/unknown/deleted/
  non-dashboard/non-DM thread, missing or cross-project link, invalid share
  recipient, or any share management by a non-owner of the task;
* 403 ``FORBIDDEN`` — granting or regranting share access on an archived
  project (reads and revokes stay allowed);
* 409 ``PROJECT_TASK_LINK_CONFLICT`` — the caller's own thread is already
  linked to a different project (deterministic; ``details.project_id`` names
  the caller's other project so the UI can offer to detach it first).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from octop.infra.db.repos.project_task_shares import VisibleTaskSummary
from octop.infra.db.repos.project_tasks import ProjectTaskSummary
from octop.infra.errors import ErrorCode, OctopError
from octop.infra.gateway.threads import ThreadRegistry
from octop.infra.projects.service import DEFAULT_PAGE_LIMIT

TaskScope = Literal["own", "shared", "all"]


@dataclass(frozen=True)
class TaskSummaryView:
    """Public safe summary — exactly the contract's fields, nothing more.

    ``access`` is ``owner`` for the task owner and ``reader`` for an active
    share recipient; it is the only field PS-05B adds to the 021 summary.
    """

    project_id: str
    thread_id: str
    owner_user_id: int
    agent_id: str
    title: str | None
    source: str
    last_active: int
    created_at: int
    access: str = "owner"


@dataclass(frozen=True)
class TaskShareView:
    """Grant metadata for the owner's share list — id, role, and time only."""

    user_id: int
    role: str
    granted_at: int


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


def _archived_error() -> OctopError:
    # Same shape as ProjectAssetService: granting access on an archived
    # project is refused; tightening or reading it stays allowed.
    return OctopError(ErrorCode.FORBIDDEN, "project is archived")


class ProjectTaskService:
    def __init__(self, services: Any) -> None:
        self._services = services

    @property
    def _repo(self) -> Any:
        return self._services.project_task_repo

    @property
    def _share_repo(self) -> Any:
        return self._services.project_task_share_repo

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
        access = summary.access if isinstance(summary, VisibleTaskSummary) else "owner"
        return TaskSummaryView(
            project_id=summary.project_id,
            thread_id=summary.thread_id,
            owner_user_id=summary.owner_user_id,
            agent_id=summary.agent_id,
            title=summary.title,
            source=summary.source,
            last_active=summary.last_active,
            created_at=summary.created_at,
            access=access,
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
        scope: TaskScope = "own",
        q: str = "",
        limit: int = DEFAULT_PAGE_LIMIT,
        offset: int = 0,
    ) -> TaskListPage:
        """Visible task cards in one project.

        ``own`` (the private slice-1 default) runs the untouched 021 query;
        ``shared`` returns only cards other members explicitly granted to the
        caller and never revoked; ``all`` merges both in ONE SQL statement
        with a single final ORDER BY / LIMIT / OFFSET. Unshared tasks of
        other members never appear in any result, total, filter, or paging
        metadata.
        """
        self._require_membership(project_id, user_id)
        if scope == "shared":
            rows = self._share_repo.list_shared(
                project_id, user_id=user_id, q=q, limit=limit, offset=offset
            )
        elif scope == "all":
            rows = self._share_repo.list_all(
                project_id, user_id=user_id, q=q, limit=limit, offset=offset
            )
        else:
            rows = self._repo.list_for_owner(
                project_id, user_id=user_id, q=q, limit=limit, offset=offset
            )
        has_more = len(rows) > limit
        items = [self._view(row) for row in rows[:limit]]
        return TaskListPage(items=items, limit=limit, offset=offset, has_more=has_more)

    def get_task(self, project_id: str, thread_id: str, *, user_id: int) -> TaskSummaryView:
        """Own card, or a card the owner explicitly shared and has not
        revoked; everything else is the same uniform 404."""
        self._require_membership(project_id, user_id)
        summary = self._repo.get_for_owner(project_id, thread_id, user_id=user_id)
        if summary is not None:
            return self._view(summary)
        shared = self._share_repo.get_shared_summary(project_id, thread_id, user_id=user_id)
        if shared is not None:
            return self._view(shared)
        raise _task_not_found()

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

    # ------------------------------------------------------------ shares

    def grant_share(
        self, project_id: str, thread_id: str, *, user_id: int, grantee_user_id: int
    ) -> tuple[TaskShareView, bool]:
        """Task owner grants card-summary access; returns ``(view, created)``.

        ``created`` is True only for a brand-new grant (201); an active
        duplicate (200, row untouched) and a regrant after revoke (200, same
        row reactivated) are False. Project owner/admin identity never
        substitutes for task ownership, self-grants and non-member recipients
        answer with the uniform task 404, and archived projects refuse grants
        with 403 inside the write transaction.
        """
        self._require_membership(project_id, user_id)
        if grantee_user_id == user_id:
            # Meaningless target; answer like every other invalid task.
            raise _task_not_found()
        mutation = self._share_repo.grant(
            project_id=project_id,
            thread_id=thread_id,
            actor_user_id=user_id,
            grantee_user_id=grantee_user_id,
        )
        if mutation.outcome == "not_member":
            raise _project_not_found()
        if mutation.outcome == "archived":
            raise _archived_error()
        if mutation.outcome in ("invalid_task", "invalid_recipient"):
            raise _task_not_found()
        if mutation.share is None:  # pragma: no cover - grant outcomes carry a row
            raise OctopError(ErrorCode.INTERNAL_ERROR, "share row missing after grant")
        view = TaskShareView(
            user_id=mutation.share.user_id,
            role=mutation.share.role,
            granted_at=mutation.share.granted_at,
        )
        return view, mutation.outcome == "created"

    def revoke_share(
        self, project_id: str, thread_id: str, *, user_id: int, grantee_user_id: int
    ) -> None:
        """Task owner revokes reader access; repeated revokes stay no-ops.

        Allowed on archived projects too — tightening access is never
        blocked. The recipient loses the card immediately; the owner's task
        and the recipient's own data are untouched.
        """
        self._require_membership(project_id, user_id)
        outcome = self._share_repo.revoke(
            project_id=project_id,
            thread_id=thread_id,
            actor_user_id=user_id,
            grantee_user_id=grantee_user_id,
        )
        if outcome == "not_member":
            raise _project_not_found()
        if outcome == "missing_task":
            raise _task_not_found()

    def list_shares(self, project_id: str, thread_id: str, *, user_id: int) -> list[TaskShareView]:
        """Active grants on one own task; owner-only.

        Any other caller — member, grantee, project admin, outsider — gets
        the same uniform task 404, so neither the task's existence nor its
        ownership leaks. Rows carry grantee id, role, and grant time only.
        """
        self._require_membership(project_id, user_id)
        rows = self._share_repo.list_shares(project_id, thread_id, actor_user_id=user_id)
        if rows is None:
            raise _task_not_found()
        return [
            TaskShareView(user_id=row.user_id, role=row.role, granted_at=row.granted_at)
            for row in rows
        ]
