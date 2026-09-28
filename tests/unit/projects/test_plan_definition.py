"""Black-box C2 Q1 shared definitions and compact cursor contracts."""

from __future__ import annotations

import base64
import copy
import json
import traceback

import pytest

from octop.infra.projects.plan_definition import (
    default_definition,
    encode_cursor,
    parse_cursor,
    parse_definition,
)

PUBLIC_ID = "01ARZ3NDEKTSV4RRFFQ69G5FAV"
SECOND_ID = "01ARZ3NDEKTSV4RRFFQ69G5FAW"
FINGERPRINT = "a" * 64
SQLITE_MAX = 2**63 - 1
PG_MAX = 2**31 - 1


def _definition(**changes):
    value = {
        "schema_version": 1,
        "fields": ["title"],
        "group_by": None,
        "filters": [],
        "sort": [],
    }
    value.update(changes)
    return value


def _dump(value, view_type="table"):
    return parse_definition(view_type, value).model_dump(mode="json")


@pytest.mark.parametrize("view_type", ["list", "table", "board", "gantt", "calendar"])
def test_default_definition_is_complete_strict_and_independent(view_type):
    value = default_definition(view_type)
    original = copy.deepcopy(value)
    assert _dump(value, view_type) == original
    assert original["schema_version"] == 1
    assert original["fields"][0] == "title"
    assert original["filters"] == []
    assert original["sort"] == [{"field": "updated_at", "direction": "desc"}]
    if view_type in ("list", "table"):
        assert original["fields"] == [
            "title",
            "status",
            "assignee",
            "priority",
            "tags",
            "start_date",
            "due_date",
        ]
        assert original["group_by"] is None
    elif view_type == "board":
        assert original["fields"] == ["title", "status", "assignee", "priority", "tags"]
        assert original["group_by"] == "status"
    else:
        assert original["fields"] == ["title", "status", "assignee", "priority"]
        assert original["group_by"] is None
    if view_type == "calendar":
        assert original["calendar"] == {"date_basis": "due_date", "mode": "month"}
    if view_type == "gantt":
        assert original["gantt"] == {"zoom": "week"}
    value["fields"].append("source")
    value["sort"][0]["direction"] = "asc"
    assert default_definition(view_type) == original


@pytest.mark.parametrize(
    "change",
    [
        {"schema_version": True},
        {"schema_version": "1"},
        {"schema_version": 1.0},
        {"schema_version": 2},
        {"fields": []},
        {"fields": ["status", "title"]},
        {"fields": ["title", "title"]},
        {"fields": ["title", "attachments"]},
        {"fields": "title"},
        {"fields": ["title", 1]},
        {"group_by": "tags"},
        {"filters": None},
        {"sort": None},
        {"calendar": None},
        {"gantt": None},
        {"arbitrary_sql": "SELECT 1"},
        {"sort": [{"field": "source", "direction": "asc"}]},
        {"sort": [{"field": "title", "direction": "ASC"}]},
        {"sort": [{"field": "title", "direction": "asc", "sql": "x"}]},
        {"sort": [{"field": "title", "direction": "asc"}] * 2},
        {
            "sort": [
                {"field": field, "direction": "asc"}
                for field in ["title", "status", "assignee", "priority"]
            ]
        },
    ],
)
def test_definition_rejects_wrong_types_unknown_keys_and_invalid_combinations(change):
    with pytest.raises(ValueError):
        _dump(_definition(**change))


@pytest.mark.parametrize("missing", ["schema_version", "fields", "group_by", "filters", "sort"])
def test_definition_requires_every_explicit_base_field(missing):
    value = _definition()
    del value[missing]
    with pytest.raises(ValueError):
        _dump(value)


def test_minimal_definition_keeps_empty_sort_and_ordered_visible_fields():
    value = _definition(fields=["title", "source", "created_at", "status"])
    assert _dump(value) == value


@pytest.mark.parametrize("view_type", ["table", "list", "board"])
@pytest.mark.parametrize("group_by", ["status", "assignee", "priority", "tag", "source"])
def test_supported_grouping_is_preserved(view_type, group_by):
    value = _definition(group_by=group_by)
    assert _dump(value, view_type) == value


