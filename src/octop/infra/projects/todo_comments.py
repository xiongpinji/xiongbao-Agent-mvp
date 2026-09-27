"""Member-authorized text comments on project todos (PS-04B B1)."""

from __future__ import annotations

import base64
import re
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from octop.infra.db.repos.project_todo_comments import ProjectTodoCommentRow
from octop.infra.errors import ErrorCode, OctopError
from octop.infra.projects.service import DEFAULT_PAGE_LIMIT

COMMENT_BODY_MAX_LENGTH = 4000
MAX_COMMENT_LIMIT = 50

_CURSOR_CHARS = re.compile(r"^[A-Za-z0-9_-]+$")


@dataclass(frozen=True)
class CommentView:
    comment_id: str
    todo_id: str
    author_user_id: int | None
    author_name: str
    body: str
    images: list[dict[str, Any]]
    created_at: int


@dataclass(frozen=True)
class CommentPage:
    items: list[CommentView]
    next_cursor: str | None


def validate_comment_body(raw: str) -> str:
    if not isinstance(raw, str):
        raise ValueError("comment body must be text")
    body = raw.strip()
    if not body:
        raise ValueError("comment body must not be empty")
    if len(body) > COMMENT_BODY_MAX_LENGTH:
        raise ValueError("comment body must be at most 4000 characters")
    return body


def validate_client_request_id(raw: str) -> str:
    """Accept exactly a lowercase canonical UUID v4, never a user or actor id."""
    if not isinstance(raw, str) or len(raw) != 36:
        raise ValueError("client_request_id must be a lowercase UUID v4")
    try:
        request_id = UUID(raw)
    except ValueError:
        raise ValueError("client_request_id must be a lowercase UUID v4") from None
    if request_id.version != 4 or str(request_id) != raw:
        raise ValueError("client_request_id must be a lowercase UUID v4")
    return raw


def encode_comment_cursor(created_at: int, comment_id: str) -> str:
    raw = f"{created_at}:{comment_id}".encode("ascii")
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _invalid_cursor() -> OctopError:
    return OctopError(ErrorCode.PROJECT_TODO_COMMENT_CURSOR_INVALID, "invalid comment cursor")


def decode_comment_cursor(raw: str) -> tuple[int, str]:
    """Decode only canonical seek keys; the cursor never grants access."""
    if not isinstance(raw, str) or not raw or len(raw) > 128 or not _CURSOR_CHARS.fullmatch(raw):
        raise _invalid_cursor()
    try:
        decoded = base64.urlsafe_b64decode(raw + "=" * (-len(raw) % 4)).decode("ascii")
    except (ValueError, UnicodeDecodeError):
        raise _invalid_cursor() from None
    parts = decoded.split(":")
    if len(parts) != 2 or not parts[0].isascii() or not parts[0].isdigit():
        raise _invalid_cursor()
    created_at = int(parts[0])
    comment_id = parts[1]
    try:
        parsed = UUID(comment_id)
    except ValueError:
        raise _invalid_cursor() from None
    if created_at <= 0 or parsed.version != 4 or str(parsed) != comment_id:
        raise _invalid_cursor()
    if encode_comment_cursor(created_at, comment_id) != raw:
        raise _invalid_cursor()
    return created_at, comment_id


def _not_found() -> OctopError:
    return OctopError(ErrorCode.NOT_FOUND, "project todo not found")


class ProjectTodoCommentService:
    def __init__(self, services: Any) -> None:
        self._services = services

    @property
    def _repo(self) -> Any:
        return self._services.project_todo_comment_repo

    @staticmethod
    def _view(row: ProjectTodoCommentRow) -> CommentView:
        return CommentView(
            comment_id=row.comment_id,
            todo_id=row.todo_id,
            author_user_id=row.author_user_id,
            author_name=row.author_name,
            body=row.body,
            images=[],
            created_at=row.created_at,
        )

    def list_comments(
        self,
        project_id: str,
        todo_id: str,
        *,
        user_id: int,
        limit: int = DEFAULT_PAGE_LIMIT,
        cursor: str | None = None,
    ) -> CommentPage:
        if not 1 <= limit <= MAX_COMMENT_LIMIT:
            raise ValueError("comment limit must be between 1 and 50")
        # Check access before cursor validation, then re-check under the read
        # transaction so outsiders and revoked members consistently get 404.
        if not self._repo.has_access(project_id, todo_id, user_id):
            raise _not_found()
        before = decode_comment_cursor(cursor) if cursor is not None else None
        rows = self._repo.list_comments(
            project_id, todo_id, user_id=user_id, limit=limit, before=before
        )
        if rows is None:
            raise _not_found()
        has_more = len(rows) > limit
        page_rows = rows[:limit]
        next_cursor = (
            encode_comment_cursor(page_rows[-1].created_at, page_rows[-1].comment_id)
            if has_more and page_rows
            else None
        )
        return CommentPage(items=[self._view(row) for row in page_rows], next_cursor=next_cursor)

    def post_comment(
        self,
        project_id: str,
        todo_id: str,
        *,
        user_id: int,
        body: str,
        client_request_id: str,
    ) -> tuple[CommentView, bool]:
        clean_body = validate_comment_body(body)
        request_id = validate_client_request_id(client_request_id)
        result = self._repo.create_comment(
            project_id=project_id,
            todo_id=todo_id,
            author_user_id=user_id,
            body=clean_body,
            client_request_id=request_id,
        )
        if result.outcome == "missing":
            raise _not_found()
        if result.outcome == "conflict":
            raise OctopError(
                ErrorCode.PROJECT_TODO_COMMENT_CONFLICT,
                "client request id was used for different comment content",
            )
        if result.row is None:  # pragma: no cover - successful writes return a row
            raise OctopError(ErrorCode.INTERNAL_ERROR, "comment row missing after write")
        return self._view(result.row), result.outcome == "created"
