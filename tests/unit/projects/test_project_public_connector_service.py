import json
from collections.abc import Iterator
from dataclasses import asdict
from pathlib import Path
from typing import Any

import pytest
from cryptography.fernet import Fernet

from octop.infra.db.migrate import run_migrations
from octop.infra.db.pool import SqlitePool
from octop.infra.db.repos.project_public_connectors import (
    KEY_REF,
    MAX_REVISION,
    ProjectPublicConnectorRepo,
    PublicConnectorFailure,
)
from octop.infra.db.repos.projects import ProjectRepo
from octop.infra.db.repos.users import UserRepo
from octop.infra.projects.connectors import ProjectPublicConnectorService

CREDENTIAL = {"endpoint": "https://synthetic.invalid/mcp", "bearer_token": "synthetic-only"}


@pytest.fixture
def setup(
    tmp_path: Path,
) -> Iterator[tuple[SqlitePool, ProjectPublicConnectorService, str, list[int]]]:
    pool = SqlitePool(tmp_path / "public.db")
    try:
        run_migrations(pool)
        users = UserRepo(pool)
        ids = [
            users.create(
                username=name, password_hash="h", role="admin" if name == "outsider" else "user"
            )
            for name in ("owner", "admin", "member", "outsider")
        ]
        projects = ProjectRepo(pool)
        project = projects.create_with_owner(creator_user_id=ids[0], name="Public")
        projects.add_member(project.project_id, ids[1], role="admin")
        projects.add_member(project.project_id, ids[2], role="member")
        yield (
            pool,
            ProjectPublicConnectorService(ProjectPublicConnectorRepo(pool)),
            project.project_id,
            ids,
        )
    finally:
        pool.close()


def create(service: ProjectPublicConnectorService, pid: str, actor: int, expected: int = 1) -> Any:
    return service.create(
        pid,
        actor,
        expected_project_revision=expected,
        display_name="Synthetic",
        description="Safe",
        credential=CREDENTIAL,
    )


def snapshot(pool: SqlitePool) -> str:
    with pool.connect() as conn:
        return "\n".join(conn.iterdump())


def resource(pool: SqlitePool) -> dict[str, Any]:
    with pool.connect() as conn:
        return dict(
            conn.execute("SELECT * FROM project_public_connectors ORDER BY id LIMIT 1").fetchone()
        )


@pytest.mark.parametrize("actor,reason", [(2, "forbidden"), (3, "not_found")])
def test_write_roles_and_nonmember_admin(setup: Any, actor: int, reason: str) -> None:
    pool, service, pid, ids = setup
    before = snapshot(pool)
    with pytest.raises(PublicConnectorFailure, match=reason):
        create(service, pid, ids[actor])
    assert snapshot(pool) == before
    assert service.list_safe(pid, ids[2]).items == []


@pytest.mark.parametrize("change", ["disabled", "removed", "deleted"])
def test_fresh_actor_qualification(setup: Any, change: str) -> None:
    pool, service, pid, ids = setup
    if change == "deleted":
        UserRepo(pool).delete(ids[1])
    else:
        with pool.transaction() as conn:
            if change == "disabled":
                conn.execute("UPDATE users SET disabled=1 WHERE id=?", (ids[1],))
            else:
                conn.execute("DELETE FROM project_members WHERE user_id=?", (ids[1],))
    before = snapshot(pool)
    with pytest.raises(PublicConnectorFailure, match="not_found"):
        create(service, pid, ids[1])
    with pytest.raises(PublicConnectorFailure, match="not_found"):
        service.list_safe(pid, ids[1])
    assert snapshot(pool) == before


def test_display_only_rename_and_stale_before_noop(setup: Any) -> None:
    pool, service, pid, ids = setup
    cid = create(service, pid, ids[1]).items[0].connector_id
    before = resource(pool)
    result = service.rename(
        pid,
        ids[0],
        cid,
        expected_project_revision=2,
        expected_grant_revision=1,
        display_name="Renamed",
        description="Safe",
    )
    assert result.public_connectors_revision == 3
    assert resource(pool)["credential_blob"] == before["credential_blob"]
    assert resource(pool)["grant_revision"] == 1
    unchanged = snapshot(pool)
    with pytest.raises(PublicConnectorFailure, match="stale_revision"):
        service.replace_credentials(
            pid,
            ids[0],
            cid,
            expected_project_revision=2,
            expected_grant_revision=1,
            credential=CREDENTIAL,
        )
    assert snapshot(pool) == unchanged
    assert (
        service.rename(
            pid,
            ids[0],
            cid,
            expected_project_revision=3,
            expected_grant_revision=1,
            display_name="Renamed",
            description="Safe",
        )
        == result
    )
    assert snapshot(pool) == unchanged


