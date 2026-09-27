"""Private project-todo comment image validation and storage."""

from __future__ import annotations

import os
import time
from io import BytesIO
from pathlib import Path

import pytest
from PIL import Image

from octop.infra.projects.todo_comment_image_storage import (
    CommentImageInvalid,
    ProjectTodoCommentImageStorage,
    validate_comment_image,
)
from octop.infra.utils.ulid import new_ulid


def _image(format_name: str) -> bytes:
    out = BytesIO()
    Image.new("RGB", (4, 4), "red").save(out, format=format_name)
    return out.getvalue()


def test_validate_real_image_and_reject_spoofed_or_truncated_bytes() -> None:
    png = _image("PNG")
    accepted = validate_comment_image(png, "image/png")
    assert accepted.media_type == "image/png"
    assert accepted.size_bytes == len(png)
    assert len(accepted.sha256) == 64

    for data, claimed in (
        (png, "image/jpeg"),
        (png[:20], "image/png"),
        (_image("GIF"), "image/gif"),
        (b"<svg xmlns='http://www.w3.org/2000/svg'/>", "image/svg+xml"),
    ):
        with pytest.raises(CommentImageInvalid):
            validate_comment_image(data, claimed)


@pytest.mark.parametrize(
    "format_name, media_type",
    [("PNG", "image/png"), ("JPEG", "image/jpeg"), ("WEBP", "image/webp")],
)
def test_all_three_supported_formats_decode_completely(
    format_name: str,
    media_type: str,
) -> None:
    data = _image(format_name)
    assert validate_comment_image(data, media_type).media_type == media_type


def test_decoder_bomb_warning_maps_to_safe_invalid_image(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(Image, "MAX_IMAGE_PIXELS", 10)
    with pytest.raises(CommentImageInvalid):
        validate_comment_image(_image("PNG"), "image/png")


def test_image_object_stays_in_dedicated_private_root(tmp_path: Path) -> None:
    root = tmp_path / "private" / "project-todo-comment-images"
    storage = ProjectTodoCommentImageStorage(root)
    project_id, image_id = new_ulid(), new_ulid()
    image = validate_comment_image(_image("JPEG"), "image/jpeg")
    temp, written = storage.write_temp(image_id, BytesIO(image.data), max_bytes=image.size_bytes)
    assert written.sha256 == image.sha256
    final = storage.publish(temp, project_id, image_id)
    assert final.parent == root / project_id
    assert final.read_bytes() == image.data
    assert "project-assets" not in str(final)
    with storage.read_object(f"{project_id}/{image_id}") as stream:
        assert stream.read() == image.data


def test_cross_process_orphan_snapshot_keeps_fresh_publish_until_grace(tmp_path: Path) -> None:
    storage = ProjectTodoCommentImageStorage(tmp_path / "project-todo-comment-images")
    project_id, image_id = new_ulid(), new_ulid()
    image = validate_comment_image(_image("PNG"), "image/png")
    temp, _ = storage.write_temp(image_id, BytesIO(image.data), max_bytes=image.size_bytes)
    final = storage.publish(temp, project_id, image_id)
    now = time.time()
    # Another process can read the DB inventory before this upload commits.
    assert storage.reclaim_orphans(set(), now=now).finals_removed == 0
    assert final.exists()
    old = now - 24 * 60 * 60 - 10
    os.utime(final, (old, old))
    key = f"{project_id}/{image_id}"
    assert storage.reclaim_orphans({key}, now=now).finals_removed == 0
    assert final.exists()
    assert storage.reclaim_orphans(set(), now=now).finals_removed == 1
    assert not final.exists()
