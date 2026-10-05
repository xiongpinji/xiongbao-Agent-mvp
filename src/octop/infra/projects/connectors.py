"""Internal project public connector storage; no transport/runtime activation."""

from __future__ import annotations

import json
import unicodedata
from collections.abc import Mapping
from urllib.parse import urlsplit, urlunsplit

from cryptography.fernet import Fernet

from octop.infra.connectors.crypto import decrypt_with_key, encrypt_with_key
from octop.infra.db.repos.project_public_connectors import (
    KIND,
    MAX_REVISION,
    PURPOSE,
    ProjectPublicConnectorRepo,
    PublicConnectorWrite,
    revision,
)
from octop.infra.db.repos.project_public_connectors import (
    PublicConnectorCollection as PublicConnectorCollection,
)
from octop.infra.db.repos.project_public_connectors import (
    PublicConnectorFailure as PublicConnectorFailure,
)
from octop.infra.utils.ulid import new_ulid


def _display(name: str, description: str) -> tuple[str, str]:
    if type(name) is not str or type(description) is not str:
        raise PublicConnectorFailure("invalid_display")
    if any(unicodedata.category(char).startswith("C") for char in name + description):
        raise PublicConnectorFailure("invalid_display")
    name, description = (
        unicodedata.normalize("NFC", name).strip(),
        unicodedata.normalize("NFC", description),
    )
    if (
        not 1 <= len(name) <= 64
        or len(description) > 256
        or any(unicodedata.category(char).startswith("C") for char in name + description)
    ):
        raise PublicConnectorFailure("invalid_display")
    return name, description


def _credential(value: Mapping[str, object]) -> dict[str, object]:
    if set(value) != {"endpoint", "bearer_token"}:
        raise PublicConnectorFailure("invalid_credential")
    endpoint, token = value["endpoint"], value["bearer_token"]
    if type(endpoint) is not str or type(token) is not str:
        raise PublicConnectorFailure("invalid_credential")
    try:
        parsed = urlsplit(endpoint)
        if (
            len(endpoint) > 2048
            or len(endpoint.encode("utf-8")) > 8192
            or parsed.scheme != "https"
            or not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
            or parsed.query
            or parsed.fragment
            or "?" in endpoint
            or "#" in endpoint
            or any(
                char.isspace() or unicodedata.category(char).startswith("C") for char in endpoint
            )
        ):
            raise ValueError
        # Validate the port; lower case only scheme/host, preserve path and slash.
        port = parsed.port
        host = parsed.hostname.lower()
        if ":" in host:
            host = "[" + host + "]"
        netloc = host + (":" + str(port) if port is not None else "")
        endpoint = urlunsplit(("https", netloc, parsed.path, "", ""))
        if (
            not token
            or len(token.encode("utf-8")) > 4096
            or any(not 33 <= ord(char) <= 126 for char in token)
        ):
            raise ValueError
    except (ValueError, UnicodeError):
        raise PublicConnectorFailure("invalid_credential") from None
    return {
        "transport": "streamable_http",
        "endpoint": endpoint,
        "headers": {"Authorization": "Bearer " + token},
    }


def _safe_display(
    name: str, description: str, credential: dict[str, object], *, original_endpoint: object
) -> None:
    headers = credential["headers"]
    assert isinstance(headers, dict)
    token = str(headers["Authorization"])[7:]
    if any(
        secret in text
        for secret in (str(credential["endpoint"]), str(original_endpoint), token)
        for text in (name, description)
    ):
        raise PublicConnectorFailure("invalid_display")


def _envelope(
    project_id: str, connector_id: str, grant: int, credential: dict[str, object]
) -> dict[str, object]:
    return {
        "purpose": PURPOSE,
        "payload_version": 1,
        "project_id": project_id,
        "connector_id": connector_id,
        "grant_revision": revision(grant),
        "kind": KIND,
        "credentials": credential,
    }


def _encrypt(key: bytes, envelope: dict[str, object]) -> bytes:
    try:
        raw = json.dumps(
            envelope, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
        ).encode("utf-8")
        if len(raw) > 16384:
            raise PublicConnectorFailure("invalid_credential")
        blob = encrypt_with_key(key, raw)
        if len(blob) > 32768:
            raise PublicConnectorFailure("invalid_credential")
        return blob
    except PublicConnectorFailure:
        raise
    except Exception:
        raise PublicConnectorFailure("credential_unavailable") from None


