from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _read(relative: str) -> str:
    return (ROOT / relative).read_text(encoding="utf-8")


def test_autonomous_runner_uses_the_loopback_api_not_a_user_scoped_ollama_cli():
    workflow = _read(".github/workflows/autonomous-codex-engineering.yml")
    provision = _read("scripts/provision_autonomous_runner.ps1")
    runner_check = _read("scripts/check_autonomous_runner.ps1")
    agent_check = _read("scripts/check_autonomous_agent.ps1")
    diagnostic = _read(".github/workflows/codex-windows-sandbox-diagnostic.yml")
    runtime = _read("scripts/AutonomousOllamaRuntime.psm1")

    assert "Get-Command ollama" not in workflow
    assert "ollama --version" not in workflow
    assert "Get-Command ollama" not in provision
    assert "Get-Command ollama" not in runner_check
    assert "Get-Command ollama" not in agent_check
    assert "Get-Command ollama" not in diagnostic
    assert "ollama --version" not in diagnostic
    assert "AutonomousOllamaRuntime.psm1" in diagnostic
    assert 'AutonomousOllamaRuntime.psm1" -Force' in workflow
    assert 'Import-Module (Join-Path $PSScriptRoot "AutonomousOllamaRuntime.psm1") -Force' in provision
    assert 'Import-Module (Join-Path $PSScriptRoot "AutonomousOllamaRuntime.psm1") -Force' in runner_check
    assert '$script:AutonomousOllamaApiBaseUrl = "http://127.0.0.1:11434"' in runtime
    assert "/api/tags" in runtime
    assert "ConvertFrom-AutonomousOllamaTagsResponse" in runtime
    assert "Test-AutonomousOllamaModelInstalled" in runtime
    assert "Ollama API is unavailable" in runtime


def test_autonomous_ollama_runtime_contract_rejects_malformed_model_catalogs():
    runtime = _read("scripts/AutonomousOllamaRuntime.psm1")

    assert '$Response.PSObject.Properties["models"]' in runtime
    assert '$model.PSObject.Properties["name"]' in runtime
    assert "without a valid name" in runtime
    assert "MaximumRedirection 0" in runtime
    assert "Get-Command ollama" not in runtime


def test_windows_provisioning_uses_only_the_dedicated_runner_identity():
    script = _read("scripts/provision_autonomous_runner.ps1")
    module = _read("scripts/AutonomousWindowsIdentity.psm1")

    assert 'Import-Module (Join-Path $PSScriptRoot "AutonomousWindowsIdentity.psm1")' in script
    assert "ConvertTo-AutonomousWindowsAccountName" in module
    assert "Get-AutonomousLocalAccountName" in module
    assert "Get-AutonomousWindowsAccountSid" in module
    assert "Get-AutonomousWindowsAccountSid -AccountName $runnerIdentity" in script
    assert "$runnerIsLocal = -not [string]::IsNullOrWhiteSpace($runnerLocalName)" in script
    assert script.index("$runnerIsLocal =") < script.index("if ($runnerIsLocal)")
    assert "AppAccount" not in script
    assert "$appIdentity" not in script
    assert "$appSid" not in script
    assert "ReadOnlySid" not in module
    assert "if ($value.Contains(" in module
    assert 'if ($value.Contains("@"))' not in module


def test_shared_state_tree_acl_is_recursive_and_only_grants_runner_and_local_service():
    script = _read("scripts/provision_autonomous_runner.ps1")
    configure = _read("scripts/configure_autonomous_engineering_progress.ps1")
    module = _read("scripts/AutonomousWindowsIdentity.psm1")

    assert 'Set-AutonomousDirectoryTreeAcl -Path $progressPath -ModifySids @($runnerSid, $brokerServiceSid) -OwnerSid $brokerServiceSid -InstallerSid $installerSid' in script
    assert 'Set-AutonomousDirectoryTreeAcl -Path $progressPath -ModifySids @($runnerSid, $brokerServiceSid) -OwnerSid $brokerServiceSid -InstallerSid $installerSid' in configure
    assert "Set-AutonomousDirectoryAcl -Path $RunnerRoot -RunnerSid $runnerSid" in script
    assert "Set-AutonomousDirectoryTreeAcl" in module
    assert "$acl.SetAccessRuleProtection($true, $false)" in module
    assert "$acl.RemoveAccessRuleSpecific($rule)" in module
    assert "$acl.SetAccessRule($newRule)" in module
    assert "ReparsePoint" in module
    assert "takeown.exe" in module
    acl_function = module[module.index("function Set-AutonomousDirectoryTreeAcl"):]
    assert "$pendingDirectories.Push($root)" in acl_function
    assert "$items.Add($directoryInfo)" in acl_function
    assert "EnumerateFileSystemEntries" in acl_function
    assert acl_function.index("EnumerateFileSystemEntries") < acl_function.index("takeown.exe")
    assert acl_function.index("EnumerateFileSystemEntries") < acl_function.index("icacls.exe")
    assert "/grant:r $installerGrant /C /Q" in acl_function
    assert "/grant:r $installerGrant /T" not in acl_function
    assert "/F $root /R" not in acl_function
    assert "$allowed = @($ModifySids | Select-Object -Unique)" in module
    assert "$acl.SetOwner([Security.Principal.SecurityIdentifier]$OwnerSid)" in module
    assert "OwnerSid" in script and '"S-1-5-19"' in script
    assert "AppAccount" not in configure
    assert "Authenticated Users" not in configure
    assert "S-1-5-32-545" not in script + configure + module
    assert "BUILTIN" not in script + configure + module
    assert "Everyone" not in script + configure + module


