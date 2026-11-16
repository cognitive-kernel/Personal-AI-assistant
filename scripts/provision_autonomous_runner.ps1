[CmdletBinding()]
param(
    [Parameter(Mandatory=$true)][ValidatePattern("^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")][string]$Repository,
    [Parameter()][ValidateNotNullOrEmpty()][string]$RunnerAccount,
    [Parameter()][ValidateNotNullOrEmpty()][string]$AppAccount,
    [string]$RunnerName = "",
    [string]$RunnerRoot = "",
    [string]$Model = "",
    [switch]$VerifyModel
)
$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest
$identity = [Security.Principal.WindowsIdentity]::GetCurrent()
$principal = [Security.Principal.WindowsPrincipal]::new($identity)
if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) { throw "Run from elevated PowerShell." }
$AppAccount = if ([string]::IsNullOrWhiteSpace($AppAccount)) { $identity.Name } else { $AppAccount.Trim() }
if ([string]::IsNullOrWhiteSpace($AppAccount)) { throw "The application identity could not be resolved from the installer process." }
if ([string]::IsNullOrWhiteSpace($env:ProgramData) -or -not [System.IO.Path]::IsPathRooted($env:ProgramData)) { throw "PROGRAMDATA must resolve to an absolute shared path." }
Import-Module (Join-Path $PSScriptRoot "AutonomousWindowsIdentity.psm1") -Force
Import-Module (Join-Path $PSScriptRoot "AutonomousWindowsPaths.psm1") -Force
Import-Module (Join-Path $PSScriptRoot "AutonomousOllamaRuntime.psm1") -Force

if ([string]::IsNullOrWhiteSpace($RunnerRoot)) {
    $RunnerRoot = [Environment]::GetEnvironmentVariable("AUTONOMOUS_ENGINEERING_RUNNER_ROOT", "Machine")
}
if ([string]::IsNullOrWhiteSpace($RunnerRoot)) {
    $installedServices = @(Get-CimInstance Win32_Service -ErrorAction Stop | Where-Object {
        $_.PathName -match '(?i)(^|[\\/])RunnerService\.exe(?:\s|$|")'
    })
    if ($installedServices.Count -gt 1) {
        throw "Multiple GitHub Runner services were found. Supply -RunnerRoot to select the intended installation without changing any service."
    }
    if ($installedServices.Count -eq 1) {
        $exeMatch = [regex]::Match([string]$installedServices[0].PathName, '(?i)^"?(?<exe>.*?RunnerService\.exe)"?(?:\s|$)')
        if (-not $exeMatch.Success) { throw "Could not safely resolve the installed Runner service executable path." }
        $serviceBin = Split-Path -Parent $exeMatch.Groups["exe"].Value
        $RunnerRoot = if ((Split-Path -Leaf $serviceBin) -ieq "bin") { Split-Path -Parent $serviceBin } else { $serviceBin }
    } else {
        $RunnerRoot = Join-Path $env:ProgramData "GitHubActionsRunner"
    }
}
if (-not [System.IO.Path]::IsPathRooted($RunnerRoot)) { throw "RunnerRoot must be an absolute path." }
$RunnerRoot = [System.IO.Path]::GetFullPath($RunnerRoot)

