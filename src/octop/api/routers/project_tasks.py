"""HTTP API for 熊宝-Agent project task links (PS-05 slice 1).

Thin transport only: membership/ownership policy lives in
:class:`octop.infra.projects.tasks.ProjectTaskService`, race-safe SQL in
``ProjectTaskRepo``. The literal ``/links`` route is declared before
``/{thread_id}`` so the fixed path never loses to the path parameter when
same-method routes are added later. The attach route runs the existing
``require_agent_row`` ACL check before the service write — agent
accessibility (owner/shared/admin) is never reduced to SQL. Operators are
always ``current_user.id``; this router accepts no ``as_user`` and project or
instance admin identity never bypasses task ownership.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Query
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from octop.api.common.agent import require_agent_row
from octop.api.deps import current_user, get_server
from octop.infra.errors import ErrorCode, OctopError
from octop.infra.projects.service import DEFAULT_PAGE_LIMIT, MAX_PAGE_LIMIT
from octop.infra.projects.tasks import ProjectTaskService, TaskSummaryView
from octop.infra.server import OctopServer
from octop.infra.users.identity import User

router = APIRouter(prefix="/projects/{project_id}/tasks")


def _task_service(server: OctopServer) -> ProjectTaskService:
    if server.services is None:
        raise OctopError(ErrorCode.INTERNAL_ERROR, "server services unavailable")
    return ProjectTaskService(server.services)


def _task_payload(view: TaskSummaryView) -> dict[str, Any]:
    """Safe summary only: never session_key, artifacts, messages, paths, or
    model credentials; ``title`` is present because the caller owns the task."""
    return {
        "project_id": view.project_id,
        "thread_id": view.thread_id,
        "owner_user_id": view.owner_user_id,
        "agent_id": view.agent_id,
        "title": view.title,
        "source": view.source,
        "last_active": view.last_active,
        "created_at": view.created_at,
    }


class AttachTaskBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    thread_id: str = Field(min_length=1, max_length=64)


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


@router.get("", summary="List own project tasks (members only)")
async def list_tasks(
    project_id: str,
    server: OctopServer = Depends(get_server),
    user: User = Depends(current_user),
    q: str = Query("", description="Title substring; % _ \\ are matched literally"),
    limit: int = Query(DEFAULT_PAGE_LIMIT, ge=1, le=MAX_PAGE_LIMIT),
    offset: int = Query(0, ge=0),
) -> dict[str, Any]:
    """Only the caller's own tasks in this project. Totals, filters, and
    paging never reveal another member's private tasks."""
    page = _task_service(server).list_tasks(
        project_id, user_id=user.id, q=q, limit=limit, offset=offset
    )
    return {
        "items": [_task_payload(view) for view in page.items],
        "limit": page.limit,
        "offset": page.offset,
        "has_more": page.has_more,
    }


@router.get("/{thread_id}", summary="Get one own project task")
async def get_task(
    project_id: str,
    thread_id: str,
    server: OctopServer = Depends(get_server),
    user: User = Depends(current_user),
) -> dict[str, Any]:
    """Non-members, foreign tasks, cross-project tasks, and unknown ids all
    return the same 404."""
    view = _task_service(server).get_task(project_id, thread_id, user_id=user.id)
    return _task_payload(view)


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