def test_archive_and_terminal_revoke(setup: Any) -> None:
    pool, service, pid, ids = setup
    cid = create(service, pid, ids[0]).items[0].connector_id
    with pool.transaction() as conn:
        conn.execute("UPDATE project_spaces SET archived=1 WHERE project_id=?", (pid,))
        conn.execute("DELETE FROM secrets WHERE k=?", (KEY_REF,))
    before = snapshot(pool)
    with pytest.raises(PublicConnectorFailure, match="project_archived"):
        create(service, pid, ids[0], 2)
    with pytest.raises(PublicConnectorFailure, match="project_archived"):
        service.rename(
            pid,
            ids[0],
            cid,
            expected_project_revision=2,
            expected_grant_revision=1,
            display_name="new",
            description="",
        )
    with pytest.raises(PublicConnectorFailure, match="project_archived"):
        service.replace_credentials(
            pid,
            ids[0],
            cid,
            expected_project_revision=2,
            expected_grant_revision=1,
            credential=CREDENTIAL,
        )
    assert snapshot(pool) == before
    assert service.list_safe(pid, ids[2]).items[0].state == "active"
    result = service.revoke(
        pid, ids[0], cid, expected_project_revision=2, expected_grant_revision=1
    )
    assert resource(pool)["credential_blob"] is None
    assert resource(pool)["key_ref"] is None
    after = snapshot(pool)
    assert (
        service.revoke(pid, ids[1], cid, expected_project_revision=3, expected_grant_revision=2)
        == result
    )
    assert snapshot(pool) == after
    with pool.transaction() as conn:
        conn.execute("UPDATE project_spaces SET archived=0 WHERE project_id=?", (pid,))
    with pytest.raises(PublicConnectorFailure, match="resource_revoked"):
        service.replace_credentials(
            pid,
            ids[0],
            cid,
            expected_project_revision=3,
            expected_grant_revision=2,
            credential=CREDENTIAL,
        )


@pytest.mark.parametrize("corruption", ["missing", "invalid", "orphan"])
def test_key_failure_never_bootstraps_or_repairs(setup: Any, corruption: str) -> None:
    pool, service, pid, ids = setup
    cid = create(service, pid, ids[0]).items[0].connector_id
    with pool.transaction() as conn:
        if corruption == "missing":
            conn.execute("DELETE FROM secrets WHERE k=?", (KEY_REF,))
        elif corruption == "invalid":
            conn.execute("UPDATE secrets SET v=? WHERE k=?", (b"invalid-secret-key", KEY_REF))
        else:
            conn.execute("UPDATE project_public_connector_key_state SET initialized=0")
    before = snapshot(pool)
    with pytest.raises(PublicConnectorFailure):
        service.replace_credentials(
            pid,
            ids[0],
            cid,
            expected_project_revision=2,
            expected_grant_revision=1,
            credential=CREDENTIAL,
        )
    with pytest.raises(PublicConnectorFailure):
        create(service, pid, ids[0], 2)
    assert snapshot(pool) == before
    service.rename(
        pid,
        ids[0],
        cid,
        expected_project_revision=2,
        expected_grant_revision=1,
        display_name="revocable",
        description="",
    )
    service.revoke(pid, ids[0], cid, expected_project_revision=3, expected_grant_revision=1)


@pytest.mark.parametrize(
    "field,value",
    [
        ("purpose", "wrong"),
        ("payload_version", True),
        ("project_id", "other"),
        ("connector_id", "other"),
        ("grant_revision", 2),
        ("grant_revision", True),
        ("kind", "other"),
        ("extra", "secret"),
    ],
)
def test_authenticated_wrong_binding_is_rejected(setup: Any, field: str, value: object) -> None:
    pool, service, pid, ids = setup
    cid = create(service, pid, ids[0]).items[0].connector_id
    with pool.transaction() as conn:
        key = bytes(conn.execute("SELECT v FROM secrets WHERE k=?", (KEY_REF,)).fetchone()[0])
        envelope = json.loads(Fernet(key).decrypt(resource(pool)["credential_blob"]))
        envelope[field] = value
        blob = Fernet(key).encrypt(json.dumps(envelope).encode())
        conn.execute("UPDATE project_public_connectors SET credential_blob=?", (blob,))
    before = snapshot(pool)
    with pytest.raises(PublicConnectorFailure, match="credential_unavailable") as exc:
        service.replace_credentials(
            pid,
            ids[0],
            cid,
            expected_project_revision=2,
            expected_grant_revision=1,
            credential=CREDENTIAL,
        )
    assert str(exc.value) == "credential_unavailable"
    assert snapshot(pool) == before


