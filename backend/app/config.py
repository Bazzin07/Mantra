from functools import lru_cache
from typing import List

from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(".env", "backend/.env"),
        env_prefix="IKI_",
        extra="ignore",
        populate_by_name=True,
    )

    app_name: str = "Industrial Knowledge Intelligence API"
    environment: str = "development"
    repository_backend: str = Field(default="sqlite", pattern="^(memory|sqlite|postgres)$")
    database_url: str = "sqlite+aiosqlite:///./.local/industrial_intel.db"
    storage_backend: str = Field(default="local", pattern="^(local|s3)$")
    local_storage_path: str = "./.local/storage"
    s3_bucket: str = ""
    s3_prefix: str = "industrial-knowledge-intel"
    cors_origins: str = "http://localhost:3000,http://127.0.0.1:3000"
    require_api_key: bool = False
    api_key: str = ""
    api_key_header: str = "X-API-Key"
    rate_limit_per_minute: int = 0  # 0 disables; per client host, per API instance
    max_upload_bytes: int = 25 * 1024 * 1024
    auto_create_schema: bool = True
    chunk_size: int = 500
    chunk_overlap: int = 100
    embedding_backend: str = Field(default="hashing", pattern="^(hashing|sentence-transformers)$")
    embedding_model: str = "BAAI/bge-base-en-v1.5"
    embedding_dimension: int = 768
    llm_provider: str = Field(default="deterministic", pattern="^(deterministic|nvidia)$")
    nvidia_api_key: str = Field(default="", validation_alias=AliasChoices("NVIDIA_API_KEY", "IKI_NVIDIA_API_KEY"))
    nvidia_base_url: str = "https://integrate.api.nvidia.com/v1"
    # Answer/rerank models. Defaults are validated against the live NVIDIA
    # integrate endpoint (2026-07-09) for responsiveness: the large frontier
    # models (qwen3-next-80b, llama-3.3-70b) exist in the catalog but time out on
    # the shared endpoint, so the copilot silently fell back to deterministic
    # answers. These defaults use verified fast, capable models. Restore frontier
    # models here once a dedicated NIM/production deployment serves them.
    rerank_model: str = "nvidia/rerank-qa-mistral-4b"
    rerank_url: str = "https://ai.api.nvidia.com/v1/retrieval/nvidia/reranking"  # reranking lives on ai.api host, not the integrate base
    utility_model: str = "meta/llama-3.1-8b-instruct"
    default_answer_model: str = "deepseek-ai/deepseek-v4-flash"
    baseline_answer_model: str = "meta/llama-3.1-8b-instruct"
    escalation_model: str = "deepseek-ai/deepseek-v4-flash"
    long_context_model: str = "deepseek-ai/deepseek-v4-flash"
    safety_model: str = "nvidia/llama-3.1-nemoguard-8b-content-safety"
    prompt_version: str = "copilot-rag-v1"
    semantic_cache_threshold: float = 0.92
    redis_url: str = ""
    graph_max_nodes: int = 5000
    copilot_default_limit: int = 5
    rca_max_hops: int = 3
    rca_top_chains: int = 3
    rca_temporal_window_days: int = 90

    @property
    def cors_origin_list(self) -> List[str]:
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
