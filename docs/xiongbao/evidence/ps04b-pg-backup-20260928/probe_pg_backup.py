"""Real PG full archive roundtrip; disposable local cluster and synthetic bytes."""

from __future__ import annotations

import argparse
import copy
import hashlib
import io
import json
import os
import platform
import socket
import subprocess
import sys
import tarfile
import tempfile
import traceback
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

parser = argparse.ArgumentParser()
parser.add_argument("--repo", type=Path, required=True)
parser.add_argument("--output", type=Path, required=True)
parser.add_argument("--expected-sha", required=True)
parser.add_argument("--git-exe", type=Path, required=True)
args = parser.parse_args()
repo = args.repo.resolve()
output = args.output.resolve()
assert output.is_relative_to(repo.parent / "output")
assert os.name == "posix", "Run with the verified WSL PostgreSQL clients"
output.mkdir(parents=True, exist_ok=True)
sys.path[:0] = [str(repo), str(repo / "src")]

import PIL  # noqa: E402
import psycopg  # noqa: E402
from PIL import Image  # noqa: E402

import octop  # noqa: E402
from octop.config import DatabaseConfig  # noqa: E402
from octop.infra.backup.system_archive import (  # noqa: E402
    create_system_backup,
    restore_system_backup,
)
from octop.infra.db.migrate import run_migrations  # noqa: E402
from octop.infra.db.pool import PostgresPool  # noqa: E402
from octop.infra.db.repos.project_todo_comments import (  # noqa: E402
    NewCommentImage,
    ProjectTodoCommentRepo,
)
from octop.infra.db.repos.users import UserRepo  # noqa: E402
from octop.infra.errors import ErrorCode, OctopError  # noqa: E402
from octop.infra.projects.todo_comment_image_storage import (  # noqa: E402
    ProjectTodoCommentImageStorage,
)
from octop.infra.utils.paths import PathLayout  # noqa: E402
from octop.infra.utils.ulid import new_ulid  # noqa: E402

git_exe = args.git_exe.resolve()
assert git_exe.is_file() and git_exe.name == "git.exe"
windows_repo = subprocess.check_output(["wslpath", "-w", str(repo)], text=True).strip()


def source_head():
    # The canonical checkout has a Windows worktree gitdir; do not rewrite it for WSL.
    return subprocess.check_output(
        [str(git_exe), "-C", windows_repo, "rev-parse", "HEAD"], text=True
    ).strip()


source = source_head()
assert source == args.expected_sha
assert Path(octop.__file__).resolve() == repo / "src/octop/__init__.py"
base = Path(tempfile.mkdtemp(prefix="xiongbao-ps04b-backup-")).resolve()
assert base.parent == Path("/tmp") and base.name.startswith("xiongbao-ps04b-backup-")
cluster = base / "cluster"
pg_bin = Path("/usr/lib/postgresql/18/bin")
for tool in ["initdb", "pg_ctl", "createdb", "pg_dump", "pg_restore"]:
    assert (pg_bin / tool).is_file(), tool
os.environ["PATH"] = str(pg_bin) + os.pathsep + os.environ.get("PATH", "")
for name in [
    "PGHOST",
    "PGPORT",
    "PGUSER",
    "PGDATABASE",
    "PGDATA",
    "PGOPTIONS",
    "PGPASSWORD",
    "PGSERVICE",
    "PGSERVICEFILE",
]:
    os.environ.pop(name, None)
with socket.socket() as probe_socket:
    probe_socket.bind(("127.0.0.1", 0))
    port = probe_socket.getsockname()[1]
pg_user = "ps04b_qa"
db_names = {label: f"ps04b_{label}_{uuid4().hex[:12]}" for label in ["source", "target"]}
report = {
    "source": source,
    "repo": str(repo),
    "octop_module": octop.__file__,
    "platform": platform.platform(),
    "python": sys.version.split()[0],
    "psycopg": psycopg.__version__,
    "Pillow": PIL.__version__,
    "temp_base": str(base),
    "cluster": str(cluster),
    "port": port,
    "databases": db_names,
    "provider_used": False,
    "outcome": "RUNNING",
    "pg_commands": [],
    "negative_cases": [],
    "cleanup": {},
}
real_run = subprocess.run
pools = []
started = False


def save():
    (output / "pg-backup-result.json").write_text(
        json.dumps(report, indent=2, ensure_ascii=False, default=str) + "\n", encoding="utf-8"
    )


