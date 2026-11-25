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
$powershell = [string](Get-Process -Id $PID -ErrorAction Stop).Path
if ([string]::IsNullOrWhiteSpace($powershell) -or
    -not (Test-Path -LiteralPath $powershell -PathType Leaf)) {
    throw "Could not resolve the executable path of the active PowerShell host."
}

Write-Host "Delegating shared-state ACL and broker repair to the canonical fail-closed setup."
& $powershell -NoLogo -NoProfile -NonInteractive -ExecutionPolicy Bypass -File $ensureScript
if ($LASTEXITCODE -ne 0) {
    throw "The canonical state-broker reconciliation failed with exit code $LASTEXITCODE."
}
Write-Output "Shared progress ACLs and broker health are reconciled for the installed Runner service identities."
