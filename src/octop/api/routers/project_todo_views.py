"""Thin authenticated transport for project-owned shared plan views."""

from __future__ import annotations

from collections.abc import Callable, Coroutine
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Request
from fastapi.exceptions import RequestValidationError
from fastapi.routing import APIRoute
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from starlette.concurrency import run_in_threadpool
from starlette.responses import Response

from octop.api.deps import current_user, get_server
from octop.infra.db.repos._base import UNSET
from octop.infra.errors import ErrorCode, OctopError
from octop.infra.projects.plan_definition import (
    BoardDefinition,
    CalendarDefinition,
    GanttDefinition,
    PlanDefinition,
    ViewType,
)
from octop.infra.projects.todo_views import ProjectTodoViewService, validate_view_name, view_error
from octop.infra.server import OctopServer
from octop.infra.users.identity import User


class SafePlanViewRoute(APIRoute):
    def get_route_handler(self) -> Callable[[Request], Coroutine[Any, Any, Response]]:
        handler = super().get_route_handler()

        async def safe_handler(request: Request) -> Response:
            try:
                return await handler(request)
            except RequestValidationError:
                # The default errors include untrusted nested loc and input.
                raise view_error("invalid_view_request", status=422) from None

        return safe_handler


router = APIRouter(prefix="/projects/{project_id}/plan", route_class=SafePlanViewRoute)
Revision = Annotated[int, Field(strict=True, ge=1)]
Version = Annotated[int, Field(strict=True, ge=1)]
ViewId = Annotated[str, Field(strict=True, min_length=1, max_length=64)]
DefinitionPayload = PlanDefinition | BoardDefinition | GanttDefinition | CalendarDefinition


class _StrictBody(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, hide_input_in_errors=True)


class _CreateViewBody(_StrictBody):
    expected_revision: Revision
    expected_catalog_revision: Revision
    name: str

    @field_validator("name", mode="before")
    @classmethod
    def _name(cls, value: object) -> str:
        return validate_view_name(value)


class CreateListViewBody(_CreateViewBody):
    type: Literal["list"]
    definition: PlanDefinition


class CreateTableViewBody(_CreateViewBody):
    type: Literal["table"]
    definition: PlanDefinition


class CreateBoardViewBody(_CreateViewBody):
    type: Literal["board"]
    definition: BoardDefinition


class CreateGanttViewBody(_CreateViewBody):
    type: Literal["gantt"]
    definition: GanttDefinition


class CreateCalendarViewBody(_CreateViewBody):
    type: Literal["calendar"]
    definition: CalendarDefinition


CreateViewBody = Annotated[
    CreateListViewBody
    | CreateTableViewBody
    | CreateBoardViewBody
    | CreateGanttViewBody
    | CreateCalendarViewBody,
    Field(discriminator="type"),
]


class UpdateViewBody(_StrictBody):
    expected_version: Version
    name: str | None = None
    type: ViewType | None = None
    definition: DefinitionPayload | None = None
    expected_catalog_revision: Revision | None = None

    @field_validator("name", mode="before")
    @classmethod
    def _name(cls, value: object) -> str:
        return validate_view_name(value)

    @model_validator(mode="after")
    def _present_values(self) -> UpdateViewBody:
        if any(getattr(self, name) is None for name in self.model_fields_set):
            raise ValueError("view patch fields cannot be null")
        return self


class OrderViewsBody(_StrictBody):
    expected_revision: Revision
    view_ids: list[ViewId] = Field(min_length=1, max_length=30)


class DefaultViewBody(_StrictBody):
    expected_revision: Revision
    view_id: ViewId


class ViewVersionBody(_StrictBody):
    expected_version: Version


def _service(server: OctopServer) -> ProjectTodoViewService:
    if server.services is None:
        raise OctopError(ErrorCode.INTERNAL_ERROR, "server services unavailable")
    return ProjectTodoViewService(server.services)


class _ViewResponse(BaseModel):
    view_id: str
    project_id: str
    name: str
    version: int
    position: int
    archived_at: int | None
    created_at: int
    updated_at: int


class ListViewResponse(_ViewResponse):
    type: Literal["list"]
    definition: PlanDefinition


class TableViewResponse(_ViewResponse):
    type: Literal["table"]
    definition: PlanDefinition


class BoardViewResponse(_ViewResponse):
    type: Literal["board"]
    definition: BoardDefinition


class GanttViewResponse(_ViewResponse):
    type: Literal["gantt"]
    definition: GanttDefinition


class CalendarViewResponse(_ViewResponse):
    type: Literal["calendar"]
    definition: CalendarDefinition


ViewResponse = Annotated[
    ListViewResponse
    | TableViewResponse
    | BoardViewResponse
    | GanttViewResponse
    | CalendarViewResponse,
    Field(discriminator="type"),
]


