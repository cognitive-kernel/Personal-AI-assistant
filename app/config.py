from __future__ import annotations
import os
from dataclasses import dataclass
from dotenv import load_dotenv

load_dotenv()


def _int_env(name: str, default: int) -> int:
    """Parse integer settings without echoing arbitrary raw values."""
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        return int(raw)
    except ValueError:
        raise ValueError(f"Environment variable {name} must be an integer.") from None


@dataclass
class Defaults:
    ollama_base_url: str = os.getenv("OLLAMA_BASE_URL", "http://127.0.0.1:11434")
    ollama_model: str = os.getenv("OLLAMA_MODEL", "qwen2.5:7b")
    ollama_embedding_model: str = os.getenv("OLLAMA_EMBEDDING_MODEL", "nomic-embed-text")
    ollama_timeout_seconds: int = _int_env("OLLAMA_TIMEOUT_SECONDS", 300)
    ollama_num_ctx: int = _int_env("OLLAMA_NUM_CTX", 2048)
    ollama_num_thread: int = _int_env("OLLAMA_NUM_THREAD", 0)
    chat_ollama_model: str = os.getenv("CHAT_OLLAMA_MODEL", os.getenv("OLLAMA_MODEL", "qwen2.5:7b"))
    learning_ollama_model: str = os.getenv("LEARNING_OLLAMA_MODEL", "qwen3:8b")
    programming_ollama_model: str = os.getenv("PROGRAMMING_OLLAMA_MODEL", "qwen3:14b")
    factual_review_ollama_model: str = os.getenv("FACTUAL_REVIEW_OLLAMA_MODEL", "")
    settings_db_path: str = os.getenv("SETTINGS_DB_PATH", "data/settings.db")
    learning_db_path: str = os.getenv("LEARNING_DB_PATH", "data/learning.db")
    cloud_resources_db_path: str = os.getenv("CLOUD_RESOURCES_DB_PATH", "data/cloud_resources.db")
    database_backup_dir: str = os.getenv("DATABASE_BACKUP_DIR", "data/backups")
    database_backup_interval_hours: int = _int_env("DATABASE_BACKUP_INTERVAL_HOURS", 0)
    host: str = os.getenv("HOST", "127.0.0.1")
    port: int = _int_env("PORT", 8000)

    def __post_init__(self) -> None:
        interval = self.database_backup_interval_hours
        if isinstance(interval, bool) or not isinstance(interval, int) or not 0 <= interval <= 168:
            raise ValueError(
                "DATABASE_BACKUP_INTERVAL_HOURS must be 0 (disabled) or between 1 and 168."
            )

defaults = Defaults()
SETTING_DEFAULTS: dict[str, object] = {
    "ollama_base_url": defaults.ollama_base_url,
    "ollama_model": defaults.ollama_model,
    "ollama_embedding_model": defaults.ollama_embedding_model,
    "ollama_timeout_seconds": defaults.ollama_timeout_seconds,
    "ollama_num_ctx": defaults.ollama_num_ctx,
    "ollama_num_thread": defaults.ollama_num_thread,
    "cpu_resource_limit_percent": 90,
    "ram_resource_limit_percent": 90,
    "gpu_resource_limit_percent": 90,
    "gpu_enabled": False,
    "ram_budget_max_gb": 25,
    "ram_safety_reserve_gb": 4,
    "chat_ollama_model": defaults.chat_ollama_model,
    "chat_ollama_num_ctx": defaults.ollama_num_ctx,
    "chat_ollama_num_thread": defaults.ollama_num_thread,
    "chat_ollama_timeout_seconds": defaults.ollama_timeout_seconds,
    "chat_max_concurrent_jobs": 1,
    "chat_model_keep_alive": "auto",
    "chat_source_max_chars": 12000,
    "learning_ollama_model": defaults.learning_ollama_model,
    "learning_ollama_num_ctx": defaults.ollama_num_ctx,
    "learning_ollama_num_thread": defaults.ollama_num_thread,
    "learning_ollama_timeout_seconds": defaults.ollama_timeout_seconds,
    "learning_max_concurrent_jobs": 1,
    "learning_model_keep_alive": "0",
    "learning_source_max_chars": 12000,
    "programming_ollama_model": defaults.programming_ollama_model,
    "factual_review_ollama_model": defaults.factual_review_ollama_model,
    "programming_ollama_num_ctx": defaults.ollama_num_ctx,
    "programming_ollama_num_thread": defaults.ollama_num_thread,
    "programming_ollama_timeout_seconds": defaults.ollama_timeout_seconds,
    "programming_max_concurrent_jobs": 1,
    "programming_model_keep_alive": "0",
    "programming_source_max_chars": 12000,
}
