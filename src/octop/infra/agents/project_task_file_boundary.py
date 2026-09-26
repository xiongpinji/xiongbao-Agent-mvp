"""Fail-closed tool boundary for managed project-task file runtimes (030A).

A ``project_task_files`` runtime exposes only ``ls``, ``read_file``,
``write_file``, ``edit_file``, ``glob`` and ``grep``. The installed harness
``tools_disabled`` denylist (``ToolsFilterMiddleware``) hides names from the
model but does not veto execution, so this module adds
:class:`ProjectTaskFileToolBoundaryMiddleware`: it filters every model request
down to the allowlist and rejects any other tool call before the handler runs.

030A S4: past the name allowlist the guard also enforces a fail-closed
argument contract and runs the same S1/S2 (/S3 for listing tools) CHECK-TIME
proofs the HTTP resolver runs, on the TOOL virtual-path contract — which is
deliberately different from the HTTP normalizer (single leading ``/`` is the
virtual root anchor, bare-relative fragments are equally legal,
``glob/grep.path`` may be omitted, whitespace in names is preserved,
backslashes are rejected outright). Unknown fields, wrong types, missing
required paths and any host-path shape refuse without echoing values back to
the model. These are check-time refusals of stable plants; the check→use
window (design N1/N2/N3) is not claimed closed.

Nothing here changes normal agents until a host explicitly mounts the guard via
:func:`apply_project_task_file_boundary`.
"""

from __future__ import annotations

import asyncio
import re
from collections.abc import Awaitable, Callable
from dataclasses import replace
from pathlib import Path
from typing import Any

from harness_agent.config import HarnessAgentConfig
from langchain.agents.middleware import AgentMiddleware, ModelRequest, ModelResponse
from langchain_core.messages import ToolMessage
from langgraph.prebuilt.tool_node import ToolCallRequest
from langgraph.types import Command

from octop.infra.agents.tool_catalog import BUILTIN_TOOL_CATALOG
from octop.infra.backend.project_task_file_paths import (
    ProjectTaskListingOverflowError,
    ProjectTaskPathError,
    assert_listing_subtree_reparse_free,
    assert_plain_components,
    assert_plain_final,
    assert_plain_search_base,
)

PROJECT_TASK_FILE_TOOLS: frozenset[str] = frozenset(
    {
        "ls",
        "read_file",
        "write_file",
        "edit_file",
        "glob",
        "grep",
    }
)

_TOOLS_OUTSIDE_CATALOG: frozenset[str] = frozenset(
    {
        "task",
        "write_todos",
        "delete",
        "execute",
        "ask_user_question",
        "agent_list",
        "ask_agent",
        "acp_runner",
    }
)


def project_task_file_tools_disabled() -> frozenset[str]:
    """Denylist superset for ``HarnessAgentConfig.tools_disabled``.

    Built from the full builtin catalog plus harness tools that are mounted
    outside it. Unlike ``tool_catalog.normalize_tools_disabled`` this keeps
    ``task`` and ``write_todos`` listed, because they must stay hidden from a
    controlled file runtime.
    """
    names = {entry.name for entry in BUILTIN_TOOL_CATALOG}
    return frozenset((names | _TOOLS_OUTSIDE_CATALOG) - PROJECT_TASK_FILE_TOOLS)


def project_task_file_backend_spec(root_dir: str | Path) -> dict[str, Any]:
    """Return the fixed virtual filesystem spec pinned to *root_dir*."""
    return {
        "type": "filesystem",
        "root_dir": str(root_dir),
        "virtual_mode": True,
    }


def _tool_name(tool: Any) -> str:
    if isinstance(tool, dict):
        function = tool.get("function")
        if isinstance(function, dict) and function.get("name"):
            return str(function["name"])
        return str(tool.get("name") or "")
    return str(getattr(tool, "name", "") or "")


# ---------------------------------------------------------------------------
# 030A S4: tool argument contract (IT-1 pinned field matrix) and the tool
# virtual-path converter. This is NOT the HTTP normalizer: no strip, no
# backslash translation, single leading "/" is the virtual root anchor.
# ---------------------------------------------------------------------------

_DRIVE_RE = re.compile(r"^[A-Za-z]:")
_SCHEME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9+.-]*://")


def _is_str(value: Any) -> bool:
    return isinstance(value, str)


def _is_bool(value: Any) -> bool:
    return isinstance(value, bool)


