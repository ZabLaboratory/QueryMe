"""Compiler — stub in v0.1.0. Lock the public signature so consumers
can import it confidently while the body is filled in for v0.2.0."""

from __future__ import annotations

import pytest

from queryme.compiler import compile_query
from queryme.descriptor import QueryDescriptor
from queryme.schema import ColumnDef, SchemaDescriptor, TableDef


def test_compile_query_raises_not_implemented() -> None:
    schema = SchemaDescriptor(
        service="truth",
        tables=[TableDef(name="matches", columns=[ColumnDef(name="id", type="uuid")])],
    )
    descriptor = QueryDescriptor(table="matches", select=["id"])
    with pytest.raises(NotImplementedError) as exc:
        compile_query(descriptor, schema, metadata=None)
    assert "v0.1.0" in str(exc.value)
