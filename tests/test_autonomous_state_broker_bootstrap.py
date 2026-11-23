from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_machine_state_broker_is_checked_before_app_startup():
    source = (ROOT / "run.py").read_text(encoding="utf-8")
    bootstrap = source[source.index("def _ensure_state_broker"):source.index("\ndef main()")]
    main = source[source.index("def main()"):]

    assert "def _ensure_state_broker() -> bool:" in bootstrap
    assert "from app.autonomous_engineering_state_broker import health" in bootstrap
    assert 'if system == "Windows":' in bootstrap
    assert 'elif system in {"Linux", "Darwin"}:' in bootstrap
    assert 'ROOT / "scripts" / "ensure_autonomous_state_broker.ps1"' in bootstrap
    assert 'ROOT / "scripts" / "ensure_autonomous_state_broker_posix.py"' in bootstrap
    assert '"-ExecutionPolicy"' in bootstrap
    assert '"-File"' in bootstrap
    assert "shell=False" in bootstrap
    assert "capture_output = False" in bootstrap
    assert "if health():" in bootstrap
    assert "if not health():" in bootstrap
    assert main.index("_ensure_dependencies(dev)") < main.index("from dotenv import load_dotenv")
    assert main.index("load_dotenv(ROOT / \".env\", override=False)") < main.index("_ensure_state_broker()")
    assert main.index("_ensure_state_broker()") < main.index("_ensure_ollama()")
    assert main.index("_ensure_state_broker()") < main.index("uvicorn")


def test_windows_broker_setup_discovers_runner_identity_and_path():
    ensure = (ROOT / "scripts" / "ensure_autonomous_state_broker.ps1").read_text(encoding="utf-8")
    provision = (ROOT / "scripts" / "provision_autonomous_runner.ps1").read_text(encoding="utf-8")

    assert "Get-CimInstance Win32_Service" in ensure
    assert "StartName" in ensure
    assert "Get-AutonomousWindowsAccountSid -AccountName $runner.Service.StartName" in ensure
    assert r"RunnerService\.exe" in ensure
    assert "Runner.Worker.exe" in ensure
    assert r"C:\actions-runner" not in ensure + provision
    assert "runner-svc" not in ensure + provision
    assert '[string]$RunnerAccount = ""' in provision
    assert '[string]$RunnerRoot = ""' in provision
    assert "Start-Process" in ensure and "-Verb RunAs" in ensure
    assert ensure.index("Stop-Service -Name $runnerService.Name") < ensure.index("Set-AutonomousDirectoryTreeAcl")
    assert "activeWorkersAfterStop" in ensure
    assert "Start-Service -Name $runnerService.Name" in ensure


def test_posix_broker_setup_uses_dynamic_service_identity_and_native_service_manager():
    installer = (ROOT / "scripts" / "ensure_autonomous_state_broker_posix.py").read_text(encoding="utf-8")
    service = (ROOT / "scripts" / "autonomous_state_broker_service_posix.py").read_text(encoding="utf-8")
    config = (ROOT / "app" / "autonomous_engineering_state_broker_config.py").read_text(encoding="utf-8")
    client = (ROOT / "app" / "autonomous_engineering_state_broker_unix.py").read_text(encoding="utf-8")

    assert "_discover_runner_service_uid" in installer
    assert "actions.runner." in installer
    assert '"/Library/LaunchDaemons"' in installer
    assert '"/etc/systemd/system"' in installer
    assert '"UserName"' in installer
    assert "service_uid = runner_uid if runner_uid is not None" in installer
    assert "CapabilityBoundingSet=" in installer
    assert "RestrictAddressFamilies=AF_UNIX" in installer
    assert 'sys.executable,\n            "-I",\n            "-S",' in installer
    assert "pwd.getpwall()" in installer
    assert 'Path("/usr/bin/python3")' not in installer
    assert 'Path("/usr/local/bin/python3")' not in installer
    assert 'Path("/opt/homebrew/bin/python3")' not in installer
    assert 'Path("/opt/local/bin/python3")' not in installer
    assert 'Path("/home")' not in installer and 'Path("/Users")' not in installer
    assert "_assert_no_active_runner_worker()" in installer
    assert '["-axo", "pid=,command="]' in installer
    assert "sys.path.insert(0, str(project_root))" in installer
    assert "def _prepare_socket_path" in service
    assert "AccessControl" not in service
    assert "MARKERS" in service and "CLEANUP_NAMES" in service
    assert "/v1/progress" in client
    assert "configured_socket_path" in client
    assert "platformdirs" in config
    assert 'site_config_path("PersonalAIAssistant", appauthor=False)' in config
    assert 'Path("/etc/xdg")' not in config
    assert 'Path("/Library/Application Support")' not in config
    assert "process_override" not in config


def test_posix_state_migration_does_not_rewrite_existing_tree_permissions():
    installer = (ROOT / "scripts" / "ensure_autonomous_state_broker_posix.py").read_text(encoding="utf-8")
    migration = installer[
        installer.index("def _migrate_legacy_state"):
        installer.index("def _systemd_unit")
    ]

    assert "shutil.copy2(source, temporary)" in migration
    assert "os.chown(temporary, service_uid" in migration
    assert "os.chmod(temporary, stat.S_IMODE(source_metadata.st_mode))" in migration
    assert "for root, directories, files in os.walk(state_dir" not in migration
    assert "os.chmod(directory, 0o700)" not in migration
    assert "both copies were preserved" in migration

def test_posix_machine_config_directory_uses_platformdirs(monkeypatch, tmp_path):
    import platformdirs

    from app import autonomous_engineering_state_broker_config as config

    expected = tmp_path / "machine-config" / "PersonalAIAssistant"
    calls = []

    def resolve_site_config(appname, *, appauthor=False):
        calls.append((appname, appauthor))
        return expected

    monkeypatch.setattr(platformdirs, "site_config_path", resolve_site_config)

    assert config.config_directory() == expected
    assert calls == [("PersonalAIAssistant", False)]

