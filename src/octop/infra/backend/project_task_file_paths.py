"""Strict workspace-relative path policy for managed project-task file runtimes.

M0 scope: this module is pure and not yet wired into HTTP routes. The later
backend slice must call :func:`normalize_project_task_io_path` /
:func:`resolve_project_task_workspace_path` at every workspace, attachment,
artifact, media and preview entry before touching ``BackendWorkspace``.

Installed ``BackendWorkspace`` falls back to raw host-absolute paths on POSIX
(``materialize_local``), so host absolute paths, drive paths, UNC paths,
``file://`` URLs, ``~`` and ``..`` must be rejected here rather than papered
over by the backend.
"""

from __future__ import annotations

import re
from pathlib import Path

_WINDOWS_DRIVE_RE = re.compile(r"^[A-Za-z]:")


class ProjectTaskPathError(ValueError):
    """A path rejected for a managed project-task file runtime."""


def normalize_project_task_io_path(path: str, *, from_workspace: bool = True) -> str:
    """Map *path* to a safe workspace-relative POSIX fragment.

    Raises :class:`ProjectTaskPathError` for ``file://`` URLs, host-absolute
    POSIX paths, Windows drive or UNC paths, ``~``, ``..`` traversal, NUL
    bytes, non-string input and ``from_workspace=False`` (internal runtimes
    never accept the chat/tool host-absolute mode).
    """
    if not from_workspace:
        raise ProjectTaskPathError(
            "internal project task runtimes accept workspace-relative paths only",
        )
    if not isinstance(path, str):
        raise ProjectTaskPathError("path must be a string")
    raw = path.strip()
    if "\x00" in raw:
        raise ProjectTaskPathError("path must not contain NUL bytes")
    if raw.startswith("file://"):
        raise ProjectTaskPathError("file:// URLs are not allowed")
    if raw.startswith(("/", "\\", "~")):
        raise ProjectTaskPathError("host-absolute and home paths are not allowed")
    if _WINDOWS_DRIVE_RE.match(raw):
        raise ProjectTaskPathError("Windows drive paths are not allowed")
    normalized = raw.replace("\\", "/")
    parts = [part for part in normalized.split("/") if part not in ("", ".")]
    if any(part == ".." for part in parts):
        raise ProjectTaskPathError("path traversal is not allowed")
    if not parts:
        return "."
    return "/".join(parts)


def resolve_project_task_workspace_path(
    root: str | Path,
    path: str,
    *,
    from_workspace: bool = True,
) -> Path:
    """Resolve *path* under the real managed *root*, rejecting escapes.

    The resolved target (including symlink/junction expansion) must stay under
    the resolved root. Raises :class:`ProjectTaskPathError` otherwise.
    """
    relative = normalize_project_task_io_path(path, from_workspace=from_workspace)
    root_path = Path(root).expanduser().resolve()
    target = (root_path / relative).resolve()
    try:
        target.relative_to(root_path)
    except ValueError as exc:
        raise ProjectTaskPathError(
            f"path {path!r} resolves outside the managed root {root_path}",
        ) from exc
    return target


__all__ = [
    "ProjectTaskPathError",
    "normalize_project_task_io_path",
    "resolve_project_task_workspace_path",
]
