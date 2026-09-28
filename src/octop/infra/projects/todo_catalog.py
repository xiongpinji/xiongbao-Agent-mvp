"""Project todo catalog validation, views and error contract."""

from __future__ import annotations

import unicodedata
from typing import Any

from octop.i18n import tr
from octop.infra.db.repos import project_plan_locks
from octop.infra.db.repos._base import UNSET
from octop.infra.db.repos.project_todo_catalog import CatalogMutation, CatalogOptionRow
from octop.infra.errors import ErrorCode, OctopError

CATALOG_COLORS = ("red", "orange", "yellow", "green", "blue", "purple", "gray")


def validate_catalog_name(raw: str) -> str:
    name = raw.strip()
    if not name or len(name) > 40 or any(unicodedata.category(char) == "Cc" for char in raw):
        raise ValueError("catalog name must be 1-40 characters without control characters")
    return name


class ProjectPlanError(OctopError):
    """Reuse existing stable codes with specific localized plan reasons."""

    def localized_message(self, locale: str, **kwargs: object) -> str:
        return tr(f"project_plan.errors.{self.details['reason']}", locale)


def plan_error(reason: str, *, status: int = 422) -> OctopError:
    return ProjectPlanError(
        ErrorCode.INVITE_INVALID,
        tr(f"project_plan.errors.{reason}", "en"),
        status=status,
        details={"reason": reason},
    )


def option_payload(item: CatalogOptionRow, kind: str) -> dict[str, Any]:
    payload = {
        "priority_id" if kind == "priority" else "tag_id": item.option_id,
        "name": item.name,
        "color": item.color,
        "archived_at": item.archived_at,
        "created_at": item.created_at,
        "updated_at": item.updated_at,
    }
    if kind == "priority":
        payload["position"] = item.position
    return payload


class ProjectTodoCatalogService:
    def __init__(self, services: Any) -> None:
        self._services = services

    def get_catalog(self, project_id: str, *, user_id: int) -> dict[str, Any]:
        snapshot = self._services.project_todo_catalog_repo.get_catalog(project_id, user_id=user_id)
        if snapshot is None:
            raise OctopError(ErrorCode.NOT_FOUND, "project not found")
        timezone = self._services.config.default_timezone
        return {
            "project_id": snapshot.project_id,
            "revision": snapshot.revision,
            "server_today": project_plan_locks.server_today(timezone),
            "server_timezone": timezone,
            "priorities": [option_payload(item, "priority") for item in snapshot.priorities],
            "tags": [option_payload(item, "tag") for item in snapshot.tags],
        }

    @staticmethod
    def _result(mutation: CatalogMutation, kind: str) -> dict[str, Any]:
        if mutation.outcome == "not_member":
            raise OctopError(ErrorCode.NOT_FOUND, "project not found")
        if mutation.outcome == "missing":
            raise OctopError(ErrorCode.NOT_FOUND, "project catalog option not found")
        if mutation.outcome == "forbidden":
            raise OctopError(ErrorCode.FORBIDDEN, "catalog editing requires owner or admin")
        if mutation.outcome == "stale":
            raise plan_error("catalog_revision_conflict", status=409)
        if mutation.outcome in ("project_archived", "name_conflict", "active_limit", "total_limit"):
            raise plan_error(mutation.outcome, status=409)
        if mutation.outcome in ("no_change", "invalid_lifecycle", "invalid_order"):
            raise plan_error(mutation.outcome)
        if mutation.outcome == "ordered":
            return {
                "revision": mutation.revision,
                "priorities": [option_payload(item, "priority") for item in mutation.priorities],
            }
        if mutation.item is None:
            raise OctopError(ErrorCode.INTERNAL_ERROR, "catalog option missing after mutation")
        return {"revision": mutation.revision, "item": option_payload(mutation.item, kind)}

    def create_option(
        self,
        project_id: str,
        *,
        actor_user_id: int,
        expected_revision: int,
        kind: str,
        name: str,
        color: str,
    ) -> dict[str, Any]:
        clean_name = validate_catalog_name(name)
        if color not in CATALOG_COLORS:
            raise ValueError("invalid catalog color")
        mutation = self._services.project_todo_catalog_repo.create_option(
            project_id=project_id,
            actor_user_id=actor_user_id,
            expected_revision=expected_revision,
            kind=kind,
            name=clean_name,
            color=color,
        )
        return self._result(mutation, kind)

    def update_option(
        self,
        project_id: str,
        *,
        actor_user_id: int,
        expected_revision: int,
        kind: str,
        option_id: str,
        name: Any = UNSET,
        color: Any = UNSET,
    ) -> dict[str, Any]:
        clean_name = validate_catalog_name(name) if name is not UNSET else UNSET
        if color is not UNSET and color not in CATALOG_COLORS:
            raise ValueError("invalid catalog color")
        mutation = self._services.project_todo_catalog_repo.update_option(
            project_id=project_id,
            actor_user_id=actor_user_id,
            expected_revision=expected_revision,
            kind=kind,
            option_id=option_id,
            name=clean_name,
            color=color,
        )
        return self._result(mutation, kind)

    def set_archived(
        self,
        project_id: str,
        *,
        actor_user_id: int,
        expected_revision: int,
        kind: str,
        option_id: str,
        archived: bool,
    ) -> dict[str, Any]:
        mutation = self._services.project_todo_catalog_repo.set_archived(
            project_id=project_id,
            actor_user_id=actor_user_id,
            expected_revision=expected_revision,
            kind=kind,
            option_id=option_id,
            archived=archived,
        )
        return self._result(mutation, kind)

    def order_priorities(
        self,
        project_id: str,
        *,
        actor_user_id: int,
        expected_revision: int,
        priority_ids: list[str],
    ) -> dict[str, Any]:
        mutation = self._services.project_todo_catalog_repo.order_priorities(
            project_id=project_id,
            actor_user_id=actor_user_id,
            expected_revision=expected_revision,
            priority_ids=priority_ids,
        )
        return self._result(mutation, "priority")
