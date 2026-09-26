"""030A S5 unit evidence: fail-closed plain-directory chain proofs.

Covers the RD-5 unit layer: clean-chain positives (idempotent, POSIX 0700
only on the feature-owned components), stable planted symlink/junction
refusals at the leaf, at the private parent and at an ancestor, relative
chain fail-closed, non-directory components, and the unsupported-platform
gate. Check-time proofs only — no TOCTOU immunity is claimed (design N3).
"""

from __future__ import annotations

import os
import stat
import subprocess
import sys
from pathlib import Path

import pytest

from octop.infra.utils.paths import PathLayout
from octop.infra.utils.safe_dirs import (
    SafeDirectoryError,
    UnsupportedPlatformError,
    assert_plain_directory,
    assert_plain_directory_chain,
    ensure_plain_directory_chain,
    is_reparse_point,
    platform_supports_plain_directory_proofs,
)

posix_only = pytest.mark.skipif(os.name != "posix", reason="POSIX symlink and mode semantics")
windows_only = pytest.mark.skipif(os.name != "nt", reason="Windows junction semantics")


def _require_windows_junction(link: Path, target: Path) -> None:
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


def _leaf(tmp_path: Path) -> Path:
    return tmp_path / "home" / ".octop" / "project-task-files" / "agent01"


def _private_parent(tmp_path: Path) -> Path:
    return tmp_path / "home" / ".octop" / "project-task-files"


# ---------------------------------------------------------------------------
# Positives
# ---------------------------------------------------------------------------


def test_clean_chain_is_created_and_idempotent(tmp_path: Path) -> None:
    leaf = _leaf(tmp_path)
    ensure_plain_directory_chain(leaf, private_from=_private_parent(tmp_path))
    assert leaf.is_dir()
    # Idempotent: a second ensure on the fully existing chain re-proves it.
    ensure_plain_directory_chain(leaf, private_from=_private_parent(tmp_path))
    assert leaf.is_dir()


@posix_only
def test_posix_modes_apply_only_to_feature_owned_components(tmp_path: Path) -> None:
    previous_umask = os.umask(0o022)
    try:
        leaf = _leaf(tmp_path)
        ensure_plain_directory_chain(leaf, private_from=_private_parent(tmp_path))
    finally:
        os.umask(previous_umask)
    assert stat.S_IMODE(leaf.stat().st_mode) == 0o700
    assert stat.S_IMODE(_private_parent(tmp_path).stat().st_mode) == 0o700
    # The shared chain above private_from is never chmod-ed: it keeps the
    # umask-default mode mkdir gave it (0755 under the pinned 0o022 umask).
    assert stat.S_IMODE((tmp_path / "home").stat().st_mode) == 0o755
    assert stat.S_IMODE((tmp_path / "home" / ".octop").stat().st_mode) == 0o755


def test_assert_plain_directory_chain_accepts_real_directories(tmp_path: Path) -> None:
    leaf = _leaf(tmp_path)
    ensure_plain_directory_chain(leaf, private_from=_private_parent(tmp_path))
    assert_plain_directory_chain(leaf)
    st = assert_plain_directory(leaf)
    assert stat.S_ISDIR(st.st_mode)
    assert not is_reparse_point(st)


# ---------------------------------------------------------------------------
# Fail-closed refusals
# ---------------------------------------------------------------------------


def test_relative_chain_fails_closed_without_mkdir(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    with pytest.raises(SafeDirectoryError):
        ensure_plain_directory_chain(Path("relative/.octop/project-task-files/a1"))
    with pytest.raises(SafeDirectoryError):
        assert_plain_directory_chain(Path("relative/leaf"))
    assert not (tmp_path / "relative").exists()


def test_missing_component_refuses_assert_chain(tmp_path: Path) -> None:
    with pytest.raises(SafeDirectoryError):
        assert_plain_directory_chain(tmp_path / "nope" / "leaf")


def test_regular_file_component_refuses_chain(tmp_path: Path) -> None:
    (tmp_path / "blocker").write_text("x", encoding="utf-8")
    with pytest.raises(SafeDirectoryError):
        ensure_plain_directory_chain(tmp_path / "blocker" / "leaf")
    with pytest.raises(SafeDirectoryError):
        assert_plain_directory(tmp_path / "blocker")


@posix_only
def test_planted_symlink_leaf_is_refused_not_adopted(tmp_path: Path) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "canary.txt").write_text("KEEP", encoding="utf-8")
    leaf = _leaf(tmp_path)
    leaf.parent.mkdir(parents=True)
    leaf.symlink_to(outside, target_is_directory=True)

    with pytest.raises(SafeDirectoryError):
        ensure_plain_directory_chain(leaf, private_from=_private_parent(tmp_path))
    # The outside target was never adopted, mutated or removed.
    assert (outside / "canary.txt").read_text(encoding="utf-8") == "KEEP"
    assert leaf.is_symlink()


@posix_only
def test_planted_symlink_ancestor_is_refused(tmp_path: Path) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    real_octop = tmp_path / "real-octop"
    real_octop.mkdir()
    link = tmp_path / "home" / ".octop"
    link.parent.mkdir(parents=True)
    link.symlink_to(real_octop, target_is_directory=True)

    leaf = _leaf(tmp_path)
    with pytest.raises(SafeDirectoryError):
        ensure_plain_directory_chain(leaf, private_from=_private_parent(tmp_path))
    with pytest.raises(SafeDirectoryError):
        assert_plain_directory_chain(leaf)
    assert not (real_octop / "project-task-files").exists()


