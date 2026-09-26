"""Workspace router — read/write into a running agent's workspace."""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any, Literal

from fastapi import APIRouter, Depends, File, Query, UploadFile
from fastapi.responses import Response, StreamingResponse
from harness_agent.backends.utils import BackendOperationNotSupportedError
from pydantic import BaseModel

from octop.api.common.agent import is_project_task_file_runtime, refuse_project_task_file_runtime
from octop.api.common.agent_workspace import resolve_agent_workspace_dir
from octop.api.common.content_disposition import content_disposition
from octop.api.common.workspace import (
    assert_project_task_file_workspace,
    coerce_read_content,
    file_info_to_dict,
    project_task_file_io_path,
    project_task_file_owner_row,
    reanchor_entry_path,
    require_project_task_file_root,
    require_running_workspace,
    workspace_api_path,
)
from octop.api.deps import current_user, get_server
from octop.infra.backup.workspace_archive import export_workspace_zip, import_workspace_zip
from octop.infra.errors import ErrorCode, OctopError
from octop.infra.gateway.media.backend_files import (
    backend_workspace_path,
    is_allowed_host_download_abs_path,
    is_host_absolute_path,
    resolve_preview_payload,
)
from octop.infra.utils.doc_edit import DocConverter, get_doc_converter

logger = logging.getLogger(__name__)

_PROTECTED_PREFIX = "_builtin_skills"


def _refuse_if_internal_runtime(server: Any, agent_id: str) -> None:
    """Deny workspace routes that must never touch an internal runtime.

    030A B4: directory create/delete/move and zip archive import/export are
    outside the owner allowlist for ``runtime_kind='project_task_files'`` —
    refused for everyone, including the owner and admins (defense in depth
    behind the HTTP gate middleware).
    """
    assert server.app_runtime is not None
    row = server.app_runtime.agent_registry.get_row(agent_id)
    if row is not None and is_project_task_file_runtime(row):
        refuse_project_task_file_runtime()


async def _file_workspace(
    server: Any,
    agent_id: str,
    *,
    user: Any,
    as_user: int | None,
    owner_only: bool,
) -> tuple[Any, Path | None]:
    """``(workspace, managed root | None)`` — internal runtimes owner-only + pinned.

    For ordinary agents the second element is ``None`` and behavior is
    unchanged. For internal project-task file runtimes the caller must be the
    owner (no admin bypass, no ``as_user``) and the live workspace must resolve
    to exactly the managed private root.
    """
    internal_row = project_task_file_owner_row(server, agent_id, user=user, as_user=as_user)
    ws = await require_running_workspace(
        agent_id, user=user, as_user=as_user, server=server, owner_only=owner_only
    )
    if internal_row is None:
        return ws, None
    root = require_project_task_file_root(server, internal_row)
    assert_project_task_file_workspace(ws, root)
    return ws, root


def _io_path(root: Path | None, path: str, *, from_workspace: bool) -> str:
    """Strict managed-root fragment for internal runtimes; legacy mapping otherwise."""
    if root is not None:
        return project_task_file_io_path(root, path, from_workspace=from_workspace)
    return _workspace_io_path(path, from_workspace=from_workspace)


def _internal_preview_source(root: Path, source: str) -> str:
    """Rewrite *source* to a proven managed-relative fragment, or refuse.

    Host-absolute paths and ``file://`` URLs are refused even when they point
    inside the managed root: only an explicitly managed-relative source is
    accepted. The returned fragment is always relative, so
    ``resolve_preview_payload`` can only take its workspace-download branch —
    never the host TEMP / screenshots / raw host-file fallbacks.
    """
    raw = source.strip()
    if is_host_absolute_path(raw):
        raise OctopError(
            ErrorCode.FORBIDDEN,
            "path escapes the project-task file workspace",
            details={"internal": True},
        )
    return project_task_file_io_path(root, raw, from_workspace=True)


def _assert_workspace_mutable(path: str) -> str:
    """Mutating ops always treat paths as workspace-relative (``from_workspace=true``)."""
    rel = _workspace_io_path(path, from_workspace=True)
    if rel == ".":
        raise OctopError(ErrorCode.FORBIDDEN, "cannot modify workspace root")
    posix = rel.replace("\\", "/").strip("/")
    if (
        posix == _PROTECTED_PREFIX
        or posix.startswith(f"{_PROTECTED_PREFIX}/")
        or posix == f".octop/{_PROTECTED_PREFIX}"
        or posix.startswith(f".octop/{_PROTECTED_PREFIX}/")
    ):
        raise OctopError(ErrorCode.FORBIDDEN, f"cannot modify {_PROTECTED_PREFIX!r} paths")
    return rel


