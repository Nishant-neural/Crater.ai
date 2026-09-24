from backend.config import Settings
from backend.llm.gateway import ModelGateway


def test_gateway_routes_tasks_to_configured_models():
    s = Settings(
        llm_provider="gemini", gemini_model="gemini-default",
        extraction_model="gemini-flash", integration_model="gemini-pro",
        diagnosis_model="gemini-pro", rerank_model="gemini-flash",
    )
    g = ModelGateway()
    import importlib
    mod = importlib.import_module("backend.llm.gateway")
    old = mod.settings
    mod.settings = s
    try:
        assert g.route("extraction").model == "gemini-flash"
        assert g.route("integration").model == "gemini-pro"
        assert g.route("diagnosis").model == "gemini-pro"
        assert g.route("rerank").model == "gemini-flash"
    finally:
        mod.settings = old
