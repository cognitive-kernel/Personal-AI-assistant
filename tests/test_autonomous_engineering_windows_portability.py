from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_autonomous_runner_uses_the_loopback_api_not_a_user_scoped_ollama_cli():
    workflow = (ROOT / ".github" / "workflows" / "autonomous-codex-engineering.yml").read_text(encoding="utf-8")
    provision = (ROOT / "scripts" / "provision_autonomous_runner.ps1").read_text(encoding="utf-8")
    runner_check = (ROOT / "scripts" / "check_autonomous_runner.ps1").read_text(encoding="utf-8")
    agent_check = (ROOT / "scripts" / "check_autonomous_agent.ps1").read_text(encoding="utf-8")
    diagnostic = (ROOT / ".github" / "workflows" / "codex-windows-sandbox-diagnostic.yml").read_text(encoding="utf-8")
    runtime = (ROOT / "scripts" / "AutonomousOllamaRuntime.psm1").read_text(encoding="utf-8")

    assert "Get-Command ollama" not in workflow
    assert "ollama --version" not in workflow
    assert "Get-Command ollama" not in provision
    assert "Get-Command ollama" not in runner_check
    assert "Get-Command ollama" not in agent_check
    assert "Get-Command ollama" not in diagnostic
    assert "ollama --version" not in diagnostic
    assert "AutonomousOllamaRuntime.psm1" in diagnostic
    assert 'Import-Module "$env:GITHUB_WORKSPACE\\scripts\\AutonomousOllamaRuntime.psm1" -Force' in workflow
    assert 'Import-Module (Join-Path $PSScriptRoot "AutonomousOllamaRuntime.psm1") -Force' in provision
    assert 'Import-Module (Join-Path $PSScriptRoot "AutonomousOllamaRuntime.psm1") -Force' in runner_check
    assert '$script:AutonomousOllamaApiBaseUrl = "http://127.0.0.1:11434"' in runtime
    assert '/api/tags' in runtime
    assert "ConvertFrom-AutonomousOllamaTagsResponse" in runtime
    assert "Test-AutonomousOllamaModelInstalled" in runtime
    assert "Ollama API is unavailable" in runtime


def test_autonomous_ollama_runtime_contract_rejects_malformed_model_catalogs():
    runtime = (ROOT / "scripts" / "AutonomousOllamaRuntime.psm1").read_text(encoding="utf-8")

    assert '$Response.PSObject.Properties["models"]' in runtime
    assert '$model.PSObject.Properties["name"]' in runtime
    assert "without a valid name" in runtime
    assert "MaximumRedirection 0" in runtime
    assert "Get-Command ollama" not in runtime


def test_windows_provisioning_uses_the_shared_identity_module():
    script = (ROOT / "scripts" / "provision_autonomous_runner.ps1").read_text(encoding="utf-8")
    module = (ROOT / "scripts" / "AutonomousWindowsIdentity.psm1").read_text(encoding="utf-8")

    assert 'Import-Module (Join-Path $PSScriptRoot "AutonomousWindowsIdentity.psm1")' in script
    assert "ConvertTo-AutonomousWindowsAccountName" in module
    assert "Get-AutonomousLocalAccountName" in module
    assert "Get-AutonomousWindowsAccountSid" in module
    assert "Get-AutonomousWindowsAccountSid -AccountName $appIdentity" in script
    assert "Get-AutonomousWindowsAccountSid -AccountName $runnerIdentity" in script
    assert "$runnerIsLocal = -not [string]::IsNullOrWhiteSpace($runnerLocalName)" in script
    assert script.index("$runnerIsLocal =") < script.index("if ($runnerIsLocal)")
    assert "if ($value.Contains(" in module
    assert 'if ($value.Contains("@"))' not in module


