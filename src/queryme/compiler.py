"""Descriptor → SQLAlchemy ``Select`` compiler.

Self-contained : the compiler builds a synthetic ``MetaData`` from the
``SchemaDescriptor`` and resolves every column reference against it.
Consuming services do not pass their own metadata — they just execute
the returned ``Select`` against their async engine and serialise rows.

Validation is re-run inside ``compile_query`` (defensive : the caller
might forget). On any issue, ``CompilationError`` is raised carrying
the structured issues — never a half-built statement.
"""

from __future__ import annotations

from typing import Any, cast

from sqlalchemy import (
    JSON,
    Boolean,
    Column,
    Date,
    DateTime,
    Float,
    Integer,
    MetaData,
    Select,
    String,
    Table,
    Text,
    Uuid,
    and_,
    asc,
    desc,
    select,
)
from sqlalchemy.sql import ColumnElement
from sqlalchemy.types import TypeEngine

from queryme.descriptor import Operator, QueryDescriptor
from queryme.schema import ColumnType, SchemaDescriptor, TableDef
from queryme.validator import ValidationIssue, validate_against_schema

# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class CompilationError(Exception):
    """Raised when ``compile_query`` cannot produce a statement.

    Carries the structured ``issues`` from the validator so the caller
    can surface them inline (the editor highlights the right node, the
    service returns a 400 with the same payload Blue would have used at
    test-node time).
    """

    def __init__(self, issues: list[ValidationIssue]) -> None:
        self.issues = issues
        super().__init__(
            f"compile_query failed validation with {len(issues)} issue(s) — "
            f"first: {issues[0].code} at {issues[0].path!r}"
            if issues
            else "compile_query failed validation"
        )


# ---------------------------------------------------------------------------
# Type mapping (closed set per schema.ColumnType)
# ---------------------------------------------------------------------------


_TYPE_MAP: dict[ColumnType, type[TypeEngine[Any]]] = {
    "string": String,
    "text": Text,
    "integer": Integer,
    "float": Float,
    "boolean": Boolean,
    "uuid": Uuid,
    "datetime": DateTime,
    "date": Date,
    "json": JSON,
}


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def compile_query(
    descriptor: QueryDescriptor,
    schema: SchemaDescriptor,
) -> Select[Any]:
    """Turn a ``QueryDescriptor`` into a SQLAlchemy ``Select``.

    The schema is the source of truth — the compiler builds a fresh
    ``MetaData`` containing only the tables this query touches. The
    consuming service runs the result against its own engine ; row
    serialisation is the service's call (typically zip with
    ``stmt.selected_columns`` to produce dict rows).

    Raises :class:`CompilationError` if validation fails. Does **not**
    catch SQLAlchemy errors that may still arise at execution time
    (type coercion mismatches, dialect-specific issues) — those surface
    when the service runs the statement.
    """
    issues = validate_against_schema(descriptor, schema)
    if issues:
        raise CompilationError(issues)

    metadata = MetaData()
    tables: dict[str, Table] = {}

    base_def = schema.table(descriptor.table)
    # Validator guarantees this — assert is for the type-checker, not for users.
    assert base_def is not None
    tables[base_def.name] = _build_table(metadata, base_def)
    for join in descriptor.joins:
        joined_def = schema.table(join.table)
        assert joined_def is not None
        tables[joined_def.name] = _build_table(metadata, joined_def)

    base_table = tables[base_def.name]

    # SELECT — FROM columns first, then each join's projection, in
    # descriptor order. The service can rely on this order to zip with
    # column names back into dict rows.
    cols_to_select: list[ColumnElement[Any]] = [
        base_table.c[c] for c in descriptor.select
    ]
    for join in descriptor.joins:
        joined_table = tables[join.table]
        cols_to_select.extend(joined_table.c[c] for c in join.select)

    stmt: Select[Any] = select(*cols_to_select).select_from(base_table)

    # JOINs (INNER). Local column resolves against accumulated known
    # tables (FROM + previously joined) so a 3-way join chain Just
    # Works ; foreign column always belongs to the joined table.
    cumul_known: dict[str, Table] = {base_def.name: base_table}
    for join in descriptor.joins:
        joined_table = tables[join.table]
        local_col, foreign_col = join.on
        local_column = _resolve_column(local_col, cumul_known)
        foreign_column = joined_table.c[foreign_col]
        stmt = stmt.join(joined_table, local_column == foreign_column)
        cumul_known[join.table] = joined_table

    # WHERE — AND-joined per ADR §7.
    where_terms: list[ColumnElement[Any]] = []
    for w in descriptor.where:
        col = _resolve_column(w.column, tables)
        where_terms.append(_apply_op(col, w.op, w.value))
    if where_terms:
        stmt = stmt.where(and_(*where_terms))

    # ORDER BY — multiple stack, first is primary.
    for o in descriptor.order:
        col = _resolve_column(o.column, tables)
        stmt = stmt.order_by(asc(col) if o.direction == "asc" else desc(col))

    # LIMIT / OFFSET — emitted only when explicitly set.
    if descriptor.limit is not None:
        stmt = stmt.limit(descriptor.limit)
    if descriptor.offset is not None:
        stmt = stmt.offset(descriptor.offset)

    return stmt


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------


