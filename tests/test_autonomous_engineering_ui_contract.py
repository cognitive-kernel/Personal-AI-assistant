from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_broker_readiness_guidance_does_not_require_a_runner():
    source = (
        ROOT / "app" / "static" / "autonomous-engineering-status.js"
    ).read_text(encoding="utf-8")

    assert "نصب Broker به Runner وابسته نیست" in source
    assert "Runner فقط برای اجرای Workflow مهندسی لازم است" in source
    assert "یا نبود Runner" not in source
    assert "PowerShell سازگار (نسخهٔ 5.1 یا 7)" in source
