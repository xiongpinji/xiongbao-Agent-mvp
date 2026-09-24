"""Integration tests for /api/projects/{id}/assets (PS-06A / 023A).

Covers the server half of ``docs/xiongbao/PROJECT_ASSET_CONTRACT.md``:
members create folders, upload single files into the private project-asset
root, list/filter/search with pagination, read real usage, and download the
same bytes through the login API. Outsiders, instance admins without
membership, unknown projects/nodes, and revoked members all get uniform 404s;
same-parent name conflicts (case-insensitive) answer 409 with paired i18n
messages; invalid names answer 422; oversize uploads answer 413 and leave no
rows or files; archived projects keep reads but refuse writes with 403; an
injected commit failure leaves neither DB rows nor orphan objects behind; and
no response ever leaks disk paths, object keys, or credentials.
"""

from __future__ import annotations

import hashlib
import io
from pathlib import Path
from typing import Any

import pytest

from octop.api.app import build_app
from octop.i18n import error_message
from tests.support.auth import create_user, resolve_user_id

OWNER = "passet_owner"
MEMBER = "passet_member"
OUTSIDER = "passet_outsider"

_ITEM_KEYS = {
    "node_id",
    "parent_node_id",
    "kind",
    "name",
    "size_bytes",
    "media_type",
    "created_at",
    "updated_at",
}
_VERSION_KEYS = {"version_id", "size_bytes", "sha256", "media_type"}


async def _base(env: Any) -> dict[str, Any]:
    """Owner + member + outsider, one project owned by OWNER with MEMBER added."""
    client, srv, admin_auth = env
    owner_auth = await create_user(client, admin_auth, username=OWNER)
    member_auth = await create_user(client, admin_auth, username=MEMBER)
    outsider_auth = await create_user(client, admin_auth, username=OUTSIDER)
    owner_uid = await resolve_user_id(client, admin_auth, OWNER)
    member_uid = await resolve_user_id(client, admin_auth, MEMBER)
    outsider_uid = await resolve_user_id(client, admin_auth, OUTSIDER)
    r = await client.post("/api/projects", headers=owner_auth, json={"name": "资产项目"})
    assert r.status_code == 201, r.text
    pid = r.json()["project_id"]
    srv.services.project_repo.add_member(pid, member_uid, role="member")
    return {
        "client": client,
        "srv": srv,
        "admin_auth": admin_auth,
        "owner_auth": owner_auth,
        "member_auth": member_auth,
        "outsider_auth": outsider_auth,
        "pid": pid,
        "owner_uid": owner_uid,
        "member_uid": member_uid,
        "outsider_uid": outsider_uid,
    }


async def _upload(
    ctx: dict[str, Any],
    auth: dict[str, str],
    *,
    name: str = "file.txt",
    data: bytes = b"hello",
    parent_id: str | None = None,
    pid: str | None = None,
    headers: dict[str, str] | None = None,
) -> Any:
    files = {"file": (name, io.BytesIO(data), "application/octet-stream")}
    form: dict[str, str] = {}
    if parent_id is not None:
        form["parent_id"] = parent_id
    return await ctx["client"].post(
        f"/api/projects/{pid or ctx['pid']}/assets/upload",
        headers={**auth, **(headers or {})},
        files=files,
        data=form,
    )


async def _mkfolder(
    ctx: dict[str, Any],
    auth: dict[str, str],
    *,
    name: str = "folder",
    parent_id: str | None = None,
    pid: str | None = None,
) -> Any:
    body: dict[str, Any] = {"name": name}
    if parent_id is not None:
        body["parent_id"] = parent_id
    return await ctx["client"].post(
        f"/api/projects/{pid or ctx['pid']}/assets/folders", headers=auth, json=body
    )


async def _list(
    ctx: dict[str, Any],
    auth: dict[str, str],
    *,
    pid: str | None = None,
    headers: dict[str, str] | None = None,
    **params: Any,
) -> Any:
    return await ctx["client"].get(
        f"/api/projects/{pid or ctx['pid']}/assets",
        headers={**auth, **(headers or {})},
        params=params or None,
    )


async def _usage(ctx: dict[str, Any], auth: dict[str, str], *, pid: str | None = None) -> Any:
    return await ctx["client"].get(f"/api/projects/{pid or ctx['pid']}/assets/usage", headers=auth)