def checkpoint(stage):
    report["stage"] = stage
    save()
    print(json.dumps({"stage": stage, "source": source}), flush=True)


def observed_run(command, *positional, **keywords):
    result = real_run(command, *positional, **keywords)
    if isinstance(command, (list, tuple)) and command:
        executable = Path(command[0])
        if executable.name in {"pg_dump", "pg_restore"}:
            assert executable.resolve() == (pg_bin / executable.name).resolve()
            report["pg_commands"].append(
                {
                    "tool": executable.name,
                    "restores_database": executable.name == "pg_restore" and "--dbname" in command,
                    "returncode": result.returncode,
                    "stderr": str(result.stderr or "").strip(),
                }
            )
    return result


def run_pg(tool, *arguments):
    return subprocess.run(
        [str(pg_bin / tool), *map(str, arguments)],
        capture_output=True,
        text=True,
        timeout=40,
        check=True,
    )


def identity(pool, database, *, fresh=False):
    with pool.connect() as conn:
        actual = conn.execute(
            "SELECT current_database(), inet_server_port(), current_user"
        ).fetchone()
        data_dir = str(conn.execute("SHOW data_directory").fetchone()[0])
        assert actual[0] == database and actual[1] == port and actual[2] == pg_user
        assert Path(data_dir).resolve() == cluster.resolve()
        if fresh:
            assert (
                conn.execute("SELECT to_regclass('public._schema_version')").fetchone()[0] is None
            )
    return {"database": actual[0], "port": actual[1], "user": actual[2], "data_directory": data_dir}


def verify_binding(pool, config, label):
    pool_identity = identity(pool, db_names[label])
    with psycopg.connect(config.postgresql_conninfo()) as direct:
        actual = direct.execute(
            "SELECT current_database(), inet_server_port(), current_user"
        ).fetchone()
        data_dir = direct.execute("SHOW data_directory").fetchone()[0]
        assert actual == (db_names[label], port, pg_user)
        assert Path(data_dir).resolve() == cluster.resolve()
    return pool_identity


def seed(pool, paths, label):
    users = UserRepo(pool)
    owner = users.create(username=label + "_owner", password_hash="synthetic-hash", role="admin")
    member = users.create(username=label + "_member", password_hash="synthetic-hash", role="user")
    outsider = users.create(
        username=label + "_outsider", password_hash="synthetic-hash", role="user"
    )
    project, markdown_todo, plain_todo = new_ulid(), new_ulid(), new_ulid()
    with pool.transaction() as conn:
        conn.execute(
            "INSERT INTO project_spaces(project_id,creator_user_id,name,created_at,updated_at) "
            "VALUES (?,?,?,1,1)",
            (project, owner, label + " synthetic project"),
        )
        conn.execute(
            "INSERT INTO project_members(project_id,user_id,role,joined_at) "
            "VALUES (?,?,?,1),(?,?,?,1)",
            (project, owner, "owner", project, member, "member"),
        )
        for todo_id, description, description_format in [
            (markdown_todo, "# synthetic heading\n\n**bold**", "markdown"),
            (plain_todo, "literal <b> and # plain", "plain"),
        ]:
            conn.execute(
                "INSERT INTO project_todos(todo_id,project_id,creator_user_id,title,description,"
                "description_format,created_at,updated_at) VALUES (?,?,?,?,?,?,1,1)",
                (
                    todo_id,
                    project,
                    owner,
                    label + " synthetic todo",
                    description,
                    description_format,
                ),
            )
    comments = ProjectTodoCommentRepo(pool)
    text_comment = comments.create_comment(
        project_id=project,
        todo_id=plain_todo,
        author_user_id=member,
        body=label + " synthetic text",
        client_request_id=str(uuid4()),
    )
    image_records, image_bytes = [], {}
    paths.project_todo_comment_images.mkdir(mode=0o700)
    for image_format in ["PNG", "JPEG"]:
        image_id = new_ulid()
        object_key = f"{project}/{image_id}"
        buffer = io.BytesIO()
        Image.new("RGB", (3, 2), "red" if label == "source" else "blue").save(buffer, image_format)
        payload = buffer.getvalue()
        path = paths.project_todo_comment_images / object_key
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        path.write_bytes(payload)
        path.chmod(0o600)
        image_records.append(
            NewCommentImage(
                image_id=image_id,
                object_key=object_key,
                size_bytes=len(payload),
                sha256=hashlib.sha256(payload).hexdigest(),
                media_type="image/png" if image_format == "PNG" else "image/jpeg",
            )
        )
        image_bytes[object_key] = payload
    image_comment = comments.create_comment(
        project_id=project,
        todo_id=markdown_todo,
        author_user_id=member,
        body="",
        client_request_id=str(uuid4()),
        images=image_records,
    )
    assert text_comment.outcome == image_comment.outcome == "created"
    assert text_comment.row is not None and image_comment.row is not None
    return {
        "owner": owner,
        "member": member,
        "outsider": outsider,
        "project": project,
        "markdown_todo": markdown_todo,
        "plain_todo": plain_todo,
        "image_comment": image_comment.row.comment_id,
        "text_comment": text_comment.row.comment_id,
        "images": image_records,
        "bytes": image_bytes,
    }


