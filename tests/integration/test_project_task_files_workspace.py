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

POSIX symlink escapes are exercised directly on POSIX; the Windows-only case
below builds a real NTFS junction (``mklink /J``) inside the managed root and
proves the same refusals through the authenticated HTTP routes — it skips on
non-Windows platforms, where junctions do not exist.
"""

from __future__ import annotations

import base64
import os
import subprocess
from collections.abc import Callable
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

    # Legal markdown globs (including the md shortcut branch) stay functional
    # on BOTH platforms: the literal wildcard must never be lstat/resolve()d
    # (Windows would raise WinError 123 on the pattern text).
    (root / "ok.md").write_text("md", encoding="utf-8")
    for pattern in ("*.md", "**/*.md"):
        r = await client.get(
            f"{base}/glob",
            params={"pattern": pattern, "from_workspace": "true"},
            headers=auth,
        )
        assert r.status_code == 200, r.text
        assert any("ok.md" in str(e) for e in r.json())

    # Grep over a single regular file is a supported backend base form.
    r = await client.get(
        f"{base}/grep",
        params={"pattern": "private", "path": "seed.txt", "from_workspace": "true"},
        headers=auth,
    )
    assert r.status_code == 200, r.text
    matches = r.json()
    assert matches
    assert all("seed.txt" in str(m.get("path", "")) for m in matches)

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
        ("/\\host\\share\\secret.txt", "true"),
        ("\\/host/share/secret.txt", "true"),
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


@pytest.mark.skipif(
    os.name == "nt", reason="POSIX symlink; Windows junctions covered by the nt-only case below"
)
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


_JUNCTION_SECRET = "junction-escape-secret"


def _require_windows_junction(link: Path, target: Path) -> None:
    """Create a real NTFS junction ``link`` → ``target`` or fail loudly.

    ``mklink /J`` is unprivileged (unlike symlinks); the argv list keeps the
    arguments separated, so cmd's own quoting preserves paths with spaces. A
    machine that cannot create the junction must FAIL with an environment
    reason — a silently skipped escape proof is not gate evidence.
    """
    result = subprocess.run(
        ["cmd", "/c", "mklink", "/J", str(link), str(target)],
        capture_output=True,
        check=False,
    )
    if result.returncode != 0 or not link.is_junction():
        stderr = result.stderr.decode("utf-8", errors="replace").strip()
        pytest.fail(
            f"environment: cannot create NTFS junction {link} -> {target}: "
            f"rc={result.returncode} stderr={stderr!r}"
        )
    # Direction proof: the link resolves to the outside target directory.
    assert os.path.realpath(link) == os.path.realpath(target)


@pytest.mark.skipif(os.name != "nt", reason="Windows junction only")
async def test_windows_junction_escape_is_refused_on_private_http_routes(
    env_with_provider: Any, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A real NTFS junction must not open any private-route escape (030A gate).

    Mirrors the POSIX symlink case above with an actual reparse point inside
    the DB-marked managed root: read/download/preview/list of an EXISTING
    outside file and a write to a NOT-yet-created outside file are refused
    (403 internal) through the authenticated owner routes, with zero outside
    bytes served and zero outside side effects.
    """
    ctx = await _ws_ctx(env_with_provider, monkeypatch)
    client, rt, root = ctx["client"], ctx["rt"], ctx["root"]
    auth = ctx["owner_auth"]
    base = f"/api/agents/{rt}/workspace"
    outside = tmp_path / "outside"
    outside.mkdir()
    secret = outside / "secret.txt"
    secret.write_text(_JUNCTION_SECRET, encoding="utf-8")
    link = root / "link"
    _require_windows_junction(link, outside)
    try:
        # Fixture validity: the managed positive read still works ...
        r = await client.get(
            f"{base}/file", params={"path": "seed.txt", "from_workspace": "true"}, headers=auth
        )
        assert r.status_code == 200, r.text
        assert r.json()["content"] == "private bytes"
        # ... while a root listing fails closed because its subtree contains
        # the planted junction (S3), without exposing outside names or bytes.
        r = await client.get(
            f"{base}/tree", params={"path": "/", "from_workspace": "true"}, headers=auth
        )
        _forbidden(r)
        assert "secret.txt" not in r.text
        assert _JUNCTION_SECRET not in r.text

        # Read / download the EXISTING outside file through the junction.
        for route in ("file", "download"):
            r = await client.get(
                f"{base}/{route}",
                params={"path": "link/secret.txt", "from_workspace": "true"},
                headers=auth,
            )
            _forbidden(r)
            assert _JUNCTION_SECRET.encode() not in r.content

        # Preview the outside file through the junction.
        r = await client.get(
            f"/api/agents/{rt}/media/preview", params={"source": "link/secret.txt"}, headers=auth
        )
        _forbidden(r)
        assert _JUNCTION_SECRET.encode() not in r.content

        # List the junction directory itself, and glob through it.
        r = await client.get(
            f"{base}/tree", params={"path": "link", "from_workspace": "true"}, headers=auth
        )
        _forbidden(r)
        assert "secret.txt" not in r.text
        r = await client.get(
            f"{base}/glob", params={"pattern": "link/*", "from_workspace": "true"}, headers=auth
        )
        _forbidden(r)
        assert "secret.txt" not in r.text

        # Write a NOT-yet-created outside file through the junction: refused
        # before any byte exists outside.
        r = await client.put(
            f"{base}/file",
            params={"path": "link/new.txt", "from_workspace": "true"},
            headers=auth,
            json={"content": "pwned"},
        )
        _forbidden(r)
        assert not (outside / "new.txt").exists()
    finally:
        # Detach the reparse point itself with the non-recursive native API
        # (RemoveDirectoryW via os.rmdir): it never follows or deletes the
        # target tree, and no shell deletion is involved. Runs BEFORE fixture
        # cleanup so teardown only ever sees ordinary directories.
        link.rmdir()

    # Cleanup evidence: junction gone; outside tree and secret untouched;
    # no refused request created anything outside.
    assert not link.exists()
    assert outside.is_dir()
    assert secret.read_text(encoding="utf-8") == _JUNCTION_SECRET
    assert sorted(p.name for p in outside.iterdir()) == ["secret.txt"]


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


