from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="REPRO_RUNNER_", extra="ignore")

    max_upload_mb: int = 100
    max_columns: int = 256
    default_target_column: str = "Y_cls"
    storage_dir: Path = Path("/data/experiments")
    max_concurrent_experiments: int = Field(default=1, ge=1)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
