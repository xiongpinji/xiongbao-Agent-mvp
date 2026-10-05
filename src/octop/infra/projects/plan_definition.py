"""Strict shared plan definitions and compact query cursors."""

from __future__ import annotations

import base64
import binascii
import json
import re
from datetime import date
from typing import Annotated, Any, Literal, Self

from pydantic import (
    AfterValidator,
    BaseModel,
    ConfigDict,
    Field,
    StrictBool,
    ValidationError,
    field_validator,
    model_validator,
)

from octop.infra.db.repos.project_plan_locks import DISPLAY_REVISION_MAX

ViewType = Literal["list", "table", "board", "gantt", "calendar"]
VisibleField = Literal[
    "title",
    "status",
    "assignee",
    "priority",
    "tags",
    "start_date",
    "due_date",
    "created_at",
    "updated_at",
    "source",
]
SortField = Literal[
    "title",
    "status",
    "assignee",
    "priority",
    "start_date",
    "due_date",
    "created_at",
    "updated_at",
]
GroupBy = Literal["status", "assignee", "priority", "tag", "source"]
InOperator = Literal["in", "not_in"]
EmptyOperator = Literal["is_empty", "not_empty"]
Status = Literal["todo", "in_progress", "done"]
UserId = Annotated[int, Field(strict=True, ge=1)]
ReferenceId = Annotated[str, Field(strict=True, min_length=1, max_length=64)]
TitleLiteral = Annotated[str, Field(strict=True, min_length=1, max_length=200)]


def _calendar_day(value: str) -> str:
    if re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", value) is None:
        raise ValueError("Date must use ASCII YYYY-MM-DD")
    date.fromisoformat(value)
    if value < "1900-01-01":
        raise ValueError("Date is outside the supported range")
    return value


DateText = Annotated[str, Field(strict=True), AfterValidator(_calendar_day)]


class _StrictModel(BaseModel):
    model_config = ConfigDict(
        extra="forbid", strict=True, revalidate_instances="always", hide_input_in_errors=True
    )


class _UniqueValuesFilter(_StrictModel):
    @field_validator("values", check_fields=False)
    @classmethod
    def _unique_values(cls, values: list[Any]) -> list[Any]:
        if len(set(values)) != len(values):
            raise ValueError("Filter values must be unique")
        return values


class TitleFilter(_StrictModel):
    field: Literal["title"]
    op: Literal["contains", "not_contains"]
    value: TitleLiteral


class StatusFilter(_UniqueValuesFilter):
    field: Literal["status"]
    op: InOperator
    values: Annotated[list[Status], Field(min_length=1, max_length=3)]


class AssigneeFilter(_UniqueValuesFilter):
    field: Literal["assignee"]
    op: InOperator
    values: Annotated[list[UserId | None], Field(min_length=1, max_length=50)]


class PriorityFilter(_UniqueValuesFilter):
    field: Literal["priority"]
    op: InOperator
    values: Annotated[list[ReferenceId | None], Field(min_length=1, max_length=32)]


class TagValuesFilter(_UniqueValuesFilter):
    field: Literal["tags"]
    op: Literal["any", "all", "none_of"]
    values: Annotated[list[ReferenceId], Field(min_length=1, max_length=20)]


class TagEmptyFilter(_StrictModel):
    field: Literal["tags"]
    op: EmptyOperator


class DateValueFilter(_StrictModel):
    field: Literal["start_date", "due_date"]
    op: Literal["on", "before", "after"]
    value: DateText


class DateRangeFilter(_StrictModel):
    field: Literal["start_date", "due_date"]
    op: Literal["between"]
    values: Annotated[list[DateText], Field(min_length=2, max_length=2)]

    @model_validator(mode="after")
    def _ordered_dates(self) -> Self:
        if self.values[0] > self.values[1]:
            raise ValueError("Date range must be ordered")
        return self


class DateEmptyFilter(_StrictModel):
    field: Literal["start_date", "due_date"]
    op: EmptyOperator


class OverdueFilter(_StrictModel):
    field: Literal["due_date"]
    op: Literal["overdue"]
    value: StrictBool


class SourceFilter(_UniqueValuesFilter):
    field: Literal["source"]
    op: InOperator
    values: Annotated[list[Literal["manual"]], Field(min_length=1, max_length=1)]


FilterClause = (
    TitleFilter
    | StatusFilter
    | AssigneeFilter
    | PriorityFilter
    | TagValuesFilter
    | TagEmptyFilter
    | DateValueFilter
    | DateRangeFilter
    | DateEmptyFilter
    | OverdueFilter
    | SourceFilter
)


class SortSpec(_StrictModel):
    field: SortField
    direction: Literal["asc", "desc"]


class _DefinitionBase(_StrictModel):
    schema_version: Literal[1]
    group_by: GroupBy | None
    filters: Annotated[list[FilterClause], Field(max_length=12)]
    sort: Annotated[list[SortSpec], Field(max_length=3)]

    @field_validator("schema_version", mode="before")
    @classmethod
    def _strict_schema_version(cls, value: Any) -> Any:
        if type(value) is not int:
            raise ValueError("schema_version must be an integer")
        return value

    @field_validator("fields", check_fields=False)
    @classmethod
    def _visible_fields(cls, values: list[str]) -> list[str]:
        if values[0] != "title" or len(set(values)) != len(values):
            raise ValueError("Visible fields must start with title and be unique")
        return values

    @field_validator("sort")
    @classmethod
    def _sort_fields(cls, values: list[SortSpec]) -> list[SortSpec]:
        if len({item.field for item in values}) != len(values):
            raise ValueError("Sort fields must be unique")
        return values


class PlanDefinition(_DefinitionBase):
    fields: Annotated[list[VisibleField], Field(min_length=1, max_length=10)]


