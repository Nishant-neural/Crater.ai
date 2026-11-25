#!/bin/sh
# Ingestion startup: Alembic is the ONLY schema owner on Postgres.
# Fix for DuplicateTable "archetype_row_flags already exists":
# if the Postgres volume already has tables (created by the old
# init_db/create_all, or by an interrupted upgrade) but alembic_version
# is empty/missing, `alembic upgrade head` blindly re-runs the initial
# migration and dies. In that case stamp the existing schema as head
# first, then upgrade normally. On a fresh DB, stamp is skipped and
# upgrade head creates everything.
set -e
uv run python - <<'PY'
import os
from sqlalchemy import create_engine, inspect, text
url = os.environ["DATABASE_URL"]
eng = create_engine(url)
insp = inspect(eng)
tables = set(insp.get_table_names())
has_archetype = "archetype_row_flags" in tables
version_rows = []
if "alembic_version" in tables:
    with eng.connect() as c:
        version_rows = list(c.execute(text("SELECT version_num FROM alembic_version")))
print(f"tables={len(tables)} archetype_row_flags={has_archetype} alembic_version={version_rows}")
if has_archetype and not version_rows:
    print("Tables exist but alembic_version is empty: stamping head (no migration run).")
    os.system("uv run alembic stamp head")
PY
uv run alembic upgrade head
exec uv run uvicorn backend.ingestion.app:app --host 0.0.0.0 --port 8000
