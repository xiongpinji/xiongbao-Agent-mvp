"""Shared plan view validation and DTOs; transport and SQL stay outside."""

from __future__ import annotations

import json
import unicodedata
from typing import Any, cast

from octop.i18n import tr
from octop.infra.db.repos._base import UNSET
from octop.infra.db.repos.project_todo_views import (
    StoredViewRow,
    ViewDefinition,
    ViewMutation,
    ViewTransactionConflict,
)
from octop.infra.db.services import SharedServices
from octop.infra.errors import ErrorCode, OctopError
from octop.infra.projects.plan_definition import PlanDefinition, ViewType, parse_definition
from octop.infra.projects.todo_catalog import ProjectPlanError

_VIEW_REASONS = frozenset(
    {
        "name_conflict",
        "view_revision_conflict",
        "view_version_conflict",
        "catalog_revision_conflict",
        "active_limit",
        "total_limit",
        "last_active_view",
        "no_change",
        "invalid_lifecycle",
        "invalid_order",
        "invalid_definition",
        "invalid_view_request",
        "invalid_catalog_reference",
        "invalid_assignee",
        "transaction_conflict",
        "project_archived",
    }
)


class _ViewError(ProjectPlanError):
    def localized_message(self, locale: str, **kwargs: object) -> str:
        return tr(f"project_plan.view_errors.{self.details['reason']}", locale)


def view_error(reason: str, *, status: int = 422) -> OctopError:
    if reason not in _VIEW_REASONS:
        raise OctopError(ErrorCode.INTERNAL_ERROR, "unexpected project view outcome")
    return _ViewError(
        ErrorCode.INVITE_INVALID,
        tr(f"project_plan.view_errors.{reason}", "en"),
        status=status,
        details={"reason": reason},
    )


def validate_view_name(raw: object) -> str:
    if type(raw) is not str:
        raise ValueError("invalid view name")
    name = raw.strip()
    if not 1 <= len(name) <= 40 or any(unicodedata.category(char) == "Cc" for char in raw):
        raise ValueError("invalid view name")
    return name


def _positive_integer(value: object) -> int:
    if type(value) is not int or value < 1:
        raise view_error("invalid_view_request")
    return value


def _kind(value: object) -> ViewType:
    if type(value) is not str or value not in ("list", "table", "board", "gantt", "calendar"):
        raise view_error("invalid_definition")
    return cast(ViewType, value)


def _definition(value: object, view_type: object = UNSET) -> ViewDefinition:
    if isinstance(value, PlanDefinition):
        value = value.model_dump(mode="json")
    types: tuple[ViewType, ...] = (
        ("list", "table", "board", "gantt", "calendar")
        if view_type is UNSET
        else (_kind(view_type),)
    )
    compatible: list[str] = []
    canonical: dict[str, Any] | None = None
    for kind in types:
        try:
            parsed = parse_definition(kind, value).model_dump(mode="json")
        except (ValueError, TypeError):
            continue
        if canonical is not None and parsed != canonical:
            raise view_error("invalid_definition") from None
        compatible.append(kind)
        canonical = parsed
    if canonical is None:
        raise view_error("invalid_definition") from None
    assignees: set[int] = set()
    priorities: set[str] = set()
    tags: set[str] = set()
    for condition in canonical["filters"]:
        values = condition.get("values", [])
        if condition["field"] == "assignee":
            assignees.update(value for value in values if value is not None)
        elif condition["field"] == "priority":
            priorities.update(value for value in values if value is not None)
        elif condition["field"] == "tags":
            tags.update(values)
    return ViewDefinition(
        tuple(compatible),
        json.dumps(canonical, ensure_ascii=False, separators=(",", ":")),
        tuple(sorted(assignees)),
        tuple(sorted(priorities)),
        tuple(sorted(tags)),
    )


def _name(value: object) -> str:
    try:
        return validate_view_name(value)
    except ValueError:
        raise view_error("invalid_view_request") from None


def view_payload(row: StoredViewRow) -> dict[str, Any]:
    try:
        definition = parse_definition(
            cast(ViewType, row.view_type), json.loads(row.definition_json)
        ).model_dump(mode="json")
    except (ValueError, TypeError):
        raise OctopError(
            ErrorCode.INTERNAL_ERROR, "project todo view definition is invalid"
        ) from None
    return {
        "view_id": row.view_id,
        "project_id": row.project_id,
        "name": row.name,
        "type": row.view_type,
        "definition": definition,
        "version": row.version,
        "position": row.position,
        "archived_at": row.archived_at,
        "created_at": row.created_at,
        "updated_at": row.updated_at,
    }


