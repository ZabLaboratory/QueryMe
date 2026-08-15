"""Validator — whitelist checks against a SchemaDescriptor."""

from __future__ import annotations

from queryme.descriptor import JoinClause, QueryDescriptor, WhereClause
from queryme.schema import ColumnDef, RelationDef, SchemaDescriptor, TableDef
from queryme.validator import validate_against_schema


def _truth_schema() -> SchemaDescriptor:
    return SchemaDescriptor(
        service="truth",
        tables=[
            TableDef(
                name="matches",
                columns=[
                    ColumnDef(name="id", type="uuid", primary=True),
                    ColumnDef(name="blue_team", type="string", nullable=True),
                ],
            ),
            TableDef(
                name="match_players",
                columns=[
                    ColumnDef(name="id", type="uuid", primary=True),
                    ColumnDef(name="match_id", type="uuid"),
                    ColumnDef(name="player_id", type="uuid"),
                    ColumnDef(name="champion", type="string"),
                    ColumnDef(name="role", type="string"),
                    ColumnDef(name="side", type="string"),
                ],
                relations=[
                    RelationDef(to="players", via="player_id", kind="belongs_to"),
                ],
            ),
            TableDef(
                name="players",
                columns=[
                    ColumnDef(name="id", type="uuid", primary=True),
                    ColumnDef(name="summoner_name", type="string"),
                ],
            ),
        ],
    )


# ── Happy paths ────────────────────────────────────────────────────────────


def test_valid_query_returns_no_issues() -> None:
    schema = _truth_schema()
    descriptor = QueryDescriptor(
        table="match_players",
        where=[WhereClause(column="match_id", op="=", value="abc")],
        joins=[
            JoinClause(
                table="players",
                on=("player_id", "id"),
                select=["summoner_name"],
            )
        ],
        select=["champion", "role", "side"],
        limit=50,
    )
    assert validate_against_schema(descriptor, schema) == []


def test_qualified_column_resolves() -> None:
    schema = _truth_schema()
    descriptor = QueryDescriptor(
        table="match_players",
        joins=[JoinClause(table="players", on=("player_id", "id"))],
        where=[WhereClause(column="players.summoner_name", op="LIKE", value="A%")],
        select=["champion"],
        limit=50,
    )
    assert validate_against_schema(descriptor, schema) == []


# ── Failure modes ──────────────────────────────────────────────────────────


def test_unknown_table_short_circuits() -> None:
    schema = _truth_schema()
    descriptor = QueryDescriptor(table="nonexistent", select=["id"], limit=50)
    issues = validate_against_schema(descriptor, schema)
    assert len(issues) == 1
    assert issues[0].code == "unknown_table"
    assert issues[0].path == "table"


def test_unknown_select_column_reported() -> None:
    schema = _truth_schema()
    descriptor = QueryDescriptor(
        table="match_players",
        select=["champion", "ghost_field"],
        limit=50,
    )
    issues = validate_against_schema(descriptor, schema)
    codes = {(i.code, i.path) for i in issues}
    assert ("unknown_select_column", "select[1]") in codes


def test_empty_select_reported() -> None:
    schema = _truth_schema()
    descriptor = QueryDescriptor(table="match_players", limit=50)
    issues = validate_against_schema(descriptor, schema)
    assert any(i.code == "empty_select" for i in issues)


def test_join_with_select_satisfies_empty_select_rule() -> None:
    """A query that doesn't project from FROM but does project from a
    join is still valid — the user wants summoner_name, not the
    match_players bag."""
    schema = _truth_schema()
    descriptor = QueryDescriptor(
        table="match_players",
        joins=[
            JoinClause(table="players", on=("player_id", "id"), select=["summoner_name"]),
        ],
        limit=50,
    )
    issues = validate_against_schema(descriptor, schema)
    assert all(i.code != "empty_select" for i in issues)


def test_unknown_join_table_reported() -> None:
    schema = _truth_schema()
    descriptor = QueryDescriptor(
        table="match_players",
        joins=[JoinClause(table="ghosts", on=("player_id", "id"))],
        select=["champion"],
        limit=50,
    )
    issues = validate_against_schema(descriptor, schema)
    assert any(i.code == "unknown_join_table" and i.path == "joins[0].table" for i in issues)


def test_unknown_join_column_reported() -> None:
    schema = _truth_schema()
    descriptor = QueryDescriptor(
        table="match_players",
        joins=[JoinClause(table="players", on=("nope", "wrong"))],
        select=["champion"],
        limit=50,
    )
    issues = validate_against_schema(descriptor, schema)
    paths = {i.path for i in issues}
    assert "joins[0].on[0]" in paths
    assert "joins[0].on[1]" in paths


def test_unknown_where_column_reported() -> None:
    schema = _truth_schema()
    descriptor = QueryDescriptor(
        table="match_players",
        where=[WhereClause(column="missing", op="=", value=1)],
        select=["champion"],
        limit=50,
    )
    issues = validate_against_schema(descriptor, schema)
    assert any(i.code == "unknown_column" and i.path == "where[0].column" for i in issues)


def test_unknown_order_column_reported() -> None:
    schema = _truth_schema()
    descriptor = QueryDescriptor(
        table="match_players",
        select=["champion"],
        order=[{"column": "ghost"}],  # type: ignore[list-item]
        limit=50,
    )
    issues = validate_against_schema(descriptor, schema)
    assert any(i.code == "unknown_order_column" for i in issues)