def test_board_and_date_views_enforce_type_specific_configurations():
    with pytest.raises(ValueError):
        _dump(_definition(), "board")
    for kind, config in [
        ("calendar", {"date_basis": "start_date", "mode": "week"}),
        ("gantt", {"zoom": "day"}),
    ]:
        value = _definition(**{kind: config})
        assert _dump(value, kind) == value
        for invalid in [
            dict(value, group_by="status"),
            _definition(),
            dict(value, **{kind: dict(config, arbitrary=1)}),
        ]:
            with pytest.raises(ValueError):
                _dump(invalid, kind)
        with pytest.raises(ValueError):
            _dump(value, "table")
    with pytest.raises(ValueError):
        _dump(_definition(), "arbitrary")


@pytest.mark.parametrize(
    "value,compatible_types",
    [
        (_definition(), {"list", "table"}),
        (_definition(group_by="status"), {"list", "table", "board"}),
        (
            _definition(calendar={"date_basis": "due_date", "mode": "month"}),
            {"calendar"},
        ),
        (_definition(gantt={"zoom": "week"}), {"gantt"}),
        (
            _definition(
                calendar={"date_basis": "due_date", "mode": "month"},
                gantt={"zoom": "week"},
            ),
            set(),
        ),
    ],
)
def test_definition_preserves_only_its_actual_compatible_view_types(value, compatible_types):
    accepted_types = set()
    for view_type in ("list", "table", "board", "gantt", "calendar"):
        try:
            parsed = _dump(copy.deepcopy(value), view_type)
        except ValueError:
            continue
        assert parsed == value
        accepted_types.add(view_type)
    assert accepted_types == compatible_types


VALID_FILTERS = [
    {"field": "title", "op": "contains", "value": " %_\\ literal "},
    {"field": "title", "op": "not_contains", "value": "\ufdfa" * 200},
    {"field": "status", "op": "in", "values": ["todo", "done"]},
    {"field": "assignee", "op": "not_in", "values": [23, None]},
    {"field": "priority", "op": "in", "values": [PUBLIC_ID, None]},
    *[
        {"field": field, "op": op, "values": [None]}
        for field in ("assignee", "priority")
        for op in ("in", "not_in")
    ],
    *[
        {"field": "tags", "op": op, "values": [PUBLIC_ID, SECOND_ID]}
        for op in ["any", "all", "none_of"]
    ],
    *[{"field": "tags", "op": op} for op in ["is_empty", "not_empty"]],
    *[
        {"field": field, "op": op, "value": "2024-02-29"}
        for field in ["start_date", "due_date"]
        for op in ["on", "before", "after"]
    ],
    *[
        {"field": field, "op": "between", "values": ["1900-01-01", "9999-12-31"]}
        for field in ["start_date", "due_date"]
    ],
    *[
        {"field": field, "op": op}
        for field in ["start_date", "due_date"]
        for op in ["is_empty", "not_empty"]
    ],
    *[{"field": "due_date", "op": "overdue", "value": value} for value in [True, False]],
    *[{"field": "source", "op": op, "values": ["manual"]} for op in ["in", "not_in"]],
]


@pytest.mark.parametrize("condition", VALID_FILTERS)
def test_strict_filter_variants_preserve_values_including_null_and_literals(condition):
    value = _definition(filters=[condition])
    assert _dump(value) == value


@pytest.mark.parametrize("condition", VALID_FILTERS)
def test_filter_variants_reject_extra_or_inappropriate_keys(condition):
    with pytest.raises(ValueError):
        _dump(_definition(filters=[dict(condition, arbitrary="x")]))
    extra = "values" if "value" in condition else "value"
    with pytest.raises(ValueError):
        _dump(_definition(filters=[dict(condition, **{extra: None})]))