def _is_int(value: Any) -> bool:
    # bool is an int subclass — True must not pass as offset/limit/max_count.
    return isinstance(value, int) and not isinstance(value, bool)


def _is_opt_str(value: Any) -> bool:
    return value is None or isinstance(value, str)


def _is_opt_int(value: Any) -> bool:
    return value is None or _is_int(value)


#: Required/optional field matrix pinned by IT-1 from the installed deepagents
#: ``middleware/filesystem.py`` schemas (LsSchema, ReadFileSchema,
#: WriteFileSchema, EditFileSchema, GlobSchema, GrepSchema). The
#: ``output_mode`` Literal VALUES are deliberately not re-pinned here — the
#: installed tool schema stays the value source of truth; only its type is
#: checked. Any unknown field, wrong type or missing required field fails
#: closed (A6: schema drift never widens the surface).
_TOOL_ARG_SCHEMAS: dict[
    str, tuple[dict[str, Callable[[Any], bool]], dict[str, Callable[[Any], bool]]]
] = {
    "ls": ({"path": _is_str}, {}),
    "read_file": ({"file_path": _is_str}, {"offset": _is_int, "limit": _is_int}),
    "write_file": ({"file_path": _is_str, "content": _is_str}, {}),
    "edit_file": (
        {"file_path": _is_str, "old_string": _is_str, "new_string": _is_str},
        {"replace_all": _is_bool},
    ),
    "glob": ({"pattern": _is_str}, {"path": _is_opt_str}),
    "grep": (
        {"pattern": _is_str},
        {
            "path": _is_opt_str,
            "glob": _is_opt_str,
            "output_mode": _is_str,
            "max_count": _is_opt_int,
        },
    ),
}


def _rel_parts(raw: str) -> list[str]:
    """Lexical "/"-split keeping whitespace, collapsing only ""/"." parts."""
    return [part for part in raw.split("/") if part not in ("", ".")]


def _reject_host_shapes(raw: str) -> None:
    """Fail closed on every host-path shape (both legal tool forms stay intact).

    ``//``/backslash/NUL are rejected on the raw form first; the single leading
    ``/`` virtual anchor is then stripped for a second pass so ``/C:/...``,
    ``/C:relative``, ``/file://...`` and ``/~/...`` cannot smuggle a host
    shape past the anchor.
    """
    if "\x00" in raw:
        raise ProjectTaskPathError("path must not contain NUL bytes")
    if "\\" in raw:
        raise ProjectTaskPathError("backslashes are not allowed in tool paths")
    if raw.startswith("//"):
        raise ProjectTaskPathError("double leading separators are not allowed")
    virtual = raw[1:] if raw.startswith("/") else raw
    if virtual.startswith("~"):
        raise ProjectTaskPathError("home paths are not allowed")
    if _DRIVE_RE.match(virtual):
        raise ProjectTaskPathError("Windows drive paths are not allowed")
    if _SCHEME_RE.match(virtual):
        raise ProjectTaskPathError("URL schemes are not allowed")
    if virtual.startswith("//"):
        raise ProjectTaskPathError("double leading separators are not allowed")


def _tool_rel(raw: Any) -> str:
    """Map a tool virtual path to a root-relative fragment (or ``"."`` = root).

    Legal forms (both get the identical S1/S2/S3 checks): single-leading-``/``
    virtual absolute paths and bare-relative fragments. ``/`` (or ``""``) alone
    maps to ``"."`` — the root is only valid as a directory/search root, and
    content tools refuse it downstream. No strip: whitespace inside names is
    preserved (P3 closure). UNC/backslash, drives including ``C:relative``,
    schemes, ``~``, NUL and any independent ``..`` component refuse.
    """
    if not isinstance(raw, str):
        raise ProjectTaskPathError("tool path arguments must be strings")
    _reject_host_shapes(raw)
    text = raw[1:] if raw.startswith("/") else raw
    parts = _rel_parts(text)
    if any(part == ".." for part in parts):
        raise ProjectTaskPathError("path traversal is not allowed")
    if not parts:
        return "."
    return "/".join(parts)


def _checked_pattern(raw: Any) -> str:
    """Pattern-only check for ``glob.pattern`` / ``grep.glob``.

    NEVER fed through the file-path normalizer: the single leading ``/``
    virtual-root anchor and wildcards are preserved verbatim (A6 positive).
    Independent ``..`` segments and host shapes refuse; ``grep.pattern`` is
    literal search text and is never passed here.
    """
    if not isinstance(raw, str):
        raise ProjectTaskPathError("pattern arguments must be strings")
    _reject_host_shapes(raw)
    if any(part == ".." for part in _rel_parts(raw)):
        raise ProjectTaskPathError("path traversal is not allowed in patterns")
    return raw


