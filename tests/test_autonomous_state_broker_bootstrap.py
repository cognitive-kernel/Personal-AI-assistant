from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_windows_state_broker_is_checked_and_repaired_before_app_startup():
    source = (ROOT / "run.py").read_text(encoding="utf-8")
    bootstrap = source[source.index("def _ensure_windows_state_broker"):source.index("def main()")]
    main = source[source.index("def main()"):]

    assert 'if platform.system() != "Windows":' in bootstrap
    assert "from app.autonomous_engineering_state_broker import health" in bootstrap
    assert 'ROOT / "scripts" / "ensure_autonomous_state_broker.ps1"' in bootstrap
    assert '"-ExecutionPolicy"' in bootstrap
    assert '"-File"' in bootstrap
    assert "shell=False" in bootstrap
    assert "if health():" in bootstrap
    assert "if not health():" in bootstrap
    assert main.index("_ensure_dependencies(dev)") < main.index("_ensure_windows_state_broker()")
    assert main.index("_ensure_windows_state_broker()") < main.index("uvicorn")


def test_state_broker_bootstrap_does_not_assume_a_specific_runner_user_or_install_path():
    ensure = (ROOT / "scripts" / "ensure_autonomous_state_broker.ps1").read_text(encoding="utf-8")
    provision = (ROOT / "scripts" / "provision_autonomous_runner.ps1").read_text(encoding="utf-8")

    assert "Get-CimInstance Win32_Service" in ensure
    assert "StartName" in ensure
    assert "Get-AutonomousWindowsAccountSid -AccountName $runner.Service.StartName" in ensure
    assert "RunnerService\\.exe" in ensure
    assert "Runner.Worker.exe" in ensure
    assert "C:\\actions-runner" not in ensure + provision
    assert "runner-svc" not in ensure + provision
    assert '[string]$RunnerAccount = ""' in provision
    assert '[string]$RunnerRoot = ""' in provision
