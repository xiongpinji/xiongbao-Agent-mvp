"""Remote PS04C cluster names have POSIX semantics on every test host."""

from pathlib import PureWindowsPath

import pytest

from tests.support.postgresql import validate_ps04c_cluster_path


@pytest.mark.parametrize(
    "value",
    [
        "/tmp/xiongbao-ps04c-http-a/data",
        "/tmp/xiongbao-ps04c-http-run_123/pgdata",
        "/tmp/xiongbao-ps04c-http-run-one/data.v1",
        "/tmp/xiongbao-ps04c-http-run/C:data",
    ],
)
def test_valid_cluster_name_is_preserved_on_windows_and_posix(value: str) -> None:
    assert validate_ps04c_cluster_path(value) == value


def test_remote_name_is_not_rewritten_into_the_windows_namespace() -> None:
    value = "/tmp/xiongbao-ps04c-http-run/data"
    assert str(PureWindowsPath(value)) != value
    assert validate_ps04c_cluster_path(value) == value


@pytest.mark.parametrize(
    "value",
    [
        "",
        "tmp/xiongbao-ps04c-http-run/data",
        "/var/tmp/xiongbao-ps04c-http-run/data",
        "/tmp-other/xiongbao-ps04c-http-run/data",
        "/tmp/xiongbao-ps04c-http-/data",
        "/tmp/xiongbao-ps04c-httpx-run/data",
        "/tmp/xiongbao-ps04c-http/data",
        "/tmp/xiongbao-ps04c-http-run",
        "/tmp/xiongbao-ps04c-http-run/data/nested",
        "/tmp/xiongbao-ps04c-http-run/..",
        "/tmp/xiongbao-ps04c-http-run/../data",
        "/tmp/xiongbao-ps04c-http-run/data/../../elsewhere",
        "/tmp/./xiongbao-ps04c-http-run/data",
        "/tmp/xiongbao-ps04c-http-run/./data",
        "/tmp//xiongbao-ps04c-http-run/data",
        "/tmp/xiongbao-ps04c-http-run//data",
        "/tmp/xiongbao-ps04c-http-run/data/",
        "//tmp/xiongbao-ps04c-http-run/data",
        "///tmp/xiongbao-ps04c-http-run/data",
        "C:/tmp/xiongbao-ps04c-http-run/data",
        "C:tmp/xiongbao-ps04c-http-run/data",
        r"C:\tmp\xiongbao-ps04c-http-run\data",
        r"\\server\share\xiongbao-ps04c-http-run\data",
        r"/tmp/xiongbao-ps04c-http-run\data",
        "/tmp/xiongbao-ps04c-http-run/data\x00",
    ],
)
def test_invalid_cluster_name_is_rejected(value: str) -> None:
    with pytest.raises(ValueError, match="canonical"):
        validate_ps04c_cluster_path(value)