def _args_match_schema(name: str, args: dict[str, Any]) -> bool:
    required, optional = _TOOL_ARG_SCHEMAS[name]
    for key, validator in required.items():
        if key not in args or not validator(args[key]):
            return False
    for key, value in args.items():
        if key in required:
            continue
        optional_validator = optional.get(key)
        if optional_validator is None or not optional_validator(value):
            return False
    return True


class ProjectTaskFileToolBoundaryMiddleware(AgentMiddleware[Any, Any]):
    """Hide and veto every tool outside the six-name project-task allowlist.

    Constructed with the managed *root* (030A S4), the guard additionally
    enforces the IT-1 argument contract and runs the S1 ancestor-chain walk,
    the S2 final-component type proof and — for the listing tools ``ls`` /
    ``glob`` / ``grep`` — the bounded S3 reparse-free subtree scan BEFORE the
    handler executes. Because the installed backend resolves ``root_dir`` at
    construction time, this per-call walk on the literal root is what catches
    a running-root or managed-ancestor replacement (A15/A16). Without a root
    the guard degrades to the name-only veto (legacy behavior). Rejections are
    generic error ToolMessages: host paths and argument values are never
    echoed back to the model. ``max_entries`` injects the S3 scan cap
    (default: the module-level :data:`PROJECT_TASK_LISTING_MAX_ENTRIES`).
    """

    def __init__(self, root: str | Path | None = None, *, max_entries: int | None = None) -> None:
        self._root: Path | None = None if root is None else Path(root)
        self._max_entries = max_entries

    @property
    def root(self) -> Path | None:
        """The injected managed root (``None`` → name-only veto mode)."""
        return self._root

    def _filter_request(self, request: ModelRequest[Any]) -> ModelRequest[Any]:
        tools = [
            tool for tool in (request.tools or []) if _tool_name(tool) in PROJECT_TASK_FILE_TOOLS
        ]
        return request.override(tools=tools)

    def wrap_model_call(
        self,
        request: ModelRequest[Any],
        handler: Callable[[ModelRequest[Any]], ModelResponse[Any]],
    ) -> ModelResponse[Any]:
        return handler(self._filter_request(request))

    async def awrap_model_call(
        self,
        request: ModelRequest[Any],
        handler: Callable[[ModelRequest[Any]], Awaitable[ModelResponse[Any]]],
    ) -> ModelResponse[Any]:
        return await handler(self._filter_request(request))

    @staticmethod
    def _blocked(name: str, tool_call: Any, message: str) -> ToolMessage:
        return ToolMessage(
            content=message,
            tool_call_id=str(tool_call.get("id") or ""),
            name=name,
            status="error",
        )

    def _path_veto(self, root: Path, name: str, args: dict[str, Any]) -> None:
        """Run the S1/S2(/S3) proofs for one allowlisted call; raise to refuse."""
        if name == "ls":
            self._prove_dir_target(root, _tool_rel(args["path"]))
        elif name in ("read_file", "write_file", "edit_file"):
            rel = _tool_rel(args["file_path"])
            if rel == ".":
                # "/" or "" is a directory/search root, never a file target
                # (RD-4 positive/negative pair).
                raise ProjectTaskPathError("the workspace root is not a file target")
            assert_plain_components(root, rel)
            assert_plain_final(root, rel, "file")
        elif name == "glob":
            pattern = _checked_pattern(args["pattern"])
            base_rel = "." if args.get("path") is None else _tool_rel(args["path"])
            # A leading "/" in the pattern re-anchors the search to the
            # virtual root, so the S3 scan must cover the whole root then.
            self._prove_dir_target(root, base_rel, scan_root=pattern.startswith("/"))
        elif name == "grep":
            # grep.pattern is literal search text — never path-checked.
            glob_filter = args.get("glob")
            if glob_filter is not None:
                _checked_pattern(glob_filter)
            base_rel = "." if args.get("path") is None else _tool_rel(args["path"])
            # The installed backend searches a single regular file OR a
            # directory: the file form gets S1/S2 only, the directory form also
            # gets the bounded S3 subtree scan.
            assert_plain_search_base(root, base_rel, max_entries=self._max_entries)
        else:  # pragma: no cover — the allowlist gate runs before this
            raise ProjectTaskPathError(f"unknown tool {name!r}")

    def _prove_dir_target(self, root: Path, rel: str, *, scan_root: bool = False) -> None:
        """S1 + S2(dir) + bounded S3 subtree proof for a listing base."""
        assert_plain_components(root, rel)
        assert_plain_final(root, rel, "dir")
        base = root if scan_root else root.joinpath(*_rel_parts(rel))
        assert_listing_subtree_reparse_free(base, max_entries=self._max_entries)

    def _rejection(self, request: ToolCallRequest) -> ToolMessage | None:
        tool_call = request.tool_call
        name = str(tool_call.get("name") or "")
        if name not in PROJECT_TASK_FILE_TOOLS:
            allowed = ", ".join(sorted(PROJECT_TASK_FILE_TOOLS))
            return ToolMessage(
                content=(
                    f"Error: {name or '<unknown>'} is not available in a controlled file task. "
                    f"Allowed tools: {allowed}."
                ),
                tool_call_id=str(tool_call.get("id") or ""),
                name=name or "project_task_file_boundary",
                status="error",
            )
        root = self._root
        if root is None:
            return None
        args = tool_call.get("args")
        if not isinstance(args, dict) or not _args_match_schema(name, args):
            return self._blocked(
                name,
                tool_call,
                f"Error: {name} was called with invalid arguments; the project-task "
                "file boundary blocked the call.",
            )
        try:
            self._path_veto(root, name, args)
        except ProjectTaskListingOverflowError:
            return self._blocked(
                name,
                tool_call,
                f"Error: {name} was blocked because the managed listing subtree exceeds "
                "the bounded proof cap.",
            )
        except (ProjectTaskPathError, OSError):
            # ProjectTaskPathError covers every lexical/no-follow refusal;
            # OSError is caught fail-closed even though the assertions wrap
            # their own probe errors.
            return self._blocked(
                name,
                tool_call,
                f"Error: {name} cannot access the requested path in a controlled file task.",
            )
        return None

    def wrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], ToolMessage | Command[Any]],
    ) -> ToolMessage | Command[Any]:
        rejection = self._rejection(request)
        if rejection is not None:
            return rejection
        return handler(request)

    async def awrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], Awaitable[ToolMessage | Command[Any]]],
    ) -> ToolMessage | Command[Any]:
        # The S1/S2/S3 proofs are blocking filesystem walks: run them in a
        # worker thread (AGENTS.md: no blocking I/O inside the event loop).
        rejection = await asyncio.to_thread(self._rejection, request)
        if rejection is not None:
            return rejection
        return await handler(request)