# ---------------------------------------------------------------------------
# 030A RD-2 / RD-3: full-route planted-reparse-point matrix, running-root and
# managed-ancestor replacement (A15/A16), bounded-listing 503 (A13).
#
# These are CHECK-TIME refusals of STABLE plants over authenticated HTTP —
# the design's RED/GREEN security gate. Nothing here claims the check→use
# window (design N1/N2/N3) is closed.
# ---------------------------------------------------------------------------

_CANARY_HTTP = "HTTP-ROUTE-CANARY-SECRET"


def _plant_dir_link(link: Path, target: Path) -> Callable[[], None]:
    """Plant a real directory reparse point; return the platform cleanup.

    POSIX plants a symlink; Windows plants a real NTFS junction through
    ``_require_windows_junction`` (fail-loud on environment problems — a
    silently skipped escape proof is not gate evidence). The cleanup removes
    ONLY the link and never touches the target tree.
    """
    if os.name == "nt":
        _require_windows_junction(link, target)
        return link.rmdir
    link.symlink_to(target, target_is_directory=True)
    assert link.is_symlink(), f"environment: cannot plant symlink {link}"
    return link.unlink


def _rename_dir_or_fail(src: Path, dst: Path) -> None:
    """Rename a directory or fail loudly with an environment reason."""
    try:
        src.rename(dst)
    except OSError as exc:
        pytest.fail(f"environment: cannot rename {src} -> {dst}: {exc}")


