from __future__ import annotations
import os
from dataclasses import dataclass
from dotenv import load_dotenv

load_dotenv()

@dataclass
class Defaults:
    ollama_base_url: str = os.getenv("OLLAMA_BASE_URL", "http://127.0.0.1:11434")
    ollama_model: str = os.getenv("OLLAMA_MODEL", "qwen2.5:7b")
    ollama_timeout_seconds: int = int(os.getenv("OLLAMA_TIMEOUT_SECONDS", "300"))
    ollama_num_ctx: int = int(os.getenv("OLLAMA_NUM_CTX", "2048"))
    ollama_num_thread: int = int(os.getenv("OLLAMA_NUM_THREAD", "0"))
    settings_db_path: str = os.getenv("SETTINGS_DB_PATH", "data/settings.db")
    learning_db_path: str = os.getenv("LEARNING_DB_PATH", "data/learning.db")
    host: str = os.getenv("HOST", "127.0.0.1")
    port: int = int(os.getenv("PORT", "8000"))

defaults = Defaults()
SETTING_DEFAULTS: dict[str, object] = {
    "ollama_base_url": defaults.ollama_base_url,
    "ollama_model": defaults.ollama_model,
    "ollama_timeout_seconds": defaults.ollama_timeout_seconds,
    "ollama_num_ctx": defaults.ollama_num_ctx,
    "ollama_num_thread": defaults.ollama_num_thread,
    "learning_max_concurrent_jobs": 1,
    "learning_source_max_chars": 12000,
}
