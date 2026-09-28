"""Unit tests for snapshot helpers."""

from __future__ import annotations

import os
import sqlite3
from contextlib import nullcontext
from pathlib import Path
from types import SimpleNamespace

import pytest

from octop.infra.backup.snapshot import (
    capture_jwt_secret_from_pool,
    capture_users_from_pool,
    restore_jwt_secret_into_pool,
    snapshot_sqlite_file,
)
from octop.infra.db.migrate import run_migrations
from octop.infra.db.pool import SqlitePool, _compat_row_factory, _CompatRow


@pytest.mark.parametrize("row_type", ["sqlite", "postgres_compat"])
@pytest.mark.parametrize("empty", [False, True])
def test_capture_users_preserves_all_eleven_values_and_snapshot_shape(
    tmp_path: Path, row_type: str, empty: bool
) -> None:
    columns = (
        "id",
        "username",
        "password_hash",
        "role",
        "display_name",
        "disabled",
        "created_at",
        "locale",
        "preferences_json",
        "login_failed_count",
        "login_locked_until",
    )
    expected = (
        []
        if empty
        else [
            (
                17,
                "Ｆinal-owner",
                "synthetic$argon2$owner-password-hash",
                "admin",
                "Final Ｓtraße",
                1,
                1700000000,
                "zh",
                '{"theme":"dark","preserve":"原值"}',
                7,
                1900000000,
            ),
            (
                29,
                "fallback-member",
                "synthetic$argon2$member-password-hash",
                "member",
                None,
                0,
                1700000011,
                "en",
                None,
                0,
                None,
            ),
        ]
    )
    if row_type == "sqlite":
        pool = SqlitePool(tmp_path / "capture.db")
        with pool.connect() as conn:
            conn.execute(f"CREATE TABLE users ({','.join(columns)})")
            conn.executemany("INSERT INTO users VALUES (?,?,?,?,?,?,?,?,?,?,?)", expected)
    else:
        make_row = _compat_row_factory(
            SimpleNamespace(description=[SimpleNamespace(name=name) for name in columns])
        )
        rows = [make_row(values) for values in expected]
        assert all(isinstance(row, _CompatRow) for row in rows)
        assert all(tuple(row) == columns for row in rows)
        cursor = SimpleNamespace(fetchall=lambda: rows)
        conn = SimpleNamespace(execute=lambda _sql: cursor)
        pool = SimpleNamespace(connect=lambda: nullcontext(conn), close=lambda: None)
    try:
        captured = capture_users_from_pool(pool)
        assert captured == expected
        assert all(type(row) is tuple and len(row) == 11 for row in captured)
        if captured:
            assert captured[0][2:6] == (
                "synthetic$argon2$owner-password-hash",
                "admin",
                "Final Ｓtraße",
                1,
            )
            assert captured[0][7:] == (
                "zh",
                '{"theme":"dark","preserve":"原值"}',
                7,
                1900000000,
            )
            assert captured[1][4:] == (None, 0, 1700000011, "en", None, 0, None)
    finally:
        pool.close()


@pytest.mark.skipif(os.name == "nt", reason="Windows disallows '?' in path names")
def test_snapshot_sqlite_file_with_special_chars_in_path(tmp_path: Path) -> None:
    """Paths containing URI metacharacters must not alter connect options."""
    tricky_dir = tmp_path / "dir?mode=memory"
    tricky_dir.mkdir()
    source = tricky_dir / "data.db"
    with sqlite3.connect(source) as conn:
        conn.execute("CREATE TABLE t (v TEXT)")
        conn.execute("INSERT INTO t VALUES ('ok')")

    dest = tmp_path / "backup.db"
    snapshot_sqlite_file(source, dest)

    with sqlite3.connect(dest) as conn:
        row = conn.execute("SELECT v FROM t").fetchone()
    assert row is not None
    assert row[0] == "ok"


def test_jwt_secret_capture_and_restore(tmp_path: Path) -> None:
    pool = SqlitePool(tmp_path / "octop.db")
    run_migrations(pool)
    secret = b"session-jwt-secret-value-32bytes!!"

    assert capture_jwt_secret_from_pool(pool) is None

    with pool.connect() as conn:
        conn.execute(
            "INSERT INTO secrets(k, v, created_at) VALUES (?, ?, ?)",
            ("jwt", secret, 1),
        )
    assert capture_jwt_secret_from_pool(pool) == secret

    # Overwrite with a foreign secret, then restore the captured one.
    with pool.connect() as conn:
        conn.execute("UPDATE secrets SET v = ? WHERE k = ?", (b"foreign", "jwt"))
    restore_jwt_secret_into_pool(pool, secret)
    assert capture_jwt_secret_from_pool(pool) == secret

    # Insert path when the row is missing after a restore wipe.
    with pool.connect() as conn:
        conn.execute("DELETE FROM secrets WHERE k = ?", ("jwt",))
    restore_jwt_secret_into_pool(pool, secret)
    assert capture_jwt_secret_from_pool(pool) == secret
    pool.close()
