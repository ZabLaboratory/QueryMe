"""Schema descriptor — wire shape and lookup helper."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from queryme.schema import ColumnDef, RelationDef, SchemaDescriptor, TableDef


def _truth_schema() -> SchemaDescriptor:
    return SchemaDescriptor(
        service="truth",
        tables=[
            TableDef(
                name="matches",
                writable=True,
                columns=[
                    ColumnDef(name="id", type="uuid", primary=True),
                    ColumnDef(name="blue_team", type="string", nullable=True),
                    ColumnDef(name="red_team", type="string", nullable=True),
                ],
                relations=[
                    RelationDef(to="match_players", via="match_id", kind="has_many"),
                ],
            ),
            TableDef(
                name="match_players",
                writable=True,
                columns=[
                    ColumnDef(name="id", type="uuid", primary=True),
                    ColumnDef(name="match_id", type="uuid"),
                    ColumnDef(name="player_id", type="uuid"),
                    ColumnDef(name="champion", type="string"),
                    ColumnDef(name="role", type="string"),
                    ColumnDef(name="side", type="string"),
                ],
                relations=[
                    RelationDef(to="matches", via="match_id", kind="belongs_to"),
                    RelationDef(to="players", via="player_id", kind="belongs_to"),
                ],
            ),
            TableDef(
                name="players",
                writable=True,
                columns=[
                    ColumnDef(name="id", type="uuid", primary=True),
                    ColumnDef(name="summoner_name", type="string"),
                ],
            ),
        ],
    )


def test_schema_round_trip() -> None:
    schema = _truth_schema()
    payload = schema.model_dump()
    again = SchemaDescriptor.model_validate(payload)
    assert again == schema


def test_table_lookup_helper() -> None:
    schema = _truth_schema()
    assert schema.table("match_players") is not None
    assert schema.table("nonexistent") is None


def test_column_type_is_closed_set() -> None:
    """An exotic type gets rejected — ColumnType is a Literal."""
    with pytest.raises(ValidationError):
        ColumnDef(name="weird", type="geography")  # type: ignore[arg-type]


def test_relation_kind_is_closed_set() -> None:
    with pytest.raises(ValidationError):
        RelationDef(to="x", via="y", kind="cousins")  # type: ignore[arg-type]