def apply_project_task_file_boundary(
    cfg: HarnessAgentConfig,
    *,
    root_dir: str | Path,
) -> HarnessAgentConfig:
    """Harden a private config for a managed project-task file runtime.

    Pins a virtual filesystem backend, clears every tool source outside the
    allowlist, disables subagents/teams/plugins/MCP/ACP/media/browser/web search
    and mounts :class:`ProjectTaskFileToolBoundaryMiddleware` last so it sees
    tools injected by any host middleware. The guard is constructed WITH the
    managed root so the S4 argument contract and the S1/S2/S3 proofs run on
    every tool call (030A). The input config is not mutated.
    """
    guard = ProjectTaskFileToolBoundaryMiddleware(root=root_dir)
    return replace(
        cfg,
        backend=project_task_file_backend_spec(root_dir),
        tools_disabled=project_task_file_tools_disabled(),
        subagents_auto_load=False,
        subagents=None,
        subagents_path=None,
        tools=None,
        middleware=[*(cfg.middleware or []), guard],
        mcp_server_configs={},
        skills_dir=None,
        skills_disabled=frozenset(),
        acp_runners={},
        acp_delegate_enabled=False,
        media_generation=None,
        team_enabled=False,
        team_peers=(),
        ask_user_enabled=False,
        todos_enabled=False,
        web_search_tools=False,
        deferred_tools=frozenset(),
        defer_mcp_tools=False,
    )


__all__ = [
    "PROJECT_TASK_FILE_TOOLS",
    "ProjectTaskFileToolBoundaryMiddleware",
    "apply_project_task_file_boundary",
    "project_task_file_backend_spec",
    "project_task_file_tools_disabled",
]
