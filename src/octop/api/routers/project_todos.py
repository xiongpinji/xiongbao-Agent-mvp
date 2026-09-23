"""HTTP API for 熊宝-Agent project todos (PS-04 plan table/board).

Thin transport only: validation and authorization live in
:class:`octop.infra.projects.todos.ProjectTodoService`, SQL in
``ProjectTodoRepo``. ``/bulk`` is declared before ``/{todo_id}`` so the
literal path never loses to the path parameter. DELETE carries
``expected_version`` as a required query parameter because DELETE requests
have no body.
"""

from __future__ import annotations

from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from octop.api.deps import current_user, get_server
from octop.infra.db.repos._base import UNSET
from octop.infra.errors import ErrorCode, OctopError
from octop.infra.projects.service import DEFAULT_PAGE_LIMIT, MAX_PAGE_LIMIT
from octop.infra.projects.todos import (
    BULK_MAX_ITEMS,
    ProjectTodoService,
    TodoView,
    validate_todo_description,
    validate_todo_title,
)
from octop.infra.server import OctopServer
from octop.infra.users.identity import User

router = APIRouter(prefix="/projects/{project_id}/todos")

TodoStatus = Literal["todo", "in_progress", "done"]


def _todo_service(server: OctopServer) -> ProjectTodoService:
    if server.services is None:
        raise OctopError(ErrorCode.INTERNAL_ERROR, "server services unavailable")
    return ProjectTodoService(server.services)


def _todo_payload(view: TodoView) -> dict[str, Any]:
    return {
        "todo_id": view.todo_id,
        "project_id": view.project_id,
        "title": view.title,
        "description": view.description,
        "status": view.status,
        "creator_user_id": view.creator_user_id,
        "assignee_user_id": view.assignee_user_id,
        "version": view.version,
        "created_at": view.created_at,
        "updated_at": view.updated_at,
    }


class CreateTodoBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str
    description: str = ""
    assignee_user_id: Annotated[int, Field(ge=1)] | None = None

    @field_validator("title")
    @classmethod
    def _validate_title(cls, v: str) -> str:
        return validate_todo_title(v)

    @field_validator("description")
    @classmethod
    def _validate_description(cls, v: str) -> str:
        return validate_todo_description(v)


class UpdateTodoBody(BaseModel):
    """Partial patch. ``assignee_user_id: null`` explicitly clears the assignee
    (owner/admin only); omitted fields are left untouched. Explicit nulls for
    title/description/status are treated as omitted — they cannot be cleared."""

    model_config = ConfigDict(extra="forbid")

    expected_version: Annotated[int, Field(ge=1)]
    title: str | None = None
    description: str | None = None
    status: TodoStatus | None = None
    assignee_user_id: Annotated[int, Field(ge=1)] | None = None

    @field_validator("title")
    @classmethod
    def _validate_title(cls, v: str | None) -> str | None:
        return None if v is None else validate_todo_title(v)

    @field_validator("description")
    @classmethod
    def _validate_description(cls, v: str | None) -> str | None:
        return None if v is None else validate_todo_description(v)


class BulkTodoItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    todo_id: str = Field(min_length=1, max_length=64)
    expected_version: Annotated[int, Field(ge=1)]


class BulkUpdateBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: list[BulkTodoItem] = Field(min_length=1, max_length=BULK_MAX_ITEMS)
    status: TodoStatus | None = None
    assignee_user_id: Annotated[int, Field(ge=1)] | None = None

    @model_validator(mode="after")
    def _check_batch(self) -> BulkUpdateBody:
        ids = [item.todo_id for item in self.items]
        if len(set(ids)) != len(ids):
            raise ValueError("bulk items must have unique todo ids")
        if self.status is None and "assignee_user_id" not in self.model_fields_set:
            raise ValueError("bulk update requires status or assignee_user_id")
        return self


