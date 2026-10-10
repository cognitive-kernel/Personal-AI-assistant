from __future__ import annotations
import re
from typing import Any

def evaluate_learning_value(lesson: str, questions: list[Any], answer_key: list[Any]) -> dict[str, Any]:
    """A deterministic first-pass filter, not an independent factual review."""
    text = lesson.strip()
    normalized = re.sub(r"\s+", " ", text.lower())
    words = re.findall(r"[^\W_]+", normalized, flags=re.UNICODE)
    unique_ratio = len(set(words)) / max(1, len(words))
    signals = [
        "version target", "source unavailability", "network restrictions",
        "403 forbidden", "local/private/reserved network targets",
        "recommendations for future actions", "failed to fetch", "client error",
    ]
    matched = [signal for signal in signals if signal in normalized]
    reasons: list[str] = []
    score = 100
    if len(words) < 80:
        score -= 25
        reasons.append("محتوا برای یک درس پایدار بسیار کوتاه است.")
    if unique_ratio < 0.48:
        score -= 25
        reasons.append("تکرار متن زیاد است و اطلاعات تازه کمی دارد.")
    if len(questions) < 3 or len(answer_key) < 3:
        score -= 20
        reasons.append("سه سؤال و سه پاسخ ارزیابی کامل تولید نشده است.")
    if len(matched) >= 2:
        score -= 70
        reasons.append("محتوا بیشتر گزارش خطا/دسترسی منبع است تا آموزش موضوعی.")
    elif matched:
        score -= 20
        reasons.append("نشانه‌هایی از گزارش خطا یا وضعیت اجرایی در متن وجود دارد.")
    if not text:
        score = 0
        reasons.append("محتوای درس خالی است.")
    score = max(0, min(100, score))
    accepted = (
        score >= 65 and len(matched) < 2 and len(words) >= 80
        and unique_ratio >= 0.48 and len(questions) >= 3 and len(answer_key) >= 3
    )
    return {
        "score": score,
        "accepted": accepted,
        "word_count": len(words),
        "unique_word_ratio": round(unique_ratio, 3),
        "matched_warning_signals": matched,
        "reasons": reasons or ["از فیلتر اولیه عبور کرد؛ این نتیجه تأیید علمی مستقل نیست."],
    }