def _map_workspace_fs_error(exc: Exception, *, operation: str, path: str) -> OctopError:
    if isinstance(exc, BackendOperationNotSupportedError):
        return OctopError(ErrorCode.WORKSPACE_OP_UNSUPPORTED, str(exc))
    if isinstance(exc, FileNotFoundError):
        return OctopError(ErrorCode.NOT_FOUND, f"cannot {operation} {path!r}: not found")
    if isinstance(exc, FileExistsError):
        return OctopError(ErrorCode.SLASH_BAD_ARGS, str(exc))
    if isinstance(exc, PermissionError):
        return OctopError(ErrorCode.FORBIDDEN, str(exc))
    if isinstance(exc, ValueError):
        return OctopError(ErrorCode.SLASH_BAD_ARGS, str(exc))
    return OctopError(ErrorCode.INTERNAL_ERROR, f"cannot {operation} {path!r}: {exc}")


def _ensure_editable_doc(path: str) -> DocConverter:
    """Return the registered converter for *path* or reject with a 400."""
    converter = get_doc_converter(path)
    if converter is None:
        raise OctopError(
            ErrorCode.SLASH_BAD_ARGS,
            f"unsupported editable document {path!r}",
        )
    return converter


def _agent_id_from_media_source(source: str) -> str | None:
    match = re.search(r"/agents/([A-Z0-9]+)/", source, re.IGNORECASE)
    return match.group(1) if match else None


def _workspace_io_path(path: str, *, from_workspace: bool = False) -> str:
    """Resolve an API path for ``BackendWorkspace``.

    ``file://`` is always a host absolute path.

    When ``from_workspace`` is true (workspace UI): leading ``/`` is relative to
    the agent workspace dir (``/logo.png`` → ``logo.png``).

    When false (default, chat/tool downloads): leading ``/`` is a host
    filesystem absolute (``/Users/…``, ``/root/…``). Paths without a leading
    ``/`` stay workspace-relative (``outbound/a.pptx``).
    """
    raw = path.strip()
    if raw.startswith("file://"):
        resolved = backend_workspace_path(raw)
        if resolved is None:
            raise OctopError(ErrorCode.NOT_FOUND, f"cannot resolve {path!r}")
        return resolved
    if from_workspace:
        return workspace_api_path(raw)
    if raw.startswith("/") or (len(raw) >= 2 and raw[1] == ":") or raw.startswith("\\\\"):
        return raw
    return workspace_api_path(raw)


_FROM_WORKSPACE_DESC = (
    "When true, leading '/' paths are workspace-relative (workspace UI). "
    "When false (default), leading '/' is host-absolute."
)


router = APIRouter()


@router.get("/agents/{agent_id}/workspace/tree")
async def list_tree(
    agent_id: str,
    path: str = "/",
    from_workspace: bool = Query(default=False, description=_FROM_WORKSPACE_DESC),
    as_user: int | None = None,
    user: Any = Depends(current_user),
    server: Any = Depends(get_server),
) -> list[dict[str, Any]]:
    """Single-level directory listing under ``path`` (agent must be running)."""
    ws, root = await _file_workspace(server, agent_id, user=user, as_user=as_user, owner_only=True)
    io_path = _io_path(root, path, from_workspace=from_workspace)
    result = await ws.als(io_path)
    if result is None:
        raise OctopError(ErrorCode.NOT_FOUND, f"cannot list {path!r}")
    entries = getattr(result, "entries", None) or []
    rows = [file_info_to_dict(f) for f in entries]
    if not is_host_absolute_path(io_path):
        for row in rows:
            row["path"] = reanchor_entry_path(str(row.get("path") or ""), parent=io_path)
    return rows


class WriteFileBody(BaseModel):
    content: str
    """UTF-8 text content. Use ``/upload`` for binary."""


class WriteDocBody(BaseModel):
    content: str
    """Markdown content to convert back into the document format."""


