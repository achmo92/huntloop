"""Portable INSERT ... ON CONFLICT DO UPDATE, shared by both stores.

Guardrail #1 (idempotent, upsert-based writes keyed on a stable external ID).
Every write path that could plausibly run twice with the same natural key MUST
go through this helper rather than a blind INSERT or a SELECT-then-branch
get_or_create (which is a TOCTOU race under any concurrent writer).
"""

from sqlalchemy import Table
from sqlalchemy.engine import Connection


def upsert(
    conn: Connection,
    table: Table,
    index_elements: list[str],
    values: dict,
    update_columns: list[str] | None = None,
) -> None:
    dialect = conn.engine.dialect.name
    if dialect == "postgresql":
        from sqlalchemy.dialects.postgresql import insert
    elif dialect == "sqlite":
        from sqlalchemy.dialects.sqlite import insert
    else:
        raise NotImplementedError(f"upsert() has no dialect support for: {dialect}")

    stmt = insert(table).values(**values)
    cols = (
        update_columns
        if update_columns is not None
        else [c for c in values if c not in index_elements]
    )
    if cols:
        stmt = stmt.on_conflict_do_update(
            index_elements=index_elements,
            set_={c: getattr(stmt.excluded, c) for c in cols},
        )
    else:
        # No columns to refresh on conflict (e.g. a first-registration
        # upsert with no extra discovery fields supplied yet). An empty
        # SET clause is invalid SQL on both backends, so this degrades to
        # a no-op on conflict rather than raising.
        stmt = stmt.on_conflict_do_nothing(index_elements=index_elements)
    conn.execute(stmt)
