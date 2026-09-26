"""HTTP API for 熊宝-Agent project task links (PS-05) and card shares (PS-05B).

Thin transport only: membership/ownership policy lives in
:class:`octop.infra.projects.tasks.ProjectTaskService`, race-safe SQL in
``ProjectTaskRepo``/``ProjectTaskShareRepo``. The literal ``/links`` route is
declared before ``/{thread_id}`` so the fixed path never loses to the path
parameter when same-method routes are added later. The attach route runs the
existing ``require_agent_row`` ACL check before the service write — agent
accessibility (owner/shared/admin) is never reduced to SQL. Operators are
always ``current_user.id``; this router accepts no ``as_user`` and project or
instance admin identity never bypasses task ownership. Share routes are
task-owner-only; ``POST .../shares`` bodies name the RECIPIENT (``user_id``),
never the actor.
"""

from __future__ import annotations

import logging
from typing import Any, Literal

from fastapi import APIRouter, Depends, Query
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from octop.api.common.agent import require_agent_row
from octop.api.common.workspace import require_running_agent
from octop.api.deps import current_user, get_server
from octop.infra.db.repos.agents import ProjectTaskFilesQuotaError
from octop.infra.errors import ErrorCode, OctopError
from octop.infra.projects import file_tasks
from octop.infra.projects.service import DEFAULT_PAGE_LIMIT, MAX_PAGE_LIMIT
from octop.infra.projects.tasks import (
    ProjectTaskService,
    TaskShareView,
    TaskSummaryView,
    expert_unavailable_error,
    files_quota_error,
    files_unavailable_error,
)
from octop.infra.server import OctopServer
from octop.infra.users.identity import User

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/projects/{project_id}/tasks")


def _task_service(server: OctopServer) -> ProjectTaskService:
    if server.services is None:
        raise OctopError(ErrorCode.INTERNAL_ERROR, "server services unavailable")
    runtime = server.app_runtime
    return ProjectTaskService(
        server.services,
        history_archive=runtime.history_archive if runtime is not None else None,
    )


def _task_payload(view: TaskSummaryView) -> dict[str, Any]:
    """Safe summary only: never session_key, artifacts, messages, paths, or
    model credentials; ``title`` is present because the caller owns the task
    or was explicitly granted reader access to its card."""
    return {
        "project_id": view.project_id,
        "thread_id": view.thread_id,
        "owner_user_id": view.owner_user_id,
        "agent_id": view.agent_id,
        "title": view.title,
        "source": view.source,
        "last_active": view.last_active,
        "created_at": view.created_at,
        "access": view.access,
        "can_read_text": view.can_read_text,
        "mode": view.mode,
        "chat_agent_id": view.chat_agent_id,
        "source_expert_id": view.source_expert_id,
    }


def _share_payload(view: TaskShareView) -> dict[str, Any]:
    """Grant metadata only — grantee id, role, and grant time; never the
    grantee's profile, the task title, or any thread content."""
    return {
        "user_id": view.user_id,
        "role": view.role,
        "granted_at": view.granted_at,
        "can_read_text": view.can_read_text,
    }


class AttachTaskBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    thread_id: str = Field(min_length=1, max_length=64)


class CreateTaskBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    agent_id: str = Field(min_length=1, max_length=64)
    expected_instructions_sha256: str = Field(
        min_length=64,
        max_length=64,
        pattern=r"^[0-9a-fA-F]{64}$",
        description="SHA-256 the user previewed on the project detail and confirmed",
    )
    expected_experts_revision: int | None = Field(
        default=None,
        ge=0,
        description=(
            "Project expert revision the client last read; required by the new "
            "frontend whenever the project has a nonempty expert list"
        ),
    )
    mode: Literal["chat", "files"] = Field(
        default="chat",
        description=(
            "Task mode. 'chat' (default, 028/029 behavior) talks through the "
            "selected expert Agent; 'files' requests the controlled per-task "
            "workspace and is refused while the server gate is closed."
        ),
    )


class GrantShareBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # The RECIPIENT of the grant; the actor is always the authenticated user.
    user_id: int = Field(ge=1)


@router.post("/links", status_code=201, summary="Attach own dashboard task to the project")
async def attach_task(
    project_id: str,
    body: AttachTaskBody,
    server: OctopServer = Depends(get_server),
    user: User = Depends(current_user),
) -> Any:
    """Members attach only their own existing Dashboard DM task; source and
    owner are server-derived. Re-attaching the same project is idempotent
    (200, no second row); a task already linked to another project returns
    409 ``PROJECT_TASK_LINK_CONFLICT``."""
    service = _task_service(server)
    agent_id = service.authorize_attach_target(
        project_id, user_id=user.id, thread_id=body.thread_id
    )
    require_agent_row(agent_id, user=user, as_user=None, server=server)
    view, created = service.attach_task(project_id, user_id=user.id, thread_id=body.thread_id)
    payload = _task_payload(view)
    if created:
        return payload
    return JSONResponse(status_code=200, content=payload)


async def _create_files_task(
    project_id: str,
    *,
    server: OctopServer,
    service: ProjectTaskService,
    user: User,
    body: CreateTaskBody,
    source_row: Any,
) -> dict[str, Any]:
    """030A files-mode create: gate → hint → precheck → B2 runtime → bind.

    The server gate stays closed by default (422). When a narrow test override
    opens it, the precheck and hint are side-effect-free; only then is the
    private runtime allocated/started through B2. Any bind failure compensates
    through the dedicated B2 cleanup (keeping a retryable marker when cleanup
    itself cannot be proven complete) and re-raises — a partial cleanup is
    never reported as success.
    """
    file_tasks.require_files_mode_enabled()
    if server.services is None:
        raise OctopError(ErrorCode.INTERNAL_ERROR, "server services unavailable")
    if not file_tasks.files_task_capability(server.services.paths)["available"]:
        raise files_unavailable_error()
    service.precheck_files_task(
        project_id,
        user_id=user.id,
        expected_instructions_sha256=body.expected_instructions_sha256,
    )
    assert server.app_runtime is not None
    manager = server.app_runtime.agent_registry
    try:
        runtime = await manager.create_project_task_file_runtime(
            owner_user_id=user.id, source_expert=source_row
        )
    except ProjectTaskFilesQuotaError as exc:
        raise files_quota_error(exc.scope) from None
    except OctopError as exc:
        raise files_unavailable_error() from exc
    try:
        view = service.bind_files_task(
            project_id,
            user_id=user.id,
            source_agent_id=body.agent_id,
            runtime_agent_id=runtime.agent_id,
            expected_instructions_sha256=body.expected_instructions_sha256,
            expected_experts_revision=body.expected_experts_revision,
            is_admin=user.is_admin,
        )
    except Exception:
        try:
            await manager.cleanup_unlinked_project_task_runtime(runtime.agent_id)
        except Exception:
            logger.exception(
                "files task create: runtime compensation failed for %s", runtime.agent_id
            )
        raise
    return _task_payload(view)