$existingRunnerConfiguration = Test-Path -LiteralPath (Join-Path $RunnerRoot ".runner")
if (-not $existingRunnerConfiguration -and (Test-Path -LiteralPath $RunnerRoot) -and @(Get-ChildItem -LiteralPath $RunnerRoot -Force).Count -gt 0) {
    throw "RunnerRoot is non-empty but unconfigured; refusing to overwrite or create a service account for it."
}
if ([string]::IsNullOrWhiteSpace($RunnerName)) {
    if ($existingRunnerConfiguration) {
        $runnerConfig = Get-Content -LiteralPath (Join-Path $RunnerRoot ".runner") -Raw | ConvertFrom-Json
        $RunnerName = [string]$runnerConfig.agentName
        if ([string]::IsNullOrWhiteSpace($RunnerName)) { throw "The existing Runner name could not be resolved from its configuration." }
    } else {
        $RunnerName = "$env:COMPUTERNAME-ollama"
    }
}
if ($RunnerName -notmatch "^[A-Za-z0-9_.-]{1,64}$") { throw "Invalid RunnerName." }
if ([string]::IsNullOrWhiteSpace($RunnerAccount)) {
    if ($existingRunnerConfiguration) {
        $discoveredService = Get-AutonomousRunnerService -RunnerRoot $RunnerRoot
        $RunnerAccount = $discoveredService.StartName
        Write-Host "Discovered existing Runner service identity: $RunnerAccount"
    } else {
        $generatedLocalName = [guid]::NewGuid().ToString("N").Substring(0, 19)
        $RunnerAccount = "$env:COMPUTERNAME\$generatedLocalName"
        Write-Host "Generated a unique non-administrator Runner service identity: $RunnerAccount"
    }
} else {
    $RunnerAccount = $RunnerAccount.Trim()
}
$runnerIdentity = ConvertTo-AutonomousWindowsAccountName -AccountName $RunnerAccount
$appIdentity = ConvertTo-AutonomousWindowsAccountName -AccountName $AppAccount
$runnerLocalName = Get-AutonomousLocalAccountName -AccountName $runnerIdentity
$runnerIsLocal = -not [string]::IsNullOrWhiteSpace($runnerLocalName)
if ($runnerLocalName -and ($runnerLocalName.Length -gt 20 -or $runnerLocalName -match '[\\/"\[\]:;|=,+*?<>]')) { throw "Invalid local Runner account name." }
$machineStateDirectory = [Environment]::GetEnvironmentVariable("AUTONOMOUS_ENGINEERING_PROGRESS_DIR", "Machine")
$processStateDirectory = $env:AUTONOMOUS_ENGINEERING_PROGRESS_DIR
if (-not [string]::IsNullOrWhiteSpace($processStateDirectory)) {
    if ([string]::IsNullOrWhiteSpace($machineStateDirectory)) {
        throw "AUTONOMOUS_ENGINEERING_PROGRESS_DIR must be configured at machine scope so all Windows service and application identities share one state root."
    }
    $processStatePath = [System.IO.Path]::GetFullPath([Environment]::ExpandEnvironmentVariables($processStateDirectory.Trim()))
    $machineStatePath = [System.IO.Path]::GetFullPath([Environment]::ExpandEnvironmentVariables($machineStateDirectory.Trim()))
    if (-not $processStatePath.Equals($machineStatePath, [StringComparison]::OrdinalIgnoreCase)) {
        throw "Process and machine AUTONOMOUS_ENGINEERING_PROGRESS_DIR values differ; refusing per-account state divergence."
    }
}
$progressPath = Resolve-AutonomousStateDirectory -ConfiguredPath $machineStateDirectory -ProgramDataPath $env:ProgramData
if ([string]::IsNullOrWhiteSpace($machineStateDirectory)) {
    $machineStateDirectory = $progressPath
}
$runnerRootBoundary = $RunnerRoot.TrimEnd([char[]]@([System.IO.Path]::DirectorySeparatorChar, [System.IO.Path]::AltDirectorySeparatorChar)) + [System.IO.Path]::DirectorySeparatorChar
$statePathBoundary = $progressPath.TrimEnd([char[]]@([System.IO.Path]::DirectorySeparatorChar, [System.IO.Path]::AltDirectorySeparatorChar)) + [System.IO.Path]::DirectorySeparatorChar
if ($RunnerRoot.Equals($progressPath, [StringComparison]::OrdinalIgnoreCase) -or $progressPath.StartsWith($runnerRootBoundary, [StringComparison]::OrdinalIgnoreCase) -or $RunnerRoot.StartsWith($statePathBoundary, [StringComparison]::OrdinalIgnoreCase)) {
    throw "RunnerRoot and shared autonomous state directory must be separate, non-nested paths."
}
$appSid = if ($AppAccount -eq $identity.Name) { $identity.User.Value } else { Get-AutonomousWindowsAccountSid -AccountName $appIdentity }
$node = Get-Command node -ErrorAction Stop
$npm = Get-Command npm -ErrorAction Stop
$git = Get-Command git -ErrorAction Stop
$python = Get-Command python -ErrorAction Stop
& $node.Source --version
if ($LASTEXITCODE -ne 0) { throw "Node.js is not executable." }
& $npm.Source --version
if ($LASTEXITCODE -ne 0) { throw "npm is not executable." }
& $git.Source --version
if ($LASTEXITCODE -ne 0) { throw "Git is not executable." }
$pythonVersion = (& $python.Source -c "import sys; print(sys.version_info.major, sys.version_info.minor, sep=chr(46))")
if ($LASTEXITCODE -ne 0 -or [version]$pythonVersion -lt [version]"3.11") { throw "Python 3.11 or newer is required on the Runner host." }
$gh = Get-Command gh -ErrorAction Stop
& $gh.Source auth status --hostname github.com
if ($LASTEXITCODE -ne 0) { throw "Authenticate gh as an account with repository Administration: write permission." }
$shouldVerifyModel = $VerifyModel -or -not [string]::IsNullOrWhiteSpace($Model)
if ($shouldVerifyModel) {
    if ([string]::IsNullOrWhiteSpace($Model)) {
        $Model = (& $gh.Source variable get AUTONOMOUS_ENGINEERING_MODEL --repo $Repository 2>$null | Out-String).Trim()
    }
    if ([string]::IsNullOrWhiteSpace($Model)) {
        throw "Model verification was requested, but no saved AUTONOMOUS_ENGINEERING_MODEL exists."
    }
    if ($Model -match "(?i)-codex-ctx[0-9]+$") {
        throw "The saved model is an internal Codex context variant. Save the original Ollama model in the dashboard."
    }
    $ollamaModels = @(Get-AutonomousOllamaModelCatalog -TimeoutSeconds 8)
    if (-not (Test-AutonomousOllamaModelInstalled -Model $Model -Catalog $ollamaModels)) {
        throw "Saved model $Model is not installed in the local Ollama API. Install it through the configured Ollama server, then save it in the dashboard."
    }
    $modelInfo = Invoke-RestMethod -Uri "http://127.0.0.1:11434/api/show" -Method Post -ContentType "application/json" -Body (@{ model = $Model; verbose = $true } | ConvertTo-Json -Compress) -TimeoutSec 15 -MaximumRedirection 0
    $architecture = [string]$modelInfo.details.family
    $contextKey = $architecture + ".context_length"
    $rawContext = $modelInfo.model_info.PSObject.Properties[$contextKey]
    if (-not $rawContext) {
        $declaredArchitecture = $modelInfo.model_info.PSObject.Properties["general.architecture"]
        if ($declaredArchitecture) {
            $architecture = [string]$declaredArchitecture.Value
            $contextKey = $architecture + ".context_length"
            $rawContext = $modelInfo.model_info.PSObject.Properties[$contextKey]
        }
    }
    $parsedContext = 0L
    if ([string]::IsNullOrWhiteSpace($architecture) -or -not $rawContext -or -not [int64]::TryParse([string]$rawContext.Value, [ref]$parsedContext) -or $parsedContext -le 0) {
        throw "Saved model $Model must expose a positive architecture-specific context length for Codex."
    }
    $capabilitiesProperty = $modelInfo.PSObject.Properties["capabilities"]
    if ($capabilitiesProperty -and $null -ne $capabilitiesProperty.Value) {
        if (@($capabilitiesProperty.Value | ForEach-Object { [string]$_ }) -notcontains "tools") {
            throw "Saved model $Model explicitly does not advertise Ollama tools capability."
        }
    } else {
        $toolProbeScript = Join-Path $PSScriptRoot "verify_ollama_tools.py"
        & $python.Source $toolProbeScript --model $Model
        if ($LASTEXITCODE -ne 0) {
            throw "Saved model $Model failed the live Ollama tool-call probe."
        }
    }
    
} else {
    Write-Host "Ollama validation is decoupled from infrastructure provisioning. Runner and shared storage will be prepared without requiring a running server or selected model."
}

