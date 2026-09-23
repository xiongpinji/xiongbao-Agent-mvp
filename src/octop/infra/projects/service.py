"""Project-space business rules: validation, authorization, view assembly.

Access is membership-based: a project is visible only to its members. Users
who are not members get ``NOT_FOUND`` even for known project ids so existence
is never leaked; members without edit rights get ``FORBIDDEN`` on updates.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from octop.infra.errors import ErrorCode, OctopError

MAX_PROJECT_NAME_LENGTH = 15
MAX_PROJECT_DESCRIPTION_LENGTH = 2000
MAX_PROJECT_INSTRUCTIONS_LENGTH = 2000
DEFAULT_PAGE_LIMIT = 20
MAX_PAGE_LIMIT = 100

ROLE_OWNER = "owner"
ROLE_ADMIN = "admin"
ROLE_MEMBER = "member"
_EDIT_ROLES = frozenset({ROLE_OWNER, ROLE_ADMIN})


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
