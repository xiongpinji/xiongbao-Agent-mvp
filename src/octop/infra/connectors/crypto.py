"""Fernet encryption for connector credentials."""

from __future__ import annotations

import json
from typing import Any

from cryptography.fernet import Fernet

from octop.infra.db.repos.secrets import SecretRepo

_FERNET_KEY = "connector_fernet"


def _get_fernet(repo: SecretRepo) -> Fernet:
    raw = repo.get_or_create(_FERNET_KEY, Fernet.generate_key)
    return Fernet(raw)


def encrypt_credentials(repo: SecretRepo, payload: dict[str, Any]) -> bytes:
    f = _get_fernet(repo)
    return f.encrypt(json.dumps(payload, ensure_ascii=False).encode("utf-8"))


def decrypt_credentials(repo: SecretRepo, blob: bytes) -> dict[str, Any]:
    f = _get_fernet(repo)
    data = json.loads(f.decrypt(blob).decode("utf-8"))
    return data if isinstance(data, dict) else {}


def encrypt_with_key(raw_key: bytes, payload: bytes) -> bytes:
    """Encrypt an already validated envelope without acquiring or changing a key."""
    return Fernet(raw_key).encrypt(payload)


def decrypt_with_key(raw_key: bytes, blob: bytes) -> bytes:
    """Authenticate bytes with the supplied existing key; failures propagate."""
    return Fernet(raw_key).decrypt(blob)
