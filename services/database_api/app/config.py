from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=Path(__file__).resolve().parents[3] / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    db_host: str = "127.0.0.1"
    db_port: int = 5432
    db_name: str = "waimea_wavecast"
    db_user: str = "waimea_web"
    db_password: str = ""
    db_sslmode: str = "require"
    db_pool_max: int = 5
    internal_api_token: str = ""
    admin_api_token: str = ""
    app_env: str = "development"
    public_origin: str = "http://localhost:5173"
    allowed_origins: str = ""
    ingestion_worker_url: str = "http://ingestion:8000"
    analysis_worker_url: str = "http://analysis:8000"
    clustering_worker_url: str = "http://clustering:8000"
    prediction_worker_url: str = "http://prediction:8000"
    classification_worker_url: str = "http://classification:8000"
    max_batch_size: int = 500
    max_query_days: int = 366
    freshness_stale_after_minutes: int = 120
    demo_max_days: int = 30
    demo_delete_enabled: bool = False
    demo_backup_confirmed: bool = False


@lru_cache
def get_settings() -> Settings:
    return Settings()
