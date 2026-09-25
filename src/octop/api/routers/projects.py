"""HTTP API for 熊宝-Agent project spaces (CRUD, members, invites, requests)."""

from __future__ import annotations

from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Path, Query, status
from pydantic import BaseModel, Field, field_validator

from octop.api.deps import current_user, get_server
from octop.infra.errors import ErrorCode, OctopError
from octop.infra.projects.service import (
    DEFAULT_PAGE_LIMIT,
    INVITE_DEFAULT_EXPIRES_DAYS,
    INVITE_MAX_EXPIRES_DAYS,
    INVITE_MIN_EXPIRES_DAYS,
    MAX_PAGE_LIMIT,
    MAX_PROJECT_DESCRIPTION_LENGTH,
    MAX_PROJECT_INSTRUCTIONS_LENGTH,
    ProjectDetailView,
    ProjectInviteView,
    ProjectJoinRequestView,
    ProjectService,
    ProjectView,
    validate_project_name,
    validate_project_text,
)
from octop.infra.projects.tasks import instructions_sha256
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


class CreateInviteBody(BaseModel):
    requires_approval: bool = False
    expires_in_days: int = Field(
        INVITE_DEFAULT_EXPIRES_DAYS, ge=INVITE_MIN_EXPIRES_DAYS, le=INVITE_MAX_EXPIRES_DAYS
    )


class AcceptInviteBody(BaseModel):
    token: str = Field(max_length=200)


class UpdateMemberRoleBody(BaseModel):
    role: Literal["member", "admin"]


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
    # Members see the digest of the CURRENT instructions so the task-creation
    # modal can confirm the exact text it previewed; the project list never
    # carries it.
    return {
        **_item_payload(view),
        "instructions": view.instructions,
        "instructions_sha256": instructions_sha256(view.instructions),
    }


def _invite_payload(view: ProjectInviteView) -> dict[str, Any]:
    return {
        "invite_id": view.invite_id,
        "project_id": view.project_id,
        "role": view.role,
        "requires_approval": view.requires_approval,
        "created_by": view.created_by,
        "created_at": view.created_at,
        "expires_at": view.expires_at,
        "revoked_at": view.revoked_at,
        "consumed_at": view.consumed_at,
        "consumed_by_user_id": view.consumed_by_user_id,
        "status": view.status,
    }


def _request_payload(view: ProjectJoinRequestView) -> dict[str, Any]:
    return {
        "request_id": view.request_id,
        "project_id": view.project_id,
        "invite_id": view.invite_id,
        "user_id": view.user_id,
        "username": view.username,
        "status": view.status,
        "requested_at": view.requested_at,
        "resolved_at": view.resolved_at,
        "resolved_by": view.resolved_by,
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


@router.patch("/{project_id}/members/{user_id}", summary="Change a member role (owner only)")
async def update_project_member_role(
    project_id: str,
    user_id: Annotated[int, Path(ge=1, le=2**63 - 1)],
    body: UpdateMemberRoleBody,
    server: OctopServer = Depends(get_server),
    user: User = Depends(current_user),
) -> dict[str, Any]:
    member = _project_service(server).set_member_role(
        project_id, user_id, role=body.role, actor_user_id=user.id
    )
    return {"user_id": member.user_id, "username": member.username, "role": member.role}


@router.delete("/{project_id}/members/{user_id}", status_code=204, summary="Remove a member")
async def remove_project_member(
    project_id: str,
    user_id: Annotated[int, Path(ge=1, le=2**63 - 1)],
    server: OctopServer = Depends(get_server),
    user: User = Depends(current_user),
) -> None:
    _project_service(server).remove_member(project_id, user_id, actor_user_id=user.id)
    return None


@router.post("/invites/accept", summary="Accept a project invite link")
async def accept_project_invite(
    body: AcceptInviteBody,
    server: OctopServer = Depends(get_server),
    user: User = Depends(current_user),
) -> dict[str, Any]:
    accepted = _project_service(server).accept_invite(body.token, user_id=user.id)
    if accepted.status == "joined":
        return {
            "status": accepted.status,
            "project_id": accepted.project_id,
            "role": accepted.role,
        }
    return {
        "status": accepted.status,
        "project_id": accepted.project_id,
        "request_id": accepted.request_id,
    }


@router.post(
    "/{project_id}/invites",
    status_code=status.HTTP_201_CREATED,
    summary="Create a project invite link (owner/admin)",
)
async def create_project_invite(
    project_id: str,
    body: CreateInviteBody,
    server: OctopServer = Depends(get_server),
    user: User = Depends(current_user),
) -> dict[str, Any]:
    created = _project_service(server).create_invite(
        project_id,
        actor_user_id=user.id,
        requires_approval=body.requires_approval,
        expires_in_days=body.expires_in_days,
    )
    # The plaintext token is exposed exactly once, in this response only.
    return {**_invite_payload(created), "token": created.token}


@router.get("/{project_id}/invites", summary="List project invites (owner/admin)")
async def list_project_invites(
    project_id: str,
    server: OctopServer = Depends(get_server),
    user: User = Depends(current_user),
) -> dict[str, Any]:
    views = _project_service(server).list_invites(project_id, actor_user_id=user.id)
    return {"items": [_invite_payload(view) for view in views]}


@router.post("/{project_id}/invites/{invite_id}/revoke", summary="Revoke a project invite")
async def revoke_project_invite(
    project_id: str,
    invite_id: str,
    server: OctopServer = Depends(get_server),
    user: User = Depends(current_user),
) -> dict[str, Any]:
    view = _project_service(server).revoke_invite(project_id, invite_id, actor_user_id=user.id)
    return _invite_payload(view)


@router.get("/{project_id}/join-requests", summary="List join requests (owner/admin)")
async def list_project_join_requests(
    project_id: str,
    server: OctopServer = Depends(get_server),
    user: User = Depends(current_user),
    status: Literal["pending", "approved", "rejected"] | None = Query(None),
) -> dict[str, Any]:
    views = _project_service(server).list_join_requests(
        project_id, actor_user_id=user.id, status=status
    )
    return {"items": [_request_payload(view) for view in views]}


@router.post("/{project_id}/join-requests/{request_id}/approve", summary="Approve a join request")
async def approve_project_join_request(
    project_id: str,
    request_id: str,
    server: OctopServer = Depends(get_server),
    user: User = Depends(current_user),
) -> dict[str, Any]:
    view = _project_service(server).approve_join_request(
        project_id, request_id, actor_user_id=user.id
    )
    return _request_payload(view)


@router.post("/{project_id}/join-requests/{request_id}/reject", summary="Reject a join request")
async def reject_project_join_request(
    project_id: str,
    request_id: str,
    server: OctopServer = Depends(get_server),
    user: User = Depends(current_user),
) -> dict[str, Any]:
    view = _project_service(server).reject_join_request(
        project_id, request_id, actor_user_id=user.id
    )
    return _request_payload(view)
