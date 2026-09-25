"""Project-space business rules: validation, authorization, view assembly.

Access is membership-based: a project is visible only to its members. Users
who are not members get ``NOT_FOUND`` even for known project ids so existence
is never leaked; members without edit rights get ``FORBIDDEN`` on updates.
"""

from __future__ import annotations

import hashlib
import secrets
from dataclasses import dataclass
from typing import Any

from octop.infra.db.repos._base import now_ts
from octop.infra.errors import ErrorCode, OctopError
from octop.infra.utils.ulid import new_ulid

MAX_PROJECT_NAME_LENGTH = 15
MAX_PROJECT_DESCRIPTION_LENGTH = 2000
MAX_PROJECT_INSTRUCTIONS_LENGTH = 2000
MAX_PROJECT_EXPERTS = 20
DEFAULT_PAGE_LIMIT = 20
MAX_PAGE_LIMIT = 100

ROLE_OWNER = "owner"
ROLE_ADMIN = "admin"
ROLE_MEMBER = "member"
_EDIT_ROLES = frozenset({ROLE_OWNER, ROLE_ADMIN})
_ASSIGNABLE_ROLES = frozenset({ROLE_MEMBER, ROLE_ADMIN})

INVITE_MIN_EXPIRES_DAYS = 1
INVITE_MAX_EXPIRES_DAYS = 7
INVITE_DEFAULT_EXPIRES_DAYS = 7
_JOIN_REQUEST_STATUSES = frozenset({"pending", "approved", "rejected"})


