from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="PAPER_DOSSIER_EXTRACTOR_", extra="ignore"
    )

    api_token: str = Field(min_length=32)
    ollama_base_url: str = "http://ollama:11434"
    ollama_model: str = "qwen3:8b"
    ollama_keep_alive: str = "1s"
    num_ctx: int = 16_384
    num_predict: int = 1_536
    max_chunk_source_bytes: int = 8_192
    max_pages_per_chunk: int = 4
    max_candidate_pages: int = 32
    max_ollama_calls: int = 12
    request_timeout_seconds: int = 1_200
    ollama_call_timeout_seconds: int = 360
    max_concurrent_extractions: int = 1


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
