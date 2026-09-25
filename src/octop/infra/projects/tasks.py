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

import hashlib
import json
from dataclasses import dataclass
from typing import Any, Literal

from octop.infra.db.repos.project_task_shares import VisibleTaskSummary
from octop.infra.db.repos.project_tasks import ProjectTaskSummary
from octop.infra.errors import ErrorCode, OctopError
from octop.infra.gateway.threads import ThreadRegistry
from octop.infra.projects.service import DEFAULT_PAGE_LIMIT
from octop.infra.utils.ulid import new_ulid

TaskScope = Literal["own", "shared", "all"]


def instructions_sha256(text: str) -> str:
    """Canonical digest of project instructions: SHA-256 over the UTF-8 bytes.

    This is exactly the value stored on the 026 snapshot and exposed on the
    member-visible project detail so the create confirmation can echo it back.
    The project instructions never appear in task responses, events, or logs.
    """
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


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
    can_read_text: bool = True


@dataclass(frozen=True)
class TaskShareView:
    """Grant metadata for the owner's share list — id, role, and time only."""

    user_id: int
    role: str
    granted_at: int
    can_read_text: bool = False


@dataclass(frozen=True)
class TaskTextGrantView:
    user_id: int
    granted_at: int


@dataclass(frozen=True)
class TaskMessageView:
    seq: int
    role: str
    text: str
    created_at: int
    truncated: bool


@dataclass(frozen=True)
class TaskMessagesPage:
    status: str
    items: list[TaskMessageView]
    has_more: bool
    next_before_seq: int | None


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


def expert_unavailable_error() -> OctopError:
    """Uniform 029 refusal for a configured expert that is no longer usable.

    Also used by the route when its Agent ACL pre-check loses a race with an
    unshare/disable/delete, so an authorized member always sees one recoverable
    code instead of AGENT_NOT_FOUND/FORBIDDEN leaking private ownership state.
    The error carries no Agent id or profile.
    """
    return OctopError(
        ErrorCode.PROJECT_EXPERT_UNAVAILABLE,
        "the selected project expert is no longer available",
    )


def _experts_changed_error() -> OctopError:
    return OctopError(
        ErrorCode.PROJECT_EXPERTS_CHANGED,
        "project experts changed since they were confirmed",
    )


def _safe_text_message(row: Any) -> TaskMessageView | None:
    """Project only plain user/assistant text from a stored projection row."""
    try:
        raw = row.message_json
        if len(raw.encode("utf-8")) > 256 * 1024:
            return None
        wire = json.loads(raw)
        if not isinstance(wire, dict) or not isinstance(wire.get("data"), dict):
            return None
        data = wire["data"]
        stored = row.role
        kind = wire.get("type")
        if stored in ("human", "user") and kind in ("human", "user"):
            role = "user"
        elif stored in ("ai", "assistant") and kind in ("ai", "assistant"):
            role = "assistant"
        else:
            return None
        # Normal AI wires include empty lists. Any populated or malformed
        # call field is excluded in its entirety, even if content is a string.
        for key in ("tool_calls", "invalid_tool_calls"):
            calls = data.get(key, [])
            if calls != []:
                return None
        extra = data.get("additional_kwargs")
        if isinstance(extra, dict) and extra.get("octop_stream_error") is True:
            return None
        content = data.get("content")
        if isinstance(content, str):
            text = content
        elif isinstance(content, list):
            parts: list[str] = []
            for part in content:
                if isinstance(part, str):
                    parts.append(part)
                elif (
                    isinstance(part, dict)
                    and part.get("type") == "text"
                    and isinstance(part.get("text"), str)
                ):
                    parts.append(part["text"])
            text = "".join(parts)
        else:
            return None
        if not text:
            return None
        encoded = text.encode("utf-8")
        truncated = len(encoded) > 32768
        if truncated:
            text = encoded[:32768].decode("utf-8", "ignore")
        return TaskMessageView(
            seq=row.seq,
            role=role,
            text=text,
            created_at=row.created_at,
            truncated=truncated,
        )
    except (TypeError, ValueError, UnicodeError, AttributeError):
        return None


