"""M0 evidence for strict project-task file paths and backend containment.

``normalize_project_task_io_path`` is the pure future HTTP helper: internal
runtimes accept only workspace-relative paths. ``BackendWorkspace`` on POSIX has
a host-absolute materialize fallback, so the helper must reject those paths
before any later HTTP wiring reaches the backend.
"""

from __future__ import annotations

import os
import socket
import subprocess
from pathlib import Path

import pytest
from deepagents.backends.filesystem import FilesystemBackend
from harness_agent.backends.workspace import BackendWorkspace

from octop.infra.backend.project_task_file_paths import (
    PROJECT_TASK_LISTING_MAX_ENTRIES,
    ProjectTaskListingOverflowError,
    ProjectTaskPathError,
    assert_listing_subtree_reparse_free,
    assert_plain_components,
    assert_plain_final,
    assert_plain_search_base,
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


# ---------------------------------------------------------------------------
# 030A B+ RD-1: S1 chain walk, S2 final-component types, S7 hard links,
# S3 bounded listing proof. Check-time refusals of STABLE plants only — no
# TOCTOU immunity is claimed (design N3).
# ---------------------------------------------------------------------------


def _require_junction(link: Path, target: Path) -> None:
    """Create a real NTFS junction or fail loudly (never a silent skip)."""
    result = subprocess.run(
        ["cmd", "/c", "mklink", "/J", str(link), str(target)],
        capture_output=True,
        check=False,
    )
    if result.returncode != 0 or not link.is_junction():
        stderr = result.stderr.decode("utf-8", errors="replace").strip()
        pytest.fail(
            f"environment: cannot create NTFS junction {link} -> {target}: "
            f"rc={result.returncode} stderr={stderr!r}"
        )
    assert os.path.realpath(link) == os.path.realpath(target)


def _managed_root(tmp_path: Path) -> Path:
    root = tmp_path / "managed"
    root.mkdir()
    return root


# --- S1: root chain and fragment walk --------------------------------------


def test_s1_rejects_relative_root(tmp_path: Path) -> None:
    with pytest.raises(ProjectTaskPathError):
        assert_plain_components("relative/managed", "a.txt")
    with pytest.raises(ProjectTaskPathError):
        resolve_project_task_workspace_path(tmp_path / ".." / "managed", "a.txt")


def test_s1_rejects_missing_root(tmp_path: Path) -> None:
    with pytest.raises(ProjectTaskPathError):
        assert_plain_components(tmp_path / "absent-root", "a.txt")


@posix_only
def test_s1_rejects_symlinked_root_itself(tmp_path: Path) -> None:
    """A15 resolver layer: the running root replaced by a stable symlink."""
    canary = _outside_canary(tmp_path)
    real = tmp_path / "real-root"
    real.mkdir()
    (real / "notes.txt").write_text("inner", encoding="utf-8")
    link = tmp_path / "managed"
    link.symlink_to(real, target_is_directory=True)

    with pytest.raises(ProjectTaskPathError):
        resolve_project_task_workspace_path(link, "notes.txt")
    with pytest.raises(ProjectTaskPathError):
        resolve_project_task_workspace_path(link, "")
    assert canary.read_text(encoding="utf-8") == CANARY_TEXT


@windows_only
def test_s1_rejects_junction_root_itself(tmp_path: Path) -> None:
    canary = _outside_canary(tmp_path)
    real = tmp_path / "real-root"
    real.mkdir()
    link = tmp_path / "managed"
    _require_junction(link, real)
    try:
        with pytest.raises(ProjectTaskPathError):
            resolve_project_task_workspace_path(link, "notes.txt")
    finally:
        link.rmdir()
    assert canary.read_text(encoding="utf-8") == CANARY_TEXT


@posix_only
def test_s1_rejects_symlinked_managed_ancestor(tmp_path: Path) -> None:
    """A16 resolver layer: a managed ancestor replaced while the leaf stays real."""
    home = tmp_path / "home"
    octop = home / ".octop"
    leaf = octop / "project-task-files" / "agent01"
    leaf.mkdir(parents=True)
    (leaf / "notes.txt").write_text("inner", encoding="utf-8")
    canary = _outside_canary(tmp_path)

    real_octop = home / "real-octop"
    octop.rename(real_octop)
    octop.symlink_to(real_octop, target_is_directory=True)
    try:
        with pytest.raises(ProjectTaskPathError):
            resolve_project_task_workspace_path(leaf, "notes.txt")
    finally:
        octop.unlink()
        real_octop.rename(octop)
    # Post-restoration positive: the clean chain resolves again.
    assert resolve_project_task_workspace_path(leaf, "notes.txt") == (leaf / "notes.txt").resolve()
    assert canary.read_text(encoding="utf-8") == CANARY_TEXT


def test_s1_fragment_walk_stops_at_first_missing_component(tmp_path: Path) -> None:
    root = _managed_root(tmp_path)
    # Missing components end the walk (creation semantics) — no refusal.
    assert_plain_components(root, "absent/deeper/file.txt")
    assert_plain_components(root, ".")


@posix_only
def test_s1_intermediate_component_symlink_is_refused(tmp_path: Path) -> None:
    root = _managed_root(tmp_path)
    canary = _outside_canary(tmp_path)
    (root / "link").symlink_to(canary.parent, target_is_directory=True)
    with pytest.raises(ProjectTaskPathError):
        assert_plain_components(root, "link/canary.txt")
    # A final-component symlink is refused too.
    (root / "escape.txt").symlink_to(canary)
    with pytest.raises(ProjectTaskPathError):
        assert_plain_components(root, "escape.txt")


@posix_only
def test_s1_wildcard_tail_stops_at_magic_component(tmp_path: Path) -> None:
    root = _managed_root(tmp_path)
    canary = _outside_canary(tmp_path)
    # A literal directory named "*" that is a symlink: with wildcard_tail the
    # component walk stops BEFORE the magic name (S3 covers the subtree);
    # without it the literal walk refuses the link.
    (root / "*").symlink_to(canary.parent, target_is_directory=True)
    assert_plain_components(root, "*/canary.txt", wildcard_tail=True)
    with pytest.raises(ProjectTaskPathError):
        assert_plain_components(root, "*/canary.txt")
    # The S3 subtree scan deterministically refuses the same plant.
    with pytest.raises(ProjectTaskPathError):
        assert_listing_subtree_reparse_free(root)


# --- wildcard_tail: pattern shapes must never be lstat/resolve'd literally --


def test_resolve_wildcard_tail_patterns_skip_literal_final(tmp_path: Path) -> None:
    """A glob pattern's final component is a pattern, not a literal name.

    The literal-``lstat``/``resolve()`` path would raise ``WinError 123`` on
    Windows for ``*.md``; with ``wildcard_tail`` the proven literal prefix is
    returned unresolved and the S3 subtree scan covers the expansion. Legal
    patterns stay functional on BOTH platforms (Windows-native gate).
    """
    root = _managed_root(tmp_path)
    (root / "notes.md").write_text("x", encoding="utf-8")
    (root / "sub").mkdir()

    assert resolve_project_task_workspace_path(root, "*.md", wildcard_tail=True) == root
    assert resolve_project_task_workspace_path(root, "**/*.md", wildcard_tail=True) == root
    assert resolve_project_task_workspace_path(root, "sub/*.md", wildcard_tail=True) == (
        root / "sub"
    )
    # Hostile / traversal pattern shapes still refuse.
    with pytest.raises(ProjectTaskPathError):
        resolve_project_task_workspace_path(root, "../*.md", wildcard_tail=True)
    with pytest.raises(ProjectTaskPathError):
        resolve_project_task_workspace_path(root, "C:/*.md", wildcard_tail=True)
    with pytest.raises(ProjectTaskPathError):
        resolve_project_task_workspace_path(root, "//host/*.md", wildcard_tail=True)


@posix_only
def test_resolve_wildcard_tail_still_refuses_literal_prefix_link(tmp_path: Path) -> None:
    """The literal prefix of a pattern keeps the full S1 no-follow proof."""
    root = _managed_root(tmp_path)
    canary = _outside_canary(tmp_path)
    (root / "link").symlink_to(canary.parent, target_is_directory=True)
    with pytest.raises(ProjectTaskPathError):
        resolve_project_task_workspace_path(root, "link/*.md", wildcard_tail=True)
    with pytest.raises(ProjectTaskPathError):
        resolve_project_task_workspace_path(root, "link/**/*.md", wildcard_tail=True)
    assert canary.read_text(encoding="utf-8") == CANARY_TEXT


# --- S2: final-component type proof ----------------------------------------


def test_s2_kind_mismatches_and_missing_semantics(tmp_path: Path) -> None:
    root = _managed_root(tmp_path)
    (root / "notes.txt").write_text("x", encoding="utf-8")
    (root / "sub").mkdir()

    assert_plain_final(root, "notes.txt", "file")
    assert_plain_final(root, "sub", "dir")
    assert_plain_final(root, ".", "dir")
    assert_plain_final(root, "absent.txt", "file")  # creation semantics
    assert_plain_final(root, "absent", "dir")  # backend 404 semantics

    with pytest.raises(ProjectTaskPathError):
        assert_plain_final(root, "notes.txt", "dir")
    with pytest.raises(ProjectTaskPathError):
        assert_plain_final(root, "sub", "file")
    with pytest.raises(ProjectTaskPathError):
        assert_plain_final(root, ".", "file")

    with pytest.raises(ProjectTaskPathError):
        resolve_project_task_workspace_path(root, "sub", kind="file")
    with pytest.raises(ProjectTaskPathError):
        resolve_project_task_workspace_path(root, "notes.txt", kind="dir")
    assert (
        resolve_project_task_workspace_path(root, "notes.txt", kind="file")
        == (root / "notes.txt").resolve()
    )


@posix_only
def test_s2_rejects_fifo_and_socket_final_components(tmp_path: Path) -> None:
    root = _managed_root(tmp_path)
    os.mkfifo(root / "pipe")
    with pytest.raises(ProjectTaskPathError):
        assert_plain_final(root, "pipe", "file")
    with pytest.raises(ProjectTaskPathError):
        resolve_project_task_workspace_path(root, "pipe", kind="file")


# --- S7: hard-link refusal (default on) ------------------------------------


def test_s7_rejects_hardlinked_final_component(tmp_path: Path) -> None:
    root = _managed_root(tmp_path)
    outside_host = tmp_path / "outside" / "host.txt"
    outside_host.parent.mkdir()
    outside_host.write_text(CANARY_TEXT, encoding="utf-8")
    os.link(outside_host, root / "linked.txt")
    try:
        with pytest.raises(ProjectTaskPathError):
            resolve_project_task_workspace_path(root, "linked.txt", kind="file")
        # Positive: a regular single-link file passes.
        (root / "plain.txt").write_text("plain", encoding="utf-8")
        assert (
            resolve_project_task_workspace_path(root, "plain.txt", kind="file")
            == (root / "plain.txt").resolve()
        )
        assert outside_host.read_text(encoding="utf-8") == CANARY_TEXT
    finally:
        (root / "linked.txt").unlink()


# --- S3: bounded reparse-free listing proof --------------------------------


def test_s3_default_cap_is_pinned() -> None:
    assert PROJECT_TASK_LISTING_MAX_ENTRIES == 50_000


def test_s3_overflow_is_injectable_and_fail_closed(tmp_path: Path) -> None:
    root = _managed_root(tmp_path)
    for index in range(3):
        (root / f"f{index}.txt").write_text("x", encoding="utf-8")
    with pytest.raises(ProjectTaskListingOverflowError):
        assert_listing_subtree_reparse_free(root, max_entries=2)
    # At the cap it passes; the bound is inclusive.
    assert_listing_subtree_reparse_free(root, max_entries=3)


@posix_only
def test_s3_rejects_reparse_anywhere_in_subtree_but_not_siblings(tmp_path: Path) -> None:
    root = _managed_root(tmp_path)
    canary = _outside_canary(tmp_path)
    (root / "a" / "b").mkdir(parents=True)
    (root / "a" / "b" / "deep_link").symlink_to(canary)
    (root / "clean").mkdir()
    (root / "clean" / "ok.txt").write_text("x", encoding="utf-8")

    # A deep link not on any requested literal path still refuses the scan.
    with pytest.raises(ProjectTaskPathError):
        assert_listing_subtree_reparse_free(root)
    # Anti-over-refusal (A4): a clean sibling subtree still lists.
    assert_listing_subtree_reparse_free(root / "clean")


def test_s3_missing_base_passes_and_non_dir_base_refuses(tmp_path: Path) -> None:
    root = _managed_root(tmp_path)
    (root / "notes.txt").write_text("x", encoding="utf-8")
    assert_listing_subtree_reparse_free(root / "absent")
    with pytest.raises(ProjectTaskPathError):
        assert_listing_subtree_reparse_free(root / "notes.txt")


@windows_only
def test_s3_rejects_junction_anywhere_in_subtree(tmp_path: Path) -> None:
    root = _managed_root(tmp_path)
    canary = _outside_canary(tmp_path)
    (root / "a").mkdir()
    link = root / "a" / "junction"
    _require_junction(link, canary.parent)
    try:
        with pytest.raises(ProjectTaskPathError):
            assert_listing_subtree_reparse_free(root)
    finally:
        link.rmdir()
    assert_listing_subtree_reparse_free(root)
    assert canary.read_text(encoding="utf-8") == CANARY_TEXT


def test_s3_rejects_hardlinked_entries_but_not_clean_siblings(tmp_path: Path) -> None:
    """S3 must refuse discoverable hard-linked regular entries (S7 subtree arm).

    ``ls``/``glob``/``grep`` could otherwise enumerate or read a file that is
    really an outside inode planted as a hard link.
    """
    root = _managed_root(tmp_path)
    outside_host = tmp_path / "outside" / "host.txt"
    outside_host.parent.mkdir()
    outside_host.write_text(CANARY_TEXT, encoding="utf-8")
    (root / "dirty").mkdir()
    (root / "clean").mkdir()
    (root / "clean" / "ok.txt").write_text("x", encoding="utf-8")
    os.link(outside_host, root / "dirty" / "linked.txt")
    try:
        with pytest.raises(ProjectTaskPathError):
            assert_listing_subtree_reparse_free(root)
        # Anti-over-refusal: the clean sibling subtree still lists.
        assert_listing_subtree_reparse_free(root / "clean")
        assert outside_host.read_text(encoding="utf-8") == CANARY_TEXT
    finally:
        (root / "dirty" / "linked.txt").unlink()


@posix_only
def test_s3_rejects_fifo_and_socket_entries(tmp_path: Path) -> None:
    root = _managed_root(tmp_path)
    os.mkfifo(root / "pipe")
    with pytest.raises(ProjectTaskPathError):
        assert_listing_subtree_reparse_free(root)
    (root / "pipe").unlink()

    sock = socket.socket(socket.AF_UNIX)
    try:
        sock.bind(str(root / "sock"))
        with pytest.raises(ProjectTaskPathError):
            assert_listing_subtree_reparse_free(root)
    finally:
        sock.close()
    (root / "sock").unlink()
    assert_listing_subtree_reparse_free(root)


# --- S2/S3: grep search base (regular file OR directory) -------------------


def test_search_base_accepts_file_and_dir_and_refuses_subtree_hardlinks(
    tmp_path: Path,
) -> None:
    """The installed backend greps a single regular file or a directory.

    Both forms are positive; a directory base additionally scans its subtree,
    so a hard link beneath it refuses; special files refuse on the file form.
    """
    root = _managed_root(tmp_path)
    (root / "notes.txt").write_text("x", encoding="utf-8")
    (root / "sub").mkdir()
    (root / "sub" / "nested.txt").write_text("y", encoding="utf-8")

    assert_plain_search_base(root, "notes.txt")
    assert_plain_search_base(root, "sub")
    assert_plain_search_base(root, ".")
    assert_plain_search_base(root, "absent")

    outside_host = tmp_path / "outside" / "host.txt"
    outside_host.parent.mkdir()
    outside_host.write_text(CANARY_TEXT, encoding="utf-8")
    os.link(outside_host, root / "sub" / "linked.txt")
    try:
        with pytest.raises(ProjectTaskPathError):
            assert_plain_search_base(root, "sub")
        assert_plain_search_base(root, "notes.txt")
    finally:
        (root / "sub" / "linked.txt").unlink()
    assert_plain_search_base(root, "sub")


@posix_only
def test_search_base_refuses_fifo_file_form(tmp_path: Path) -> None:
    root = _managed_root(tmp_path)
    os.mkfifo(root / "pipe")
    with pytest.raises(ProjectTaskPathError):
        assert_plain_search_base(root, "pipe")