def test_windows_provisioning_adds_modify_access_without_replacing_existing_acl_entries():
    script = (ROOT / "scripts" / "provision_autonomous_runner.ps1").read_text(encoding="utf-8")
    module = (ROOT / "scripts" / "AutonomousWindowsIdentity.psm1").read_text(encoding="utf-8")

    assert "Add-LocalGroupMember" not in script
    assert "Remove-LocalGroupMember" not in script
    assert "S-1-5-32-545" not in script + module
    assert "BUILTIN" not in script + module
    assert '"SYSTEM:(OI)(CI)F"' not in script + module
    assert "Set-AutonomousDirectoryAcl -Path $RunnerRoot -RunnerSid $runnerSid" in script
    assert "Set-AutonomousStateDirectoryAcl -Path $progressPath -RunnerSid $runnerSid -AdditionalModifySids $appSid" in script
    assert "Set-AutonomousStateDirectoryAcl -Path $progressPath -RunnerSid $runnerSid -AdditionalModifySids @($appSid, $installerSid)" in script
    assert "[string[]]$AdditionalModifySids" in module
    assert "foreach ($sid in $additionalSids)" in module
    assert "$modifySids = @($RunnerSid)" in module
    assert "$acl.AddAccessRule($rule)" in module
    assert "Set-Acl -LiteralPath $Path -AclObject $acl" in module
    assert "/inheritance:r" not in module
    assert "/remove" not in module
    assert "/grant:r" not in module
    assert "$unexpectedSids" not in module
    assert '$installerSid = [Security.Principal.WindowsIdentity]::GetCurrent().User.Value' in script
    assert "Set-AutonomousDirectoryAcl -Path $RunnerRoot -RunnerSid $runnerSid -AdditionalModifySid $installerSid" in script
    assert '$runnerTempPath = Join-Path $RunnerRoot "_work\\_temp"' in script
    assert 'Set-AutonomousDirectoryAcl -Path $runnerTempPath -RunnerSid $runnerSid -AdditionalModifySid $appSid' in script
    assert '[Environment]::SetEnvironmentVariable("AUTONOMOUS_ENGINEERING_RUNNER_TEMP", $runnerTempPath, "Machine")' in script
    assert '$runnerDiagnosticPath = Join-Path $RunnerRoot "_diag"' in script
    assert 'Set-AutonomousDirectoryAcl -Path $runnerDiagnosticPath -RunnerSid $runnerSid -AdditionalModifySid $appSid' in script


def test_storage_acceptance_script_is_independent_of_ollama_and_account_names():
    script = (ROOT / "scripts" / "check_autonomous_storage.ps1").read_text(encoding="utf-8")
    paths = (ROOT / "scripts" / "AutonomousWindowsPaths.psm1").read_text(encoding="utf-8")

    assert "WindowsIdentity]::GetCurrent()" in script
    assert "AUTONOMOUS_ENGINEERING_PROGRESS_DIR" in script
    assert "Test-AutonomousStateStorageAccess" in script
    assert "autonomous-durable-state.lock" in script
    assert "ollama" not in script.lower()
    assert "RunnerAccount" not in script
    assert "AppAccount" not in script
    assert "Test-AutonomousStateStorageAccess" in paths


def test_runner_identity_is_discovered_or_generated_without_fixed_account_names():
    provision = (ROOT / "scripts" / "provision_autonomous_runner.ps1").read_text(encoding="utf-8")
    configure = (ROOT / "scripts" / "configure_autonomous_engineering_progress.ps1").read_text(encoding="utf-8")
    identity = (ROOT / "scripts" / "AutonomousWindowsIdentity.psm1").read_text(encoding="utf-8")

    assert "[Parameter()][ValidateNotNullOrEmpty()][string]$RunnerAccount" in provision
    assert '[guid]::NewGuid().ToString("N").Substring(0, 19)' in provision
    assert "Get-AutonomousRunnerService -RunnerRoot $RunnerRoot" in provision
    assert "$RunnerAccount = $discoveredService.StartName" in provision
    assert "Get-AutonomousRunnerService -RunnerRoot $resolvedRunnerRoot" in configure
    assert "Get-AutonomousRunnerService" in identity
    assert "AUTONOMOUS_ENGINEERING_RUNNER_ROOT" in provision
    assert "AUTONOMOUS_ENGINEERING_PROGRESS_DIR" in provision


