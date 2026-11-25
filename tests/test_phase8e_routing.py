from backend.config import Settings
from backend.llm.model_router import ModelGateway


def test_gateway_routes_tasks_to_configured_models():
    s = Settings(
        llm_provider="gemini", gemini_model="gemini-default",
        extraction_model="gemini-flash", integration_model="gemini-pro",
        diagnosis_model="gemini-pro",
    )
    g = ModelGateway()
    import importlib
    mod = importlib.import_module("backend.llm.model_router")
    old = mod.settings
    mod.settings = s
    try:
        assert g.route("extraction").model == "gemini-flash"
        assert g.route("integration").model == "gemini-pro"
        assert g.route("diagnosis").model == "gemini-pro"
        # Phase 1 Step 3: reranking is a local CrossEncoder, not an LLM
        # route -- there is no "rerank" entry in model_routes any more.
        assert "rerank" not in s.model_routes
    finally:
        mod.settings = old
