"""Shared workspace helpers (not route handlers)."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

from harness_agent.backends.workspace import BackendWorkspace

from octop.api.common.agent import (
    assert_project_task_file_owner,
    is_project_task_file_runtime,
    require_agent_owner_row,
    require_agent_row,
)
from octop.infra.backend.project_task_file_paths import (
    normalize_project_task_io_path,
    resolve_project_task_workspace_path,
)
from octop.infra.errors import ErrorCode, OctopError

if TYPE_CHECKING:
    from harness_agent import HarnessAgent

# deepagents.backends.utils.EMPTY_CONTENT_WARNING — shown to LLM tools, not humans.
_DEEPAGENTS_EMPTY_WARNING = "System reminder: File exists but has empty contents"


def coerce_read_content(content: Any) -> str:
    """Normalize backend read payloads for the dashboard file viewer."""
    if content is None:
        return ""
    if isinstance(content, list):
        content = "\n".join(str(line) for line in content)
    text = str(content)
    if text == _DEEPAGENTS_EMPTY_WARNING:
        return ""
    return text


def require_running_agent(server: Any, agent_id: str) -> HarnessAgent:
    """Return the live harness agent or raise not-running / not-found errors."""
    assert server.app_runtime is not None
    return cast("HarnessAgent", server.app_runtime.agent_registry.get_agent(agent_id))


async def require_running_workspace(
    agent_id: str,
    *,
    user: Any,
    as_user: int | None,
    server: Any,
    owner_only: bool = False,
) -> BackendWorkspace:
    """Auth-checked :class:`BackendWorkspace` for a running agent.

    PATCH /agents triggers a background ``arebuild_agent`` that briefly
    removes the live harness handle while the DB row still says ``running``.
    Workspace file I/O does not need the compiled graph, so during that
    window we fall back to :meth:`AgentManager.workspace_for_agent`.
    Stopped / failed agents still raise — the fallback is only for the
    rebuild gap, not a way to edit a stopped workspace through this helper.
    """
    checker = require_agent_owner_row if owner_only else require_agent_row
    row = checker(agent_id, user=user, as_user=as_user, server=server)
    try:
        return require_running_agent(server, agent_id).workspace
    except OctopError as exc:
        if exc.code is not ErrorCode.AGENT_NOT_RUNNING:
            raise
        state = str(getattr(row, "last_state", "") or "").strip().lower()
        if state != "running":
            raise
        assert server.app_runtime is not None
        fallback = server.app_runtime.agent_registry.workspace_for_agent(agent_id)
        if fallback is None:
            raise
        return cast("BackendWorkspace", fallback)


async def require_agent_workspace(
    agent_id: str,
    *,
    user: Any,
    server: Any,
    owner_only: bool = False,
) -> BackendWorkspace:
    """Auth-checked workspace even when the agent is stopped.

    Used for display files (e.g. expert avatar) that must work from the
    experts list without requiring a running harness handle.
    """
    checker = require_agent_owner_row if owner_only else require_agent_row
    checker(agent_id, user=user, as_user=None, server=server)
    assert server.app_runtime is not None
    workspace = server.app_runtime.agent_registry.workspace_for_agent(agent_id)
    if workspace is None:
        raise OctopError(ErrorCode.AGENT_NOT_FOUND, f"agent {agent_id!r} not found")
    return cast("BackendWorkspace", workspace)


def workspace_api_path(raw: str) -> str:
    """Map dashboard ``/`` / ``/foo`` paths to workspace-relative fragments for I/O."""
    text = raw.strip().replace("\\", "/")
    if not text or text == "/":
        return "."
    return text.lstrip("/")


def project_task_file_owner_row(
    server: Any,
    agent_id: str,
    *,
    user: Any,
    as_user: int | None,
) -> Any | None:
    """Internal-runtime row after owner-only checks; ``None`` for ordinary agents.

    030A B4: internal project-task file runtimes are owner-only — admins and
    ``as_user`` impersonation are refused before any workspace I/O happens.
    """
    assert server.app_runtime is not None
    row = server.app_runtime.agent_registry.get_row(agent_id)
    if row is None or not is_project_task_file_runtime(row):
        return None
    assert_project_task_file_owner(row, user, as_user=as_user)
    return row


def require_project_task_file_root(server: Any, row: Any) -> Path:
    """Managed private root for an internal runtime row.

    The row's live workspace must be pinned to the managed
    ``project-task-files/{agent_id}`` directory; anything else (or an unsafe
    agent id) fails closed with 503 instead of serving host files.
    """
    try:
        root = server.services.paths.project_task_file_runtime_dir(str(row.agent_id))
    except Exception:  # noqa: BLE001 — unsafe ids / unbound paths fail closed
        raise OctopError(
            ErrorCode.PROJECT_TASK_FILES_UNAVAILABLE,
            "project-task file workspace is not available",
        ) from None
    return Path(root)


def assert_project_task_file_workspace(ws: Any, root: Path) -> None:
    """Prove the resolved workspace is exactly the managed private root."""
    try:
        ws_dir = Path(str(getattr(ws, "workspace_dir", "") or "")).resolve()
        if ws_dir != root.resolve():
            raise ValueError(ws_dir)
    except Exception:  # noqa: BLE001 — unprovable confinement is a refusal
        raise OctopError(
            ErrorCode.PROJECT_TASK_FILES_UNAVAILABLE,
            "project-task file workspace is not available",
        ) from None


def project_task_file_io_path(root: Path, raw: str, *, from_workspace: bool) -> str:
    """Strict workspace-relative fragment for internal runtimes (M0 resolver).

    Rejects host absolute POSIX/Windows/UNC paths, ``file://`` URLs, ``..``
    and encoded traversal, ``~``, NUL, and anything whose no-follow containment
    under *root* cannot be proven (symlink/junction escape). Internal runtimes
    accept workspace-relative mode only: ``from_workspace=False`` (the chat/tool
    host-absolute mode) is refused outright, never silently upgraded.
    """
    try:
        # The dashboard's single leading "/" denotes the managed root, but
        # two leading slashes (POSIX UNC) or a leading backslash (Windows UNC)
        # must be rejected before workspace_api_path erases their shape.
        if from_workspace and str(raw).strip().startswith(("//", "\\")):
            raise ValueError("UNC and Windows-absolute paths are not allowed")
        text = workspace_api_path(raw) if from_workspace else str(raw)
        rel = normalize_project_task_io_path(text, from_workspace=from_workspace)
        resolve_project_task_workspace_path(root, rel, from_workspace=from_workspace)
    except ValueError as exc:  # ProjectTaskPathError is a ValueError subclass
        raise OctopError(
            ErrorCode.FORBIDDEN,
            "path escapes the project-task file workspace",
            details={"internal": True},
        ) from exc
    return rel


def reanchor_entry_path(entry_path: str, *, parent: str) -> str:
    """Anchor a single-level listing entry under *parent* (the requested dir).

    A directory listing only carries entry names, so the dashboard must get
    ``{requested}/{name}`` back — otherwise a backend that presents longer
    paths yields keys the workspace API cannot resolve on the next request.
    """
    name = entry_path.strip().replace("\\", "/").rstrip("/").rsplit("/", 1)[-1]
    base = parent.strip().replace("\\", "/").rstrip("/")
    if base in ("", "."):
        return name
    return f"{base}/{name}" if name else base


def file_info_to_dict(info: Any) -> dict[str, Any]:
    """Coerce a ``FileInfo`` TypedDict into a JSON-friendly dict."""
    if isinstance(info, dict):
        path = info.get("path")
        is_dir = info.get("is_dir")
        size = info.get("size")
        modified_at = info.get("modified_at")
    else:
        path = getattr(info, "path", None)
        is_dir = getattr(info, "is_dir", None)
        size = getattr(info, "size", None)
        modified_at = getattr(info, "modified_at", None)
    out: dict[str, Any] = {"path": path}
    if is_dir is not None:
        out["is_dir"] = bool(is_dir)
    if size is not None:
        out["size"] = int(size)
    if modified_at is not None:
        out["modified_at"] = modified_at
    return out