@pytest.mark.parametrize("raw", [b"null", b"[]", b"{", b'{"purpose":"one","purpose":"two"}'])
def test_authenticated_malformed_json_is_rejected(setup: Any, raw: bytes) -> None:
    pool, service, pid, ids = setup
    cid = create(service, pid, ids[0]).items[0].connector_id
    with pool.transaction() as conn:
        key = bytes(conn.execute("SELECT v FROM secrets WHERE k=?", (KEY_REF,)).fetchone()[0])
        conn.execute(
            "UPDATE project_public_connectors SET credential_blob=?", (Fernet(key).encrypt(raw),)
        )
    before = snapshot(pool)
    with pytest.raises(PublicConnectorFailure, match="credential_unavailable"):
        service.replace_credentials(
            pid,
            ids[0],
            cid,
            expected_project_revision=2,
            expected_grant_revision=1,
            credential=CREDENTIAL,
        )
    assert snapshot(pool) == before


@pytest.mark.parametrize(
    "point",
    [
        "secrets",
        "project_public_connector_key_state",
        "project_public_connector_ids",
        "project_public_connectors",
        "project_spaces",
        "project_events",
    ],
)
def test_every_sql_mutation_failure_rolls_back_full_state(setup: Any, point: str) -> None:
    pool, service, pid, ids = setup
    operation = (
        "UPDATE" if point in ("project_spaces", "project_public_connector_key_state") else "INSERT"
    )
    with pool.transaction() as conn:
        conn.execute(
            f"CREATE TRIGGER fail_public BEFORE {operation} ON {point} BEGIN SELECT RAISE(ABORT,'synthetic SQL failure'); END"
        )
    before = snapshot(pool)
    with pytest.raises(PublicConnectorFailure, match="storage_failure"):
        create(service, pid, ids[0])
    assert snapshot(pool) == before
    with pool.connect() as conn:
        assert not conn.in_transaction


@pytest.mark.parametrize("failure", [KeyboardInterrupt, SystemExit, RuntimeError])
def test_real_write_transaction_interrupt_restores_preimage(
    setup: Any, monkeypatch: pytest.MonkeyPatch, failure: type[BaseException]
) -> None:
    from octop.infra.projects import connectors

    pool, service, pid, ids = setup
    before = snapshot(pool)
    primary = failure("synthetic interrupt")

    def interrupt(*args: object) -> bytes:
        with pool.connect() as conn:
            assert conn.in_transaction
            assert (
                conn.execute(
                    "SELECT initialized FROM project_public_connector_key_state"
                ).fetchone()[0]
                == 1
            )
            assert conn.execute("SELECT 1 FROM secrets WHERE k=?", (KEY_REF,)).fetchone()
        raise primary

    monkeypatch.setattr(connectors, "_encrypt", interrupt)
    with pytest.raises(failure) as exc:
        create(service, pid, ids[0])
    assert exc.value is primary
    assert snapshot(pool) == before
    with pool.connect() as conn:
        assert not conn.in_transaction


def test_safe_outputs_and_audit_do_not_copy_internal_values(setup: Any) -> None:
    pool, service, pid, ids = setup
    result = create(service, pid, ids[0])
    safe = asdict(result)
    assert set(safe["items"][0]) == {
        "connector_id",
        "kind",
        "display_name",
        "description",
        "state",
        "grant_revision",
        "created_at",
        "updated_at",
    }
    with pool.connect() as conn:
        events = [
            dict(row)
            for row in conn.execute(
                "SELECT * FROM project_events WHERE event_type LIKE 'public_connector_%'"
            )
        ]
    text = json.dumps([safe, events])
    assert CREDENTIAL["endpoint"] not in text and CREDENTIAL["bearer_token"] not in text
    assert KEY_REF not in text
    assert set(json.loads(events[0]["payload_json"])) == {
        "action",
        "project_revision",
        "grant_revision",
        "changed_fields",
    }


