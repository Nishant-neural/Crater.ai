"""Phase 1 Step 5 gate tests: the approved CUTs are gone, with their
routes and UI swept (audit final test: cut only if neither core loop
nor scalability).

Cut: closeout (157 lines), review_view (280), the
Phase 5 expert trio (470), ProcedureViewer, ExpertKnowledge,
DigitalTwin demo panel. Drafting (960) was cut here too, but RESTORED
27 Nov 2026 as authoring-time tooling with a human-approval gate
(see backend/simulation/drafting.py) — it is no longer in CUT_MODULES.
Deferred (kept): learning, fleet, downtime,
pm, field_promotion, library_versions — the scalability base.
"""
import re
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from backend.db.models import Base
from backend.db.session import get_session

ROOT = Path(__file__).resolve().parent.parent

CUT_MODULES = [
    "backend.diagnostics.closeout",
    # NOTE (restore, 27 Nov 2026): "backend.simulation.drafting" was cut
    # here and has been restored as authoring-time tooling with a
    # human-approval gate — it is deliberately NOT in this list.
    "backend.simulation.review_view",
    "backend.knowledge.expert",
    "backend.knowledge.expert_schema",
    "backend.knowledge.expert_prompts",
    "backend.api.routes.expert",
]

CUT_FRONTEND = [
    "frontend/src/components/DigitalTwin.jsx",
    "frontend/src/components/ExpertKnowledge.jsx",
    "frontend/src/components/ProcedureViewer.jsx",
    "frontend/src/api/schematic.js",
    "frontend/src/api/evidence.js",
]

# The scalability base — explicitly DEFERRED, must still be present.
KEPT_MODULES = [
    "backend/diagnostics/learning.py",
    "backend/simulation/field_promotion.py",
    "backend/simulation/library_versions.py",
    "backend/diagnostics/safety.py",
]


def test_cut_backend_modules_are_gone():
    """No source file under backend/ or tests/ may import a CUT module
    (this test file itself is the only place the names may appear)."""
    here = Path(__file__).resolve()
    for tree in ("backend", "tests"):
        for path in sorted((ROOT / tree).rglob("*.py")):
            if path.resolve() == here:
                continue
            lines = path.read_text().splitlines()
            for i, line in enumerate(lines):
                for module in CUT_MODULES:
                    if module in line:
                        # Allowed: a cut-verification test that imports
                        # the module only to assert it raises
                        # ModuleNotFoundError (test_phase5_learning.py).
                        context = "\n".join(lines[max(0, i - 3):i + 1])
                        assert "raises(ModuleNotFoundError)" in context, (
                            f"{path.relative_to(ROOT)}:{i + 1} still "
                            f"references {module}")
    for module in CUT_MODULES:
        assert not (ROOT / (module.replace(".", "/") + ".py")).exists()


def test_cut_frontend_files_are_gone():
    for rel in CUT_FRONTEND:
        assert not (ROOT / rel).exists(), f"{rel} was cut in Step 5"
    # No remaining caller references the CUT components or the dead shims.
    # (The kept digital-twin client functions — createDigitalTwin etc. —
    # still contain "DigitalTwin"; only the demo-panel call is banned.)
    for path in (ROOT / "frontend" / "src").rglob("*"):
        if path.is_file() and path.suffix in (".jsx", ".js", ".css"):
            text = path.read_text(errors="ignore")
            for name in ("ExpertKnowledge", "ProcedureViewer",
                         "createDemoDigitalTwin", "getDigitalTwinEvents",
                         "api/schematic", "api/evidence"):
                assert name not in text, f"{path.relative_to(ROOT)} still references {name}"


def test_deferred_scalability_base_stays():
    for rel in KEPT_MODULES:
        assert (ROOT / rel).exists(), f"{rel} is DEFER, not a CUT"


@pytest.fixture()
def db():
    # StaticPool + check_same_thread False: the TestClient runs the app
    # in a worker thread, and the graph/client share this session.
    from sqlalchemy.pool import StaticPool
    engine = create_engine("sqlite:///:memory:",
                           connect_args={"check_same_thread": False},
                           poolclass=StaticPool)
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    session = factory()
    yield session
    session.close()


def _client(db, *routers):
    app = FastAPI()
    for router in routers:
        app.include_router(router)

    def _override():
        yield db

    app.dependency_overrides[get_session] = _override
    return TestClient(app, raise_server_exceptions=False)


def test_cut_routes_return_404(db):
    """The routes the CUTs owned are gone; the neighbouring kept routes
    still answer (fleet-patterns and safety GETs were explicitly kept)."""
    from backend.api.routes import diagnostics as diagnostics_routes
    from backend.api.routes import digital_twin as digital_twin_routes
    from backend.api.routes import learning as learning_routes

    client = _client(db, diagnostics_routes.router, learning_routes.router,
                     digital_twin_routes.router)
    assert client.get("/learning/closeout").status_code == 404
    assert client.post("/digital-twins/demo/p1/r1").status_code == 404
    # The expert router is gone entirely: no /expert/* route exists on
    # any mounted router.
    assert client.get("/expert/knowledge").status_code == 404
    assert client.post("/expert/interviews").status_code == 404
    # Kept neighbours still answer.
    assert client.get("/learning/review").status_code == 200


def test_no_expert_router_in_main_app():
    """The main app mounts no expert router (the CUT's route/UI sweep
    includes backend/api/main.py)."""
    text = (ROOT / "backend" / "api" / "main.py").read_text()
    # "expert knowledge" still appears in the app description prose;
    # what must be gone is the router mount.
    assert "routes.expert" not in text
    assert "expert_router" not in text
    assert "verify" in text.lower(), "the /v1/verify router is mounted"


def test_oracle_pre_step_shared_module_exists():
    """Oracle pre-step (Step 5): load_fault_injections / normalize_text
    live in the shared module and the old import sites still resolve."""
    assert (ROOT / "backend" / "simulation" / "oracle_shared.py").exists()
    from backend.simulation.oracle_shared import (
        load_fault_injections, normalize_text)
    from benchmark import oracle
    from backend.simulation import repair_actions
    assert oracle.load_fault_injections is load_fault_injections
    assert oracle.normalize_text is normalize_text
    assert repair_actions.load_fault_injections is load_fault_injections
    assert normalize_text("  Feed  Tensioner ") == "feed tensioner"
