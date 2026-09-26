"""Strict workspace-relative path policy for managed project-task file runtimes.

HTTP wiring calls :func:`normalize_project_task_io_path` /
:func:`resolve_project_task_workspace_path` at every internal-runtime entry
before touching ``BackendWorkspace``; listing routes additionally run
:func:`assert_listing_subtree_reparse_free` (030A S3) before ``als``/``aglob``
/``agrep``; the tool boundary middleware (030A S4) reuses the S1/S2/S3
assertions here.

Installed ``BackendWorkspace`` falls back to raw host-absolute paths on POSIX
(``materialize_local``), so host absolute paths, drive paths, UNC paths,
``file://`` URLs, ``~`` and ``..`` must be rejected here rather than papered
over by the backend.

030A B+ (design §5.2): S1 walks the full lexical absolute chain from the
root's anchor through every managed ancestor and every literal fragment
component with no-follow ``lstat`` BEFORE any ``resolve()``; S2 checks the
final component type; S7 refuses ``st_nlink > 1`` hard-linked regular files;
S3 proves a bounded reparse-free subtree for listing operations. These are
CHECK-TIME proofs — they deterministically refuse stable planted
symlinks/junctions but do not close the check→use window (design N3).
"""

from __future__ import annotations

import os
import re
import stat
from pathlib import Path
from typing import Literal

from octop.infra.utils.safe_dirs import is_reparse_point

_WINDOWS_DRIVE_RE = re.compile(r"^[A-Za-z]:")

#: Bounded-scan cap for listing proofs (design A13; injectable per call so
#: tests can pin the boundary deterministically with small fixtures).
PROJECT_TASK_LISTING_MAX_ENTRIES = 50_000

_GLOB_MAGIC = frozenset("*?[]")


class ProjectTaskPathError(ValueError):
    """A path rejected for a managed project-task file runtime."""


class ProjectTaskListingOverflowError(RuntimeError):
    """A listing proof exceeded the bounded-scan cap (fail-closed 503)."""


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


def _literal_parts(rel: str) -> list[str]:
    """Lexical fragment components (``.``/empty collapsed, ``..`` impossible)."""
    return [part for part in rel.split("/") if part not in ("", ".")]


def _has_glob_magic(rel: str) -> bool:
    """True when any literal component of *rel* contains glob metacharacters."""
    return any(_GLOB_MAGIC.intersection(part) for part in _literal_parts(rel))


def _literal_prefix(rel: str) -> list[str]:
    """Literal components before the first glob-magic segment."""
    parts: list[str] = []
    for part in _literal_parts(rel):
        if _GLOB_MAGIC.intersection(part):
            break
        parts.append(part)
    return parts


def _lstat_or_refuse(path: Path) -> os.stat_result:
    try:
        return os.lstat(path)
    except OSError as exc:
        raise ProjectTaskPathError("cannot inspect a managed path component") from exc


def _refuse_reparse(st: os.stat_result) -> None:
    if is_reparse_point(st):
        raise ProjectTaskPathError("path component is a symlink or reparse point")


