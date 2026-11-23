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
    assert "$normalizedAccountName = ConvertTo-AutonomousWindowsAccountName -AccountName $AccountName" in module
    assert "[Security.Principal.NTAccount]$normalizedAccountName" in module
    assert '$RunnerAccount = [string]$matchingRunnerServices[0].Service.StartName' in script
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
    assert "ensure_autonomous_state_broker.ps1" in configure
    assert "Get-AutonomousWindowsAccountSid -AccountName $service.StartName" in configure
    assert "Set-AutonomousDirectoryTreeAcl" not in configure
    assert "Set-AutonomousDirectoryAcl -Path $RunnerRoot -RunnerSid $runnerSid" in script
    assert "Set-AutonomousDirectoryTreeAcl" in module
    assert "$acl.SetAccessRuleProtection($true, $false)" in module
    assert "$acl.RemoveAccessRuleSpecific($rule)" in module
    assert "$acl.SetAccessRule($newRule)" in module
    assert "ReparsePoint" in module
    assert "takeown.exe" in module
    acl_function = module[module.index("function Set-AutonomousDirectoryTreeAcl"):]
    assert "Get-ChildItem -LiteralPath $rootParent" in acl_function
    assert "Get-Item -LiteralPath $root" not in acl_function
    assert "$pendingDirectories.Push([pscustomobject]@{ Path = $root; Attributes = $rootEntry.Attributes })" in acl_function
    assert "$items.Add($directoryInfo)" in acl_function
    assert "Get-ChildItem -LiteralPath $directory" in acl_function
    directory_walk = acl_function[acl_function.index("while ($pendingDirectories"):acl_function.index("# Files are touched")]
    assert directory_walk.index("ReparsePoint") < directory_walk.index("& $takeown.Source /F $directory")
    assert directory_walk.index("& $takeown.Source /F $directory") < directory_walk.index("Get-ChildItem -LiteralPath $directory")
    assert "$snapshots.Add([pscustomobject]@{ Item = $directoryInfo; Acl = $originalAcl })" in directory_walk
    assert "if (-not $completed)" in acl_function
    assert "$restoreTargets = @($snapshots" in acl_function
    assert "$restoreOriginalAcl $snapshot" in acl_function
    assert acl_function.index("Get-Acl -LiteralPath $Item.FullName") < acl_function.index("Set-Acl -LiteralPath $Item.FullName")
    assert acl_function.count("Get-Acl -LiteralPath $Item.FullName") == 1
    assert "/grant:r $installerGrant /C /Q" in acl_function
    assert "/grant:r $installerGrant /T" not in acl_function
    assert "/R" not in acl_function
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
    installer = _read("scripts/AutonomousStateBrokerInstall.psm1")
    ensure = _read("scripts/ensure_autonomous_state_broker.ps1")

    assert "NamedPipeServerStream" in service
    assert "PipeOptions.Asynchronous" in service
    assert "ReadAsync(chunk" in service and "WriteAsync(bytes" in service
    assert "WaitForPipeIo" in service
    assert "ReadTimeout" not in service and "WriteTimeout" not in service
    assert "PipeSecurity" in service
    assert 'new SecurityIdentifier("S-1-5-2")' in service
    assert 'new SecurityIdentifier("S-1-5-11")' in service
    assert "AccessControlType.Deny" in service
    assert "GetNamedPipeClientComputerName" in service
    assert "ErrorPipeLocal = 229" in service
    assert "Marshal.GetLastWin32Error()" in service
    assert "clientMachine.Capacity * sizeof(char)" in service
    assert "clientNameError != ErrorPipeLocal" in service
    assert "Remote named-pipe clients are not accepted" in service
    assert "RunAsClient" in service
    assert "PipeAccessRights.ReadWrite" in service
    assert "TcpListener" not in service
    assert "TcpClient" not in service
    assert "NetworkStream" not in service
    assert '"version", 1' in service
    assert 'method == "GET" and path in {"/v1/health", "/v1/pending-work", "/v1/progress"}' in python_client
    assert '_WINDOWS and method == "GET" and path == "/v1/health/verify"' in python_client
    assert 'method == "POST" and path == "/v1/cleanup"' in python_client
    assert 're.fullmatch(r"/v1/diagnostics/[0-9]{1,20}", path)' in python_client
    assert "Unsupported state-broker endpoint" in python_client
    assert "ValidateSet(" in powershell_client
    assert '"/v1/health", "/v1/health/verify", "/v1/pending-work", "/v1/progress"' in powershell_client
    assert "[System.IO.Pipes.PipeOptions]::Asynchronous" in powershell_client
    assert "WriteLineAsync" in powershell_client and "ReadLineAsync" in powershell_client
    assert "GetAwaiter().GetResult()" in powershell_client
    assert "ReadTimeout" not in powershell_client and "WriteTimeout" not in powershell_client
    assert "Wait([TimeSpan]::FromMilliseconds" in powershell_client
    assert "AutonomousStateBrokerInstall.psm1" in provision
    assert "Install-AutonomousStateBrokerService" in provision
    assert "PersonalAIAssistantStateBroker" in installer
    assert "'obj=' 'NT AUTHORITY\\LocalService' 'start=' 'auto'" in installer
    assert 'Join-Path $env:ProgramData "PersonalAIAssistant\\StateBroker"' in installer
    assert "AutonomousStateBrokerService.cs" in installer
    assert "Invoke-AutonomousStateBroker" in installer
    assert "Start-Process" in ensure and "-Verb RunAs" in ensure
    assert "Runner.Worker.exe" in ensure
    assert "Get-AutonomousWindowsAccountSid -AccountName $candidate.StartName" in ensure




