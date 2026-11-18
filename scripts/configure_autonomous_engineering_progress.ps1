[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [ValidateNotNullOrEmpty()]
    [string]$RunnerAccount
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest
Import-Module (Join-Path $PSScriptRoot "AutonomousWindowsIdentity.psm1") -Force
Import-Module (Join-Path $PSScriptRoot "AutonomousWindowsPaths.psm1") -Force

$identity = [Security.Principal.WindowsIdentity]::GetCurrent()
$installerSid = $identity.User.Value
$principal = [Security.Principal.WindowsPrincipal]::new($identity)
if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    throw "Run this configuration from elevated PowerShell."
}
$runnerIdentity = ConvertTo-AutonomousWindowsAccountName -AccountName $RunnerAccount
$runnerSid = Get-AutonomousWindowsAccountSid -AccountName $runnerIdentity
$brokerServiceSid = "S-1-5-19"

$machineStatePath = [Environment]::GetEnvironmentVariable("AUTONOMOUS_ENGINEERING_PROGRESS_DIR", "Machine")
$processStatePath = $env:AUTONOMOUS_ENGINEERING_PROGRESS_DIR
if (-not [string]::IsNullOrWhiteSpace($processStatePath) -and [string]::IsNullOrWhiteSpace($machineStatePath)) {
    throw "AUTONOMOUS_ENGINEERING_PROGRESS_DIR is process/user-scoped; configure it at machine scope."
}
if (-not [string]::IsNullOrWhiteSpace($processStatePath)) {
    # Machine scope is authoritative; user-scoped overrides are not consulted.
}
$progressPath = Resolve-AutonomousStateDirectory -ConfiguredPath $machineStatePath -ProgramDataPath $env:ProgramData
New-Item -ItemType Directory -Path $progressPath -Force | Out-Null

# Remove stale app-user ACEs recursively. Only the dedicated Runner role and
# the LocalService broker retain direct filesystem access to this data tree.
Set-AutonomousDirectoryTreeAcl -Path $progressPath -ModifySids @($runnerSid, $brokerServiceSid) -OwnerSid $brokerServiceSid -InstallerSid $installerSid

$broker = Get-Service -Name "PersonalAIAssistantStateBroker" -ErrorAction SilentlyContinue
if (-not $broker -or $broker.Status -ne "Running") {
    throw "The machine state broker is not installed or running; rerun provision_autonomous_runner.ps1."
}
Write-Output "Shared progress storage is protected for Runner and LocalService broker access."
Write-Output "Application sessions use the named-pipe broker and do not need filesystem ACL entries."
