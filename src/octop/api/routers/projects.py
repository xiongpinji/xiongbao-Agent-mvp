"""HTTP API for 熊宝-Agent project spaces (CRUD, members, invites, requests)."""

from __future__ import annotations

import asyncio
import json
from functools import partial
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Path, Query, Request, status
from pydantic import BaseModel, ConfigDict, Field, field_validator

from octop.api.deps import current_user, get_server
from octop.infra.errors import ErrorCode, OctopError
from octop.infra.projects import file_tasks
from octop.infra.projects.connectors import ProjectPublicConnectorService, PublicConnectorCollection
from octop.infra.projects.service import (
    DEFAULT_PAGE_LIMIT,
    INVITE_DEFAULT_EXPIRES_DAYS,
    INVITE_MAX_EXPIRES_DAYS,
    INVITE_MIN_EXPIRES_DAYS,
    MAX_PAGE_LIMIT,
    MAX_PROJECT_DESCRIPTION_LENGTH,
    MAX_PROJECT_EXPERTS,
    MAX_PROJECT_INSTRUCTIONS_LENGTH,
    ProjectDetailView,
    ProjectExpertsView,
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

PublicConnectorRevision = Annotated[
    int,
    Field(
        strict=True,
        ge=1,
        le=9007199254740991,
        description="Last-read revision; stale values are refused",
    ),
]
PublicConnectorCommand = Literal["create", "rename", "credentials", "revoke"]
_PUBLIC_CONNECTOR_COMMAND_KEYS = {
    "create": {
        "expected_project_revision",
        "kind",
        "display_name",
        "description",
        "endpoint",
        "bearer_token",
    },
    "rename": {
        "expected_project_revision",
        "expected_grant_revision",
        "display_name",
        "description",
    },
    "credentials": {
        "expected_project_revision",
        "expected_grant_revision",
        "endpoint",
        "bearer_token",
    },
    "revoke": {"expected_project_revision", "expected_grant_revision"},
}
_PUBLIC_CONNECTOR_STRING_CAPS = {
    "kind": 64,
    "display_name": 512,
    "description": 2048,
    "endpoint": 8192,
    "bearer_token": 8192,
}


class PublicConnectorItemResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", from_attributes=True)
    connector_id: str
    kind: Literal["http_mcp_static_bearer"]
    display_name: str
    description: str
    state: Literal["active", "revoked"]
    grant_revision: PublicConnectorRevision
    created_at: int
    updated_at: int


class PublicConnectorCollectionResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", from_attributes=True)
    project_id: str
    public_connectors_revision: PublicConnectorRevision
    items: list[PublicConnectorItemResponse]


class CreatePublicConnectorCommand(BaseModel):
    """Documentation only. Runtime commands are read without input-echo validation."""

    model_config = ConfigDict(extra="forbid")
    expected_project_revision: PublicConnectorRevision
    kind: Literal["http_mcp_static_bearer"]
    display_name: str = Field(
        max_length=64, description="Safe display name; never credential material"
    )
    description: str = Field(max_length=256, description="Safe display description")
    endpoint: str = Field(
        max_length=2048,
        description="Stored HTTPS endpoint; write-only, no provider request",
        json_schema_extra={"writeOnly": True},
    )
    bearer_token: str = Field(
        max_length=4096,
        description="Static bearer credential; write-only",
        json_schema_extra={"writeOnly": True},
    )


class RenamePublicConnectorCommand(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_project_revision: PublicConnectorRevision
    expected_grant_revision: PublicConnectorRevision
    display_name: str = Field(max_length=64, description="Safe display name")
    description: str = Field(max_length=256, description="Safe display description")


class ReplacePublicConnectorCredentialsCommand(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_project_revision: PublicConnectorRevision
    expected_grant_revision: PublicConnectorRevision
    endpoint: str = Field(
        max_length=2048,
        description="Stored HTTPS endpoint; write-only",
        json_schema_extra={"writeOnly": True},
    )
    bearer_token: str = Field(
        max_length=4096,
        description="Static bearer credential; write-only",
        json_schema_extra={"writeOnly": True},
    )


class RevokePublicConnectorCommand(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_project_revision: PublicConnectorRevision
    expected_grant_revision: PublicConnectorRevision


def _public_connector_request_schema(model: type[BaseModel]) -> dict[str, Any]:
    return {
        "requestBody": {
            "required": True,
            "content": {"application/json": {"schema": model.model_json_schema()}},
        }
    }


def _public_connector_payload_error(
    reason: str = "invalid_payload", status_code: int = 422
) -> OctopError:
    return OctopError.localized(
        ErrorCode.PROJECT_PUBLIC_CONNECTOR_INVALID, status=status_code, details={"reason": reason}
    )


async def _read_public_connector_command(
    request: Request, command: PublicConnectorCommand
) -> dict[str, Any]:
    """Bounded flat JSON reader; failures never expose caller values or key names."""
    content_type = request.headers.get("content-type", "").split(";")
    if content_type[0].strip().lower() != "application/json":
        raise _public_connector_payload_error("unsupported_content_type", 415)
    for parameter in content_type[1:]:
        key, separator, value = parameter.strip().partition("=")
        if key.lower() == "charset" and (
            not separator or value.strip().strip('"').lower() != "utf-8"
        ):
            raise _public_connector_payload_error("unsupported_content_type", 415)
    data = bytearray()
    oversized = False
    try:
        async for chunk in request.stream():
            if len(data) + len(chunk) > 65536:
                oversized = True
                break
            data.extend(chunk)
    except Exception:
        raise _public_connector_payload_error("request_read_failed", 400) from None
    if oversized:
        raise _public_connector_payload_error("body_too_large", 413)

    def unique(pairs: list[tuple[str, object]]) -> dict[str, object]:
        result: dict[str, object] = {}
        for key, value in pairs:
            if key in result:
                raise ValueError
            result[key] = value
        return result

    def reject_constant(_value: str) -> None:
        raise ValueError

    try:
        payload = json.loads(
            data.decode("utf-8"), object_pairs_hook=unique, parse_constant=reject_constant
        )
        if type(payload) is not dict or set(payload) != _PUBLIC_CONNECTOR_COMMAND_KEYS[command]:
            raise ValueError
        for key, value in payload.items():
            if key in ("expected_project_revision", "expected_grant_revision"):
                if type(value) is not int or not 1 <= value <= 9007199254740991:
                    raise ValueError
            elif (
                type(value) is not str
                or len(value.encode("utf-8")) > _PUBLIC_CONNECTOR_STRING_CAPS[key]
            ):
                raise ValueError
        if command == "create" and payload["kind"] != "http_mcp_static_bearer":
            raise ValueError
    except (ValueError, UnicodeError, RecursionError):
        raise _public_connector_payload_error() from None
    return payload


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


class SetExpertsBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_revision: int = Field(
        ge=0,
        description="Revision the client last read; a mismatch is refused with 409",
    )
    agent_ids: list[str] = Field(
        description=(
            f"Ordered shared single-expert Agent ids (at most {MAX_PROJECT_EXPERTS}, "
            "no duplicates); an empty list keeps the default member choice flow"
        )
    )


def _project_service(server: OctopServer) -> ProjectService:
    if server.services is None:
        raise OctopError(ErrorCode.INTERNAL_ERROR, "server services unavailable")
    return ProjectService(
        server.services,
        public_connector_service=ProjectPublicConnectorService(
            server.services.project_public_connector_repo
        ),
    )


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
        "archived": view.archived,
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


def _experts_payload(view: ProjectExpertsView) -> dict[str, Any]:
    """Ordered masked list: unavailable experts carry null name/description."""
    return {
        "revision": view.revision,
        "items": [
            {
                "agent_id": item.agent_id,
                "name": item.name,
                "description": item.description,
                "status": item.status,
            }
            for item in view.items
        ],
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


@router.get(
    "/task-capabilities",
    summary="Hint: project task modes this host can offer",
)
async def task_capabilities(
    server: OctopServer = Depends(get_server),
    user: User = Depends(current_user),
) -> dict[str, Any]:
    """Non-authoritative UI hint for ``mode='files'`` availability.

    Declared before ``/{project_id}`` so the literal path never loses to the
    dynamic route. ``available=false`` reasons: ``disabled`` (030A gate still
    closed) or ``storage_unavailable`` (managed root not writable). Creation
    re-checks the gate, capability, quota, and ACL server-side regardless of
    this response.
    """
    if server.services is None:
        raise OctopError(ErrorCode.INTERNAL_ERROR, "server services unavailable")
    return {"files": file_tasks.files_task_capability(server.services.paths)}


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


@router.get(
    "/{project_id}/public-connectors",
    response_model=PublicConnectorCollectionResponse,
    summary="Read safe project public connector settings",
    description="Project members can read metadata only. Credentials and endpoints are never returned; this does not enable runtime tools.",
)
async def list_public_connectors(
    project_id: str,
    server: OctopServer = Depends(get_server),
    user: User = Depends(current_user),
) -> PublicConnectorCollection:
    return await asyncio.get_running_loop().run_in_executor(
        None,
        partial(_project_service(server).list_public_connectors, project_id, user.id),
    )


@router.post(
    "/{project_id}/public-connectors",
    status_code=status.HTTP_201_CREATED,
    response_model=PublicConnectorCollectionResponse,
    summary="Store a project public connector configuration",
    description="Project owner/admin only. Stores encrypted static bearer settings without contacting MCP or enabling runtime tools. Current project revision is required.",
    openapi_extra=_public_connector_request_schema(CreatePublicConnectorCommand),
)
async def create_public_connector(
    project_id: str,
    request: Request,
    server: OctopServer = Depends(get_server),
    user: User = Depends(current_user),
) -> PublicConnectorCollection:
    command = await _read_public_connector_command(request, "create")
    return await asyncio.get_running_loop().run_in_executor(
        None,
        partial(
            _project_service(server).create_public_connector,
            project_id,
            user.id,
            expected_project_revision=command["expected_project_revision"],
            display_name=command["display_name"],
            description=command["description"],
            credential={"endpoint": command["endpoint"], "bearer_token": command["bearer_token"]},
        ),
    )


@router.patch(
    "/{project_id}/public-connectors/{connector_id}",
    response_model=PublicConnectorCollectionResponse,
    summary="Rename safe project public connector metadata",
    description="Project owner/admin only; current project and grant revisions are required. Credentials and grant revision remain unchanged.",
    openapi_extra=_public_connector_request_schema(RenamePublicConnectorCommand),
)
async def rename_public_connector(
    project_id: str,
    connector_id: str,
    request: Request,
    server: OctopServer = Depends(get_server),
    user: User = Depends(current_user),
) -> PublicConnectorCollection:
    command = await _read_public_connector_command(request, "rename")
    return await asyncio.get_running_loop().run_in_executor(
        None,
        partial(
            _project_service(server).rename_public_connector,
            project_id,
            user.id,
            connector_id,
            **command,
        ),
    )


@router.put(
    "/{project_id}/public-connectors/{connector_id}/credentials",
    response_model=PublicConnectorCollectionResponse,
    summary="Replace write-only project public connector credentials",
    description="Project owner/admin only. Current project and grant revisions are required; credentials are never read back or sent to MCP.",
    openapi_extra=_public_connector_request_schema(ReplacePublicConnectorCredentialsCommand),
)
async def replace_public_connector_credentials(
    project_id: str,
    connector_id: str,
    request: Request,
    server: OctopServer = Depends(get_server),
    user: User = Depends(current_user),
) -> PublicConnectorCollection:
    command = await _read_public_connector_command(request, "credentials")
    return await asyncio.get_running_loop().run_in_executor(
        None,
        partial(
            _project_service(server).replace_public_connector_credentials,
            project_id,
            user.id,
            connector_id,
            expected_project_revision=command["expected_project_revision"],
            expected_grant_revision=command["expected_grant_revision"],
            credential={"endpoint": command["endpoint"], "bearer_token": command["bearer_token"]},
        ),
    )


@router.post(
    "/{project_id}/public-connectors/{connector_id}/revoke",
    response_model=PublicConnectorCollectionResponse,
    summary="Revoke and erase project public connector credentials",
    description="Project owner/admin only. Requires current project and grant revisions. Revocation is terminal and is allowed on archived projects.",
    openapi_extra=_public_connector_request_schema(RevokePublicConnectorCommand),
)
async def revoke_public_connector(
    project_id: str,
    connector_id: str,
    request: Request,
    server: OctopServer = Depends(get_server),
    user: User = Depends(current_user),
) -> PublicConnectorCollection:
    command = await _read_public_connector_command(request, "revoke")
    return await asyncio.get_running_loop().run_in_executor(
        None,
        partial(
            _project_service(server).revoke_public_connector,
            project_id,
            user.id,
            connector_id,
            **command,
        ),
    )


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


@router.get(
    "/{project_id}/experts",
    summary="List the project's ordered experts (members only)",
)
async def list_project_experts(
    project_id: str,
    server: OctopServer = Depends(get_server),
    user: User = Depends(current_user),
) -> dict[str, Any]:
    """Members read the live ordered list with the admin-PUT revision. Rows for
    experts that are currently disabled, unshared, or no longer single experts
    return ``name=null, description=null, status='unavailable'``; hard-deleted
    agents cascade out of the list. Non-members and unknown projects share one
    404, and private Agent profiles never appear in the response."""
    view = _project_service(server).list_experts(project_id, user_id=user.id)
    return _experts_payload(view)


@router.put(
    "/{project_id}/experts",
    summary="Replace the project's ordered experts (owner/admin)",
)
async def set_project_experts(
    project_id: str,
    body: SetExpertsBody,
    server: OctopServer = Depends(get_server),
    user: User = Depends(current_user),
) -> dict[str, Any]:
    """Owner/admin replace the ordered list of globally shared, enabled single
    experts. The list only gates which experts members may pick for NEW private
    tasks — it grants no Agent, OAuth, file, or history access. A stale
    ``expected_revision`` answers 409 ``PROJECT_EXPERTS_CHANGED`` with no
    writes; an identical ordered list keeps the revision; a real change bumps
    it once. A disabled/team/private/unknown Agent is refused uniformly with
    422 ``PROJECT_EXPERT_INVALID``."""
    view = _project_service(server).set_experts(
        project_id,
        actor_user_id=user.id,
        expected_revision=body.expected_revision,
        agent_ids=body.agent_ids,
    )
    return _experts_payload(view)


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