@pytest.mark.parametrize(
    "credential",
    [
        {"endpoint": "http://host/mcp", "bearer_token": "x"},
        {"endpoint": "https://u:p@host/mcp", "bearer_token": "x"},
        {"endpoint": "https://host/mcp?q=x", "bearer_token": "x"},
        {"endpoint": "https://host/mcp#f", "bearer_token": "x"},
        {"endpoint": "https://host/mcp", "bearer_token": "x y"},
        {"endpoint": "https://host/mcp", "bearer_token": "x", "headers": {}},
        {"endpoint": "https://host/mcp", "bearer_token": "x" * 4097},
    ],
)
def test_invalid_input_has_no_write(setup: Any, credential: Any) -> None:
    pool, service, pid, ids = setup
    before = snapshot(pool)
    with pytest.raises(PublicConnectorFailure, match="invalid_credential"):
        service.create(
            pid,
            ids[0],
            expected_project_revision=1,
            display_name="safe",
            description="",
            credential=credential,
        )
    assert snapshot(pool) == before


@pytest.mark.parametrize("expected", [True, 0, -1, MAX_REVISION + 1, 1.0])
def test_revision_parser_rejects_invalid_exact_types(setup: Any, expected: Any) -> None:
    pool, service, pid, ids = setup
    before = snapshot(pool)
    with pytest.raises(PublicConnectorFailure, match="invalid_revision"):
        create(service, pid, ids[0], expected)
    assert snapshot(pool) == before