@router.post(
    "", status_code=201, summary="Create a private project task with an instruction snapshot"
)
async def create_project_task(
    project_id: str,
    body: CreateTaskBody,
    server: OctopServer = Depends(get_server),
    user: User = Depends(current_user),
) -> dict[str, Any]:
    """Members create a brand-new private Dashboard DM task for one running
    expert Agent. The thread, history projection, ``source='project'`` link,
    and an immutable snapshot of the current project instructions commit in
    ONE transaction; the active session is not rebound. A digest that no
    longer matches answers 409 ``PROJECT_INSTRUCTIONS_CHANGED`` with no task
    row, so the dashboard refreshes and asks the user to confirm again.
    Non-members and unknown projects share one 404; archived projects, team
    hosts, and non-running agents are refused. When the project has a
    nonempty 029 expert list, a missing/stale ``expected_experts_revision``
    answers 409 ``PROJECT_EXPERTS_CHANGED``, and a selected Agent that is no
    longer listed/shared/enabled/running answers the uniform recoverable 409
    ``PROJECT_EXPERT_UNAVAILABLE`` — never an ownership/existence leak. The
    response is the same safe private-task summary as manual attach — never
    the instruction text.

    ``mode='files'`` (030A) requests the controlled per-task workspace: the
    server gate is closed by default (422 ``PROJECT_TASK_FILES_UNSUPPORTED``),
    the capability probe is only a hint, quota answers 409
    ``PROJECT_TASK_FILES_QUOTA``, and a transient runtime failure answers 503
    ``PROJECT_TASK_FILES_UNAVAILABLE``. On success the response carries
    ``mode='files'``, the SOURCE expert in the public ``agent_id``, and the
    owner-only private ``chat_agent_id``/``source_expert_id``.
    """
    service = _task_service(server)
    # Membership first so an outsider cannot probe agent existence/ACL.
    experts_gated = service.authorize_create_target(
        project_id,
        user_id=user.id,
        agent_id=body.agent_id,
        expected_experts_revision=body.expected_experts_revision,
    )
    try:
        source_row = require_agent_row(body.agent_id, user=user, as_user=None, server=server)
    except OctopError as exc:
        # With the expert gate on, the list itself already passed membership;
        # an Agent that vanished or lost its grant between the two reads must
        # not surface owner/existence details to an authorized member.
        if experts_gated and exc.code in (
            ErrorCode.AGENT_NOT_FOUND,
            ErrorCode.FORBIDDEN,
            ErrorCode.AGENT_NOT_RUNNING,
            ErrorCode.AGENT_FAILED,
        ):
            raise expert_unavailable_error() from None
        raise
    if body.mode == "files":
        return await _create_files_task(
            project_id,
            server=server,
            service=service,
            user=user,
            body=body,
            source_row=source_row,
        )
    try:
        require_running_agent(server, body.agent_id)
    except OctopError as exc:
        if experts_gated and exc.code in (
            ErrorCode.AGENT_NOT_FOUND,
            ErrorCode.FORBIDDEN,
            ErrorCode.AGENT_NOT_RUNNING,
            ErrorCode.AGENT_FAILED,
        ):
            raise expert_unavailable_error() from None
        raise
    view = service.create_project_task(
        project_id,
        user_id=user.id,
        agent_id=body.agent_id,
        expected_instructions_sha256=body.expected_instructions_sha256,
        expected_experts_revision=body.expected_experts_revision,
        is_admin=user.is_admin,
    )
    return _task_payload(view)


@router.get("", summary="List project tasks visible to the caller (members only)")
async def list_tasks(
    project_id: str,
    server: OctopServer = Depends(get_server),
    user: User = Depends(current_user),
    scope: Literal["own", "shared", "all"] = Query(
        "own",
        description="own = attached by me; shared = explicitly granted to me; all = both",
    ),
    q: str = Query("", description="Title substring; % _ \\ are matched literally"),
    limit: int = Query(DEFAULT_PAGE_LIMIT, ge=1, le=MAX_PAGE_LIMIT),
    offset: int = Query(0, ge=0),
) -> dict[str, Any]:
    """``own`` (default) keeps the private slice-1 behavior; ``shared`` lists
    cards other members granted and have not revoked; ``all`` merges both in
    one stable order. Totals, filters, and paging never reveal another
    member's unshared private tasks."""
    page = _task_service(server).list_tasks(
        project_id, user_id=user.id, scope=scope, q=q, limit=limit, offset=offset
    )
    return {
        "items": [_task_payload(view) for view in page.items],
        "limit": page.limit,
        "offset": page.offset,
        "has_more": page.has_more,
    }


@router.get("/{thread_id}", summary="Get one project task visible to the caller")
async def get_task(
    project_id: str,
    thread_id: str,
    server: OctopServer = Depends(get_server),
    user: User = Depends(current_user),
) -> dict[str, Any]:
    """Own task, or a card the owner explicitly shared with the caller and
    has not revoked (``access`` names which). Non-members, foreign tasks,
    cross-project tasks, and unknown ids all return the same 404."""
    view = _task_service(server).get_task(project_id, thread_id, user_id=user.id)
    return _task_payload(view)


