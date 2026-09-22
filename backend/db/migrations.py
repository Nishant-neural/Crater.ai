"""Small Phase 8A schema compatibility helpers.

The project still uses SQLAlchemy ``create_all`` for the MVP. This helper adds
new Phase 8A columns to an existing database without requiring a full Alembic
migration yet. Legacy ports/states JSON columns are intentionally left in old
databases but are no longer mapped or written by the application.
"""
from __future__ import annotations

from sqlalchemy import inspect, text
from sqlalchemy.engine import Engine


_REQUIRED_COLUMNS = {
    "machine_knowledge_entities": {
        "canonical_id": "VARCHAR",
    },
    "machine_knowledge_evidence": {
        "revision_id": "VARCHAR",
    },
}


def ensure_phase8a_columns(engine: Engine) -> None:
    inspector = inspect(engine)
    for table, columns in _REQUIRED_COLUMNS.items():
        if not inspector.has_table(table):
            continue
        existing = {column["name"] for column in inspector.get_columns(table)}
        with engine.begin() as connection:
            for name, sql_type in columns.items():
                if name in existing:
                    continue
                connection.execute(text(
                    f"ALTER TABLE {table} ADD COLUMN {name} {sql_type}"
                ))

        if table == "machine_knowledge_entities" and "canonical_id" not in existing:
            with engine.begin() as connection:
                connection.execute(text(
                    "UPDATE machine_knowledge_entities "
                    "SET canonical_id = id WHERE canonical_id IS NULL"
                ))
