"""Runtime configuration, read from environment variables (or a .env file)."""

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # LLM layer (Ollama)
    ollama_host: str = "http://localhost:11434"
    ollama_model: str = "qwen3:8b"  # chosen by eval/evaluate.py, see README
    ollama_timeout: float = 120.0
    llm_chunk_size: int = 1800  # characters per LLM call
    llm_chunk_overlap: int = 200

    # Limits
    max_upload_mb: int = 10
    max_pages: int = 50
    max_text_chars: int = 200_000

    # Storage limitation (GDPR Art. 5(1)(e)): results are purged after this many seconds
    job_ttl_seconds: int = 900

    log_level: str = "INFO"


@lru_cache
def get_settings() -> Settings:
    return Settings()
