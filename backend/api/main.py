"""FastAPI app entrypoint. Run with: uvicorn crater.api.main:app --reload"""
from contextlib import asynccontextmanager

from fastapi import FastAPI

from backend.api.routes import diagnostics, expert, ingestion, products, query, schematics, visualization
from backend.db.session import init_db


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    yield


app = FastAPI(
    title="Crater.ai — Phases 1-4: Product Knowledge, Diagnostic Agent, Schematic Intelligence, Technical Visualization",
    version="0.1.0",
    lifespan=lifespan,
)

app.include_router(products.router)
app.include_router(ingestion.router)
app.include_router(query.router)
app.include_router(diagnostics.router)
app.include_router(schematics.router)
app.include_router(visualization.router)
app.include_router(expert.router)


@app.get("/health")
def health():
    return {"status": "ok"}