async def _download(
    ctx: dict[str, Any],
    auth: dict[str, str],
    node_id: str,
    *,
    pid: str | None = None,
) -> Any:
    return await ctx["client"].get(
        f"/api/projects/{pid or ctx['pid']}/assets/{node_id}/download", headers=auth
    )


def _assets_root(srv: Any) -> Path:
    return srv.services.paths.project_assets_dir


def _files_under(root: Path) -> list[Path]:
    if not root.exists():
        return []
    return sorted(p for p in root.rglob("*") if p.is_file())


def _asset_rows(srv: Any, pid: str) -> tuple[int, int]:
    with srv.services.db.connect() as conn:
        nodes = conn.execute(
            "SELECT COUNT(*) AS n FROM project_asset_nodes WHERE project_id = ?", (pid,)
        ).fetchone()
        versions = conn.execute(
            "SELECT COUNT(*) AS n FROM project_asset_versions WHERE project_id = ?", (pid,)
        ).fetchone()
    return int(nodes["n"]), int(versions["n"])


# ---------------------------------------------------------------------------
# OpenAPI surface
# ---------------------------------------------------------------------------


async def test_assets_openapi_has_typed_responses_and_project_tag(env_with_provider: Any) -> None:
    _client, srv, _admin_auth = env_with_provider
    spec = build_app(srv).openapi()
    tags = {tag["name"]: tag for tag in spec["tags"]}
    assert tags["projects"]["description"]
    paths = spec["paths"]
    list_schema = paths["/api/projects/{project_id}/assets"]["get"]["responses"]["200"]["content"][
        "application/json"
    ]["schema"]
    upload_schema = paths["/api/projects/{project_id}/assets/upload"]["post"]["responses"]["201"][
        "content"
    ]["application/json"]["schema"]
    folder_schema = paths["/api/projects/{project_id}/assets/folders"]["post"]["responses"]["201"][
        "content"
    ]["application/json"]["schema"]
    usage_schema = paths["/api/projects/{project_id}/assets/usage"]["get"]["responses"]["200"][
        "content"
    ]["application/json"]["schema"]
    assert list_schema["$ref"].endswith("AssetPageResponse")
    assert upload_schema["$ref"].endswith("AssetUploadResponse")
    assert folder_schema["$ref"].endswith("AssetNodeResponse")
    assert usage_schema["$ref"].endswith("AssetUsageResponse")
    assert "/api/projects/{project_id}/assets/{node_id}/download" in paths


# ---------------------------------------------------------------------------
# Upload → list → usage → download round trip
# ---------------------------------------------------------------------------


async def test_upload_list_usage_download_roundtrip(env_with_provider: Any) -> None:
    ctx = await _base(env_with_provider)
    data = "熊宝资产".encode() * 10
    r = await _upload(ctx, ctx["owner_auth"], name="报告.txt", data=data)
    assert r.status_code == 201, r.text
    payload = r.json()
    assert set(payload) == _ITEM_KEYS | {"version"}
    assert set(payload["version"]) == _VERSION_KEYS
    assert payload["kind"] == "file"
    assert payload["name"] == "报告.txt"
    assert payload["size_bytes"] == len(data)
    assert payload["media_type"] == "text/plain"
    assert payload["parent_node_id"]  # the hidden root, exposed as a plain id
    assert payload["version"]["size_bytes"] == len(data)
    assert payload["version"]["sha256"] == hashlib.sha256(data).hexdigest()
    node_id = payload["node_id"]
    version_id = payload["version"]["version_id"]

    # Member sees the same node after refresh.
    r = await _list(ctx, ctx["member_auth"])
    assert r.status_code == 200, r.text
    page = r.json()
    assert set(page) == {"items", "total", "limit", "offset", "has_more"}
    assert page["total"] == 1 and page["has_more"] is False
    assert set(page["items"][0]) == _ITEM_KEYS
    assert page["items"][0]["node_id"] == node_id
    assert page["items"][0]["name"] == "报告.txt"

    r = await _usage(ctx, ctx["member_auth"])
    assert r.status_code == 200, r.text
    assert r.json() == {"file_count": 1, "total_bytes": len(data)}

    # Download answers the same bytes with a conservative, attachment-forced
    # response; the non-ASCII name uses the RFC 5987 form.
    r = await _download(ctx, ctx["member_auth"], node_id)
    assert r.status_code == 200, r.text
    assert r.content == data
    assert r.headers["content-type"].startswith("application/octet-stream")
    assert r.headers["x-content-type-options"] == "nosniff"
    disposition = r.headers["content-disposition"]
    assert disposition.startswith("attachment")
    assert "filename*=UTF-8''" in disposition
    assert "%E6%8A%A5%E5%91%8A.txt" in disposition  # 报告.txt percent-encoded

    # A second member upload of identical bytes hashes the same but stays a
    # distinct node/version with its own physical object (no aliasing).
    r2 = await _upload(ctx, ctx["member_auth"], name="副本.txt", data=data)
    assert r2.status_code == 201, r2.text
    payload2 = r2.json()
    assert payload2["version"]["sha256"] == payload["version"]["sha256"]
    assert payload2["version"]["version_id"] != version_id
    objects = _files_under(_assets_root(ctx["srv"]))
    assert len(objects) == 2
    r = await _usage(ctx, ctx["owner_auth"])
    assert r.json() == {"file_count": 2, "total_bytes": 2 * len(data)}


