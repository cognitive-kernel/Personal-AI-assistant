[CmdletBinding()]
param(
    [Parameter(Mandatory=$true)][ValidatePattern("^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")][string]$Repository,
    [Parameter(Mandatory=$true)][ValidateNotNullOrEmpty()][string]$RunnerAccount,
    [string]$RunnerName = "$env:COMPUTERNAME-ollama",
    [string]$RunnerRoot = (Join-Path $env:ProgramData "GitHubActionsRunner"),
    [string]$Model = ""
)
$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest
$identity = [Security.Principal.WindowsIdentity]::GetCurrent()
$installerSid = $identity.User.Value
$principal = [Security.Principal.WindowsPrincipal]::new($identity)
if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) { throw "Run from elevated PowerShell." }
$RunnerAccount = $RunnerAccount.Trim()
if ([string]::IsNullOrWhiteSpace($RunnerAccount)) { throw "RunnerAccount must be a non-empty Windows account identity." }
if ($RunnerName -notmatch "^[A-Za-z0-9_.-]{1,64}$") { throw "Invalid RunnerName." }
if ([string]::IsNullOrWhiteSpace($env:ProgramData) -or -not [System.IO.Path]::IsPathRooted($env:ProgramData)) { throw "PROGRAMDATA must resolve to an absolute shared path." }
if (-not [System.IO.Path]::IsPathRooted($RunnerRoot)) { throw "RunnerRoot must be an absolute path." }
$RunnerRoot = [System.IO.Path]::GetFullPath($RunnerRoot)
Import-Module (Join-Path $PSScriptRoot "AutonomousWindowsIdentity.psm1") -Force
Import-Module (Join-Path $PSScriptRoot "AutonomousWindowsPaths.psm1") -Force
Import-Module (Join-Path $PSScriptRoot "AutonomousOllamaRuntime.psm1") -Force
$runnerIdentity = ConvertTo-AutonomousWindowsAccountName -AccountName $RunnerAccount
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
$runnerRootBoundary = $RunnerRoot.TrimEnd([char[]]@([System.IO.Path]::DirectorySeparatorChar, [System.IO.Path]::AltDirectorySeparatorChar)) + [System.IO.Path]::DirectorySeparatorChar
$statePathBoundary = $progressPath.TrimEnd([char[]]@([System.IO.Path]::DirectorySeparatorChar, [System.IO.Path]::AltDirectorySeparatorChar)) + [System.IO.Path]::DirectorySeparatorChar
if ($RunnerRoot.Equals($progressPath, [StringComparison]::OrdinalIgnoreCase) -or $progressPath.StartsWith($runnerRootBoundary, [StringComparison]::OrdinalIgnoreCase) -or $RunnerRoot.StartsWith($statePathBoundary, [StringComparison]::OrdinalIgnoreCase)) {
    throw "RunnerRoot and shared autonomous state directory must be separate, non-nested paths."
}
$brokerServiceSid = "S-1-5-19"
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
if ([string]::IsNullOrWhiteSpace($Model)) {
    $Model = (& $gh.Source variable get AUTONOMOUS_ENGINEERING_MODEL --repo $Repository 2>$null | Out-String).Trim()
}
if ([string]::IsNullOrWhiteSpace($Model)) {
    throw "No saved AUTONOMOUS_ENGINEERING_MODEL repository variable exists. Save an installed model in the autonomous-engineering dashboard first."
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


function Install-AutonomousStateBrokerService {
    $serviceName = "PersonalAIAssistantStateBroker"
    $serviceRoot = Join-Path $env:ProgramData "PersonalAIAssistant\StateBroker"
    $serviceSource = Join-Path $PSScriptRoot "AutonomousStateBrokerService.cs"
    if (-not (Test-Path -LiteralPath $serviceSource -PathType Leaf)) {
        throw "AutonomousStateBrokerService.cs is missing from the provisioning package."
    }
    New-Item -ItemType Directory -Path $serviceRoot -Force | Out-Null
    $serviceExe = Join-Path $serviceRoot "PersonalAIAssistant.StateBroker.exe"
    $temporaryExe = Join-Path $serviceRoot ("broker-build-" + [guid]::NewGuid().ToString("N") + ".exe")

    $frameworkCandidates = @(
        (Join-Path $env:WINDIR "Microsoft.NET\Framework64\v4.0.30319"),
        (Join-Path $env:WINDIR "Microsoft.NET\Framework\v4.0.30319")
    )
    $frameworkRoot = $frameworkCandidates | Where-Object {
        Test-Path -LiteralPath (Join-Path $_ "csc.exe") -PathType Leaf
    } | Select-Object -First 1
    if (-not $frameworkRoot) {
        throw ".NET Framework C# compiler is required to install the local state broker."
    }
    $compiler = Join-Path $frameworkRoot "csc.exe"
    $references = @("System.ServiceProcess.dll", "System.Core.dll", "System.Web.Extensions.dll")
    $pipeAccessAssembly = Join-Path $frameworkRoot "System.IO.Pipes.AccessControl.dll"
    if (Test-Path -LiteralPath $pipeAccessAssembly -PathType Leaf) {
        $references += "System.IO.Pipes.AccessControl.dll"
    }
    $compileArguments = @("/nologo", "/target:exe", "/out:$temporaryExe")
    foreach ($reference in $references) {
        $compileArguments += "/reference:" + (Join-Path $frameworkRoot $reference)
    }
    $compileArguments += $serviceSource
    & $compiler @compileArguments
    if ($LASTEXITCODE -ne 0 -or -not (Test-Path -LiteralPath $temporaryExe -PathType Leaf)) {
        Remove-Item -LiteralPath $temporaryExe -Force -ErrorAction SilentlyContinue
        throw "Could not compile the local Windows state broker; the Runner and existing state were not replaced."
    }

    $existing = Get-Service -Name $serviceName -ErrorAction SilentlyContinue
    $wasRunning = ($null -ne $existing -and $existing.Status -eq "Running")
    if ($wasRunning) {
        Stop-Service -Name $serviceName -Force -ErrorAction Stop
        $existing = Get-Service -Name $serviceName -ErrorAction Stop
        $existing.WaitForStatus(
            [System.ServiceProcess.ServiceControllerStatus]::Stopped,
            [TimeSpan]::FromSeconds(30)
        )
    }
    $backupExe = $null
    $newServiceCreated = $false
    try {
        if (Test-Path -LiteralPath $serviceExe -PathType Leaf) {
            $backupExe = Join-Path $serviceRoot ("broker-previous-" + [guid]::NewGuid().ToString("N") + ".exe")
            [System.IO.File]::Replace($temporaryExe, $serviceExe, $backupExe, $true)
        } else {
            [System.IO.File]::Move($temporaryExe, $serviceExe)
        }
        if (-not $existing) {
            New-Service -Name $serviceName -BinaryPathName ('"' + $serviceExe + '"') -DisplayName "Personal AI Assistant State Broker" -Description "Mediates allow-listed local access to protected autonomous-engineering state." -StartupType Automatic | Out-Null
            $newServiceCreated = $true
        }
        $sc = Get-Command sc.exe -ErrorAction Stop
        $binPathArgument = 'binPath= "' + $serviceExe + '"'
        $serviceAccountArgument = 'obj= "NT AUTHORITY\LocalService"'
        $servicePasswordArgument = 'password= ""'
        & $sc.Source config $serviceName $binPathArgument $serviceAccountArgument $servicePasswordArgument "start= auto" | Out-Null
        if ($LASTEXITCODE -ne 0) {
            throw "Could not configure the state broker to run under its restricted LocalService identity."
        }
        $service = Get-CimInstance Win32_Service -Filter "Name='$serviceName'" -ErrorAction Stop
        if ($service.StartName -ne "NT AUTHORITY\LocalService") {
            throw "The state broker must run under NT AUTHORITY\LocalService."
        }
        Start-Service -Name $serviceName -ErrorAction Stop
        $running = Get-Service -Name $serviceName -ErrorAction Stop
        $running.WaitForStatus(
            [System.ServiceProcess.ServiceControllerStatus]::Running,
            [TimeSpan]::FromSeconds(30)
        )

        Import-Module (Join-Path $PSScriptRoot "AutonomousStateBroker.psm1") -Force
        $health = Invoke-AutonomousStateBroker -Method GET -Path "/v1/health" -TimeoutMilliseconds 5000
        if ($null -eq $health -or $health.ready -ne $true -or $health.schema_version -ne 1) {
            throw "The state broker service started but failed its protected storage health check."
        }
        if ($backupExe -and (Test-Path -LiteralPath $backupExe)) {
            Remove-Item -LiteralPath $backupExe -Force -ErrorAction Stop
        }
        Write-Host "Machine state broker installed and verified at $serviceRoot."
    } catch {
        $failure = $_
        $serviceAfterFailure = Get-Service -Name $serviceName -ErrorAction SilentlyContinue
        if ($serviceAfterFailure -and $serviceAfterFailure.Status -eq "Running") {
            Stop-Service -Name $serviceName -Force -ErrorAction SilentlyContinue
        }
        if ($backupExe -and (Test-Path -LiteralPath $backupExe)) {
            try {
                [System.IO.File]::Replace($backupExe, $serviceExe, $null, $true)
            } catch {
                Write-Warning "The previous state-broker binary backup remains at $backupExe; do not delete it until the service is restored."
            }
        } elseif ($newServiceCreated) {
            Remove-Item -LiteralPath $serviceExe -Force -ErrorAction SilentlyContinue
            sc.exe delete $serviceName | Out-Null
        }
        if ($wasRunning) {
            Start-Service -Name $serviceName -ErrorAction SilentlyContinue
        }
        if (Test-Path -LiteralPath $temporaryExe) {
            Remove-Item -LiteralPath $temporaryExe -Force -ErrorAction SilentlyContinue
        }
        throw $failure
    }
}

if (Test-Path -LiteralPath (Join-Path $RunnerRoot ".runner")) {
    Assert-NoActiveAutonomousWorkflow
    $services = @(Get-CimInstance Win32_Service | Where-Object { $_.PathName -like "*$RunnerRoot*" })
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
