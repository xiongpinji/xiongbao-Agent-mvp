"""PostgreSQL dump/restore via client tools on PATH."""

from __future__ import annotations

import shutil
import subprocess
from collections.abc import Sequence
from pathlib import Path

from octop.infra.db.pool import DatabasePool
from octop.infra.errors import ErrorCode, OctopError


def _require_tool(name: str) -> str:
    path = shutil.which(name)
    if not path:
        raise OctopError(
            ErrorCode.INTERNAL_ERROR,
            f"{name} not found on PATH; required for PostgreSQL backup/restore",
        )
    return path


def dump_postgres(
    conninfo: str,
    dest: Path,
    *,
    exclude_table_data: Sequence[str] = (),
) -> None:
    pg_dump = _require_tool("pg_dump")
    dest.parent.mkdir(parents=True, exist_ok=True)
    cmd = [pg_dump, "-Fc", "-f", str(dest), "--dbname", conninfo]
    for table in exclude_table_data:
        cmd.extend(["--exclude-table-data", table])
    proc = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        raise OctopError(
            ErrorCode.INTERNAL_ERROR,
            f"pg_dump failed: {proc.stderr.strip() or proc.stdout.strip()}",
        )


def _require_restore_success(proc: subprocess.CompletedProcess[str], stage: str) -> None:
    if proc.returncode != 0:
        raise OctopError(
            ErrorCode.INTERNAL_ERROR,
            "PostgreSQL backup restore tool failed",
            details={
                "reason": "backup_restore_tool_failed",
                "stage": stage,
                "exit_code": proc.returncode,
            },
        )


def restore_postgres(
    conninfo: str,
    dump_file: Path,
    *,
    pool: DatabasePool,
    schema_version: int,
) -> None:
    """Restore a dump, removing only known later project-plan tables first.

    The caller retains the complete database preimage and compensates failures.
    Cleanup commits before the separate, atomic pg_restore transaction starts.
    Public app objects recreated by --no-owner belong to the restoring role.
    """
    if type(schema_version) is not int or schema_version < 0:
        raise OctopError(
            ErrorCode.INTERNAL_ERROR,
            "PostgreSQL backup schema version is invalid",
            details={"reason": "backup_restore_schema_version_invalid"},
        )
    if pool.dialect != "postgresql":
        raise OctopError(ErrorCode.INTERNAL_ERROR, "PostgreSQL restore requires a PostgreSQL pool")
    pg_restore = _require_tool("pg_restore")
    inspected = subprocess.run(
        [pg_restore, "--list", str(dump_file)],
        capture_output=True,
        text=True,
        check=False,
    )
    _require_restore_success(inspected, "inspect")

    # --clean only drops objects present in the dump. Later FK-bearing plan
    # tables would otherwise prevent an older dump from dropping its tables.
    # RESTRICT makes unrelated dependencies reject and roll back this cleanup.
    if schema_version < 35:
        try:
            with pool.transaction() as conn:
                conn.execute(
                    "DROP TABLE IF EXISTS public.project_todo_view_state, "
                    "public.project_todo_views RESTRICT"
                )
                if schema_version < 34:
                    conn.execute(
                        "ALTER TABLE IF EXISTS public.project_todos "
                        "DROP CONSTRAINT IF EXISTS fk_project_todos_priority"
                    )
                    conn.execute(
                        "DROP TABLE IF EXISTS public.project_todo_tag_links, "
                        "public.project_todo_tags, public.project_todo_priorities, "
                        "public.project_todo_catalog_state RESTRICT"
                    )
        except Exception:
            raise OctopError(
                ErrorCode.INTERNAL_ERROR,
                "PostgreSQL backup restore cleanup failed",
                details={"reason": "backup_restore_cleanup_failed"},
            ) from None

    proc = subprocess.run(
        [
            pg_restore,
            "--clean",
            "--if-exists",
            "--no-owner",
            "--single-transaction",
            "--dbname",
            conninfo,
            str(dump_file),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    _require_restore_success(proc, "restore")
