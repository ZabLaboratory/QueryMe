"""Whitelist validation : a ``QueryDescriptor`` against a
``SchemaDescriptor``.

Returns a list of structured ``ValidationIssue`` rather than raising,
so callers (services receiving ``_query`` requests, the test-node
endpoint, the editor previewing a query) can surface every problem in
one round-trip rather than fixing them one at a time.

A validated descriptor is **not** safe to execute by itself — the
compiler is the next gate. The validator only proves that every
table/column the descriptor references actually exists in the schema.
Type compatibility between WHERE values and column types is delegated
to the compiler / database layer.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from queryme.descriptor import QueryDescriptor
from queryme.schema import SchemaDescriptor, TableDef

IssueCode = Literal[
    "unknown_table",
    "unknown_column",
    "unknown_join_table",
    "unknown_join_column",
    "unknown_order_column",
    "unknown_select_column",
    "duplicate_join",
    "empty_select",
    "limit_out_of_bounds",
]


class ValidationIssue(BaseModel):
    """A single problem found during validation.

    ``path`` is a dotted reference to the offending field in the
    descriptor (e.g. ``where[2].column`` or ``joins[0].on[1]``) — the
    editor highlights the right node in the inline graph from this.
    """

    model_config = ConfigDict(extra="forbid")

    code: IssueCode
    message: str
    path: str = Field(min_length=1)


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def validate_against_schema(
    descriptor: QueryDescriptor,
    schema: SchemaDescriptor,
) -> list[ValidationIssue]:
    """Walk every reference in ``descriptor`` and check it against the
    declared ``schema``. Empty list = valid.
    """
    issues: list[ValidationIssue] = []

    base_table = schema.table(descriptor.table)
    if base_table is None:
        issues.append(
            ValidationIssue(
                code="unknown_table",
                message=f"Table {descriptor.table!r} is not exposed by service {schema.service!r}",
                path="table",
            )
        )
        # Without a base table every other check would be noise — bail
        # early so the caller sees the root cause first.
        return issues

    # ── Build a "known tables" set as we walk through joins, so a
    # ── second join can reference a previously-joined table. We also
    # ── keep TableDef references handy for column lookups.
    known: dict[str, TableDef] = {base_table.name: base_table}

    # ── JOINS
    for idx, join in enumerate(descriptor.joins):
        joined = schema.table(join.table)
        if joined is None:
            issues.append(
                ValidationIssue(
                    code="unknown_join_table",
                    message=f"Joined table {join.table!r} is not exposed",
                    path=f"joins[{idx}].table",
                )
            )
            # Skip column checks for an unknown table — the user fixes
            # the table first, columns next round.
            continue

        local_col, foreign_col = join.on
        # ``local`` resolves either against the FROM table or any
        # previously-joined table — the descriptor doesn't specify
        # which, the validator finds the first match. If it's
        # ambiguous, the editor should make the user qualify with
        # ``"<table>.<column>"`` ; we accept both forms.
        if not _column_resolves(local_col, known):
            issues.append(
                ValidationIssue(
                    code="unknown_join_column",
                    message=f"Local join column {local_col!r} not found on FROM/joined tables",
                    path=f"joins[{idx}].on[0]",
                )
            )
        if not _has_column(joined, foreign_col):
            issues.append(
                ValidationIssue(
                    code="unknown_join_column",
                    message=f"Foreign join column {foreign_col!r} not on table {joined.name!r}",
                    path=f"joins[{idx}].on[1]",
                )
            )

        for j_idx, col in enumerate(join.select):
            if not _has_column(joined, col):
                issues.append(
                    ValidationIssue(
                        code="unknown_select_column",
                        message=f"Selected column {col!r} not on joined table {joined.name!r}",
                        path=f"joins[{idx}].select[{j_idx}]",
                    )
                )

        known[joined.name] = joined

    # ── WHERE
    for idx, predicate in enumerate(descriptor.where):
        if not _column_resolves(predicate.column, known):
            issues.append(
                ValidationIssue(
                    code="unknown_column",
                    message=f"WHERE column {predicate.column!r} not found on FROM/joined tables",
                    path=f"where[{idx}].column",
                )
            )

    # ── SELECT (FROM table columns)
    if not descriptor.select and not any(j.select for j in descriptor.joins):
        issues.append(
            ValidationIssue(
                code="empty_select",
                message="Query selects no column — at least one column on the FROM table or a join is required",
                path="select",
            )
        )
    for idx, col in enumerate(descriptor.select):
        if not _has_column(base_table, col):
            issues.append(
                ValidationIssue(
                    code="unknown_select_column",
                    message=f"Selected column {col!r} not on FROM table {base_table.name!r}",
                    path=f"select[{idx}]",
                )
            )

    # ── ORDER
    for idx, clause in enumerate(descriptor.order):
        if not _column_resolves(clause.column, known):
            issues.append(
                ValidationIssue(
                    code="unknown_order_column",
                    message=f"ORDER BY column {clause.column!r} not found on FROM/joined tables",
                    path=f"order[{idx}].column",
                )
            )

    return issues


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------


def _has_column(table: TableDef, column: str) -> bool:
    return any(c.name == column for c in table.columns)


def _column_resolves(reference: str, known: dict[str, TableDef]) -> bool:
    """A reference is either ``"col"`` (any known table has it) or
    ``"table.col"`` (that specific table has it). Returns True iff
    the reference resolves to at least one declared column."""
    if "." in reference:
        table_name, _, col_name = reference.partition(".")
        table = known.get(table_name)
        if table is None:
            return False
        return _has_column(table, col_name)
    return any(_has_column(t, reference) for t in known.values())