async def test_zero_byte_upload_roundtrip(env_with_provider: Any) -> None:
    ctx = await _base(env_with_provider)
    r = await _upload(ctx, ctx["owner_auth"], name="empty.bin", data=b"")
    assert r.status_code == 201, r.text
    payload = r.json()
    assert payload["size_bytes"] == 0
    assert payload["version"]["size_bytes"] == 0
    assert payload["version"]["sha256"] == hashlib.sha256(b"").hexdigest()
    r = await _download(ctx, ctx["owner_auth"], payload["node_id"])
    assert r.status_code == 200 and r.content == b""


# ---------------------------------------------------------------------------
# Folders, filtering, search, pagination
# ---------------------------------------------------------------------------


async def test_folders_filters_search_and_pagination(env_with_provider: Any) -> None:
    ctx = await _base(env_with_provider)
    auth = ctx["owner_auth"]

    r = await _mkfolder(ctx, auth, name="  资料 ")
    assert r.status_code == 201, r.text
    folder = r.json()
    assert set(folder) == _ITEM_KEYS
    assert folder["kind"] == "folder" and folder["name"] == "资料"  # server-trimmed
    assert folder["size_bytes"] is None and folder["media_type"] is None

    for name, data in (
        ("Alpha.txt", b"aaa"),
        ("beta.txt", b"bb"),
        ("Gamma.pdf", b"g"),
    ):
        r = await _upload(ctx, auth, name=name, data=data)
        assert r.status_code == 201, r.text
    r = await _upload(ctx, auth, name="nested.txt", data=b"nn", parent_id=folder["node_id"])
    assert r.status_code == 201, r.text

    # Root listing: files first, then folders, each ordered by folded name.
    r = await _list(ctx, auth)
    names = [item["name"] for item in r.json()["items"]]
    assert names == ["Alpha.txt", "beta.txt", "Gamma.pdf", "资料"]
    assert r.json()["total"] == 4
    # The nested file only shows under its folder.
    r = await _list(ctx, auth, parent_id=folder["node_id"])
    assert [item["name"] for item in r.json()["items"]] == ["nested.txt"]
    assert r.json()["total"] == 1
    # kind filter.
    r = await _list(ctx, auth, kind="folder")
    assert [item["name"] for item in r.json()["items"]] == ["资料"]
    r = await _list(ctx, auth, kind="file")
    assert r.json()["total"] == 3
    # Name search is case-insensitive on the folded key.
    r = await _list(ctx, auth, q="ALPHA")
    assert [item["name"] for item in r.json()["items"]] == ["Alpha.txt"]
    r = await _list(ctx, auth, q="txt")
    assert r.json()["total"] == 2  # Alpha.txt + beta.txt at root; nested is elsewhere
    # Pagination filters before paging and reports stable totals.
    r = await _list(ctx, auth, limit=2, offset=0)
    page = r.json()
    assert page["total"] == 4 and page["has_more"] is True
    assert [item["name"] for item in page["items"]] == ["Alpha.txt", "beta.txt"]
    assert page["limit"] == 2 and page["offset"] == 0
    r = await _list(ctx, auth, limit=2, offset=2)
    page = r.json()
    assert [item["name"] for item in page["items"]] == ["Gamma.pdf", "资料"]
    assert page["has_more"] is False
    r = await _list(ctx, auth, limit=100, offset=10)
    assert r.json()["items"] == [] and r.json()["total"] == 4
    assert r.json()["has_more"] is False

    # Usage counts every committed current file in the project, nested included.
    r = await _usage(ctx, auth)
    assert r.json() == {"file_count": 4, "total_bytes": 3 + 2 + 1 + 2}


