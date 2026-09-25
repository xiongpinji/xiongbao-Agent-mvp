"""M0 evidence for strict project-task file paths and backend containment.

``normalize_project_task_io_path`` is the pure future HTTP helper: internal
runtimes accept only workspace-relative paths. ``BackendWorkspace`` on POSIX has
a host-absolute materialize fallback, so the helper must reject those paths
before any later HTTP wiring reaches the backend.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest
from deepagents.backends.filesystem import FilesystemBackend
from harness_agent.backends.workspace import BackendWorkspace

from octop.infra.backend.project_task_file_paths import (
    ProjectTaskPathError,
    normalize_project_task_io_path,
    resolve_project_task_workspace_path,
)

posix_only = pytest.mark.skipif(os.name != "posix", reason="POSIX path and symlink semantics")
windows_only = pytest.mark.skipif(os.name != "nt", reason="Windows junction semantics")

CANARY_TEXT = "CANARY-SECRET"

REJECTED_PATHS = (
    "file:///etc/passwd",
    "file://C:/Windows/win.ini",
    "/etc/passwd",
    "/",
    "\\Windows\\System32\\drivers\\etc\\hosts",
    "\\\\server\\share\\secret.txt",
    "C:\\Windows\\win.ini",
    "C:/Windows/win.ini",
    "c:secret.txt",
    "..",
    "../secret.txt",
    "a/../secret.txt",
    "a\\..\\secret.txt",
    "a/../../secret.txt",
    "~",
    "~/secret.txt",
    "\x00",
    "a\x00b",
)


def _outside_canary(tmp_path: Path) -> Path:
    outside = tmp_path / "outside"
    outside.mkdir()
    canary = outside / "canary.txt"
    canary.write_text(CANARY_TEXT, encoding="utf-8")
    return canary


def _assert_no_canary(content: object) -> None:
    assert CANARY_TEXT not in str(content)


def _try_create_junction(link: Path, target: Path) -> bool:
    result = subprocess.run(
        ["cmd", "/c", "mklink", "/J", str(link), str(target)],
        capture_output=True,
        text=True,
        check=False,
    )
    return result.returncode == 0 and link.exists()


@pytest.mark.parametrize("raw", REJECTED_PATHS)
def test_reject_non_workspace_relative_inputs(raw: str) -> None:
    with pytest.raises(ProjectTaskPathError):
        normalize_project_task_io_path(raw, from_workspace=True)


def test_from_workspace_false_is_rejected_for_internal_runtimes() -> None:
    with pytest.raises(ProjectTaskPathError):
        normalize_project_task_io_path("notes.txt", from_workspace=False)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("notes.txt", "notes.txt"),
        ("./notes.txt", "notes.txt"),
        ("a\\b.txt", "a/b.txt"),
        ("a//b.txt", "a/b.txt"),
        ("a/./b.txt", "a/b.txt"),
        ("sub dir/file name.txt", "sub dir/file name.txt"),
        ("", "."),
        (".", "."),
        ("./", "."),
    ],
)
def test_maps_safe_workspace_relative_paths(raw: str, expected: str) -> None:
    assert normalize_project_task_io_path(raw, from_workspace=True) == expected


def test_resolve_stays_under_real_managed_root(tmp_path: Path) -> None:
    root = tmp_path / "managed"
    root.mkdir()
    resolved = resolve_project_task_workspace_path(root, "sub/dir/file.txt")
    assert resolved == (root / "sub" / "dir" / "file.txt").resolve()
    assert resolve_project_task_workspace_path(root, "") == root.resolve()
    resolved.relative_to(root.resolve())


@posix_only
def test_resolve_rejects_posix_symlink_escape(tmp_path: Path) -> None:
    root = tmp_path / "managed"
    root.mkdir()
    canary = _outside_canary(tmp_path)
    (root / "link").symlink_to(canary.parent, target_is_directory=True)
    (root / "escape.txt").symlink_to(canary)

    with pytest.raises(ProjectTaskPathError):
        resolve_project_task_workspace_path(root, "link/canary.txt")
    with pytest.raises(ProjectTaskPathError):
        resolve_project_task_workspace_path(root, "escape.txt")


@windows_only
def test_resolve_rejects_windows_junction_escape(tmp_path: Path) -> None:
    root = tmp_path / "managed"
    root.mkdir()
    canary = _outside_canary(tmp_path)
    link = root / "junction"
    if not _try_create_junction(link, canary.parent):
        pytest.skip("junction creation unavailable on this machine (privilege or policy)")

    with pytest.raises(ProjectTaskPathError):
        resolve_project_task_workspace_path(root, "junction/canary.txt")


def test_filesystem_backend_in_root_read_write_works(tmp_path: Path) -> None:
    root = tmp_path / "managed"
    root.mkdir()
    backend = FilesystemBackend(root_dir=root, virtual_mode=True)

    assert backend.write("notes.txt", "hello").error is None
    result = backend.read("notes.txt")
    assert result.error is None
    assert result.file_data is not None
    assert result.file_data["content"] == "hello"


def test_filesystem_backend_blocks_traversal(tmp_path: Path) -> None:
    root = tmp_path / "managed"
    root.mkdir()
    _outside_canary(tmp_path)
    backend = FilesystemBackend(root_dir=root, virtual_mode=True)

    with pytest.raises(ValueError):
        backend.read("../outside/canary.txt")
    with pytest.raises(ValueError):
        backend.write("../outside/evil.txt", "evil")
    assert not (tmp_path / "outside" / "evil.txt").exists()


def test_filesystem_backend_blocks_host_absolute_read(tmp_path: Path) -> None:
    root = tmp_path / "managed"
    root.mkdir()
    canary = _outside_canary(tmp_path)
    backend = FilesystemBackend(root_dir=root, virtual_mode=True)

    try:
        result = backend.read(str(canary))
    except (ValueError, PermissionError):
        return
    assert result.error is not None or result.file_data is None
    if result.file_data is not None:
        _assert_no_canary(result.file_data.get("content"))


@posix_only
def test_filesystem_backend_blocks_posix_symlink_escape(tmp_path: Path) -> None:
    root = tmp_path / "managed"
    root.mkdir()
    canary = _outside_canary(tmp_path)
    (root / "link").symlink_to(canary.parent, target_is_directory=True)
    (root / "escape.txt").symlink_to(canary)
    backend = FilesystemBackend(root_dir=root, virtual_mode=True)

    with pytest.raises(ValueError):
        backend.read("link/canary.txt")
    with pytest.raises(ValueError):
        backend.read("escape.txt")


@windows_only
def test_filesystem_backend_blocks_windows_junction_escape(tmp_path: Path) -> None:
    root = tmp_path / "managed"
    root.mkdir()
    canary = _outside_canary(tmp_path)
    link = root / "junction"
    if not _try_create_junction(link, canary.parent):
        pytest.skip("junction creation unavailable on this machine (privilege or policy)")
    backend = FilesystemBackend(root_dir=root, virtual_mode=True)

    try:
        result = backend.read("junction/canary.txt")
    except (ValueError, PermissionError):
        return
    assert result.error is not None or result.file_data is None
    if result.file_data is not None:
        _assert_no_canary(result.file_data.get("content"))


def test_backend_workspace_relative_traversal_is_rejected(tmp_path: Path) -> None:
    root = tmp_path / "managed"
    root.mkdir()
    _outside_canary(tmp_path)
    backend = FilesystemBackend(root_dir=root, virtual_mode=True)
    workspace = BackendWorkspace(backend, root)

    with pytest.raises(PermissionError):
        workspace.read_text("../outside/canary.txt")
    with pytest.raises(PermissionError):
        workspace.write_text("../outside/evil.txt", "evil")
    assert not (tmp_path / "outside" / "evil.txt").exists()


@posix_only
def test_backend_workspace_host_absolute_fallback_is_blocked_by_helper(tmp_path: Path) -> None:
    root = tmp_path / "managed"
    root.mkdir()
    canary = _outside_canary(tmp_path)
    backend = FilesystemBackend(root_dir=root, virtual_mode=True)
    workspace = BackendWorkspace(backend, root)

    assert workspace.read_text(str(canary)) == CANARY_TEXT
    assert workspace.exists(str(canary)) is True

    with pytest.raises(ProjectTaskPathError):
        normalize_project_task_io_path(str(canary), from_workspace=True)
    with pytest.raises(ProjectTaskPathError):
        resolve_project_task_workspace_path(root, str(canary))


@posix_only
def test_backend_workspace_symlink_escape_is_rejected(tmp_path: Path) -> None:
    root = tmp_path / "managed"
    root.mkdir()
    canary = _outside_canary(tmp_path)
    (root / "link").symlink_to(canary.parent, target_is_directory=True)
    (root / "escape.txt").symlink_to(canary)
    backend = FilesystemBackend(root_dir=root, virtual_mode=True)
    workspace = BackendWorkspace(backend, root)

    with pytest.raises(PermissionError):
        workspace.read_text("link/canary.txt")
    with pytest.raises(PermissionError):
        workspace.read_text("escape.txt")
