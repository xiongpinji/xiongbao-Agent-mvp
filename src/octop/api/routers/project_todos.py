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

from fastapi import APIRouter, Depends, Query, Request, Response
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator
from starlette.concurrency import run_in_threadpool

from octop.api.common.todo_comment_upload import read_bounded_json, read_comment_multipart
from octop.api.deps import current_user, get_server
from octop.infra.db.repos._base import UNSET
from octop.infra.errors import ErrorCode, OctopError
from octop.infra.projects.service import DEFAULT_PAGE_LIMIT, MAX_PAGE_LIMIT
from octop.infra.projects.todo_comments import (
    MAX_COMMENT_LIMIT,
    CommentImageUpload,
    CommentView,
    ProjectTodoCommentService,
    validate_client_request_id,
    validate_comment_body,
)
from octop.infra.projects.todos import (
    BULK_MAX_ITEMS,
    DISPLAY_REVISION_MAX,
    ProjectTodoService,
    TodoView,
    validate_todo_description,
    validate_todo_title,
)
from octop.infra.server import OctopServer
from octop.infra.users.identity import User

router = APIRouter(prefix="/projects/{project_id}/todos")

TodoStatus = Literal["todo", "in_progress", "done"]
TodoDescriptionFormat = Literal["plain", "markdown"]
PositiveInt = Annotated[int, Field(strict=True, ge=1)]
PlanDate = Annotated[str, Field(strict=True)]
CatalogId = Annotated[str, Field(strict=True, min_length=1, max_length=64)]


def _todo_service(server: OctopServer) -> ProjectTodoService:
    if server.services is None:
        raise OctopError(ErrorCode.INTERNAL_ERROR, "server services unavailable")
    return ProjectTodoService(server.services)


def _comment_service(server: OctopServer) -> ProjectTodoCommentService:
    if server.services is None:
        raise OctopError(ErrorCode.INTERNAL_ERROR, "server services unavailable")
    return ProjectTodoCommentService(server.services)


def _todo_payload(view: TodoView) -> dict[str, Any]:
    return {
        "todo_id": view.todo_id,
        "project_id": view.project_id,
        "title": view.title,
        "description": view.description,
        "description_format": view.description_format,
        "status": view.status,
        "creator_user_id": view.creator_user_id,
        "assignee_user_id": view.assignee_user_id,
        "version": view.version,
        "display_revision": view.display_revision,
        "created_at": view.created_at,
        "updated_at": view.updated_at,
        "start_date": view.start_date,
        "due_date": view.due_date,
        "priority_id": view.priority_id,
        "tag_ids": view.tag_ids,
        "catalog_revision": view.catalog_revision,
    }


class CreateTodoBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str
    description: str = ""
    description_format: TodoDescriptionFormat = "plain"
    assignee_user_id: PositiveInt | None = None
    status: TodoStatus = "todo"
    start_date: PlanDate | None = None
    due_date: PlanDate | None = None
    priority_id: CatalogId | None = None
    tag_ids: list[CatalogId] = Field(default_factory=list)
    expected_catalog_revision: PositiveInt | None = None

    @field_validator("title")
    @classmethod
    def _validate_title(cls, v: str) -> str:
        return validate_todo_title(v)

    @field_validator("description")
    @classmethod
    def _validate_description(cls, v: str) -> str:
        return validate_todo_description(v)

    @model_validator(mode="after")
    def _catalog_pair(self) -> CreateTodoBody:
        if (
            "expected_catalog_revision" in self.model_fields_set
            and self.expected_catalog_revision is None
        ):
            raise ValueError("expected_catalog_revision cannot be null")
        if (
            self.priority_id is not None or self.tag_ids
        ) and self.expected_catalog_revision is None:
            raise ValueError("priority or tags require expected_catalog_revision")
        if len(set(self.tag_ids)) > 20:
            raise ValueError("a todo may reference at most 20 different tags")
        return self