async def test_folder_and_unknown_node_download_404(env_with_provider: Any) -> None:
    ctx = await _base(env_with_provider)
    r = await _mkfolder(ctx, ctx["owner_auth"], name="资料")
    folder_id = r.json()["node_id"]
    r = await _download(ctx, ctx["owner_auth"], folder_id)
    assert r.status_code == 404, r.text
    assert r.json()["error"]["code"] == "NOT_FOUND"
    r = await _download(ctx, ctx["owner_auth"], "01ARZ3NDEKTSV4RRFFQ69G5FAV")
    assert r.status_code == 404
    assert r.json()["error"]["code"] == "NOT_FOUND"


# ---------------------------------------------------------------------------
# ACL: outsider / admin-nonmember / revoked member / cross-project → 404
# ---------------------------------------------------------------------------


async def test_outsider_admin_and_unknown_project_get_uniform_404(env_with_provider: Any) -> None:
    ctx = await _base(env_with_provider)
    for auth in (ctx["outsider_auth"], ctx["admin_auth"]):
        responses = [
            await _list(ctx, auth),
            await _usage(ctx, auth),
            await _download(ctx, auth, "01ARZ3NDEKTSV4RRFFQ69G5FAV"),
            await _mkfolder(ctx, auth, name="不该成功"),
            await _upload(ctx, auth, name="不该成功.txt"),
        ]
        for r in responses:
            assert r.status_code == 404, r.text
            assert r.json()["error"]["code"] == "NOT_FOUND"
    # Unknown project ids are indistinguishable from membership failure.
    r = await _list(ctx, ctx["owner_auth"], pid="ghost-project")
    assert r.status_code == 404
    r = await _upload(ctx, ctx["owner_auth"], pid="ghost-project")
    assert r.status_code == 404
    assert _asset_rows(ctx["srv"], "ghost-project") == (0, 0)


async def test_removed_member_gets_404_and_assets_stay_for_others(
    env_with_provider: Any,
) -> None:
    ctx = await _base(env_with_provider)
    r = await _upload(ctx, ctx["member_auth"], name="成员文件.txt", data=b"member-bytes")
    assert r.status_code == 201, r.text
    node_id = r.json()["node_id"]

    r = await ctx["client"].delete(
        f"/api/projects/{ctx['pid']}/members/{ctx['member_uid']}", headers=ctx["owner_auth"]
    )
    assert r.status_code == 204, r.text

    responses = [
        await _list(ctx, ctx["member_auth"]),
        await _usage(ctx, ctx["member_auth"]),
        await _download(ctx, ctx["member_auth"], node_id),
        await _mkfolder(ctx, ctx["member_auth"], name="晚了"),
        await _upload(ctx, ctx["member_auth"], name="晚了.txt"),
    ]
    for r in responses:
        assert r.status_code == 404, r.text
        assert r.json()["error"]["code"] == "NOT_FOUND"

    # Remaining members keep reading and downloading the historic asset.
    r = await _list(ctx, ctx["owner_auth"])
    assert [item["name"] for item in r.json()["items"]] == ["成员文件.txt"]
    r = await _download(ctx, ctx["owner_auth"], node_id)
    assert r.status_code == 200 and r.content == b"member-bytes"


