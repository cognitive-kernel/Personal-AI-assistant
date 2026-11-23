from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_broker_readiness_guidance_does_not_require_a_runner_or_fixed_shell_version():
    source = (ROOT / "app" / "static" / "autonomous-engineering-status.js").read_text(encoding="utf-8")

    assert "نبود Runner مانع نصب Broker نیست" in source
    assert "Runner فقط برای اجرای Workflow مهندسی لازم است" in source
    assert "در صورت ردشدن UAC یا نبود Runner" not in source
    assert "نسخهٔ 5.1 یا 7" not in source
    assert "به‌صورت خودکار شناسایی می‌کند" in source
