"""Disposable PostgreSQL HTTP journey for PS-01; never use an existing database."""

from __future__ import annotations

import argparse
import asyncio
from contextlib import redirect_stdout
from io import StringIO
import json
import os
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch
from urllib.parse import urlsplit


def require_status(response: object, expected: int, stage: str) -> None:
    actual = getattr(response, "status_code")
    if actual != expected:
        raise AssertionError(f"{stage}: expected HTTP {expected}, got {actual}")


async def run_probe(source: Path, home: Path) -> dict[str, object]:
    from tests.support.app import octop_client
    from tests.support.auth import (
        auth_header,
        bootstrap_admin,
        create_user,
        resolve_user_id,
    )
    import octop

    imported = Path(octop.__file__).resolve()
    expected_package = (source / "src" / "octop").resolve()
    if expected_package not in imported.parents:
        raise AssertionError(f"wrong source import: {imported}")
    url = urlsplit(os.environ["OCTOP_DATABASE_URL"])
    if url.hostname != "127.0.0.1" or url.port != 39152:
        raise AssertionError("database URL is not the disposable loopback port")
    if not url.path.lstrip("/").startswith("ps01_http_"):
        raise AssertionError("database name is not disposable")
    overrides = [
        name
        for name in os.environ
        if name.startswith("OCTOP_DATABASE_") and name != "OCTOP_DATABASE_URL"
    ]
    if overrides:
        raise AssertionError(f"unexpected database environment overrides: {overrides}")
    if home.exists() and any(home.iterdir()):
        raise AssertionError("probe home must start empty")
    home.mkdir(parents=True, exist_ok=True)

    steps: list[str] = []
    project_id = ""
    async with octop_client(home) as (client, srv):
        if srv.services is None or srv.services.db.dialect != "postgresql":
            raise AssertionError("server is not bound to PostgreSQL")
        with srv.services.db.connect() as conn:
            db_row = conn.execute(
                "SELECT current_database(), inet_server_port()"
            ).fetchone()
            db_name, server_port = db_row[0], db_row[1]
            migration_version = conn.execute(
                "SELECT version FROM _schema_version"
            ).fetchone()[0]
        if db_name != url.path.lstrip("/") or server_port != 39152:
            raise AssertionError("connected database/port differs from disposable URL")
        if migration_version != 30:
            raise AssertionError(f"unexpected migration version: {migration_version}")

        await bootstrap_admin(client, home)
        admin_auth = await auth_header(client)
        owner_auth = await create_user(client, admin_auth, username="pghttp_owner")
        member_auth = await create_user(client, admin_auth, username="pghttp_member")
        outsider_auth = await create_user(
            client, admin_auth, username="pghttp_outsider"
        )
        member_id = await resolve_user_id(client, admin_auth, "pghttp_member")
        steps.append("postgresql_v30_and_three_synthetic_users")

        unauth = await client.get("/api/projects")
        require_status(unauth, 401, "unauthenticated list")
        created = await client.post(
            "/api/projects", headers=owner_auth, json={"name": "PGHTTP初名"}
        )
        require_status(created, 201, "owner create")
        project_id = created.json()["project_id"]
        require_status(
            await client.get(f"/api/projects/{project_id}", headers=owner_auth),
            200,
            "owner detail",
        )
        owner_found = await client.get(
            "/api/projects", headers=owner_auth, params={"q": "PGHTTP初名"}
        )
        require_status(owner_found, 200, "owner search")
        assert [item["project_id"] for item in owner_found.json()["items"]] == [
            project_id
        ]
        before_join = await client.get(
            f"/api/projects/{project_id}", headers=member_auth
        )
        require_status(before_join, 404, "member before invite")
        steps.append("owner_create_detail_search_member_prejoin_404")

        invite = await client.post(
            f"/api/projects/{project_id}/invites", headers=owner_auth, json={}
        )
        require_status(invite, 201, "owner invite")
        token = invite.json()["token"]
        accepted = await client.post(
            "/api/projects/invites/accept",
            headers=member_auth,
            json={"token": token},
        )
        require_status(accepted, 200, "member accepts invite")
        assert accepted.json()["status"] == "joined"
        require_status(
            await client.get(f"/api/projects/{project_id}", headers=member_auth),
            200,
            "member detail",
        )
        member_found = await client.get(
            "/api/projects", headers=member_auth, params={"q": "PGHTTP初名"}
        )
        assert [item["project_id"] for item in member_found.json()["items"]] == [
            project_id
        ]
        member_edit = await client.patch(
            f"/api/projects/{project_id}",
            headers=member_auth,
            json={"name": "成员不能改"},
        )
        require_status(member_edit, 403, "member cannot rename")
        steps.append("invite_join_member_read_search_edit_403")

        outsider_list = await client.get("/api/projects", headers=outsider_auth)
        require_status(outsider_list, 200, "outsider list")
        assert outsider_list.json()["items"] == []
        outsider_search = await client.get(
            "/api/projects", headers=outsider_auth, params={"q": "PGHTTP"}
        )
        require_status(outsider_search, 200, "outsider search")
        assert outsider_search.json()["items"] == []
        require_status(
            await client.get(f"/api/projects/{project_id}", headers=outsider_auth),
            404,
            "outsider detail",
        )
        steps.append("outsider_empty_list_search_detail_404")

        renamed = await client.patch(
            f"/api/projects/{project_id}",
            headers=owner_auth,
            json={"name": "PGHTTP改名"},
        )
        require_status(renamed, 200, "owner rename")
        assert renamed.json()["name"] == "PGHTTP改名"
        for actor, auth in (("owner", owner_auth), ("member", member_auth)):
            detail = await client.get(f"/api/projects/{project_id}", headers=auth)
            require_status(detail, 200, f"{actor} renamed detail")
            assert detail.json()["name"] == "PGHTTP改名"
            found = await client.get(
                "/api/projects", headers=auth, params={"q": "PGHTTP改名"}
            )
            require_status(found, 200, f"{actor} renamed search")
            assert [item["project_id"] for item in found.json()["items"]] == [
                project_id
            ]
        steps.append("owner_rename_owner_member_refresh_and_search")

        removed = await client.delete(
            f"/api/projects/{project_id}/members/{member_id}",
            headers=owner_auth,
        )
        require_status(removed, 204, "owner revokes member")
        require_status(
            await client.get(f"/api/projects/{project_id}", headers=member_auth),
            404,
            "revoked member detail",
        )
        revoked_list = await client.get("/api/projects", headers=member_auth)
        assert revoked_list.json()["items"] == []
        revoked_search = await client.get(
            "/api/projects", headers=member_auth, params={"q": "PGHTTP改名"}
        )
        assert revoked_search.json()["items"] == []
        steps.append("revocation_immediate_detail_404_list_search_empty")

    async with octop_client(home) as (client, srv):
        if srv.services is None or srv.services.db.dialect != "postgresql":
            raise AssertionError("restart switched away from PostgreSQL")
        owner_auth = await auth_header(client, username="pghttp_owner")
        member_auth = await auth_header(client, username="pghttp_member")
        detail = await client.get(f"/api/projects/{project_id}", headers=owner_auth)
        require_status(detail, 200, "owner detail after restart")
        assert detail.json()["name"] == "PGHTTP改名"
        require_status(
            await client.get(f"/api/projects/{project_id}", headers=member_auth),
            404,
            "revoked member after restart",
        )
        steps.append("restart_persists_rename_and_revocation")

    source_sha = subprocess.check_output(
        ["git", "-C", str(source), "rev-parse", "HEAD"], text=True
    ).strip()
    return {
        "result": "PASS",
        "source_sha": source_sha,
        "octop_import": str(imported),
        "database_driver": "postgresql",
        "database_host": url.hostname,
        "database_port": url.port,
        "database_name": url.path.lstrip("/"),
        "migration_version": migration_version,
        "project_id": project_id,
        "steps": steps,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", required=True, type=Path)
    parser.add_argument("--home", required=True, type=Path)
    args = parser.parse_args()
    source = args.source.resolve()
    sys.path.insert(0, str(source))
    sys.path.insert(0, str(source / "src"))
    isolated_home = args.home.resolve()
    with (
        patch.object(Path, "home", return_value=isolated_home),
        redirect_stdout(StringIO()),
    ):
        result = asyncio.run(run_probe(source, isolated_home))
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
