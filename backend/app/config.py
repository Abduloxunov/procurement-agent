"""Configuration loaded from .env.

Every secret lives in .env and nothing else reads os.environ directly --
if a value is needed somewhere, it gets a field here first.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BACKEND_DIR / "data"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=BACKEND_DIR / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # --- OpenRouter ---
    openrouter_api_key: str = ""
    openrouter_base_url: str = "https://openrouter.ai/api/v1"

    # Three roles, deliberately separate env vars so a single node can be
    # pointed at a stronger model without touching code (design doc S13).
    model_default: str = "google/gemini-2.5-flash-lite"
    model_reasoning: str = "google/gemini-2.5-flash-lite"
    model_vision: str = "google/gemini-2.5-flash-lite"

    # --- search ---
    serpapi_key: str = ""

    # --- email, RFQ fallback only ---
    smtp_host: str = "smtp.gmail.com"
    smtp_port: int = 587
    imap_host: str = "imap.gmail.com"
    imap_port: int = 993
    email_address: str = ""
    email_password: str = ""

    # --- landed cost, see design doc S07 ---
    destination_country: str = "UZ"
    vat_rate: float = 0.12
    default_duty_rate: float = 0.20
    base_currency: str = "USD"

    # --- local stores ---
    db_path: Path = DATA_DIR / "sourcing.db"
    qdrant_path: Path = DATA_DIR / "qdrant"
    datasheet_dir: Path = DATA_DIR / "datasheets"

    embedding_model: str = "BAAI/bge-small-en-v1.5"  # 384 dims, runs locally
    embedding_dim: int = 384
    collection_name: str = "datasheets"

    def ensure_dirs(self) -> None:
        for path in (DATA_DIR, self.qdrant_path, self.datasheet_dir):
            path.mkdir(parents=True, exist_ok=True)


@lru_cache
def get_settings() -> Settings:
    settings = Settings()
    settings.ensure_dirs()
    return settings
