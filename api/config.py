"""Typed settings loaded from `api/.env` (see docs/CLAUDE.md §4)."""

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

API_DIR = Path(__file__).resolve().parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=API_DIR / ".env", extra="ignore")

    youtube_api_key: str = ""
    groq_api_key_1: str = ""
    groq_api_key_2: str = ""
    llm_model_large: str = ""
    llm_model_small: str = ""
    db_url: str = "sqlite:///./coverly.db"
    max_topics: int = 40
    search_results_per_topic: int = 10
    segment_window_seconds: int = 240
    segment_text_max_chars: int = 1500
    tag_videos_per_topic: int = 5
    tag_batch_size: int = 8
    tag_confidence_min: float = 0.6
    cors_origin: str = "http://localhost:3000"

    @property
    def groq_api_keys(self) -> list[str]:
        """Non-empty Groq keys, in rotation order."""
        return [k for k in (self.groq_api_key_1, self.groq_api_key_2) if k]

    @property
    def db_path(self) -> Path:
        """Filesystem path from a `sqlite:///` URL; relative paths resolve against `api/`."""
        prefix = "sqlite:///"
        if not self.db_url.startswith(prefix):
            raise ValueError(f"DB_URL must start with {prefix!r} in v0")
        path = Path(self.db_url.removeprefix(prefix))
        return path if path.is_absolute() else API_DIR / path


@lru_cache
def get_settings() -> Settings:
    return Settings()
