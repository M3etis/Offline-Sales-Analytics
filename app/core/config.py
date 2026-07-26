from pydantic_settings import BaseSettings
from pathlib import Path
import os
import secrets

class Settings(BaseSettings):
    """Application settings loaded from .env file."""

    # Database
    DB_PATH: str = "data/processed/sales.duckdb"

    # Ollama LLM
    OLLAMA_URL: str = "http://127.0.0.1:11434"
    OLLAMA_MODEL: str = "qwen2.5:14b"

    # Voice - STT
    WHISPER_MODEL: str = "bzikst/faster-whisper-large-v3-russian"
    WHISPER_LANGUAGE: str = "ru"

    # Voice - TTS
    PIPER_MODEL: str = "ru_RU-irina-medium"

    # Security
    SECRET_KEY: str = ""  # loaded from env or .env by pydantic-settings
    ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 60 * 24  # 24 hours

    # CORS
    ALLOWED_ORIGINS: str = os.getenv("ALLOWED_ORIGINS", "http://localhost:5173,http://localhost:3000,http://127.0.0.1:5173,http://127.0.0.1:3000")
    
    # Paths
    LOG_DIR: str = "logs"
    DATA_DIR: str = "data"
    DEMO_FILE: str = "data/demo/demo_sales.csv"
    
    # Limits
    MAX_RESULT_ROWS: int = 1000
    MAX_UPLOAD_SIZE_MB: int = 50
    
    @property
    def project_root(self) -> Path:
        return Path(__file__).parent.parent.parent
    
    @property
    def db_full_path(self) -> str:
        p = Path(self.DB_PATH)
        return str(p) if p.is_absolute() else str(self.project_root / p)
    
    @property
    def demo_full_path(self) -> str:
        p = Path(self.DEMO_FILE)
        return str(p) if p.is_absolute() else str(self.project_root / p)
    
    @property
    def log_full_path(self) -> str:
        p = Path(self.LOG_DIR)
        return str(p) if p.is_absolute() else str(self.project_root / p)
    
    @property
    def data_full_path(self) -> str:
        p = Path(self.DATA_DIR)
        return str(p) if p.is_absolute() else str(self.project_root / p)
    
    model_config = {"env_file": ".env", "env_file_encoding": "utf-8"}

settings = Settings()

# Validate critical security settings
if not settings.SECRET_KEY or len(settings.SECRET_KEY) < 32:
    import logging
    logging.getLogger(__name__).warning(
        "SECRET_KEY not set or too short — generating ephemeral key. "
        "Tokens will not survive app restart. Set SECRET_KEY in .env for persistence."
    )
    settings.SECRET_KEY = secrets.token_hex(32)
