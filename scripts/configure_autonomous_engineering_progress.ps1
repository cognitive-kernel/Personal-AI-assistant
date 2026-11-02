[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [ValidateNotNullOrEmpty()]
    [string]$RunnerAccount,

    [Parameter(Mandatory = $true)]
    [ValidateNotNullOrEmpty()]
    [string]$AppAccount
)

$ErrorActionPreference = "Stop"
$RunnerAccount = $RunnerAccount.Trim()
$AppAccount = $AppAccount.Trim()
if ([string]::Equals($RunnerAccount, $AppAccount, [System.StringComparison]::OrdinalIgnoreCase)) {
    throw "RunnerAccount and AppAccount must be different Windows identities."
}
$progressPath = Join-Path $env:ProgramData "PersonalAIAssistant\autonomous-engineering"

if (-not (Test-Path -LiteralPath $progressPath)) {
    New-Item -ItemType Directory -Path $progressPath -Force | Out-Null
}

$rules = @(
    "${RunnerAccount}:(OI)(CI)M",
    "${AppAccount}:(OI)(CI)RX",
    "SYSTEM:(OI)(CI)F",
    "BUILTIN\Administrators:(OI)(CI)F"
)

& icacls.exe $progressPath /inheritance:r /T /C /grant:r $rules
if ($LASTEXITCODE -ne 0) {
    throw "Could not configure progress directory permissions (icacls exit code $LASTEXITCODE)."
}

Write-Output "Progress telemetry directory is ready: $progressPath"
Write-Output "Only the specified runner/app accounts, SYSTEM, and local administrators have access."
