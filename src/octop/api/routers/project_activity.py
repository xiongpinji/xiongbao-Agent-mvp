"""HTTP API for 熊宝-Agent project activity + messages (PS-03A).

Thin transport only: whitelist/scope/paging policy lives in
:class:`octop.infra.projects.activity.ProjectActivityService`, race-safe SQL
in ``ProjectActivityRepo``. The operator is always ``current_user.id``; the
publish route accepts no actor field of any kind (extra body fields are
rejected outright), and neither project-admin nor instance-admin identity
bypasses membership — outsiders and unknown projects share one uniform 404.
Response items carry exactly the contract's fixed fields; ``payload_json``,
instructions, invite/join-request material, and private task data never
appear.
"""

from __future__ import annotations

from typing import Literal, cast

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, ConfigDict, field_validator

from octop.api.deps import current_user, get_server
from octop.infra.errors import ErrorCode, OctopError
from octop.infra.projects.activity import (
    MAX_ACTIVITY_LIMIT,
    ActivityItemView,
    ProjectActivityService,
    validate_message_body,
)
from octop.infra.projects.service import DEFAULT_PAGE_LIMIT
from octop.infra.server import OctopServer
from octop.infra.users.identity import User

router = APIRouter(prefix="/projects/{project_id}")


def _activity_service(server: OctopServer) -> ProjectActivityService:
    if server.services is None:
        raise OctopError(ErrorCode.INTERNAL_ERROR, "server services unavailable")
    return ProjectActivityService(server.services)


class ActivityItemResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    event_id: int
    event_type: str
    actor_user_id: int | None
    actor_name: str | None
    object_kind: Literal["project", "member", "todo", "message"]
    object_id: str | None
    message_body: str | None
    created_at: int


class ActivityPageResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: list[ActivityItemResponse]
    next_cursor: str | None


def _item_payload(view: ActivityItemView) -> ActivityItemResponse:
    """Exactly the contract's fixed item fields — nothing else is serializable
    here, so timeline responses cannot grow payload or credential leaks."""
    return ActivityItemResponse(
        event_id=view.event_id,
        event_type=view.event_type,
        actor_user_id=view.actor_user_id,
        actor_name=view.actor_name,
        object_kind=cast(Literal["project", "member", "todo", "message"], view.object_kind),
        object_id=view.object_id,
        message_body=view.message_body,
        created_at=view.created_at,
    )


class PostMessageBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    body: str

    @field_validator("body")
    @classmethod
    def _validate_body(cls, v: str) -> str:
        # Trims and enforces the 1–4000 character bound; ValueError → 422.
        return validate_message_body(v)


@router.get(
    "/activity",
    response_model=ActivityPageResponse,
    summary="Read the project activity timeline (members only)",
)
async def list_activity(
    project_id: str,
    server: OctopServer = Depends(get_server),
    user: User = Depends(current_user),
    scope: Literal["members", "related"] = Query(
        "members", description="members = full whitelist; related = involving me"
    ),
    limit: int = Query(DEFAULT_PAGE_LIMIT, ge=1, le=MAX_ACTIVITY_LIMIT),
    cursor: str | None = Query(None, description="Opaque seek cursor from a previous next_cursor"),
) -> ActivityPageResponse:
    """Whitelisted events only, filtered and paginated server-side. Malformed
    cursors answer 422 ``PROJECT_ACTIVITY_CURSOR_INVALID``; the cursor is not
    an auth credential — membership is re-checked on every page."""
    page = _activity_service(server).list_activity(
        project_id, user_id=user.id, scope=scope, limit=limit, cursor=cursor
    )
    return ActivityPageResponse(
        items=[_item_payload(view) for view in page.items],
        next_cursor=page.next_cursor,
    )


@router.post(
    "/messages",
    status_code=201,
    response_model=ActivityItemResponse,
    summary="Publish a plain-text project message",
)
async def post_message(
    project_id: str,
    body: PostMessageBody,
    server: OctopServer = Depends(get_server),
    user: User = Depends(current_user),
) -> ActivityItemResponse:
    """Members publish a trimmed plain-text message (1–4000 characters) as
    themselves; the message row and its timeline event are written atomically,
    so a failed write leaves nothing behind."""
    view = _activity_service(server).post_message(project_id, user_id=user.id, body=body.body)
    return _item_payload(view)
