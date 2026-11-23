[CmdletBinding()]
param(
    [Parameter(Mandatory=$true)][ValidatePattern("^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")][string]$Repository,
    [string]$RunnerAccount = "",
    [string]$RunnerName = "",
    [string]$RunnerRoot = "",
    [string]$Model = ""
)
$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest
$identity = [Security.Principal.WindowsIdentity]::GetCurrent()
$installerSid = $identity.User.Value
$principal = [Security.Principal.WindowsPrincipal]::new($identity)
if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) { throw "Run from elevated PowerShell." }
if ([string]::IsNullOrWhiteSpace($env:ProgramData) -or -not [System.IO.Path]::IsPathRooted($env:ProgramData)) { throw "PROGRAMDATA must resolve to an absolute shared path." }
Import-Module (Join-Path $PSScriptRoot "AutonomousWindowsIdentity.psm1") -Force
Import-Module (Join-Path $PSScriptRoot "AutonomousWindowsPaths.psm1") -Force
Import-Module (Join-Path $PSScriptRoot "AutonomousOllamaRuntime.psm1") -Force
Import-Module (Join-Path $PSScriptRoot "AutonomousStateBrokerInstall.psm1") -Force

# Discover an existing Runner from its registered Windows service instead of
# assuming a fixed install path or requiring a specific account name.
$runnerServiceCandidates = @(Get-CimInstance Win32_Service -ErrorAction Stop | Where-Object { $_.PathName -match '(?i)RunnerService\.exe' })
$resolvedRunnerServices = @()
foreach ($candidate in $runnerServiceCandidates) {
    $rawPath = [string]$candidate.PathName
    $match = [regex]::Match($rawPath, '^\s*"(?<exe>[^"]+RunnerService\.exe)"')
    if (-not $match.Success) {
        $match = [regex]::Match($rawPath, '^\s*(?<exe>.+?RunnerService\.exe)(?:\s|$)')
    }
    if (-not $match.Success) { continue }
    $executable = [System.IO.Path]::GetFullPath($match.Groups["exe"].Value)
    $root = [System.IO.Path]::GetFullPath((Split-Path -Parent (Split-Path -Parent $executable)))
    if ([string]::IsNullOrWhiteSpace($RunnerRoot) -or $root.Equals([System.IO.Path]::GetFullPath($RunnerRoot), [StringComparison]::OrdinalIgnoreCase)) {
        $resolvedRunnerServices += [pscustomobject]@{ Service = $candidate; Executable = $executable; Root = $root }
    }
}
if ([string]::IsNullOrWhiteSpace($RunnerRoot)) {
    if ($resolvedRunnerServices.Count -gt 1) {
        throw "Multiple Runner services were found. Specify RunnerRoot to select the intended installation."
    }
    if ($resolvedRunnerServices.Count -eq 1) {
        $RunnerRoot = $resolvedRunnerServices[0].Root
    } else {
        $RunnerRoot = Join-Path $env:ProgramData "GitHubActionsRunner"
    }
}
if (-not [System.IO.Path]::IsPathRooted($RunnerRoot)) { throw "RunnerRoot must be an absolute path." }
$RunnerRoot = [System.IO.Path]::GetFullPath($RunnerRoot)
$matchingRunnerServices = @($resolvedRunnerServices | Where-Object { $_.Root.Equals($RunnerRoot, [StringComparison]::OrdinalIgnoreCase) })
if ($matchingRunnerServices.Count -gt 1) { throw "More than one Runner service maps to the selected RunnerRoot." }

if ([string]::IsNullOrWhiteSpace($RunnerAccount)) {
    if ($matchingRunnerServices.Count -eq 1) {
        $RunnerAccount = [string]$matchingRunnerServices[0].Service.StartName
    } else {
        $seed = [System.Text.Encoding]::UTF8.GetBytes(("$env:COMPUTERNAME|$Repository".ToLowerInvariant()))
        $sha = [System.Security.Cryptography.SHA256]::Create()
        try { $suffix = ([BitConverter]::ToString($sha.ComputeHash($seed))).Replace("-", "").Substring(0, 6).ToLowerInvariant() }
        finally { $sha.Dispose() }
        $RunnerAccount = "paa-runner-$suffix"
        Write-Host "No existing Runner service was found; using a generated least-privilege local service identity."
    }
}
$RunnerAccount = $RunnerAccount.Trim()
$runnerIdentity = ConvertTo-AutonomousWindowsAccountName -AccountName $RunnerAccount