class ProjectTaskService:
    def __init__(self, services: Any, *, history_archive: Any = None) -> None:
        self._services = services
        self._history_archive = history_archive

    @property
    def _repo(self) -> Any:
        return self._services.project_task_repo

    @property
    def _share_repo(self) -> Any:
        return self._services.project_task_share_repo

    @property
    def _content_repo(self) -> Any:
        return self._services.project_task_content_repo

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
            can_read_text=summary.can_read_text
            if isinstance(summary, VisibleTaskSummary)
            else True,
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

    # ------------------------------------------------------------ create

    def authorize_create_target(
        self,
        project_id: str,
        *,
        user_id: int,
        agent_id: str,
        expected_experts_revision: int | None = None,
    ) -> bool:
        """Create-route pre-check; returns whether the project experts gate on.

        Runs before the router's Agent ACL check so a non-member never learns
        whether the named Agent exists or is accessible. With the 029 gate on
        (nonempty live list), an old client that omitted the revision and any
        stale revision are refused 409, and a selected Agent that is unlisted
        or presently unavailable is refused with the uniform recoverable
        ``PROJECT_EXPERT_UNAVAILABLE`` before any Agent detail is read. An
        empty list keeps the 028 owner/shared flow but still rejects a supplied
        revision that no longer matches.
        """
        self._require_membership(project_id, user_id)
        selection = self._project_repo.get_experts(project_id)
        if selection is None:
            raise _project_not_found()
        revision = int(selection.revision)
        if not selection.items:
            if expected_experts_revision is not None and expected_experts_revision != revision:
                raise _experts_changed_error()
            return False
        if expected_experts_revision is None or expected_experts_revision != revision:
            raise _experts_changed_error()
        selected = next((row for row in selection.items if row.agent_id == agent_id), None)
        if selected is None or selected.status != "available":
            raise expert_unavailable_error()
        return True

    def create_project_task(
        self,
        project_id: str,
        *,
        user_id: int,
        agent_id: str,
        expected_instructions_sha256: str,
        expected_experts_revision: int | None = None,
        is_admin: bool = False,
    ) -> TaskSummaryView:
        """Create a private task frozen against the current project instructions.

        The repository re-validates membership, archive state, the expert list
        and revision, agent kind, and the freshly-read digest inside one write
        transaction that also inserts the thread, projection,
        ``source='project'`` link, and snapshot. A stale digest raises 409
        ``PROJECT_INSTRUCTIONS_CHANGED`` and a stale expert revision raises 409
        ``PROJECT_EXPERTS_CHANGED`` with no task row; a configured expert that
        stopped being shared/enabled/listed raises the uniform recoverable
        ``PROJECT_EXPERT_UNAVAILABLE``. The caller's active dashboard session
        is never rebound.
        """
        self._require_membership(project_id, user_id)
        mutation = self._repo.create_with_context(
            project_id=project_id,
            user_id=user_id,
            agent_id=agent_id,
            thread_id=f"thr_{new_ulid()}",
            session_key=ThreadRegistry.dashboard_key(agent_id=agent_id, user_id=user_id),
            expected_instructions_sha256=expected_instructions_sha256.strip().lower(),
            expected_experts_revision=expected_experts_revision,
            is_admin=is_admin,
        )
        if mutation.outcome == "not_member":
            raise _project_not_found()
        if mutation.outcome == "archived":
            raise _archived_error()
        if mutation.outcome == "stale_instructions":
            raise OctopError(
                ErrorCode.PROJECT_INSTRUCTIONS_CHANGED,
                "project instructions changed since they were confirmed",
            )
        if mutation.outcome == "stale_experts":
            raise _experts_changed_error()
        if mutation.outcome == "invalid_expert":
            raise expert_unavailable_error()
        if mutation.outcome == "invalid_agent":
            # Team hosts are excluded from this slice: delegated peer agents
            # do not inherit the project context the snapshot freezes.
            raise OctopError(
                ErrorCode.FORBIDDEN,
                "agent no longer accessible or supported for project tasks",
            )
        if mutation.summary is None:  # pragma: no cover - created carries a summary
            raise OctopError(ErrorCode.INTERNAL_ERROR, "task summary missing after create")
        return self._view(mutation.summary)

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
            can_read_text=mutation.share.can_read_text,
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
            TaskShareView(
                user_id=row.user_id,
                role=row.role,
                granted_at=row.granted_at,
                can_read_text=row.can_read_text,
            )
            for row in rows
        ]

    # ------------------------------------------------------------ text grants

    def grant_text_share(
        self, project_id: str, thread_id: str, *, user_id: int, grantee_user_id: int
    ) -> tuple[TaskTextGrantView, bool]:
        self._require_membership(project_id, user_id)
        if user_id == grantee_user_id:
            raise _task_not_found()
        mutation = self._content_repo.grant(
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
        if mutation.grant is None:
            raise OctopError(ErrorCode.INTERNAL_ERROR, "text grant missing after grant")
        return (
            TaskTextGrantView(mutation.grant.user_id, mutation.grant.granted_at),
            mutation.outcome == "created",
        )

    def revoke_text_share(
        self, project_id: str, thread_id: str, *, user_id: int, grantee_user_id: int
    ) -> None:
        self._require_membership(project_id, user_id)
        outcome = self._content_repo.revoke(
            project_id=project_id,
            thread_id=thread_id,
            actor_user_id=user_id,
            grantee_user_id=grantee_user_id,
        )
        if outcome == "not_member":
            raise _project_not_found()
        if outcome == "missing_task":
            raise _task_not_found()

    def read_task_messages(
        self,
        project_id: str,
        thread_id: str,
        *,
        user_id: int,
        limit: int = 50,
        before_seq: int | None = None,
    ) -> TaskMessagesPage:
        # Authorization and raw-row fetch share a single DB transaction. The
        # service parses JSON only after the repository has released its lock.
        self._require_membership(project_id, user_id)
        raw = self._content_repo.read_page(
            project_id=project_id,
            thread_id=thread_id,
            actor_user_id=user_id,
            limit=limit,
            before_seq=before_seq,
            projection_enabled=self._history_archive is None,
        )
        if raw is None:
            raise _task_not_found()
        if raw.status != "ready":
            return TaskMessagesPage("pending", [], False, None)
        items = [item for row in raw.rows if (item := _safe_text_message(row)) is not None]
        return TaskMessagesPage("ready", items, raw.has_more, raw.next_before_seq)
