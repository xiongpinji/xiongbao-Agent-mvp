"""HTTP API for secure project assets (PS-06A / 023A).

Thin transport only: authorization, naming, pagination, and the publish
protocol live in :class:`octop.infra.projects.assets.ProjectAssetService`
(race-safe SQL in ``ProjectAssetRepo``, disk in ``ProjectAssetStorage``).
The operator is always ``current_user.id`` — uploads accept no actor field,
extra body fields are rejected outright, and neither project-admin nor
instance-admin identity bypasses membership: outsiders and unknown projects
share one uniform 404. Node ids, object keys, and download URLs are never
authorization credentials. Downloads always answer a forced attachment with
conservative ``application/octet-stream`` + ``nosniff``; real disk paths and
object keys never appear in any response.

All disk and sync-DB work runs in the default executor so the event loop
stays responsive during large uploads.
"""

from __future__ import annotations

import asyncio
import os
from collections.abc import Iterator
from functools import partial
from typing import BinaryIO, Literal, cast

from fastapi import APIRouter, Depends, File, Form, Query, Request, UploadFile
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ConfigDict
from starlette.background import BackgroundTask

from octop.api.common.content_disposition import content_disposition
from octop.api.deps import current_user, get_server
from octop.config import DEFAULT_MAX_UPLOAD_MB, upload_mb_to_bytes
from octop.infra.errors import ErrorCode, OctopError
from octop.infra.projects.asset_storage import AssetStorageError, open_regular_file_no_follow
from octop.infra.projects.assets import (
    ASSET_NAME_MAX_LENGTH,
    AssetDownloadView,
    AssetNodeView,
    AssetPage,
    AssetUsageView,
    ProjectAssetService,
    UploadedAssetView,
)
from octop.infra.projects.service import DEFAULT_PAGE_LIMIT, MAX_PAGE_LIMIT
from octop.infra.server import OctopServer
from octop.infra.users.identity import User

router = APIRouter(prefix="/projects/{project_id}")

#: Multipart framing overhead tolerated by the whole-body pre-check; the
#: authoritative cap is enforced on actual streamed bytes in storage.
_MULTIPART_OVERHEAD_ALLOWANCE = 64 * 1024

AssetKind = Literal["file", "folder"]


def _asset_service(server: OctopServer) -> ProjectAssetService:
    if server.services is None:
        raise OctopError(ErrorCode.INTERNAL_ERROR, "server services unavailable")
    return ProjectAssetService(server.services)


def _max_upload_bytes(server: OctopServer) -> int:
    """Instance upload cap in bytes (module-level so tests can monkeypatch)."""
    config = getattr(getattr(server, "services", None), "config", None)
    value = getattr(config, "max_upload_mb", None)
    if isinstance(value, int) and not isinstance(value, bool) and value > 0:
        return upload_mb_to_bytes(value)
    return upload_mb_to_bytes(DEFAULT_MAX_UPLOAD_MB)