@router.get("/{thread_id}/shares", summary="List active card shares for own task")
async def list_task_shares(
    project_id: str,
    thread_id: str,
    server: OctopServer = Depends(get_server),
    user: User = Depends(current_user),
) -> dict[str, Any]:
    views = _task_service(server).list_shares(project_id, thread_id, user_id=user.id)
    return {"items": [_share_payload(view) for view in views]}


@router.post(
    "/{thread_id}/shares", status_code=201, summary="Share own task card with a project member"
)
async def grant_task_share(
    project_id: str,
    thread_id: str,
    body: GrantShareBody,
    server: OctopServer = Depends(get_server),
    user: User = Depends(current_user),
) -> Any:
    view, created = _task_service(server).grant_share(
        project_id, thread_id, user_id=user.id, grantee_user_id=body.user_id
    )
    payload = _share_payload(view)
    return payload if created else JSONResponse(status_code=200, content=payload)


@router.delete(
    "/{thread_id}/shares/{user_id}",
    status_code=204,
    summary="Revoke a card share for own task",
)
async def revoke_task_share(
    project_id: str,
    thread_id: str,
    user_id: int,
    server: OctopServer = Depends(get_server),
    user: User = Depends(current_user),
) -> None:
    _task_service(server).revoke_share(
        project_id, thread_id, user_id=user.id, grantee_user_id=user_id
    )


@router.post(
    "/{thread_id}/shares/{user_id}/text",
    status_code=201,
    summary="Grant task conversation text to a card recipient",
)
async def grant_task_text(
    project_id: str,
    thread_id: str,
    user_id: int,
    server: OctopServer = Depends(get_server),
    user: User = Depends(current_user),
) -> Any:
    view, created = _task_service(server).grant_text_share(
        project_id, thread_id, user_id=user.id, grantee_user_id=user_id
    )
    payload = {"user_id": view.user_id, "granted_at": view.granted_at}
    return payload if created else JSONResponse(status_code=200, content=payload)


@router.delete(
    "/{thread_id}/shares/{user_id}/text",
    status_code=204,
    summary="Revoke task conversation text without revoking its card",
)
async def revoke_task_text(
    project_id: str,
    thread_id: str,
    user_id: int,
    server: OctopServer = Depends(get_server),
    user: User = Depends(current_user),
) -> None:
    _task_service(server).revoke_text_share(
        project_id, thread_id, user_id=user.id, grantee_user_id=user_id
    )


@router.get("/{thread_id}/messages", summary="Read authorized task conversation text")
async def get_task_messages(
    project_id: str,
    thread_id: str,
    server: OctopServer = Depends(get_server),
    user: User = Depends(current_user),
    limit: int = Query(50, ge=1, le=100),
    before_seq: int | None = Query(None, gt=0),
) -> dict[str, Any]:
    page = _task_service(server).read_task_messages(
        project_id, thread_id, user_id=user.id, limit=limit, before_seq=before_seq
    )
    return {
        "status": page.status,
        "items": [
            {
                "seq": item.seq,
                "role": item.role,
                "text": item.text,
                "created_at": item.created_at,
                "truncated": item.truncated,
            }
            for item in page.items
        ],
        "has_more": page.has_more,
        "next_before_seq": page.next_before_seq,
    }


@router.delete("/{thread_id}", status_code=204, summary="Detach own task from the project")
async def detach_task(
    project_id: str,
    thread_id: str,
    server: OctopServer = Depends(get_server),
    user: User = Depends(current_user),
) -> None:
    """Removes only the project attribution; the original conversation,
    history, attachments, and dashboard session stay with the task owner.
    Repeated deletes are 404s with no extra writes."""
    _task_service(server).detach_task(project_id, thread_id, user_id=user.id)
    return None
