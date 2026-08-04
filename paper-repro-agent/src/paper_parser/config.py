from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="PAPER_PARSER_",
        extra="ignore",
        populate_by_name=True,
    )

    parser_api_token: str = Field(
        min_length=32,
        validation_alias="PAPER_PARSER_API_TOKEN",
    )
    max_upload_mb: int = Field(default=50, ge=1, le=100)
    max_pages: int = Field(default=400, ge=1, le=1000)
    max_concurrent_jobs: int = Field(default=1, ge=1, le=1)
    work_dir: str = "/data/jobs"


@lru_cache
def get_settings() -> Settings:
    return Settings()