async def _assert_owner_surface_restored(ctx: dict[str, Any]) -> None:
    """RD-2 GREEN arm: the full owner listing/read surface serves again.

    Run after the plants are removed, so a refusal above is proven to be
    caused by the reparse points themselves and not by fixture breakage.
    """
    client, rt = ctx["client"], ctx["rt"]
    auth = ctx["owner_auth"]
    base = f"/api/agents/{rt}/workspace"

    r = await client.get(
        f"{base}/tree", params={"path": "/", "from_workspace": "true"}, headers=auth
    )
    assert r.status_code == 200, r.text
    assert any(str(e.get("path", "")).endswith("seed.txt") for e in r.json())
    assert _CANARY_HTTP not in r.text
    r = await client.get(
        f"{base}/file", params={"path": "seed.txt", "from_workspace": "true"}, headers=auth
    )
    assert r.status_code == 200, r.text
    assert r.json()["content"] == "private bytes"
    r = await client.get(
        f"{base}/download", params={"path": "seed.txt", "from_workspace": "true"}, headers=auth
    )
    assert r.status_code == 200, r.text
    assert r.content == b"private bytes"
    r = await client.get(
        f"{base}/glob", params={"pattern": "*", "from_workspace": "true"}, headers=auth
    )
    assert r.status_code == 200, r.text
    assert any("seed.txt" in str(e) for e in r.json())
    assert _CANARY_HTTP not in r.text
    (ctx["root"] / "ok.md").write_text("md", encoding="utf-8")
    for pattern in ("*.md", "**/*.md"):
        r = await client.get(
            f"{base}/glob",
            params={"pattern": pattern, "from_workspace": "true"},
            headers=auth,
        )
        assert r.status_code == 200, r.text
        assert any("ok.md" in str(e) for e in r.json())
        assert _CANARY_HTTP not in r.text
    r = await client.get(
        f"{base}/grep", params={"pattern": "private", "from_workspace": "true"}, headers=auth
    )
    assert r.status_code == 200, r.text
    assert _CANARY_HTTP not in r.text
    r = await client.get(
        f"{base}/grep",
        params={"pattern": "private", "path": "seed.txt", "from_workspace": "true"},
        headers=auth,
    )
    assert r.status_code == 200, r.text
    assert r.json()
    assert _CANARY_HTTP not in r.text


