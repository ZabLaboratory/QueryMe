"""Descriptor → SQLAlchemy ``Select`` compiler.

**Stub in v0.1.0.** The compilation step needs design decisions about
how the consuming service wires its SQLAlchemy ``MetaData`` into the
compiler (declarative-mapper introspection vs. explicit ``Table``
registry vs. on-the-fly ``Table`` construction from
``SchemaDescriptor``). Those are out of scope for the scaffold ; the
real implementation lands in v0.2.0 alongside the first consuming
service (ZabTruth).

Until then, callers should validate the descriptor with
``validate_against_schema`` and apply their own compilation. This
function exists to lock the public signature : every consumer will
import the exact same name when v0.2.0 ships.
"""

from __future__ import annotations

from typing import Any

from queryme.descriptor import QueryDescriptor
from queryme.schema import SchemaDescriptor


def compile_query(
    descriptor: QueryDescriptor,
    schema: SchemaDescriptor,
    metadata: Any,  # noqa: ANN401 — sqlalchemy.MetaData ; typed loosely until v0.2.0
) -> Any:  # noqa: ANN401 — sqlalchemy.Select ; same.
    """Turn a validated ``QueryDescriptor`` into a SQLAlchemy
    ``Select`` against ``metadata``.

    Raises :class:`NotImplementedError` in v0.1.0 — the signature is
    locked, the body lands in v0.2.0.
    """
    raise NotImplementedError(
        "queryme.compile_query is not implemented in v0.1.0 — "
        "see ADR 001 §Phase 2. Validate with validate_against_schema "
        "and compile in your service in the meantime."
    )