async def test_cross_project_nodes_never_resolve(env_with_provider: Any) -> None:
    ctx = await _base(env_with_provider)
    client, owner_auth = ctx["client"], ctx["owner_auth"]
    r = await client.post("/api/projects", headers=owner_auth, json={"name": "资产项目B"})
    assert r.status_code == 201, r.text
    pid_b = r.json()["project_id"]

    r = await _upload(ctx, owner_auth, name="A侧文件.txt", data=b"A-bytes")
    node_a = r.json()["node_id"]
    r = await _mkfolder(ctx, owner_auth, name="B侧文件夹", pid=pid_b)
    assert r.status_code == 201, r.text
    folder_b = r.json()["node_id"]

    # A's node never downloads under B's project id, even for a member of both.
    r = await _download(ctx, owner_auth, node_a, pid=pid_b)
    assert r.status_code == 404 and r.json()["error"]["code"] == "NOT_FOUND"
    # B's folder is never a valid parent inside A, for listing or writing.
    r = await _list(ctx, owner_auth, parent_id=folder_b)
    assert r.status_code == 404 and r.json()["error"]["code"] == "NOT_FOUND"
    r = await _mkfolder(ctx, owner_auth, name="越界", parent_id=folder_b)
    assert r.status_code == 404 and r.json()["error"]["code"] == "NOT_FOUND"
    r = await _upload(ctx, owner_auth, name="越界.txt", parent_id=folder_b)
    assert r.status_code == 404 and r.json()["error"]["code"] == "NOT_FOUND"
    # The member (A only) cannot even see B's empty listing.
    r = await _list(ctx, ctx["member_auth"], pid=pid_b)
    assert r.status_code == 404
    r = await _download(ctx, ctx["member_auth"], node_a, pid=pid_b)
    assert r.status_code == 404
    # A's tree is intact.
    r = await _list(ctx, owner_auth)
    assert [item["name"] for item in r.json()["items"]] == ["A侧文件.txt"]


# ---------------------------------------------------------------------------
# Conflicts (409) and validation (422)
# ---------------------------------------------------------------------------


async def test_same_parent_name_conflict_409_with_paired_i18n(env_with_provider: Any) -> None:
    ctx = await _base(env_with_provider)
    r = await _upload(ctx, ctx["owner_auth"], name="Notes.txt", data=b"first")
    assert r.status_code == 201, r.text
    first_version = r.json()["version"]["version_id"]

    r = await _upload(ctx, ctx["owner_auth"], name="notes.TXT", data=b"second")
    assert r.status_code == 409, r.text
    envelope = r.json()["error"]
    assert envelope["code"] == "PROJECT_ASSET_NAME_CONFLICT"
    # A folder cannot take an existing file's (folded) name either.
    r = await _mkfolder(ctx, ctx["owner_auth"], name="NOTES.txt")
    assert r.status_code == 409, r.text
    # Paired backend locales drive the message via Accept-Language.
    client = ctx["client"]
    url = f"/api/projects/{ctx['pid']}/assets/folders"
    r = await client.post(
        url,
        headers={**ctx["owner_auth"], "Accept-Language": "zh-CN,zh;q=0.9"},
        json={"name": "NOTES.txt"},
    )
    assert r.status_code == 409
    assert r.json()["error"]["message"] == error_message("PROJECT_ASSET_NAME_CONFLICT", "zh")
    r = await client.post(
        url,
        headers={**ctx["owner_auth"], "Accept-Language": "en"},
        json={"name": "NOTES.txt"},
    )
    assert r.status_code == 409
    assert r.json()["error"]["message"] == error_message("PROJECT_ASSET_NAME_CONFLICT", "en")

    # A subfolder may reuse the name; the winner is untouched (W4 retry → 409).
    r = await _mkfolder(ctx, ctx["owner_auth"], name="Sub")
    assert r.status_code == 201, r.text
    r = await _upload(
        ctx, ctx["owner_auth"], name="Notes.txt", data=b"inner", parent_id=r.json()["node_id"]
    )
    assert r.status_code == 201, r.text
    r = await _list(ctx, ctx["owner_auth"])
    assert r.json()["total"] == 2  # Notes.txt + Sub at root
    nodes, versions = _asset_rows(ctx["srv"], ctx["pid"])
    assert versions == 2  # one per file; the 409s wrote nothing
    assert nodes == 4  # hidden root + Notes.txt + Sub + inner Notes.txt
    r = await _list(ctx, ctx["owner_auth"])
    winner = next(i for i in r.json()["items"] if i["name"] == "Notes.txt")
    r = await _download(ctx, ctx["owner_auth"], winner["node_id"])
    assert r.content == b"first"
    assert first_version


