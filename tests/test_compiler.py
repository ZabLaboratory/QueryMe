"""Compiler — descriptor → SQLAlchemy Select.

We compile-and-stringify rather than running statements against a real
DB : QueryMe is a pure library, so checking the produced SQL against
SQLAlchemy's own dialect-rendering is enough to prove the compiler
wired the clauses correctly. Services run the statements for real
against their own engine and have their own integration tests for
that.
"""

from __future__ import annotations

import pytest
from sqlalchemy.dialects import postgresql

from queryme.compiler import CompilationError, compile_query
from queryme.descriptor import JoinClause, OrderClause, QueryDescriptor, WhereClause
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
                    ColumnDef(name="patch", type="string", nullable=True),
                    ColumnDef(name="duration_seconds", type="integer", nullable=True),
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
                    ColumnDef(name="kills", type="integer"),
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


def _sql(stmt: object) -> str:
    """Render a Select to its postgresql dialect SQL with bound params
    inlined (literal_binds) — easier to assert against."""
    return str(
        stmt.compile(  # type: ignore[attr-defined]
            dialect=postgresql.dialect(),
            compile_kwargs={"literal_binds": True},
        )
    ).replace("\n", " ")


# ── Happy paths ────────────────────────────────────────────────────────────


def test_compile_simple_select() -> None:
    schema = _truth_schema()
    descriptor = QueryDescriptor(
        table="matches",
        select=["id", "blue_team", "red_team"],
    )
    sql = _sql(compile_query(descriptor, schema))
    assert "SELECT matches.id, matches.blue_team, matches.red_team" in sql
    assert "FROM matches" in sql
    assert "WHERE" not in sql
    assert "JOIN" not in sql
    assert "ORDER BY" not in sql
    assert "LIMIT" not in sql


def test_compile_where_equality_with_value() -> None:
    schema = _truth_schema()
    descriptor = QueryDescriptor(
        table="matches",
        select=["id"],
        where=[WhereClause(column="patch", op="=", value="14.7")],
    )
    sql = _sql(compile_query(descriptor, schema))
    assert "WHERE matches.patch = '14.7'" in sql


@pytest.mark.parametrize(
    ("op", "value", "needle"),
    [
        ("!=", "14.7", "matches.patch != '14.7'"),
        (">", 1500, "matches.duration_seconds > 1500"),
        ("<", 3600, "matches.duration_seconds < 3600"),
        (">=", 1500, "matches.duration_seconds >= 1500"),
        ("<=", 3600, "matches.duration_seconds <= 3600"),
    ],
)
def test_compile_comparison_operators(op: str, value: object, needle: str) -> None:
    schema = _truth_schema()
    column = "patch" if op in ("!=",) else "duration_seconds"
    descriptor = QueryDescriptor(
        table="matches",
        select=["id"],
        where=[WhereClause(column=column, op=op, value=value)],  # type: ignore[arg-type]
    )
    sql = _sql(compile_query(descriptor, schema))
    assert needle in sql


def test_compile_in_operator() -> None:
    schema = _truth_schema()
    descriptor = QueryDescriptor(
        table="match_players",
        select=["champion"],
        where=[WhereClause(column="role", op="IN", value=["mid", "top"])],
    )
    sql = _sql(compile_query(descriptor, schema))
    assert "match_players.role IN ('mid', 'top')" in sql


def test_compile_like_operator() -> None:
    schema = _truth_schema()
    descriptor = QueryDescriptor(
        table="players",
        select=["summoner_name"],
        where=[WhereClause(column="summoner_name", op="LIKE", value="A%")],
    )
    sql = _sql(compile_query(descriptor, schema))
    # PG dialect doubles the % in literal_binds output (param-marker
    # escape) — the actual emitted SQL has a single % at execute time.
    assert "players.summoner_name LIKE 'A%%'" in sql


def test_compile_is_null_operator() -> None:
    schema = _truth_schema()
    descriptor = QueryDescriptor(
        table="matches",
        select=["id"],
        where=[WhereClause(column="patch", op="IS NULL")],
    )
    sql = _sql(compile_query(descriptor, schema))
    assert "matches.patch IS NULL" in sql


def test_compile_multiple_where_are_and_joined() -> None:
    schema = _truth_schema()
    descriptor = QueryDescriptor(
        table="match_players",
        select=["champion"],
        where=[
            WhereClause(column="role", op="=", value="mid"),
            WhereClause(column="side", op="=", value="blue"),
        ],
    )
    sql = _sql(compile_query(descriptor, schema))
    assert " AND " in sql
    assert "match_players.role = 'mid'" in sql
    assert "match_players.side = 'blue'" in sql