if ($runnerIsLocal) {
    $user = Get-LocalUser -Name $runnerLocalName -ErrorAction SilentlyContinue
    if (-not $user) {
        $password = Read-Host "Set password for the generated or selected Runner service identity $runnerIdentity" -AsSecureString
        if ($password.Length -lt 16) { $password.Dispose(); throw "Use at least 16 characters." }
        New-LocalUser -Name $runnerLocalName -Password $password -PasswordNeverExpires -AccountNeverExpires -Description "Non-admin autonomous engineering Runner" | Out-Null
        $password.Dispose()
    }
    $user = Get-LocalUser -Name $runnerLocalName
    if (-not $user.Enabled) { Enable-LocalUser -Name $runnerLocalName }
} else {
    Write-Host "Using externally managed Runner identity $runnerIdentity; no local account is created or modified."
}
$runnerSidForComparison = Get-AutonomousWindowsAccountSid -AccountName $runnerIdentity
if ($appSid -eq $runnerSidForComparison) {
    throw "The application and Runner must resolve to different Windows identities."
}

function Ensure-AutonomousStateOwnerLabel {
    $inventoryJson = & $gh.Source api "repos/$Repository/actions/runners?per_page=100"
    if ($LASTEXITCODE -ne 0 -or [string]::IsNullOrWhiteSpace($inventoryJson)) {
        throw "Could not inspect repository Runner inventory; refusing to assume local durable-state affinity."
    }
    $inventory = $inventoryJson | ConvertFrom-Json
    if ($null -eq $inventory.runners -or [int]$inventory.total_count -gt @($inventory.runners).Count) {
        throw "Runner inventory is incomplete; cannot prove that autonomous-state-owner is unique."
    }
    $matching = @($inventory.runners | Where-Object { $_.name -eq $RunnerName })
    if ($matching.Count -ne 1) {
        throw "Expected exactly one registered Runner named '$RunnerName'; found $($matching.Count)."
    }
    $runner = $matching[0]
    $owners = @($inventory.runners | Where-Object {
        @($_.labels | ForEach-Object { $_.name }) -contains "autonomous-state-owner"
    })
    if (@($owners | Where-Object { $_.id -ne $runner.id }).Count -gt 0) {
        throw "Another Runner already owns the autonomous-state-owner label; refusing ambiguous state affinity."
    }
    if (@($runner.labels | ForEach-Object { $_.name }) -notcontains "autonomous-state-owner") {
        $null = & $gh.Source api --method POST "repos/$Repository/actions/runners/$($runner.id)/labels" -f "labels[]=autonomous-state-owner"
        if ($LASTEXITCODE -ne 0) { throw "Could not assign the unique autonomous-state-owner Runner label." }
    }
    $verifiedJson = & $gh.Source api "repos/$Repository/actions/runners?per_page=100"
    if ($LASTEXITCODE -ne 0 -or [string]::IsNullOrWhiteSpace($verifiedJson)) {
        throw "Could not verify autonomous-state-owner Runner label assignment."
    }
    $verified = $verifiedJson | ConvertFrom-Json
    $owners = @($verified.runners | Where-Object {
        @($_.labels | ForEach-Object { $_.name }) -contains "autonomous-state-owner"
    })
    if ($owners.Count -ne 1 -or $owners[0].id -ne $runner.id) {
        throw "autonomous-state-owner must identify exactly the configured Runner and no other host."
    }
}
[Environment]::SetEnvironmentVariable("AUTONOMOUS_ENGINEERING_PROGRESS_DIR", $machineStateDirectory, "Machine")
[Environment]::SetEnvironmentVariable("AUTONOMOUS_ENGINEERING_RUNNER_ROOT", $RunnerRoot, "Machine")


