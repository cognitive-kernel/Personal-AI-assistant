from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_machine_state_broker_startup_preflight_is_read_only():
    source = (ROOT / "run.py").read_text(encoding="utf-8")
    bootstrap = source[source.index("def _ensure_state_broker"):source.index("\ndef main()")]
    main = source[source.index("def main()"):]

    assert "def _ensure_state_broker() -> bool:" in bootstrap
    assert "from app.autonomous_engineering_state_broker import health" in bootstrap
    assert "health(timeout_ms=30000, verify=True)" in bootstrap
    assert "Start-Process" not in bootstrap
    assert "ensure_autonomous_state_broker.ps1" not in bootstrap
    assert "ensure_autonomous_state_broker_posix.py" not in bootstrap
    assert "subprocess.run" not in bootstrap
    assert main.index("_ensure_dependencies(dev)") < main.index("from dotenv import load_dotenv")
    assert main.index('load_dotenv(ROOT / ".env", override=False)') < main.index("_ensure_state_broker()")
    assert main.index("_ensure_state_broker()") < main.index("_ensure_ollama()")
    assert main.index("_ensure_state_broker()") < main.index("uvicorn")


def test_windows_broker_setup_discovers_runner_identity_and_path():
    ensure = (ROOT / "scripts" / "ensure_autonomous_state_broker.ps1").read_text(encoding="utf-8")
    provision = (ROOT / "scripts" / "provision_autonomous_runner.ps1").read_text(encoding="utf-8")

    assert "Get-CimInstance Win32_Service" in ensure
    assert "StartName" in ensure
    assert "Get-AutonomousWindowsAccountSid -AccountName $candidate.StartName" in ensure
    assert r"RunnerService\.exe" in ensure
    assert "Runner.Worker.exe" in ensure
    assert r"C:\actions-runner" not in ensure + provision
    assert "runner-svc" not in ensure + provision
    assert '[string]$RunnerAccount = ""' in provision
    assert '[string]$RunnerRoot = ""' in provision
    assert "Start-Process" in ensure and "-Verb RunAs" in ensure
    elevation = ensure[:ensure.index("$brokerLogDirectory")]
    assert '$previousModulePath = $env:PSModulePath' in elevation
    assert '$env:PSModulePath = Join-Path $PSHOME "Modules"' in elevation
    assert elevation.index('$env:PSModulePath = Join-Path $PSHOME "Modules"') < elevation.index("Start-Process")
    assert '$env:PSModulePath = $previousModulePath' in elevation
    assert "finally {" in elevation
    assert "$modifySids = @($brokerServiceSid)" in ensure
    assert "$modifySids += $runner.Sid" in ensure
    assert "foreach ($runner in $allRunnerServices)" in ensure
    assert "Expected exactly one installed Runner service matching the supplied RunnerRoot" in ensure
    assert "if ($resolvedRunnerServices.Count -eq 1)" not in ensure
    assert "No GitHub Runner is installed; the core application broker is ready." in ensure
    assert 'Join-Path $runner.Root ".runner"' not in ensure
    assert ensure.index("Stop-Service -Name $service.Name") < ensure.index("Set-AutonomousDirectoryTreeAcl")
    assert ensure.index("$stoppedRunnerServices += $service.Name") < ensure.index("$service.WaitForStatus(")
    assert "activeWorkersAfterStop" in ensure
    assert "Start-Transcript -Path $brokerLogPath -Append -Force" in ensure
    assert "Start-Service -Name $serviceNameToRestart" in ensure
    assert 'if ($restarted.Status -eq "StopPending")' in ensure
    assert 'if ($restarted.Status -eq "Stopped")' in ensure


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
    assert '"/home"' in installer and '"/Users"' in installer
    assert "_assert_no_active_runner_worker(sys.platform)" in installer
    assert "sys.path.insert(0, str(project_root))" in installer
    assert "def _prepare_socket_path" in service
    assert "AccessControl" not in service
    assert "MARKERS" in service and "CLEANUP_NAMES" in service
    assert "/v1/progress" in client
    assert "configured_socket_path" in client
    assert 'Path("/etc/xdg")' in config
    assert 'Path("/Library/Application Support")' in config
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

def test_windows_acl_module_loads_security_cmdlets_in_its_own_scope():
    module = (ROOT / "scripts" / "AutonomousWindowsIdentity.psm1").read_text(encoding="utf-8")

    security_import = "Import-Module Microsoft.PowerShell.Security -ErrorAction Stop"
    assert security_import in module
    assert module.index(security_import) < module.index("function Set-AutonomousDirectoryAcl")
    assert "Get-Acl -LiteralPath" in module
    assert "Set-Acl -LiteralPath" in module


def test_windows_acl_module_import_isolated_to_active_host_and_restores_module_path():
    module = (ROOT / "scripts" / "AutonomousWindowsIdentity.psm1").read_text(encoding="utf-8")
    import_block = module[:module.index("function ConvertTo-AutonomousWindowsAccountName")]

    assert '$previousModulePath = $env:PSModulePath' in import_block
    assert '$env:PSModulePath = Join-Path $PSHOME "Modules"' in import_block
    assert "Import-Module Microsoft.PowerShell.Security -ErrorAction Stop" in import_block
    assert import_block.index('$env:PSModulePath = Join-Path $PSHOME "Modules"') < import_block.index(
        "Import-Module Microsoft.PowerShell.Security"
    )
    assert import_block.index("Import-Module Microsoft.PowerShell.Security") < import_block.index(
        "$env:PSModulePath = $previousModulePath"
    )
    assert "finally {" in import_block
    assert "SetEnvironmentVariable" not in import_block
    assert "Program Files\\\\PowerShell" not in import_block

def test_windows_broker_relaunches_the_current_host_without_pinning_powershell_exe():
    ensure = (ROOT / "scripts" / "ensure_autonomous_state_broker.ps1").read_text(encoding="utf-8")
    compatibility = (ROOT / "scripts" / "configure_autonomous_engineering_progress.ps1").read_text(encoding="utf-8")

    assert '$hostExecutable = [string](Get-Process -Id $PID -ErrorAction Stop).Path' in ensure
    assert 'Start-Process -FilePath $hostExecutable -Verb RunAs' in ensure
    assert 'Join-Path $PSHOME "powershell.exe"' not in ensure
    assert '$powershell = [string](Get-Process -Id $PID -ErrorAction Stop).Path' in compatibility
    assert '& $powershell -NoLogo -NoProfile -NonInteractive' in compatibility
    assert 'Get-Command powershell.exe' not in compatibility
