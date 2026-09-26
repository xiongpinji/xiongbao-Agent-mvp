"""030A dedicated full-delete route for one owned controlled file task.

``DELETE /api/project-task-files/{thread_id}`` removes the complete private
task — checkpoint, private root bytes, thread/projection/link/context/share
metadata, and the runtime Agent row when it was the last thread — through
:meth:`octop.infra.agents.manager.AgentManager.delete_project_task_file_task`.

It is independent of project membership and of the project task link: after a
project detach the owner can still delete the task (and repeated deletes answer
404). It is deliberately the ONLY complete-delete entry for internal runtimes;
generic Agent/thread deletes refuse them. Project ``DELETE /projects/{id}/tasks/
{thread_id}`` stays detach-only.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends

from octop.api.deps import current_user, get_server
from octop.infra.errors import ErrorCode, OctopError
from octop.infra.server import OctopServer
from octop.infra.users.identity import User

router = APIRouter(prefix="/project-task-files")


@router.delete(
    "/{thread_id}",
    status_code=204,
    summary="Delete one own controlled file task completely",
)
async def delete_project_task_file(
    thread_id: str,
    server: OctopServer = Depends(get_server),
    user: User = Depends(current_user),
) -> None:
    """Owner-only complete delete; independent of project membership.

    The manager verifies the exact private thread↔runtime↔owner DB binding
    before touching any bytes, deletes the checkpoint, unloads the runtime,
    removes the managed root, and only then deletes the metadata rows. Any
    failure returns an error (503 for a retryable runtime/byte failure) and
    leaves the task visible so the caller can retry; an already-complete
    delete or a task the caller does not own answers 404. A partial cleanup is
    never reported as 204.
    """
    assert server.app_runtime is not None
    removed = await server.app_runtime.agent_registry.delete_project_task_file_task(
        thread_id=thread_id, owner_user_id=user.id
    )
    if not removed:
        raise OctopError(ErrorCode.NOT_FOUND, "project task not found")
    return None