@windows_only
def test_planted_junction_leaf_is_refused_not_adopted(tmp_path: Path) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "canary.txt").write_text("KEEP", encoding="utf-8")
    leaf = _leaf(tmp_path)
    leaf.parent.mkdir(parents=True)
    _require_windows_junction(leaf, outside)
    try:
        with pytest.raises(SafeDirectoryError):
            ensure_plain_directory_chain(leaf, private_from=_private_parent(tmp_path))
        assert (outside / "canary.txt").read_text(encoding="utf-8") == "KEEP"
    finally:
        leaf.rmdir()


@windows_only
def test_planted_junction_ancestor_is_refused(tmp_path: Path) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    link = tmp_path / "project-task-files"
    _require_windows_junction(link, outside)
    try:
        with pytest.raises(SafeDirectoryError):
            ensure_plain_directory_chain(
                link / "agent01", private_from=tmp_path / "project-task-files"
            )
    finally:
        link.rmdir()


def test_private_from_off_chain_is_refused(tmp_path: Path) -> None:
    with pytest.raises(SafeDirectoryError):
        ensure_plain_directory_chain(tmp_path / "a" / "b", private_from=tmp_path / "other")
    assert not (tmp_path / "a").exists()


# ---------------------------------------------------------------------------
# Platform gate (design §8.3)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("platform", "os_name", "supported"),
    [
        ("linux", "posix", True),
        ("win32", "nt", True),
        # macOS/BSD and other POSIX are unprobed → fail closed.
        ("darwin", "posix", False),
        ("freebsd13", "posix", False),
        # Inconsistent simulation: an "nt" interpreter still selects Windows.
        ("darwin", "nt", True),
    ],
)
def test_platform_predicate_matrix(platform: str, os_name: str, supported: bool) -> None:
    """Portable matrix: no monkeypatching of global ``os.name`` (pathlib)."""
    assert platform_supports_plain_directory_proofs(platform=platform, os_name=os_name) is supported


def test_platform_predicate_defaults_to_runtime() -> None:
    assert platform_supports_plain_directory_proofs() == (
        sys.platform == "linux" or os.name == "nt"
    )


def test_unsupported_platform_fails_closed(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """The gate wiring refuses before any mkdir on an unsupported platform."""
    from octop.infra.utils import safe_dirs

    monkeypatch.setattr(safe_dirs, "platform_supports_plain_directory_proofs", lambda: False)
    assert not safe_dirs.platform_supports_plain_directory_proofs()
    with pytest.raises(UnsupportedPlatformError):
        ensure_plain_directory_chain(tmp_path / "leaf")
    assert not (tmp_path / "leaf").exists()
    # The platform refusal is an OSError subclass for lifecycle catches.
    assert issubclass(UnsupportedPlatformError, OSError)
    assert issubclass(SafeDirectoryError, NotADirectoryError)


@posix_only
def test_darwin_platform_simulation_fails_closed(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Real predicate simulation on POSIX (on Windows ``os.name`` stays nt)."""
    monkeypatch.setattr(sys, "platform", "darwin")
    assert not platform_supports_plain_directory_proofs()
    with pytest.raises(UnsupportedPlatformError):
        ensure_plain_directory_chain(tmp_path / "leaf")
    assert not (tmp_path / "leaf").exists()


# ---------------------------------------------------------------------------
# PathLayout S5 wiring
# ---------------------------------------------------------------------------


def test_layout_ensure_refuses_relative_octop_home(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("OCTOP_HOME", "relative-home")
    layout = PathLayout.from_env()
    with pytest.raises(SafeDirectoryError):
        layout.ensure_project_task_file_runtime_dir("agent01")
    assert not (tmp_path / "relative-home").exists()


@posix_only
def test_layout_ensure_refuses_preplanted_symlink_root(tmp_path: Path) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "canary.txt").write_text("KEEP", encoding="utf-8")
    layout = PathLayout(tmp_path / ".octop")
    root = layout.project_task_file_runtime_dir("agent01")
    root.parent.mkdir(parents=True)
    root.symlink_to(outside, target_is_directory=True)

    with pytest.raises(SafeDirectoryError):
        layout.ensure_project_task_file_runtime_dir("agent01")
    assert (outside / "canary.txt").read_text(encoding="utf-8") == "KEEP"
    assert not (outside / "seed.txt").exists()


def test_layout_ensure_keeps_unsafe_id_valueerror(tmp_path: Path) -> None:
    layout = PathLayout(tmp_path / ".octop")
    with pytest.raises(ValueError, match="unsafe"):
        layout.ensure_project_task_file_runtime_dir("../escape")


def test_layout_ensure_positive_creates_plain_root(tmp_path: Path) -> None:
    layout = PathLayout(tmp_path / ".octop")
    root = layout.ensure_project_task_file_runtime_dir("agent01")
    assert root == layout.project_task_file_runtime_dir("agent01")
    assert_plain_directory_chain(root)
    if os.name == "posix":
        assert stat.S_IMODE(root.stat().st_mode) == 0o700
        assert stat.S_IMODE(layout.project_task_files_dir.stat().st_mode) == 0o700
    # Re-ensure is idempotent and keeps the same real directory.
    before = os.lstat(root)
    layout.ensure_project_task_file_runtime_dir("agent01")
    after = os.lstat(root)
    assert (before.st_dev, before.st_ino) == (after.st_dev, after.st_ino)
