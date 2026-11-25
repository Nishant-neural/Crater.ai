"""Central settings, loaded from environment / .env."""
from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    anthropic_api_key: str = ""
    anthropic_model: str = "claude-sonnet-4-6"
    # Accept Google's standard key name as a fallback: a key stored only
    # under GOOGLE_API_KEY used to be silently ignored (every Gemini call
    # then ran with an empty key and returned None). GEMINI_API_KEY wins
    # when both are set. Verified with tests in test_ingestion_gemini_fix.py.
    gemini_api_key: str = Field(
        default="",
        validation_alias=AliasChoices("GEMINI_API_KEY", "GOOGLE_API_KEY"),
    )
    # NOTE (21 Nov 2026): this id could not be verified live against
    # Google's model list in this build (no live probe run here). If the
    # API 404s on it, the error names the model; llm-probe.py on the
    # user's laptop remains the decider.
    gemini_model: str = "gemini-2.5-flash-lite"
    llm_provider: str = "anthropic"
    llm_model: str = ""

    # OpenRouter fallback (fix5, 21 Nov 2026): OpenAI-compatible endpoint
    # used when Gemini's free tier 503s. Free key from openrouter.ai.
    # Default model verified live 21 Nov 2026 via GET
    # https://openrouter.ai/api/v1/models: the suggested
    # meta-llama/llama-3.3-70b-instruct:free is DELISTED (only the paid
    # variant remains, $0.71/1M tokens, so it must not be the default).
    # Default is google/gemma-4-31b-it:free — the largest well-known
    # instruction model among the 11 live :free models with
    # response_format support: context 262144, max_completion_tokens
    # 32768, pricing $0. The 1M-context :free models (Ling, Ernie
    # preview, Particle, Nemotron) are preview/obscure, and the Nemotron
    # free variants do not support response_format at all.
    openrouter_api_key: str = ""
    openrouter_base_url: str = "https://openrouter.ai/api/v1"
    openrouter_model: str = "google/gemma-4-31b-it:free"

    # Task-specific routing. Environment variables can override the individual
    # model names without changing application code.
    extraction_model: str = ""
    integration_model: str = ""
    diagnosis_model: str = ""
    extraction_provider: str = ""
    integration_provider: str = ""
    # fix5 (21 Nov 2026, user decision): the benchmark's live path is
    # diagnosis only, and Gemini's free tier 503s there, so
    # diagnosis defaults to the OpenRouter free model. Extraction,
    # integration, vision and expert stay on Gemini (blank = fall back
    # to llm_provider). Override per task, e.g. DIAGNOSIS_PROVIDER=gemini.
    diagnosis_provider: str = "openrouter"
    vision_provider: str = ""
    vision_model: str = ""
    expert_provider: str = ""
    expert_model: str = ""


    database_url: str = "sqlite:///./crater.db"

    qdrant_url: str = "http://localhost:6333"
    qdrant_collection: str = "crater_chunks"

    embedding_model: str = "sentence-transformers/all-MiniLM-L6-v2"

    # CrossEncoder reranker (Phase 1 Step 2; amendment resolution 1):
    # local model, same dependency family as embeddings, no LLM call.
    # The reranker scores the WHOLE candidate text -- no [:1000] cut.
    reranker_model: str = "cross-encoder/ms-marco-MiniLM-L-6-v2"

    tesseract_cmd: str = "tesseract"

    # --- Gateway shim (gateway audit §2/§7; contracts README §1.5, §2.4) ---
    # CORS origins come from environment per deployment; nothing is
    # compiled into code (audit §4). Empty means no CORS middleware.
    # See .env.example for the local-dev origin list.
    cors_origins: str = ""
    # Learning routes stay mounted but gated: 403 learning_deferred
    # until the pilot produces >=20 field outcomes (contracts §2.4).
    learning_enabled: bool = False
    # API-key auth (forwardAuth target). Off by default for local dev;
    # the pilot enables it and Traefik enforces it before any service.
    gateway_auth_enabled: bool = False
    # Bootstrap keys (comma-separated) used only until the api_keys
    # Postgres table lands with the Postgres move (contracts §8.2).
    gateway_api_keys: str = ""
    # Redis for the gateway token bucket (contracts §1.5). Empty means
    # the shim falls back to a process-local bucket (single replica)
    # and logs a warning; Redis holds no durable state.
    redis_url: str = ""

    # --- Celery + Redis job queue (contracts README §8.1; amendment
    # resolution 2: Celery + Redis everywhere, no Redis Streams) ---
    # Broker is Redis db 0, result backend db 1. Blank means derive
    # from redis_url (broker) and the broker URL with db 1 (backend);
    # with neither set the local defaults below apply.
    celery_broker_url: str = ""
    celery_result_backend: str = ""

    # --- Service URLs (Phase 3 split; contracts README §1.1/§3.3) ---
    # Diagnosis-agent reaches ingestion over HTTP, never by import or
    # cross-service DB read. In compose these point at the service
    # names (http://ingestion:8000); the local monolith dev default
    # is the same process on :8000.
    ingestion_base_url: str = "http://localhost:8000"
    # Ingestion reaches diagnosis-agent for Mode B diagnose over HTTP
    # (contracts §2.2: Mode B diagnose is diagnosis-owned).
    diagnosis_base_url: str = "http://localhost:8000"
    # Twin-verifier base URL. The diagnosis loop reaches the
    # twin-verifier service over HTTP (POST /v1/verify -> 202 + job,
    # poll); in compose this is the service name
    # (http://twin-verifier:8000), the local monolith dev default is
    # the same process on :8000. Empty means no twin verifier is
    # configured: the thin client degrades every call to NOT_VERIFIED
    # with the reason recorded (never a silent pass, never a
    # conclusion on that basis).
    twin_verifier_base_url: str = "http://localhost:8000"

    # Chunking
    # Technical manuals benefit from larger semantic context. Keep enough text
    # together for the extractor to see component descriptions, specifications,
    # procedures, and their surrounding context instead of isolated fragments.
    chunk_size_chars: int = 12000
    chunk_overlap_chars: int = 1000
    chunk_min_chars: int = 2500
    chunk_max_chars: int = 15000

    # Retrieval chunking (Step 1, P-02): the extraction numbers above
    # serve one job (large context for the extractor) and must not be
    # reused for retrieval units. Tables are atomic retrieval units --
    # one table = one chunk -- split into row groups only when a
    # single table exceeds this cap (header repeated per group, never
    # a split row). ~4,096 chars ~= 1,024 tokens of technical prose.
    # Text chunking keeps the extraction numbers above until Step 2
    # builds the second (retrieval) stream; this setting governs
    # tables only in Step 1.
    retrieval_chunk_max_chars: int = 4096
    retrieval_chunk_target_chars: int = 2048

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

    # Retrieval (Phase 1 Step 3, audit p.8): 15 candidates per leg,
    # fused with RRF, reranked to a final top 5 of whole items for the
    # ~4,700-char prompt budget. (Was 40 / 8 before the budget work.)
    hybrid_top_k: int = 15          # candidates pulled from each of vector/BM25 before fusion
    final_top_k: int = 5            # results returned after fusion + rerank

    @property
    def model_routes(self) -> dict:
        return {
            "extraction": {"provider": self.extraction_provider, "model": self.extraction_model, "max_tokens": 4000},
            "integration": {"provider": self.integration_provider, "model": self.integration_model, "max_tokens": 12000},
            "diagnosis": {"provider": self.diagnosis_provider, "model": self.diagnosis_model, "max_tokens": 8192},
            "vision": {"provider": self.vision_provider, "model": self.vision_model, "max_tokens": 2000},
            "expert": {"provider": self.expert_provider, "model": self.expert_model, "max_tokens": 1200},
        }

    @property
    def task_max_tokens(self) -> dict:
        return {"extraction": 4000, "integration": 12000, "diagnosis": 8192, "vision": 2000, "expert": 1200}


settings = Settings()
