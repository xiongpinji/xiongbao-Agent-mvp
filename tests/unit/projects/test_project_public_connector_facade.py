from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from octop.infra.db.repos.project_public_connectors import (
    PublicConnectorCollection,
    PublicConnectorFailure,
)
from octop.infra.errors import OctopError
from octop.infra.projects.service import ProjectService


def test_old_construction_has_explicit_missing_connector_failure() -> None:
    service = ProjectService(SimpleNamespace())
    with pytest.raises(OctopError) as error:
        service.list_public_connectors("p", 1)
    assert error.value.code.value == "PROJECT_PUBLIC_CONNECTOR_UNAVAILABLE"
    assert error.value.status == 503


@pytest.mark.parametrize(
    "reason,code,status,safe",
    [
        ("invalid_revision", "PROJECT_PUBLIC_CONNECTOR_INVALID", 422, "invalid_payload"),
        ("invalid_display", "PROJECT_PUBLIC_CONNECTOR_INVALID", 422, "invalid_payload"),
        ("invalid_credential", "PROJECT_PUBLIC_CONNECTOR_INVALID", 422, "invalid_payload"),
        ("invalid_action", "PROJECT_PUBLIC_CONNECTOR_INVALID", 422, "invalid_payload"),
        ("stale_revision", "PROJECT_PUBLIC_CONNECTORS_CHANGED", 409, "stale_revision"),
        ("resource_revoked", "PROJECT_PUBLIC_CONNECTOR_REVOKED", 409, "resource_revoked"),
        ("revision_exhausted", "PROJECT_PUBLIC_CONNECTOR_UNAVAILABLE", 409, "revision_exhausted"),
        (
            "credential_unavailable",
            "PROJECT_PUBLIC_CONNECTOR_UNAVAILABLE",
            503,
            "configuration_unavailable",
        ),
        (
            "key_state_invalid",
            "PROJECT_PUBLIC_CONNECTOR_UNAVAILABLE",
            503,
            "configuration_unavailable",
        ),
        ("key_missing", "PROJECT_PUBLIC_CONNECTOR_UNAVAILABLE", 503, "configuration_unavailable"),
        (
            "storage_failure",
            "PROJECT_PUBLIC_CONNECTOR_UNAVAILABLE",
            503,
            "configuration_unavailable",
        ),
        ("not_found", "NOT_FOUND", 404, None),
        ("forbidden", "FORBIDDEN", 403, None),
        ("project_archived", "FORBIDDEN", 403, "project_archived"),
        (
            "unknown-secret-internal",
            "PROJECT_PUBLIC_CONNECTOR_UNAVAILABLE",
            503,
            "configuration_unavailable",
        ),
    ],
)
def test_reason_mapping(reason: str, code: str, status: int, safe: str | None) -> None:
    domain = Mock()
    domain.list_safe.side_effect = PublicConnectorFailure(reason)
    service = ProjectService(SimpleNamespace(), public_connector_service=domain)
    with pytest.raises(OctopError) as error:
        service.list_public_connectors("p", 7)
    assert error.value.code.value == code
    assert error.value.status == status
    assert error.value.details == ({"reason": safe} if safe else {})
    assert "secret" not in str(error.value)
    domain.list_safe.assert_called_once_with("p", 7)


@pytest.mark.parametrize("control", [KeyboardInterrupt, SystemExit])
def test_control_flow_is_not_business_failure(control: type[BaseException]) -> None:
    domain = Mock()
    domain.list_safe.side_effect = control()
    service = ProjectService(SimpleNamespace(), public_connector_service=domain)
    with pytest.raises(control):
        service.list_public_connectors("p", 7)


def test_all_facade_writers_delegate_without_outer_qualification() -> None:
    domain = Mock()
    collection = PublicConnectorCollection("p", 2, [])
    service = ProjectService(SimpleNamespace(), public_connector_service=domain)
    credential = {"endpoint": "https://synthetic.invalid/mcp", "bearer_token": "synthetic"}
    domain.create.return_value = collection
    assert (
        service.create_public_connector(
            "p",
            7,
            expected_project_revision=1,
            display_name="Safe",
            description="",
            credential=credential,
        )
        is collection
    )
    domain.create.assert_called_once_with(
        "p",
        7,
        expected_project_revision=1,
        display_name="Safe",
        description="",
        credential=credential,
    )
    domain.rename.return_value = collection
    assert (
        service.rename_public_connector(
            "p",
            7,
            "c",
            expected_project_revision=2,
            expected_grant_revision=1,
            display_name="Safe",
            description="",
        )
        is collection
    )
    domain.rename.assert_called_once_with(
        "p",
        7,
        "c",
        expected_project_revision=2,
        expected_grant_revision=1,
        display_name="Safe",
        description="",
    )
    domain.replace_credentials.return_value = collection
    assert (
        service.replace_public_connector_credentials(
            "p",
            7,
            "c",
            expected_project_revision=2,
            expected_grant_revision=1,
            credential=credential,
        )
        is collection
    )
    domain.replace_credentials.assert_called_once_with(
        "p", 7, "c", expected_project_revision=2, expected_grant_revision=1, credential=credential
    )
    domain.revoke.return_value = collection
    assert (
        service.revoke_public_connector(
            "p", 7, "c", expected_project_revision=2, expected_grant_revision=1
        )
        is collection
    )
    domain.revoke.assert_called_once_with(
        "p", 7, "c", expected_project_revision=2, expected_grant_revision=1
    )
