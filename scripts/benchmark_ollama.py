from __future__ import annotations

import argparse
import json
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app import ollama

_PROMPTS = [
    "Explain Python JSONDecodeError and show one safe exception-handling example.",
    "Explain how SQLite FTS5 differs from vector search and when each helps.",
    "یک توضیح فنی کوتاه درباره اعتبارسنجی ورودی در FastAPI بنویس و یک مثال ساده ارائه کن.",
    "A user asks for a source-backed answer. List the checks needed before calling a claim verified.",
]


def run_benchmark(models: list[str], *, num_ctx: int, num_thread: int) -> dict[str, Any]:
    records: list[dict[str, Any]] = []
    for model in models:
        for index, prompt in enumerate(_PROMPTS, start=1):
            payload = {
                "model": model,
                "stream": False,
                "messages": [{"role": "user", "content": prompt}],
                "options": {"num_ctx": num_ctx, "num_thread": num_thread},
            }
            started = time.perf_counter()
            try:
                response = ollama._request("/api/chat", payload)
                elapsed = time.perf_counter() - started
                message = response.get("message", {})
                text = message.get("content", "") if isinstance(message, dict) else ""
                eval_count = response.get("eval_count")
                eval_duration = response.get("eval_duration")
                records.append({
                    "model": model, "prompt_id": index, "prompt": prompt,
                    "ok": isinstance(text, str) and bool(text.strip()),
                    "elapsed_seconds": round(elapsed, 3),
                    "output_characters": len(text) if isinstance(text, str) else 0,
                    "eval_count": eval_count,
                    "tokens_per_second": round(eval_count / (eval_duration / 1_000_000_000), 2)
                        if isinstance(eval_count, (int, float)) and isinstance(eval_duration, (int, float)) and eval_duration > 0 else None,
                    "response": text,
                })
            except Exception as exc:
                records.append({"model": model, "prompt_id": index, "prompt": prompt,
                                "ok": False, "elapsed_seconds": round(time.perf_counter() - started, 3),
                                "error": f"{type(exc).__name__}: {exc}"[:500]})
    return {"created_at": datetime.now(timezone.utc).isoformat(), "num_ctx": num_ctx,
            "num_thread": num_thread, "note": "Latency/token metrics are measured; semantic quality and RAM must be reviewed separately.",
            "records": records}


def main() -> int:
    parser = argparse.ArgumentParser(description="Run a repeatable local Ollama model comparison.")
    parser.add_argument("--models", default="qwen2.5:7b,qwen3:8b,qwen3:14b", help="Comma-separated installed or candidate model names")
    parser.add_argument("--num-ctx", type=int, default=8192)
    parser.add_argument("--num-thread", type=int, default=10)
    parser.add_argument("--output", default="benchmark-results.json")
    args = parser.parse_args()
    if not 512 <= args.num_ctx <= 8192:
        parser.error("--num-ctx must be between 512 and 8192")
    if not 0 <= args.num_thread <= 64:
        parser.error("--num-thread must be between 0 and 64")
    models = [item.strip() for item in args.models.split(",") if item.strip()]
    if not models:
        parser.error("Provide at least one model name")
    report = run_benchmark(models, num_ctx=args.num_ctx, num_thread=args.num_thread)
    output = Path(args.output)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Wrote {len(report['records'])} benchmark records to {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
