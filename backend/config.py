"""Central settings, loaded from environment / .env."""
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    anthropic_api_key: str = ""
    anthropic_model: str = "claude-sonnet-4-6"
    gemini_api_key: str = ""
    gemini_model: str = "gemini-3.8-flash"
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
    # Technical manuals benefit from larger semantic context. Keep enough text
    # together for the extractor to see component descriptions, specifications,
    # procedures, and their surrounding context instead of isolated fragments.
    chunk_size_chars: int = 12000
    chunk_overlap_chars: int = 1000
    chunk_min_chars: int = 2500
    chunk_max_chars: int = 15000

    # Gemini free-tier/project limits are enforced as requests per minute.
    # Stay below a 15 RPM ceiling with headroom for transient/retry behavior.
    extraction_gemini_rpm: int = 10
    extraction_rate_limit_backoff_seconds: float = 5.0
    # Large-manual ingestion controls. The character budget is a conservative
    # proxy for tokens (roughly 4 chars/token for ordinary technical prose).
    # 80k characters ~= 20k input tokens; at 10 RPM plus up to 4k output tokens
    # per call this targets <=240k tokens/min, leaving a little headroom under
    # a 250k TPM quota. Actual tokenization varies, so reduce RPM/budget if 429s
    # or TPM throttling occur. Prompt budget includes IDs and separators.
    vector_index_batch_size: int = 64
    extraction_batch_size: int = 18
    extraction_batch_max_chars: int = 80000

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
