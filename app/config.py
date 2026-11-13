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
    learning_ollama_model: str = os.getenv("LEARNING_OLLAMA_MODEL", "gpt-oss:20b")
    programming_ollama_model: str = os.getenv("PROGRAMMING_OLLAMA_MODEL", "qwen3:14b")
    factual_review_ollama_model: str = os.getenv("FACTUAL_REVIEW_OLLAMA_MODEL", "")
    ai_learning_ollama_model: str = os.getenv("AI_LEARNING_OLLAMA_MODEL", os.getenv("CHAT_OLLAMA_MODEL", "qwen2.5:7b"))
    topic_learning_ollama_model: str = os.getenv("TOPIC_LEARNING_OLLAMA_MODEL", os.getenv("LEARNING_OLLAMA_MODEL", "gpt-oss:20b"))
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
    "gpu_enabled": True,
    "ai_learning_enabled": True,
    "ai_learning_knowledge_enabled": True,
    "basic_learning_enabled": True,
    "topic_learning_enabled": True,
    "experience_system_enabled": True,
    "ai_learning_retrieval_enabled": True,
    "ai_learning_browser_enabled": True,
    "ai_learning_lesson_batch_size": 20,
    "ai_learning_chunk_target_chars": 1800,
    "ai_learning_chunk_overlap_chars": 180,
    "ai_learning_embedding_batch_size": 64,
    "ai_learning_embedding_input_max_chars": 6000,
    "ai_learning_title_duplicate_threshold": 0.985,
    "ai_learning_content_duplicate_threshold": 0.90,
    "ai_learning_response_timeout_seconds": 1800,
    "ai_learning_max_lesson_continuations": 8,
    "ai_learning_stability_seconds": 20,
    "ai_learning_stop_stability_seconds": 45,
    "ai_learning_last_stop_stability_seconds": 25,
    "ai_learning_min_complete_lesson_words": 1200,
    "ai_learning_max_context_relaunches": 1,
    "ai_learning_browser_timeout_seconds": 300,
    "ai_learning_browser_poll_interval_ms": 1000,
    "ai_learning_browser_control_poll_interval_ms": 100,
    "ai_learning_browser_lease_seconds": 90,
    "ai_learning_browser_lease_heartbeat_seconds": 15,
    "ai_learning_chrome_path": "",
    "ai_learning_browser_profile": "data/ai_learning_browser_profile",
    "ai_learning_max_retries": 2,
    "ai_learning_llm_provider": "ollama",
    "ai_learning_llm_base_url": "https://api.openai.com/v1",
    "ai_learning_llm_api_key_env": "",
    "ai_learning_ollama_model": defaults.ai_learning_ollama_model,
    "ai_learning_ollama_num_ctx": defaults.ollama_num_ctx,
    "ai_learning_ollama_num_thread": defaults.ollama_num_thread,
    "ai_learning_ollama_timeout_seconds": defaults.ollama_timeout_seconds,
    "ai_learning_model_keep_alive": "0",
    "ai_learning_source_max_chars": 100000,
    "topic_learning_llm_provider": "ollama",
    "topic_learning_llm_base_url": "https://api.openai.com/v1",
    "topic_learning_llm_api_key_env": "",
    "topic_learning_ollama_model": defaults.topic_learning_ollama_model,
    "topic_learning_ollama_num_ctx": defaults.ollama_num_ctx,
    "topic_learning_ollama_num_thread": defaults.ollama_num_thread,
    "topic_learning_ollama_timeout_seconds": defaults.ollama_timeout_seconds,
    "topic_learning_model_keep_alive": "0",
    "topic_learning_source_max_chars": 100000,
    "sandbox_enabled": True,
    "self_improvement_enabled": True,
    "sandbox_docker_executable": "",
    "sandbox_python_image": "",
    "sandbox_test_image": "",
    "sandbox_node_image": "",
    "sandbox_rust_image": "",
    "sandbox_timeout_seconds": 60,
    "sandbox_test_timeout_seconds": 300,
    "sandbox_max_output_bytes": 32768,
    "self_improvement_repo_root": "",
    "self_improvement_github_repository": "",
    "self_improvement_github_base_branch": "main",
    "self_improvement_github_token_env": "SELF_IMPROVEMENT_GITHUB_TOKEN",
    "ram_budget_max_gb": 25,
    "ram_safety_reserve_gb": 4,
    "chat_llm_provider": "ollama",
    "chat_llm_base_url": "https://api.openai.com/v1",
    "chat_llm_api_key_env": "",
    "research_plan_llm_provider": "ollama",
    "research_plan_llm_base_url": "https://api.openai.com/v1",
    "research_plan_llm_api_key_env": "",
    "learning_llm_provider": "ollama",
    "learning_llm_base_url": "https://api.openai.com/v1",
    "learning_llm_api_key_env": "",
    "programming_llm_provider": "ollama",
    "programming_llm_base_url": "https://api.openai.com/v1",
    "programming_llm_api_key_env": "",
    "factual_review_llm_provider": "ollama",
    "factual_review_llm_base_url": "https://api.openai.com/v1",
    "factual_review_llm_api_key_env": "",
    "factual_review_ollama_num_ctx": 4096,
    "factual_review_ollama_num_thread": defaults.ollama_num_thread,
    "factual_review_ollama_timeout_seconds": 120,
    "factual_review_model_keep_alive": "0",
    "factual_review_max_output_tokens": 500,
    "embedding_llm_provider": "ollama",
    "embedding_llm_base_url": "https://api.openai.com/v1",
    "embedding_llm_api_key_env": "",
    "chat_ollama_model": defaults.chat_ollama_model,
    "chat_ollama_num_ctx": defaults.ollama_num_ctx,
    "chat_ollama_num_thread": defaults.ollama_num_thread,
    "chat_ollama_timeout_seconds": defaults.ollama_timeout_seconds,
    "chat_max_concurrent_jobs": 1,
    "chat_model_keep_alive": "auto",
    "research_plan_ollama_model": defaults.chat_ollama_model,
    "research_plan_ollama_num_ctx": defaults.ollama_num_ctx,
    "research_plan_ollama_num_thread": defaults.ollama_num_thread,
    "research_plan_ollama_timeout_seconds": defaults.ollama_timeout_seconds,
    "research_plan_model_keep_alive": "0",
    "research_plan_max_output_tokens": 700,
    "learning_external_review_providers": [],
    "chat_source_max_chars": 12000,
    "learning_ollama_model": defaults.learning_ollama_model,
    "learning_ollama_num_ctx": defaults.ollama_num_ctx,
    "learning_ollama_num_thread": defaults.ollama_num_thread,
    "learning_ollama_timeout_seconds": defaults.ollama_timeout_seconds,
    "learning_max_concurrent_jobs": 1,
    "learning_model_keep_alive": "0",
    "learning_model_cost_profiles": {},
    "learning_model_minimum_quality_score": 0.0,
    "learning_task_budget_microusd": None,
    "learning_model_pricing_json": "",
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
