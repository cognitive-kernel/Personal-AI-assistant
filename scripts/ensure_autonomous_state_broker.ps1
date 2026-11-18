[CmdletBinding()]
param(
    [switch]$ElevatedChild,
    [string]$RunnerRoot = ""
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$identity = [Security.Principal.WindowsIdentity]::GetCurrent()
$principal = [Security.Principal.WindowsPrincipal]::new($identity)
if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    if ($ElevatedChild) {
        throw "The elevated state-broker setup process is not running as an administrator."
    }
    $scriptPath = $PSCommandPath
    $argumentLine = '-NoProfile -ExecutionPolicy Bypass -File "' + $scriptPath + '" -ElevatedChild'
    if (-not [string]::IsNullOrWhiteSpace($RunnerRoot)) {
        $argumentLine += ' -RunnerRoot "' + $RunnerRoot.Replace('"', '""') + '"'
    }
    try {
        $child = Start-Process -FilePath (Join-Path $PSHOME "powershell.exe") -Verb RunAs -ArgumentList $argumentLine -Wait -PassThru -ErrorAction Stop
        exit $child.ExitCode
    } catch {
        Write-Error "Administrator approval is required to install the machine state broker. $($_.Exception.Message)"
        exit 1
    }
}

$scriptRoot = Split-Path -Parent $PSCommandPath
Import-Module (Join-Path $scriptRoot "AutonomousWindowsIdentity.psm1") -Force
Import-Module (Join-Path $scriptRoot "AutonomousWindowsPaths.psm1") -Force
Import-Module (Join-Path $scriptRoot "AutonomousStateBroker.psm1") -Force
Import-Module (Join-Path $scriptRoot "AutonomousStateBrokerInstall.psm1") -Force

$serviceName = "PersonalAIAssistantStateBroker"
$brokerServiceSid = "S-1-5-19"

# Do not touch ACLs or stop services when a Runner job is active.
$activeWorkers = @(Get-CimInstance Win32_Process -ErrorAction Stop | Where-Object { $_.Name -ieq "Runner.Worker.exe" })
if ($activeWorkers.Count -gt 0) {
    throw "A GitHub Runner job is currently executing. State-broker setup was deferred without changing ACLs or services."
}

$brokerService = Get-Service -Name $serviceName -ErrorAction SilentlyContinue
if ($brokerService -and $brokerService.Status -eq "Running") {
    try {
        $health = Invoke-AutonomousStateBroker -Method GET -Path "/v1/health" -TimeoutMilliseconds 5000
        if ($health.ready -eq $true -and $health.schema_version -eq 1) {
            Write-Host "Machine state broker is already installed and healthy."
            exit 0
        }
    } catch {
        Write-Warning "The installed broker is not healthy; attempting an idempotent repair."
    }
}

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
        $resolvedRunnerServices += [pscustomobject]@{
            Service = $candidate
            Executable = $executable
            Root = $root
        }
    }
}

if (-not [string]::IsNullOrWhiteSpace($RunnerRoot) -and $resolvedRunnerServices.Count -eq 0) {
    throw "No installed Runner service matches the supplied RunnerRoot. No state permissions were changed."
}
if ($resolvedRunnerServices.Count -ne 1) {
    throw "Expected exactly one installed GitHub Runner service for this machine; found $($resolvedRunnerServices.Count). Specify RunnerRoot only when multiple Runner services are installed."
}

$runner = $resolvedRunnerServices[0]
if (-not (Test-Path -LiteralPath (Join-Path $runner.Root ".runner") -PathType Leaf)) {
    throw "The discovered Runner service has no .runner registration metadata. No state permissions were changed."
}
$runnerSid = Get-AutonomousWindowsAccountSid -AccountName $runner.Service.StartName

$machineStateDirectory = [Environment]::GetEnvironmentVariable("AUTONOMOUS_ENGINEERING_PROGRESS_DIR", "Machine")
$processStateDirectory = $env:AUTONOMOUS_ENGINEERING_PROGRESS_DIR
if (-not [string]::IsNullOrWhiteSpace($processStateDirectory)) {
    if ([string]::IsNullOrWhiteSpace($machineStateDirectory)) {
        throw "AUTONOMOUS_ENGINEERING_PROGRESS_DIR is process/user-scoped; configure it at machine scope before broker setup."
    }
    $processPath = [System.IO.Path]::GetFullPath([Environment]::ExpandEnvironmentVariables($processStateDirectory.Trim()))
    $machinePath = [System.IO.Path]::GetFullPath([Environment]::ExpandEnvironmentVariables($machineStateDirectory.Trim()))
    if (-not $processPath.Equals($machinePath, [StringComparison]::OrdinalIgnoreCase)) {
        throw "The current process state path differs from the machine state path. No ACLs were changed."
    }
}
$stateRoot = Resolve-AutonomousStateDirectory -ConfiguredPath $machineStateDirectory -ProgramDataPath $env:ProgramData
New-Item -ItemType Directory -Path $stateRoot -Force | Out-Null

$installerSid = [Security.Principal.WindowsIdentity]::GetCurrent().User.Value
Set-AutonomousDirectoryTreeAcl -Path $stateRoot -ModifySids @($runnerSid, $brokerServiceSid) -OwnerSid $brokerServiceSid -InstallerSid $installerSid

Install-AutonomousStateBrokerService

$verified = Get-Service -Name $serviceName -ErrorAction Stop
if ($verified.Status -ne "Running") {
    throw "The state broker service did not reach Running state."
}
$health = Invoke-AutonomousStateBroker -Method GET -Path "/v1/health" -TimeoutMilliseconds 5000
if ($health.ready -ne $true -or $health.schema_version -ne 1) {
    throw "The installed state broker did not pass its health check."
}

Write-Host "Machine state broker is installed and healthy."
Write-Host "Runner service identity: $($runner.Service.StartName)"
Write-Host "Runner installation root: $($runner.Root)"
Write-Host "Shared state root: $stateRoot"
