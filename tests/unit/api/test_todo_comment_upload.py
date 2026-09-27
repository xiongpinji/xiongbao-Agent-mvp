"""The todo-comment multipart cap runs before Starlette writes upload spools."""

from __future__ import annotations

from collections import deque
from tempfile import SpooledTemporaryFile
from typing import Any

import pytest
from starlette.requests import Request

from octop.api.common.todo_comment_upload import (
    _UploadTooLarge,
    bounded_request_stream,
    read_comment_multipart,
)
from octop.infra.errors import OctopError


def _request(
    chunks: list[bytes],
    *,
    length: str | None = None,
    content_type: str = "multipart/form-data; boundary=B",
) -> tuple[Request, list[int]]:
    queue = deque(chunks)
    reads = [0]

    async def receive() -> dict[str, Any]:
        reads[0] += 1
        if queue:
            return {"type": "http.request", "body": queue.popleft(), "more_body": bool(queue)}
        return {"type": "http.request", "body": b"", "more_body": False}

    headers = [(b"content-type", content_type.encode("ascii"))]
    if length is not None:
        headers.append((b"content-length", length.encode("ascii")))
    scope = {
        "type": "http",
        "method": "POST",
        "path": "/",
        "headers": headers,
        "query_string": b"",
        "scheme": "http",
        "http_version": "1.1",
        "server": ("test", 80),
        "client": ("test", 123),
    }
    return Request(scope, receive), reads


@pytest.mark.asyncio
@pytest.mark.parametrize("length", [None, "1"])
async def test_actual_stream_cap_ignores_missing_or_forged_content_length(
    length: str | None,
) -> None:
    request, reads = _request([b"1234567890", b"abcdefghij", b"never-read"], length=length)
    with pytest.raises(_UploadTooLarge):
        _ = [chunk async for chunk in bounded_request_stream(request, max_bytes=15)]
    assert reads == [2]


@pytest.mark.asyncio
async def test_declared_over_limit_rejects_before_reading_a_chunk() -> None:
    request, reads = _request([b"tiny"], length="16")
    with pytest.raises(_UploadTooLarge):
        _ = [chunk async for chunk in bounded_request_stream(request, max_bytes=15)]
    assert reads == [0]


@pytest.mark.asyncio
async def test_bad_multipart_closes_every_created_spool(monkeypatch: pytest.MonkeyPatch) -> None:
    import starlette.formparsers as formparsers

    opened: list[SpooledTemporaryFile[bytes]] = []
    original = SpooledTemporaryFile

    def tracked_spool(*args: Any, **kwargs: Any) -> SpooledTemporaryFile[bytes]:
        spool: SpooledTemporaryFile[bytes] = original(*args, **kwargs)
        opened.append(spool)
        return spool

    monkeypatch.setattr(formparsers, "SpooledTemporaryFile", tracked_spool)
    body = (
        b'--B\r\nContent-Disposition: form-data; name="images"; filename="one.png"\r\n'
        b"Content-Type: image/png\r\n\r\nx\r\n"
        b'--B\r\nContent-Disposition: form-data; name="unknown"\r\n\r\ny\r\n--B--\r\n'
    )
    request, _reads = _request([body])
    with pytest.raises(OctopError) as error:
        await read_comment_multipart(request)
    assert error.value.status == 422
    assert opened and all(spool.closed for spool in opened)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "content_type, body",
    [
        ("multipart/form-data", b"--B--\r\n"),
        ("multipart/form-data; boundary=B", b"--different\r\ninvalid\r\n"),
    ],
)
async def test_missing_or_invalid_boundary_is_controlled_422(
    content_type: str,
    body: bytes,
) -> None:
    request, _reads = _request([body], content_type=content_type)
    with pytest.raises(OctopError) as error:
        await read_comment_multipart(request)
    assert error.value.status == 422


@pytest.mark.asyncio
async def test_parser_constructor_value_error_is_controlled_422(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import octop.api.common.todo_comment_upload as upload

    def reject_constructor(_request: Request) -> None:
        raise ValueError("bad boundary")

    monkeypatch.setattr(upload, "BoundedCommentMultipartParser", reject_constructor)
    request, _reads = _request([b"--B--\r\n"])
    with pytest.raises(OctopError) as error:
        await read_comment_multipart(request)
    assert error.value.status == 422