async def _route_matrix_refusals(
    ctx: dict[str, Any],
    *,
    link: str,
    outside: Path,
    file_link: str | None,
) -> None:
    """A7/A8: every owner file route refuses the planted link (403 internal).

    Covers the intermediate-component link (``{link}/...``) on every content
    and write route, the link itself as final component on every route kind,
    the POSIX-only final-component FILE symlink (*file_link*), the GLM P3 #3
    overwrite refusal onto an EXISTING outside file, and the root-level
    listing routes that fail closed while any reparse point lives in the
    tree (S3) — including the glob ``**/*.md`` shortcut branch. Ends with
    the anti-over-refusal pin: a clean content route still serves.
    """
    client, rt = ctx["client"], ctx["rt"]
    auth = ctx["owner_auth"]
    base = f"/api/agents/{rt}/workspace"
    canary = _CANARY_HTTP.encode()

    # ---- reads through the planted directory link (intermediate component)
    for route in ("file", "download"):
        r = await client.get(
            f"{base}/{route}",
            params={"path": f"{link}/secret.txt", "from_workspace": "true"},
            headers=auth,
        )
        _forbidden(r)
        assert canary not in r.content
    # doc GET checks the editable extension FIRST, so use a .docx-shaped
    # path: the strict path refusal (403) still precedes any backend I/O.
    r = await client.get(
        f"{base}/doc",
        params={"path": f"{link}/report.docx", "from_workspace": "true"},
        headers=auth,
    )
    _forbidden(r)
    assert canary not in r.content
    r = await client.get(
        f"/api/agents/{rt}/media/preview",
        params={"source": f"{link}/secret.txt"},
        headers=auth,
    )
    _forbidden(r)
    assert canary not in r.content

    # ---- writes through the link are refused BEFORE any outside byte (A7)
    r = await client.put(
        f"{base}/file",
        params={"path": f"{link}/pwn.txt", "from_workspace": "true"},
        headers=auth,
        json={"content": "pwned"},
    )
    _forbidden(r)
    r = await client.put(
        f"{base}/doc",
        params={"path": f"{link}/pwn.docx", "from_workspace": "true"},
        headers=auth,
        json={"content": "# pwned"},
    )
    _forbidden(r)
    r = await client.post(
        f"{base}/upload",
        params={"path": f"{link}/pwn-up.txt", "from_workspace": "true"},
        headers=auth,
        files={"file": ("ignored.txt", b"pwned", "text/plain")},
    )
    _forbidden(r)
    assert not (outside / "pwn.txt").exists()
    assert not (outside / "pwn.docx").exists()
    assert not (outside / "pwn-up.txt").exists()

    # ---- GLM P3 #3: overwrite write onto an EXISTING outside file refused
    r = await client.put(
        f"{base}/file",
        params={"path": f"{link}/secret.txt", "from_workspace": "true"},
        headers=auth,
        json={"content": "pwned"},
    )
    _forbidden(r)
    assert (outside / "secret.txt").read_text(encoding="utf-8") == _CANARY_HTTP

    # ---- the link as final component: every route kind refuses (A8)
    for route in ("file", "download", "tree"):
        r = await client.get(
            f"{base}/{route}",
            params={"path": link, "from_workspace": "true"},
            headers=auth,
        )
        _forbidden(r)
        assert canary not in r.content
    r = await client.get(f"/api/agents/{rt}/media/preview", params={"source": link}, headers=auth)
    _forbidden(r)
    r = await client.get(
        f"{base}/grep",
        params={"pattern": "CANARY", "path": link, "from_workspace": "true"},
        headers=auth,
    )
    _forbidden(r)
    r = await client.get(
        f"{base}/glob",
        params={"pattern": "*", "path": link, "from_workspace": "true"},
        headers=auth,
    )
    _forbidden(r)

    if file_link is not None:
        # POSIX final-component FILE symlink: reads, overwrite write, upload,
        # preview, listing — all refused, outside bytes unchanged.
        for route in ("file", "download"):
            r = await client.get(
                f"{base}/{route}",
                params={"path": file_link, "from_workspace": "true"},
                headers=auth,
            )
            _forbidden(r)
            assert canary not in r.content
        r = await client.put(
            f"{base}/file",
            params={"path": file_link, "from_workspace": "true"},
            headers=auth,
            json={"content": "pwned"},
        )
        _forbidden(r)
        r = await client.post(
            f"{base}/upload",
            params={"path": file_link, "from_workspace": "true"},
            headers=auth,
            files={"file": ("ignored.txt", b"pwned", "text/plain")},
        )
        _forbidden(r)
        r = await client.get(
            f"/api/agents/{rt}/media/preview", params={"source": file_link}, headers=auth
        )
        _forbidden(r)
        r = await client.get(
            f"{base}/tree",
            params={"path": file_link, "from_workspace": "true"},
            headers=auth,
        )
        _forbidden(r)
        assert (outside / "secret.txt").read_text(encoding="utf-8") == _CANARY_HTTP

    # ---- root-level listing routes fail closed while the plant is live (S3)
    r = await client.get(
        f"{base}/tree", params={"path": "/", "from_workspace": "true"}, headers=auth
    )
    _forbidden(r)
    assert "secret.txt" not in r.text
    for pattern in ("*", "**", "**/*.md", f"{link}/*"):
        r = await client.get(
            f"{base}/glob",
            params={"pattern": pattern, "from_workspace": "true"},
            headers=auth,
        )
        _forbidden(r)
        assert "secret.txt" not in r.text
    r = await client.get(
        f"{base}/grep",
        params={"pattern": "CANARY", "path": "/", "from_workspace": "true"},
        headers=auth,
    )
    _forbidden(r)
    assert canary not in r.content

    # ---- anti-over-refusal: a clean content route still serves
    r = await client.get(
        f"{base}/file", params={"path": "seed.txt", "from_workspace": "true"}, headers=auth
    )
    assert r.status_code == 200, r.text
    assert r.json()["content"] == "private bytes"


