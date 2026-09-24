"""Project activity + message business rules (PS-03A).

Members read a safe, server-filtered timeline (``members`` = every whitelisted
event, ``related`` = events the caller acted, targeted, or was/had-been the
todo assignee of, plus their own messages) and publish plain-text messages.
Access mirrors :class:`octop.infra.projects.tasks.ProjectTaskService`:
non-members — including instance admins — get the same 404 as unknown
projects, and revoked members lose both routes immediately while their
historical messages stay visible to remaining members.

Pagination uses a strict opaque ``(created_at, id)`` cursor. The cursor is a
paging position, never an auth credential: membership is re-checked on every
page, and malformed cursors are rejected before any query.

Error contract:

* 404 ``NOT_FOUND`` — outsider, unknown project, revoked member (one uniform
  message so existence is never leaked);
* 422 ``PROJECT_ACTIVITY_CURSOR_INVALID`` — a cursor that is not canonical
  unpadded base64url of positive ``created_at:id``;
* ``ValueError`` — invalid scope/limit/message body (the HTTP layer
  pre-validates all three, so these never surface as 500s).
"""

from __future__ import annotations

import base64
import logging
import re
from dataclasses import dataclass
from typing import Any

from octop.infra.db.repos.project_activity import ACTIVITY_EVENT_TYPES, ActivityRow
from octop.infra.errors import ErrorCode, OctopError
from octop.infra.projects.service import DEFAULT_PAGE_LIMIT

logger = logging.getLogger(__name__)

MESSAGE_BODY_MAX_LENGTH = 4000
MAX_ACTIVITY_LIMIT = 50
ACTIVITY_SCOPES: tuple[str, ...] = ("members", "related")

_CURSOR_MAX_LENGTH = 64
_CURSOR_CHARSET = re.compile(r"^[A-Za-z0-9_-]+$")


@dataclass(frozen=True)
class ActivityItemView:
    """Public timeline item — exactly the contract's fixed fields, nothing more.

    ``object_id`` is ``None`` for member events (target ids stay internal),
    ``message_body`` is set only for message events, and both actor fields are
    ``None`` when the author was deleted. Never carries ``payload_json``,
    instructions, invite/join-request material, or private task data.
    """

    event_id: int
    event_type: str
    actor_user_id: int | None
    actor_name: str | None
    object_kind: str
    object_id: str | None
    message_body: str | None
    created_at: int


@dataclass(frozen=True)
class ActivityPage:
    items: list[ActivityItemView]
    next_cursor: str | None


def validate_message_body(raw: str) -> str:
    """Trim, then require 1–4000 characters (contract bound, CHECK-enforced)."""
    if not isinstance(raw, str):
        raise ValueError("message body must be plain text")
    body = raw.strip()
    if not body:
        raise ValueError("message body must not be empty")
    if len(body) > MESSAGE_BODY_MAX_LENGTH:
        raise ValueError(f"message body must be at most {MESSAGE_BODY_MAX_LENGTH} characters")
    return body


def encode_activity_cursor(created_at: int, event_id: int) -> str:
    """Canonical cursor: unpadded base64url of ``"<created_at>:<id>"``."""
    raw = f"{int(created_at)}:{int(event_id)}".encode("ascii")
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _cursor_invalid() -> OctopError:
    return OctopError(ErrorCode.PROJECT_ACTIVITY_CURSOR_INVALID, "activity cursor is not valid")


def decode_activity_cursor(raw: str) -> tuple[int, int]:
    """Strictly decode a cursor back to ``(created_at, event_id)``.

    Rejects anything that is not canonical unpadded base64url of two positive
    decimal integers joined by ``:`` (charset, length, padding, sign, leading
    zeros, and a re-encode round-trip are all checked), so no attacker-shaped
    value ever reaches SQL. The decoded pair is a seek position only — it
    grants no access and membership is re-checked on every page.
    """
    if not isinstance(raw, str) or not raw or len(raw) > _CURSOR_MAX_LENGTH:
        raise _cursor_invalid()
    if not _CURSOR_CHARSET.match(raw):
        raise _cursor_invalid()
    try:
        decoded = base64.urlsafe_b64decode(raw + "=" * (-len(raw) % 4)).decode("ascii")
    except Exception:
        raise _cursor_invalid() from None
    parts = decoded.split(":")
    if len(parts) != 2 or not all(p.isascii() and p.isdigit() for p in parts):
        raise _cursor_invalid()
    created_at, event_id = int(parts[0]), int(parts[1])
    if created_at <= 0 or event_id <= 0:
        raise _cursor_invalid()
    if encode_activity_cursor(created_at, event_id) != raw:
        # Non-canonical encoding (e.g. leading zeros): refuse it.
        raise _cursor_invalid()
    return created_at, event_id