def _too_large(max_bytes: int) -> OctopError:
    max_mb = max(1, max_bytes // (1024 * 1024))
    return OctopError(
        ErrorCode.PROJECT_ASSET_TOO_LARGE,
        f"file too large (max {max_mb}MB)",
        details={"max_mb": max_mb},
    )


def _declared_size(upload: object) -> int | None:
    raw = getattr(upload, "size", None)
    if isinstance(raw, bool) or not isinstance(raw, int) or raw <= 0:
        return None
    return raw


class AssetNodeResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    node_id: str
    parent_node_id: str | None
    kind: AssetKind
    name: str
    size_bytes: int | None
    media_type: str | None
    created_at: int
    updated_at: int


class AssetVersionResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version_id: str
    size_bytes: int
    sha256: str
    media_type: str | None


class AssetUploadResponse(AssetNodeResponse):
    version: AssetVersionResponse


class AssetPageResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: list[AssetNodeResponse]
    total: int
    limit: int
    offset: int
    has_more: bool


class AssetUsageResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    file_count: int
    total_bytes: int


class CreateFolderBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    parent_id: str | None = None
    name: str


def _download_chunks(stream: BinaryIO) -> Iterator[bytes]:
    while chunk := stream.read(256 * 1024):
        yield chunk


def _node_payload(view: AssetNodeView) -> AssetNodeResponse:
    """Exactly the contract's safe node fields — no object keys, no paths."""
    return AssetNodeResponse(
        node_id=view.node_id,
        parent_node_id=view.parent_node_id,
        kind=cast(AssetKind, view.kind),
        name=view.name,
        size_bytes=view.size_bytes,
        media_type=view.media_type,
        created_at=view.created_at,
        updated_at=view.updated_at,
    )


def _page_payload(page: AssetPage) -> AssetPageResponse:
    return AssetPageResponse(
        items=[_node_payload(view) for view in page.items],
        total=page.total,
        limit=page.limit,
        offset=page.offset,
        has_more=page.has_more,
    )


def _upload_payload(view: UploadedAssetView) -> AssetUploadResponse:
    node = view.node
    return AssetUploadResponse(
        node_id=node.node_id,
        parent_node_id=node.parent_node_id,
        kind=cast(AssetKind, node.kind),
        name=node.name,
        size_bytes=node.size_bytes,
        media_type=node.media_type,
        created_at=node.created_at,
        updated_at=node.updated_at,
        version=AssetVersionResponse(
            version_id=view.version.version_id,
            size_bytes=view.version.size_bytes,
            sha256=view.version.sha256,
            media_type=view.version.media_type,
        ),
    )


@router.get(
    "/assets",
    response_model=AssetPageResponse,
    summary="List project assets in one folder (members only)",
)
async def list_assets(
    project_id: str,
    server: OctopServer = Depends(get_server),
    user: User = Depends(current_user),
    parent_id: str | None = Query(None, description="Folder to list; omit for the project root"),
    q: str | None = Query(
        None,
        max_length=ASSET_NAME_MAX_LENGTH,
        description="Case-insensitive substring match on the display name",
    ),
    kind: AssetKind | None = Query(None, description="Restrict to files or folders"),
    limit: int = Query(DEFAULT_PAGE_LIMIT, ge=1, le=MAX_PAGE_LIMIT),
    offset: int = Query(0, ge=0),
) -> AssetPageResponse:
    """Filtered, fully ordered ``(kind, name_key, node_id)`` page of one
    folder. Membership is re-checked per request; the hidden root folder is
    never listed as an item."""
    service = _asset_service(server)
    loop = asyncio.get_running_loop()
    page = await loop.run_in_executor(
        None,
        partial(
            service.list_assets,
            project_id,
            user_id=user.id,
            parent_id=parent_id,
            q=q,
            kind=kind,
            limit=limit,
            offset=offset,
        ),
    )
    return _page_payload(page)


@router.get(
    "/assets/usage",
    response_model=AssetUsageResponse,
    summary="Real committed asset usage for the project (members only)",
)
async def get_asset_usage(
    project_id: str,
    server: OctopServer = Depends(get_server),
    user: User = Depends(current_user),
) -> AssetUsageResponse:
    """Counts and sums only committed current file versions — never temps,
    never orphaned objects, never historical versions."""
    service = _asset_service(server)
    loop = asyncio.get_running_loop()
    usage: AssetUsageView = await loop.run_in_executor(
        None, partial(service.get_usage, project_id, user_id=user.id)
    )
    return AssetUsageResponse(file_count=usage.file_count, total_bytes=usage.total_bytes)


@router.post(
    "/assets/folders",
    status_code=201,
    response_model=AssetNodeResponse,
    summary="Create an asset folder (members of live projects only)",
)
async def create_asset_folder(
    project_id: str,
    body: CreateFolderBody,
    server: OctopServer = Depends(get_server),
    user: User = Depends(current_user),
) -> AssetNodeResponse:
    """NFC-trimmed name; same-parent ``Foo``/``foo`` collide via the DB
    unique constraint and answer 409 ``PROJECT_ASSET_NAME_CONFLICT``.
    Invalid names answer 422; archived projects answer 403."""
    service = _asset_service(server)
    loop = asyncio.get_running_loop()
    view = await loop.run_in_executor(
        None,
        partial(
            service.create_folder,
            project_id,
            user_id=user.id,
            name=body.name,
            parent_id=body.parent_id,
        ),
    )
    return _node_payload(view)


@router.post(
    "/assets/upload",
    status_code=201,
    response_model=AssetUploadResponse,
    summary="Upload one asset file (members of live projects only)",
)
async def upload_asset(
    project_id: str,
    request: Request,
    file: UploadFile = File(description="Single file to store as a project asset"),
    parent_id: str | None = Form(None, description="Target folder; omit for the project root"),
    server: OctopServer = Depends(get_server),
    user: User = Depends(current_user),
) -> AssetUploadResponse:
    """Fixed publish order: chunked temp write (cap + SHA-256) → atomic
    rename → one DB transaction → 201. Failures never leave a visible
    half-asset; oversized bodies answer 413 ``PROJECT_ASSET_TOO_LARGE``."""
    max_bytes = _max_upload_bytes(server)
    declared_total = request.headers.get("content-length")
    if (
        declared_total is not None
        and declared_total.isdigit()
        and int(declared_total) > max_bytes + _MULTIPART_OVERHEAD_ALLOWANCE
    ):
        raise _too_large(max_bytes)
    declared = _declared_size(file)
    if declared is not None and declared > max_bytes:
        raise _too_large(max_bytes)
    service = _asset_service(server)
    # Starlette has already spooled the multipart body; hand the sync file
    # object to the executor so the streaming copy never blocks the loop.
    stream = file.file
    loop = asyncio.get_running_loop()
    view = await loop.run_in_executor(
        None,
        partial(
            service.upload_file,
            project_id,
            user_id=user.id,
            filename=file.filename or "",
            stream=stream,
            max_bytes=max_bytes,
            parent_id=parent_id,
        ),
    )
    return _upload_payload(view)


@router.get(
    "/assets/{node_id}/download",
    summary="Download the committed bytes of one asset file (members only)",
)
async def download_asset(
    project_id: str,
    node_id: str,
    server: OctopServer = Depends(get_server),
    user: User = Depends(current_user),
) -> StreamingResponse:
    """Only the server-version-id-derived plain file under the private root is
    served (symlinks and escapes rejected), always as a forced attachment with
    ``application/octet-stream`` + ``nosniff``. Folder nodes, foreign nodes,
    and unknown ids answer the uniform 404."""
    service = _asset_service(server)
    loop = asyncio.get_running_loop()
    view: AssetDownloadView = await loop.run_in_executor(
        None, partial(service.prepare_download, project_id, user_id=user.id, node_id=node_id)
    )
    try:
        stream = await loop.run_in_executor(None, partial(open_regular_file_no_follow, view.path))
    except AssetStorageError as exc:
        raise OctopError(ErrorCode.NOT_FOUND, "asset not found") from exc
    if os.fstat(stream.fileno()).st_size != view.size_bytes:
        stream.close()
        raise OctopError(ErrorCode.NOT_FOUND, "asset not found")
    return StreamingResponse(
        _download_chunks(stream),
        media_type="application/octet-stream",
        headers={
            "Content-Disposition": content_disposition(view.filename, disposition="attachment"),
            "X-Content-Type-Options": "nosniff",
            "Content-Length": str(view.size_bytes),
        },
        background=BackgroundTask(stream.close),
    )
