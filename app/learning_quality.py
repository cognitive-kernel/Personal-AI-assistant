from __future__ import annotations
import re
from typing import Any


def evaluate_learning_value(lesson: str, questions: list[Any], answer_key: list[Any]) -> dict[str, Any]:
    """Deterministic content-shape filter; never an independent factual review."""
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
    # These are conservative warning heuristics for obvious mixed-script or malformed output.
    mixed_script_tokens = re.findall(r"(?u)\b\w*[А-Яа-яЁё]+\w*\b", text)
    malformed_phrases = [
        phrase for phrase in (
            "معمایی سیستم مدیریتی",
            "داده‌های رеляتیوی",
            "داده های رеляتیوی",
            "منجر به بهبود بهره‌وری",
            "سطوح سطوحی",
            "سطح کاربری سه‌بعدی",
            "سطح کاربری سه بعدی",
            "ساختار و سطوح سطوحی",
            "به کار کاربران اجازه می‌دهد تا",
        ) if phrase in normalized
    ]
    # Flag recurring signs of vague, fabricated architecture descriptions.
    vague_architecture = bool(
        re.search(r"معماری.{0,30}(سطح داده|سطح سیستم|سطح کاربر)", normalized)
        and any(term in normalized for term in ("سطح داده", "سطح سیستم", "سطح کاربر"))
        and any(term in normalized for term in ("سه‌بعدی", "سه بعدی", "سطوح سطوحی"))
    )
    if vague_architecture and "معماری سطح‌بندی‌شده نامعتبر" not in malformed_phrases:
        malformed_phrases.append("معماری سطح‌بندی‌شده نامعتبر")
    valid_questions = (
        len(questions) == 3
        and all(isinstance(item, str) and len(item.strip()) >= 12 for item in questions)
    )
    valid_answers = (
        len(answer_key) == 3
        and all(isinstance(item, str) and len(item.strip()) >= 15 for item in answer_key)
    )

    score = 100
    reasons: list[str] = []
    if len(words) < 100:
        score -= 35
        reasons.append("درس کوتاه‌تر از حداقل آموزشی تعیین‌شده است.")
    elif len(words) < 120:
        score -= 10
        reasons.append("درس از بازه پیشنهادی ۱۲۰ تا ۱۸۰ واژه کوتاه‌تر است.")
    if unique_ratio < 0.48:
        score -= 20
        reasons.append("تکرار متن زیاد است و اطلاعات تازه کمی دارد.")
    if not valid_questions:
        score -= 15
        reasons.append("باید دقیقاً سه سؤال روشن و کامل تولید شود.")
    if not valid_answers:
        score -= 15
        reasons.append("باید برای هر سؤال یک پاسخ کلیدی کامل وجود داشته باشد.")
    if mixed_script_tokens:
        score -= 35
        reasons.append("واژه‌های دارای حروف سیریلیک در متن فارسی دیده شد؛ احتمال خطای زبانی وجود دارد.")
    if malformed_phrases:
        score -= min(40, 20 * len(malformed_phrases))
        reasons.append("عبارت‌های مشکوک یا نامأنوس در متن دیده شد.")
    if len(matched) >= 2:
        score -= 60
        reasons.append("محتوا بیشتر گزارش خطا/دسترسی منبع است تا آموزش موضوعی.")
    elif matched:
        score -= 20
        reasons.append("نشانه‌ای از گزارش خطا یا وضعیت اجرایی در متن وجود دارد.")
    if not text:
        score = 0
        reasons.append("محتوای درس خالی است.")

    score = max(0, min(100, score))
    accepted = (
        score >= 75 and len(words) >= 100 and unique_ratio >= 0.48
        and valid_questions and valid_answers and not mixed_script_tokens
        and not malformed_phrases and len(matched) < 2 and bool(text)
    )
    return {
        "score": score,
        "accepted": accepted,
        "word_count": len(words),
        "unique_word_ratio": round(unique_ratio, 3),
        "matched_warning_signals": matched,
        "mixed_script_tokens": mixed_script_tokens[:10],
        "malformed_phrases": malformed_phrases,
        "reasons": reasons or ["از فیلتر ساختاری عبور کرد؛ این نتیجه تأیید علمی مستقل نیست."],
    }