@router.get("/agents/{agent_id}/workspace/file")
async def read_file(
    agent_id: str,
    path: str,
    from_workspace: bool = Query(default=False, description=_FROM_WORKSPACE_DESC),
    as_user: int | None = None,
    user: Any = Depends(current_user),
    server: Any = Depends(get_server),
) -> dict[str, Any]:
    """Read a UTF-8 text file."""
    ws, root = await _file_workspace(server, agent_id, user=user, as_user=as_user, owner_only=False)
    content = await ws.aread_text(_io_path(root, path, from_workspace=from_workspace))
    if content is None:
        raise OctopError(ErrorCode.NOT_FOUND, f"cannot read {path!r}")
    return {"path": path, "content": coerce_read_content(content)}


@router.put("/agents/{agent_id}/workspace/file")
async def write_file(
    agent_id: str,
    body: WriteFileBody,
    path: str,
    from_workspace: bool = Query(default=False, description=_FROM_WORKSPACE_DESC),
    as_user: int | None = None,
    user: Any = Depends(current_user),
    server: Any = Depends(get_server),
) -> dict[str, Any]:
    """Overwrite ``path`` with ``body.content`` (text)."""
    ws, root = await _file_workspace(server, agent_id, user=user, as_user=as_user, owner_only=True)
    # Resolve/authorize before building content or touching the backend: a path
    # refusal (403, e.g. symlink escape) must never be remapped to 404.
    io_path = _io_path(root, path, from_workspace=from_workspace)
    converter = get_doc_converter(path)
    if converter is not None:
        # Editable-document paths are always stored as the binary document
        # format. This matters for workspace "new file": an empty .docx created
        # here must be a valid package so its preview (and edit round-trip)
        # works immediately instead of showing a 0-byte file.
        try:
            data = converter.from_markdown(body.content)
        except Exception as exc:
            raise OctopError(
                ErrorCode.SLASH_BAD_ARGS,
                f"cannot build document for {path!r}: {exc}",
            ) from exc
    else:
        data = body.content.encode("utf-8")
    try:
        await ws.aupload_bytes(io_path, data)
    except Exception as exc:
        raise OctopError(ErrorCode.NOT_FOUND, f"cannot write {path!r}: {exc}") from exc
    return {"path": path, "size": len(data)}


class MoveFileBody(BaseModel):
    destination: str
    """Workspace-relative destination path (e.g. ``/sub/file.txt``)."""


@router.post(
    "/agents/{agent_id}/workspace/mkdir",
    status_code=201,
    summary="Create workspace directory",
)
async def mkdir_workspace_dir(
    agent_id: str,
    path: str,
    from_workspace: bool = Query(
        default=True,
        description="Mutating endpoints always treat paths as workspace-relative.",
    ),
    as_user: int | None = None,
    user: Any = Depends(current_user),
    server: Any = Depends(get_server),
) -> dict[str, Any]:
    """Create a directory (and parents) under the agent workspace."""
    _refuse_if_internal_runtime(server, agent_id)
    _ = from_workspace  # API surface; mutations always use workspace-relative paths.
    rel = _assert_workspace_mutable(path)
    ws = await require_running_workspace(
        agent_id, user=user, as_user=as_user, server=server, owner_only=True
    )
    try:
        await ws.amkdir(rel)
    except Exception as exc:
        raise _map_workspace_fs_error(exc, operation="mkdir", path=path) from exc
    api_path = path if path.startswith("/") else f"/{path}"
    return {"path": api_path, "is_dir": True}


@router.delete(
    "/agents/{agent_id}/workspace/file",
    status_code=204,
    summary="Delete workspace file or directory",
)
async def delete_workspace_file(
    agent_id: str,
    path: str,
    from_workspace: bool = Query(
        default=True,
        description="Mutating endpoints always treat paths as workspace-relative.",
    ),
    as_user: int | None = None,
    user: Any = Depends(current_user),
    server: Any = Depends(get_server),
) -> Response:
    """Remove a file or directory tree from the agent workspace."""
    _refuse_if_internal_runtime(server, agent_id)
    _ = from_workspace
    rel = _assert_workspace_mutable(path)
    ws = await require_running_workspace(
        agent_id, user=user, as_user=as_user, server=server, owner_only=True
    )
    try:
        await ws.adelete(rel)
    except Exception as exc:
        raise _map_workspace_fs_error(exc, operation="delete", path=path) from exc
    return Response(status_code=204)


