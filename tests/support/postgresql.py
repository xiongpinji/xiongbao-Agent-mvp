"""Helpers for gated live-PostgreSQL tests.

Prefer a dedicated database (tests may ``DROP SCHEMA public CASCADE``)::

    export OCTOP_TEST_DATABASE_URL='postgresql://postgres:postgres@127.0.0.1:15432/octop_test'
"""

from __future__ import annotations

import os
from pathlib import PurePosixPath

import pytest


def validate_ps04c_cluster_path(value: str) -> str:
    """Validate a remote POSIX name; the lease verifies realpath and ownership."""
    path = PurePosixPath(value)
    prefix = "xiongbao-ps04c-http-"
    if (
        "\\" in value
        or "\x00" in value
        or str(path) != value
        or len(path.parts) != 4
        or path.parts[:2] != ("/", "tmp")
        or ".." in path.parts
        or not path.parts[2].startswith(prefix)
        or path.parts[2] == prefix
    ):
        raise ValueError("expected a canonical /tmp/xiongbao-ps04c-http-<suffix>/<leaf> path")
    return value


requires_postgresql = pytest.mark.skipif(
    not os.environ.get("OCTOP_TEST_DATABASE_URL"),
    reason="set OCTOP_TEST_DATABASE_URL to run PostgreSQL tests",
)