def assert_plain_components(
    root: str | Path,
    rel: str,
    *,
    wildcard_tail: bool = False,
) -> None:
    """030A S1: no-follow plainness walk BEFORE any ``resolve()``.

    Walks the entire lexical absolute chain from ``root.anchor`` through the
    managed root (layout root, ``project-task-files``, ``<agent_id>`` — every
    component, not only the last two), then every literal component of *rel*
    under the root. A relative root fails closed without ``resolve()`` hiding
    ancestor links. Any symlink/junction/reparse point, probe error, missing
    root-chain component or non-directory intermediate is refused. Fragment
    components stop at the first ``ENOENT`` (creation semantics: a missing
    component is NOT proof it can be opened safely). With *wildcard_tail*,
    the walk stops before the first glob-magic component — unprovable
    wildcard names are covered by the S3 subtree scan instead.
    """
    root_path = Path(root)
    if not root_path.is_absolute():
        raise ProjectTaskPathError("managed root must be a lexical absolute path")
    if any(part in (".", "..", "") for part in root_path.parts[1:]):
        raise ProjectTaskPathError("managed root must be lexically plain (no dot components)")

    def _plain_dir(component: Path) -> None:
        st = _lstat_or_refuse(component)
        _refuse_reparse(st)
        if not stat.S_ISDIR(st.st_mode):
            raise ProjectTaskPathError("managed root chain component is not a directory")

    # parts[0] is the anchor itself on both platforms ("/", "C:\\", UNC share).
    current = Path(root_path.parts[0])
    _plain_dir(current)
    for part in root_path.parts[1:]:
        current = current / part
        _plain_dir(current)
    parts = _literal_parts(str(rel))
    base = root_path
    last = len(parts) - 1
    for index, part in enumerate(parts):
        if wildcard_tail and _GLOB_MAGIC.intersection(part):
            return
        base = base / part
        try:
            st = os.lstat(base)
        except FileNotFoundError:
            return
        except OSError as exc:
            raise ProjectTaskPathError("cannot inspect a managed path component") from exc
        _refuse_reparse(st)
        if index < last and not stat.S_ISDIR(st.st_mode):
            raise ProjectTaskPathError("intermediate path component is not a directory")


def assert_plain_final(
    root: str | Path, rel: str, kind: Literal["file", "dir", "any"]
) -> os.stat_result | None:
    """030A S2 (+S7): final-component type proof for one operation.

    A missing final component passes and returns ``None`` (backend 404
    semantics for reads, creation semantics for writes). An existing final
    component must be a plain regular file (``kind`` ``file``/``any``) or a
    plain real directory (``kind`` ``dir``/``any``): symlinks/junctions/
    reparse points, FIFOs, sockets and device files are refused, and regular
    files with ``st_nlink > 1`` are refused (S7 hard-link default-on — no
    allowed entry point can legitimately produce a multi-link file inside the
    root).
    """
    root_path = Path(root)
    parts = _literal_parts(str(rel))
    target = root_path.joinpath(*parts) if parts else root_path
    try:
        st = os.lstat(target)
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise ProjectTaskPathError("cannot inspect the managed target") from exc
    _refuse_reparse(st)
    if stat.S_ISREG(st.st_mode):
        if getattr(st, "st_nlink", 1) > 1:
            raise ProjectTaskPathError("target file has multiple hard links")
        if kind == "dir":
            raise ProjectTaskPathError("expected a directory, found a regular file")
        return st
    if stat.S_ISDIR(st.st_mode):
        if kind == "file":
            raise ProjectTaskPathError("expected a file, found a directory")
        return st
    raise ProjectTaskPathError("target is not a regular file or a real directory")


def assert_plain_search_base(root: str | Path, rel: str, *, max_entries: int | None = None) -> None:
    """030A S2/S3 for a search base that may be a regular file OR a directory.

    The installed filesystem backend permits ``grep`` to search either a
    single regular file or a directory. Both forms get S1 + the full S2 type
    proof (reparse points, FIFOs/sockets/devices and hard-linked regular
    files refuse). A directory base is additionally covered by the bounded S3
    subtree proof, because the backend reads every candidate file beneath it.
    A missing base passes through to the backend's empty-result semantics.
    """
    assert_plain_components(root, rel)
    st = assert_plain_final(root, rel, "any")
    if st is None or not stat.S_ISDIR(st.st_mode):
        return
    parts = _literal_parts(rel)
    base = Path(root).joinpath(*parts) if parts else Path(root)
    assert_listing_subtree_reparse_free(base, max_entries=max_entries)