if ([string]::IsNullOrWhiteSpace($RunnerName)) {
    $runnerMetadataPath = Join-Path $RunnerRoot ".runner"
    if (Test-Path -LiteralPath $runnerMetadataPath -PathType Leaf) {
        $runnerMetadata = Get-Content -LiteralPath $runnerMetadataPath -Raw | ConvertFrom-Json -ErrorAction Stop
        $RunnerName = [string]$runnerMetadata.agentName
    }
    if ([string]::IsNullOrWhiteSpace($RunnerName)) {
        $RunnerName = "$env:COMPUTERNAME-ollama"
    }
}
if ($RunnerName -notmatch "^[A-Za-z0-9_.-]{1,64}$") { throw "Invalid RunnerName." }
$runnerLocalName = Get-AutonomousLocalAccountName -AccountName $runnerIdentity
$runnerIsLocal = -not [string]::IsNullOrWhiteSpace($runnerLocalName)
if ($runnerLocalName -and ($runnerLocalName.Length -gt 20 -or $runnerLocalName -match '[\\/"\[\]:;|=,+*?<>]')) { throw "Invalid local Runner account name." }
$machineStateDirectory = [Environment]::GetEnvironmentVariable("AUTONOMOUS_ENGINEERING_PROGRESS_DIR", "Machine")
$processStateDirectory = $env:AUTONOMOUS_ENGINEERING_PROGRESS_DIR
if (-not [string]::IsNullOrWhiteSpace($processStateDirectory)) {
    if ([string]::IsNullOrWhiteSpace($machineStateDirectory)) {
        throw "AUTONOMOUS_ENGINEERING_PROGRESS_DIR must be configured at machine scope so all Windows service and application identities share one state root."
    }
    # A machine setting is authoritative for every account; ignore any process override.
}
$progressPath = Resolve-AutonomousStateDirectory -ConfiguredPath $machineStateDirectory -ProgramDataPath $env:ProgramData
$runnerRootBoundary = $RunnerRoot.TrimEnd([char[]]@([System.IO.Path]::DirectorySeparatorChar, [System.IO.Path]::AltDirectorySeparatorChar)) + [System.IO.Path]::DirectorySeparatorChar
$statePathBoundary = $progressPath.TrimEnd([char[]]@([System.IO.Path]::DirectorySeparatorChar, [System.IO.Path]::AltDirectorySeparatorChar)) + [System.IO.Path]::DirectorySeparatorChar
if ($RunnerRoot.Equals($progressPath, [StringComparison]::OrdinalIgnoreCase) -or $progressPath.StartsWith($runnerRootBoundary, [StringComparison]::OrdinalIgnoreCase) -or $RunnerRoot.StartsWith($statePathBoundary, [StringComparison]::OrdinalIgnoreCase)) {
    throw "RunnerRoot and shared autonomous state directory must be separate, non-nested paths."
}
$brokerServiceSid = Get-AutonomousWindowsWellKnownSid -SidType LocalServiceSid

# Node.js/npm and Python are provisioned inside each autonomous workflow job.
# Do not require global copies during machine provisioning.
$git = Get-Command git -ErrorAction SilentlyContinue
if (-not $git) {
    throw "Git for Windows is required by actions/checkout but is not installed or visible in the Runner service PATH. Install the official Git for Windows package, then retry."
}
& $git.Source --version
if ($LASTEXITCODE -ne 0) { throw "Git is present but cannot execute." }

