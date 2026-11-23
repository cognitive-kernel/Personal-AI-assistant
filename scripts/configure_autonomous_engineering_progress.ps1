[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [ValidateNotNullOrEmpty()]
    [string]$RunnerAccount
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest
Import-Module (Join-Path $PSScriptRoot "AutonomousWindowsIdentity.psm1") -Force

# Keep this compatibility entry point aligned with the canonical broker repair
# path. It must never rewrite the shared ACL independently while a Runner job
# may be active.
$runnerIdentity = ConvertTo-AutonomousWindowsAccountName -AccountName $RunnerAccount
$runnerSid = Get-AutonomousWindowsAccountSid -AccountName $runnerIdentity
$runnerServices = @(Get-CimInstance Win32_Service -ErrorAction Stop | Where-Object {
    $_.PathName -match '(?i)RunnerService\.exe'
})
$matchingRunnerServices = @()
foreach ($service in $runnerServices) {
    $serviceSid = Get-AutonomousWindowsAccountSid -AccountName $service.StartName
    if ($serviceSid -eq $runnerSid) {
        $matchingRunnerServices += $service
    }
}
if ($matchingRunnerServices.Count -eq 0) {
    throw "No installed GitHub Runner service uses the requested account '$runnerIdentity'. No state ACLs were changed."
}

$ensureScript = Join-Path $PSScriptRoot "ensure_autonomous_state_broker.ps1"
if (-not (Test-Path -LiteralPath $ensureScript -PathType Leaf)) {
    throw "The canonical state-broker reconciliation script is missing: $ensureScript"
}
$hostExecutableName = if ($PSVersionTable.PSEdition -eq "Core") { "pwsh.exe" } else { "powershell.exe" }
$hostExecutable = Join-Path $PSHOME $hostExecutableName
if (-not (Test-Path -LiteralPath $hostExecutable -PathType Leaf)) {
    throw "The current PowerShell host executable could not be resolved from PSHOME ($PSHOME)."
}

Write-Host "Delegating shared-state ACL and broker repair to the canonical fail-closed setup."
$previousModulePath = $env:PSModulePath
try {
    $env:PSModulePath = Join-Path $PSHOME "Modules"
    & $hostExecutable -NoLogo -NoProfile -NonInteractive -ExecutionPolicy Bypass -File $ensureScript
    if ($LASTEXITCODE -ne 0) {
        throw "The canonical state-broker reconciliation failed with exit code $LASTEXITCODE."
    }
} finally {
    $env:PSModulePath = $previousModulePath
}
Write-Output "Shared progress ACLs and broker health are reconciled for the installed Runner service identities."
