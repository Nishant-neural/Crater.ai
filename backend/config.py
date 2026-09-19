"""Central settings, loaded from environment / .env."""
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    anthropic_api_key: str = ""
    anthropic_model: str = "claude-sonnet-4-6"
    llm_provider: str = "anthropic"
    llm_model: str = ""

    database_url: str = "sqlite:///./crater.db"

    qdrant_url: str = "http://localhost:6333"
    qdrant_collection: str = "crater_chunks"

    embedding_model: str = "sentence-transformers/all-MiniLM-L6-v2"

    tesseract_cmd: str = "tesseract"

    # Chunking
    chunk_size_chars: int = 1800
    chunk_overlap_chars: int = 200

    # Retrieval
    hybrid_top_k: int = 40          # candidates pulled from each of vector/BM25 before fusion
    final_top_k: int = 8            # results returned after fusion + rerank


settings = Settings()