def activity_item_view(row: ActivityRow) -> ActivityItemView | None:
    """Map one repo row to the public view; ``None`` for non-whitelisted rows.

    Defense in depth: the SQL whitelist is authoritative, but the view layer
    re-checks it so a future event kind can never ride an existing query into
    a response. Member target ids are hidden here.
    """
    if row.event_type not in ACTIVITY_EVENT_TYPES:
        return None
    return ActivityItemView(
        event_id=row.event_id,
        event_type=row.event_type,
        actor_user_id=row.actor_user_id,
        actor_name=row.actor_name,
        object_kind=row.object_kind,
        object_id=None if row.object_kind == "member" else row.object_id,
        message_body=row.message_body,
        created_at=row.created_at,
    )


def _project_not_found() -> OctopError:
    # Same message for outsiders and unknown projects: no existence leak.
    return OctopError(ErrorCode.NOT_FOUND, "project not found")


class ProjectActivityService:
    def __init__(self, services: Any) -> None:
        self._services = services

    @property
    def _repo(self) -> Any:
        return self._services.project_activity_repo

    @property
    def _project_repo(self) -> Any:
        return self._services.project_repo

    # ------------------------------------------------------------ access

    def _require_membership(self, project_id: str, user_id: int) -> None:
        """Members only; outsiders get the same 404 as unknown projects."""
        if self._project_repo.get_membership(project_id, user_id) is None:
            raise _project_not_found()

    # ------------------------------------------------------------ reads

    def list_activity(
        self,
        project_id: str,
        *,
        user_id: int,
        scope: str = "members",
        limit: int = DEFAULT_PAGE_LIMIT,
        cursor: str | None = None,
    ) -> ActivityPage:
        """One filtered, seek-paged timeline page for a current member."""
        if scope not in ACTIVITY_SCOPES:
            raise ValueError(f"scope must be one of {', '.join(ACTIVITY_SCOPES)}")
        if not 1 <= limit <= MAX_ACTIVITY_LIMIT:
            raise ValueError(f"limit must be between 1 and {MAX_ACTIVITY_LIMIT}")
        self._require_membership(project_id, user_id)
        before = decode_activity_cursor(cursor) if cursor is not None else None
        try:
            rows = self._repo.list_activity(
                project_id, user_id=user_id, scope=scope, limit=limit, before=before
            )
        except Exception:
            # Fail closed: malformed historical payload JSON surfaces as a
            # database error on the related scope. Log server-side and let the
            # generic handler answer 500 — payload text never reaches clients.
            logger.exception("project activity query failed for project %s", project_id)
            raise
        if rows is None:
            raise _project_not_found()
        has_more = len(rows) > limit
        rows = rows[:limit]
        items = [view for view in (activity_item_view(row) for row in rows) if view is not None]
        next_cursor = (
            encode_activity_cursor(rows[-1].created_at, rows[-1].event_id)
            if has_more and rows
            else None
        )
        return ActivityPage(items=items, next_cursor=next_cursor)

    # ------------------------------------------------------------ messages

    def post_message(self, project_id: str, *, user_id: int, body: str) -> ActivityItemView:
        """Publish one plain-text message as the caller; returns its view.

        The author is always ``user_id`` (the authenticated operator) — never
        request data. A concurrent removal between the membership check and
        the write transaction is re-caught by the repo and maps to the same
        uniform 404.
        """
        clean_body = validate_message_body(body)
        self._require_membership(project_id, user_id)
        created = self._repo.create_message(
            project_id=project_id, author_user_id=user_id, body=clean_body
        )
        if created.outcome == "not_member":
            raise _project_not_found()
        if created.row is None:  # pragma: no cover - created rows are read back
            raise OctopError(ErrorCode.INTERNAL_ERROR, "activity row missing after message create")
        view = activity_item_view(created.row)
        if view is None:  # pragma: no cover - message events are whitelisted
            raise OctopError(ErrorCode.INTERNAL_ERROR, "message event failed the whitelist recheck")
        return view