if (Test-Path -LiteralPath (Join-Path $RunnerRoot ".runner")) {
    $service = Get-AutonomousRunnerService -RunnerRoot $RunnerRoot
    $services = @($service)
    if ((Get-AutonomousWindowsAccountSid -AccountName $service.StartName) -ne (Get-AutonomousWindowsAccountSid -AccountName $runnerIdentity)) {
        throw "Existing Runner configuration found, but its actual Windows service identity differs from the requested identity; no service or account membership was changed."
    }

    $administratorsSid = [Security.Principal.SecurityIdentifier]::new([Security.Principal.WellKnownSidType]::BuiltinAdministratorsSid, $null)
    $administratorsGroup = Get-LocalGroup -SID $administratorsSid
    $runnerIsDirectAdministrator = @(
        Get-LocalGroupMember -Group $administratorsGroup.Name -ErrorAction Stop |
            Where-Object { $_.SID -and $_.SID.Value -eq (Get-AutonomousWindowsAccountSid -AccountName $service.StartName) }
    ).Count -gt 0
    if ($runnerIsDirectAdministrator) {
        throw "The existing Runner service identity is a local administrator. The provisioner will not change account or group membership; configure a dedicated non-administrator service identity."
    }

    $runnerIdentity = ConvertTo-AutonomousWindowsAccountName -AccountName $service.StartName
    $runnerSid = Get-AutonomousWindowsAccountSid -AccountName $runnerIdentity
    $installerSid = [Security.Principal.WindowsIdentity]::GetCurrent().User.Value
    New-Item -ItemType Directory -Path $progressPath -Force | Out-Null
    Set-AutonomousDirectoryAcl -Path $RunnerRoot -RunnerSid $runnerSid -AdditionalModifySid $installerSid
    $runnerTempPath = Join-Path $RunnerRoot "_work\_temp"
    New-Item -ItemType Directory -Path $runnerTempPath -Force | Out-Null
    Set-AutonomousDirectoryAcl -Path $runnerTempPath -RunnerSid $runnerSid -AdditionalModifySid $appSid
    $runnerDiagnosticPath = Join-Path $RunnerRoot "_diag"
    New-Item -ItemType Directory -Path $runnerDiagnosticPath -Force | Out-Null
    Set-AutonomousDirectoryAcl -Path $runnerDiagnosticPath -RunnerSid $runnerSid -AdditionalModifySid $appSid
    [Environment]::SetEnvironmentVariable("AUTONOMOUS_ENGINEERING_RUNNER_TEMP", $runnerTempPath, "Machine")
    Set-AutonomousStateDirectoryAcl -Path $progressPath -RunnerSid $runnerSid -AdditionalModifySids @($appSid, $installerSid)
    try {
        Ensure-AutonomousStateOwnerLabel
    } finally {
        Set-AutonomousDirectoryAcl -Path $RunnerRoot -RunnerSid $runnerSid
        Set-AutonomousStateDirectoryAcl -Path $progressPath -RunnerSid $runnerSid -AdditionalModifySids $appSid
    }
    $serviceState = (Get-CimInstance Win32_Service | Where-Object { $_.Name -eq $services[0].Name }).State
    if ($serviceState -ne "Running") { Start-Service -Name $services[0].Name -ErrorAction Stop }
    $service = Get-CimInstance Win32_Service -Filter "Name='$($services[0].Name)'"
    if ($service.State -ne "Running" -or (Get-AutonomousWindowsAccountSid -AccountName $service.StartName) -ne (Get-AutonomousWindowsAccountSid -AccountName $runnerIdentity)) {
        throw "Runner service did not restart under the verified non-administrator identity."
    }
    Write-Host "Existing Runner configuration preserved, uniquely labelled, and verified under $runnerIdentity."
    exit 0
}
$runnerSid = Get-AutonomousWindowsAccountSid -AccountName $runnerIdentity
$administratorsSid = [Security.Principal.SecurityIdentifier]::new([Security.Principal.WellKnownSidType]::BuiltinAdministratorsSid, $null)
    $administratorsGroup = Get-LocalGroup -SID $administratorsSid