def test_machine_state_broker_health_checks_the_full_state_tree_acl_manifest():
    service = _read("scripts/AutonomousStateBrokerService.cs")
    ensure = _read("scripts/ensure_autonomous_state_broker.ps1")
    provision = _read("scripts/provision_autonomous_runner.ps1")
    configure = _read("scripts/configure_autonomous_engineering_progress.ps1")
    runtime_test = _read("scripts/test_autonomous_state_broker_service.ps1")
    client = _read("app/autonomous_engineering_state_broker.py")
    run = _read("run.py")

    assert 'ModifySidsMachineVariable = "AUTONOMOUS_ENGINEERING_STATE_MODIFY_SIDS"' in service
    assert "VerifyStateAclIntegrity()" in service
    assert "Directory.EnumerateFileSystemEntries(directory)" in service
    assert "FileSystemRights.Modify" in service
    assert "owner == null || !expected.Contains(owner.Value)" in service
    assert "String.Equals(owner.Value, BrokerOwnerSid, StringComparison.OrdinalIgnoreCase)" in service
    assert "MaxAclEntries = 100000" in service
    assert '"acl_ready", _lastAclHealth' in service
    assert 'SetEnvironmentVariable(\n        "AUTONOMOUS_ENGINEERING_STATE_MODIFY_SIDS"' in ensure
    assert 'SetEnvironmentVariable(\n        "AUTONOMOUS_ENGINEERING_STATE_MODIFY_SIDS"' in provision
    assert "ensure_autonomous_state_broker.ps1" in configure
    assert "Set-AutonomousDirectoryTreeAcl" not in configure
    assert 'health(*, timeout_ms: int = 3500, verify: bool = False)' in client
    assert '"/v1/health/verify"' in client
    assert 'path == "/v1/health/verify"' in service
    assert 'TimeSpan.FromSeconds(15)' in service
    assert 'VerifyStorageAccessCached(forceVerification)' in service
    assert 'health(timeout_ms=30000, verify=True)' in run
    assert 'Path "/v1/health/verify"' in ensure
    assert 'Path "/v1/health/verify"' in _read("scripts/AutonomousStateBrokerInstall.psm1")
    assert '"/v1/health/verify"' in _read("scripts/AutonomousStateBroker.psm1")
    assert 'value.get("acl_ready") is True' in client
    assert run.count('health(timeout_ms=30000, verify=True)') == 2
    assert 'TimeoutMilliseconds 30000' in ensure
    assert '$currentSid, $brokerSid, "S-1-5-32-545"' in runtime_test
    assert 'Path "/v1/health" -TimeoutMilliseconds 30000' in runtime_test
    assert 'Path "/v1/health/verify" -TimeoutMilliseconds 30000' in runtime_test
    assert "normal UI health endpoint did not reuse its bounded health cache" in runtime_test
    assert "forced startup health endpoint did not bypass cache" in runtime_test
    assert '$driftDetected = $false' in runtime_test
    assert 'if ($_.Exception.Message -match "503")' in runtime_test
    assert '$repairedHealth.acl_ready -ne $true' in runtime_test