@router.post("", status_code=201, summary="Create a project todo")
async def create_todo(
    project_id: str,
    body: CreateTodoBody,
    server: OctopServer = Depends(get_server),
    user: User = Depends(current_user),
) -> dict[str, Any]:
    """Any member may create; ordinary members may only assign themselves."""
    view = _todo_service(server).create_todo(
        project_id,
        actor_user_id=user.id,
        title=body.title,
        description=body.description,
        assignee_user_id=body.assignee_user_id,
    )
    return _todo_payload(view)


@router.post("/bulk", summary="Bulk-update todos atomically (owner/admin)")
async def bulk_update_todos(
    project_id: str,
    body: BulkUpdateBody,
    server: OctopServer = Depends(get_server),
    user: User = Depends(current_user),
) -> dict[str, Any]:
    """All-or-nothing: any stale version, foreign, or deleted id rejects the
    whole batch with nothing written. Returns every updated row."""
    views = _todo_service(server).bulk_update_todos(
        project_id,
        actor_user_id=user.id,
        items=[(item.todo_id, item.expected_version) for item in body.items],
        status=body.status if body.status is not None else UNSET,
        assignee_user_id=(
            body.assignee_user_id if "assignee_user_id" in body.model_fields_set else UNSET
        ),
    )
    return {"items": [_todo_payload(view) for view in views]}


@router.get("", summary="List project todos (members only)")
async def list_todos(
    project_id: str,
    server: OctopServer = Depends(get_server),
    user: User = Depends(current_user),
    q: str = Query("", description="Title substring; % _ \\ are matched literally"),
    status: TodoStatus | None = Query(None, description="Board column filter"),
    assignee_user_id: Annotated[int, Query(ge=1)] | None = None,
    limit: int = Query(DEFAULT_PAGE_LIMIT, ge=1, le=MAX_PAGE_LIMIT),
    offset: int = Query(0, ge=0),
) -> dict[str, Any]:
    page = _todo_service(server).list_todos(
        project_id,
        user_id=user.id,
        q=q,
        status=status,
        assignee_user_id=assignee_user_id,
        limit=limit,
        offset=offset,
    )
    return {
        "items": [_todo_payload(view) for view in page.items],
        "limit": page.limit,
        "offset": page.offset,
        "has_more": page.has_more,
    }


@router.get("/{todo_id}", summary="Get one project todo")
async def get_todo(
    project_id: str,
    todo_id: str,
    server: OctopServer = Depends(get_server),
    user: User = Depends(current_user),
) -> dict[str, Any]:
    view = _todo_service(server).get_todo(project_id, todo_id, user_id=user.id)
    return _todo_payload(view)


@router.patch("/{todo_id}", summary="Update a project todo (optimistic version)")
async def update_todo(
    project_id: str,
    todo_id: str,
    body: UpdateTodoBody,
    server: OctopServer = Depends(get_server),
    user: User = Depends(current_user),
) -> dict[str, Any]:
    """Requires ``expected_version``; a stale version returns 409 with no
    partial write. Owner/admin edit everything; the creator or current
    assignee may edit title/description/status but never the assignee."""
    view = _todo_service(server).update_todo(
        project_id,
        todo_id,
        actor_user_id=user.id,
        expected_version=body.expected_version,
        title=body.title if body.title is not None else UNSET,
        description=body.description if body.description is not None else UNSET,
        status=body.status if body.status is not None else UNSET,
        assignee_user_id=(
            body.assignee_user_id if "assignee_user_id" in body.model_fields_set else UNSET
        ),
    )
    return _todo_payload(view)


@router.delete("/{todo_id}", status_code=204, summary="Soft-delete a project todo")
async def delete_todo(
    project_id: str,
    todo_id: str,
    expected_version: Annotated[
        int,
        Query(
            ge=1,
            description="Optimistic version guard sent as a query parameter",
        ),
    ],
    server: OctopServer = Depends(get_server),
    user: User = Depends(current_user),
) -> None:
    """Owner/admin or the creator only; deleted todos disappear from every
    read path and can never be returned again."""
    _todo_service(server).delete_todo(
        project_id,
        todo_id,
        actor_user_id=user.id,
        expected_version=expected_version,
    )
    return None
