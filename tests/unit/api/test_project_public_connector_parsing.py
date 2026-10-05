from __future__ import annotations

import asyncio
import json

import pytest
from starlette.requests import Request

from octop.api.routers import projects
from octop.infra.errors import ErrorCode, OctopError


def request(body: bytes, content_type: str = "application/json") -> Request:
    async def receive():
        return {"type": "http.request", "body": body, "more_body": False}

    return Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/",
            "headers": [(b"content-type", content_type.encode())],
        },
        receive,
    )


@pytest.mark.parametrize(
    "body",
    [
        b"{}",
        b"[]",
        b'{"expected_project_revision":true,"expected_grant_revision":1}',
        b'{"expected_project_revision":1.0,"expected_grant_revision":1}',
        b'{"expected_project_revision":1,"expected_project_revision":2,"expected_grant_revision":1}',
        b'{"expected_project_revision":NaN,"expected_grant_revision":1}',
        b'{"expected_project_revision":9007199254740992,"expected_grant_revision":1}',
        b'{"expected_project_revision":1,"expected_grant_revision":1,"secret-marker":"sensitive"}',
    ],
)
async def test_structural_payload_is_safely_rejected(body: bytes) -> None:
    with pytest.raises(OctopError) as error:
        await projects._read_public_connector_command(request(body), "revoke")
    assert error.value.status == 422
    assert error.value.details == {"reason": "invalid_payload"}
    assert "sensitive" not in str(error.value) and "secret-marker" not in str(error.value)


async def test_valid_flat_revoke_strict_boundary() -> None:
    payload = {"expected_project_revision": 9007199254740991, "expected_grant_revision": 1}
    assert (
        await projects._read_public_connector_command(
            request(json.dumps(payload).encode()), "revoke"
        )
        == payload
    )


@pytest.mark.parametrize(
    "content_type",
    [
        "",
        "text/plain",
        "application/json; charset=latin-1",
        "application/problem+json",
        "application/json; charset",
    ],
)
async def test_unsupported_media_is_safe(content_type: str) -> None:
    with pytest.raises(OctopError) as error:
        await projects._read_public_connector_command(
            request(b"secret-marker", content_type), "revoke"
        )
    assert error.value.status == 415
    assert error.value.details == {"reason": "unsupported_content_type"}


@pytest.mark.parametrize(
    "failure",
    [
        RuntimeError("secret-marker"),
        OctopError(ErrorCode.INTERNAL_ERROR, "secret-marker", status=500),
        None,
    ],
)
async def test_read_failure_is_always_safe(failure) -> None:
    async def receive():
        if failure is not None:
            raise failure
        return {"type": "http.disconnect"}

    req = Request({"type": "http", "headers": [(b"content-type", b"application/json")]}, receive)
    with pytest.raises(OctopError) as error:
        await projects._read_public_connector_command(req, "revoke")
    assert error.value.status == 400
    assert error.value.details == {"reason": "request_read_failed"}
    assert "secret-marker" not in str(error.value)


async def test_read_cancellation_is_not_swallowed() -> None:
    async def receive():
        raise asyncio.CancelledError

    req = Request({"type": "http", "headers": [(b"content-type", b"application/json")]}, receive)
    with pytest.raises(asyncio.CancelledError):
        await projects._read_public_connector_command(req, "revoke")


async def test_cap_stops_stream_and_exact_limit_is_valid() -> None:
    reads = 0

    async def receive():
        nonlocal reads
        reads += 1
        assert reads == 1, "Reader must stop before another chunk"
        return {"type": "http.request", "body": b"x" * 65537, "more_body": True}

    req = Request({"type": "http", "headers": [(b"content-type", b"application/json")]}, receive)
    with pytest.raises(OctopError) as error:
        await projects._read_public_connector_command(req, "revoke")
    assert error.value.status == 413 and reads == 1
    assert error.value.details == {"reason": "body_too_large"}
    payload = {"expected_project_revision": 1, "expected_grant_revision": 1}
    body = json.dumps(payload).encode().ljust(65536, b" ")
    assert (
        await projects._read_public_connector_command(
            request(body, 'Application/JSON; charset="UTF-8"'), "revoke"
        )
        == payload
    )


@pytest.mark.parametrize(
    "body",
    [
        b"\xff",
        b'{"expected_project_revision":Infinity,"expected_grant_revision":1}',
        b'{"expected_project_revision":-Infinity,"expected_grant_revision":1}',
        b"[" * 2000 + b"]" * 2000,
        b'{"expected_project_revision":1,"expected_grant_revision":null}',
        b'{"expected_project_revision":0,"expected_grant_revision":1}',
        b'{"expected_project_revision":-1,"expected_grant_revision":1}',
        b'{"expected_project_revision":{"secret":"marker"},"expected_grant_revision":1}',
    ],
)
async def test_decode_and_revision_failures(body: bytes) -> None:
    with pytest.raises(OctopError) as error:
        await projects._read_public_connector_command(request(body), "revoke")
    assert error.value.status == 422 and error.value.details == {"reason": "invalid_payload"}


@pytest.mark.parametrize(
    "field,value",
    [
        ("display_name", "😀" * 129),
        ("description", "x" * 2049),
        ("endpoint", "x" * 8193),
        ("bearer_token", "x" * 8193),
        ("kind", "x" * 65),
        ("display_name", "\ud800"),
        ("endpoint", []),
        ("kind", "unsupported"),
    ],
)
async def test_flat_string_gross_bounds_and_unicode(field: str, value) -> None:
    payload = {
        "expected_project_revision": 1,
        "kind": "http_mcp_static_bearer",
        "display_name": "Safe",
        "description": "",
        "endpoint": "https://synthetic.invalid/mcp",
        "bearer_token": "synthetic",
    }
    payload[field] = value
    with pytest.raises(OctopError) as error:
        await projects._read_public_connector_command(
            request(json.dumps(payload).encode()), "create"
        )
    assert error.value.status == 422 and error.value.details == {"reason": "invalid_payload"}