class UpdateTodoBody(BaseModel):
    """Partial patch. ``assignee_user_id: null`` explicitly clears the assignee
    (owner/admin only); omitted fields are left untouched. Explicit nulls for
    title/description/status are treated as omitted — they cannot be cleared."""

    model_config = ConfigDict(extra="forbid")

    expected_version: PositiveInt
    title: str | None = None
    description: str | None = None
    description_format: TodoDescriptionFormat | None = None
    status: TodoStatus | None = None
    assignee_user_id: PositiveInt | None = None
    start_date: PlanDate | None = None
    due_date: PlanDate | None = None
    priority_id: CatalogId | None = None
    tag_ids: list[CatalogId] | None = None
    expected_catalog_revision: PositiveInt | None = None

    @field_validator("title")
    @classmethod
    def _validate_title(cls, v: str | None) -> str | None:
        return None if v is None else validate_todo_title(v)

    @field_validator("description")
    @classmethod
    def _validate_description(cls, v: str | None) -> str | None:
        return None if v is None else validate_todo_description(v)

    @model_validator(mode="after")
    def _check_description_format_pair(self) -> UpdateTodoBody:
        if "description_format" in self.model_fields_set and (
            self.description_format is None or self.description is None
        ):
            raise ValueError("description_format requires a description and plain or markdown")
        has_refs = bool({"priority_id", "tag_ids"} & self.model_fields_set)
        has_revision = "expected_catalog_revision" in self.model_fields_set
        if has_refs != has_revision or (has_revision and self.expected_catalog_revision is None):
            raise ValueError("priority or tag fields must be paired with expected_catalog_revision")
        if "tag_ids" in self.model_fields_set and self.tag_ids is None:
            raise ValueError("tag_ids cannot be null")
        if self.tag_ids is not None and len(set(self.tag_ids)) > 20:
            raise ValueError("a todo may reference at most 20 different tags")
        return self


class BulkTodoItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    todo_id: str = Field(min_length=1, max_length=64)
    expected_version: PositiveInt


class BulkUpdateBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: list[BulkTodoItem] = Field(min_length=1, max_length=BULK_MAX_ITEMS)
    status: TodoStatus | None = None
    assignee_user_id: PositiveInt | None = None

    @model_validator(mode="after")
    def _check_batch(self) -> BulkUpdateBody:
        ids = [item.todo_id for item in self.items]
        if len(set(ids)) != len(ids):
            raise ValueError("bulk items must have unique todo ids")
        if self.status is None and "assignee_user_id" not in self.model_fields_set:
            raise ValueError("bulk update requires status or assignee_user_id")
        return self


class TodoResponse(BaseModel):
    todo_id: str
    project_id: str
    title: str
    description: str
    description_format: TodoDescriptionFormat
    status: TodoStatus
    creator_user_id: int
    assignee_user_id: int | None
    version: int
    display_revision: Annotated[int, Field(strict=True, ge=1, le=DISPLAY_REVISION_MAX)]
    created_at: int
    updated_at: int
    start_date: str | None
    due_date: str | None
    priority_id: str | None
    tag_ids: list[str]
    catalog_revision: int


class TodoListResponse(BaseModel):
    items: list[TodoResponse]
    limit: int
    offset: int
    has_more: bool


class BulkTodoResponse(BaseModel):
    items: list[TodoResponse]


class CommentImageResponse(BaseModel):
    image_id: str
    media_type: str
    size_bytes: int
    position: int


class TodoCommentResponse(BaseModel):
    comment_id: str
    todo_id: str
    author_user_id: int | None
    author_name: str
    body: str
    images: list[CommentImageResponse]
    created_at: int


class TodoCommentPageResponse(BaseModel):
    items: list[TodoCommentResponse]
    next_cursor: str | None


class CreateTodoCommentBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    body: str = Field(description="Plain text, trimmed to 1–4000 characters")
    client_request_id: str = Field(description="A fresh lowercase canonical UUID v4 per draft")

    @field_validator("body")
    @classmethod
    def _validate_body(cls, value: str) -> str:
        return validate_comment_body(value)

    @field_validator("client_request_id")
    @classmethod
    def _validate_request_id(cls, value: str) -> str:
        return validate_client_request_id(value)


def _comment_payload(view: CommentView) -> TodoCommentResponse:
    return TodoCommentResponse(
        comment_id=view.comment_id,
        todo_id=view.todo_id,
        author_user_id=view.author_user_id,
        author_name=view.author_name,
        body=view.body,
        images=[CommentImageResponse(**item) for item in view.images],
        created_at=view.created_at,
    )