@pytest.mark.parametrize(
    "condition",
    [
        {"field": "title", "op": "contains", "value": ""},
        {"field": "title", "op": "contains", "value": "x" * 201},
        {"field": "title", "op": "contains", "value": 1},
        {"field": "status", "op": "in", "values": []},
        {"field": "status", "op": "in", "values": ["todo", "todo"]},
        {"field": "status", "op": "in", "values": ["closed"]},
        {"field": "assignee", "op": "in", "values": [True]},
        {"field": "assignee", "op": "in", "values": [0]},
        {"field": "assignee", "op": "in", "values": ["23"]},
        {"field": "assignee", "op": "in", "values": [None, None]},
        {"field": "assignee", "op": "in", "values": list(range(1, 52))},
        {"field": "priority", "op": "in", "values": [1]},
        {"field": "tags", "op": "any", "values": [None]},
        {"field": "tags", "op": "all", "values": [PUBLIC_ID, PUBLIC_ID]},
        {"field": "tags", "op": "none_of", "values": []},
        {"field": "due_date", "op": "overdue", "value": 1},
        {"field": "due_date", "op": "overdue", "value": "true"},
        {"field": "start_date", "op": "overdue", "value": True},
        {"field": "due_date", "op": "on", "value": "2023-02-29"},
        {"field": "due_date", "op": "on", "value": "1899-12-31"},
        {"field": "due_date", "op": "on", "value": "2024-2-29"},
        {"field": "due_date", "op": "on", "value": "20240229"},
        {"field": "due_date", "op": "on", "value": "２０２４-02-29"},
        {
            "field": "start_date",
            "op": "between",
            "values": ["2024-03-01", "2024-02-29"],
        },
        {"field": "due_date", "op": "between", "values": ["2024-02-29"]},
        {
            "field": "due_date",
            "op": "between",
            "values": ["2024-02-29", "2024-03-01", "2024-03-02"],
        },
        {"field": "source", "op": "in", "values": ["imported"]},
        {"field": "source", "op": "in", "values": ["manual", "manual"]},
        {"field": "sql", "op": "eval", "value": "1=1"},
    ],
)
def test_filter_types_bounds_and_operators_are_strict(condition):
    with pytest.raises(ValueError):
        _dump(_definition(filters=[condition]))


def test_twelve_filters_allowed_and_thirteenth_rejected_without_normalizing_literal():
    condition = {"field": "title", "op": "contains", "value": "\ufdfa" * 200}
    accepted = _definition(filters=[copy.deepcopy(condition) for _ in range(12)])
    assert _dump(accepted) == accepted
    with pytest.raises(ValueError):
        _dump(_definition(filters=[condition] * 13))


@pytest.mark.parametrize("field,maximum", [("assignee", 50), ("priority", 32), ("tags", 20)])
def test_filter_collection_limits_apply_to_distinct_structurally_valid_ids(field, maximum):
    values = (
        list(range(1, maximum + 1))
        if field == "assignee"
        else [PUBLIC_ID[:24] + f"{index:02X}" for index in range(maximum)]
    )
    condition = {
        "field": field,
        "op": "any" if field == "tags" else "in",
        "values": values,
    }
    assert _dump(_definition(filters=[condition]))["filters"] == [condition]
    extra = maximum + 1 if field == "assignee" else PUBLIC_ID[:24] + f"{maximum:02X}"
    with pytest.raises(ValueError):
        _dump(_definition(filters=[dict(condition, values=[*values, extra])]))


@pytest.mark.parametrize(
    "field",
    [
        "title",
        "status",
        "assignee",
        "priority",
        "start_date",
        "due_date",
        "created_at",
        "updated_at",
    ],
)
@pytest.mark.parametrize("direction", ["asc", "desc"])
def test_each_allowed_sort_field_and_direction_is_preserved(field, direction):
    value = _definition(sort=[{"field": field, "direction": direction}])
    assert _dump(value) == value


@pytest.mark.parametrize(
    "sort",
    [
        [
            {"field": "priority", "direction": "asc"},
            {"field": "due_date", "direction": "desc"},
        ],
        [
            {"field": "assignee", "direction": "desc"},
            {"field": "title", "direction": "asc"},
            {"field": "updated_at", "direction": "desc"},
        ],
    ],
)
def test_two_and_three_distinct_sort_fields_keep_order_and_direction(sort):
    value = _definition(sort=sort)
    assert _dump(value) == value


