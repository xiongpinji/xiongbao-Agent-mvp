"""Bounded request parsing before todo-comment files reach multipart spool.

The route accepts ``Request`` rather than FastAPI ``File``/``Form`` parameters;
otherwise FastAPI parses and spools the body before application limits run.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator

from fastapi import Request
from starlette.datastructures import UploadFile
from starlette.formparsers import MultiPartException, MultiPartParser

from octop.infra.errors import ErrorCode, OctopError
from octop.infra.projects.todo_comment_image_storage import MAX_COMMENT_IMAGE_BYTES
from octop.infra.projects.todo_comments import (
    MAX_COMMENT_IMAGES,
    MAX_COMMENT_UPLOAD_BYTES,
    CommentImageUpload,
)

MAX_COMMENT_RAW_BODY_BYTES = MAX_COMMENT_UPLOAD_BYTES + 256 * 1024
MAX_COMMENT_JSON_BODY_BYTES = 32 * 1024


class _UploadTooLarge(OSError):
    """Raised inside Starlette's parser so it closes every open spool file."""


def _too_large() -> OctopError:
    return OctopError(
        ErrorCode.PROJECT_TODO_COMMENT_IMAGE_TOO_LARGE,
        "comment request exceeds its byte limit",
    )


def _invalid_form() -> OctopError:
    return OctopError(
        ErrorCode.PROJECT_TODO_COMMENT_IMAGE_INVALID,
        "invalid comment multipart form",
    )


async def bounded_request_stream(
    request: Request, *, max_bytes: int
) -> AsyncGenerator[bytes, None]:
    """Count actual ASGI chunks, independent of Content-Length claims."""
    declared = request.headers.get("content-length")
    if declared is not None:
        try:
            size = int(declared)
        except ValueError:
            raise _invalid_form() from None
        if size < 0:
            raise _invalid_form()
        if size > max_bytes:
            raise _UploadTooLarge()
    consumed = 0
    async for chunk in request.stream():
        consumed += len(chunk)
        if consumed > max_bytes:
            raise _UploadTooLarge()
        yield chunk


class BoundedCommentMultipartParser(MultiPartParser):
    """Reject a file chunk before it is queued for Starlette's spool write."""

    def __init__(self, request: Request) -> None:
        super().__init__(
            request.headers,
            bounded_request_stream(request, max_bytes=MAX_COMMENT_RAW_BODY_BYTES),
            max_files=MAX_COMMENT_IMAGES,
            max_fields=2,
            max_part_size=20 * 1024,
        )
        self._current_file_bytes = 0
        self._total_file_bytes = 0

    def on_part_begin(self) -> None:
        super().on_part_begin()
        self._current_file_bytes = 0

    def on_headers_finished(self) -> None:
        super().on_headers_finished()
        name = self._current_part.field_name
        if self._current_part.file is not None:
            if name != "images":
                raise MultiPartException("unexpected file field")
        elif name not in {"body", "client_request_id"}:
            raise MultiPartException("unexpected text field")

    def on_part_data(self, data: bytes, start: int, end: int) -> None:
        if self._current_part.file is not None:
            size = end - start
            self._current_file_bytes += size
            self._total_file_bytes += size
            if (
                self._current_file_bytes > MAX_COMMENT_IMAGE_BYTES
                or self._total_file_bytes > MAX_COMMENT_UPLOAD_BYTES
            ):
                raise _UploadTooLarge()
        super().on_part_data(data, start, end)


async def read_bounded_json(request: Request) -> bytes:
    try:
        return b"".join(
            [
                chunk
                async for chunk in bounded_request_stream(
                    request, max_bytes=MAX_COMMENT_JSON_BODY_BYTES
                )
            ]
        )
    except _UploadTooLarge:
        raise _too_large() from None


async def read_comment_multipart(request: Request) -> tuple[str, str, list[CommentImageUpload]]:
    parser: BoundedCommentMultipartParser | None = None
    form = None
    try:
        try:
            parser = BoundedCommentMultipartParser(request)
            form = await parser.parse()
        except _UploadTooLarge:
            raise _too_large() from None
        except (MultiPartException, ValueError):
            raise _invalid_form() from None
        fields = form.multi_items()
        body: str | None = None
        request_id: str | None = None
        images: list[CommentImageUpload] = []
        for name, value in fields:
            if name == "body" and isinstance(value, str) and body is None:
                body = value
            elif name == "client_request_id" and isinstance(value, str) and request_id is None:
                request_id = value
            elif name == "images" and isinstance(value, UploadFile):
                data = await value.read(MAX_COMMENT_IMAGE_BYTES + 1)
                if len(data) > MAX_COMMENT_IMAGE_BYTES:
                    raise _too_large()
                images.append(CommentImageUpload(data, value.content_type or ""))
            else:
                raise _invalid_form()
        if request_id is None:
            raise _invalid_form()
        return body or "", request_id, images
    finally:
        if form is not None:
            await form.close()
        elif parser is not None:
            # Starlette closes on its common error paths; the explicit cleanup
            # also covers malformed parser exceptions outside that catch list.
            for spool in parser._files_to_close_on_error:
                spool.close()
