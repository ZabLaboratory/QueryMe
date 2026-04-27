"""Schema descriptor — what every service exposes on
``GET /api/v1/_schema``.

Services declare their schema statically (a single Python dict
literal in code) ; the blueprint catalog polls these endpoints and
caches the result so the editor can render autocomplete and the
validator can whitelist-check incoming descriptors.

Per ADR 001 §1 the ``writable`` flag on ``TableDef`` is the authority
for whether a table accepts ``POST /_mutate`` calls — Blue trusts what
the service says rather than maintaining a parallel allowlist.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

#: Coarse normalised types ; services map their PG-specific types
#: into this closed list so the editor can pick widgets and the
#: validator can sanity-check ``WhereClause.value`` shapes. Anything
#: exotic (``jsonb``, geometry, custom enums) is reported as
#: ``"json"`` and the service handles coercion at compile time.
ColumnType = Literal[
    "string",
    "text",
    "integer",
    "float",
    "boolean",
    "uuid",
    "datetime",
    "date",
    "json",
]

#: A relation between two tables — the editor uses these to suggest
#: joins that actually make sense (you joined ``match_players`` ?
#: it has a ``belongs_to players`` via ``player_id``).
RelationKind = Literal["has_many", "has_one", "belongs_to"]


class ColumnDef(BaseModel):
    """A single column on a table."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1)
    type: ColumnType
    nullable: bool = False
    primary: bool = False
    description: str | None = None


class RelationDef(BaseModel):
    """A typed link from this table to another table in the same service.

    Cross-service relations are intentionally absent — joins across
    services are not supported by ``_query`` (services own their DB ;
    crossing the boundary needs blueprint composition, not a SQL JOIN).
    """

    model_config = ConfigDict(extra="forbid")

    to: str = Field(min_length=1)
    via: str = Field(min_length=1, description="FK column on this table")
    kind: RelationKind


class TableDef(BaseModel):
    """A table exposed by the service to blueprints."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1)
    columns: list[ColumnDef]
    relations: list[RelationDef] = Field(default_factory=list)
    writable: bool = False
    description: str | None = None


class SchemaDescriptor(BaseModel):
    """The full schema block returned by ``GET /api/v1/_schema``."""

    model_config = ConfigDict(extra="forbid")

    service: str = Field(min_length=1, description="Gateway prefix without leading slash, e.g. 'truth'")
    tables: list[TableDef] = Field(default_factory=list)

    def table(self, name: str) -> TableDef | None:
        """Lookup helper — returns ``None`` if the table is not exposed."""
        for t in self.tables:
            if t.name == name:
                return t
        return None
