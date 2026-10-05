"""Personal thread organization, independent of execution and project grants."""

from __future__ import annotations

from dataclasses import dataclass

from octop.infra.db.repos._base import now_ts
from octop.infra.db.repos.threads import ArchivedThreadSummary, ArchiveMutationRow
from octop.infra.db.services import SharedServices
from octop.infra.errors import ErrorCode, OctopError


@dataclass(frozen=True)
class ArchivePage:
    items: list[ArchivedThreadSummary]
    limit: int
    offset: int
    has_more: bool


class ThreadArchiveService:
    def __init__(self, services: SharedServices) -> None:
        self._repo = services.thread_repo

    @staticmethod
    def _real_actor(as_user: str | None) -> None:
        if as_user is not None:
            raise OctopError.localized(ErrorCode.FORBIDDEN)

    def set_archive(
        self,
        *,
        thread_id: str,
        user_id: int,
        actor_is_admin: bool,
        archived: bool,
        as_user: str | None = None,
    ) -> ArchiveMutationRow:
        self._real_actor(as_user)
        row = self._repo.set_archive_owned(
            thread_id=thread_id,
            user_id=user_id,
            actor_is_admin=actor_is_admin,
            archived=archived,
            now=now_ts(),
        )
        if row is None:
            raise OctopError.localized(ErrorCode.NOT_FOUND)
        return row

    def list_archived(
        self,
        *,
        user_id: int,
        actor_is_admin: bool,
        q: str = "",
        limit: int = 20,
        offset: int = 0,
        as_user: str | None = None,
    ) -> ArchivePage:
        self._real_actor(as_user)
        if (
            len(q) > 256
            or "\x00" in q
            or type(limit) is not int
            or not 1 <= limit <= 100
            or type(offset) is not int
            or not 0 <= offset <= 9007199254740991
        ):
            raise ValueError("invalid archive page parameters")
        rows = self._repo.list_archived_by_user(
            user_id=user_id, actor_is_admin=actor_is_admin, q=q, limit=limit + 1, offset=offset
        )
        return ArchivePage(rows[:limit], limit, offset, len(rows) > limit)
