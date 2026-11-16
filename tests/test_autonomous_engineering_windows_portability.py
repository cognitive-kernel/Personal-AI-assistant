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


def test_windows_provisioning_does_not_require_a_fixed_users_group_or_admin_acl_grants():
    script = (ROOT / "scripts" / "provision_autonomous_runner.ps1").read_text(encoding="utf-8")
    module = (ROOT / "scripts" / "AutonomousWindowsIdentity.psm1").read_text(encoding="utf-8")

    assert "Add-LocalGroupMember" not in script
    assert "S-1-5-32-545" not in script
    assert "BUILTIN" not in script + module
    assert '"SYSTEM:(OI)(CI)F"' not in script + module
    assert "Set-AutonomousDirectoryAcl -Path $RunnerRoot -RunnerSid $runnerSid" in script
    assert "Set-AutonomousDirectoryAcl -Path $progressPath -RunnerSid $runnerSid -AdditionalModifySids $appSid" in script
    assert "Set-AutonomousDirectoryAcl -Path $progressPath -RunnerSid $runnerSid -AdditionalModifySids @($appSid, $installerSid)" in script
    assert "[string[]]$AdditionalModifySids" in module
    assert "foreach ($sid in $additionalSids)" in module
    assert "$allowedSids = @($modifySids)" in module
    assert '$Path /remove "*$sid"' in module
    assert "$unexpectedSids" in module
    assert '$modifySids = @($RunnerSid)' in module
    assert '*$($_):(OI)(CI)M' in module
    assert '*$($ReadOnlySid):(OI)(CI)RX' in module
    assert '$installerSid = [Security.Principal.WindowsIdentity]::GetCurrent().User.Value' in script
    assert "Set-AutonomousDirectoryAcl -Path $RunnerRoot -RunnerSid $runnerSid -AdditionalModifySid $installerSid" in script
    assert '$runnerTempPath = Join-Path $RunnerRoot "_work\\_temp"' in script
    assert 'Set-AutonomousDirectoryAcl -Path $runnerTempPath -RunnerSid $runnerSid -AdditionalModifySid $appSid' in script
    assert '[Environment]::SetEnvironmentVariable("AUTONOMOUS_ENGINEERING_RUNNER_TEMP", $runnerTempPath, "Machine")' in script
    assert '$runnerDiagnosticPath = Join-Path $RunnerRoot "_diag"' in script
    assert 'Set-AutonomousDirectoryAcl -Path $runnerDiagnosticPath -RunnerSid $runnerSid -AdditionalModifySid $appSid' in script


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
    assert "RunnerAccount and AppAccount must be non-empty Windows account identities." in script
    assert "^[A-Za-z0-9_.-]+$" not in script
    assert "runnerLocalName.Length -gt 20" in script