def test_windows_provisioning_uses_the_runtime_shared_state_path_contract():
    script = (ROOT / "scripts" / "provision_autonomous_runner.ps1").read_text(encoding="utf-8")
    paths_module = (ROOT / "scripts" / "AutonomousWindowsPaths.psm1").read_text(encoding="utf-8")

    assert 'Import-Module (Join-Path $PSScriptRoot "AutonomousWindowsPaths.psm1")' in script
    assert "$env:AUTONOMOUS_ENGINEERING_PROGRESS_DIR" in script
    assert '[Environment]::GetEnvironmentVariable("AUTONOMOUS_ENGINEERING_PROGRESS_DIR", "Machine")' in script
    assert "must be configured at machine scope" in script
    assert "Process and machine AUTONOMOUS_ENGINEERING_PROGRESS_DIR values differ" in script
    assert "Resolve-AutonomousStateDirectory" in paths_module
    assert "AUTONOMOUS_ENGINEERING_PROGRESS_DIR must be an absolute shared path." in paths_module
    assert "per-user fallback is not supported on Windows" in paths_module
    assert "RunnerRoot and shared autonomous state directory must be separate" in script


def test_windows_provisioning_keeps_runner_least_privilege_as_a_role_invariant():
    script = (ROOT / "scripts" / "provision_autonomous_runner.ps1").read_text(encoding="utf-8")

    assert 'Get-LocalGroup -SID "S-1-5-32-544"' in script
    assert "Runner account is a direct member of the local Administrators group" in script
    assert "[Parameter()][ValidateNotNullOrEmpty()][string]$AppAccount" in script
    assert "$AppAccount = if ([string]::IsNullOrWhiteSpace($AppAccount)) { $identity.Name } else { $AppAccount.Trim() }" in script
    assert "$appSid = if ($AppAccount -eq $identity.Name) { $identity.User.Value } else {" in script
    assert "^[A-Za-z0-9_.-]+$" not in script
    assert "runnerLocalName.Length -gt 20" in script


def test_windows_state_readiness_uses_the_shared_configured_path_and_lock_probe():
    paths_module = (ROOT / "scripts" / "AutonomousWindowsPaths.psm1").read_text(encoding="utf-8")
    agent_check = (ROOT / "scripts" / "check_autonomous_agent.ps1").read_text(encoding="utf-8")
    runner_check = (ROOT / "scripts" / "check_autonomous_runner.ps1").read_text(encoding="utf-8")
    configure = (ROOT / "scripts" / "configure_autonomous_engineering_progress.ps1").read_text(encoding="utf-8")

    assert "Test-AutonomousStateStorageAccess" in paths_module
    assert '"autonomous-durable-state.lock"' in paths_module
    assert '$stream.Lock(0, 1)' in paths_module
    assert '$stream.Unlock(0, 1)' in paths_module
    assert "AUTONOMOUS_ENGINEERING_PROGRESS_DIR" in paths_module
    assert "Join-Path $env:ProgramData \"PersonalAIAssistant\\autonomous-engineering\"" not in agent_check
    assert "Join-Path $env:ProgramData \"PersonalAIAssistant\\autonomous-engineering\"" not in runner_check
    assert agent_check.count("Test-AutonomousStateStorageAccess") == 1
    assert runner_check.count("Test-AutonomousStateStorageAccess") == 1
    assert "BUILTIN\\Administrators" not in configure
    assert '"SYSTEM:(OI)(CI)F"' not in configure
    assert "Set-AutonomousStateDirectoryAcl" in configure
    assert "Test-AutonomousStateStorageAccess" in configure


def test_existing_durable_lock_file_receives_explicit_dynamic_modify_acl():
    identity_module = (ROOT / "scripts" / "AutonomousWindowsIdentity.psm1").read_text(encoding="utf-8")
    provision = (ROOT / "scripts" / "provision_autonomous_runner.ps1").read_text(encoding="utf-8")
    configure = (ROOT / "scripts" / "configure_autonomous_engineering_progress.ps1").read_text(encoding="utf-8")

    assert "function Set-AutonomousStateDirectoryAcl" in identity_module
    assert '"autonomous-durable-state.lock"' in identity_module
    assert "-File" in identity_module
    assert "[System.Security.AccessControl.FileSystemRights]::Modify" in identity_module
    assert "$acl.AddAccessRule($rule)" in identity_module
    assert "[System.IO.FileMode]::OpenOrCreate" in identity_module
    assert "Set-AutonomousStateDirectoryAcl -Path $progressPath" in provision
    assert "Set-AutonomousStateDirectoryAcl -Path $progressPath" in configure
