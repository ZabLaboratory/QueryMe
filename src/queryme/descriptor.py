"""Query descriptor — the structured payload services receive on
``POST /api/v1/_query``.

The shape is locked by ADR 001 §7 to the canonical modal-compatible
form :

    from -> (where|join)* -> select -> (order|limit)?

Operators on WHERE are restricted to the v1 closed list ; OR and
sub-expressions are out of scope. A graph that exceeds this shape
opens in graph mode and never reaches a service — so the wire shape
stays predictable.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

# ---------------------------------------------------------------------------
# Operators
# ---------------------------------------------------------------------------

#: The closed set of WHERE operators. ``IS NULL`` ignores ``value``.
#: ``IN`` requires a list. ``LIKE`` requires a string with optional
#: ``%`` wildcards (services compile to ``ILIKE`` if they want to be
#: case-insensitive — that's a per-service call, not a descriptor flag).
Operator = Literal[
    "=",
    "!=",
    ">",
    "<",
    ">=",
    "<=",
    "IN",
    "LIKE",
    "IS NULL",
]

OPERATORS: tuple[Operator, ...] = (
    "=",
    "!=",
    ">",
    "<",
    ">=",
    "<=",
    "IN",
    "LIKE",
    "IS NULL",
)


OrderDirection = Literal["asc", "desc"]


# ---------------------------------------------------------------------------
# Clauses
# ---------------------------------------------------------------------------


class WhereClause(BaseModel):
    """A single WHERE predicate. AND-joined with siblings — no OR in v1.

    ``column`` is either ``"<column>"`` (referring to the FROM table)
    or ``"<table>.<column>"`` (referring to a joined table). The
    validator enforces that ``<table>`` is one of FROM + the joined
    set.
    """

    model_config = ConfigDict(extra="forbid")

    column: str = Field(min_length=1)
    op: Operator
    value: Any = None

    @model_validator(mode="after")
    def _check_value_shape(self) -> WhereClause:
        if self.op == "IS NULL":
            # value is meaningless for IS NULL — accept whatever comes
            # in but normalise to None so downstream consumers don't
            # have to special-case the wire shape.
            object.__setattr__(self, "value", None)
            return self
        if self.op == "IN":
            if not isinstance(self.value, list):
                raise ValueError("IN requires a list value")
            return self
        if self.op == "LIKE":
            if not isinstance(self.value, str):
                raise ValueError("LIKE requires a string value")
            return self
        # Comparison operators: any scalar JSON-compatible value goes.
        # Type compatibility with the column is the validator/compiler's job.
        return self


class JoinClause(BaseModel):
    """A JOIN against another table on a single (left, right) column pair.

    ``on`` is a tuple ``(local_column, foreign_column)`` where
    ``local_column`` is on the FROM table (or a previously-joined
    table) and ``foreign_column`` is on the joined-in ``table``.
    Multi-column joins are out of scope for v1 — services compile
    a single equality.

    ``select`` is the subset of columns from the joined table to
    project alongside the FROM table's columns. Empty list = join
    only (filter effect, no extra columns).
    """

    model_config = ConfigDict(extra="forbid")

    table: str = Field(min_length=1)
    on: tuple[str, str]
    select: list[str] = Field(default_factory=list)


class OrderClause(BaseModel):
    """An ORDER BY entry. Multiple stack — first one is the primary key,
    subsequent ones break ties.
    """

    model_config = ConfigDict(extra="forbid")

    column: str = Field(min_length=1)
    direction: OrderDirection = "asc"


# ---------------------------------------------------------------------------
# QueryDescriptor
# ---------------------------------------------------------------------------


class QueryDescriptor(BaseModel):
    """The full read query.

    Wire shape — what the blueprint executor POSTs to
    ``/<svc>/api/v1/_query`` and what the service compiles. Inputs from
    the calling blueprint must already be resolved into concrete
    ``value`` fields ; the descriptor is data, not a template.
    """

    model_config = ConfigDict(extra="forbid")

    # FROM
    table: str = Field(min_length=1)

    # WHERE (AND-joined)
    where: list[WhereClause] = Field(default_factory=list)

    # JOIN
    joins: list[JoinClause] = Field(default_factory=list)

    # SELECT — columns from the FROM table. JoinClause.select adds
    # joined-table columns.
    select: list[str] = Field(default_factory=list)

    # ORDER BY (stacked)
    order: list[OrderClause] = Field(default_factory=list)

    # LIMIT / OFFSET. ``None`` = no clause emitted.
    limit: int | None = Field(default=None, ge=0)
    offset: int | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def _no_dup_joins(self) -> QueryDescriptor:
        seen: set[str] = set()
        for j in self.joins:
            if j.table in seen:
                raise ValueError(f"duplicate join on table {j.table!r}")
            seen.add(j.table)
        return self