def _cursor(**changes):
    value = {
        "v": 1,
        "query_fingerprint": FINGERPRINT,
        "last_todo_id": PUBLIC_ID,
        "last_version": 1,
    }
    value.update(changes)
    return value


def _token(data):
    raw = data if isinstance(data, bytes) else json.dumps(data, separators=(",", ":")).encode()
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def test_cursor_is_compact_canonical_and_roundtrips_only_four_fields():
    value = _cursor()
    token = encode_cursor(value, max_version=SQLITE_MAX)
    assert len(token) < 2048
    assert "=" not in token
    assert parse_cursor(token, max_version=SQLITE_MAX).model_dump(mode="json") == value
    assert json.loads(base64.urlsafe_b64decode(token + "=" * (-len(token) % 4))) == value
    reversed_token = _token(dict(reversed(list(value.items()))))
    assert parse_cursor(reversed_token, max_version=SQLITE_MAX).model_dump(mode="json") == value


@pytest.mark.parametrize(
    "maximum,version,allowed",
    [
        (PG_MAX, PG_MAX, True),
        (PG_MAX, PG_MAX + 1, False),
        (SQLITE_MAX, SQLITE_MAX, True),
        (SQLITE_MAX, SQLITE_MAX + 1, False),
    ],
)
def test_cursor_version_uses_explicit_actual_database_boundary(maximum, version, allowed):
    value = _cursor(last_version=version)
    if allowed:
        token = encode_cursor(value, max_version=maximum)
        assert parse_cursor(token, max_version=maximum).last_version == version
    else:
        with pytest.raises(ValueError):
            encode_cursor(value, max_version=maximum)
        with pytest.raises(ValueError):
            parse_cursor(_token(value), max_version=maximum)


@pytest.mark.parametrize(
    "change",
    [
        {"v": True},
        {"v": 1.0},
        {"v": "1"},
        {"v": 2},
        {"last_version": True},
        {"last_version": 0},
        {"last_version": -1},
        {"last_version": "1"},
        {"last_version": 1.0},
        {"last_todo_id": PUBLIC_ID.lower()},
        {"last_todo_id": "8" + PUBLIC_ID[1:]},
        {"last_todo_id": "I" + PUBLIC_ID[1:]},
        {"query_fingerprint": "x" * 64},
        {"query_fingerprint": "a" * 63},
        {"sort_title": "\ufdfa" * 200},
        {"member_name": "x" * 10000},
    ],
)
def test_cursor_rejects_wrong_types_public_id_fingerprint_and_sort_text(change):
    value = _cursor(**change)
    with pytest.raises(ValueError):
        encode_cursor(value, max_version=SQLITE_MAX)
    with pytest.raises(ValueError):
        parse_cursor(_token(value), max_version=SQLITE_MAX)


@pytest.mark.parametrize("maximum", [True, False, 0, -1, "2147483647", 1.0, None])
def test_cursor_rejects_noninteger_or_nonpositive_boundary_parameter(maximum):
    with pytest.raises(ValueError):
        encode_cursor(_cursor(), max_version=maximum)
    with pytest.raises(ValueError):
        parse_cursor(_token(_cursor()), max_version=maximum)


@pytest.mark.parametrize(
    "token",
    [
        "",
        "x" * 2049,
        "a+b",
        "a/b",
        "a=b",
        "A",
        "\n",
        _token(b"\xff"),
        _token([]),
        _token(None),
        _token(b'{"v":1,"v":1}'),
        _token(b'{"v":1,"\\u0076":1}'),
        _token(b'{"v":NaN}'),
        _token(b'{"v":Infinity}'),
    ],
)
def test_cursor_rejects_malformed_noncanonical_or_ambiguous_wire_input(token):
    with pytest.raises(ValueError):
        parse_cursor(token, max_version=SQLITE_MAX)


@pytest.mark.parametrize(
    "duplicate", ['"v":1', '"\\u0076":1', '"query_fingerprint":"' + FINGERPRINT + '"']
)
def test_cursor_rejects_duplicate_keys_even_when_all_fields_would_otherwise_be_valid(
    duplicate,
):
    valid = json.dumps(_cursor(), separators=(",", ":"))
    token = _token(("{" + duplicate + "," + valid[1:]).encode())
    with pytest.raises(ValueError):
        parse_cursor(token, max_version=SQLITE_MAX)


