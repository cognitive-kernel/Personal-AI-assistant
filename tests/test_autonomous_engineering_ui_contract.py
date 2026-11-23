from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_broker_setup_is_explicit_and_does_not_require_a_runner_or_fixed_shell_version():
    static = ROOT / "app" / "static"
    status = (static / "autonomous-engineering-status.js").read_text(encoding="utf-8")
    controls = (static / "autonomous-engineering-controls.js").read_text(encoding="utf-8")
    page = (static / "autonomous-engineering.html").read_text(encoding="utf-8")

    assert 'id="setup-state-broker"' in page
    assert 'request("broker/setup"' in controls
    assert "timeoutMs: 300000" in controls
    assert "فقط پس از فشردن دکمهٔ زیر" in status
    assert "نبود Runner مانع نصب Broker نیست" in status
    assert "Runner فقط برای اجرای Workflow مهندسی لازم است" in status
    assert "در صورت ردشدن UAC یا نبود Runner" not in status
    assert "powershell.exe" not in status
    assert "برنامه هنگام شروع برای نصب یا ترمیم" not in status
