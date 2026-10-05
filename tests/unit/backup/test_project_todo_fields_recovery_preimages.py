"""Real rename interruption and durable current-state recovery preimages."""

from __future__ import annotations

import hashlib
import sqlite3
from contextlib import closing
from pathlib import Path
from typing import Any

import pytest
from tests.unit.backup.test_project_todo_fields_archive_failure import (
    _assert_unchanged,
    _database_dump,
    _private_tree,
    _restore,
)
from tests.unit.backup.test_project_todo_fields_archive_failure import (
    restore_case as _restore_case,
)

from octop.infra.backup import system_archive
from octop.infra.errors import ErrorCode, OctopError

# Register the existing real-data fixture without changing its frozen module.
restore_case = _restore_case


def _tree(root: Path) -> dict[str, str]:
    return {
        path.relative_to(root).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in root.rglob("*")
        if path.is_file()
    }


def _stages(case: dict[str, Any]) -> list[Path]:
    return list(case["target"].root.glob(".todo-comment-image-restore-*"))


@pytest.mark.parametrize("transition", ["previous", "objects"])
@pytest.mark.parametrize("after", [False, True], ids=["before-real-rename", "after-real-rename"])
@pytest.mark.parametrize("interrupted", [False, True], ids=["os-error", "interrupt"])
def test_real_image_transition_failure_restores_original_pair_without_database_attempt(
    restore_case: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
    transition: str,
    after: bool,
    interrupted: bool,
) -> None:
    real_replace = system_archive.os.replace
    real_database_restore = system_archive.restore_sqlite_into_pool
    destination = restore_case["target"].project_todo_comment_images
    faults: list[str] = []
    database_calls: list[Path] = []

    def replace(source: Any, target: Any) -> None:
        src, dest = Path(source), Path(target)
        match = (
            src == destination and dest.name == "previous"
            if transition == "previous"
            else src.name == "objects" and dest == destination
        )
        if match and not faults:
            faults.append(transition)
            if after:
                real_replace(source, target)
            if interrupted:
                raise KeyboardInterrupt("interrupted actual image transition")
            raise OSError("failed actual image transition")
        real_replace(source, target)

    def database_restore(source: Path, pool: Any) -> None:
        database_calls.append(source)
        real_database_restore(source, pool)

    with monkeypatch.context() as patch:
        patch.setattr(system_archive.os, "replace", replace)
        patch.setattr(system_archive, "restore_sqlite_into_pool", database_restore)
        with pytest.raises(
            KeyboardInterrupt if interrupted else OSError, match="actual image transition"
        ):
            _restore(restore_case)
    assert faults == [transition]
    assert database_calls == []
    _assert_unchanged(restore_case)
    assert _stages(restore_case) == []


@pytest.mark.parametrize(
    "failed_resources",
    [
        ["database"],
        ["private_comment_images"],
        ["database", "private_comment_images"],
    ],
    ids=["database", "private", "both"],
)
def test_failed_compensation_retains_real_preimages_for_later_pair_recovery(
    restore_case: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
    failed_resources: list[str],
) -> None:
    real_restore = system_archive.restore_sqlite_into_pool
    real_snapshot = system_archive.snapshot_sqlite_file
    real_migrate = system_archive.run_migrations
    real_image_rollback = system_archive._rollback_private_comment_images
    saved: dict[str, Path] = {}
    database_calls: list[Path] = []

    def snapshot(source: Path, dest: Path) -> None:
        real_snapshot(source, dest)
        saved["database"] = dest

    def database_restore(source: Path, pool: Any) -> None:
        database_calls.append(source)
        if len(database_calls) == 2 and "database" in failed_resources:
            raise OSError("SECRET_DSN database compensation failure")
        real_restore(source, pool)

    def migration(pool: Any) -> None:
        real_migrate(pool)
        raise RuntimeError("injected post-install migration failure")

    def image_rollback(stage: Path, dest: Path) -> None:
        saved["stage"] = stage
        if "private_comment_images" in failed_resources:
            raise KeyboardInterrupt("SECRET_DSN image compensation failure")
        real_image_rollback(stage, dest)

    with monkeypatch.context() as patch:
        patch.setattr(system_archive, "snapshot_sqlite_file", snapshot)
        patch.setattr(system_archive, "restore_sqlite_into_pool", database_restore)
        patch.setattr(system_archive, "run_migrations", migration)
        patch.setattr(system_archive, "_rollback_private_comment_images", image_rollback)
        with pytest.raises(OctopError) as caught:
            _restore(restore_case)
    error = caught.value
    assert error.code == ErrorCode.INTERNAL_ERROR
    assert error.details == {
        "reason": "backup_restore_rollback_failed",
        "failed_resources": failed_resources,
    }
    assert "SECRET_DSN" not in str(error)
    assert "SECRET_DSN" not in str(error.to_envelope())
    assert str(restore_case["target"].root) not in str(error.to_envelope())
    assert saved["database"].is_file(), "current database preimage was deleted"
    assert saved["database"].parent == saved["stage"]
    assert saved["stage"] in _stages(restore_case)
    with closing(sqlite3.connect(saved["database"])) as conn:
        assert tuple(sorted(conn.iterdump())) == restore_case["database_before"]
    if "private_comment_images" in failed_resources:
        assert _tree(saved["stage"] / "previous") == restore_case["tree_before"]
    else:
        assert _private_tree(restore_case["target"]) == restore_case["tree_before"]

    # Remove faults, then perform real recovery from the retained SQL/PNG resources.
    real_restore(saved["database"], restore_case["pool"])
    real_image_rollback(saved["stage"], restore_case["target"].project_todo_comment_images)
    real_image_rollback(saved["stage"], restore_case["target"].project_todo_comment_images)
    _assert_unchanged(restore_case)