def test_cursor_rejects_padding_and_noncanonical_pad_bits():
    token = encode_cursor(_cursor(), max_version=SQLITE_MAX)
    with pytest.raises(ValueError):
        parse_cursor(token + "=", max_version=SQLITE_MAX)
    raw = json.dumps(_cursor(), separators=(",", ":")).encode()
    while len(raw) % 3 == 0:
        raw += b" "
    canonical = _token(raw)
    alphabet = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_"
    altered = canonical[:-1] + alphabet[alphabet.index(canonical[-1]) | 1]
    assert altered != canonical
    assert base64.urlsafe_b64decode(altered + "=" * (-len(altered) % 4)) == raw
    with pytest.raises(ValueError):
        parse_cursor(altered, max_version=SQLITE_MAX)


def test_mutated_returned_definition_is_revalidated_before_reuse():
    parsed = parse_definition("table", _definition())
    parsed.fields.append("title")
    with pytest.raises(ValueError):
        parse_definition("table", parsed)


@pytest.mark.parametrize(
    "field,value",
    [("v", True), ("last_version", "2"), ("query_fingerprint", "invalid")],
)
def test_mutated_returned_cursor_is_revalidated_before_encoding(field, value):
    parsed = parse_cursor(_token(_cursor()), max_version=SQLITE_MAX)
    setattr(parsed, field, value)
    with pytest.raises(ValueError):
        encode_cursor(parsed, max_version=SQLITE_MAX)


def test_validation_error_strings_do_not_include_private_condition_text():
    private_text = "private-project-filter-marker-9726"
    invalid = _definition(filters=[{"field": "title", "op": "sql", "value": private_text}])
    with pytest.raises(ValueError) as definition_error:
        _dump(invalid)
    assert private_text not in str(definition_error.value)
    with pytest.raises(ValueError) as cursor_error:
        encode_cursor(_cursor(sort_title=private_text), max_version=SQLITE_MAX)
    assert private_text not in str(cursor_error.value)


@pytest.mark.parametrize(
    "site",
    ["definition", "filter", "sort", "calendar", "gantt", "encode", "parse"],
)
def test_public_validation_errors_hide_arbitrary_extra_keys_and_context(site):
    private_key = "private-condition-key-marker-92852"
    private_value = "private-condition-value-marker-92853"
    view_type = site if site in ("calendar", "gantt") else "table"
    definition = default_definition(view_type)
    if site == "filter":
        definition["filters"] = [
            {"field": "title", "op": "contains", "value": "literal", private_key: private_value}
        ]
    elif site == "sort":
        definition["sort"][0][private_key] = private_value
    elif site in ("calendar", "gantt"):
        definition[site][private_key] = private_value
    else:
        definition[private_key] = private_value
    invalid_cursor = _cursor(**{private_key: private_value})
    with pytest.raises(ValueError) as error:
        if site == "encode":
            encode_cursor(invalid_cursor, max_version=SQLITE_MAX)
        elif site == "parse":
            parse_cursor(_token(invalid_cursor), max_version=SQLITE_MAX)
        else:
            parse_definition(view_type, definition)
    assert private_key not in str(error.value)
    assert private_value not in str(error.value)
    assert error.value.__cause__ is None
    assert error.value.__suppress_context__ is True
    rendered = "".join(traceback.format_exception(error.type, error.value, error.tb))
    assert private_key not in rendered
    assert private_value not in rendered


@pytest.mark.parametrize("malformed", ["json", "utf8"])
def test_malformed_cursor_errors_do_not_chain_private_wire_input(malformed):
    private_key = "private-wire-key-marker-92854"
    raw = ('{"' + private_key + '":').encode()
    if malformed == "utf8":
        raw += b"\xff"
    with pytest.raises(ValueError) as error:
        parse_cursor(_token(raw), max_version=SQLITE_MAX)
    assert error.value.__cause__ is None
    assert error.value.__suppress_context__ is True
    rendered = "".join(traceback.format_exception(error.type, error.value, error.tb))
    assert private_key not in rendered
