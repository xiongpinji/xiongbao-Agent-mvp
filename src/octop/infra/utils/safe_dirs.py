"""Fail-closed plain-directory proofs for managed directory chains (030A S5).

Pure-stdlib helpers extracted from the ``asset_storage._ensure_private_dir``
precedent (``infra/utils`` must not import ``infra/projects``; asset_storage
itself is deliberately NOT refactored in this slice). Everything here is
lstat/stat-level on Linux and Windows with identical semantics — no new
platform syscall surface beyond the POSIX ``O_DIRECTORY|O_NOFOLLOW`` probe
the repo already uses.

These are CHECK-TIME proofs (design §5.2 S5, residual race N3 unchanged):
they deterministically refuse stable planted symlinks/junctions, never
claim to close the check→use window.
"""

from __future__ import annotations

import os
import stat
import sys
from pathlib import Path

__all__ = [
    "SafeDirectoryError",
    "UnsupportedPlatformError",
    "assert_plain_directory",
    "assert_plain_directory_chain",
    "ensure_plain_directory_chain",
    "is_reparse_point",
    "platform_supports_plain_directory_proofs",
    "require_plain_directory_platform",
]


class SafeDirectoryError(NotADirectoryError):
    """A managed chain component is not a provably plain real directory.

    Subclasses :class:`NotADirectoryError` (hence ``OSError``) so lifecycle
    callers that catch ``OSError`` — the ``AgentManager`` create-flow
    compensation at ``manager.py`` — treat a refusal exactly like a
    filesystem failure (design §5.2 S5 failure-type contract).
    """


class UnsupportedPlatformError(SafeDirectoryError):
    """The running platform cannot prove directory plainness (design §8.3)."""


def platform_supports_plain_directory_proofs(
    *, platform: str | None = None, os_name: str | None = None
) -> bool:
    """True only where S1/S5 plainness proofs were verified (Linux, Windows).

    Other POSIX (macOS/BSD) and exotic ``os.name`` values fail closed: their
    lstat/reparse semantics are not in CI and were never probed. *platform*
    and *os_name* default to the running interpreter's values; tests inject
    explicit values so the full matrix is provable on every host without
    monkeypatching the global ``os.name`` (which would disturb ``pathlib``).
    """
    effective_platform = sys.platform if platform is None else platform
    effective_os_name = os.name if os_name is None else os_name
    return effective_platform == "linux" or effective_os_name == "nt"


def require_plain_directory_platform() -> None:
    """Raise :class:`UnsupportedPlatformError` on unprobed platforms."""
    if not platform_supports_plain_directory_proofs():
        raise UnsupportedPlatformError(
            "managed project-task directories require Linux or Windows directory semantics"
        )


def is_reparse_point(st: os.stat_result) -> bool:
    """Detect ANY no-follow reparse/symlink component (both platforms).

    ``st_reparse_tag`` exists only on Windows; a non-zero tag of any kind
    (junction, symlink, mount point, cloud placeholder, dedup) is treated
    conservatively as not-plain. POSIX symlinks are caught by ``S_ISLNK``.
    ``is_symlink()`` alone is NOT sufficient on Windows (junctions).
    """
    if stat.S_ISLNK(st.st_mode):
        return True
    return getattr(st, "st_reparse_tag", 0) != 0


def assert_plain_directory(path: Path) -> os.stat_result:
    """Prove *path* is a real plain directory via no-follow lstat (+POSIX probe).

    Any probe error, symlink, reparse point or non-directory fails closed with
    :class:`SafeDirectoryError`. POSIX adds the ``O_DIRECTORY|O_NOFOLLOW``
    open probe + fstat from the ``asset_storage._ensure_private_dir`` precedent.
    """
    try:
        st = os.lstat(path)
    except OSError as exc:
        raise SafeDirectoryError(f"cannot inspect directory {path}") from exc
    if not stat.S_ISDIR(st.st_mode) or is_reparse_point(st):
        raise SafeDirectoryError(f"{path} is not a plain directory")
    if os.name == "posix":
        flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
        try:
            fd = os.open(path, flags)
        except OSError as exc:
            raise SafeDirectoryError(f"cannot open directory {path} no-follow") from exc
        try:
            if not stat.S_ISDIR(os.fstat(fd).st_mode):
                raise SafeDirectoryError(f"{path} is not a directory")
        finally:
            os.close(fd)
    return st