@router.post(
    "/agents/{agent_id}/workspace/move",
    summary="Move or rename a workspace file or directory",
)
async def move_workspace_file(
    agent_id: str,
    body: MoveFileBody,
    path: str,
    from_workspace: bool = Query(
        default=True,
        description="Mutating endpoints always treat paths as workspace-relative.",
    ),
    as_user: int | None = None,
    user: Any = Depends(current_user),
    server: Any = Depends(get_server),
) -> dict[str, Any]:
    """Move ``path`` to ``body.destination`` (rename when the parent directory is unchanged)."""
    _refuse_if_internal_runtime(server, agent_id)
    _ = from_workspace
    src = _assert_workspace_mutable(path)
    dest = _assert_workspace_mutable(body.destination)
    ws = await require_running_workspace(
        agent_id, user=user, as_user=as_user, server=server, owner_only=True
    )
    try:
        await ws.amove(src, dest)
    except Exception as exc:
        raise _map_workspace_fs_error(exc, operation="move", path=path) from exc
    dest_api = body.destination if body.destination.startswith("/") else f"/{body.destination}"
    return {"path": dest_api}


@router.post("/agents/{agent_id}/workspace/upload")
async def upload_file(
    agent_id: str,
    file: UploadFile = File(...),  # noqa: B008
    path: str | None = Query(default=None),
    from_workspace: bool = Query(default=False, description=_FROM_WORKSPACE_DESC),
    as_user: int | None = None,
    user: Any = Depends(current_user),
    server: Any = Depends(get_server),
) -> dict[str, Any]:
    """Upload a binary file via multipart ``file=@...``."""
    ws, root = await _file_workspace(server, agent_id, user=user, as_user=as_user, owner_only=True)
    target = path or f"/{file.filename or 'upload.bin'}"
    # Internal runtimes: the multipart filename is client-controlled, so the
    # strict resolver must reject traversal / host-absolute shapes from it too
    # — and must run before any bytes are buffered or written. The request's
    # own ``from_workspace`` is honored: false is refused, never upgraded.
    io_target = _io_path(root, target, from_workspace=from_workspace)
    data = await file.read()
    try:
        await ws.aupload_bytes(io_target, data)
    except Exception as exc:
        raise OctopError(ErrorCode.NOT_FOUND, f"cannot upload to {target!r}: {exc}") from exc
    return {"path": target, "size": len(data)}


@router.get("/agents/{agent_id}/workspace/download")
async def download_file(
    agent_id: str,
    path: str,
    from_workspace: bool = Query(default=False, description=_FROM_WORKSPACE_DESC),
    as_user: int | None = None,
    user: Any = Depends(current_user),
    server: Any = Depends(get_server),
) -> StreamingResponse:
    """Stream ``path`` back as application/octet-stream.

    See ``from_workspace``: workspace UI uses true; chat/tool downloads use false.
    ``file://`` and other host-absolute paths are allowed for agent/OS tool
    outputs (Desktop, ``~/.octop/agents/…``, workspace tree) but denied for
    sensitive system roots (``/etc``, ``.harness-browser``, Windows system dirs).
    """
    ws, root = await _file_workspace(server, agent_id, user=user, as_user=as_user, owner_only=False)
    io_path = _io_path(root, path, from_workspace=from_workspace)
    # Internal runtimes never take the host-absolute branch: _io_path already
    # proved the fragment is workspace-relative inside the managed root.
    if (
        root is None
        and is_host_absolute_path(io_path)
        and not is_allowed_host_download_abs_path(
            io_path,
            workspace=ws.workspace_dir,
        )
    ):
        raise OctopError(ErrorCode.FORBIDDEN, f"cannot download {path!r}: path not allowed")

    try:
        file_blob = await ws.adownload_bytes(io_path)
    except PermissionError as exc:
        raise OctopError(ErrorCode.NOT_FOUND, f"cannot download {path!r}") from exc
    if file_blob is None:
        raise OctopError(ErrorCode.NOT_FOUND, f"cannot download {path!r}") from None

    fname = io_path.rsplit("/", 1)[-1] or "download.bin"
    return StreamingResponse(
        iter([file_blob]),
        media_type="application/octet-stream",
        headers={"Content-Disposition": content_disposition(fname)},
    )