def test_machine_state_broker_uses_a_local_allowlisted_pipe_not_a_network_file_api():
    service = _read("scripts/AutonomousStateBrokerService.cs")
    python_client = _read("app/autonomous_engineering_state_broker.py")
    powershell_client = _read("scripts/AutonomousStateBroker.psm1")
    provision = _read("scripts/provision_autonomous_runner.ps1")

    assert "NamedPipeServerStream" in service
    assert "PipeSecurity" in service
    assert 'new SecurityIdentifier("S-1-5-11")' in service
    assert "GetNamedPipeClientComputerName" in service
    assert "Remote named-pipe clients are not accepted" in service
    assert "RunAsClient" in service
    assert "PipeAccessRights.ReadWrite" in service
    assert "TcpListener" not in service
    assert "TcpClient" not in service
    assert "NetworkStream" not in service
    assert '"version", 1' in service
    assert 'method == "GET" and path in {"/v1/health", "/v1/pending-work", "/v1/progress"}' in python_client
    assert 'method == "POST" and path == "/v1/cleanup"' in python_client
    assert 're.fullmatch(r"/v1/diagnostics/[0-9]{1,20}", path)' in python_client
    assert "Unsupported state-broker endpoint" in python_client
    assert "ValidateSet(" in powershell_client
    assert '"/v1/health", "/v1/pending-work", "/v1/progress"' in powershell_client
    assert "[System.IO.Pipes.PipeOptions]::Asynchronous" in powershell_client
    assert "WriteLineAsync" in powershell_client and "ReadLineAsync" in powershell_client
    assert "GetAwaiter().GetResult()" in powershell_client
    assert "ReadTimeout" not in powershell_client and "WriteTimeout" not in powershell_client
    assert "Wait([TimeSpan]::FromMilliseconds" in powershell_client
    assert "PersonalAIAssistantStateBroker" in provision
    assert "'obj=' 'NT AUTHORITY\\LocalService' 'start=' 'auto'" in provision
    assert 'Join-Path $env:ProgramData "PersonalAIAssistant\\StateBroker"' in provision
    assert "AutonomousStateBrokerService.cs" in provision
    assert "Invoke-AutonomousStateBroker" in provision


def test_app_and_runner_checks_use_the_state_broker_without_app_directory_acl():
    app_check = _read("scripts/check_autonomous_agent.ps1")
    runner_check = _read("scripts/check_autonomous_runner.ps1")
    configure = _read("scripts/configure_autonomous_engineering_progress.ps1")

    assert 'Get-Service -Name "PersonalAIAssistantStateBroker"' in app_check
    assert 'Invoke-AutonomousStateBroker -Method GET -Path "/v1/health"' in app_check
    assert "Join-Path $env:ProgramData" not in app_check
    assert 'Invoke-AutonomousStateBroker -Method GET -Path "/v1/health"' in runner_check
    assert "Runner shared-state write access" in runner_check
    assert "WriteAllText($probe" in runner_check
    assert "AppAccount" not in configure


def test_windows_provisioning_uses_the_runtime_shared_state_path_contract():
    script = _read("scripts/provision_autonomous_runner.ps1")
    paths_module = _read("scripts/AutonomousWindowsPaths.psm1")

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
    script = _read("scripts/provision_autonomous_runner.ps1")

    assert 'Get-LocalGroup -SID "S-1-5-32-544"' in script
    assert "Runner account is a direct member of the local Administrators group" in script
    assert "RunnerAccount must be a non-empty Windows account identity." in script
    assert "^[A-Za-z0-9_.-]+$" not in script
    assert "runnerLocalName.Length -gt 20" in script
