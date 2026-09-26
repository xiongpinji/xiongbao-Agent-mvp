"""030A B4 workspace confinement integration tests for internal file runtimes.

Proves against the real app, the real filesystem backend, and the real managed
root that:

* the owner keeps positive read/write/download/upload/glob/grep/preview on the
  managed ``project-task-files/{agent_id}`` directory ONLY, in the explicit
  ``from_workspace=true`` mode;
* every host-absolute / ``file://`` / drive / UNC / ``~`` / ``..`` / encoded /
  NUL / symlink-escape shape is refused (403), and never serves a host file;
* ``from_workspace=false`` (chat/tool host-absolute mode) is refused outright
  for an internal runtime — even for safe relative names, and before I/O;
* media preview accepts only an explicitly managed-relative source: absolute
  paths and ``file://`` are refused even when they point inside the managed
  root;
* denied mutators (mkdir / delete-file / move / archive import+export) are
  refused for everyone including the owner;
* admins, ``as_user`` impersonation, unrelated users, and project share
  readers are denied on every workspace route — including a preview requested
  through another agent's URL.

POSIX symlink escapes are exercised directly; on Windows the equivalent
junction/reparse-point escape is covered by the resolver's ``relative_to``
containment proof but cannot be created portably here, so those cases skip.
"""

from __future__ import annotations

import base64
import os
from pathlib import Path
from typing import Any

import pytest

from tests.integration.test_project_task_file_mode_api import _share
from tests.integration.test_project_task_files_access import _files_ctx, _forbidden

_PNG_1PX = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
)


