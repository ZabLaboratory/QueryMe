"""Descriptor — wire shape, validators, round-trip."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from queryme.descriptor import (
    DEFAULT_MAX_LIMIT,
    JoinClause,
    OrderClause,
    QueryDescriptor,
    WhereClause,
)


def test_query_descriptor_minimal_round_trip() -> None:
    payload = {
        "table": "match_players",
        "select": ["champion", "role", "side"],
    }
    descriptor = QueryDescriptor.model_validate(payload)
    # Default lists are empty, optional ints stay None.
    assert descriptor.where == []
    assert descriptor.joins == []
    assert descriptor.order == []
    assert descriptor.limit is None
    assert descriptor.offset is None
    # Round-trip keeps the explicit fields intact.
    redumped = descriptor.model_dump()
    assert redumped["table"] == "match_players"
    assert redumped["select"] == ["champion", "role", "side"]


def test_query_descriptor_full_round_trip() -> None:
    payload = {
        "table": "match_players",
        "where": [
            {"column": "match_id", "op": "=", "value": "abc"},
        ],
        "joins": [
            {
                "table": "players",
                "on": ("player_id", "id"),
                "select": ["summoner_name"],
            }
        ],
        "select": ["champion", "role", "side"],
        "order": [{"column": "side"}, {"column": "role", "direction": "desc"}],
        "limit": 100,
        "offset": 0,
    }
    descriptor = QueryDescriptor.model_validate(payload)
    assert descriptor.joins[0].on == ("player_id", "id")
    assert descriptor.order[1].direction == "desc"
    assert descriptor.limit == 100


def test_where_in_requires_list() -> None:
    with pytest.raises(ValidationError):
        WhereClause(column="status", op="IN", value="not-a-list")


def test_where_like_requires_string() -> None:
    with pytest.raises(ValidationError):
        WhereClause(column="name", op="LIKE", value=123)


def test_where_is_null_normalises_value_to_none() -> None:
    clause = WhereClause(column="patch", op="IS NULL", value="ignored-anyway")
    assert clause.value is None


def test_order_default_direction_is_asc() -> None:
    clause = OrderClause(column="side")
    assert clause.direction == "asc"


def test_query_descriptor_rejects_duplicate_join() -> None:
    with pytest.raises(ValidationError):
        QueryDescriptor(
            table="match_players",
            joins=[
                JoinClause(table="players", on=("player_id", "id")),
                JoinClause(table="players", on=("player_id", "id")),
            ],
            select=["champion"],
        )


def test_query_descriptor_rejects_unknown_field() -> None:
    """``extra=forbid`` keeps the wire shape stable — typos surface
    immediately rather than being silently discarded."""
    with pytest.raises(ValidationError):
        QueryDescriptor.model_validate(
            {"table": "matches", "select": ["id"], "groupby": ["side"]}
        )


def test_negative_limit_rejected() -> None:
    with pytest.raises(ValidationError):
        QueryDescriptor(table="matches", select=["id"], limit=-1)


def test_limit_above_default_max_rejected() -> None:
    with pytest.raises(ValidationError):
        QueryDescriptor(table="matches", select=["id"], limit=DEFAULT_MAX_LIMIT + 1)


def test_limit_at_default_max_accepted() -> None:
    descriptor = QueryDescriptor(table="matches", select=["id"], limit=DEFAULT_MAX_LIMIT)
    assert descriptor.limit == DEFAULT_MAX_LIMIT
