"""Thin HTTP adapter for project todo priorities and tags."""

from __future__ import annotations

from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from starlette.concurrency import run_in_threadpool

from octop.api.deps import current_user, get_server
from octop.infra.db.repos._base import UNSET
from octop.infra.errors import ErrorCode, OctopError
from octop.infra.projects.todo_catalog import ProjectTodoCatalogService, validate_catalog_name
from octop.infra.server import OctopServer
from octop.infra.users.identity import User

router = APIRouter(prefix="/projects/{project_id}/plan")
Color = Literal["red", "orange", "yellow", "green", "blue", "purple", "gray"]
Revision = Annotated[int, Field(strict=True, ge=1)]
OptionId = Annotated[str, Field(strict=True, min_length=1, max_length=64)]


def _service(server: OctopServer) -> ProjectTodoCatalogService:
    if server.services is None:
        raise OctopError(ErrorCode.INTERNAL_ERROR, "server services unavailable")
    return ProjectTodoCatalogService(server.services)


class RevisionBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_revision: Revision


class CreateOptionBody(RevisionBody):
    name: Annotated[str, Field(strict=True)]
    color: Color

    @field_validator("name")
    @classmethod
    def _name(cls, value: str) -> str:
        return validate_catalog_name(value)


class UpdateOptionBody(RevisionBody):
    name: Annotated[str, Field(strict=True)] | None = None
    color: Color | None = None

    @field_validator("name")
    @classmethod
    def _name(cls, value: str | None) -> str | None:
        return validate_catalog_name(value) if value is not None else None

    @model_validator(mode="after")
    def _fields(self) -> UpdateOptionBody:
        if any(
            getattr(self, field) is None
            for field in ("name", "color")
            if field in self.model_fields_set
        ):
            raise ValueError("catalog name and color cannot be null")
        return self


class OrderPrioritiesBody(RevisionBody):
    priority_ids: list[OptionId] = Field(max_length=32)

    @field_validator("priority_ids")
    @classmethod
    def _unique(cls, values: list[str]) -> list[str]:
        if len(values) != len(set(values)):
            raise ValueError("priority_ids must not repeat")
        return values


class PriorityResponse(BaseModel):
    priority_id: str
    name: str
    color: Color
    position: int
    archived_at: int | None
    created_at: int
    updated_at: int


class TagResponse(BaseModel):
    tag_id: str
    name: str
    color: Color
    archived_at: int | None
    created_at: int
    updated_at: int


class CatalogResponse(BaseModel):
    project_id: str
    revision: int
    server_today: str
    server_timezone: str
    priorities: list[PriorityResponse]
    tags: list[TagResponse]


class PriorityMutationResponse(BaseModel):
    revision: int
    item: PriorityResponse


class TagMutationResponse(BaseModel):
    revision: int
    item: TagResponse


class PriorityOrderResponse(BaseModel):
    revision: int
    priorities: list[PriorityResponse]


@router.get(
    "/catalog",
    response_model=CatalogResponse,
    summary="Read the project's todo catalog and server calendar day",
)
async def get_catalog(
    project_id: str, server: OctopServer = Depends(get_server), user: User = Depends(current_user)
) -> dict[str, Any]:
    return await run_in_threadpool(_service(server).get_catalog, project_id, user_id=user.id)


@router.post(
    "/priorities",
    response_model=PriorityMutationResponse,
    status_code=201,
    summary="Create a project todo priority (owner/admin)",
)
async def create_priority(
    project_id: str,
    body: CreateOptionBody,
    server: OctopServer = Depends(get_server),
    user: User = Depends(current_user),
) -> dict[str, Any]:
    return await run_in_threadpool(
        _service(server).create_option,
        project_id,
        actor_user_id=user.id,
        expected_revision=body.expected_revision,
        kind="priority",
        name=body.name,
        color=body.color,
    )


@router.put(
    "/priorities/order",
    response_model=PriorityOrderResponse,
    summary="Replace the complete active priority order (owner/admin)",
)
async def order_priorities(
    project_id: str,
    body: OrderPrioritiesBody,
    server: OctopServer = Depends(get_server),
    user: User = Depends(current_user),
) -> dict[str, Any]:
    return await run_in_threadpool(
        _service(server).order_priorities,
        project_id,
        actor_user_id=user.id,
        expected_revision=body.expected_revision,
        priority_ids=body.priority_ids,
    )