def test_compile_inner_join() -> None:
    schema = _truth_schema()
    descriptor = QueryDescriptor(
        table="match_players",
        joins=[
            JoinClause(
                table="players",
                on=("player_id", "id"),
                select=["summoner_name"],
            )
        ],
        select=["champion", "role"],
        where=[WhereClause(column="match_id", op="=", value="abc")],
    )
    sql = _sql(compile_query(descriptor, schema))
    assert "JOIN players ON match_players.player_id = players.id" in sql
    # FROM-side cols come before joined-side cols.
    assert "match_players.champion" in sql
    assert "players.summoner_name" in sql
    select_clause = sql.split(" FROM ")[0]
    assert select_clause.index("match_players.champion") < select_clause.index(
        "players.summoner_name"
    )


def test_compile_chained_joins_resolve_against_previously_joined() -> None:
    """A join's local column can live on FROM or any previously-joined
    table — the resolver walks the cumulative known set."""
    schema = _truth_schema()
    descriptor = QueryDescriptor(
        table="matches",
        joins=[
            JoinClause(
                table="match_players",
                on=("id", "match_id"),
                select=["champion"],
            ),
            JoinClause(
                # ``player_id`` lives on match_players (joined just above).
                table="players",
                on=("match_players.player_id", "id"),
                select=["summoner_name"],
            ),
        ],
        select=["blue_team"],
    )
    sql = _sql(compile_query(descriptor, schema))
    assert "JOIN match_players ON matches.id = match_players.match_id" in sql
    assert "JOIN players ON match_players.player_id = players.id" in sql


def test_compile_qualified_where_column() -> None:
    schema = _truth_schema()
    descriptor = QueryDescriptor(
        table="match_players",
        joins=[JoinClause(table="players", on=("player_id", "id"))],
        select=["champion"],
        where=[
            WhereClause(column="players.summoner_name", op="LIKE", value="Faker%"),
        ],
    )
    sql = _sql(compile_query(descriptor, schema))
    assert "players.summoner_name LIKE 'Faker%%'" in sql


def test_compile_order_by_multiple() -> None:
    schema = _truth_schema()
    descriptor = QueryDescriptor(
        table="match_players",
        select=["champion"],
        order=[
            OrderClause(column="side"),
            OrderClause(column="role", direction="desc"),
        ],
    )
    sql = _sql(compile_query(descriptor, schema))
    assert "ORDER BY match_players.side ASC, match_players.role DESC" in sql


def test_compile_limit_and_offset() -> None:
    schema = _truth_schema()
    descriptor = QueryDescriptor(
        table="matches",
        select=["id"],
        limit=10,
        offset=20,
    )
    sql = _sql(compile_query(descriptor, schema))
    assert "LIMIT 10" in sql
    assert "OFFSET 20" in sql


def test_compile_omits_limit_offset_when_unset() -> None:
    schema = _truth_schema()
    descriptor = QueryDescriptor(table="matches", select=["id"])
    sql = _sql(compile_query(descriptor, schema))
    assert "LIMIT" not in sql
    assert "OFFSET" not in sql


# ── Failure path ───────────────────────────────────────────────────────────


def test_compile_raises_compilation_error_on_validation_failure() -> None:
    schema = _truth_schema()
    descriptor = QueryDescriptor(
        table="match_players",
        select=["ghost_field"],
    )
    with pytest.raises(CompilationError) as exc_info:
        compile_query(descriptor, schema)
    issues = exc_info.value.issues
    assert len(issues) == 1
    assert issues[0].code == "unknown_select_column"
    assert issues[0].path == "select[0]"


def test_compile_raises_with_unknown_table() -> None:
    schema = _truth_schema()
    descriptor = QueryDescriptor(table="ghosts", select=["id"])
    with pytest.raises(CompilationError) as exc_info:
        compile_query(descriptor, schema)
    assert exc_info.value.issues[0].code == "unknown_table"


def test_compile_returned_select_is_executable_shape() -> None:
    """selected_columns reflects descriptor.select + each join.select in
    that order — the service relies on this to zip rows back to dicts."""
    schema = _truth_schema()
    descriptor = QueryDescriptor(
        table="match_players",
        joins=[
            JoinClause(
                table="players",
                on=("player_id", "id"),
                select=["summoner_name"],
            )
        ],
        select=["champion", "role", "side"],
    )
    stmt = compile_query(descriptor, schema)
    names = [c.name for c in stmt.selected_columns]
    assert names == ["champion", "role", "side", "summoner_name"]
