[CmdletBinding()]
param()

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest
Import-Module (Join-Path $PSScriptRoot "AutonomousWindowsPaths.psm1") -Force

$identity = [Security.Principal.WindowsIdentity]::GetCurrent()
$machineStateDirectory = [Environment]::GetEnvironmentVariable("AUTONOMOUS_ENGINEERING_PROGRESS_DIR", "Machine")
$processStateDirectory = $env:AUTONOMOUS_ENGINEERING_PROGRESS_DIR

try {
    $statePath = Test-AutonomousStateStorageAccess -ConfiguredPath $machineStateDirectory -ProcessPath $processStateDirectory -ProgramDataPath $env:ProgramData
    $lockPath = Join-Path $statePath "autonomous-durable-state.lock"
    Write-Output "Identity: $($identity.Name)"
    Write-Output "SID: $($identity.User.Value)"
    Write-Output "State directory: $statePath"
    Write-Output "Durable lock: $lockPath"
    Write-Output "Result: directory write and durable lock access verified."
    exit 0
} catch {
    Write-Error "Identity '$($identity.Name)' could not write the shared state directory or acquire its durable lock: $($_.Exception.Message)"
    exit 1
}