def test_machine_state_broker_bootstrap_is_independent_of_runner_registration():
    ensure = _read("scripts/ensure_autonomous_state_broker.ps1")

    assert "$brokerServiceSid = \"S-1-5-19\"" in ensure
    assert "$modifySids = @($brokerServiceSid)" in ensure
    assert "$modifySids += $runner.Sid" in ensure
    assert "if (-not [string]::IsNullOrWhiteSpace($RunnerRoot) -and $resolvedRunnerServices.Count -ne 1)" in ensure
    assert "Expected exactly one installed Runner service matching the supplied RunnerRoot" in ensure
    assert "if ($resolvedRunnerServices.Count -ne 1)" not in ensure
    assert 'Join-Path $runner.Root ".runner"' not in ensure
    assert "No GitHub Runner is installed; the core application broker is ready." in ensure
    assert "foreach ($runner in $allRunnerServices)" in ensure
    assert "Get-AutonomousWindowsAccountSid -AccountName $candidate.StartName" in ensure
    assert ensure.index("Stop-Service -Name $service.Name") < ensure.index("Set-AutonomousDirectoryTreeAcl")
    assert "activeWorkersAfterStop" in ensure
    assert "Start-Service -Name $serviceNameToRestart" in ensure


def test_runner_provisioning_discovers_existing_path_and_service_identity():
    script = _read("scripts/provision_autonomous_runner.ps1")
    ensure = _read("scripts/ensure_autonomous_state_broker.ps1")
    module = _read("scripts/AutonomousWindowsIdentity.psm1")

    assert '[string]$RunnerAccount = ""' in script
    assert '[string]$RunnerRoot = ""' in script
    assert "RunnerService\\.exe" in script
    assert "$matchingRunnerServices[0].Service.StartName" in script
    assert 'Join-Path $env:ProgramData "GitHubActionsRunner"' in script
    assert "paa-runner-$suffix" in script
    assert "Get-Command node -ErrorAction Stop" not in script
    assert "Get-Command npm -ErrorAction Stop" not in script
    assert "Python 3.11 or newer is required on the Runner host" not in script
    assert '.venv\\\\Scripts\\\\python.exe' in script
    assert "$normalizedAccountName = ConvertTo-AutonomousWindowsAccountName -AccountName $AccountName" in module
    assert "ConvertTo-AutonomousWindowsAccountName -AccountName $AccountName" in module
    assert "Runner.Worker.exe" in ensure
    assert "C:\\actions-runner" not in script + ensure


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
    assert "AUTONOMOUS_ENGINEERING_PROGRESS_DIR must be configured at machine scope" in script
    assert "Process and machine AUTONOMOUS_ENGINEERING_PROGRESS_DIR values differ" not in script
    assert "Resolve-AutonomousStateDirectory" in paths_module
    assert "AUTONOMOUS_ENGINEERING_PROGRESS_DIR must be an absolute shared path." in paths_module
    assert "per-user fallback is not supported on Windows" in paths_module
    assert "RunnerRoot and shared autonomous state directory must be separate" in script


def test_windows_provisioning_keeps_runner_least_privilege_as_a_role_invariant():
    script = _read("scripts/provision_autonomous_runner.ps1")

    assert 'Get-LocalGroup -SID "S-1-5-32-544"' in script
    assert "Runner account is a direct member of the local Administrators group" in script
    # Runner identity is discovered or generated; it is no longer a required
    # caller-supplied account name. Preserve the least-privilege role guard.
    assert '$runnerIsAdministrator =' in script
    assert "if ($runnerIsAdministrator)" in script
    assert "Runner account is a direct member of the local Administrators group" in script
    assert "^[A-Za-z0-9_.-]+$" not in script
    assert "runnerLocalName.Length -gt 20" in script


def test_powershell_broker_client_reports_health_payload_without_missing_error_property():
    client = _read("scripts/AutonomousStateBroker.psm1")

    assert '$payload.PSObject.Properties["error"]' in client
    assert '$payload.PSObject.Properties[$fieldName]' in client
    assert 'Health: " + ($healthDetails -join ", ")' in client
    assert '$envelope.payload.error' not in client


