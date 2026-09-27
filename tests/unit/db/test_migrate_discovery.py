from __future__ import annotations

from octop.infra.db.migrate import _discover


def test_discover_sqlite_excludes_pg_files():
    files = _discover("sqlite")
    names = [p.name for _, p in files]
    assert any(n == "001_initial.sql" for n in names)
    assert not any(n.endswith(".pg.sql") for n in names)


def test_discover_postgresql_only_pg_files():
    files = _discover("postgresql")
    names = [p.name for _, p in files]
    assert "001_initial.pg.sql" in names
    assert not any(n.endswith(".sql") and not n.endswith(".pg.sql") for n in names)


def test_project_asset_move_rename_v30_has_paired_migrations() -> None:
    assert any(
        version == 30 and path.name == "030_project_asset_move_rename.sql"
        for version, path in _discover("sqlite")
    )
    assert any(
        version == 30 and path.name == "030_project_asset_move_rename.pg.sql"
        for version, path in _discover("postgresql")
    )
