"""Q3 service defense tests; approved component-only metadata contract."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from octop.infra.projects.activity import activity_item_view

_VIEW_ID = "01M3M96KJMH2HTQ4KFPHRTBH25"
_VIEW_EVENT = "project.todo_view_updated"
_PRIVATE = "NEVER-EXPOSE-VIEW-NAME-OR-FILTER"
_ACTIONS = ("created", "updated", "ordered", "archived", "restored", "default_changed")
_FIELDS = ("name", "type", "definition", "order", "archived", "default_view_id")


def _view_row(**overrides):
    data = {
        "event_id": 17,
        "event_type": _VIEW_EVENT,
        "actor_user_id": 2,
        "actor_name": "owner",
        "object_kind": _PRIVATE,
        "object_id": _PRIVATE,
        "message_body": _PRIVATE,
        "created_at": 1234567,
        "fields": _FIELDS,
        "catalog_revision": 33,
        "catalog_kind": "tag",
        "option_id": _VIEW_ID,
        "action": "updated",
        "view_id": _VIEW_ID,
        "version": 7,
        "collection_revision": 15,
    }
    data.update(overrides)
    return SimpleNamespace(**data)


@pytest.mark.parametrize("action", _ACTIONS)
def test_view_activity_service_only_projects_allowed_metadata(action):
    item = activity_item_view(_view_row(action=action))
    assert item is not None
    assert item.object_kind == "project"
    assert item.object_id == _VIEW_ID
    assert item.message_body is None
    assert item.view_id == _VIEW_ID
    assert item.version == 7 and item.collection_revision == 15
    assert item.action == action
    assert item.fields == tuple(sorted(_FIELDS))
    assert item.catalog_revision is None and item.catalog_kind is None and item.option_id is None
    assert _PRIVATE not in repr(item)


@pytest.mark.parametrize("bad", [None, True, False, 0, -1, 1.5, "7", 2**100, [], {}])
def test_view_activity_service_rejects_non_storage_versions(bad):
    item = activity_item_view(_view_row(version=bad, collection_revision=bad))
    assert item is not None
    assert item.version is None and item.collection_revision is None
    assert _PRIVATE not in repr(item)


def test_view_activity_service_rechecks_fields_action_and_identifier():
    item = activity_item_view(
        _view_row(
            action=_PRIVATE,
            view_id=_PRIVATE,
            fields=("name", "name", "filters", ["definition"], {"name": _PRIVATE}, True, _PRIVATE),
        )
    )
    assert item is not None
    assert item.fields == ("name",)
    assert item.action is None and item.view_id is None and item.object_id is None
    assert item.object_kind == "project" and item.message_body is None
    assert _PRIVATE not in repr(item)


def test_view_activity_service_keeps_unknown_event_out():
    assert activity_item_view(_view_row(event_type="project.unknown_private")) is None