def test_broker_health_exposes_only_safe_acl_failure_reason_codes():
    service = _read("scripts/AutonomousStateBrokerService.cs")
    powershell_client = _read("scripts/AutonomousStateBroker.psm1")

    assert '"acl_reason", _lastAclFailureReason' in service
    for reason in (
        "expected_sid_manifest_missing",
        "expected_sid_manifest_invalid",
        "state_entry_owner_unexpected",
        "state_root_owner_not_local_service",
        "state_root_acl_inheritance_enabled",
        "state_entry_ace_count_mismatch",
        "state_entry_ace_rights_or_type_mismatch",
        "state_entry_ace_identity_unexpected_or_duplicate",
        "state_entry_expected_identity_missing",
        "state_tree_reparse_point",
        "state_tree_entry_limit_exceeded",
        "state_acl_scan_failed",
    ):
        assert reason in service
    assert '"acl_reason"' in powershell_client
    assert '"acl_reason"' in powershell_client[powershell_client.index('foreach ($fieldName'):powershell_client.index('$detail = if')]
    health_details = powershell_client[
        powershell_client.index('foreach ($fieldName'):powershell_client.index('$detail = if')
    ]
    assert '"state_directory"' not in health_details
    assert '"state_path"' not in health_details


def test_windows_broker_installer_uses_verified_prebuilt_binary_without_target_compiler():
    installer = _read("scripts/AutonomousStateBrokerInstall.psm1")
    workflow = _read(".github/workflows/tests.yml")

    assert 'Join-Path $PSScriptRoot "bin"' in installer
    assert 'PersonalAIAssistant.StateBroker.exe' in installer
    assert '$hashFile = $packagedExe + ".sha256"' in installer
    assert 'Get-FileHash -LiteralPath $packagedExe -Algorithm SHA256' in installer
    assert 'Get-FileHash -LiteralPath $temporaryExe -Algorithm SHA256' in installer
    assert 'Copy-Item -LiteralPath $packagedExe -Destination $temporaryExe' in installer
    assert 'csc.exe' not in installer
    assert 'Microsoft.NET' not in installer
    assert 'Build and stage the Windows machine state broker' in workflow
    assert 'upload-artifact@v4' in workflow
    assert 'actions/attest-build-provenance@v3' in workflow
    assert 'attestations: write' in workflow
    assert 'windows-state-broker-package' in workflow
    assert 'Exercise the packaged broker under Windows PowerShell Desktop' in workflow
    assert workflow.count('Test-AutonomousDotNetFramework48') >= 2
    assert '$PSVersionTable.PSEdition' in workflow
    live_test = _read("scripts/test_autonomous_state_broker_service.ps1")
    assert 'PersonalAIAssistant.StateBroker.exe' in live_test
    assert 'Get-FileHash -LiteralPath $servicePackage -Algorithm SHA256' in live_test
    assert 'Get-FileHash -LiteralPath $serviceExe -Algorithm SHA256' in live_test
    assert 'Copy-Item -LiteralPath $servicePackage -Destination $serviceExe' in live_test
    assert 'csc.exe' not in live_test
    build_step = workflow.split('Build and stage the Windows machine state broker', 1)[1].split(
        'Upload the prebuilt Windows state broker package', 1
    )[0]
    assert 'dotnet build $project' in build_step
    assert 'csc.exe' not in build_step
    assert '--output $buildRoot' in build_step
    assert '$freshHash' in build_step and '$committedHash' in build_step
    assert 'fresh deterministic broker build differs' in build_step
    assert 'No committed broker package exists yet' in build_step
    assert 'final deterministic SHA-256 verification' in build_step
    assert 'commit-broker-package:' in workflow
    assert 'contents: write' in workflow
    assert 'git push origin' in workflow
    commit_job = workflow.split('commit-broker-package:', 1)[1].split('runtime-bootstrap:', 1)[0]
    assert 'cmp -s' in commit_job
    assert 'already committed; no branch update is needed' in commit_job
    broker_project = _read("scripts/AutonomousStateBrokerService.csproj")
    assert '<TargetFramework>net48</TargetFramework>' in broker_project
    assert '<Deterministic>true</Deterministic>' in broker_project
    assert 'Microsoft.NETFramework.ReferenceAssemblies.net48' in broker_project
    assert 'Version="1.0.3"' in broker_project
    assert '<Reference Include="System.IO.Pipes.AccessControl" />' in broker_project
    sdk_pin = _read("scripts/global.json")
    assert '"version": "11.0.100"' in sdk_pin
    assert 'dotnet-version: 11.0.100' in workflow


