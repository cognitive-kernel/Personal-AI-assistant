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

# The machine broker is a core application prerequisite. A GitHub Runner is an
# optional execution capability and must never be required to install the broker.
# Discover every installed Runner service by its registered executable path; do
# not inspect .runner because its ACL may intentionally deny the app/admin account.
$runnerServiceCandidates = @(Get-CimInstance Win32_Service -ErrorAction Stop | Where-Object {
    $_.PathName -match '(?i)RunnerService\.exe'
})
$allRunnerServices = @()
$resolvedRunnerServices = @()
foreach ($candidate in $runnerServiceCandidates) {
    $rawPath = [string]$candidate.PathName
    $match = [regex]::Match($rawPath, '^\s*"(?<exe>[^"]+RunnerService\.exe)"')
    if (-not $match.Success) {
        $match = [regex]::Match($rawPath, '^\s*(?<exe>.+?RunnerService\.exe)(?:\s|$)')
    }
    if (-not $match.Success) {
        throw "Could not safely resolve an installed Runner service executable path; refusing to rewrite shared-state ACLs."
    }
    $executable = [System.IO.Path]::GetFullPath($match.Groups["exe"].Value)
    $root = [System.IO.Path]::GetFullPath((Split-Path -Parent (Split-Path -Parent $executable)))
    $runnerSid = Get-AutonomousWindowsAccountSid -AccountName $candidate.StartName
    $discoveredRunner = [pscustomobject]@{
        Service = $candidate
        Executable = $executable
        Root = $root
        Sid = $runnerSid
    }
    $allRunnerServices += $discoveredRunner
    if ([string]::IsNullOrWhiteSpace($RunnerRoot) -or
        $root.Equals([System.IO.Path]::GetFullPath($RunnerRoot), [StringComparison]::OrdinalIgnoreCase)) {
        $resolvedRunnerServices += $discoveredRunner
    }
}

if (-not [string]::IsNullOrWhiteSpace($RunnerRoot) -and $resolvedRunnerServices.Count -ne 1) {
    throw "Expected exactly one installed Runner service matching the supplied RunnerRoot; found $($resolvedRunnerServices.Count). No state permissions were changed."
}

# Do not touch ACLs or services when any Runner job is active.
$activeWorkers = @(Get-CimInstance Win32_Process -ErrorAction Stop | Where-Object {
    $_.Name -ieq "Runner.Worker.exe"
})
if ($activeWorkers.Count -gt 0) {
    throw "A GitHub Runner job is currently executing. State-broker setup was deferred without changing ACLs or services."
}

$machineStateDirectory = [Environment]::GetEnvironmentVariable("AUTONOMOUS_ENGINEERING_PROGRESS_DIR", "Machine")
$processStateDirectory = $env:AUTONOMOUS_ENGINEERING_PROGRESS_DIR
if (-not [string]::IsNullOrWhiteSpace($processStateDirectory) -and
    [string]::IsNullOrWhiteSpace($machineStateDirectory)) {
    throw "AUTONOMOUS_ENGINEERING_PROGRESS_DIR is process/user-scoped; configure a machine-level path before broker setup."
}
$stateRoot = Resolve-AutonomousStateDirectory -ConfiguredPath $machineStateDirectory -ProgramDataPath $env:ProgramData
$installerSid = [Security.Principal.WindowsIdentity]::GetCurrent().User.Value

$modifySids = @($brokerServiceSid)
foreach ($runner in $resolvedRunnerServices) {
    if ($runner.Sid -notin $modifySids) { $modifySids += $runner.Sid }
}

$stoppedRunnerServices = @()
$setupFailure = $null
try {
    # Stop listeners before changing the shared ACL, then re-check workers to
    # close the race where a job starts between process discovery and service stop.
    foreach ($runner in $allRunnerServices) {
        $service = Get-Service -Name $runner.Service.Name -ErrorAction Stop
        if ($service.Status -eq "Running") {
            Stop-Service -Name $service.Name -ErrorAction Stop
            $stoppedRunnerServices += $service.Name
            $service = Get-Service -Name $service.Name -ErrorAction Stop
            $service.WaitForStatus(
                [System.ServiceProcess.ServiceControllerStatus]::Stopped,
                [TimeSpan]::FromSeconds(30)
            )
        }
    }

    $activeWorkersAfterStop = @(Get-CimInstance Win32_Process -ErrorAction Stop | Where-Object {
        $_.Name -ieq "Runner.Worker.exe"
    })
    if ($activeWorkersAfterStop.Count -gt 0) {
        throw "A Runner worker remained active after stopping Runner listeners. No ACL or state files were changed."
    }

    New-Item -ItemType Directory -Path $stateRoot -Force | Out-Null
    Set-AutonomousDirectoryTreeAcl -Path $stateRoot -ModifySids $modifySids -OwnerSid $brokerServiceSid -InstallerSid $installerSid

    Install-AutonomousStateBrokerService

    $verified = Get-Service -Name $serviceName -ErrorAction Stop
    if ($verified.Status -ne "Running") {
        throw "The state broker service did not reach Running state."
    }
    $brokerCim = Get-CimInstance Win32_Service -Filter "Name='$serviceName'" -ErrorAction Stop
    if ($brokerCim.StartName -ne "NT AUTHORITY\LocalService") {
        throw "The state broker must run under NT AUTHORITY\LocalService."
    }
    $health = Invoke-AutonomousStateBroker -Method GET -Path "/v1/health" -TimeoutMilliseconds 5000
    if ($health.ready -ne $true -or $health.schema_version -ne 1) {
        throw "The installed state broker did not pass its health check."
    }
} catch {
    $setupFailure = $_
} finally {
    $restartFailures = @()
    foreach ($serviceNameToRestart in $stoppedRunnerServices) {
        try {
            $restarted = Get-Service -Name $serviceNameToRestart -ErrorAction Stop
            if ($restarted.Status -eq "StopPending") {
                $restarted.WaitForStatus(
                    [System.ServiceProcess.ServiceControllerStatus]::Stopped,
                    [TimeSpan]::FromSeconds(30)
                )
                $restarted = Get-Service -Name $serviceNameToRestart -ErrorAction Stop
            }
            if ($restarted.Status -eq "Stopped") {
                Start-Service -Name $serviceNameToRestart -ErrorAction Stop
            }
            $restarted = Get-Service -Name $serviceNameToRestart -ErrorAction Stop
            $restarted.WaitForStatus(
                [System.ServiceProcess.ServiceControllerStatus]::Running,
                [TimeSpan]::FromSeconds(30)
            )
        } catch {
            $restartFailures += "$serviceNameToRestart ($($_.Exception.GetType().Name))"
        }
    }
    if ($restartFailures.Count -gt 0) {
        $restartMessage = "Runner service restart failed: " + ($restartFailures -join ", ")
        if ($setupFailure) {
            throw "Broker setup failed: $($setupFailure.Exception.Message). $restartMessage"
        }
        throw $restartMessage
    }
}

if ($setupFailure) { throw $setupFailure }

Write-Host "Machine state broker is installed and healthy."
if ($resolvedRunnerServices.Count -gt 0) {
    Write-Host "Runner service identities configured: $($resolvedRunnerServices.Count)"
} else {
    Write-Host "No GitHub Runner is installed; the core application broker is ready. Autonomous dispatch remains unavailable until Runner provisioning is completed."
}
Write-Host "Shared state root: $stateRoot"