@router.post("", status_code=201, response_model=TodoResponse, summary="Create a project todo")
async def create_todo(
    project_id: str,
    body: CreateTodoBody,
    server: OctopServer = Depends(get_server),
    user: User = Depends(current_user),
) -> dict[str, Any]:
    """Any member may create; ordinary members may only assign themselves."""
    view = await run_in_threadpool(
        _todo_service(server).create_todo,
        project_id,
        actor_user_id=user.id,
        title=body.title,
        description=body.description,
        description_format=body.description_format,
        assignee_user_id=body.assignee_user_id,
        status=body.status,
        start_date=body.start_date,
        due_date=body.due_date,
        priority_id=body.priority_id,
        tag_ids=body.tag_ids,
        expected_catalog_revision=body.expected_catalog_revision
        if "expected_catalog_revision" in body.model_fields_set
        else UNSET,
    )
    return _todo_payload(view)


@router.post(
    "/bulk", response_model=BulkTodoResponse, summary="Bulk-update todos atomically (owner/admin)"
)
async def bulk_update_todos(
    project_id: str,
    body: BulkUpdateBody,
    server: OctopServer = Depends(get_server),
    user: User = Depends(current_user),
) -> dict[str, Any]:
    """All-or-nothing: any stale version, foreign, or deleted id rejects the
    whole batch with nothing written. Returns every updated row."""
    views = await run_in_threadpool(
        _todo_service(server).bulk_update_todos,
        project_id,
        actor_user_id=user.id,
        items=[(item.todo_id, item.expected_version) for item in body.items],
        status=body.status if body.status is not None else UNSET,
        assignee_user_id=(
            body.assignee_user_id if "assignee_user_id" in body.model_fields_set else UNSET
        ),
    )
    return {"items": [_todo_payload(view) for view in views]}


