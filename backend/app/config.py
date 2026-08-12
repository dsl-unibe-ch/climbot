from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        # Check parent dir first (local dev: cd backend && ...), then cwd
        env_file=("../.env", ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Microsoft Entra ID
    azure_tenant_id: str
    azure_client_id: str
    azure_client_secret: str = ""

    # OpenAI — or any OpenAI-compatible endpoint (e.g. GPUStack)
    openai_base_url: str = ""  # empty → use OpenAI directly
    openai_api_key: str
    openai_model: str = "gpt-4o"
    # separate model for image description; defaults to openai_model if not set
    openai_vision_model: str = ""
    openai_embedding_model: str = "qwen3-embedding-0.6b"
    # Must match the output dimension of the embedding model above
    embedding_dim: int = 1024
    llm_temperature: float = 0.3
    llm_max_tokens: int = 16000
    llm_top_p: float = 1.0

    # Qdrant
    qdrant_host: str = "qdrant"
    qdrant_port: int = 6333
    qdrant_api_key: str = ""
    qdrant_collection_text: str = "climate_docs"
    qdrant_collection_images: str = "climate_images"

    # Backend
    backend_host: str = "0.0.0.0"
    backend_port: int = 8000
    backend_cors_origins: str = "http://localhost:8501"

    # Data
    data_dir: str = "/app/data"
    chunk_size: int = 1000
    chunk_overlap: int = 200


@lru_cache
def get_settings() -> Settings:
    return Settings()