@router.get("/agents/{agent_id}/workspace/doc")
async def read_doc(
    agent_id: str,
    path: str,
    from_workspace: bool = Query(default=False, description=_FROM_WORKSPACE_DESC),
    as_user: int | None = None,
    user: Any = Depends(current_user),
    server: Any = Depends(get_server),
) -> dict[str, Any]:
    """Read an editable document (e.g. ``.docx``) as Markdown for online editing."""
    converter = _ensure_editable_doc(path)
    ws, root = await _file_workspace(server, agent_id, user=user, as_user=as_user, owner_only=False)
    io_path = _io_path(root, path, from_workspace=from_workspace)
    try:
        blob = await ws.adownload_bytes(io_path)
    except PermissionError as exc:
        raise OctopError(ErrorCode.NOT_FOUND, f"cannot read {path!r}") from exc
    if blob is None:
        raise OctopError(ErrorCode.NOT_FOUND, f"cannot read {path!r}")
    try:
        content = converter.to_markdown(blob)
    except Exception as exc:
        raise OctopError(ErrorCode.SLASH_BAD_ARGS, f"cannot parse {path!r}: {exc}") from exc
    return {"path": path, "content": content}


@router.put("/agents/{agent_id}/workspace/doc")
async def write_doc(
    agent_id: str,
    body: WriteDocBody,
    path: str,
    from_workspace: bool = Query(
        default=True,
        description="Mutating endpoints always treat paths as workspace-relative.",
    ),
    as_user: int | None = None,
    user: Any = Depends(current_user),
    server: Any = Depends(get_server),
) -> dict[str, Any]:
    """Convert Markdown *content* back to the document format and overwrite *path*."""
    # Resolve/authorize before the converter and any backend I/O: the root
    # check (403) must win over an unsupported-extension 400. Ordinary
    # mutations always use workspace-relative paths; internal runtimes still
    # honor the request flag so ``from_workspace=false`` is refused.
    ws, root = await _file_workspace(server, agent_id, user=user, as_user=as_user, owner_only=False)
    if root is not None:
        rel = project_task_file_io_path(root, path, from_workspace=from_workspace)
        if rel == ".":
            raise OctopError(ErrorCode.FORBIDDEN, "cannot modify workspace root")
    else:
        rel = _assert_workspace_mutable(path)
    converter = _ensure_editable_doc(path)
    try:
        data = converter.from_markdown(body.content)
    except Exception as exc:
        raise OctopError(
            ErrorCode.SLASH_BAD_ARGS,
            f"cannot build document for {path!r}: {exc}",
        ) from exc
    try:
        await ws.aupload_bytes(rel, data)
    except Exception as exc:
        raise _map_workspace_fs_error(exc, operation="write", path=path) from exc
    return {"path": path, "size": len(data)}


@router.get(
    "/agents/{agent_id}/media/preview",
    summary="Preview image or video",
    response_class=StreamingResponse,
)
async def preview_media(
    agent_id: str,
    source: str = Query(..., description="``file://`` URL or workspace-relative path"),
    mime_type: str | None = Query(default=None, alias="mime_type"),
    as_user: int | None = None,
    user: Any = Depends(current_user),
    server: Any = Depends(get_server),
) -> StreamingResponse:
    """Stream an image or video inline for dashboard tool-result previews."""
    path_agent = _agent_id_from_media_source(source)
    effective_agent = path_agent or agent_id
    # 030A B4: gate on the agent the source actually resolves to, so an
    # internal runtime cannot be previewed through another agent's URL either.
    internal_row = project_task_file_owner_row(server, effective_agent, user=user, as_user=as_user)
    ws = await require_running_workspace(effective_agent, user=user, as_user=as_user, server=server)
    preview_source = source
    if internal_row is not None:
        root = require_project_task_file_root(server, internal_row)
        assert_project_task_file_workspace(ws, root)
        # Only a proven workspace-relative fragment reaches the resolver, so
        # its host TEMP / screenshots / raw host-file fallbacks can never run.
        preview_source = _internal_preview_source(root, source)
    payload = await resolve_preview_payload(
        source=preview_source,
        workspace=ws,
        mime_hint=mime_type or "",
    )
    if payload is None:
        raise OctopError(ErrorCode.NOT_FOUND, "preview not available for this source")
    data, mime = payload

    return StreamingResponse(
        iter([data]),
        media_type=mime,
        headers={"Content-Disposition": "inline"},
    )