async def test_invalid_names_and_params_422(env_with_provider: Any) -> None:
    ctx = await _base(env_with_provider)
    client, auth = ctx["client"], ctx["owner_auth"]
    folder_url = f"/api/projects/{ctx['pid']}/assets/folders"

    for bad in ("CON.txt", "..", ".", "", "a/b", "x" * 121, "bad\x00name"):
        r = await client.post(folder_url, headers=auth, json={"name": bad})
        assert r.status_code == 422, (bad, r.text)
        assert r.json()["error"]["code"] == "PROJECT_ASSET_INVALID"
    # Extra or missing fields are rejected outright.
    r = await client.post(folder_url, headers=auth, json={})
    assert r.status_code == 422, r.text
    r = await client.post(folder_url, headers=auth, json={"name": "ok", "bogus": 1})
    assert r.status_code == 422, r.text
    # Upload filenames go through the same rules (path parts are baselined).
    for bad_filename in ("..", "CON"):
        r = await _upload(ctx, auth, name=bad_filename)
        assert r.status_code == 422, (bad_filename, r.text)
        assert r.json()["error"]["code"] == "PROJECT_ASSET_INVALID"
    # An empty multipart filename is parsed as a plain form field by Starlette;
    # FastAPI rejects it before the asset route can produce a domain error.
    r = await _upload(ctx, auth, name="")
    assert r.status_code == 422
    assert r.json()["detail"][0]["loc"] == ["body", "file"]
    r = await _upload(ctx, auth, name="some/dir/报告.txt", data=b"x")
    assert r.status_code == 201, r.text
    assert r.json()["name"] == "报告.txt"

    # Query-param validation is handled by FastAPI before the service runs.
    r = await _list(ctx, auth, kind="bogus")
    assert r.status_code == 422, r.text
    for limit in (0, 101):
        r = await _list(ctx, auth, limit=limit)
        assert r.status_code == 422, r.text
    r = await _list(ctx, auth, offset=-1)
    assert r.status_code == 422, r.text
    r = await _list(ctx, auth, q="x" * 121)
    assert r.status_code == 422, r.text
    r = await _list(ctx, auth, q="İ" * 120)
    assert r.status_code == 422, r.text
    assert r.json()["error"]["code"] == "PROJECT_ASSET_INVALID"

    # Nothing half-baked survives the rejected calls: exactly the one upload.
    assert _asset_rows(ctx["srv"], ctx["pid"]) == (2, 1)  # root + 报告.txt
    assert len(_files_under(_assets_root(ctx["srv"]))) == 1


# ---------------------------------------------------------------------------
# Size limit (413)
# ---------------------------------------------------------------------------


async def test_oversize_upload_413_leaves_nothing(env_with_provider: Any, monkeypatch: Any) -> None:
    ctx = await _base(env_with_provider)
    monkeypatch.setattr("octop.api.routers.project_assets._max_upload_bytes", lambda _server: 64)
    r = await _upload(ctx, ctx["owner_auth"], name="big.bin", data=b"z" * 65)
    assert r.status_code == 413, r.text
    assert r.json()["error"]["code"] == "PROJECT_ASSET_TOO_LARGE"
    # No rows, no temps, no finals anywhere under the private root.
    assert _asset_rows(ctx["srv"], ctx["pid"]) == (0, 0)
    assert _files_under(_assets_root(ctx["srv"])) == []
    # Exactly-at-limit uploads pass the declared-size gate.
    r = await _upload(ctx, ctx["owner_auth"], name="edge.bin", data=b"z" * 64)
    assert r.status_code == 201, r.text
    assert r.json()["size_bytes"] == 64


async def test_content_length_precheck_rejects_large_declared_body(
    env_with_provider: Any, monkeypatch: Any
) -> None:
    ctx = await _base(env_with_provider)
    monkeypatch.setattr("octop.api.routers.project_assets._max_upload_bytes", lambda _server: 1)
    r = await _upload(
        ctx,
        ctx["owner_auth"],
        name="declared.bin",
        data=b"x",
        headers={"Content-Length": str(1 + 64 * 1024 + 1)},
    )
    assert r.status_code == 413, r.text
    assert r.json()["error"]["code"] == "PROJECT_ASSET_TOO_LARGE"
    assert _asset_rows(ctx["srv"], ctx["pid"]) == (0, 0)


