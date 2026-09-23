"""Central settings, loaded from environment / .env."""
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    anthropic_api_key: str = ""
    anthropic_model: str = "claude-sonnet-4-6"
    gemini_api_key: str = ""
    gemini_model: str = "gemini-2.5-flash"
    llm_provider: str = "anthropic"
    llm_model: str = ""

    # Task-specific routing. Environment variables can override the individual
    # model names without changing application code.
    extraction_model: str = ""
    integration_model: str = ""
    diagnosis_model: str = ""
    rerank_model: str = ""
    extraction_provider: str = ""
    integration_provider: str = ""
    diagnosis_provider: str = ""
    rerank_provider: str = ""
    vision_provider: str = ""
    vision_model: str = ""
    expert_provider: str = ""
    expert_model: str = ""


    database_url: str = "sqlite:///./crater.db"

    qdrant_url: str = "http://localhost:6333"
    qdrant_collection: str = "crater_chunks"

    embedding_model: str = "sentence-transformers/all-MiniLM-L6-v2"

    tesseract_cmd: str = "tesseract"

    # Chunking
    chunk_size_chars: int = 1800
    chunk_overlap_chars: int = 200
    chunk_min_chars: int = 500
    chunk_max_chars: int = 2200

    # Retrieval
    hybrid_top_k: int = 40          # candidates pulled from each of vector/BM25 before fusion
    final_top_k: int = 8            # results returned after fusion + rerank

    @property
    def model_routes(self) -> dict:
        return {
            "extraction": {"provider": self.extraction_provider, "model": self.extraction_model, "max_tokens": 4000},
            "integration": {"provider": self.integration_provider, "model": self.integration_model, "max_tokens": 12000},
            "diagnosis": {"provider": self.diagnosis_provider, "model": self.diagnosis_model, "max_tokens": 2500},
            "rerank": {"provider": self.rerank_provider, "model": self.rerank_model, "max_tokens": 500},
            "vision": {"provider": self.vision_provider, "model": self.vision_model, "max_tokens": 2000},
            "expert": {"provider": self.expert_provider, "model": self.expert_model, "max_tokens": 1200},
        }

    @property
    def task_max_tokens(self) -> dict:
        return {"extraction": 4000, "integration": 12000, "diagnosis": 2500, "rerank": 500, "vision": 2000, "expert": 1200}


settings = Settings()