class ProjectTodoViewService:
    def __init__(self, services: SharedServices) -> None:
        self._services = services

    def list_views(self, project_id: str, *, user_id: int) -> dict[str, Any]:
        try:
            snapshot = self._services.project_todo_view_repo.list_views(project_id, user_id=user_id)
        except ViewTransactionConflict:
            raise view_error("transaction_conflict", status=409) from None
        if snapshot is None:
            raise OctopError(ErrorCode.NOT_FOUND, "project not found")
        return {
            "project_id": snapshot.project_id,
            "revision": snapshot.revision,
            "default_view_id": snapshot.default_view_id,
            "items": [view_payload(item) for item in snapshot.items],
        }

    def get_view(self, project_id: str, view_id: str, *, user_id: int) -> dict[str, Any]:
        try:
            row = self._services.project_todo_view_repo.get_view(
                project_id, view_id, user_id=user_id
            )
        except ViewTransactionConflict:
            raise view_error("transaction_conflict", status=409) from None
        if row is None:
            raise OctopError(ErrorCode.NOT_FOUND, "project todo view not found")
        return view_payload(row)

    @staticmethod
    def _result(result: ViewMutation) -> dict[str, Any]:
        if result.outcome in ("not_member", "missing"):
            raise OctopError(ErrorCode.NOT_FOUND, "project todo view not found")
        if result.outcome == "forbidden":
            raise OctopError(ErrorCode.FORBIDDEN, "view editing requires project owner or admin")
        conflicts = {
            "view_version_conflict",
            "view_revision_conflict",
            "catalog_revision_conflict",
            "name_conflict",
            "active_limit",
            "total_limit",
            "last_active_view",
            "project_archived",
            "transaction_conflict",
        }
        if result.outcome in conflicts:
            raise view_error(result.outcome, status=409)
        if result.outcome in {
            "no_change",
            "invalid_lifecycle",
            "invalid_order",
            "invalid_definition",
            "invalid_catalog_reference",
            "invalid_assignee",
        }:
            raise view_error(result.outcome)
        payload: dict[str, Any] = {
            "revision": result.revision,
            "default_view_id": result.default_view_id,
        }
        if result.outcome == "default_changed":
            return payload
        if result.outcome == "ordered":
            payload["items"] = [view_payload(item) for item in result.items]
            return payload
        if (
            result.outcome in {"created", "updated", "archived", "restored"}
            and result.item is not None
        ):
            payload["item"] = view_payload(result.item)
            return payload
        raise OctopError(ErrorCode.INTERNAL_ERROR, "unexpected project view outcome")

    def create_view(
        self,
        project_id: str,
        *,
        actor_user_id: int,
        expected_revision: int,
        expected_catalog_revision: int,
        name: object,
        view_type: object,
        definition: object,
    ) -> dict[str, Any]:
        revision, catalog = (
            _positive_integer(expected_revision),
            _positive_integer(expected_catalog_revision),
        )
        kind, normalized = _kind(view_type), _name(name)
        parsed = _definition(definition, kind)
        return self._result(
            self._services.project_todo_view_repo.create_view(
                project_id,
                actor_user_id=actor_user_id,
                expected_revision=revision,
                expected_catalog_revision=catalog,
                name=normalized,
                view_type=kind,
                definition=parsed,
            )
        )

    def update_view(
        self,
        project_id: str,
        view_id: str,
        *,
        actor_user_id: int,
        expected_version: int,
        name: object = UNSET,
        view_type: object = UNSET,
        definition: object = UNSET,
        expected_catalog_revision: object = UNSET,
    ) -> dict[str, Any]:
        version = _positive_integer(expected_version)
        normalized = UNSET if name is UNSET else _name(name)
        kind: object = UNSET if view_type is UNSET else _kind(view_type)
        catalog = (
            None
            if expected_catalog_revision is UNSET
            else _positive_integer(expected_catalog_revision)
        )
        if definition is not UNSET and catalog is None:
            raise view_error("invalid_view_request")
        parsed = None if definition is UNSET else _definition(definition, kind)
        return self._result(
            self._services.project_todo_view_repo.update_view(
                project_id,
                view_id,
                actor_user_id=actor_user_id,
                expected_version=version,
                name=normalized,
                view_type=kind,
                definition=parsed,
                expected_catalog_revision=catalog,
            )
        )

    def order_views(
        self,
        project_id: str,
        *,
        actor_user_id: int,
        expected_revision: int,
        view_ids: list[str],
    ) -> dict[str, Any]:
        revision = _positive_integer(expected_revision)
        if type(view_ids) is not list or any(
            type(value) is not str or not 1 <= len(value) <= 64 for value in view_ids
        ):
            raise view_error("invalid_view_request")
        return self._result(
            self._services.project_todo_view_repo.order_views(
                project_id,
                actor_user_id=actor_user_id,
                expected_revision=revision,
                view_ids=view_ids,
            )
        )

    def set_default_view(
        self,
        project_id: str,
        *,
        actor_user_id: int,
        expected_revision: int,
        view_id: str,
    ) -> dict[str, Any]:
        revision = _positive_integer(expected_revision)
        if type(view_id) is not str or not 1 <= len(view_id) <= 64:
            raise view_error("invalid_view_request")
        return self._result(
            self._services.project_todo_view_repo.set_default_view(
                project_id,
                actor_user_id=actor_user_id,
                expected_revision=revision,
                view_id=view_id,
            )
        )

    def archive_view(
        self,
        project_id: str,
        view_id: str,
        *,
        actor_user_id: int,
        expected_version: int,
    ) -> dict[str, Any]:
        return self._result(
            self._services.project_todo_view_repo.archive_view(
                project_id,
                view_id,
                actor_user_id=actor_user_id,
                expected_version=_positive_integer(expected_version),
            )
        )

    def restore_view(
        self,
        project_id: str,
        view_id: str,
        *,
        actor_user_id: int,
        expected_version: int,
    ) -> dict[str, Any]:
        return self._result(
            self._services.project_todo_view_repo.restore_view(
                project_id,
                view_id,
                actor_user_id=actor_user_id,
                expected_version=_positive_integer(expected_version),
            )
        )
