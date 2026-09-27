"""Member-authorized text comments on project todos (PS-04B B1)."""

from __future__ import annotations

import base64
import hashlib
import re
import threading
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
from typing import Any
from uuid import UUID

from octop.infra.db.repos.project_todo_comments import NewCommentImage, ProjectTodoCommentRow
from octop.infra.errors import ErrorCode, OctopError
from octop.infra.projects.asset_storage import AssetStorageError, make_object_key
from octop.infra.projects.service import DEFAULT_PAGE_LIMIT
from octop.infra.projects.todo_comment_image_storage import (
    MAX_COMMENT_IMAGE_BYTES,
    CommentImageInvalid,
    CommentImageTooLarge,
    ProjectTodoCommentImageStorage,
    validate_comment_image,
)
from octop.infra.utils.ulid import new_ulid

COMMENT_BODY_MAX_LENGTH = 4000
MAX_COMMENT_LIMIT = 50
MAX_COMMENT_IMAGES = 5
MAX_COMMENT_UPLOAD_BYTES = 20 * 1024 * 1024

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


@dataclass(frozen=True)
class CommentImageUpload:
    data: bytes
    media_type: str


@dataclass(frozen=True)
class CommentImageDownload:
    data: bytes
    media_type: str


_IMAGE_STORES: dict[str, ProjectTodoCommentImageStorage] = {}
_IMAGE_STORES_LOCK = threading.Lock()


def _image_store_for(root: Path) -> ProjectTodoCommentImageStorage:
    with _IMAGE_STORES_LOCK:
        return _IMAGE_STORES.setdefault(str(root), ProjectTodoCommentImageStorage(root))


def validate_comment_body(raw: str, *, allow_empty: bool = False) -> str:
    if not isinstance(raw, str):
        raise ValueError("comment body must be text")
    body = raw.strip()
    if not body and not allow_empty:
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
        self._storage = _image_store_for(services.paths.project_todo_comment_images)

    @property
    def _repo(self) -> Any:
        return self._services.project_todo_comment_repo

    def assert_access(self, project_id: str, todo_id: str, *, user_id: int) -> None:
        if not self._repo.has_access(project_id, todo_id, user_id):
            raise _not_found()

    def _ensure_reclaim(self) -> None:
        self._storage.ensure_root()
        with self._storage.reclaim_lock:
            if not self._storage.reclaim_done:
                self._storage.reclaim_orphans(self._repo.known_image_object_keys())
                self._storage.reclaim_done = True

    @staticmethod
    def _view(row: ProjectTodoCommentRow) -> CommentView:
        return CommentView(
            comment_id=row.comment_id,
            todo_id=row.todo_id,
            author_user_id=row.author_user_id,
            author_name=row.author_name,
            body=row.body,
            images=[
                {
                    "image_id": image.image_id,
                    "media_type": image.media_type,
                    "size_bytes": image.size_bytes,
                    "position": image.position,
                }
                for image in row.images
            ],
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
        self.assert_access(project_id, todo_id, user_id=user_id)
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
        images: list[CommentImageUpload] | None = None,
    ) -> tuple[CommentView, bool]:
        # Prioritize the uniform 404 over validation details for outsiders.
        if not self._repo.has_access(project_id, todo_id, user_id):
            raise _not_found()
        uploads = images or []
        if len(uploads) > MAX_COMMENT_IMAGES:
            raise OctopError(
                ErrorCode.PROJECT_TODO_COMMENT_IMAGE_INVALID,
                "at most five comment images are allowed",
            )
        clean_body = validate_comment_body(body, allow_empty=bool(uploads))
        request_id = validate_client_request_id(client_request_id)
        prepared = []
        total = 0
        for upload in uploads:
            try:
                image = validate_comment_image(upload.data, upload.media_type)
            except CommentImageTooLarge as exc:
                raise OctopError(
                    ErrorCode.PROJECT_TODO_COMMENT_IMAGE_TOO_LARGE,
                    "comment image exceeds the per-file byte limit",
                ) from exc
            except CommentImageInvalid as exc:
                raise OctopError(
                    ErrorCode.PROJECT_TODO_COMMENT_IMAGE_INVALID,
                    "invalid comment image",
                ) from exc
            total += image.size_bytes
            if total > MAX_COMMENT_UPLOAD_BYTES:
                raise OctopError(
                    ErrorCode.PROJECT_TODO_COMMENT_IMAGE_TOO_LARGE,
                    "comment images exceed the request byte limit",
                )
            prepared.append(image)

        paths_to_discard: list[Path] = []
        records: list[NewCommentImage] = []
        committed = False
        try:
            if prepared:
                self._ensure_reclaim()
            for image in prepared:
                image_id = new_ulid()
                temp, stored = self._storage.write_temp(
                    image_id, BytesIO(image.data), max_bytes=MAX_COMMENT_IMAGE_BYTES
                )
                final = self._storage.final_path(project_id, image_id)
                # Include both paths before publish: an error after os.replace
                # must still remove the orphan final object.
                paths_to_discard.extend((temp, final))
                self._storage.publish(temp, project_id, image_id)
                records.append(
                    NewCommentImage(
                        image_id=image_id,
                        object_key=make_object_key(project_id, image_id),
                        size_bytes=stored.size_bytes,
                        sha256=stored.sha256,
                        media_type=image.media_type,
                    )
                )
            result = self._repo.create_comment(
                project_id=project_id,
                todo_id=todo_id,
                author_user_id=user_id,
                body=clean_body,
                client_request_id=request_id,
                images=records,
            )
            if result.outcome == "created":
                committed = True
        finally:
            if not committed:
                for path in paths_to_discard:
                    self._storage.discard(path)
        if result.outcome == "missing":
            raise _not_found()
        if result.outcome == "conflict":
            raise OctopError(
                ErrorCode.PROJECT_TODO_COMMENT_CONFLICT,
                "client request id was used for different comment content",
            )
        if result.outcome == "quota":
            raise OctopError(
                ErrorCode.PROJECT_TODO_COMMENT_IMAGE_QUOTA,
                "project comment image quota exceeded",
            )
        if result.row is None:  # pragma: no cover - successful writes return a row
            raise OctopError(ErrorCode.INTERNAL_ERROR, "comment row missing after write")
        return self._view(result.row), result.outcome == "created"

    def get_image(
        self,
        project_id: str,
        todo_id: str,
        comment_id: str,
        image_id: str,
        *,
        user_id: int,
    ) -> CommentImageDownload:
        row = self._repo.get_image(project_id, todo_id, comment_id, image_id, user_id=user_id)
        if row is None:
            raise _not_found()
        try:
            self._ensure_reclaim()
            with self._storage.read_object(row.object_key) as source:
                data = source.read(MAX_COMMENT_IMAGE_BYTES + 1)
        except (AssetStorageError, OSError):
            raise _not_found() from None
        if len(data) != row.size_bytes or hashlib.sha256(data).hexdigest() != row.sha256:
            raise _not_found()
        return CommentImageDownload(data=data, media_type=row.media_type)