class PublicTableDefinition(PlanDefinition):
    """Public table settings, without dormant attachment fields."""

    show_subtodos: StrictBool = Field(
        default=False,
        description="Include active first-level subtodos as independently filtered table rows.",
    )


TableVisibleField = Literal[
    "title",
    "status",
    "assignee",
    "priority",
    "tags",
    "start_date",
    "due_date",
    "created_at",
    "updated_at",
    "source",
    "attachments",
]


class TableDefinition(_DefinitionBase):
    """Dormant internal D1 attachment contract; not used by public parsing."""

    fields: Annotated[list[TableVisibleField], Field(min_length=1, max_length=11)]


class BoardDefinition(PlanDefinition):
    group_by: GroupBy


class CalendarSettings(_StrictModel):
    date_basis: Literal["due_date", "start_date"]
    mode: Literal["month", "week"]


class GanttSettings(_StrictModel):
    zoom: Literal["day", "week", "month"]


class CalendarDefinition(PlanDefinition):
    group_by: None
    calendar: CalendarSettings


class GanttDefinition(PlanDefinition):
    group_by: None
    gantt: GanttSettings


_MODELS: dict[ViewType, type[PlanDefinition]] = {
    "list": PlanDefinition,
    "table": PublicTableDefinition,
    "board": BoardDefinition,
    "calendar": CalendarDefinition,
    "gantt": GanttDefinition,
}


def _view_type(value: Any) -> ViewType:
    if type(value) is not str or value not in _MODELS:
        raise ValueError("Unknown view type")
    return value


def default_definition(view_type: ViewType) -> dict[str, Any]:
    kind = _view_type(view_type)
    fields = ["title", "status", "assignee", "priority"]
    if kind in ("table", "list", "board"):
        fields.append("tags")
    if kind in ("table", "list"):
        fields.extend(["start_date", "due_date"])
    value: dict[str, Any] = {
        "schema_version": 1,
        "fields": fields,
        "group_by": "status" if kind == "board" else None,
        "filters": [],
        "sort": [{"field": "updated_at", "direction": "desc"}],
    }
    if kind == "table":
        value["show_subtodos"] = False
    elif kind == "calendar":
        value["calendar"] = {"date_basis": "due_date", "mode": "month"}
    elif kind == "gantt":
        value["gantt"] = {"zoom": "week"}
    return value


def parse_definition(view_type: ViewType, value: Any) -> PlanDefinition:
    model = _MODELS[_view_type(view_type)]
    try:
        return model.model_validate(value)
    except ValidationError:
        raise ValueError("Invalid plan definition") from None


class _CursorIdentity(_StrictModel):
    query_fingerprint: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
    last_todo_id: Annotated[str, Field(pattern=r"^[0-7][0-9A-HJKMNP-TV-Z]{25}$")]

    @field_validator("v", mode="before", check_fields=False)
    @classmethod
    def _strict_version(cls, value: Any) -> Any:
        if type(value) is not int:
            raise ValueError("Cursor schema version must be an integer")
        return value


class LegacyQueryCursor(_CursorIdentity):
    v: Literal[1]
    last_version: Annotated[int, Field(strict=True, ge=1)]


class QueryCursor(_CursorIdentity):
    v: Literal[2]
    last_display_revision: Annotated[int, Field(strict=True, ge=1, le=DISPLAY_REVISION_MAX)]


def _version_boundary(max_version: int) -> None:
    if type(max_version) is not int or max_version < 1:
        raise ValueError("max_version must be a positive integer")


def _validated_cursor(
    value: Any, max_version: int, *, allow_legacy: bool = False
) -> QueryCursor | LegacyQueryCursor:
    _version_boundary(max_version)
    try:
        if (
            allow_legacy
            and isinstance(value, dict)
            and type(value.get("v")) is int
            and value.get("v") == 1
        ):
            legacy = LegacyQueryCursor.model_validate(value)
            if legacy.last_version > max_version:
                raise ValueError("Cursor version exceeds the database boundary")
            return legacy
        cursor = QueryCursor.model_validate(value)
    except ValidationError:
        raise ValueError("Invalid cursor") from None
    return cursor


def _encode_bytes(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def encode_cursor(value: Any, *, max_version: int) -> str:
    """Encode only cursor identity, never definition or complete sort text."""
    cursor = _validated_cursor(value, max_version)
    raw = json.dumps(cursor.model_dump(mode="json"), separators=(",", ":")).encode("utf-8")
    return _encode_bytes(raw)


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate cursor JSON key")
        result[key] = value
    return result


def _reject_constant(value: str) -> Any:
    raise ValueError("Non-finite cursor JSON number")


def parse_cursor(raw: str, *, max_version: int) -> QueryCursor | LegacyQueryCursor:
    """Validate bounded, canonical base64url and strict UTF-8 JSON structure."""
    _version_boundary(max_version)
    if type(raw) is not str or not 1 <= len(raw) <= 2048:
        raise ValueError("Invalid cursor length or type")
    if re.fullmatch(r"[A-Za-z0-9_-]+", raw) is None:
        raise ValueError("Cursor must be unpadded base64url")
    try:
        decoded = base64.b64decode(raw + "=" * (-len(raw) % 4), altchars=b"-_", validate=True)
        if _encode_bytes(decoded) != raw:
            raise ValueError("Noncanonical cursor pad bits")
        value = json.loads(
            decoded.decode("utf-8"),
            object_pairs_hook=_unique_object,
            parse_constant=_reject_constant,
        )
    except (binascii.Error, UnicodeDecodeError, json.JSONDecodeError):
        raise ValueError("Malformed cursor") from None
    return _validated_cursor(value, max_version, allow_legacy=True)