def test_cross_project_and_retained_id_after_physical_delete(
    setup: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    from octop.infra.projects import connectors

    pool, service, pid, ids = setup
    cid = create(service, pid, ids[0]).items[0].connector_id
    other = ProjectRepo(pool).create_with_owner(creator_user_id=ids[0], name="Other").project_id
    before = snapshot(pool)
    with pytest.raises(PublicConnectorFailure, match="not_found"):
        service.revoke(other, ids[0], cid, expected_project_revision=1, expected_grant_revision=1)
    assert snapshot(pool) == before
    with pool.transaction() as conn:
        conn.execute("DELETE FROM project_spaces WHERE project_id=?", (pid,))
        assert conn.execute(
            "SELECT 1 FROM project_public_connector_ids WHERE connector_id=?", (cid,)
        ).fetchone()
        assert (
            conn.execute(
                "SELECT 1 FROM project_public_connectors WHERE connector_id=?", (cid,)
            ).fetchone()
            is None
        )
    monkeypatch.setattr(connectors, "new_ulid", lambda: cid)
    before = snapshot(pool)
    with pytest.raises(PublicConnectorFailure, match="storage_failure"):
        create(service, other, ids[0])
    assert snapshot(pool) == before


def test_user_audit_fk_null_keeps_project_resource(setup: Any) -> None:
    pool, service, pid, ids = setup
    create(service, pid, ids[1])
    before = resource(pool)
    UserRepo(pool).delete(ids[1])
    after = resource(pool)
    assert after["creator_user_id"] is None and after["updated_by"] is None
    assert after["credential_blob"] == before["credential_blob"]
    assert after["grant_revision"] == before["grant_revision"]
    assert service.list_safe(pid, ids[0]).public_connectors_revision == 2


def test_replace_changes_grant_and_upper_bound_noop(setup: Any) -> None:
    pool, service, pid, ids = setup
    cid = create(service, pid, ids[0]).items[0].connector_id
    new_credential = {**CREDENTIAL, "bearer_token": "second-synthetic"}
    result = service.replace_credentials(
        pid,
        ids[0],
        cid,
        expected_project_revision=2,
        expected_grant_revision=1,
        credential=new_credential,
    )
    assert result.public_connectors_revision == 3 and result.items[0].grant_revision == 2
    with pool.transaction() as conn:
        conn.execute("UPDATE project_spaces SET public_connectors_revision=?", (MAX_REVISION,))
    before = snapshot(pool)
    service.replace_credentials(
        pid,
        ids[0],
        cid,
        expected_project_revision=MAX_REVISION,
        expected_grant_revision=2,
        credential=new_credential,
    )
    assert snapshot(pool) == before
    with pytest.raises(PublicConnectorFailure, match="revision_exhausted"):
        service.revoke(
            pid, ids[0], cid, expected_project_revision=MAX_REVISION, expected_grant_revision=2
        )
    assert snapshot(pool) == before


@pytest.mark.parametrize(
    "name,description",
    [("safe\n", ""), ("safe", "\rhidden"), ("Synthetic", "https://SYNTHETIC.invalid/mcp")],
)
def test_display_rejects_raw_controls_and_original_secret(
    setup: Any, name: str, description: str
) -> None:
    pool, service, pid, ids = setup
    before = snapshot(pool)
    with pytest.raises(PublicConnectorFailure, match="invalid_display"):
        service.create(
            pid,
            ids[0],
            expected_project_revision=1,
            display_name=name,
            description=description,
            credential={**CREDENTIAL, "endpoint": "https://SYNTHETIC.invalid/mcp"},
        )
    assert snapshot(pool) == before


@pytest.mark.parametrize(
    "column,table",
    [
        ("grant_revision", "project_public_connectors"),
        ("public_connectors_revision", "project_spaces"),
    ],
)
def test_safe_list_does_not_truncate_illegal_stored_revision(
    setup: Any, column: str, table: str
) -> None:
    pool, service, pid, ids = setup
    create(service, pid, ids[0])
    with pool.transaction() as conn:
        conn.execute(f"UPDATE {table} SET {column}=1.5")
    with pytest.raises(PublicConnectorFailure, match="invalid_revision"):
        service.list_safe(pid, ids[0])


def test_missing_key_after_all_revoked_still_refuses_bootstrap(setup: Any) -> None:
    pool, service, pid, ids = setup
    cid = create(service, pid, ids[0]).items[0].connector_id
    service.revoke(pid, ids[0], cid, expected_project_revision=2, expected_grant_revision=1)
    with pool.transaction() as conn:
        conn.execute("DELETE FROM secrets WHERE k=?", (KEY_REF,))
    before = snapshot(pool)
    with pytest.raises(PublicConnectorFailure, match="key_missing"):
        create(service, pid, ids[0], 3)
    assert snapshot(pool) == before


def test_cross_project_creates_share_initialized_key(setup: Any) -> None:
    pool, service, pid, ids = setup
    create(service, pid, ids[0])
    with pool.connect() as conn:
        key_before = bytes(
            conn.execute("SELECT v FROM secrets WHERE k=?", (KEY_REF,)).fetchone()[0]
        )
    other = ProjectRepo(pool).create_with_owner(creator_user_id=ids[0], name="Other").project_id
    create(service, other, ids[0])
    with pool.connect() as conn:
        assert (
            bytes(conn.execute("SELECT v FROM secrets WHERE k=?", (KEY_REF,)).fetchone()[0])
            == key_before
        )
        assert (
            conn.execute("SELECT initialized FROM project_public_connector_key_state").fetchone()[0]
            == 1
        )
        assert conn.execute("SELECT COUNT(*) FROM secrets WHERE k=?", (KEY_REF,)).fetchone()[0] == 1


def test_create_rename_noop_revoke(tmp_path: Path) -> None:
    from octop.infra.db.repos.project_public_connectors import ProjectPublicConnectorRepo
    from octop.infra.projects.connectors import ProjectPublicConnectorService

    pool = SqlitePool(tmp_path / "public.db")
    try:
        run_migrations(pool)
        actor = UserRepo(pool).create(username="owner", password_hash="h", role="user")
        project = ProjectRepo(pool).create_with_owner(creator_user_id=actor, name="Public")
        service = ProjectPublicConnectorService(ProjectPublicConnectorRepo(pool))
        result = service.create(
            project.project_id,
            actor,
            expected_project_revision=1,
            display_name="Synthetic",
            description="",
            credential={
                "endpoint": "https://synthetic.invalid/mcp",
                "bearer_token": "synthetic-only",
            },
        )
        item = result.items[0]
        assert result.public_connectors_revision == 2
        assert item.grant_revision == 1
        renamed = service.rename(
            project.project_id,
            actor,
            item.connector_id,
            expected_project_revision=2,
            expected_grant_revision=1,
            display_name="Renamed",
            description="",
        )
        assert renamed.public_connectors_revision == 3
        assert renamed.items[0].grant_revision == 1
        noop = service.replace_credentials(
            project.project_id,
            actor,
            item.connector_id,
            expected_project_revision=3,
            expected_grant_revision=1,
            credential={
                "endpoint": "https://synthetic.invalid/mcp",
                "bearer_token": "synthetic-only",
            },
        )
        assert noop == renamed
        revoked = service.revoke(
            project.project_id,
            actor,
            item.connector_id,
            expected_project_revision=3,
            expected_grant_revision=1,
        )
        assert revoked.items[0].state == "revoked"
        assert revoked.items[0].grant_revision == 2
    finally:
        pool.close()