$python = $null
$projectRoot = Split-Path -Parent $PSScriptRoot
$projectPython = Join-Path $projectRoot ".venv\\Scripts\\python.exe"
if (Test-Path -LiteralPath $projectPython -PathType Leaf) {
    $python = Get-Item -LiteralPath $projectPython
} else {
    $pythonCommand = Get-Command python -ErrorAction SilentlyContinue
    if ($pythonCommand) { $python = $pythonCommand }
}
$gh = Get-Command gh -ErrorAction Stop
& $gh.Source auth status --hostname github.com
if ($LASTEXITCODE -ne 0) { throw "Authenticate gh as an account with repository Administration: write permission." }
if ([string]::IsNullOrWhiteSpace($Model)) {
    $Model = (& $gh.Source variable get AUTONOMOUS_ENGINEERING_MODEL --repo $Repository 2>$null | Out-String).Trim()
}
if ([string]::IsNullOrWhiteSpace($Model)) {
    throw "No saved AUTONOMOUS_ENGINEERING_MODEL repository variable exists. Save an installed model in the autonomous-engineering dashboard first."
}
if ($Model -match "(?i)-codex-ctx([0-9]+)?$") {
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
    if (-not $python) {
        throw "Ollama did not publish tool capability metadata and no project Python runtime is available. Start run.py once to prepare the local Python environment, then retry."
    }
    $pythonExecutable = if ($python.PSObject.Properties["Source"]) { $python.Source } else { $python.FullName }
    & $pythonExecutable $toolProbeScript --model $Model
    if ($LASTEXITCODE -ne 0) {
        throw "Saved model $Model failed the live Ollama tool-call probe."
    }
}
function Assert-NoActiveAutonomousWorkflow {
    $runsJson = & $gh.Source api "repos/$Repository/actions/workflows/autonomous-codex-engineering.yml/runs?per_page=100"
    if ($LASTEXITCODE -ne 0 -or [string]::IsNullOrWhiteSpace($runsJson)) {
        throw "Could not inspect autonomous workflow activity; refusing to modify live shared-state permissions."
    }
    $runs = $runsJson | ConvertFrom-Json
    if ($null -eq $runs.workflow_runs -or [int]$runs.total_count -gt @($runs.workflow_runs).Count) {
        throw "Autonomous workflow run inventory is incomplete; refusing to change the shared state ACL."
    }
    $active = @($runs.workflow_runs | Where-Object {
        $_.status -in @("queued", "in_progress", "waiting", "requested", "pending")
    })
    if ($active.Count -gt 0) {
        $ids = ($active | ForEach-Object { [string]$_.id }) -join ", "
        throw "Autonomous workflow runs are active or queued ($ids); stop them before repairing state ACLs or updating the broker."
    }
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


if (Test-Path -LiteralPath (Join-Path $RunnerRoot ".runner")) {
    Assert-NoActiveAutonomousWorkflow
    $services = @($matchingRunnerServices | ForEach-Object { $_.Service })
    if ($services.Count -ne 1 -or (Get-AutonomousWindowsAccountSid -AccountName $services[0].StartName) -ne (Get-AutonomousWindowsAccountSid -AccountName $runnerIdentity)) { throw "Existing Runner configuration found; refusing to replace or reconfigure it." }

    # Existing installations must receive the same least-privilege enforcement as
    # newly provisioned ones. A service keeps its old access token until restarted.
    $administratorsGroup = Get-LocalGroup -SID "S-1-5-32-544"
    $runnerIsDirectAdministrator = @(
        Get-LocalGroupMember -Group $administratorsGroup.Name -ErrorAction Stop |
            Where-Object { $_.SID -and $_.SID.Value -eq (Get-AutonomousWindowsAccountSid -AccountName $runnerIdentity) }
    ).Count -gt 0
    if ($runnerIsDirectAdministrator) {
        Write-Warning "Removing $runnerIdentity from the local Administrators group; autonomous execution requires least privilege."
        if ($services[0].State -eq "Running") {
            Stop-Service -Name $services[0].Name -ErrorAction Stop
            $stoppedService = Get-Service -Name $services[0].Name -ErrorAction Stop
            $stoppedService.WaitForStatus(
                [System.ServiceProcess.ServiceControllerStatus]::Stopped,
                [TimeSpan]::FromSeconds(30)
            )
        }
        Remove-LocalGroupMember -Group $administratorsGroup.Name -Member $runnerIdentity -ErrorAction Stop
        $stillAdministrator = @(
            Get-LocalGroupMember -Group $administratorsGroup.Name -ErrorAction Stop |
                Where-Object { $_.SID -and $_.SID.Value -eq (Get-AutonomousWindowsAccountSid -AccountName $runnerIdentity) }
        ).Count -gt 0
        if ($stillAdministrator) {
            throw "Could not remove $runnerIdentity from the local Administrators group."
        }
        Write-Host "Least-privilege membership corrected. The Runner service will be restarted to discard its previous administrator token."
    }

    $runnerSid = Get-AutonomousWindowsAccountSid -AccountName $runnerIdentity
    New-Item -ItemType Directory -Path $progressPath -Force | Out-Null
    Set-AutonomousDirectoryAcl -Path $RunnerRoot -RunnerSid $runnerSid -AdditionalModifySid $installerSid
    Set-AutonomousDirectoryTreeAcl -Path $progressPath -ModifySids @($runnerSid, $brokerServiceSid) -OwnerSid $brokerServiceSid -InstallerSid $installerSid
    try {
        Ensure-AutonomousStateOwnerLabel
    } finally {
        Set-AutonomousDirectoryAcl -Path $RunnerRoot -RunnerSid $runnerSid
    }
    Install-AutonomousStateBrokerService
    $serviceState = (Get-CimInstance Win32_Service | Where-Object { $_.Name -eq $services[0].Name }).State
    if ($serviceState -ne "Running") { Start-Service -Name $services[0].Name -ErrorAction Stop }
    $service = Get-CimInstance Win32_Service -Filter "Name='$($services[0].Name)'"
    if ($service.State -ne "Running" -or (Get-AutonomousWindowsAccountSid -AccountName $service.StartName) -ne (Get-AutonomousWindowsAccountSid -AccountName $runnerIdentity)) {
        throw "Runner service did not restart under the verified non-administrator identity."
    }
    Write-Host "Existing Runner configuration preserved, uniquely labelled, and verified under $runnerIdentity."
    exit 0
}
if ((Test-Path -LiteralPath $RunnerRoot) -and @(Get-ChildItem -LiteralPath $RunnerRoot -Force).Count -gt 0) { throw "RunnerRoot is non-empty but unconfigured; refusing to overwrite." }
if ($runnerIsLocal) {
    $user = Get-LocalUser -Name $runnerLocalName -ErrorAction SilentlyContinue
    if (-not $user) {
        $password = Read-Host "Set password for $runnerIdentity" -AsSecureString
        if ($password.Length -lt 16) { $password.Dispose(); throw "Use at least 16 characters." }
        New-LocalUser -Name $runnerLocalName -Password $password -PasswordNeverExpires -AccountNeverExpires -Description "Non-admin autonomous engineering Runner" | Out-Null
        $password.Dispose()
    }
    $user = Get-LocalUser -Name $runnerLocalName
    if (-not $user.Enabled) { Enable-LocalUser -Name $runnerLocalName }
} else {
    Write-Host "Using externally managed Runner identity $runnerIdentity; no local account is created or modified."
}
$runnerSid = Get-AutonomousWindowsAccountSid -AccountName $runnerIdentity
$administratorsGroup = Get-LocalGroup -SID "S-1-5-32-544"
$runnerIsAdministrator = @(
    Get-LocalGroupMember -Group $administratorsGroup.Name -ErrorAction Stop |
        Where-Object { $_.SID -and $_.SID.Value -eq $runnerSid }
).Count -gt 0
if ($runnerIsAdministrator) { throw "Runner account is a direct member of the local Administrators group; use a least-privilege identity." }
New-Item -ItemType Directory -Path $RunnerRoot -Force | Out-Null
New-Item -ItemType Directory -Path $progressPath -Force | Out-Null
$installerSid = [Security.Principal.WindowsIdentity]::GetCurrent().User.Value
Set-AutonomousDirectoryAcl -Path $RunnerRoot -RunnerSid $runnerSid -AdditionalModifySid $installerSid
Set-AutonomousDirectoryTreeAcl -Path $progressPath -ModifySids @($runnerSid, $brokerServiceSid) -OwnerSid $brokerServiceSid -InstallerSid $installerSid
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
        Set-AutonomousDirectoryTreeAcl -Path $progressPath -ModifySids @($runnerSid, $brokerServiceSid) -OwnerSid $brokerServiceSid -InstallerSid $installerSid
    }
    $services = @(Get-CimInstance Win32_Service | Where-Object { $_.PathName -like "*$RunnerRoot*" })
    if ($services.Count -ne 1) { throw "Could not locate exactly one Runner service." }
    $actualServiceSid = Get-AutonomousWindowsAccountSid -AccountName $services[0].StartName
    $expectedRunnerSid = Get-AutonomousWindowsAccountSid -AccountName $runnerIdentity
    if ($actualServiceSid -ne $expectedRunnerSid) { throw "Could not verify the Runner service identity." }
    if ($services[0].StartMode -eq "Disabled") { Set-Service -Name $services[0].Name -StartupType Automatic }
    Start-Service -Name $services[0].Name
    if ((Get-Service -Name $services[0].Name).Status -ne "Running") { throw "Runner service did not start." }
    Install-AutonomousStateBrokerService
    Write-Host "Runner registered and running as $runnerIdentity; model $Model verified. The state broker runs as the restricted LocalService identity; app users do not receive filesystem ACLs."
} finally {
    $token = $null
    if ($archive -and (Test-Path -LiteralPath $archive)) { Remove-Item -LiteralPath $archive -Force -ErrorAction SilentlyContinue }
}