$runnerIsAdministrator = @(
    Get-LocalGroupMember -Group $administratorsGroup.Name -ErrorAction Stop |
        Where-Object { $_.SID -and $_.SID.Value -eq $runnerSid }
).Count -gt 0
if ($runnerIsAdministrator) { throw "Runner account is a direct member of the local Administrators group; use a least-privilege identity." }
New-Item -ItemType Directory -Path $RunnerRoot -Force | Out-Null
New-Item -ItemType Directory -Path $progressPath -Force | Out-Null
$runnerTempPath = Join-Path $RunnerRoot "_work\_temp"
New-Item -ItemType Directory -Path $runnerTempPath -Force | Out-Null
$runnerDiagnosticPath = Join-Path $RunnerRoot "_diag"
New-Item -ItemType Directory -Path $runnerDiagnosticPath -Force | Out-Null
$installerSid = [Security.Principal.WindowsIdentity]::GetCurrent().User.Value
Set-AutonomousDirectoryAcl -Path $RunnerRoot -RunnerSid $runnerSid -AdditionalModifySid $installerSid
Set-AutonomousDirectoryAcl -Path $runnerTempPath -RunnerSid $runnerSid -AdditionalModifySid $appSid
Set-AutonomousDirectoryAcl -Path $runnerDiagnosticPath -RunnerSid $runnerSid -AdditionalModifySid $appSid
[Environment]::SetEnvironmentVariable("AUTONOMOUS_ENGINEERING_RUNNER_TEMP", $runnerTempPath, "Machine")
Set-AutonomousStateDirectoryAcl -Path $progressPath -RunnerSid $runnerSid -AdditionalModifySids $appSid
$token = (& $gh.Source api --method POST "repos/$Repository/actions/runners/registration-token" --jq ".token")
if ($LASTEXITCODE -ne 0 -or [string]::IsNullOrWhiteSpace($token)) { throw "Could not obtain registration token; verify Administration: write permission." }
$archive = $null
try {
    $release = Invoke-RestMethod -Uri "https://api.github.com/repos/actions/runner/releases/latest" -Headers @{ Accept="application/vnd.github+json"; "X-GitHub-Api-Version"="2022-11-28" } -TimeoutSec 30
    $asset = @($release.assets | Where-Object { $_.name -match "^actions-runner-win-x64-[0-9.]+\.zip$" }) | Select-Object -First 1
    if (-not $asset -or $asset.digest -notmatch "^sha256:[0-9a-fA-F]{64}$") { throw "Latest official Runner asset has no verifiable SHA-256 digest." }
    $archive = Join-Path $env:TEMP ("actions-runner-" + [guid]::NewGuid().ToString("N") + ".zip")
    Invoke-WebRequest -Uri $asset.browser_download_url -OutFile $archive -TimeoutSec 180
    $hash = (Get-FileHash -LiteralPath $archive -Algorithm SHA256).Hash.ToLowerInvariant()
    if ($hash -ne $asset.digest.Substring(7).ToLowerInvariant()) { throw "Runner archive digest mismatch." }
    Expand-Archive -LiteralPath $archive -DestinationPath $RunnerRoot -Force
    Remove-Item -LiteralPath $archive -Force
    $config = Join-Path $RunnerRoot "config.cmd"
    if (-not (Test-Path -LiteralPath $config)) { throw "config.cmd missing from official Runner archive." }
    $configArgs = @("--url", "https://github.com/$Repository", "--token", $token, "--name", $RunnerName, "--labels", "ollama,autonomous-state-owner", "--work", "_work", "--runasservice", "--windowslogonaccount", $runnerIdentity)
    Write-Host "Runner configuration will prompt for the service-account password; it is not passed as a process argument."
    try {
        & $config @configArgs
        if ($LASTEXITCODE -ne 0) { throw "Runner registration failed with exit code $LASTEXITCODE." }
    } finally {
        # Keep the elevated installer principal temporary; the installed service
        # retains access through its configured Runner SID.
        Set-AutonomousDirectoryAcl -Path $RunnerRoot -RunnerSid $runnerSid
        Set-AutonomousStateDirectoryAcl -Path $progressPath -RunnerSid $runnerSid -AdditionalModifySids $appSid
    }
    $services = @(Get-CimInstance Win32_Service | Where-Object { $_.PathName -like "*$RunnerRoot*" })
    if ($services.Count -ne 1) { throw "Could not locate exactly one Runner service." }
    $actualServiceSid = Get-AutonomousWindowsAccountSid -AccountName $services[0].StartName
    $expectedRunnerSid = Get-AutonomousWindowsAccountSid -AccountName $runnerIdentity
    if ($actualServiceSid -ne $expectedRunnerSid) { throw "Could not verify the Runner service identity." }
    if ($services[0].StartMode -eq "Disabled") { Set-Service -Name $services[0].Name -StartupType Automatic }
    Start-Service -Name $services[0].Name
    if ((Get-Service -Name $services[0].Name).Status -ne "Running") { throw "Runner service did not start." }
    Write-Host "Runner registered and running as $runnerIdentity. Shared storage and service identity were configured; model readiness is reported separately by the dashboard."
} finally {
    $token = $null
    if ($archive -and (Test-Path -LiteralPath $archive)) { Remove-Item -LiteralPath $archive -Force -ErrorAction SilentlyContinue }
}