@pytest.mark.skipif(
    os.name == "nt", reason="POSIX symlink; Windows junctions covered by the nt-only case below"
)
async def test_planted_symlink_full_route_matrix_refuses_and_restores(
    env_with_provider: Any, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """RD-2 (POSIX arm): every owner route refuses stable planted links.

    Plants a directory symlink (intermediate-component position) and a file
    symlink (final-component position) inside the managed root, runs the full
    route matrix, then removes both and proves the whole positive surface
    serves again — with zero outside bytes served and zero outside side
    effects at every step.
    """
    ctx = await _ws_ctx(env_with_provider, monkeypatch)
    root = ctx["root"]
    outside = tmp_path / "rd2-outside"
    (outside / "nested").mkdir(parents=True)
    (outside / "secret.txt").write_text(_CANARY_HTTP, encoding="utf-8")
    (outside / "nested" / "deep.txt").write_text(_CANARY_HTTP, encoding="utf-8")

    link = root / "link"
    seed_link = root / "seed_link"
    remove_link = _plant_dir_link(link, outside)
    seed_link.symlink_to(outside / "secret.txt")
    try:
        await _route_matrix_refusals(ctx, link="link", outside=outside, file_link="seed_link")
    finally:
        remove_link()
        seed_link.unlink(missing_ok=True)

    await _assert_owner_surface_restored(ctx)

    # Zero outside side effects: canary bytes intact, nothing created.
    assert not link.exists()
    assert not seed_link.exists()
    assert sorted(p.name for p in outside.iterdir()) == ["nested", "secret.txt"]
    assert sorted(p.name for p in (outside / "nested").iterdir()) == ["deep.txt"]
    assert (outside / "secret.txt").read_text(encoding="utf-8") == _CANARY_HTTP
    assert (outside / "nested" / "deep.txt").read_text(encoding="utf-8") == _CANARY_HTTP


@pytest.mark.skipif(os.name != "nt", reason="Windows junction only")
async def test_planted_junction_full_route_matrix_refuses_and_restores(
    env_with_provider: Any, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """RD-2 (Windows arm): the same matrix against a real NTFS junction.

    Junctions cannot target files unprivileged, so the final-component arm
    is the junction itself (refused on every route kind); the existing
    outside file is reached — and refused — through it.
    """
    ctx = await _ws_ctx(env_with_provider, monkeypatch)
    root = ctx["root"]
    outside = tmp_path / "rd2-outside-nt"
    (outside / "nested").mkdir(parents=True)
    (outside / "secret.txt").write_text(_CANARY_HTTP, encoding="utf-8")
    (outside / "nested" / "deep.txt").write_text(_CANARY_HTTP, encoding="utf-8")

    link = root / "link"
    remove_link = _plant_dir_link(link, outside)
    try:
        await _route_matrix_refusals(ctx, link="link", outside=outside, file_link=None)
    finally:
        remove_link()

    await _assert_owner_surface_restored(ctx)

    assert not link.exists()
    assert sorted(p.name for p in outside.iterdir()) == ["nested", "secret.txt"]
    assert (outside / "secret.txt").read_text(encoding="utf-8") == _CANARY_HTTP


async def test_planted_hardlink_in_subtree_refuses_every_listing_route(
    env_with_provider: Any, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """S7/S3 HTTP arm: a hard link inside the managed tree is never listed.

    The link is planted inside a subdirectory and points at an outside inode
    (canary); ``tree``/``glob`` never enumerate it, ``grep`` refuses both the
    link itself (S7 final component) and the directory above it (S3 subtree),
    and the outside bytes/entry set stay untouched. Removal restores the full
    owner positive surface.
    """
    ctx = await _ws_ctx(env_with_provider, monkeypatch)
    client, rt, root = ctx["client"], ctx["rt"], ctx["root"]
    auth = ctx["owner_auth"]
    base = f"/api/agents/{rt}/workspace"
    outside = tmp_path / "rd-hardlink-outside"
    outside.mkdir()
    (outside / "secret.txt").write_text(_CANARY_HTTP, encoding="utf-8")
    (root / "sub").mkdir()
    os.link(outside / "secret.txt", root / "sub" / "linked.txt")
    try:
        r = await client.get(
            f"{base}/tree", params={"path": "/", "from_workspace": "true"}, headers=auth
        )
        _forbidden(r)
        assert "linked.txt" not in r.text
        for pattern in ("*", "**", "**/*.md", "sub/*"):
            r = await client.get(
                f"{base}/glob",
                params={"pattern": pattern, "from_workspace": "true"},
                headers=auth,
            )
            _forbidden(r)
            assert "linked.txt" not in r.text
        r = await client.get(
            f"{base}/grep", params={"pattern": _CANARY_HTTP, "from_workspace": "true"}, headers=auth
        )
        _forbidden(r)
        assert _CANARY_HTTP.encode() not in r.content
        r = await client.get(
            f"{base}/grep",
            params={"pattern": "CANARY", "path": "sub", "from_workspace": "true"},
            headers=auth,
        )
        _forbidden(r)
        r = await client.get(
            f"{base}/grep",
            params={"pattern": "CANARY", "path": "sub/linked.txt", "from_workspace": "true"},
            headers=auth,
        )
        _forbidden(r)
        assert outside.joinpath("secret.txt").read_text(encoding="utf-8") == _CANARY_HTTP
        assert sorted(p.name for p in outside.iterdir()) == ["secret.txt"]
    finally:
        (root / "sub" / "linked.txt").unlink()
        (root / "sub").rmdir()

    await _assert_owner_surface_restored(ctx)
