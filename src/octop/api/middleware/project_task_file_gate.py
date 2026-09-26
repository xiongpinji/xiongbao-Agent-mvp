"""030A B4 default-deny HTTP ingress gate for internal project-task file runtimes.

A DB-marked ``runtime_kind='project_task_files'`` Agent is a private storage
runtime owned by exactly one user. Every HTTP route under
``/api/agents/{agent_id}`` (and every route selecting the agent through the
``X-Octop-Agent-Id`` header) is DENIED by default — including for admins and
including ``as_user`` impersonation — except the narrow owner-only allowlist:

* ``GET  /api/agents/{id}/threads``                       (list bound threads)
* ``GET  /api/agents/{id}/threads/{tid}/history``
* ``POST /api/agents/{id}/threads/{tid}/read``
* ``GET  /api/agents/{id}/workspace/tree``
* ``GET|PUT /api/agents/{id}/workspace/file``
* ``GET  /api/agents/{id}/workspace/download``
* ``GET|PUT /api/agents/{id}/workspace/doc``
* ``GET  /api/agents/{id}/workspace/glob``
* ``GET  /api/agents/{id}/workspace/grep``
* ``POST /api/agents/{id}/workspace/upload``
* ``GET  /api/agents/{id}/media/preview``

Allowed routes are re-validated in their handlers (thread ownership, strict
managed-root path confinement, metadata sanitization) and again in the
processor; this middleware is the outer fail-closed layer. WebSockets bypass
HTTP middleware entirely and are guarded in their handlers (chat ws, terminal
ws). ``DELETE /api/project-task-files/{thread_id}`` — the only complete-delete
route — lives outside ``/api/agents`` and is unaffected here.

Install BEFORE ``install_jwt_auth`` so the gate executes INSIDE JWT auth
(Starlette runs middleware in reverse registration order) and can read
``request.state.octop_user``.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable, Sequence
from typing import Any

from fastapi import Request
from fastapi.responses import JSONResponse

from octop.infra.db.repos.agents import RUNTIME_KIND_PROJECT_TASK_FILES
from octop.infra.errors import ErrorCode, OctopError

_INSTALL_ATTR = "_octop_project_task_file_gate_installed"
_AGENT_ID_HEADER = "x-octop-agent-id"

logger = logging.getLogger(__name__)


def refusal_response() -> JSONResponse:
    """Stable 030A refusal envelope (same shape as the B3 route guards)."""
    exc = OctopError(
        ErrorCode.FORBIDDEN,
        "internal project-task runtime is not manageable",
        details={"internal": True},
    )
    return JSONResponse(status_code=exc.status, content=exc.to_envelope())


def unavailable_response() -> JSONResponse:
    """Stable fail-closed envelope when the agent row cannot be read.

    A lookup failure must never read as "ordinary agent" (that would let a DB
    outage bypass the gate); the target is denied with a retryable 503 instead.
    """
    exc = OctopError(
        ErrorCode.PROJECT_TASK_FILES_UNAVAILABLE,
        "internal project-task runtime state is unavailable",
    )
    return JSONResponse(status_code=exc.status, content=exc.to_envelope())


def is_project_task_file_row(row: Any) -> bool:
    """True for DB-marked 030A internal project-task file runtimes."""
    return getattr(row, "runtime_kind", None) == RUNTIME_KIND_PROJECT_TASK_FILES


def path_agent_id(path: str) -> str | None:
    """Agent id segment of ``/api/agents/{agent_id}/...`` paths, else None."""
    parts = path.split("/")
    if len(parts) < 4 or parts[1] != "api" or parts[2] != "agents":
        return None
    segment = parts[3]
    return segment or None


def path_tail(path: str) -> tuple[str, ...]:
    """Path segments after ``/api/agents/{agent_id}`` (empty for the detail route)."""
    parts = path.split("/")
    if len(parts) < 4 or parts[1] != "api" or parts[2] != "agents":
        return ()
    return tuple(part for part in parts[4:] if part != "")


def is_allowed_internal_route(method: str, tail: Sequence[str]) -> bool:
    """Owner-only allowlist for internal runtimes (default deny)."""
    parts = tuple(tail)
    if method == "GET" and parts == ("threads",):
        return True
    if len(parts) == 3 and parts[0] == "threads":
        if method == "GET" and parts[2] == "history":
            return True
        if method == "POST" and parts[2] == "read":
            return True
    if method == "GET" and parts == ("media", "preview"):
        return True
    if len(parts) == 2 and parts[0] == "workspace":
        sub = parts[1]
        if method == "GET" and sub in ("tree", "download", "glob", "grep"):
            return True
        if method in ("GET", "PUT") and sub in ("file", "doc"):
            return True
        if method == "POST" and sub == "upload":
            return True
    return False


def _internal_row(server: Any, agent_id: str) -> Any | None:
    """Row for *agent_id* when it is an internal runtime; None if ordinary.

    Raises :class:`OctopError` (503) when the control plane is unbound or the
    lookup itself fails: an unreadable row must deny rather than masquerade as
    an ordinary agent, so a DB outage can never fail open.
    """
    runtime = getattr(server, "app_runtime", None)
    registry = getattr(runtime, "agent_registry", None)
    if registry is None:
        raise OctopError(
            ErrorCode.PROJECT_TASK_FILES_UNAVAILABLE,
            "control plane is unavailable",
        )
    try:
        row = registry.get_row(agent_id)
    except Exception as exc:  # noqa: BLE001 — fail closed on any lookup error
        logger.warning("project-task file gate: row lookup failed for %s", agent_id, exc_info=True)
        raise OctopError(
            ErrorCode.PROJECT_TASK_FILES_UNAVAILABLE,
            "control plane is unavailable",
        ) from exc
    return row if is_project_task_file_row(row) else None


def _refusal_for_request(request: Request, server: Any) -> JSONResponse | None:
    """403 refusal when this request targets an internal runtime without allowance."""
    candidates: list[str] = []
    header = request.headers.get(_AGENT_ID_HEADER)
    if header is not None and header.strip():
        candidates.append(header.strip())
    segment = path_agent_id(request.url.path)
    if segment is not None and segment not in candidates:
        candidates.append(segment)

    row: Any | None = None
    for agent_id in candidates:
        try:
            row = _internal_row(server, agent_id)
        except OctopError:
            # Unreadable target → deny before the handler can act; never treat
            # the unknown row as ordinary.
            return unavailable_response()
        if row is not None:
            break
    if row is None:
        return None

    if request.method == "OPTIONS":
        # CORS preflight carries no credentials and no data; the real request
        # is gated on its own.
        return None

    user = getattr(request.state, "octop_user", None)
    if user is None:
        # Fail closed: a jwt-exempt path must never reach an internal runtime.
        return refusal_response()
    if request.query_params.get("as_user") is not None:
        return refusal_response()
    try:
        owner_id = int(getattr(row, "user_id", None) or -1)
        caller_id = int(getattr(user, "id", None) or -2)
    except (TypeError, ValueError):
        return refusal_response()
    if owner_id < 0 or caller_id != owner_id:
        # Admins are denied too: an internal runtime is owner-only surface.
        return refusal_response()
    if is_allowed_internal_route(request.method, path_tail(request.url.path)):
        return None
    return refusal_response()


def install(app: Any, server: Any) -> None:
    """Register the default-deny gate (idempotent)."""
    if getattr(app, _INSTALL_ATTR, False):
        return
    setattr(app, _INSTALL_ATTR, True)

    @app.middleware("http")  # type: ignore[untyped-decorator]
    async def _project_task_file_gate(
        request: Request,
        call_next: Callable[[Request], Awaitable[Any]],
    ) -> Any:
        refusal = _refusal_for_request(request, server)
        if refusal is not None:
            return refusal
        return await call_next(request)
