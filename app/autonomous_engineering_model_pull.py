"""Background model downloads through the configured loopback-only Ollama API."""

from __future__ import annotations

import json
import re
import threading
import time
import urllib.error
import urllib.request
import uuid
from typing import Any

from .ollama import _base_url


_MODEL_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,119}$")
_MAX_PROGRESS_LINE_BYTES = 1024 * 1024
_LOCK = threading.Lock()
_JOBS: dict[str, dict[str, Any]] = {}
_ACTIVE_JOB: str | None = None
_MAX_RETAINED_JOBS = 20


class _NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _public_state(state: dict[str, Any]) -> dict[str, Any]:
    return {
        "job_id": state["job_id"],
        "model": state["model"],
        "status": state["status"],
        "progress": state["progress"],
        "message": state["message"],
        "error": state["error"],
        "started_at": state["started_at"],
        "updated_at": state["updated_at"],
    }


def _update(job_id: str, **values: Any) -> None:
    global _ACTIVE_JOB
    with _LOCK:
        state = _JOBS.get(job_id)
        if not state:
            return
        state.update(values)
        state["updated_at"] = time.time()
        if state["status"] in {"completed", "failed"} and _ACTIVE_JOB == job_id:
            _ACTIVE_JOB = None


def _run_pull(job_id: str, model: str) -> None:
    _update(job_id, status="running", message="اتصال به Ollama و دریافت مدل آغاز شد.")
    try:
        request = urllib.request.Request(
            _base_url() + "/api/pull",
            data=json.dumps({"model": model, "stream": True}).encode("utf-8"),
            headers={"Accept": "application/x-ndjson", "Content-Type": "application/json"},
            method="POST",
        )
        opener = urllib.request.build_opener(
            urllib.request.ProxyHandler({}),
            _NoRedirectHandler(),
        )
        saw_success = False
        with opener.open(request, timeout=60) as response:
            while True:
                line = response.readline(_MAX_PROGRESS_LINE_BYTES + 1)
                if not line:
                    break
                if len(line) > _MAX_PROGRESS_LINE_BYTES:
                    raise ValueError("Ollama progress record exceeded the size limit.")
                try:
                    payload = json.loads(line.decode("utf-8"))
                except (UnicodeDecodeError, ValueError) as exc:
                    raise ValueError("Ollama returned an invalid model-download progress record.") from exc
                if not isinstance(payload, dict):
                    raise ValueError("Ollama returned an invalid model-download progress record.")
                if payload.get("error"):
                    raise RuntimeError("Ollama rejected the model download request.")
                completed = payload.get("completed")
                total = payload.get("total")
                percent = None
                if (
                    isinstance(completed, int) and not isinstance(completed, bool) and completed >= 0
                    and isinstance(total, int) and not isinstance(total, bool) and total > 0
                ):
                    percent = max(0, min(100, int(completed * 100 / total)))
                status = str(payload.get("status") or "در حال دریافت")
                if status.casefold() == "success":
                    saw_success = True
                    break
                _update(
                    job_id,
                    progress=percent,
                    message=status[:160],
                )
        if not saw_success:
            raise RuntimeError("Ollama closed the download stream before confirming success.")
        verify_request = urllib.request.Request(
            _base_url() + "/api/tags",
            headers={"Accept": "application/json"},
            method="GET",
        )
        with opener.open(verify_request, timeout=10) as response:
            body = response.read(8_000_001)
        if len(body) > 8_000_000:
            raise ValueError("Ollama model inventory exceeded the safety limit.")
        inventory = json.loads(body.decode("utf-8"))
        models = inventory.get("models", []) if isinstance(inventory, dict) else []
        installed_names = {
            str(item.get("name"))
            for item in models
            if isinstance(item, dict) and item.get("name")
        } if isinstance(models, list) else set()
        expected_names = {model, model + ":latest"} if ":" not in model else {model}
        if not expected_names.intersection(installed_names):
            raise RuntimeError("Ollama reported success but the selected model is not present in its local inventory.")
        _update(job_id, status="completed", progress=100, message="دانلود، نصب و حضور مدل در Ollama تأیید شد.", error=None)
    except urllib.error.HTTPError as exc:
        _update(
            job_id,
            status="failed",
            message="Ollama درخواست دانلود مدل را نپذیرفت.",
            error=f"HTTP {exc.code}",
        )
    except (OSError, TimeoutError, urllib.error.URLError, ValueError, RuntimeError) as exc:
        _update(
            job_id,
            status="failed",
            message="دانلود مدل کامل نشد.",
            error=(str(exc) if isinstance(exc, RuntimeError) and str(exc).startswith("Ollama ") else type(exc).__name__)[:240],
        )
    except Exception as exc:  # Defensive boundary for the background worker.
        _update(job_id, status="failed", message="دانلود مدل کامل نشد.", error=type(exc).__name__)


def start_model_pull(model: str) -> dict[str, Any]:
    global _ACTIVE_JOB
    selected = model.strip() if isinstance(model, str) else ""
    if not _MODEL_PATTERN.fullmatch(selected) or selected.endswith(("/", ":")):
        raise ValueError("شناسهٔ مدل Ollama معتبر نیست.")
    with _LOCK:
        if _ACTIVE_JOB and _JOBS.get(_ACTIVE_JOB, {}).get("status") in {"queued", "running"}:
            raise RuntimeError("یک دانلود مدل Ollama در حال انجام است.")
        job_id = uuid.uuid4().hex
        now = time.time()
        state = {
            "job_id": job_id,
            "model": selected,
            "status": "queued",
            "progress": 0,
            "message": "دانلود در صف شروع قرار گرفت.",
            "error": None,
            "started_at": now,
            "updated_at": now,
        }
        _JOBS[job_id] = state
        _ACTIVE_JOB = job_id
        for old_id in list(_JOBS)[:-_MAX_RETAINED_JOBS]:
            if old_id != _ACTIVE_JOB:
                _JOBS.pop(old_id, None)
    worker = threading.Thread(target=_run_pull, args=(job_id, selected), name="ollama-model-pull", daemon=True)
    worker.start()
    return _public_state(state)


def get_model_pull(job_id: str) -> dict[str, Any] | None:
    if not isinstance(job_id, str) or not re.fullmatch(r"[0-9a-f]{32}", job_id):
        return None
    with _LOCK:
        state = _JOBS.get(job_id)
        return _public_state(state) if state else None