def test_broker_setup_preflights_package_before_runner_or_acl_mutation():
    ensure = _read("scripts/ensure_autonomous_state_broker.ps1")
    installer = _read("scripts/AutonomousStateBrokerInstall.psm1")

    package_preflight = ensure.index("$brokerPackage = Get-AutonomousStateBrokerPackage")
    runner_discovery = ensure.index("$runnerServiceCandidates")
    runner_stop = ensure.index("Stop-Service -Name $service.Name")
    state_acl_change = ensure.index("Set-AutonomousDirectoryTreeAcl -Path $stateRoot")

    assert package_preflight < runner_discovery < runner_stop < state_acl_change
    assert "Get-AutonomousStateBrokerPackage" in installer
    assert '"Get-Acl", "Set-Acl", "Get-CimInstance"' in installer
    assert "Get-Command $commandName -ErrorAction Stop" in installer
    assert "Get-Command takeown.exe -ErrorAction Stop" in installer
    assert "Get-Command icacls.exe -ErrorAction Stop" in installer
    assert "Get-Command sc.exe -ErrorAction Stop" in installer
    assert "$serviceSource" not in installer
    assert "AutonomousStateBrokerService.cs is missing" not in installer
    assert "csc.exe" not in installer


def test_broker_setup_rolls_back_acl_and_manifest_when_install_fails():
    ensure = _read("scripts/ensure_autonomous_state_broker.ps1")
    identity = _read("scripts/AutonomousWindowsIdentity.psm1")
    workflow = _read(".github/workflows/tests.yml")

    assert "[switch]$PassThru" in identity
    assert "function Restore-AutonomousDirectoryTreeAcl" in identity
    assert "InstallerSid = $InstallerSid" in identity
    assert "& $takeown.Source /F $entryPath" in identity
    assert "& $icacls.Source $entryPath /grant:r $grant /C /Q" in identity
    assert "Restore-AutonomousDirectoryTreeAcl -Snapshot $aclTransaction" in ensure
    assert "$previousStateModifySids" in ensure
    assert "if (-not $setupCommitted)" in ensure
    assert ensure.index("Install-AutonomousStateBrokerService") < ensure.index("$setupCommitted = $true")
    assert "Transactional ACL snapshot and exact rollback passed." in workflow
    assert "Restore-AutonomousDirectoryTreeAcl -Snapshot $aclSnapshot" in workflow


def test_broker_installer_cleans_staging_and_restores_existing_service_on_stop_failure():
    installer = _read("scripts/AutonomousStateBrokerInstall.psm1")
    install = installer.split("function Install-AutonomousStateBrokerService", 1)[1]

    stage_start = install.index("try {")
    stop_service = install.index("Stop-Service -Name $serviceName", stage_start)
    stage_catch = install.index("} catch {", stop_service)
    cleanup = install.index("Remove-Item -LiteralPath $temporaryExe", stage_catch)

    assert stage_start < stop_service < stage_catch < cleanup
    assert "if ($wasRunning)" in install[stage_catch:cleanup]
    assert "Start-Service -Name $serviceName -ErrorAction SilentlyContinue" in install[stage_catch:cleanup]
    assert "$existingServicePath" in install
    assert "The previous state-broker service configuration" in install
    assert "'binPath=' $binaryPath" in install
    assert '"StartPending"' in install and '"StopPending"' in install
    assert 'refusing to replace its executable' in install


def test_broker_installer_preflights_net48_and_service_control_before_mutation():
    installer = _read("scripts/AutonomousStateBrokerInstall.psm1")
    release_check = installer.index("$frameworkRelease = Get-AutonomousDotNetFrameworkRelease")
    service_root_creation = installer.index("New-Item -ItemType Directory -Path $serviceRoot")
    service_lookup = installer.index("Get-Service -Name $serviceName")

    assert "RegistryView]::Registry64" in installer
    assert "RegistryView]::Registry32" in installer
    assert "$frameworkRelease -lt 528040" in installer
    assert release_check < service_root_creation < service_lookup
    assert "$scCommand = Get-Command sc.exe -ErrorAction Stop" in installer
    assert installer.index("$scCommand = Get-Command sc.exe") < service_root_creation