@router.get("/agents/{agent_id}/workspace/glob")
async def glob_files(
    agent_id: str,
    pattern: str,
    path: str = "/",
    from_workspace: bool = Query(default=False, description=_FROM_WORKSPACE_DESC),
    as_user: int | None = None,
    user: Any = Depends(current_user),
    server: Any = Depends(get_server),
) -> list[dict[str, Any]]:
    ws, managed = await _file_workspace(
        server, agent_id, user=user, as_user=as_user, owner_only=True
    )
    root = _io_path(managed, path, from_workspace=from_workspace)
    if managed is not None:
        # The glob pattern is a path shape too: refuse traversal / host-absolute
        # patterns via the same strict resolver (globs have no containment proof
        # inside the backend glob itself).
        pattern = project_task_file_io_path(managed, pattern, from_workspace=True)
    if pattern in ("**/*.md", "*.md") and root == ".":
        ls_result = await ws.als(".")
        if ls_result is None:
            raise OctopError(ErrorCode.NOT_FOUND, "glob failed")
        entries = getattr(ls_result, "entries", None) or []
        matches = []
        for f in entries:
            row = file_info_to_dict(f)
            if row.get("is_dir"):
                continue
            entry_path = str(row.get("path") or "")
            if entry_path.endswith(".md"):
                matches.append(row)
        return matches
    glob_result = await ws.aglob(pattern, root)
    if glob_result is None:
        raise OctopError(ErrorCode.NOT_FOUND, "glob failed")
    matches = getattr(glob_result, "matches", None) or []
    return [file_info_to_dict(item) for item in matches]


@router.get("/agents/{agent_id}/workspace/grep")
async def grep_files(
    agent_id: str,
    pattern: str,
    path: str = "/",
    from_workspace: bool = Query(default=False, description=_FROM_WORKSPACE_DESC),
    as_user: int | None = None,
    user: Any = Depends(current_user),
    server: Any = Depends(get_server),
) -> list[dict[str, Any]]:
    ws, managed = await _file_workspace(
        server, agent_id, user=user, as_user=as_user, owner_only=True
    )
    io_path = _io_path(managed, path, from_workspace=from_workspace)
    result = await ws.agrep(pattern, io_path)
    if result is None:
        raise OctopError(ErrorCode.NOT_FOUND, "grep failed")
    matches = getattr(result, "matches", None) or []
    return [dict(m) for m in matches]


_MAX_WORKSPACE_ARCHIVE_BYTES = 200 * 1024 * 1024


@router.get(
    "/agents/{agent_id}/workspace/archive",
    summary="Download workspace as zip",
    response_class=StreamingResponse,
)
async def export_workspace_archive(
    agent_id: str,
    as_user: int | None = None,
    user: Any = Depends(current_user),
    server: Any = Depends(get_server),
) -> StreamingResponse:
    """Pack workspace files into a zip archive."""
    _refuse_if_internal_runtime(server, agent_id)
    ws = await require_running_workspace(
        agent_id, user=user, as_user=as_user, server=server, owner_only=True
    )
    data = await export_workspace_zip(ws)
    filename = f"workspace-{agent_id}.zip"
    return StreamingResponse(
        iter([data]),
        media_type="application/zip",
        headers={"Content-Disposition": content_disposition(filename)},
    )


@router.post(
    "/agents/{agent_id}/workspace/archive",
    summary="Import workspace zip",
)
async def import_workspace_archive(
    agent_id: str,
    file: UploadFile = File(...),  # noqa: B008
    mode: Literal["merge", "replace"] = Query(default="merge"),
    as_user: int | None = None,
    user: Any = Depends(current_user),
    server: Any = Depends(get_server),
) -> dict[str, Any]:
    """Import a zip archive into the workspace (merge or replace)."""
    _refuse_if_internal_runtime(server, agent_id)
    raw = await file.read()
    if len(raw) > _MAX_WORKSPACE_ARCHIVE_BYTES:
        raise OctopError(ErrorCode.SLASH_BAD_ARGS, "workspace archive too large (max 200MB)")
    if not raw:
        raise OctopError(ErrorCode.SLASH_BAD_ARGS, "empty archive")

    ws = await require_running_workspace(
        agent_id, user=user, as_user=as_user, server=server, owner_only=True
    )
    local_ws = resolve_agent_workspace_dir(server, agent_id)
    result = await import_workspace_zip(
        ws,
        raw,
        mode=mode,
        local_workspace_dir=local_ws,
    )
    return {"ok": True, **result}
