"""030A ``mode='files'`` task gate, capability hint, and full-delete helpers.

B3 ships controlled file-task CREATION closed: :data:`PROJECT_TASK_FILES_MODE_ENABLED`
is ``False`` and every creation entry point must call
:func:`require_files_mode_enabled` first, so the 422 ``PROJECT_TASK_FILES_UNSUPPORTED``
contract holds even when a UI asks for the mode. Narrow tests override the
module flag explicitly; B4 flips it only after the full ingress audit.

:func:`files_task_capability` is a non-authoritative UI hint: the create route
re-checks the gate, the managed root, membership, the digest, the expert list,
the source Agent, and the runtime binding inside the write path.
"""

from __future__ import annotations

import os
from typing import Any

from octop.infra.projects.tasks import files_unsupported_error

# B3 default: CLOSED. Flipping this on is a B4 acceptance decision.
PROJECT_TASK_FILES_MODE_ENABLED = False


def require_files_mode_enabled() -> None:
    """Raise the stable 422 unsupported error while the gate is closed."""
    if not PROJECT_TASK_FILES_MODE_ENABLED:
        raise files_unsupported_error()


def managed_root_reachable(paths: Any) -> bool:
    """Whether the managed project-task-files root can be written.

    Read-only probe (``os.access``): the capability hint must not create,
    delete, or chmod anything. The root is created only by the B2 runtime
    startup, so a missing root falls back to checking its parent.
    """
    try:
        root = paths.project_task_files_dir
    except Exception:
        return False
    try:
        probe = root if root.exists() else root.parent
        return os.access(probe, os.W_OK)
    except OSError:
        return False


def files_task_capability(paths: Any) -> dict[str, Any]:
    """``{available, reason}`` hint for ``GET /api/projects/task-capabilities``."""
    if not PROJECT_TASK_FILES_MODE_ENABLED:
        return {"available": False, "reason": "disabled"}
    if not managed_root_reachable(paths):
        return {"available": False, "reason": "storage_unavailable"}
    return {"available": True, "reason": None}