async def _ws_ctx(env: Any, monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """File-task ctx with the runtime marked running so workspace I/O resolves.

    ``require_running_workspace`` falls back to ``workspace_for_agent`` only
    while the row says ``running``; the fake runtime never starts a harness,
    so the fallback branch (managed-root BackendWorkspace) is what serves.
    """
    ctx = await _files_ctx(env, monkeypatch)
    srv = ctx["srv"]
    srv.services.agent_repo.set_state(ctx["rt"], "running")
    ctx["root"] = Path(srv.services.paths.project_task_file_runtime_dir(ctx["rt"]))
    assert (ctx["root"] / "seed.txt").read_text(encoding="utf-8") == "private bytes"
    return ctx


def _never_served(resp: Any) -> None:
    assert b"root:" not in resp.content


# ---------------------------------------------------------------------------
# Owner positives: read / write / download / upload / glob / grep / preview
# ---------------------------------------------------------------------------


async def test_owner_positive_surface_stays_inside_managed_root(
    env_with_provider: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    ctx = await _ws_ctx(env_with_provider, monkeypatch)
    client, rt, root = ctx["client"], ctx["rt"], ctx["root"]
    auth = ctx["owner_auth"]
    base = f"/api/agents/{rt}/workspace"

    # Tree (workspace-UI mode) lists the managed root.
    r = await client.get(
        f"{base}/tree", params={"path": "/", "from_workspace": "true"}, headers=auth
    )
    assert r.status_code == 200, r.text
    assert any(str(e.get("path", "")).endswith("seed.txt") for e in r.json())

    # Read text.
    r = await client.get(
        f"{base}/file", params={"path": "seed.txt", "from_workspace": "true"}, headers=auth
    )
    assert r.status_code == 200, r.text
    assert r.json()["content"] == "private bytes"

    # Write text and prove the bytes land inside the managed root.
    r = await client.put(
        f"{base}/file",
        params={"path": "note.txt", "from_workspace": "true"},
        headers=auth,
        json={"content": "b4 write"},
    )
    assert r.status_code == 200, r.text
    assert (root / "note.txt").read_text(encoding="utf-8") == "b4 write"

    # Download streams the managed file only.
    r = await client.get(
        f"{base}/download", params={"path": "seed.txt", "from_workspace": "true"}, headers=auth
    )
    assert r.status_code == 200, r.text
    assert r.headers["content-type"].startswith("application/octet-stream")
    assert r.content == b"private bytes"

    # Upload (multipart filename is workspace-relative for internal runtimes).
    r = await client.post(
        f"{base}/upload",
        params={"from_workspace": "true"},
        headers=auth,
        files={"file": ("up.txt", b"b4 upload", "text/plain")},
    )
    assert r.status_code == 200, r.text
    assert (root / "up.txt").read_bytes() == b"b4 upload"

    # Glob and grep stay inside the root.
    r = await client.get(
        f"{base}/glob", params={"pattern": "*", "from_workspace": "true"}, headers=auth
    )
    assert r.status_code == 200, r.text
    assert any("seed.txt" in str(e) for e in r.json())
    r = await client.get(
        f"{base}/grep", params={"pattern": "private", "from_workspace": "true"}, headers=auth
    )
    assert r.status_code == 200, r.text
    assert isinstance(r.json(), list)

    # Media preview of a managed image works for the owner.
    (root / "dot.png").write_bytes(_PNG_1PX)
    r = await client.get(
        f"/api/agents/{rt}/media/preview", params={"source": "dot.png"}, headers=auth
    )
    assert r.status_code == 200, r.text
    assert r.headers["content-type"].startswith("image/")
    assert r.content == _PNG_1PX


# ---------------------------------------------------------------------------
# Host-path escape matrix on the read route
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("path", "from_workspace"),
    [
        ("/etc/passwd", "false"),
        ("../../etc/passwd", "false"),
        ("../../etc/passwd", "true"),
        ("../../../etc/passwd", "true"),
        # NOTE: ("/etc/passwd", "true") is intentionally absent — in
        # workspace-UI mode the leading "/" means workspace-relative, so the
        # resolver CONTAINS it as "etc/passwd" (404), never a host read.
        # Asserted separately below.
        ("file:///etc/passwd", "false"),
        ("file:///etc/passwd", "true"),
        ("C:\\Windows\\win.ini", "false"),
        ("C:\\Windows\\win.ini", "true"),
        ("\\\\host\\share\\secret.txt", "false"),
        ("\\\\host\\share\\secret.txt", "true"),
        ("//host/share/secret.txt", "true"),
        ("~/.octop/db.sqlite3", "false"),
        ("~/.octop/db.sqlite3", "true"),
        ("sub/../../etc/passwd", "true"),
    ],
)
async def test_host_absolute_and_traversal_paths_are_refused(
    env_with_provider: Any,
    monkeypatch: pytest.MonkeyPatch,
    path: str,
    from_workspace: str,
) -> None:
    ctx = await _ws_ctx(env_with_provider, monkeypatch)
    client, rt = ctx["client"], ctx["rt"]
    r = await client.get(
        f"/api/agents/{rt}/workspace/file",
        params={"path": path, "from_workspace": from_workspace},
        headers=ctx["owner_auth"],
    )
    _forbidden(r)
    _never_served(r)


async def test_encoded_traversal_and_nul_are_refused(
    env_with_provider: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    ctx = await _ws_ctx(env_with_provider, monkeypatch)
    client, rt = ctx["client"], ctx["rt"]
    base = f"/api/agents/{rt}/workspace/file"

    # Percent-encoded "../" arrives decoded at the resolver → refused.
    r = await client.get(
        f"{base}?path=%2e%2e%2f%2e%2e%2fetc%2fpasswd&from_workspace=false",
        headers=ctx["owner_auth"],
    )
    _forbidden(r)
    _never_served(r)

    # NUL byte injection is refused.
    r = await client.get(
        f"{base}?path=seed.txt%00.png&from_workspace=true", headers=ctx["owner_auth"]
    )
    _forbidden(r)

    # Double encoding is NOT decoded twice: the literal "%2e%2e" fragment is a
    # harmless (missing) name inside the managed root — never a host file.
    r = await client.get(
        f"{base}?path=%252e%252e%252f%252e%252e%252fetc%252fpasswd&from_workspace=true",
        headers=ctx["owner_auth"],
    )
    assert r.status_code == 404, r.text
    _never_served(r)


async def test_from_workspace_false_never_maps_host_root(
    env_with_provider: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    ctx = await _ws_ctx(env_with_provider, monkeypatch)
    client, rt = ctx["client"], ctx["rt"]
    # Dashboard-default tree call ("/" + from_workspace=false) means host root
    # for ordinary agents; internal runtimes refuse the mode outright.
    r = await client.get(f"/api/agents/{rt}/workspace/tree", headers=ctx["owner_auth"])
    _forbidden(r)
    r = await client.get(
        f"/api/agents/{rt}/workspace/download",
        params={"path": "/etc/passwd"},
        headers=ctx["owner_auth"],
    )
    _forbidden(r)
    _never_served(r)

    # In workspace-UI mode a leading "/" is workspace-relative by contract:
    # the resolver contains it under the managed root (missing file → 404),
    # and the host /etc/passwd is never read.
    r = await client.get(
        f"/api/agents/{rt}/workspace/file",
        params={"path": "/etc/passwd", "from_workspace": "true"},
        headers=ctx["owner_auth"],
    )
    assert r.status_code == 404, r.text
    _never_served(r)


async def test_from_workspace_false_is_refused_on_every_file_entry(
    env_with_provider: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    ctx = await _ws_ctx(env_with_provider, monkeypatch)
    client, rt, root = ctx["client"], ctx["rt"], ctx["root"]
    auth = ctx["owner_auth"]
    base = f"/api/agents/{rt}/workspace"

    # Safe relative names are refused too: false is never silently upgraded.
    r = await client.get(f"{base}/file", params={"path": "seed.txt"}, headers=auth)
    _forbidden(r)
    r = await client.put(
        f"{base}/file", params={"path": "seed.txt"}, headers=auth, json={"content": "x"}
    )
    _forbidden(r)
    r = await client.get(f"{base}/download", params={"path": "seed.txt"}, headers=auth)
    _forbidden(r)
    r = await client.get(f"{base}/doc", params={"path": "seed.docx"}, headers=auth)
    _forbidden(r)
    r = await client.put(
        f"{base}/doc",
        params={"path": "seed.docx", "from_workspace": "false"},
        headers=auth,
        json={"content": "# x"},
    )
    _forbidden(r)
    r = await client.get(f"{base}/glob", params={"pattern": "*"}, headers=auth)
    _forbidden(r)
    r = await client.get(f"{base}/grep", params={"pattern": "private"}, headers=auth)
    _forbidden(r)
    # Root listing with the dashboard-default host-absolute mode.
    r = await client.get(f"{base}/tree", headers=auth)
    _forbidden(r)
    # Upload with the default host-absolute mode.
    r = await client.post(
        f"{base}/upload",
        headers=auth,
        files={"file": ("up-false.txt", b"nope", "text/plain")},
    )
    _forbidden(r)

    # No refused request wrote anything.
    assert (root / "seed.txt").read_text(encoding="utf-8") == "private bytes"
    assert not (root / "seed.docx").exists()
    assert not (root / "up-false.txt").exists()


@pytest.mark.skipif(os.name == "nt", reason="POSIX symlink; Windows junctions noted in report")
async def test_symlink_escape_is_refused_for_read_write_and_listing(
    env_with_provider: Any, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    ctx = await _ws_ctx(env_with_provider, monkeypatch)
    client, rt, root = ctx["client"], ctx["rt"], ctx["root"]
    auth = ctx["owner_auth"]
    outside = tmp_path / "outside"
    outside.mkdir()
    secret = outside / "secret.txt"
    secret.write_text("host secret", encoding="utf-8")
    (root / "link").symlink_to(outside, target_is_directory=True)
    (root / "seed_link").symlink_to(secret)
    base = f"/api/agents/{rt}/workspace"

    # Read through a symlinked directory.
    r = await client.get(
        f"{base}/file", params={"path": "link/secret.txt", "from_workspace": "true"}, headers=auth
    )
    _forbidden(r)
    assert b"host secret" not in r.content

    # Read through a symlinked file, via download too.
    for route in ("file", "download"):
        r = await client.get(
            f"{base}/{route}",
            params={"path": "seed_link", "from_workspace": "true"},
            headers=auth,
        )
        _forbidden(r)
        assert b"host secret" not in r.content

    # Listing the symlinked directory itself fails the containment proof.
    r = await client.get(
        f"{base}/tree", params={"path": "link", "from_workspace": "true"}, headers=auth
    )
    _forbidden(r)

    # Writing through the symlink is refused BEFORE any byte is created.
    r = await client.put(
        f"{base}/file",
        params={"path": "link/pwn.txt", "from_workspace": "true"},
        headers=auth,
        json={"content": "pwned"},
    )
    _forbidden(r)
    assert not (outside / "pwn.txt").exists()
    assert secret.read_text(encoding="utf-8") == "host secret"


async def test_upload_filename_traversal_is_refused_or_contained(
    env_with_provider: Any, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    ctx = await _ws_ctx(env_with_provider, monkeypatch)
    client, rt, root = ctx["client"], ctx["rt"], ctx["root"]
    auth = ctx["owner_auth"]
    outside = tmp_path / "outside-up"
    outside.mkdir()

    # Traversing multipart filename → refused.
    r = await client.post(
        f"/api/agents/{rt}/workspace/upload",
        headers=auth,
        files={"file": ("../../evil.txt", b"evil", "text/plain")},
    )
    _forbidden(r)
    assert not (outside / "evil.txt").exists()
    assert not (root.parent.parent / "evil.txt").exists()

    # A leading-"/" target in workspace-UI mode is contained under the root.
    r = await client.post(
        f"/api/agents/{rt}/workspace/upload",
        headers=auth,
        params={"path": "/evil2.txt", "from_workspace": "true"},
        files={"file": ("ignored.txt", b"evil2", "text/plain")},
    )
    assert r.status_code == 200, r.text
    assert (root / "evil2.txt").read_bytes() == b"evil2"
    assert not Path("/tmp/evil2.txt").exists()


async def test_glob_pattern_traversal_is_refused(
    env_with_provider: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    ctx = await _ws_ctx(env_with_provider, monkeypatch)
    client, rt = ctx["client"], ctx["rt"]
    r = await client.get(
        f"/api/agents/{rt}/workspace/glob",
        params={"pattern": "../../**", "from_workspace": "true"},
        headers=ctx["owner_auth"],
    )
    _forbidden(r)
    r = await client.get(
        f"/api/agents/{rt}/workspace/grep",
        params={"pattern": "root", "path": "/etc", "from_workspace": "false"},
        headers=ctx["owner_auth"],
    )
    _forbidden(r)
    _never_served(r)


# ---------------------------------------------------------------------------
# Denied mutators — including for the owner
# ---------------------------------------------------------------------------


async def test_denied_mutators_refuse_everyone(
    env_with_provider: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    ctx = await _ws_ctx(env_with_provider, monkeypatch)
    client, rt, root = ctx["client"], ctx["rt"], ctx["root"]
    base = f"/api/agents/{rt}/workspace"

    for auth in (ctx["owner_auth"], ctx["admin_auth"]):
        r = await client.post(f"{base}/mkdir", params={"path": "/newdir"}, headers=auth)
        _forbidden(r)
        r = await client.request(
            "DELETE", f"{base}/file", params={"path": "seed.txt"}, headers=auth
        )
        _forbidden(r)
        r = await client.post(
            f"{base}/move",
            params={"path": "seed.txt"},
            headers=auth,
            json={"destination": "/moved.txt"},
        )
        _forbidden(r)
        r = await client.get(f"{base}/archive", headers=auth)
        _forbidden(r)
        r = await client.post(f"{base}/archive", headers=auth)
        _forbidden(r)

    # Nothing was created, moved, or deleted.
    assert (root / "seed.txt").read_text(encoding="utf-8") == "private bytes"
    assert not (root / "newdir").exists()
    assert not (root / "moved.txt").exists()


# ---------------------------------------------------------------------------
# Identity matrix: admin / as_user / unrelated / share reader
# ---------------------------------------------------------------------------


async def test_non_owner_identities_denied_every_workspace_route(
    env_with_provider: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    ctx = await _ws_ctx(env_with_provider, monkeypatch)
    client, srv, rt, tid = ctx["client"], ctx["srv"], ctx["rt"], ctx["tid"]
    base = f"/api/agents/{rt}/workspace"
    q = {"path": "seed.txt", "from_workspace": "true"}

    _share(srv, tid, ctx["owner_uid"], ctx["uid"]["other"], ctx["pid"])

    denied = [
        (ctx["auth"]["other"], {}),
        (ctx["auth"]["outsider"], {}),
        (ctx["admin_auth"], {}),
        (ctx["admin_auth"], {"as_user": ctx["owner_uid"]}),
        (ctx["owner_auth"], {"as_user": ctx["uid"]["other"]}),
    ]
    for auth, params in denied:
        r = await client.get(f"{base}/tree", headers=auth, params={**q, **params})
        _forbidden(r)
        r = await client.get(f"{base}/file", headers=auth, params={**q, **params})
        _forbidden(r)
        r = await client.get(f"{base}/download", headers=auth, params={**q, **params})
        _forbidden(r)
        _never_served(r)
        r = await client.put(
            f"{base}/file", headers=auth, params={**q, **params}, json={"content": "x"}
        )
        _forbidden(r)


async def test_preview_refuses_host_sources_and_cross_agent_urls(
    env_with_provider: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    ctx = await _ws_ctx(env_with_provider, monkeypatch)
    client, rt = ctx["client"], ctx["rt"]
    auth = ctx["owner_auth"]
    preview = f"/api/agents/{rt}/media/preview"

    # Host-absolute and file:// sources cannot reach the host fallbacks.
    r = await client.get(preview, params={"source": "/etc/passwd"}, headers=auth)
    _forbidden(r)
    _never_served(r)
    r = await client.get(preview, params={"source": "file:///etc/passwd"}, headers=auth)
    _forbidden(r)
    _never_served(r)

    # An internal runtime cannot be previewed through another agent's URL:
    # the gate follows the agent the source actually resolves to.
    other = ctx["auth"]["other"]
    sneaky = f"/agents/{rt}/workspace/seed.txt"
    r = await client.get(
        f"/api/agents/{ctx['source']}/media/preview", params={"source": sneaky}, headers=other
    )
    _forbidden(r)
    _never_served(r)
    r = await client.get(preview, params={"source": sneaky}, headers=other)
    _forbidden(r)

    # The URL's internal runtime must not switch to an owner-accessible
    # ordinary agent just because the source names that agent.
    ordinary_source = f"/agents/{ctx['source']}/workspace/seed.txt"
    r = await client.get(preview, params={"source": ordinary_source}, headers=auth)
    _forbidden(r)
    _never_served(r)

    # Host shapes are refused even when they point INSIDE the managed root:
    # only the explicit managed-relative preview form is accepted.
    (ctx["root"] / "dot.png").write_bytes(_PNG_1PX)
    inside = str(ctx["root"] / "dot.png")
    for source in (inside, f"file://{inside}", "/dot.png"):
        r = await client.get(preview, params={"source": source}, headers=auth)
        _forbidden(r)
        _never_served(r)
    r = await client.get(preview, params={"source": "dot.png"}, headers=auth)
    assert r.status_code == 200, r.text
    assert r.content == _PNG_1PX
