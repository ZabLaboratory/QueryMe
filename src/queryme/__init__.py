"""QueryMe — shared query toolkit for the Zablab platform.

Public API:

- ``QueryDescriptor`` (and friends) — the structured shape blueprints
  emit and services receive on ``POST /api/v1/_query``.
- ``SchemaDescriptor`` — what every service exposes via
  ``GET /api/v1/_schema``.
- ``validate_against_schema()`` — whitelist check against the
  service's declared schema.
- ``compile_query()`` — turns a validated descriptor into a
  SQLAlchemy ``Select`` (stub in v0.1.0).
- ``ValidationIssue`` — structured issue type returned by the validator.

See ADR 001 in ``D:/Document/ZabLaboratory/docs/adr/001-blueprint-db-access.md``
for the canonical form locked in v0.1.0.
"""

from queryme.compiler import CompilationError, compile_query
from queryme.descriptor import (
    JoinClause,
    Operator,
    OrderClause,
    OrderDirection,
    QueryDescriptor,
    WhereClause,
)
from queryme.schema import (
    ColumnDef,
    RelationDef,
    RelationKind,
    SchemaDescriptor,
    TableDef,
)
from queryme.validator import ValidationIssue, validate_against_schema

__all__ = [
    "ColumnDef",
    "CompilationError",
    "JoinClause",
    "Operator",
    "OrderClause",
    "OrderDirection",
    "QueryDescriptor",
    "RelationDef",
    "RelationKind",
    "SchemaDescriptor",
    "TableDef",
    "ValidationIssue",
    "WhereClause",
    "compile_query",
    "validate_against_schema",
]

__version__ = "0.2.0"