def hash_invite_token(token: str) -> str:
    """SHA-256 hex digest — the only form of an invite token ever persisted."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def validate_project_name(raw: str) -> str:
    """Trim, then require 1–15 Unicode codepoints (spec-fixed bound)."""
    name = raw.strip()
    if not name or len(name) > MAX_PROJECT_NAME_LENGTH:
        raise ValueError(f"project name must be 1-{MAX_PROJECT_NAME_LENGTH} characters after trim")
    return name


def validate_project_text(raw: str, *, field: str, max_length: int) -> str:
    text = raw.strip()
    if len(text) > max_length:
        raise ValueError(f"project {field} must be at most {max_length} characters")
    return text


@dataclass(frozen=True)
class ProjectView:
    project_id: str
    name: str
    description: str
    my_role: str
    member_count: int
    created_at: int
    updated_at: int


@dataclass(frozen=True)
class ProjectDetailView(ProjectView):
    instructions: str


@dataclass(frozen=True)
class ProjectListPage:
    items: list[ProjectView]
    limit: int
    offset: int
    has_more: bool


@dataclass(frozen=True)
class ProjectMemberView:
    user_id: int
    username: str
    role: str


@dataclass(frozen=True)
class ProjectInviteView:
    """List/management view of an invite — never carries token material."""

    invite_id: str
    project_id: str
    role: str
    requires_approval: bool
    created_by: int
    created_at: int
    expires_at: int
    revoked_at: int | None
    consumed_at: int | None
    consumed_by_user_id: int | None
    status: str


@dataclass(frozen=True)
class ProjectInviteCreatedView(ProjectInviteView):
    """Returned once, at creation time; ``token`` is the plaintext link."""

    token: str


@dataclass(frozen=True)
class ProjectJoinRequestView:
    request_id: str
    project_id: str
    invite_id: str | None
    user_id: int
    username: str
    status: str
    requested_at: int
    resolved_at: int | None
    resolved_by: int | None


@dataclass(frozen=True)
class InviteAcceptanceView:
    status: str  # "joined" | "pending_approval"
    project_id: str
    role: str = ""
    request_id: str = ""


@dataclass(frozen=True)
class ProjectExpertView:
    """One masked project expert: unavailable rows carry no Agent profile."""

    agent_id: str
    name: str | None
    description: str | None
    status: str


@dataclass(frozen=True)
class ProjectExpertsView:
    revision: int
    items: list[ProjectExpertView]


class ProjectService:
    def __init__(self, services: Any) -> None:
        self._services = services

    @property
    def _repo(self) -> Any:
        return self._services.project_repo

    def create_project(
        self,
        *,
        creator_user_id: int,
        name: str,
        description: str = "",
        instructions: str = "",
    ) -> ProjectDetailView:
        clean_name = validate_project_name(name)
        clean_description = validate_project_text(
            description, field="description", max_length=MAX_PROJECT_DESCRIPTION_LENGTH
        )
        clean_instructions = validate_project_text(
            instructions, field="instructions", max_length=MAX_PROJECT_INSTRUCTIONS_LENGTH
        )
        row = self._repo.create_with_owner(
            creator_user_id=creator_user_id,
            name=clean_name,
            description=clean_description,
            instructions=clean_instructions,
        )
        # The transaction guarantees the owner membership and the creation
        # event, so role/count are known without re-reading.
        return ProjectDetailView(
            project_id=str(row.project_id),
            name=str(row.name),
            description=str(row.description),
            my_role=ROLE_OWNER,
            member_count=1,
            created_at=int(row.created_at),
            updated_at=int(row.updated_at),
            instructions=str(row.instructions),
        )

    def list_projects(
        self,
        *,
        user_id: int,
        q: str = "",
        limit: int = DEFAULT_PAGE_LIMIT,
        offset: int = 0,
    ) -> ProjectListPage:
        if not 1 <= limit <= MAX_PAGE_LIMIT:
            raise ValueError(f"limit must be between 1 and {MAX_PAGE_LIMIT}")
        if offset < 0:
            raise ValueError("offset must be >= 0")
        rows = self._repo.list_for_user(user_id, q=q.strip(), limit=limit, offset=offset)
        has_more = len(rows) > limit
        items = [
            ProjectView(
                project_id=str(r.project_id),
                name=str(r.name),
                description=str(r.description),
                my_role=str(r.my_role),
                member_count=int(r.member_count),
                created_at=int(r.created_at),
                updated_at=int(r.updated_at),
            )
            for r in rows[:limit]
        ]
        return ProjectListPage(items=items, limit=limit, offset=offset, has_more=has_more)

    def get_view(self, project_id: str, *, user_id: int) -> ProjectDetailView:
        project, membership = self._require_membership(project_id, user_id)
        return self._detail_view(project, role=str(membership.role))

    def update_project(
        self,
        project_id: str,
        *,
        actor_user_id: int,
        name: str | None = None,
        description: str | None = None,
        instructions: str | None = None,
    ) -> ProjectDetailView:
        self._require_membership(project_id, actor_user_id, edit=True)
        clean_name = validate_project_name(name) if name is not None else None
        clean_description = (
            validate_project_text(
                description,
                field="description",
                max_length=MAX_PROJECT_DESCRIPTION_LENGTH,
            )
            if description is not None
            else None
        )
        clean_instructions = (
            validate_project_text(
                instructions,
                field="instructions",
                max_length=MAX_PROJECT_INSTRUCTIONS_LENGTH,
            )
            if instructions is not None
            else None
        )
        if clean_name is None and clean_description is None and clean_instructions is None:
            return self.get_view(project_id, user_id=actor_user_id)
        self._repo.update_project(
            project_id,
            actor_user_id=actor_user_id,
            name=clean_name,
            description=clean_description,
            instructions=clean_instructions,
        )
        return self.get_view(project_id, user_id=actor_user_id)

    def list_members(self, project_id: str, *, user_id: int) -> list[ProjectMemberView]:
        self._require_membership(project_id, user_id)
        rows = self._repo.list_members(project_id)
        return [
            ProjectMemberView(user_id=int(r.user_id), username=str(r.username), role=str(r.role))
            for r in rows
        ]

    # ------------------------------------------------------------------
    # Versioned expert selection (029)
    # ------------------------------------------------------------------

    def list_experts(self, project_id: str, *, user_id: int) -> ProjectExpertsView:
        """Members read the live masked list; outsiders get the uniform 404."""
        self._require_membership(project_id, user_id)
        selection = self._repo.get_experts(project_id)
        if selection is None:
            raise OctopError(ErrorCode.NOT_FOUND, "project not found")
        return self._experts_view(selection)

    def set_experts(
        self,
        project_id: str,
        *,
        actor_user_id: int,
        expected_revision: int,
        agent_ids: list[str],
    ) -> ProjectExpertsView:
        """Owner/admin replace the ordered shared-expert list.

        The repository re-checks membership, role, archive state, the expected
        revision, and every Agent's shared/enabled/kind state inside one write
        transaction before deleting or inserting anything. Stale revisions
        answer 409 ``PROJECT_EXPERTS_CHANGED`` with no writes; an identical
        ordered list is a no-op; a real change bumps the revision once.
        """
        self._require_membership(project_id, actor_user_id, edit=True)
        if expected_revision < 0 or len(agent_ids) > MAX_PROJECT_EXPERTS:
            raise OctopError(ErrorCode.PROJECT_EXPERT_INVALID, "project expert list is invalid")
        if len(set(agent_ids)) != len(agent_ids):
            raise OctopError(ErrorCode.PROJECT_EXPERT_INVALID, "project expert list has duplicates")
        mutation = self._repo.replace_experts(
            project_id=project_id,
            actor_user_id=actor_user_id,
            expected_revision=expected_revision,
            agent_ids=agent_ids,
        )
        if mutation.outcome == "not_member":
            raise OctopError(ErrorCode.NOT_FOUND, "project not found")
        if mutation.outcome == "forbidden":
            raise OctopError(ErrorCode.FORBIDDEN, "project update requires owner or admin role")
        if mutation.outcome == "archived":
            raise OctopError(ErrorCode.FORBIDDEN, "project is archived")
        if mutation.outcome == "stale":
            raise OctopError(
                ErrorCode.PROJECT_EXPERTS_CHANGED,
                "project experts changed since they were confirmed",
            )
        if mutation.outcome == "invalid_agent":
            raise OctopError(
                ErrorCode.PROJECT_EXPERT_INVALID,
                "selected agents are not all shared, enabled experts",
            )
        selection = self._repo.get_experts(project_id)
        if selection is None:
            raise OctopError(ErrorCode.NOT_FOUND, "project not found")
        return self._experts_view(selection)

    @staticmethod
    def _experts_view(selection: Any) -> ProjectExpertsView:
        return ProjectExpertsView(
            revision=int(selection.revision),
            items=[
                ProjectExpertView(
                    agent_id=str(row.agent_id),
                    name=row.name,
                    description=row.description,
                    status=str(row.status),
                )
                for row in selection.items
            ],
        )

    # ------------------------------------------------------------------
    # Invites
    # ------------------------------------------------------------------

    def create_invite(
        self,
        project_id: str,
        *,
        actor_user_id: int,
        requires_approval: bool = False,
        expires_in_days: int = INVITE_DEFAULT_EXPIRES_DAYS,
    ) -> ProjectInviteCreatedView:
        """Create a one-use invite; the plaintext token is returned exactly once."""
        self._require_membership(project_id, actor_user_id, edit=True)
        if not INVITE_MIN_EXPIRES_DAYS <= expires_in_days <= INVITE_MAX_EXPIRES_DAYS:
            raise ValueError(
                f"expires_in_days must be between {INVITE_MIN_EXPIRES_DAYS} "
                f"and {INVITE_MAX_EXPIRES_DAYS}"
            )
        token = secrets.token_urlsafe(32)
        ts = now_ts()
        row = self._repo.create_invite(
            invite_id=new_ulid(),
            project_id=project_id,
            token_hash=hash_invite_token(token),
            created_by=actor_user_id,
            role=ROLE_MEMBER,
            requires_approval=requires_approval,
            created_at=ts,
            expires_at=ts + expires_in_days * 86400,
        )
        view = self._invite_view(row)
        return ProjectInviteCreatedView(
            invite_id=view.invite_id,
            project_id=view.project_id,
            role=view.role,
            requires_approval=view.requires_approval,
            created_by=view.created_by,
            created_at=view.created_at,
            expires_at=view.expires_at,
            revoked_at=view.revoked_at,
            consumed_at=view.consumed_at,
            consumed_by_user_id=view.consumed_by_user_id,
            status=view.status,
            token=token,
        )

    def list_invites(self, project_id: str, *, actor_user_id: int) -> list[ProjectInviteView]:
        self._require_membership(project_id, actor_user_id, edit=True)
        return [self._invite_view(row) for row in self._repo.list_invites(project_id)]

    def revoke_invite(
        self, project_id: str, invite_id: str, *, actor_user_id: int
    ) -> ProjectInviteView:
        self._require_membership(project_id, actor_user_id, edit=True)
        outcome = self._repo.revoke_invite(project_id, invite_id, actor_user_id=actor_user_id)
        if outcome == "missing":
            raise OctopError(ErrorCode.NOT_FOUND, "project invite not found")
        row = self._repo.get_invite(project_id, invite_id)
        if row is None:
            raise OctopError(ErrorCode.NOT_FOUND, "project invite not found")
        if outcome != "revoked":
            raise OctopError(
                ErrorCode.INVITE_INVALID,
                f"project invite cannot be revoked ({outcome})",
                status=409,
                details={"reason": outcome},
            )
        return self._invite_view(row)

    def accept_invite(self, token: str, *, user_id: int) -> InviteAcceptanceView:
        """Redeem a plaintext token for the current logged-in user.

        Error responses never disclose which project (if any) the token
        belonged to: unknown tokens are indistinguishable from garbage.
        """
        clean = token.strip()
        if not clean:
            raise OctopError(ErrorCode.INVITE_INVALID, "invite link is not valid")
        result = self._repo.redeem_invite(token_hash=hash_invite_token(clean), user_id=user_id)
        if result.outcome == "joined":
            return InviteAcceptanceView(
                status="joined", project_id=result.project_id, role=result.role
            )
        if result.outcome == "requested":
            return InviteAcceptanceView(
                status="pending_approval",
                project_id=result.project_id,
                request_id=result.request_id,
            )
        if result.outcome == "revoked":
            raise OctopError(ErrorCode.INVITE_REVOKED, "invite link was revoked", status=410)
        if result.outcome == "expired":
            raise OctopError(ErrorCode.INVITE_EXPIRED, "invite link has expired", status=410)
        if result.outcome == "already_member":
            raise OctopError(
                ErrorCode.INVITE_USED,
                "user is already a project member",
                status=409,
                details={"reason": "already_member"},
            )
        if result.outcome == "pending_exists":
            raise OctopError(
                ErrorCode.INVITE_USED,
                "a join request is already pending",
                status=409,
                details={"reason": "pending_request_exists"},
            )
        if result.outcome == "used":
            raise OctopError(
                ErrorCode.INVITE_USED,
                "invite link was already used",
                status=409,
                details={"reason": "invite_used"},
            )
        raise OctopError(ErrorCode.INVITE_INVALID, "invite link is not valid")

    # ------------------------------------------------------------------
    # Join requests
    # ------------------------------------------------------------------

    def list_join_requests(
        self, project_id: str, *, actor_user_id: int, status: str | None = None
    ) -> list[ProjectJoinRequestView]:
        self._require_membership(project_id, actor_user_id, edit=True)
        if status is not None and status not in _JOIN_REQUEST_STATUSES:
            raise ValueError("status must be one of pending, approved, rejected")
        rows = self._repo.list_join_requests(project_id, status=status)
        return [self._request_view(row) for row in rows]

    def approve_join_request(
        self, project_id: str, request_id: str, *, actor_user_id: int
    ) -> ProjectJoinRequestView:
        return self._resolve_join_request(project_id, request_id, actor_user_id, approve=True)

    def reject_join_request(
        self, project_id: str, request_id: str, *, actor_user_id: int
    ) -> ProjectJoinRequestView:
        return self._resolve_join_request(project_id, request_id, actor_user_id, approve=False)

    # ------------------------------------------------------------------
    # Member management
    # ------------------------------------------------------------------

    def set_member_role(
        self, project_id: str, user_id: int, *, role: str, actor_user_id: int
    ) -> ProjectMemberView:
        """Owner-only promotion/demotion between ``member`` and ``admin``."""
        _project, actor = self._require_membership(project_id, actor_user_id)
        if role not in _ASSIGNABLE_ROLES:
            raise ValueError("role must be 'member' or 'admin'")
        if str(actor.role) != ROLE_OWNER:
            raise OctopError(ErrorCode.FORBIDDEN, "only the project owner can change member roles")
        mutation = self._repo.set_member_role(
            project_id=project_id,
            user_id=user_id,
            role=role,
            actor_user_id=actor_user_id,
            forbid_roles=(ROLE_OWNER,),
        )
        if mutation.outcome == "missing":
            raise OctopError(ErrorCode.NOT_FOUND, "project member not found")
        if mutation.outcome == "forbidden_role":
            raise OctopError(
                ErrorCode.FORBIDDEN, f"the project {mutation.role} role cannot be changed"
            )
        if mutation.outcome == "stale":
            raise OctopError(
                ErrorCode.INVITE_INVALID,
                "project membership changed; retry",
                status=409,
                details={"reason": "membership_changed"},
            )
        member = self._repo.get_member_user(project_id, user_id)
        if member is None:
            raise OctopError(ErrorCode.NOT_FOUND, "project member not found")
        return ProjectMemberView(
            user_id=int(member.user_id), username=str(member.username), role=str(member.role)
        )

    def remove_member(self, project_id: str, user_id: int, *, actor_user_id: int) -> None:
        """Owner/admin may remove a ``member``; only the owner may remove an
        ``admin``; nobody may remove the owner (including the owner themself).
        """
        _project, actor = self._require_membership(project_id, actor_user_id, edit=True)
        forbid = (ROLE_OWNER,) if str(actor.role) == ROLE_OWNER else (ROLE_OWNER, ROLE_ADMIN)
        mutation = self._repo.remove_member(
            project_id=project_id,
            user_id=user_id,
            actor_user_id=actor_user_id,
            forbid_roles=forbid,
        )
        if mutation.outcome == "missing":
            raise OctopError(ErrorCode.NOT_FOUND, "project member not found")
        if mutation.outcome == "forbidden_role":
            raise OctopError(
                ErrorCode.FORBIDDEN, f"cannot remove a member with the {mutation.role} role"
            )
        if mutation.outcome == "stale":
            raise OctopError(
                ErrorCode.INVITE_INVALID,
                "project membership changed; retry",
                status=409,
                details={"reason": "membership_changed"},
            )

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _resolve_join_request(
        self, project_id: str, request_id: str, actor_user_id: int, *, approve: bool
    ) -> ProjectJoinRequestView:
        self._require_membership(project_id, actor_user_id, edit=True)
        resolution = self._repo.resolve_join_request(
            project_id=project_id,
            request_id=request_id,
            resolver_user_id=actor_user_id,
            approve=approve,
        )
        if resolution.outcome == "missing" or resolution.request is None:
            raise OctopError(ErrorCode.NOT_FOUND, "join request not found")
        if resolution.outcome == "resolved":
            raise OctopError(
                ErrorCode.INVITE_INVALID,
                "join request is already resolved",
                status=409,
                details={
                    "reason": "request_already_resolved",
                    "status": str(resolution.request.status),
                },
            )
        if resolution.outcome == "already_member":
            raise OctopError(
                ErrorCode.INVITE_USED,
                "user is already a project member",
                status=409,
                details={"reason": "already_member"},
            )
        return self._request_view(resolution.request)

    def _invite_view(self, row: Any) -> ProjectInviteView:
        return ProjectInviteView(
            invite_id=str(row.invite_id),
            project_id=str(row.project_id),
            role=str(row.role),
            requires_approval=bool(row.requires_approval),
            created_by=int(row.created_by),
            created_at=int(row.created_at),
            expires_at=int(row.expires_at),
            revoked_at=(None if row.revoked_at is None else int(row.revoked_at)),
            consumed_at=(None if row.consumed_at is None else int(row.consumed_at)),
            consumed_by_user_id=(
                None if row.consumed_by_user_id is None else int(row.consumed_by_user_id)
            ),
            status=str(row.status()),
        )

    def _request_view(self, row: Any) -> ProjectJoinRequestView:
        return ProjectJoinRequestView(
            request_id=str(row.request_id),
            project_id=str(row.project_id),
            invite_id=(None if row.invite_id is None else str(row.invite_id)),
            user_id=int(row.user_id),
            username=str(row.username),
            status=str(row.status),
            requested_at=int(row.requested_at),
            resolved_at=(None if row.resolved_at is None else int(row.resolved_at)),
            resolved_by=(None if row.resolved_by is None else int(row.resolved_by)),
        )

    def _require_membership(
        self, project_id: str, user_id: int, *, edit: bool = False
    ) -> tuple[Any, Any]:
        """Return ``(project, membership)`` or raise the spec-mandated error.

        Non-members (and missing projects) both map to ``NOT_FOUND``; members
        without edit rights map to ``FORBIDDEN`` only when ``edit`` is set.
        """
        project = self._repo.get_project(project_id)
        if project is None:
            raise OctopError(ErrorCode.NOT_FOUND, "project not found")
        membership = self._repo.get_membership(project_id, user_id)
        if membership is None:
            raise OctopError(ErrorCode.NOT_FOUND, "project not found")
        if edit and str(membership.role) not in _EDIT_ROLES:
            raise OctopError(ErrorCode.FORBIDDEN, "project update requires owner or admin role")
        return project, membership

    def _detail_view(self, project: Any, *, role: str) -> ProjectDetailView:
        return ProjectDetailView(
            project_id=str(project.project_id),
            name=str(project.name),
            description=str(project.description),
            my_role=role,
            member_count=int(self._repo.count_members(str(project.project_id))),
            created_at=int(project.created_at),
            updated_at=int(project.updated_at),
            instructions=str(project.instructions),
        )