@router.get("", response_model=TodoListResponse, summary="List project todos (members only)")
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
    page = await run_in_threadpool(
        _todo_service(server).list_todos,
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


@router.get("/{todo_id}", response_model=TodoResponse, summary="Get one project todo")
async def get_todo(
    project_id: str,
    todo_id: str,
    server: OctopServer = Depends(get_server),
    user: User = Depends(current_user),
) -> dict[str, Any]:
    view = await run_in_threadpool(
        _todo_service(server).get_todo, project_id, todo_id, user_id=user.id
    )
    return _todo_payload(view)


@router.patch(
    "/{todo_id}", response_model=TodoResponse, summary="Update a project todo (optimistic version)"
)
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
    view = await run_in_threadpool(
        _todo_service(server).update_todo,
        project_id,
        todo_id,
        actor_user_id=user.id,
        expected_version=body.expected_version,
        title=body.title if body.title is not None else UNSET,
        description=body.description if body.description is not None else UNSET,
        description_format=(
            body.description_format or "plain" if body.description is not None else UNSET
        ),
        status=body.status if body.status is not None else UNSET,
        assignee_user_id=(
            body.assignee_user_id if "assignee_user_id" in body.model_fields_set else UNSET
        ),
        start_date=body.start_date if "start_date" in body.model_fields_set else UNSET,
        due_date=body.due_date if "due_date" in body.model_fields_set else UNSET,
        priority_id=body.priority_id if "priority_id" in body.model_fields_set else UNSET,
        tag_ids=body.tag_ids if "tag_ids" in body.model_fields_set else UNSET,
        expected_catalog_revision=body.expected_catalog_revision
        if "expected_catalog_revision" in body.model_fields_set
        else UNSET,
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
    await run_in_threadpool(
        _todo_service(server).delete_todo,
        project_id,
        todo_id,
        actor_user_id=user.id,
        expected_version=expected_version,
    )
    return None


@router.get(
    "/{todo_id}/comments",
    response_model=TodoCommentPageResponse,
    summary="List a project todo's comments (members only)",
)
async def list_todo_comments(
    project_id: str,
    todo_id: str,
    server: OctopServer = Depends(get_server),
    user: User = Depends(current_user),
    limit: int = Query(DEFAULT_PAGE_LIMIT, ge=1, le=MAX_COMMENT_LIMIT),
    cursor: str | None = Query(None, description="Opaque seek cursor from next_cursor"),
) -> TodoCommentPageResponse:
    """Each page rechecks current membership and that the todo is still live."""
    page = _comment_service(server).list_comments(
        project_id, todo_id, user_id=user.id, limit=limit, cursor=cursor
    )
    return TodoCommentPageResponse(
        items=[_comment_payload(view) for view in page.items], next_cursor=page.next_cursor
    )


@router.post(
    "/{todo_id}/comments",
    status_code=201,
    response_model=TodoCommentResponse,
    summary="Publish a text or private-image comment on a project todo",
    description=(
        "Current project members may submit plain text as JSON or text with up to five "
        "PNG/JPEG/WebP images as multipart. The server checks membership again before "
        "committing. Repeating the same request ID and content returns 200."
    ),
    responses={200: {"model": TodoCommentResponse, "description": "Idempotent replay"}},
    openapi_extra={
        "requestBody": {
            "required": True,
            "content": {
                "application/json": {
                    "schema": {
                        "type": "object",
                        "required": ["body", "client_request_id"],
                        "additionalProperties": False,
                        "properties": {
                            "body": {"type": "string", "minLength": 1, "maxLength": 4000},
                            "client_request_id": {"type": "string", "format": "uuid"},
                        },
                    }
                },
                "multipart/form-data": {
                    "schema": {
                        "type": "object",
                        "required": ["client_request_id"],
                        "description": "Provide non-blank body text or at least one image.",
                        "additionalProperties": False,
                        "properties": {
                            "client_request_id": {"type": "string", "format": "uuid"},
                            "body": {"type": "string", "maxLength": 4000},
                            "images": {
                                "type": "array",
                                "minItems": 1,
                                "maxItems": 5,
                                "items": {"type": "string", "format": "binary"},
                                "description": "Each image is at most 8 MiB; one request is at most 20 MiB.",
                            },
                        },
                    }
                },
            },
        }
    },
)
async def post_todo_comment(
    project_id: str,
    todo_id: str,
    request: Request,
    response: Response,
    server: OctopServer = Depends(get_server),
    user: User = Depends(current_user),
) -> TodoCommentResponse:
    """Bound raw bytes before parsing/spooling; identical retries return 200."""
    service = _comment_service(server)
    service.assert_access(project_id, todo_id, user_id=user.id)
    media_type = request.headers.get("content-type", "").split(";", 1)[0].strip().lower()
    images: list[CommentImageUpload]
    if media_type == "application/json":
        raw = await read_bounded_json(request)
        try:
            parsed = CreateTodoCommentBody.model_validate_json(raw)
        except ValidationError:
            raise OctopError(
                ErrorCode.PROJECT_TODO_COMMENT_INVALID, "invalid comment JSON"
            ) from None
        text_body, request_id, images = parsed.body, parsed.client_request_id, []
    elif media_type == "multipart/form-data":
        text_body, request_id, images = await read_comment_multipart(request)
    else:
        raise OctopError(ErrorCode.PROJECT_TODO_COMMENT_INVALID, "unsupported comment content type")
    try:
        view, created = await run_in_threadpool(
            service.post_comment,
            project_id,
            todo_id,
            user_id=user.id,
            body=text_body,
            client_request_id=request_id,
            images=images,
        )
    except ValueError:
        raise OctopError(
            ErrorCode.PROJECT_TODO_COMMENT_INVALID, "invalid comment body or request id"
        ) from None
    if not created:
        response.status_code = 200
    return _comment_payload(view)


@router.get(
    "/{todo_id}/comments/{comment_id}/images/{image_id}",
    summary="Fetch a private image attached to a project todo comment",
)
async def get_todo_comment_image(
    project_id: str,
    todo_id: str,
    comment_id: str,
    image_id: str,
    server: OctopServer = Depends(get_server),
    user: User = Depends(current_user),
) -> Response:
    image = await run_in_threadpool(
        _comment_service(server).get_image,
        project_id,
        todo_id,
        comment_id,
        image_id,
        user_id=user.id,
    )
    return Response(
        content=image.data,
        media_type=image.media_type,
        headers={"Cache-Control": "private, no-store", "X-Content-Type-Options": "nosniff"},
    )