def _build_table(metadata: MetaData, table_def: TableDef) -> Table:
    """Materialise a ``TableDef`` as a SQLAlchemy ``Table`` on the
    given metadata. Each column becomes a ``Column`` with the mapped
    type, nullability and primary-key flag — enough to compile a
    SELECT correctly. We do not register relations as ``ForeignKey``
    because joins are explicit in the descriptor."""
    cols: list[Column[Any]] = []
    for c in table_def.columns:
        sa_type = _TYPE_MAP[c.type]()
        cols.append(
            Column(c.name, sa_type, nullable=c.nullable, primary_key=c.primary)
        )
    return Table(table_def.name, metadata, *cols)


def _resolve_column(
    reference: str, tables: dict[str, Table],
) -> ColumnElement[Any]:
    """Resolve a column reference into a SQLAlchemy column.

    Qualified ``"table.col"`` looks up the specific table ; unqualified
    ``"col"`` returns the first match across the known tables. The
    validator already proved the reference resolves at least once, so
    a missed lookup here is an internal contract violation, not user
    input — surface it as a ``KeyError`` for the test suite to catch.
    """
    if "." in reference:
        table_name, _, col_name = reference.partition(".")
        return tables[table_name].c[col_name]
    for t in tables.values():
        if reference in t.c:
            return t.c[reference]
    raise KeyError(reference)


def _apply_op(
    col: ColumnElement[Any], op: Operator, value: Any,  # noqa: ANN401 — opaque JSON value
) -> ColumnElement[Any]:
    """Map a single ``WhereClause`` to its SQLAlchemy expression.

    The closed ``Operator`` set is exhaustive — mypy enforces the
    dispatch is total. ``IS NULL`` ignores ``value`` (validator
    normalises it to ``None``). ``IN`` and ``LIKE`` shapes are
    enforced by ``WhereClause`` itself, not re-checked here.
    """
    # SQLAlchemy's ColumnOperators stubs return Any for comparison
    # operators (overload soup) ; cast at the boundary so mypy strict
    # stays clean without leaking Any out to call sites.
    expr: Any
    if op == "=":
        expr = col == value
    elif op == "!=":
        expr = col != value
    elif op == ">":
        expr = col > value
    elif op == "<":
        expr = col < value
    elif op == ">=":
        expr = col >= value
    elif op == "<=":
        expr = col <= value
    elif op == "IN":
        expr = col.in_(value)
    elif op == "LIKE":
        expr = col.like(value)
    else:  # op == "IS NULL"
        expr = col.is_(None)
    return cast(ColumnElement[Any], expr)