class ViewListResponse(BaseModel):
    project_id: str
    revision: int
    default_view_id: str
    items: list[ViewResponse]


class ViewMutationResponse(BaseModel):
    revision: int
    default_view_id: str
    item: ViewResponse


class ViewOrderResponse(BaseModel):
    revision: int
    default_view_id: str
    items: list[ViewResponse]


class ViewDefaultResponse(BaseModel):
    revision: int
    default_view_id: str


@router.get(
    "/views", response_model=ViewListResponse, summary="Read the project's shared plan views"
)
async def list_views(
    project_id: str, server: OctopServer = Depends(get_server), user: User = Depends(current_user)
) -> dict[str, Any]:
    return await run_in_threadpool(_service(server).list_views, project_id, user_id=user.id)


@router.post(
    "/views",
    response_model=ViewMutationResponse,
    status_code=201,
    summary="Create a shared plan view (owner/admin)",
)
async def create_view(
    project_id: str,
    body: CreateViewBody,
    server: OctopServer = Depends(get_server),
    user: User = Depends(current_user),
) -> dict[str, Any]:
    return await run_in_threadpool(
        _service(server).create_view,
        project_id,
        actor_user_id=user.id,
        expected_revision=body.expected_revision,
        expected_catalog_revision=body.expected_catalog_revision,
        name=body.name,
        view_type=body.type,
        definition=body.definition,
    )


@router.put(
    "/views/order",
    response_model=ViewOrderResponse,
    summary="Replace the complete active view order (owner/admin)",
)
async def order_views(
    project_id: str,
    body: OrderViewsBody,
    server: OctopServer = Depends(get_server),
    user: User = Depends(current_user),
) -> dict[str, Any]:
    return await run_in_threadpool(
        _service(server).order_views,
        project_id,
        actor_user_id=user.id,
        expected_revision=body.expected_revision,
        view_ids=body.view_ids,
    )


@router.put(
    "/views/default",
    response_model=ViewDefaultResponse,
    summary="Choose the project default shared view (owner/admin)",
)
async def set_default_view(
    project_id: str,
    body: DefaultViewBody,
    server: OctopServer = Depends(get_server),
    user: User = Depends(current_user),
) -> dict[str, Any]:
    return await run_in_threadpool(
        _service(server).set_default_view,
        project_id,
        actor_user_id=user.id,
        expected_revision=body.expected_revision,
        view_id=body.view_id,
    )


@router.get("/views/{view_id}", response_model=ViewResponse, summary="Read one shared plan view")
async def get_view(
    project_id: str,
    view_id: ViewId,
    server: OctopServer = Depends(get_server),
    user: User = Depends(current_user),
) -> dict[str, Any]:
    return await run_in_threadpool(_service(server).get_view, project_id, view_id, user_id=user.id)


@router.patch(
    "/views/{view_id}",
    response_model=ViewMutationResponse,
    summary="Edit one versioned shared plan view (owner/admin)",
)
async def update_view(
    project_id: str,
    view_id: ViewId,
    body: UpdateViewBody,
    server: OctopServer = Depends(get_server),
    user: User = Depends(current_user),
) -> dict[str, Any]:
    return await run_in_threadpool(
        _service(server).update_view,
        project_id,
        view_id,
        actor_user_id=user.id,
        expected_version=body.expected_version,
        name=body.name if "name" in body.model_fields_set else UNSET,
        view_type=body.type if "type" in body.model_fields_set else UNSET,
        definition=body.definition if "definition" in body.model_fields_set else UNSET,
        expected_catalog_revision=body.expected_catalog_revision
        if "expected_catalog_revision" in body.model_fields_set
        else UNSET,
    )


@router.post(
    "/views/{view_id}/archive",
    response_model=ViewMutationResponse,
    summary="Archive a shared view while retaining an active default (owner/admin)",
)
async def archive_view(
    project_id: str,
    view_id: ViewId,
    body: ViewVersionBody,
    server: OctopServer = Depends(get_server),
    user: User = Depends(current_user),
) -> dict[str, Any]:
    return await run_in_threadpool(
        _service(server).archive_view,
        project_id,
        view_id,
        actor_user_id=user.id,
        expected_version=body.expected_version,
    )


@router.post(
    "/views/{view_id}/restore",
    response_model=ViewMutationResponse,
    summary="Restore a shared view at the active order tail (owner/admin)",
)
async def restore_view(
    project_id: str,
    view_id: ViewId,
    body: ViewVersionBody,
    server: OctopServer = Depends(get_server),
    user: User = Depends(current_user),
) -> dict[str, Any]:
    return await run_in_threadpool(
        _service(server).restore_view,
        project_id,
        view_id,
        actor_user_id=user.id,
        expected_version=body.expected_version,
    )
