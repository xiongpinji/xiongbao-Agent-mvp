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

from typing import Any, Literal

from fastapi import APIRouter, Depends, Query
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from octop.api.common.agent import require_agent_row
from octop.api.common.workspace import require_running_agent
from octop.api.deps import current_user, get_server
from octop.infra.errors import ErrorCode, OctopError
from octop.infra.projects.service import DEFAULT_PAGE_LIMIT, MAX_PAGE_LIMIT
from octop.infra.projects.tasks import (
    ProjectTaskService,
    TaskShareView,
    TaskSummaryView,
    expert_unavailable_error,
)
from octop.infra.server import OctopServer
from octop.infra.users.identity import User

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
    longer listed/shared/enabled answers the uniform recoverable 409
    ``PROJECT_EXPERT_UNAVAILABLE`` — never an ownership/existence leak. The
    response is the same safe private-task summary as manual attach — never
    the instruction text."""
    service = _task_service(server)
    # Membership first so an outsider cannot probe agent existence/ACL.
    experts_gated = service.authorize_create_target(
        project_id,
        user_id=user.id,
        agent_id=body.agent_id,
        expected_experts_revision=body.expected_experts_revision,
    )
    try:
        require_agent_row(body.agent_id, user=user, as_user=None, server=server)
    except OctopError as exc:
        # With the expert gate on, the list itself already passed membership;
        # an Agent that vanished or lost its grant between the two reads must
        # not surface owner/existence details to an authorized member.
        if experts_gated and exc.code in (ErrorCode.AGENT_NOT_FOUND, ErrorCode.FORBIDDEN):
            raise expert_unavailable_error() from None
        raise
    require_running_agent(server, body.agent_id)
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
