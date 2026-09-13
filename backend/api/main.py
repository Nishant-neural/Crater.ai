"""FastAPI app entrypoint. Run with: uvicorn crater.api.main:app --reload"""
from contextlib import asynccontextmanager

from fastapi import FastAPI

from crater.api.routes import diagnostics, ingestion, products, query, schematics
from crater.db.session import init_db


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    yield


app = FastAPI(title="Crater.ai — Phases 1-3: Product Knowledge, Diagnostic Agent, Schematic Intelligence", version="0.1.0", lifespan=lifespan)

app.include_router(products.router)
app.include_router(ingestion.router)
app.include_router(query.router)
app.include_router(diagnostics.router)
app.include_router(schematics.router)


@app.get("/health")
def health():
    return {"status": "ok"}
