[CmdletBinding()]
param(
    [Parameter()]
    [ValidateNotNullOrEmpty()]
    [string]$RunnerAccount,

    [Parameter()]
    [ValidateNotNullOrEmpty()]
    [string]$AppAccount,

    [Parameter()]
    [ValidateNotNullOrEmpty()]
    [string]$RunnerRoot
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest
$installerIdentity = [Security.Principal.WindowsIdentity]::GetCurrent()
$installerPrincipal = [Security.Principal.WindowsPrincipal]::new($installerIdentity)
if (-not $installerPrincipal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    throw "Run from an elevated PowerShell session."
}

Import-Module (Join-Path $PSScriptRoot "AutonomousWindowsIdentity.psm1") -Force
Import-Module (Join-Path $PSScriptRoot "AutonomousWindowsPaths.psm1") -Force

$resolvedRunnerRoot = if (-not [string]::IsNullOrWhiteSpace($RunnerRoot)) {
    [System.IO.Path]::GetFullPath($RunnerRoot.Trim())
} else {
    [Environment]::GetEnvironmentVariable("AUTONOMOUS_ENGINEERING_RUNNER_ROOT", "Machine")
}
$runnerService = Get-AutonomousRunnerService -RunnerRoot $resolvedRunnerRoot
if ([string]::IsNullOrWhiteSpace($resolvedRunnerRoot)) {
    $exeMatch = [regex]::Match([string]$runnerService.PathName, '(?i)^"?(?<exe>.*?RunnerService\.exe)"?(?:\s|$)')
    if (-not $exeMatch.Success) { throw "Could not safely resolve the actual Runner service root." }
    $serviceBin = Split-Path -Parent $exeMatch.Groups["exe"].Value
    $resolvedRunnerRoot = if ((Split-Path -Leaf $serviceBin) -ieq "bin") { Split-Path -Parent $serviceBin } else { $serviceBin }
}
$runnerIdentity = ConvertTo-AutonomousWindowsAccountName -AccountName $runnerService.StartName
$runnerSid = Get-AutonomousWindowsAccountSid -AccountName $runnerIdentity
if (-not [string]::IsNullOrWhiteSpace($RunnerAccount)) {
    $requestedRunnerSid = Get-AutonomousWindowsAccountSid -AccountName (
        ConvertTo-AutonomousWindowsAccountName -AccountName $RunnerAccount.Trim()
    )
    if ($requestedRunnerSid -ne $runnerSid) {
        throw "The supplied RunnerAccount does not match the actual Windows service identity '$($runnerService.StartName)'."
    }
}

if ([string]::IsNullOrWhiteSpace($AppAccount)) {
    $applicationIdentity = Get-AutonomousApplicationIdentity
    if ($applicationIdentity) {
        $appIdentity = $applicationIdentity.Name
        $appSid = $applicationIdentity.Sid
        Write-Output "Discovered running application identity: $appIdentity ($appSid)"
    } else {
        throw "Could not discover the running application identity. Keep the local dashboard running during configuration or supply -AppAccount explicitly; the installer identity will not be guessed."
    }
} else {
    $appIdentity = ConvertTo-AutonomousWindowsAccountName -AccountName $AppAccount.Trim()
    $appSid = Get-AutonomousWindowsAccountSid -AccountName $appIdentity
}
if ($runnerSid -eq $appSid) {
    throw "The application and Runner resolve to the same Windows identity; configure separate identities."
}
[Environment]::SetEnvironmentVariable("AUTONOMOUS_ENGINEERING_RUNNER_ROOT", $resolvedRunnerRoot, "Machine")
[Environment]::SetEnvironmentVariable("AUTONOMOUS_ENGINEERING_RUNNER_ACCOUNT", $runnerIdentity, "Machine")

$machineStateDirectory = [Environment]::GetEnvironmentVariable("AUTONOMOUS_ENGINEERING_PROGRESS_DIR", "Machine")
$processStateDirectory = $env:AUTONOMOUS_ENGINEERING_PROGRESS_DIR
if (-not [string]::IsNullOrWhiteSpace($processStateDirectory) -and [string]::IsNullOrWhiteSpace($machineStateDirectory)) {
    throw "AUTONOMOUS_ENGINEERING_PROGRESS_DIR is process-scoped; configure one machine-wide shared state directory."
}
$progressPath = Resolve-AutonomousStateDirectory -ConfiguredPath $machineStateDirectory -ProgramDataPath $env:ProgramData
if ([string]::IsNullOrWhiteSpace($machineStateDirectory)) {
    [Environment]::SetEnvironmentVariable("AUTONOMOUS_ENGINEERING_PROGRESS_DIR", $progressPath, "Machine")
    $machineStateDirectory = $progressPath
}
if (-not (Test-Path -LiteralPath $progressPath -PathType Container)) {
    New-Item -ItemType Directory -Path $progressPath -Force | Out-Null
}

Set-AutonomousStateDirectoryAcl -Path $progressPath -RunnerSid $runnerSid -AdditionalModifySids @($appSid)
$verifiedPath = Test-AutonomousStateStorageAccess -ConfiguredPath $machineStateDirectory -ProcessPath $processStateDirectory -ProgramDataPath $env:ProgramData
Write-Output "Shared durable state is configured at '$verifiedPath'."
Write-Output "Application identity: $appIdentity ($appSid)"
Write-Output "Runner service identity: $runnerIdentity ($runnerSid)"
Write-Output "Both identities have additive Modify ACLs on the state directory and durable lock."
