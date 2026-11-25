"""SQLAlchemy engine/session setup + a FastAPI dependency."""
from collections.abc import Generator

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import Session, sessionmaker

from backend.config import settings
from backend.db.models import Base

engine = create_engine(
    settings.database_url,
    connect_args={"check_same_thread": False} if settings.database_url.startswith("sqlite") else {},
)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)


def init_db() -> None:
    """Create tables and add Phase 8A integration columns for existing SQLite DBs.

    Alembic is the ONLY schema owner on Postgres (contracts 8.2).
    Base.metadata.create_all must never run against Postgres: it
    creates tables without stamping alembic_version, so the next
    alembic upgrade head re-runs the initial migration and dies
    with DuplicateTable: relation "archetype_row_flags" already
    exists - exactly the ingestion startup failure. On Postgres
    this function only runs the Phase 8A column backfill (guarded
    by the inspector); schema creation is Alembic job, in the
    ingestion container startup script (scripts/start_ingestion.sh).
    """
    if settings.database_url.startswith("sqlite"):
        Base.metadata.create_all(bind=engine)
    # else Postgres: Alembic owns the schema, never create_all here.
    inspector = inspect(engine)
    with engine.begin() as conn:
        for table, column in (("machine_knowledge_entities", "canonical_id"), ("machine_knowledge_relations", "canonical_id"), ("machine_knowledge_behaviors", "canonical_id"), ("machine_knowledge_evidence", "claim_status")):
            if table in inspector.get_table_names() and column not in {c["name"] for c in inspector.get_columns(table)}:
                conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {column} VARCHAR DEFAULT 'observed'"))


def get_session() -> Generator[Session, None, None]:
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()
