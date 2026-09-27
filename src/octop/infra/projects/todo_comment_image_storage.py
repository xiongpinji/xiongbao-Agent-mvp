"""Private bytes and strict image validation for project todo comments.

The storage engine is reused under a separate root from project assets. Its
ULID-only paths are never exposed by the comment DTO or a public static mount.
"""

from __future__ import annotations

import hashlib
import warnings
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
from typing import BinaryIO

from PIL import Image, UnidentifiedImageError

from octop.infra.projects.asset_storage import (
    ProjectAssetStorage,
    open_regular_file_no_follow,
)
from octop.infra.utils.safe_dirs import ensure_plain_directory_chain

MAX_COMMENT_IMAGE_BYTES = 8 * 1024 * 1024
MAX_COMMENT_IMAGE_PIXELS = 40_000_000

_FORMATS = {"PNG": "image/png", "JPEG": "image/jpeg", "WEBP": "image/webp"}


class CommentImageInvalid(ValueError):
    """Unsupported, disguised, corrupt, or excessive image bytes."""


class CommentImageTooLarge(CommentImageInvalid):
    """The file exceeds the exact per-image byte cap."""


@dataclass(frozen=True)
class ValidatedCommentImage:
    data: bytes
    media_type: str
    size_bytes: int
    sha256: str


def _magic_type(data: bytes) -> str | None:
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return None


def validate_comment_image(data: bytes, claimed_media_type: str) -> ValidatedCommentImage:
    """Require matching MIME, genuine format signature, and complete decoding."""
    if not isinstance(data, bytes) or not data:
        raise CommentImageInvalid("image bytes are required")
    if len(data) > MAX_COMMENT_IMAGE_BYTES:
        raise CommentImageTooLarge("comment image exceeds 8 MiB")
    magic_type = _magic_type(data)
    if magic_type is None or claimed_media_type != magic_type:
        raise CommentImageInvalid("unsupported or mismatched image media type")
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(BytesIO(data)) as probe:
                if _FORMATS.get(probe.format or "") != magic_type:
                    raise CommentImageInvalid("image decoder disagrees with signature")
                if probe.width * probe.height > MAX_COMMENT_IMAGE_PIXELS:
                    raise CommentImageInvalid("image dimensions exceed the safety cap")
                probe.verify()
            # verify() checks the container; load() checks every pixel stream.
            with Image.open(BytesIO(data)) as decoded:
                if _FORMATS.get(decoded.format or "") != magic_type:
                    raise CommentImageInvalid("image decoder disagrees with signature")
                decoded.load()
    except (
        OSError,
        ValueError,
        SyntaxError,
        UnidentifiedImageError,
        Image.DecompressionBombError,
        Image.DecompressionBombWarning,
    ) as exc:
        raise CommentImageInvalid("image bytes could not be fully decoded") from exc
    return ValidatedCommentImage(
        data=data,
        media_type=magic_type,
        size_bytes=len(data),
        sha256=hashlib.sha256(data).hexdigest(),
    )


class ProjectTodoCommentImageStorage(ProjectAssetStorage):
    """Atomic private object storage with a full plain-directory-chain check."""

    def __init__(self, root: Path) -> None:
        super().__init__(root)

    def ensure_root(self) -> Path:
        ensure_plain_directory_chain(self.root, private_from=self.root)
        return super().ensure_root()

    def publish(self, temp: Path, project_id: str, image_id: str) -> Path:
        self.ensure_root()
        return super().publish(temp, project_id, image_id)

    def read_object(self, object_key: str) -> BinaryIO:
        self.ensure_root()
        path = self.open_download(object_key)
        return open_regular_file_no_follow(path)
