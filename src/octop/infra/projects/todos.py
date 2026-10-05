"""Project todo business rules (PS-04 plans): validation, authorization, views.

Access mirrors :class:`octop.infra.projects.service.ProjectService`: users who
are not project members get ``NOT_FOUND`` on every route so project/todo
existence is never leaked; members without the needed privilege get
``FORBIDDEN``. Writes are guarded by the todo's ``version`` — a stale write
gets 409 with nothing persisted and no event emitted.

Domain errors reuse existing :class:`~octop.infra.errors.ErrorCode` values so
no i18n/dashboard error-code parity files change:

* 404 ``NOT_FOUND`` — outsider, unknown project, foreign/deleted todo;
* 403 ``FORBIDDEN`` — member lacking the privilege;
* 409 ``INVITE_INVALID`` + ``details.reason="version_conflict"`` — stale
  ``expected_version`` (same 409 pattern as project membership conflicts);
* 400 ``INVITE_INVALID`` + ``details.reason`` — ``invalid_assignee`` (not a
  current member; deliberately indistinguishable from any other bad assignee
  so foreign-project membership is never revealed) or ``no_change``.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
import re
from dataclasses import asdict, dataclass
from typing import Any
from uuid import UUID

from octop.infra.db.repos._base import UNSET
from octop.infra.db.repos.project_plan_locks import DISPLAY_REVISION_MAX as DISPLAY_REVISION_MAX
from octop.infra.db.repos.project_todos import ProjectTodoRow
from octop.infra.errors import ErrorCode, OctopError
from octop.infra.projects.service import (
    DEFAULT_PAGE_LIMIT,
    ROLE_ADMIN,
    ROLE_OWNER,
)
from octop.infra.projects.todo_catalog import plan_error

MAX_TODO_TITLE_LENGTH = 200
MAX_TODO_DESCRIPTION_LENGTH = 4000
TODO_DESCRIPTION_FORMATS = ("plain", "markdown")
TODO_STATUSES = ("todo", "in_progress", "done")
BULK_MAX_ITEMS = 50
_MANAGER_ROLES = frozenset({ROLE_OWNER, ROLE_ADMIN})


def validate_todo_title(raw: str) -> str:
    """Trim, then require 1–200 characters (contract-fixed bound)."""
    title = raw.strip()
    if not title or len(title) > MAX_TODO_TITLE_LENGTH:
        raise ValueError(f"todo title must be 1-{MAX_TODO_TITLE_LENGTH} characters after trim")
    return title


def validate_todo_description(raw: str) -> str:
    description = raw.strip()
    if len(description) > MAX_TODO_DESCRIPTION_LENGTH:
        raise ValueError(
            f"todo description must be at most {MAX_TODO_DESCRIPTION_LENGTH} characters"
        )
    return description


def validate_todo_description_format(raw: str) -> str:
    if raw not in TODO_DESCRIPTION_FORMATS:
        raise ValueError("description_format must be plain or markdown")
    return raw


@dataclass(frozen=True)
class TodoView:
    todo_id: str
    project_id: str
    title: str
    description: str
    description_format: str
    status: str
    creator_user_id: int
    assignee_user_id: int | None
    version: int
    created_at: int
    updated_at: int
    start_date: str | None
    due_date: str | None
    priority_id: str | None
    tag_ids: list[str]
    catalog_revision: int
    display_revision: int
    parent_todo_id: str | None
    children_count: int
    done_children_count: int
    children_revision: int | None


@dataclass(frozen=True)
class TodoListPage:
    items: list[TodoView]
    limit: int
    offset: int
    has_more: bool


def _todo_not_found() -> OctopError:
    # Same message for foreign/deleted/unknown todos: no existence leak.
    return OctopError(ErrorCode.NOT_FOUND, "project todo not found")


def _version_conflict(todo_id: str = "") -> OctopError:
    details: dict[str, Any] = {"reason": "version_conflict"}
    if todo_id:
        details["todo_id"] = todo_id
    return OctopError(
        ErrorCode.INVITE_INVALID,
        "todo changed since it was loaded; refresh and retry",
        status=409,
        details=details,
    )


def _invalid_assignee() -> OctopError:
    return OctopError(
        ErrorCode.INVITE_INVALID,
        "assignee must be a current project member",
        status=400,
        details={"reason": "invalid_assignee"},
    )


class ProjectTodoService:
    def __init__(self, services: Any) -> None:
        self._services = services

    @property
    def _repo(self) -> Any:
        return self._services.project_todo_repo

    @property
    def _project_repo(self) -> Any:
        return self._services.project_repo

    @property
    def _timezone(self) -> str:
        return str(self._services.config.default_timezone)

    # ------------------------------------------------------------ access

    def _require_membership(self, project_id: str, user_id: int) -> Any:
        """Members only; outsiders get the same 404 as unknown projects."""
        membership = self._project_repo.get_membership(project_id, user_id)
        if membership is None:
            raise OctopError(ErrorCode.NOT_FOUND, "project not found")
        return membership

    def _require_manager(self, project_id: str, user_id: int) -> Any:
        membership = self._require_membership(project_id, user_id)
        if str(membership.role) not in _MANAGER_ROLES:
            raise OctopError(ErrorCode.FORBIDDEN, "bulk todo update requires owner or admin role")
        return membership

    def _raise_for_outcome(self, outcome: str, *, todo_id: str = "") -> None:
        """Map a repo outcome onto the HTTP error contract (success → return)."""
        if outcome in ("created", "updated", "deleted", "replayed", "listed"):
            return
        if outcome == "not_member":
            raise OctopError(ErrorCode.NOT_FOUND, "project not found")
        if outcome == "missing":
            raise _todo_not_found()
        if outcome == "forbidden":
            raise OctopError(ErrorCode.FORBIDDEN, "project todo change not allowed")
        if outcome == "invalid_assignee":
            raise _invalid_assignee()
        if outcome == "stale":
            raise _version_conflict(todo_id)
        if outcome == "catalog_stale":
            raise plan_error("catalog_revision_conflict", status=409)
        if outcome in (
            "invalid_dates",
            "invalid_priority",
            "invalid_tags",
            "invalid_catalog_revision",
        ):
            raise plan_error(outcome)
        if outcome in {
            "query_changed",
            "child_result_invalidated",
            "idempotency_conflict",
            "children_revision_conflict",
            "children_confirmation_required",
            "children_active_limit",
            "children_retained_limit",
        }:
            raise plan_error(outcome, status=409)
        if outcome == "invalid_cursor":
            raise plan_error(outcome, status=422)
        if outcome == "project_archived":
            raise plan_error(outcome, status=403)
        if outcome == "no_change":
            raise OctopError(
                ErrorCode.INVITE_INVALID,
                "at least one field must change",
                status=400,
                details={"reason": "no_change"},
            )
        raise OctopError(ErrorCode.INTERNAL_ERROR, f"unexpected todo outcome: {outcome}")

    @staticmethod
    def _view(row: ProjectTodoRow) -> TodoView:
        return TodoView(
            todo_id=row.todo_id,
            project_id=row.project_id,
            title=row.title,
            description=row.description,
            description_format=row.description_format,
            status=row.status,
            creator_user_id=row.creator_user_id,
            assignee_user_id=row.assignee_user_id,
            version=row.version,
            display_revision=row.display_revision,
            parent_todo_id=row.parent_todo_id,
            children_count=row.children_count,
            done_children_count=row.done_children_count,
            children_revision=row.children_revision,
            created_at=row.created_at,
            updated_at=row.updated_at,
            start_date=row.start_date,
            due_date=row.due_date,
            priority_id=row.priority_id,
            tag_ids=row.tag_ids,
            catalog_revision=row.catalog_revision,
        )

    # ------------------------------------------------------------ reads

    def list_todos(
        self,
        project_id: str,
        *,
        user_id: int,
        q: str = "",
        status: str | None = None,
        assignee_user_id: int | None = None,
        limit: int = DEFAULT_PAGE_LIMIT,
        offset: int = 0,
    ) -> TodoListPage:
        rows = self._repo.list_todos(
            project_id,
            user_id=user_id,
            q=q,
            status=status,
            assignee_user_id=assignee_user_id,
            limit=limit,
            offset=offset,
        )
        if rows is None:
            raise OctopError(ErrorCode.NOT_FOUND, "project not found")
        has_more = len(rows) > limit
        items = [self._view(row) for row in rows[:limit]]
        return TodoListPage(items=items, limit=limit, offset=offset, has_more=has_more)

    def get_todo(self, project_id: str, todo_id: str, *, user_id: int) -> TodoView:
        row = self._repo.get(project_id, todo_id, user_id=user_id)
        if row is None:
            raise _todo_not_found()
        return self._view(row)

    # ------------------------------------------------------------ mutations

    def create_todo(
        self,
        project_id: str,
        *,
        actor_user_id: int,
        title: str,
        description: str = "",
        description_format: str = "plain",
        assignee_user_id: int | None = None,
        status: str = "todo",
        start_date: str | None = None,
        due_date: str | None = None,
        priority_id: str | None = None,
        tag_ids: list[str] | None = None,
        expected_catalog_revision: Any = UNSET,
    ) -> TodoView:
        membership = self._require_membership(project_id, actor_user_id)
        clean_title = validate_todo_title(title)
        clean_description = validate_todo_description(description)
        clean_description_format = validate_todo_description_format(description_format)
        if status not in TODO_STATUSES:
            raise ValueError("todo status must be todo, in_progress, or done")
        # Fast pre-check; the repo re-checks inside the write transaction.
        if (
            assignee_user_id is not None
            and assignee_user_id != actor_user_id
            and str(membership.role) not in _MANAGER_ROLES
        ):
            raise OctopError(ErrorCode.FORBIDDEN, "only owner or admin may assign another member")
        mutation = self._repo.create(
            project_id=project_id,
            creator_user_id=actor_user_id,
            title=clean_title,
            description=clean_description,
            description_format=clean_description_format,
            assignee_user_id=assignee_user_id,
            status=status,
            start_date=start_date,
            due_date=due_date,
            priority_id=priority_id,
            tag_ids=[] if tag_ids is None else tag_ids,
            expected_catalog_revision=expected_catalog_revision,
            timezone=self._timezone,
        )
        self._raise_for_outcome(mutation.outcome)
        if mutation.row is None:  # pragma: no cover - created always carries a row
            raise OctopError(ErrorCode.INTERNAL_ERROR, "todo row missing after create")
        return self._view(mutation.row)

    def update_todo(
        self,
        project_id: str,
        todo_id: str,
        *,
        actor_user_id: int,
        expected_version: int,
        title: Any = UNSET,
        description: Any = UNSET,
        description_format: Any = UNSET,
        status: Any = UNSET,
        assignee_user_id: Any = UNSET,
        start_date: Any = UNSET,
        due_date: Any = UNSET,
        priority_id: Any = UNSET,
        tag_ids: Any = UNSET,
        expected_catalog_revision: Any = UNSET,
    ) -> TodoView:
        self._require_membership(project_id, actor_user_id)
        clean_title = UNSET if title is UNSET else validate_todo_title(title)
        clean_description = (
            UNSET if description is UNSET else validate_todo_description(description)
        )
        if description is UNSET and description_format is not UNSET:
            raise ValueError("description_format requires description")
        clean_description_format = (
            UNSET
            if clean_description is UNSET
            else (
                "plain"
                if description_format is UNSET
                else validate_todo_description_format(description_format)
            )
        )
        if status is not UNSET and str(status) not in TODO_STATUSES:
            raise OctopError(
                ErrorCode.INVITE_INVALID,
                "todo status must be todo, in_progress, or done",
                status=400,
                details={"reason": "invalid_status"},
            )
        mutation = self._repo.update(
            project_id=project_id,
            todo_id=todo_id,
            actor_user_id=actor_user_id,
            expected_version=expected_version,
            title=clean_title,
            description=clean_description,
            description_format=clean_description_format,
            status=status,
            assignee_user_id=assignee_user_id,
            start_date=start_date,
            due_date=due_date,
            priority_id=priority_id,
            tag_ids=tag_ids,
            expected_catalog_revision=expected_catalog_revision,
            timezone=self._timezone,
        )
        self._raise_for_outcome(mutation.outcome, todo_id=todo_id)
        if mutation.row is None:  # pragma: no cover - updated always carries a row
            raise OctopError(ErrorCode.INTERNAL_ERROR, "todo row missing after update")
        return self._view(mutation.row)

    def delete_todo(
        self,
        project_id: str,
        todo_id: str,
        *,
        actor_user_id: int,
        expected_version: int,
    ) -> None:
        self._require_membership(project_id, actor_user_id)
        mutation = self._repo.delete(
            project_id=project_id,
            todo_id=todo_id,
            actor_user_id=actor_user_id,
            expected_version=expected_version,
        )
        self._raise_for_outcome(mutation.outcome, todo_id=todo_id)

    def bulk_update_todos(
        self,
        project_id: str,
        *,
        actor_user_id: int,
        items: list[tuple[str, int]],
        status: Any = UNSET,
        assignee_user_id: Any = UNSET,
    ) -> list[TodoView]:
        self._require_manager(project_id, actor_user_id)
        if status is UNSET and assignee_user_id is UNSET:
            raise OctopError(
                ErrorCode.INVITE_INVALID,
                "bulk update requires status or assignee_user_id",
                status=400,
                details={"reason": "no_change"},
            )
        if status is not UNSET and str(status) not in TODO_STATUSES:
            raise OctopError(
                ErrorCode.INVITE_INVALID,
                "todo status must be todo, in_progress, or done",
                status=400,
                details={"reason": "invalid_status"},
            )
        mutation = self._repo.bulk_update(
            project_id=project_id,
            items=items,
            actor_user_id=actor_user_id,
            status=status,
            assignee_user_id=assignee_user_id,
        )
        if mutation.outcome != "updated":
            self._raise_for_outcome(mutation.outcome, todo_id=mutation.todo_id)
        return [self._view(row) for row in mutation.rows]

    def create_child(
        self,
        project_id: str,
        parent_todo_id: str,
        *,
        actor_user_id: int,
        expected_children_revision: int,
        client_request_id: str,
        fields: dict[str, Any],
    ) -> dict[str, Any]:
        parsed = UUID(client_request_id)
        if parsed.version != 4 or str(parsed) != client_request_id.lower():
            raise ValueError("client_request_id must be UUIDv4")
        if (
            type(expected_children_revision) is not int
            or not 1 <= expected_children_revision <= DISPLAY_REVISION_MAX
        ):
            raise ValueError("invalid children revision")
        clean = {
            "description": "",
            "description_format": "plain",
            "status": "todo",
            "assignee_user_id": None,
            "start_date": None,
            "due_date": None,
            "priority_id": None,
            "tag_ids": [],
            **fields,
        }
        clean["title"] = validate_todo_title(clean["title"])
        clean["description"] = validate_todo_description(clean.get("description", ""))
        clean["description_format"] = validate_todo_description_format(
            clean.get("description_format", "plain")
        )
        clean["tag_ids"] = sorted(set(clean.get("tag_ids", [])))
        if clean.get("status", "todo") not in TODO_STATUSES:
            raise ValueError("invalid todo status")
        # Include catalog field presence/value; exclude only the collection token.
        fingerprint = hashlib.sha256(
            json.dumps(clean, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode(
                "utf-8"
            )
        ).hexdigest()
        result = self._repo.create_child(
            project_id=project_id,
            parent_todo_id=parent_todo_id,
            actor_user_id=actor_user_id,
            expected_children_revision=expected_children_revision,
            client_request_id=str(parsed),
            request_fingerprint=fingerprint,
            fields=clean,
            timezone=self._timezone,
        )
        self._raise_for_outcome(result.outcome)
        data = dict(result.data)
        data["item"] = asdict(self._view(data["item"]))
        return data

    def list_children(
        self,
        project_id: str,
        parent_todo_id: str,
        *,
        actor_user_id: int,
        limit: int = 50,
        cursor: str | None = None,
    ) -> dict[str, Any]:
        if type(limit) is not int or not 1 <= limit <= 100:
            raise ValueError("limit must be between 1 and 100")
        value = None
        invalid = False
        if cursor is not None:
            try:
                if (
                    not 1 <= len(cursor.encode("utf-8")) <= 2048
                    or re.fullmatch(r"[A-Za-z0-9_-]+", cursor) is None
                ):
                    raise ValueError("invalid cursor")
                decoded = base64.b64decode(
                    cursor + "=" * (-len(cursor) % 4), altchars=b"-_", validate=True
                )
                if base64.urlsafe_b64encode(decoded).decode("ascii").rstrip("=") != cursor:
                    raise ValueError("noncanonical cursor")

                def unique(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
                    result: dict[str, Any] = {}
                    for key, item in pairs:
                        if key in result:
                            raise ValueError("duplicate key")
                        result[key] = item
                    return result

                value = json.loads(decoded.decode("utf-8"), object_pairs_hook=unique)
                if type(value) is not dict or set(value) != {
                    "v",
                    "project_id",
                    "parent_todo_id",
                    "last_created_at",
                    "last_todo_id",
                    "children_revision",
                    "parent_display_revision",
                }:
                    raise ValueError("invalid cursor schema")
                if (
                    type(value["v"]) is not int
                    or value["v"] != 1
                    or type(value["last_created_at"]) is not int
                ):
                    raise ValueError("invalid cursor version or time")
                for key in ("children_revision", "parent_display_revision"):
                    if type(value[key]) is not int or not 1 <= value[key] <= DISPLAY_REVISION_MAX:
                        raise ValueError("invalid revision")
                for key in ("project_id", "parent_todo_id", "last_todo_id"):
                    if (
                        type(value[key]) is not str
                        or not 1 <= len(value[key]) <= 64
                        or any(ord(char) < 32 for char in value[key])
                    ):
                        raise ValueError("invalid cursor identity")
            except (ValueError, UnicodeError, binascii.Error, RecursionError):
                invalid = True
                value = None
        result = self._repo.list_children(
            project_id=project_id,
            parent_todo_id=parent_todo_id,
            actor_user_id=actor_user_id,
            limit=limit,
            cursor=value,
            invalid_cursor=invalid,
        )
        self._raise_for_outcome(result.outcome)
        data = dict(result.data)
        data["items"] = [asdict(self._view(row)) for row in data["items"]]
        data["next_cursor"] = (
            None
            if data["next_cursor"] is None
            else base64.urlsafe_b64encode(
                json.dumps(data["next_cursor"], separators=(",", ":")).encode("utf-8")
            )
            .decode("ascii")
            .rstrip("=")
        )
        return data

    def delete_tree(
        self,
        project_id: str,
        parent_todo_id: str,
        *,
        actor_user_id: int,
        expected_version: int,
        expected_children_revision: int,
        children: list[tuple[str, int]],
    ) -> dict[str, Any]:
        result = self._repo.delete_tree(
            project_id=project_id,
            parent_todo_id=parent_todo_id,
            actor_user_id=actor_user_id,
            expected_version=expected_version,
            expected_children_revision=expected_children_revision,
            children=children,
        )
        self._raise_for_outcome(result.outcome)
        return dict(result.data)