def test_direct_install_covers_interrupt_after_first_real_rename(
    restore_case: dict[str, Any], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    dest = restore_case["target"].project_todo_comment_images
    stage = tmp_path / "direct-stage"
    objects = stage / "objects"
    objects.mkdir(parents=True)
    (objects / "new-image").write_bytes(b"new synthetic image")
    real_replace = system_archive.os.replace
    reached: list[bool] = []

    def replace(source: Any, target: Any) -> None:
        real_replace(source, target)
        if Path(source) == dest and not reached:
            reached.append(True)
            raise KeyboardInterrupt("interrupted after actual first rename")

    with monkeypatch.context() as patch:
        patch.setattr(system_archive.os, "replace", replace)
        with pytest.raises(KeyboardInterrupt, match="actual first rename"):
            system_archive._install_private_comment_images(stage, dest)
    assert reached == [True]
    _assert_unchanged(restore_case)
    system_archive._rollback_private_comment_images(stage, dest)
    system_archive._rollback_private_comment_images(stage, dest)
    _assert_unchanged(restore_case)


def test_image_rollback_is_idempotent_after_actual_install(
    restore_case: dict[str, Any], tmp_path: Path
) -> None:
    dest = restore_case["target"].project_todo_comment_images
    stage = tmp_path / "direct-stage"
    objects = stage / "objects"
    objects.mkdir(parents=True)
    (objects / "new-image").write_bytes(b"new synthetic image")
    system_archive._install_private_comment_images(stage, dest)
    assert _private_tree(restore_case["target"]) != restore_case["tree_before"]
    system_archive._rollback_private_comment_images(stage, dest)
    _assert_unchanged(restore_case)
    system_archive._rollback_private_comment_images(stage, dest)
    _assert_unchanged(restore_case)


@pytest.mark.parametrize("interrupted", [False, True], ids=["os-error", "interrupt"])
def test_snapshot_failure_never_installs_and_leaves_no_recovery_stage(
    restore_case: dict[str, Any], monkeypatch: pytest.MonkeyPatch, interrupted: bool
) -> None:
    installs: list[bool] = []

    def snapshot(source: Path, dest: Path) -> None:
        if interrupted:
            raise KeyboardInterrupt("preimage snapshot failure")
        raise OSError("preimage snapshot failure")

    def install(*args: Any, **kwargs: Any) -> None:
        installs.append(True)
        raise AssertionError("must not install without a current database preimage")

    monkeypatch.setattr(system_archive, "snapshot_sqlite_file", snapshot)
    monkeypatch.setattr(system_archive, "_install_private_comment_images", install)
    monkeypatch.setattr(system_archive, "restore_sqlite_into_pool", install)
    with pytest.raises(KeyboardInterrupt if interrupted else OSError, match="snapshot failure"):
        _restore(restore_case)
    assert installs == []
    _assert_unchanged(restore_case)
    assert _stages(restore_case) == []


def test_successful_restore_cleans_recovery_stage(restore_case: dict[str, Any]) -> None:
    _restore(restore_case)
    assert _database_dump(restore_case["pool"]) == restore_case["source_database"]
    assert _private_tree(restore_case["target"]) == restore_case["source_tree"]
    assert _stages(restore_case) == []
