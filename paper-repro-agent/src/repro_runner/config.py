from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


MAX_DOSSIER_BYTES = 5 * 1024 * 1024
MAX_METRIC_OVERRIDES_BYTES = 64 * 1024


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="REPRO_RUNNER_", extra="ignore")

    max_upload_mb: int = 100
    max_dossier_mb: int = Field(default=5, ge=1, le=20)
    max_metric_overrides_kb: int = Field(default=64, ge=1, le=256)
    max_columns: int = 256
    max_diagnostic_rows: int = Field(default=50000, ge=1)
    max_diagnostic_cardinality: int = Field(default=5000, ge=2)
    max_transformed_features: int = Field(default=2048, ge=1)
    default_target_column: str = "Y_cls"
    storage_dir: Path = Path("/data/experiments")
    job_store_path: Path = Path("/data/experiments/jobs.sqlite3")
    job_work_dir: Path = Path("/data/experiments/jobs")
    max_concurrent_experiments: int = Field(default=1, ge=1)
    protocol_secret: str = "local-only-fallback-not-for-production"
    protocol_draft_ttl_seconds: int = Field(default=900, ge=60, le=3600)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