def db_state(pool):
    tables = [
        "_schema_version",
        "users",
        "project_spaces",
        "project_members",
        "project_todos",
        "project_todo_comments",
        "project_todo_comment_images",
        "project_todo_comment_image_usage",
        "project_events",
    ]
    with pool.connect() as conn:
        state = {
            table: sorted(
                [dict(row) for row in conn.execute("SELECT * FROM " + table).fetchall()],
                key=lambda row: json.dumps(row, sort_keys=True, default=str),
            )
            for table in tables
        }
    return state


def tree_state(paths):
    root = paths.project_todo_comment_images
    return {
        str(path.relative_to(root)): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def state_digest(state):
    return hashlib.sha256(json.dumps(state, sort_keys=True, default=str).encode()).hexdigest()


def corrupt_archive(archive, destination, label, image_name):
    changed = 0
    with tarfile.open(archive, "r:gz") as original, tarfile.open(destination, "w:gz") as altered:
        for member in original.getmembers():
            assert member.isfile(), "Synthetic archive expected only regular file members"
            content = original.extractfile(member).read()
            if member.name == image_name and label == "missing-image":
                changed += 1
                continue
            if member.name == image_name and label == "tampered-bytes":
                content = bytes([content[0] ^ 1]) + content[1:]
                changed += 1
            if member.name == "project-todo-comment-image-index.json" and label == "index-mismatch":
                index = json.loads(content)
                first = sorted(index["objects"])[0]
                index["objects"][first]["sha256"] = "0" * 64
                content = json.dumps(index).encode()
                changed += 1
            info = copy.copy(member)
            info.size = len(content)
            altered.addfile(info, io.BytesIO(content))
    assert changed == 1


def database_restore_calls():
    return sum(item["restores_database"] for item in report["pg_commands"])


try:
    with patch.object(subprocess, "run", observed_run):
        checkpoint("initializing-fresh-cluster")
        report["postgresql"] = run_pg("pg_ctl", "--version").stdout.strip()
        run_pg("initdb", "-D", cluster, "-A", "trust", "-U", pg_user, "--no-instructions")
        started = True  # Inspect our cluster in finally even if startup raises or times out.
        run_pg(
            "pg_ctl",
            "-D",
            cluster,
            "-o",
            f"-h 127.0.0.1 -k {base} -p {port}",
            "-l",
            base / "postgres.log",
            "-w",
            "start",
        )
        layouts, configs, identity_results = {}, {}, {}
        for label, database in db_names.items():
            run_pg("createdb", "-h", "127.0.0.1", "-p", port, "-U", pg_user, database)
            config = DatabaseConfig(
                driver="postgresql", host="127.0.0.1", port=port, database=database, user=pg_user
            )
            pool = PostgresPool(config.postgresql_conninfo(), min_size=1, max_size=4)
            pools.append(pool)
            identity_results[label] = identity(pool, database, fresh=True)
            run_migrations(pool)
            layout = PathLayout(base / (label + "-home"))
            layout.root.mkdir(mode=0o700)
            assert layout.root.resolve().is_relative_to(base)
            layouts[label], configs[label] = layout, config
        report["verified_identities"] = identity_results
        source_pool, target_pool = pools
        source_seed = seed(source_pool, layouts["source"], "source")
        target_seed = seed(target_pool, layouts["target"], "target")
        expected_db = db_state(source_pool)
        expected_tree = tree_state(layouts["source"])
        assert all(row["version"] == 33 for row in expected_db["_schema_version"])
        checkpoint("creating-real-pg-full-archive")
        archive = base / "valid-backup.tar.gz"
        verify_binding(source_pool, configs["source"], "source")
        create_system_backup(
            paths=layouts["source"],
            agent_rows=[],
            pool=source_pool,
            db_config=configs["source"],
            dest=archive,
            include_config=False,
            include_workspaces=False,
            include_skill_packages=False,
            include_plugins=False,
            include_knowledge=False,
            include_chats=False,
        )
        with tarfile.open(archive, "r:gz") as tf:
            manifest = json.loads(tf.extractfile("manifest.json").read())
            index = json.loads(tf.extractfile("project-todo-comment-image-index.json").read())
            assert manifest["database_driver"] == "postgresql" and manifest["schema_version"] == 33
            assert manifest["database_dump_format"] == "pg_custom"
            assert (
                not manifest["includes_chats"] and manifest["includes_project_todo_comment_images"]
            )
            assert set(index["objects"]) == set(source_seed["bytes"])
            assert tf.extractfile("db/octop.dump").read(5) == b"PGDMP"
            for object_key, payload in source_seed["bytes"].items():
                assert tf.extractfile("project-todo-comment-images/" + object_key).read() == payload
        report["archive"] = {
            "path": str(archive),
            "bytes": archive.stat().st_size,
            "sha256": hashlib.sha256(archive.read_bytes()).hexdigest(),
            "manifest": manifest,
            "image_index": index,
        }
        checkpoint("restoring-real-pg-full-archive")
        verify_binding(target_pool, configs["target"], "target")
        restored = restore_system_backup(
            archive,
            paths=layouts["target"],
            pool=target_pool,
            db_config=configs["target"],
            restore_config=False,
            preserve_users=False,
        )
        assert database_restore_calls() == 1
        assert all(item["returncode"] == 0 for item in report["pg_commands"])
        target_pool.close()
        pools.remove(target_pool)
        target_pool = PostgresPool(configs["target"].postgresql_conninfo(), min_size=1, max_size=4)
        pools.append(target_pool)
        verify_binding(target_pool, configs["target"], "target")
        actual_db, actual_tree = db_state(target_pool), tree_state(layouts["target"])
        assert actual_db == expected_db and actual_tree == expected_tree
        assert not (set(target_seed["bytes"]) & set(actual_tree))
        assert restored["project_todo_comment_image_files"] == 2
        assert restored["database_driver"] == "postgresql" and restored["schema_version"] == 33
        assert sum(
            row["used_bytes"] for row in actual_db["project_todo_comment_image_usage"]
        ) == sum(len(payload) for payload in source_seed["bytes"].values())
        for object_key, payload in source_seed["bytes"].items():
            with ProjectTodoCommentImageStorage(
                layouts["target"].project_todo_comment_images
            ).read_object(object_key) as handle:
                assert handle.read() == payload
        report["roundtrip"] = {
            "restore_result": restored,
            "tables_equal": True,
            "table_counts": {key: len(rows) for key, rows in actual_db.items()},
            "source_state_sha256": state_digest(expected_db),
            "target_state_sha256": state_digest(actual_db),
            "image_files": actual_tree,
            "image_bytes_equal": True,
            "old_target_objects_removed": True,
            "usage_matches_bytes": True,
        }
        checkpoint("rejecting-three-corrupted-real-pg-archives")
        image_name = "project-todo-comment-images/" + sorted(source_seed["bytes"])[0]
        for label, expected_message in [
            ("tampered-bytes", "comment image archive bytes differ from index"),
            ("missing-image", "comment image archive objects are incomplete"),
            ("index-mismatch", "comment image archive index differs from database"),
        ]:
            bad_archive = base / (label + ".tar.gz")
            corrupt_archive(archive, bad_archive, label, image_name)
            before_db, before_tree = db_state(target_pool), tree_state(layouts["target"])
            before_calls = database_restore_calls()
            verify_binding(target_pool, configs["target"], "target")
            try:
                restore_system_backup(
                    bad_archive,
                    paths=layouts["target"],
                    pool=target_pool,
                    db_config=configs["target"],
                    restore_config=False,
                    preserve_users=False,
                )
            except OctopError as error:
                assert error.code == ErrorCode.SLASH_BAD_ARGS and expected_message in str(error)
                message = str(error)
            else:
                raise AssertionError("Corrupt archive unexpectedly restored: " + label)
            assert database_restore_calls() == before_calls
            assert (
                db_state(target_pool) == before_db and tree_state(layouts["target"]) == before_tree
            )
            report["negative_cases"].append(
                {
                    "label": label,
                    "error": message,
                    "database_unchanged": True,
                    "image_tree_unchanged": True,
                    "database_restore_calls": 0,
                }
            )
        checkpoint("checking-restored-image-repository-authorization")
        comment_repo = ProjectTodoCommentRepo(target_pool)
        allowed, refused = 0, 0
        for image in source_seed["images"]:
            positional = (
                source_seed["project"],
                source_seed["markdown_todo"],
                source_seed["image_comment"],
                image.image_id,
            )
            for user_id in [source_seed["owner"], source_seed["member"]]:
                assert comment_repo.get_image(*positional, user_id=user_id) is not None
                allowed += 1
            assert comment_repo.get_image(*positional, user_id=source_seed["outsider"]) is None
            refused += 1
            assert (
                comment_repo.get_image(
                    source_seed["project"],
                    source_seed["plain_todo"],
                    source_seed["image_comment"],
                    image.image_id,
                    user_id=source_seed["member"],
                )
                is None
            )
            refused += 1
        with target_pool.transaction() as conn:
            conn.execute(
                "DELETE FROM project_members WHERE project_id=? AND user_id=?",
                (source_seed["project"], source_seed["member"]),
            )
        for image in source_seed["images"]:
            assert (
                comment_repo.get_image(
                    source_seed["project"],
                    source_seed["markdown_todo"],
                    source_seed["image_comment"],
                    image.image_id,
                    user_id=source_seed["member"],
                )
                is None
            )
            refused += 1
        report["repository_acl"] = {
            "allowed": allowed,
            "refused": refused,
            "revoked_member_refused": 2,
            "HTTP_not_exercised": True,
        }
        assert (
            db_state(source_pool) == expected_db and tree_state(layouts["source"]) == expected_tree
        )
        report["source_unchanged"] = True
        assert source == source_head()
        report["outcome"] = "PASS_REAL_PG_ARCHIVE_ROUNDTRIP_AND_THREE_NEGATIVES"
except Exception:
    report["outcome"] = "FAIL"
    report["exception"] = traceback.format_exc()
finally:
    close_errors = []
    for pool in reversed(pools):
        try:
            pool.close()
        except Exception as error:
            close_errors.append(repr(error))
    report["cleanup"]["pool_close_errors"] = close_errors
    if started:
        try:
            assert cluster.resolve().is_relative_to(base) and base.parent == Path("/tmp")
            before_status = real_run(
                [str(pg_bin / "pg_ctl"), "-D", str(cluster), "status"],
                capture_output=True,
                text=True,
                timeout=10,
            )
            assert before_status.returncode in {0, 3}
            if before_status.returncode == 0:
                stopped = real_run(
                    [str(pg_bin / "pg_ctl"), "-D", str(cluster), "-m", "fast", "-w", "stop"],
                    capture_output=True,
                    text=True,
                    timeout=40,
                )
                report["cleanup"]["pg_ctl_stop_code"] = stopped.returncode
                assert stopped.returncode == 0
            status = real_run(
                [str(pg_bin / "pg_ctl"), "-D", str(cluster), "status"],
                capture_output=True,
                text=True,
                timeout=10,
            )
            report["cleanup"]["pg_ctl_status_code"] = status.returncode
            assert status.returncode == 3
            with socket.socket() as probe_socket:
                probe_socket.settimeout(1)
                assert probe_socket.connect_ex(("127.0.0.1", port)) != 0
            report["cleanup"]["port_closed"] = True
        except Exception:
            report["cleanup"]["error"] = traceback.format_exc()
    report["cleanup"]["temp_retained"] = True
    report["cleanup"]["recursive_delete_performed"] = False
    if close_errors or report["cleanup"].get("error"):
        report["outcome"] = "FAIL_CLEANUP"
    save()
print(
    json.dumps(
        {
            "outcome": report["outcome"],
            "temp_base": str(base),
            "output": str(output),
            "cleanup": report["cleanup"],
        }
    ),
    flush=True,
)
sys.exit(0 if report["outcome"].startswith("PASS_") else 2)