def assert_listing_subtree_reparse_free(
    base: str | Path, *, max_entries: int | None = None
) -> None:
    """030A S3: bounded no-follow proof that a listing subtree is safe to walk.

    Scans *base* with ``os.scandir`` + ``os.lstat(entry.path)``
    (plus ``entry.is_symlink()``); on Windows ``st_reparse_tag != 0`` is the
    primary junction detector — ``is_symlink()`` alone is NOT sufficient. Any
    symlink/junction/reparse point anywhere in the subtree refuses the whole
    listing, and so does any discovered entry that is neither a real directory
    nor a plain single-link regular file (FIFOs, sockets, device files and
    hard-linked regular files would otherwise be enumerable/readable through
    ``ls``/``glob``/``grep``). More than *max_entries* (default
    :data:`PROJECT_TASK_LISTING_MAX_ENTRIES`) entries raises
    :class:`ProjectTaskListingOverflowError` — fail-closed, never a silent
    pass. A missing base passes through to the backend's empty/404 semantics.
    """
    limit = PROJECT_TASK_LISTING_MAX_ENTRIES if max_entries is None else max_entries
    base_path = Path(base)
    try:
        st = os.lstat(base_path)
    except FileNotFoundError:
        return
    except OSError as exc:
        raise ProjectTaskPathError("cannot inspect the listing base") from exc
    _refuse_reparse(st)
    if not stat.S_ISDIR(st.st_mode):
        raise ProjectTaskPathError("listing base is not a real directory")
    counted = 0
    stack: list[str] = [str(base_path)]
    while stack:
        current = stack.pop()
        try:
            with os.scandir(current) as entries:
                for entry in entries:
                    counted += 1
                    if counted > limit:
                        raise ProjectTaskListingOverflowError(
                            f"listing proof exceeded the {limit}-entry bound"
                        )
                    try:
                        # On Windows DirEntry.stat may report st_nlink=0 for
                        # hard links; os.lstat returns the real link count.
                        entry_st = os.lstat(entry.path)
                    except OSError as exc:
                        raise ProjectTaskPathError(
                            "cannot inspect a listing subtree entry"
                        ) from exc
                    if entry.is_symlink() or is_reparse_point(entry_st):
                        raise ProjectTaskPathError(
                            "listing subtree contains a symlink or reparse point"
                        )
                    if stat.S_ISDIR(entry_st.st_mode):
                        stack.append(entry.path)
                        continue
                    if not stat.S_ISREG(entry_st.st_mode):
                        raise ProjectTaskPathError("listing subtree contains a non-regular file")
                    if getattr(entry_st, "st_nlink", 1) > 1:
                        raise ProjectTaskPathError(
                            "listing subtree contains a hard-linked regular file"
                        )
        except ProjectTaskPathError:
            raise
        except OSError as exc:
            raise ProjectTaskPathError("cannot scan the listing subtree") from exc


def resolve_project_task_workspace_path(
    root: str | Path,
    path: str,
    *,
    from_workspace: bool = True,
    kind: Literal["file", "dir", "any"] = "any",
    wildcard_tail: bool = False,
) -> Path:
    """Resolve *path* under the real managed *root*, rejecting escapes.

    030A S1/S2/S7 run on the LITERAL root and fragment before any
    ``resolve()``; the resolved target (including symlink/junction expansion)
    must then still stay under the resolved root. Raises
    :class:`ProjectTaskPathError` otherwise.

    With *wildcard_tail* and a fragment that contains glob metacharacters the
    final component is a PATTERN, not a literal name: S2 is skipped (a literal
    ``lstat``/``resolve()`` of e.g. ``*.md`` raises ``WinError 123`` on
    Windows) and the proven literal prefix is returned without ``resolve()``.
    The bounded S3 subtree scan covers the base subtree the pattern is matched
    against (design §5.2 S3).
    """
    relative = normalize_project_task_io_path(path, from_workspace=from_workspace)
    assert_plain_components(root, relative, wildcard_tail=wildcard_tail)
    if wildcard_tail and _has_glob_magic(relative):
        return Path(root).joinpath(*_literal_prefix(relative))
    assert_plain_final(root, relative, kind)
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
    "PROJECT_TASK_LISTING_MAX_ENTRIES",
    "ProjectTaskListingOverflowError",
    "ProjectTaskPathError",
    "assert_listing_subtree_reparse_free",
    "assert_plain_components",
    "assert_plain_final",
    "assert_plain_search_base",
    "normalize_project_task_io_path",
    "resolve_project_task_workspace_path",
]
