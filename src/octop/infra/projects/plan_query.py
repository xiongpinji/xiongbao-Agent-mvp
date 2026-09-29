"""Project plan query request and snapshot orchestration."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict
from datetime import date
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from octop.i18n import tr
from octop.infra.db.repos import project_plan_locks as locks
from octop.infra.db.repos.project_plan_query import QueryAnchorChanged
from octop.infra.errors import ErrorCode, OctopError
from octop.infra.projects.plan_definition import (
    BoardDefinition,
    CalendarDefinition,
    DateText,
    GanttDefinition,
    GroupBy,
    PlanDefinition,
    QueryCursor,
    encode_cursor,
    parse_cursor,
    parse_definition,
)
from octop.infra.projects.todo_catalog import ProjectPlanError

DefinitionPayload = PlanDefinition | BoardDefinition | CalendarDefinition | GanttDefinition


class _StrictQueryModel(BaseModel):
    model_config = ConfigDict(
        extra="forbid", strict=True, revalidate_instances="always", hide_input_in_errors=True
    )


class PlanQueryGroupKey(_StrictQueryModel):
    kind: GroupBy
    id: Annotated[str, Field(min_length=1, max_length=64)] | None


class PlanQueryWindow(_StrictQueryModel):
    start_date: DateText
    end_date: DateText

    @model_validator(mode="after")
    def _ordered_window(self) -> PlanQueryWindow:
        if (
            not 0
            <= (date.fromisoformat(self.end_date) - date.fromisoformat(self.start_date)).days
            <= 365
        ):
            raise ValueError("Invalid query date window")
        return self


class PlanQueryRequest(_StrictQueryModel):
    view_id: Annotated[str, Field(min_length=1, max_length=64)]
    expected_view_version: Annotated[int, Field(strict=True, ge=1)]
    expected_catalog_revision: Annotated[int, Field(strict=True, ge=1)]
    override_definition: DefinitionPayload | None = None
    group_key: PlanQueryGroupKey | None = None
    window: PlanQueryWindow | None = None
    bucket: Literal["scheduled", "unscheduled"] | None = None
    limit: Annotated[int, Field(strict=True, ge=1, le=100)] = 50
    cursor: Annotated[str, Field(min_length=1, max_length=2048)] | None = None

    @model_validator(mode="after")
    def _present_override(self) -> PlanQueryRequest:
        if "override_definition" in self.model_fields_set and self.override_definition is None:
            raise ValueError("Invalid query override")
        return self


class _QueryError(ProjectPlanError):
    def localized_message(self, locale: str, **kwargs: object) -> str:
        return tr(f"project_plan.query_errors.{self.details['reason']}", locale)


def query_error(
    reason: str, *, status: int = 422, condition_indices: list[int] | None = None
) -> OctopError:
    if reason not in {
        "invalid_query_request",
        "query_changed",
        "filter_reference_unavailable",
        "transaction_conflict",
    }:
        raise OctopError(ErrorCode.INTERNAL_ERROR, "unexpected project query outcome")
    details: dict[str, Any] = {"reason": reason}
    if condition_indices is not None and reason == "filter_reference_unavailable":
        details["condition_indices"] = condition_indices
    return _QueryError(
        ErrorCode.INVITE_INVALID,
        tr(f"project_plan.query_errors.{reason}", "en"),
        status=status,
        details=details,
    )


class ProjectPlanQueryService:
    def __init__(self, services: Any) -> None:
        self._services = services

    def query(self, project_id: str, *, user_id: int, data: object) -> dict[str, Any]:
        try:
            request = PlanQueryRequest.model_validate(data)
        except ValidationError:
            raise query_error("invalid_query_request") from None
        repo = self._services.project_plan_query_repo
        if type(user_id) is not int or not 1 <= user_id <= repo.max_integer:
            raise OctopError(ErrorCode.NOT_FOUND, "project not found")
        if (
            request.expected_view_version > repo.max_integer
            or request.expected_catalog_revision > repo.max_integer
        ):
            raise query_error("invalid_query_request")
        try:
            cursor = (
                None
                if request.cursor is None
                else parse_cursor(request.cursor, max_version=repo.max_integer)
            )
        except ValueError:
            raise query_error("invalid_query_request") from None
        try:
            with repo.read_transaction() as conn:
                result = self._query_in_connection(conn, project_id, user_id, request, cursor)
        except Exception as error:
            if getattr(error, "sqlstate", None) not in {"40001", "40P01", "55P03"}:
                raise
            # The failed read transaction has completely exited and rolled back.
            # This new transaction checks only project membership; never retry.
            if not repo.can_read_project(project_id, user_id):
                raise OctopError(ErrorCode.NOT_FOUND, "project not found") from None
            raise query_error("transaction_conflict", status=409) from None
        return result

    def _query_in_connection(
        self,
        conn: Any,
        project_id: str,
        user_id: int,
        request: PlanQueryRequest,
        cursor: QueryCursor | None,
    ) -> dict[str, Any]:
        repo = self._services.project_plan_query_repo
        hint = repo.bootstrap_in_connection(conn, project_id, user_id, request.view_id)
        if hint is None:
            raise OctopError(ErrorCode.NOT_FOUND, "project view not found")
        definition = None
        try:
            raw = (
                request.override_definition.model_dump(mode="json")
                if request.override_definition is not None
                else json.loads(hint.definition_json)
            )
            definition = parse_definition(hint.view_type, raw).model_dump(mode="json")
        except (ValueError, TypeError):
            if request.override_definition is not None:
                raise query_error("invalid_query_request") from None
            # Lock/recheck ACL before reporting a fixed stored integrity error.
        members = {user_id}
        metadata_required = definition is not None and (
            definition["group_by"] == "assignee"
            or any(item["field"] == "assignee" for item in definition["sort"])
        )
        metadata = repo.members_in_connection(conn, project_id) if metadata_required else []
        members.update(member.user_id for member in metadata)
        if (
            request.group_key is not None
            and request.group_key.kind == "assignee"
            and request.group_key.id is not None
        ):
            if re.fullmatch(r"[1-9][0-9]*", request.group_key.id) is None:
                raise query_error("invalid_query_request")
            members.add(int(request.group_key.id))
        for condition in definition["filters"] if definition is not None else []:
            if condition["field"] == "assignee":
                members.update(value for value in condition["values"] if value is not None)
        if any(value > repo.max_integer for value in members):
            raise query_error("invalid_query_request")
        snapshot = self._services.project_todo_view_repo.read_plan_snapshot_in_connection(
            conn, project_id, user_id, sorted(members), request.view_id
        )
        if snapshot is None or snapshot.view is None or snapshot.view.archived_at is not None:
            raise OctopError(ErrorCode.NOT_FOUND, "project view not found")
        if (
            snapshot.view.version != request.expected_view_version
            or snapshot.catalog_revision != request.expected_catalog_revision
            or snapshot.view.view_type != hint.view_type
        ):
            raise query_error("query_changed", status=409)
        if definition is None:
            raise OctopError(
                ErrorCode.INTERNAL_ERROR, "stored plan definition is invalid"
            ) from None
        if snapshot.view.view_type not in ("calendar", "gantt"):
            if request.model_fields_set & {"window", "bucket"}:
                raise query_error("invalid_query_request")
        elif request.window is None or request.bucket is None:
            raise query_error("invalid_query_request")
        priority_ids = {str(item["priority_id"]) for item in snapshot.priorities}
        tag_ids = {str(item["tag_id"]) for item in snapshot.tags}
        group_key = None if request.group_key is None else request.group_key.model_dump(mode="json")
        if group_key is not None:
            kind, value = group_key["kind"], group_key["id"]
            allowed: dict[str, set[str | None]] = {
                "status": {"todo", "in_progress", "done"},
                "assignee": {str(member.user_id) for member in metadata} | {None},
                "priority": priority_ids | {None},
                "tag": tag_ids | {None},
                "source": {"manual"},
            }
            if kind != definition["group_by"] or value not in allowed[kind]:
                raise query_error("invalid_query_request")
        unavailable = []
        for index, condition in enumerate(definition["filters"]):
            reference_set = {
                "assignee": set(snapshot.member_roles),
                "priority": priority_ids,
                "tags": tag_ids,
            }.get(condition["field"])
            if reference_set is not None and any(
                value not in reference_set
                for value in condition.get("values", [])
                if value is not None
            ):
                unavailable.append(index)
        if unavailable:
            if request.override_definition is not None:
                raise query_error("invalid_query_request")
            raise query_error(
                "filter_reference_unavailable", status=409, condition_indices=unavailable
            )
        timezone = self._services.config.default_timezone
        today = locks.server_today(timezone)
        window = None if request.window is None else request.window.model_dump(mode="json")
        fingerprint_data = {
            "project_id": project_id,
            "view_id": request.view_id,
            "view_version": snapshot.view.version,
            "catalog_revision": snapshot.catalog_revision,
            "definition": definition,
            "group_key": group_key,
            "window": window,
            "bucket": request.bucket,
        }
        if metadata_required:
            fingerprint_data["members_digest"] = hashlib.sha256(
                json.dumps(
                    [(member.user_id, member.sort_key) for member in metadata],
                    ensure_ascii=False,
                    separators=(",", ":"),
                ).encode("utf-8")
            ).hexdigest()
        if any(item["op"] == "overdue" for item in definition["filters"]):
            fingerprint_data["server_today"] = today
        fingerprint = hashlib.sha256(
            json.dumps(
                fingerprint_data,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()
        if cursor is not None and cursor.query_fingerprint != fingerprint:
            raise query_error("query_changed", status=409)
        try:
            page = repo.materialize_in_connection(
                conn,
                project_id,
                definition=definition,
                catalog_revision=snapshot.catalog_revision,
                today=today,
                limit=request.limit,
                members=metadata,
                priorities=snapshot.priorities,
                tags=snapshot.tags,
                group_key=group_key,
                view_type=snapshot.view.view_type,
                window=window,
                bucket=request.bucket,
                anchor=None if cursor is None else (cursor.last_todo_id, cursor.last_version),
            )
        except QueryAnchorChanged:
            raise query_error("query_changed", status=409) from None
        items = []
        for row in page.rows:
            item = asdict(row)
            item.pop("deleted_at")
            items.append(item)
        next_cursor = None
        if page.has_more:
            last = page.rows[-1]
            next_cursor = encode_cursor(
                {
                    "v": 1,
                    "query_fingerprint": fingerprint,
                    "last_todo_id": last.todo_id,
                    "last_version": last.version,
                },
                max_version=repo.max_integer,
            )
        result = {
            "items": items,
            "next_cursor": next_cursor,
            "total": page.total,
            "matched_total": page.matched_total,
            "unscheduled_total": page.unscheduled_total,
            "groups": page.groups,
            "view_id": request.view_id,
            "view_version": snapshot.view.version,
            "catalog_revision": snapshot.catalog_revision,
            "server_today": today,
            "server_timezone": timezone,
            "query_fingerprint": fingerprint,
        }
        return result
