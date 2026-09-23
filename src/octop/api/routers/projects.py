"""HTTP API for 熊宝-Agent project spaces (first slice: CRUD + members)."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Query, status
from pydantic import BaseModel, field_validator

from octop.api.deps import current_user, get_server
from octop.infra.errors import ErrorCode, OctopError
from octop.infra.projects.service import (
    DEFAULT_PAGE_LIMIT,
    MAX_PAGE_LIMIT,
    MAX_PROJECT_DESCRIPTION_LENGTH,
    MAX_PROJECT_INSTRUCTIONS_LENGTH,
    ProjectDetailView,
    ProjectService,
    ProjectView,
    validate_project_name,
    validate_project_text,
)
from octop.infra.server import OctopServer
from octop.infra.users.identity import User

router = APIRouter(prefix="/projects")


class CreateProjectBody(BaseModel):
    name: str
    description: str = ""
    instructions: str = ""

    @field_validator("name")
    @classmethod
    def _validate_name(cls, v: str) -> str:
        return validate_project_name(v)

    @field_validator("description")
    @classmethod
    def _validate_description(cls, v: str) -> str:
        return validate_project_text(
            v, field="description", max_length=MAX_PROJECT_DESCRIPTION_LENGTH
        )

    @field_validator("instructions")
    @classmethod
    def _validate_instructions(cls, v: str) -> str:
        return validate_project_text(
            v, field="instructions", max_length=MAX_PROJECT_INSTRUCTIONS_LENGTH
        )


class UpdateProjectBody(BaseModel):
    name: str | None = None
    description: str | None = None
    instructions: str | None = None

    @field_validator("name")
    @classmethod
    def _validate_name(cls, v: str | None) -> str | None:
        return None if v is None else validate_project_name(v)

    @field_validator("description")
    @classmethod
    def _validate_description(cls, v: str | None) -> str | None:
        if v is None:
            return None
        return validate_project_text(
            v, field="description", max_length=MAX_PROJECT_DESCRIPTION_LENGTH
        )

    @field_validator("instructions")
    @classmethod
    def _validate_instructions(cls, v: str | None) -> str | None:
        if v is None:
            return None
        return validate_project_text(
            v, field="instructions", max_length=MAX_PROJECT_INSTRUCTIONS_LENGTH
        )


def _project_service(server: OctopServer) -> ProjectService:
    if server.services is None:
        raise OctopError(ErrorCode.INTERNAL_ERROR, "server services unavailable")
    return ProjectService(server.services)


def _item_payload(view: ProjectView) -> dict[str, Any]:
    return {
        "project_id": view.project_id,
        "name": view.name,
        "description": view.description,
        "my_role": view.my_role,
        "member_count": view.member_count,
        "created_at": view.created_at,
        "updated_at": view.updated_at,
    }


def _detail_payload(view: ProjectDetailView) -> dict[str, Any]:
    return {
        **_item_payload(view),
        "instructions": view.instructions,
    }


@router.get("", summary="List projects visible to the current user")
async def list_projects(
    server: OctopServer = Depends(get_server),
    user: User = Depends(current_user),
    q: str = Query(""),
    limit: int = Query(DEFAULT_PAGE_LIMIT, ge=1, le=MAX_PAGE_LIMIT),
    offset: int = Query(0, ge=0),
) -> dict[str, Any]:
    page = _project_service(server).list_projects(user_id=user.id, q=q, limit=limit, offset=offset)
    return {
        "items": [_item_payload(item) for item in page.items],
        "limit": page.limit,
        "offset": page.offset,
        "has_more": page.has_more,
    }


@router.post("", status_code=status.HTTP_201_CREATED, summary="Create a project")
async def create_project(
    body: CreateProjectBody,
    server: OctopServer = Depends(get_server),
    user: User = Depends(current_user),
) -> dict[str, Any]:
    view = _project_service(server).create_project(
        creator_user_id=user.id,
        name=body.name,
        description=body.description,
        instructions=body.instructions,
    )
    return _detail_payload(view)


@router.get("/{project_id}", summary="Get one accessible project")
async def get_project(
    project_id: str,
    server: OctopServer = Depends(get_server),
    user: User = Depends(current_user),
) -> dict[str, Any]:
    return _detail_payload(_project_service(server).get_view(project_id, user_id=user.id))


@router.patch("/{project_id}", summary="Update project settings")
async def update_project(
    project_id: str,
    body: UpdateProjectBody,
    server: OctopServer = Depends(get_server),
    user: User = Depends(current_user),
) -> dict[str, Any]:
    view = _project_service(server).update_project(
        project_id,
        actor_user_id=user.id,
        name=body.name,
        description=body.description,
        instructions=body.instructions,
    )
    return _detail_payload(view)


@router.get("/{project_id}/members", summary="List project members")
async def list_project_members(
    project_id: str,
    server: OctopServer = Depends(get_server),
    user: User = Depends(current_user),
) -> list[dict[str, Any]]:
    members = _project_service(server).list_members(project_id, user_id=user.id)
    return [
        {"user_id": member.user_id, "username": member.username, "role": member.role}
        for member in members
    ]
