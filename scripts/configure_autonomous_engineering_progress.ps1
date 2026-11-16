[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [ValidateNotNullOrEmpty()]
    [string]$RunnerAccount,

    [Parameter()]
    [ValidateNotNullOrEmpty()]
    [string]$AppAccount
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
$resolvedAppAccount = if ([string]::IsNullOrWhiteSpace($AppAccount)) { $installerIdentity.Name } else { $AppAccount.Trim() }
$runnerIdentity = ConvertTo-AutonomousWindowsAccountName -AccountName $RunnerAccount.Trim()
$appIdentity = ConvertTo-AutonomousWindowsAccountName -AccountName $resolvedAppAccount
$runnerSid = Get-AutonomousWindowsAccountSid -AccountName $runnerIdentity
$appSid = if ($resolvedAppAccount -eq $installerIdentity.Name) { $installerIdentity.User.Value } else { Get-AutonomousWindowsAccountSid -AccountName $appIdentity }
if ($runnerSid -eq $appSid) {
    throw "The application and Runner must resolve to different Windows identities."
}

$machineStateDirectory = [Environment]::GetEnvironmentVariable("AUTONOMOUS_ENGINEERING_PROGRESS_DIR", "Machine")
$processStateDirectory = $env:AUTONOMOUS_ENGINEERING_PROGRESS_DIR
if (-not [string]::IsNullOrWhiteSpace($processStateDirectory) -and [string]::IsNullOrWhiteSpace($machineStateDirectory)) {
    throw "AUTONOMOUS_ENGINEERING_PROGRESS_DIR must be configured at machine scope."
}
$progressPath = Resolve-AutonomousStateDirectory -ConfiguredPath $machineStateDirectory -ProgramDataPath $env:ProgramData
if (-not (Test-Path -LiteralPath $progressPath)) {
    New-Item -ItemType Directory -Path $progressPath -Force | Out-Null
}
Set-AutonomousStateDirectoryAcl -Path $progressPath -RunnerSid $runnerSid -AdditionalModifySids $appSid
$verifiedPath = Test-AutonomousStateStorageAccess -ConfiguredPath $machineStateDirectory -ProcessPath $processStateDirectory -ProgramDataPath $env:ProgramData
Write-Output "Shared durable state storage is ready for the resolved application and Runner identities: $verifiedPath"