@router.patch(
    "/priorities/{priority_id}",
    response_model=PriorityMutationResponse,
    summary="Rename or recolor a project todo priority (owner/admin)",
)
async def update_priority(
    project_id: str,
    priority_id: str,
    body: UpdateOptionBody,
    server: OctopServer = Depends(get_server),
    user: User = Depends(current_user),
) -> dict[str, Any]:
    return await run_in_threadpool(
        _service(server).update_option,
        project_id,
        actor_user_id=user.id,
        expected_revision=body.expected_revision,
        kind="priority",
        option_id=priority_id,
        name=body.name if "name" in body.model_fields_set else UNSET,
        color=body.color if "color" in body.model_fields_set else UNSET,
    )


@router.post(
    "/priorities/{priority_id}/archive",
    response_model=PriorityMutationResponse,
    summary="Archive a project todo priority (owner/admin)",
)
async def archive_priority(
    project_id: str,
    priority_id: str,
    body: RevisionBody,
    server: OctopServer = Depends(get_server),
    user: User = Depends(current_user),
) -> dict[str, Any]:
    return await run_in_threadpool(
        _service(server).set_archived,
        project_id,
        actor_user_id=user.id,
        expected_revision=body.expected_revision,
        kind="priority",
        option_id=priority_id,
        archived=True,
    )


@router.post(
    "/priorities/{priority_id}/restore",
    response_model=PriorityMutationResponse,
    summary="Restore a project todo priority at the end (owner/admin)",
)
async def restore_priority(
    project_id: str,
    priority_id: str,
    body: RevisionBody,
    server: OctopServer = Depends(get_server),
    user: User = Depends(current_user),
) -> dict[str, Any]:
    return await run_in_threadpool(
        _service(server).set_archived,
        project_id,
        actor_user_id=user.id,
        expected_revision=body.expected_revision,
        kind="priority",
        option_id=priority_id,
        archived=False,
    )


@router.post(
    "/tags",
    response_model=TagMutationResponse,
    status_code=201,
    summary="Create a project todo tag (owner/admin)",
)
async def create_tag(
    project_id: str,
    body: CreateOptionBody,
    server: OctopServer = Depends(get_server),
    user: User = Depends(current_user),
) -> dict[str, Any]:
    return await run_in_threadpool(
        _service(server).create_option,
        project_id,
        actor_user_id=user.id,
        expected_revision=body.expected_revision,
        kind="tag",
        name=body.name,
        color=body.color,
    )


@router.patch(
    "/tags/{tag_id}",
    response_model=TagMutationResponse,
    summary="Rename or recolor a project todo tag (owner/admin)",
)
async def update_tag(
    project_id: str,
    tag_id: str,
    body: UpdateOptionBody,
    server: OctopServer = Depends(get_server),
    user: User = Depends(current_user),
) -> dict[str, Any]:
    return await run_in_threadpool(
        _service(server).update_option,
        project_id,
        actor_user_id=user.id,
        expected_revision=body.expected_revision,
        kind="tag",
        option_id=tag_id,
        name=body.name if "name" in body.model_fields_set else UNSET,
        color=body.color if "color" in body.model_fields_set else UNSET,
    )


@router.post(
    "/tags/{tag_id}/archive",
    response_model=TagMutationResponse,
    summary="Archive a project todo tag (owner/admin)",
)
async def archive_tag(
    project_id: str,
    tag_id: str,
    body: RevisionBody,
    server: OctopServer = Depends(get_server),
    user: User = Depends(current_user),
) -> dict[str, Any]:
    return await run_in_threadpool(
        _service(server).set_archived,
        project_id,
        actor_user_id=user.id,
        expected_revision=body.expected_revision,
        kind="tag",
        option_id=tag_id,
        archived=True,
    )


@router.post(
    "/tags/{tag_id}/restore",
    response_model=TagMutationResponse,
    summary="Restore a project todo tag (owner/admin)",
)
async def restore_tag(
    project_id: str,
    tag_id: str,
    body: RevisionBody,
    server: OctopServer = Depends(get_server),
    user: User = Depends(current_user),
) -> dict[str, Any]:
    return await run_in_threadpool(
        _service(server).set_archived,
        project_id,
        actor_user_id=user.id,
        expected_revision=body.expected_revision,
        kind="tag",
        option_id=tag_id,
        archived=False,
    )