def _chain_components(path: Path) -> list[Path]:
    """Lexical component prefixes from the anchor down to *path* inclusive."""
    components: list[Path] = [Path(path.anchor)]
    current = components[0]
    for part in path.parts[1:]:
        current = current / part
        components.append(current)
    return components


def _validated_chain(path: Path) -> list[Path]:
    """Fail-closed lexical validation + component list for a managed chain.

    Relative paths are refused WITHOUT ``resolve()`` hiding ancestor links;
    ``.``/``..`` components are refused too (a lexically non-plain chain can
    hide link traversal behind kernel ``..`` resolution).
    """
    target = Path(path)
    if not target.is_absolute():
        raise SafeDirectoryError(f"managed directory chain must be absolute: {path}")
    if any(part in (".", "..", "") for part in target.parts[1:]):
        raise SafeDirectoryError(f"managed directory chain must be lexically plain: {path}")
    return _chain_components(target)


def assert_plain_directory_chain(path: Path) -> None:
    """Prove the FULL lexical absolute chain anchor→*path* is plain directories.

    030A S1/S5/S6: every component — including the layout root
    (``OCTOP_HOME`` / ``~/.octop``), ``project-task-files`` and the
    ``<agent_id>`` leaf — must lstat as a real plain directory; only the last
    two levels is not enough. Relative paths fail closed WITHOUT ``resolve()``
    hiding ancestor links. All components must exist.
    """
    for component in _validated_chain(path):
        assert_plain_directory(component)


def ensure_plain_directory_chain(
    target: Path,
    *,
    private_from: Path | None = None,
    mode: int = 0o700,
) -> None:
    """Create (as needed) and prove the anchor→*target* plain directory chain.

    Every already-existing component is proven plain BEFORE any mutation;
    each created component is proven immediately after creation; the full
    chain is re-proven at the end. ``exist_ok`` hits — including a pre-planted
    symlink/junction at *target* itself (the ``mkdir(exist_ok=True)`` adoption
    gap, design §3.4) — are validated the same way, never adopted.

    ``private_from`` (an ancestor of *target* on the chain, inclusive) and
    everything below it receive *mode* on POSIX; the shared chain above it
    (e.g. the layout root) is created with default umask mode and never
    chmod-ed. Relative *target* fails closed.
    """
    require_plain_directory_platform()
    components = _validated_chain(target)
    private_index = len(components) - 1
    if private_from is not None:
        boundary = Path(private_from)
        try:
            private_index = components.index(boundary)
        except ValueError as exc:
            raise SafeDirectoryError(f"{private_from} is not on the {target} chain") from exc
    # Pass 1: prove every existing component before mutating anything; stop
    # at the first missing one (creation semantics — missing is not proof of
    # safety, so each created component is proven again in pass 2).
    first_missing = len(components)
    for index, component in enumerate(components):
        try:
            os.lstat(component)
        except FileNotFoundError:
            first_missing = index
            break
        except OSError as exc:
            raise SafeDirectoryError(f"cannot inspect directory {component}") from exc
        assert_plain_directory(component)
    # Pass 2: create missing components one by one, proving each immediately.
    for index in range(first_missing, len(components)):
        component = components[index]
        component_mode = mode if index >= private_index else 0o777
        try:
            component.mkdir(mode=component_mode, exist_ok=True)
        except OSError as exc:
            raise SafeDirectoryError(f"cannot create directory {component}") from exc
        assert_plain_directory(component)
    # POSIX: enforce the feature-owned modes (mkdir's mode is umask-masked).
    # The shared chain above private_from is never chmod-ed.
    if os.name == "posix":
        for component in components[private_index:]:
            os.chmod(component, mode)
    # Pass 3: post-creation full-chain proof.
    for component in components:
        assert_plain_directory(component)