def _decrypt(tx: PublicConnectorWrite, key: bytes) -> dict[str, object]:
    assert tx.resource is not None
    try:
        raw = decrypt_with_key(key, bytes(tx.resource["credential_blob"]))
        if len(raw) > 16384:
            raise ValueError

        # Reject duplicate JSON members rather than accepting a last-value alias.
        def unique(pairs: list[tuple[str, object]]) -> dict[str, object]:
            result: dict[str, object] = {}
            for key_name, value in pairs:
                if key_name in result:
                    raise ValueError
                result[key_name] = value
            return result

        envelope = json.loads(raw, object_pairs_hook=unique)
        if type(envelope) is not dict or set(envelope) != {
            "purpose",
            "payload_version",
            "project_id",
            "connector_id",
            "grant_revision",
            "kind",
            "credentials",
        }:
            raise ValueError
        if (
            type(envelope["payload_version"]) is not int
            or envelope["payload_version"] != 1
            or envelope["purpose"] != PURPOSE
            or envelope["project_id"] != tx.project_id
            or envelope["connector_id"] != tx.resource["connector_id"]
            or envelope["kind"] != KIND
            or revision(envelope["grant_revision"]) != tx.resource["grant_revision"]
        ):
            raise ValueError
        credential = envelope["credentials"]
        if (
            type(credential) is not dict
            or set(credential) != {"transport", "endpoint", "headers"}
            or credential["transport"] != "streamable_http"
        ):
            raise ValueError
        headers = credential["headers"]
        if (
            type(headers) is not dict
            or set(headers) != {"Authorization"}
            or type(headers["Authorization"]) is not str
            or not headers["Authorization"].startswith("Bearer ")
        ):
            raise ValueError
        canonical = _credential(
            {"endpoint": credential["endpoint"], "bearer_token": headers["Authorization"][7:]}
        )
        if canonical != credential:
            raise ValueError
        return canonical
    except Exception:
        raise PublicConnectorFailure("credential_unavailable") from None


class ProjectPublicConnectorService:
    def __init__(self, repo: ProjectPublicConnectorRepo) -> None:
        self._repo = repo

    def list_safe(self, project_id: str, actor_id: int) -> PublicConnectorCollection:
        return self._repo.list_safe(project_id, actor_id)

    def create(
        self,
        project_id: str,
        actor_id: int,
        *,
        expected_project_revision: int,
        display_name: str,
        description: str,
        credential: Mapping[str, object],
    ) -> PublicConnectorCollection:
        with self._repo.mutation(
            project_id, actor_id, expected_project_revision=expected_project_revision
        ) as tx:
            name, description = _display(display_name, description)
            config = _credential(credential)
            _safe_display(name, description, config, original_endpoint=credential["endpoint"])
            key = tx.locked_key(candidate=Fernet.generate_key())
            connector_id = new_ulid()
            return tx.apply(
                "created",
                connector_id,
                display_name=name,
                description=description,
                blob=_encrypt(key, _envelope(project_id, connector_id, 1, config)),
                changed_fields=("display_name", "description", "credentials"),
            )

    def replace_credentials(
        self,
        project_id: str,
        actor_id: int,
        connector_id: str,
        *,
        expected_project_revision: int,
        expected_grant_revision: int,
        credential: Mapping[str, object],
    ) -> PublicConnectorCollection:
        with self._repo.mutation(
            project_id,
            actor_id,
            connector_id=connector_id,
            expected_project_revision=expected_project_revision,
            expected_grant_revision=expected_grant_revision,
        ) as tx:
            assert tx.resource is not None
            if tx.resource["state"] != "active":
                raise PublicConnectorFailure("resource_revoked")
            config = _credential(credential)
            _safe_display(
                tx.resource["display_name"],
                tx.resource["description"],
                config,
                original_endpoint=credential["endpoint"],
            )
            key = tx.locked_key()
            if _decrypt(tx, key) == config:
                return tx.collection()
            if expected_grant_revision == MAX_REVISION:
                raise PublicConnectorFailure("revision_exhausted")
            blob = _encrypt(
                key, _envelope(project_id, connector_id, expected_grant_revision + 1, config)
            )
            return tx.apply(
                "credentials_replaced", connector_id, blob=blob, changed_fields=("credentials",)
            )

    def rename(
        self,
        project_id: str,
        actor_id: int,
        connector_id: str,
        *,
        expected_project_revision: int,
        expected_grant_revision: int,
        display_name: str,
        description: str,
    ) -> PublicConnectorCollection:
        with self._repo.mutation(
            project_id,
            actor_id,
            connector_id=connector_id,
            expected_project_revision=expected_project_revision,
            expected_grant_revision=expected_grant_revision,
        ) as tx:
            assert tx.resource is not None
            if tx.resource["state"] != "active":
                raise PublicConnectorFailure("resource_revoked")
            name, description = _display(display_name, description)
            fields = tuple(
                field
                for field, value in (("display_name", name), ("description", description))
                if tx.resource[field] != value
            )
            if not fields:
                return tx.collection()
            return tx.apply(
                "renamed",
                connector_id,
                display_name=name,
                description=description,
                changed_fields=fields,
            )

    def revoke(
        self,
        project_id: str,
        actor_id: int,
        connector_id: str,
        *,
        expected_project_revision: int,
        expected_grant_revision: int,
    ) -> PublicConnectorCollection:
        with self._repo.mutation(
            project_id,
            actor_id,
            connector_id=connector_id,
            expected_project_revision=expected_project_revision,
            expected_grant_revision=expected_grant_revision,
            allow_archived=True,
        ) as tx:
            assert tx.resource is not None
            if tx.resource["state"] == "revoked":
                return tx.collection()
            return tx.apply("revoked", connector_id, changed_fields=("state", "credentials"))