# ---------------------------------------------------------------------------
# Archived projects: reads stay, writes refuse
# ---------------------------------------------------------------------------


async def test_archived_project_reads_ok_writes_403(env_with_provider: Any) -> None:
    ctx = await _base(env_with_provider)
    srv = ctx["srv"]
    r = await _upload(ctx, ctx["member_auth"], name="归档前.txt", data=b"before")
    assert r.status_code == 201, r.text
    node_id = r.json()["node_id"]
    before = _asset_rows(srv, ctx["pid"])
    with srv.services.db.transaction() as conn:
        conn.execute("UPDATE project_spaces SET archived = 1 WHERE project_id = ?", (ctx["pid"],))

    r = await _list(ctx, ctx["member_auth"])
    assert r.status_code == 200 and r.json()["total"] == 1
    r = await _usage(ctx, ctx["member_auth"])
    assert r.status_code == 200 and r.json() == {"file_count": 1, "total_bytes": 6}
    r = await _download(ctx, ctx["member_auth"], node_id)
    assert r.status_code == 200 and r.content == b"before"

    r = await _upload(ctx, ctx["member_auth"], name="归档后.txt")
    assert r.status_code == 403, r.text
    assert r.json()["error"]["code"] == "FORBIDDEN"
    r = await _mkfolder(ctx, ctx["member_auth"], name="归档后")
    assert r.status_code == 403, r.text
    assert r.json()["error"]["code"] == "FORBIDDEN"
    assert _asset_rows(srv, ctx["pid"]) == before
    assert len(_files_under(_assets_root(srv))) == 1


# ---------------------------------------------------------------------------
# Atomicity: injected commit failure (crash window W3) leaves nothing visible
# ---------------------------------------------------------------------------


async def test_upload_commit_failure_leaves_no_rows_or_objects(
    env_with_provider: Any, monkeypatch: Any
) -> None:
    ctx = await _base(env_with_provider)

    def _boom(*args: object, **kwargs: object) -> None:
        raise RuntimeError("injected version failure")

    monkeypatch.setattr("octop.infra.db.repos.project_assets._insert_asset_version", _boom)
    # The in-process ASGI transport propagates uncaught server exceptions to
    # the test client; the cleanup assertions below are the behavior under test.
    with pytest.raises(RuntimeError, match="injected version failure"):
        await _upload(ctx, ctx["owner_auth"], name="注定回滚.txt", data=b"doomed")
    assert _asset_rows(ctx["srv"], ctx["pid"]) == (0, 0)
    assert _files_under(_assets_root(ctx["srv"])) == []

    # After the injected failure clears, the same name uploads and downloads.
    monkeypatch.undo()
    r = await _upload(ctx, ctx["owner_auth"], name="注定回滚.txt", data=b"doomed")
    assert r.status_code == 201, r.text
    node_id = r.json()["node_id"]
    r = await _download(ctx, ctx["owner_auth"], node_id)
    assert r.status_code == 200 and r.content == b"doomed"


# ---------------------------------------------------------------------------
# Leak surface
# ---------------------------------------------------------------------------


async def test_asset_responses_never_leak_paths_keys_or_secrets(
    env_with_provider: Any,
) -> None:
    ctx = await _base(env_with_provider)
    srv = ctx["srv"]
    data = b"leak-check-bytes"
    r = await _upload(ctx, ctx["owner_auth"], name="泄漏检查.txt", data=data)
    assert r.status_code == 201, r.text
    payload = r.json()
    node_id = payload["node_id"]
    version_id = payload["version"]["version_id"]

    texts = [r.text]
    r = await _list(ctx, ctx["owner_auth"])
    texts.append(r.text)
    r = await _usage(ctx, ctx["owner_auth"])
    texts.append(r.text)
    r = await _download(ctx, ctx["owner_auth"], node_id)
    texts.append(str(dict(r.headers)))

    home_root = str(srv.services.paths.root)
    for text in texts:
        for secret in (
            home_root,
            "project-assets",
            "object_key",
            "_tmp",
            ".part",
            f"{ctx['pid']}/{version_id}",
        ):
            assert secret not in text, secret
    # The upload payload carries only safe fields.
    assert set(payload) == _ITEM_KEYS | {"version"}
    assert set(payload["version"]) == _VERSION_KEYS
