[CmdletBinding()]
param(
    [System.Management.Automation.PSCredential]$SecondaryCredential,
    [string]$PythonPath
)

$ErrorActionPreference = "Stop"

if ([string]::IsNullOrWhiteSpace($PythonPath)) {
    $pythonCommand = Get-Command python.exe -ErrorAction Stop
    $PythonPath = $pythonCommand.Source
}
if (-not (Test-Path -LiteralPath $PythonPath -PathType Leaf)) {
    throw "The selected Python executable does not exist: $PythonPath"
}

if (-not $SecondaryCredential) {
    $SecondaryCredential = Get-Credential -Message "Enter credentials for a second, non-administrator Windows account."
}

$stateDirectory = [Environment]::GetEnvironmentVariable(
    "AUTONOMOUS_ENGINEERING_PROGRESS_DIR",
    "Machine"
)
if ([string]::IsNullOrWhiteSpace($stateDirectory)) {
    throw "The machine-level AUTONOMOUS_ENGINEERING_PROGRESS_DIR is not configured."
}
$stateDirectory = [Environment]::ExpandEnvironmentVariables($stateDirectory.Trim())
if (-not [System.IO.Path]::IsPathRooted($stateDirectory)) {
    throw "The shared state directory must be an absolute path."
}

$repoRoot = Split-Path -Parent $PSScriptRoot
$modulePath = Join-Path $PSScriptRoot "AutonomousStateBroker.psm1"
Import-Module $modulePath -Force

$health = Invoke-AutonomousStateBroker -Method GET -Path "/v1/health" -TimeoutMilliseconds 5000
if ($null -eq $health -or $health.ready -ne $true) {
    throw "The machine state broker is not healthy."
}
$progress = Invoke-AutonomousStateBroker -Method GET -Path "/v1/progress" -TimeoutMilliseconds 5000
if ($null -eq $progress -or $progress.storage_configured -ne $true) {
    throw "The broker did not return a configured shared progress snapshot."
}
$pending = Invoke-AutonomousStateBroker -Method GET -Path "/v1/pending-work" -TimeoutMilliseconds 5000
if ($null -eq $pending -or $pending.available -ne $true) {
    throw "The broker did not return a valid pending-work snapshot."
}

$currentUpdatedAt = ""
if ($null -ne $progress.current -and $progress.current.updated_at) {
    $currentUpdatedAt = [string]$progress.current.updated_at
}
$expected = @{
    current_present = ($null -ne $progress.current)
    current_updated_at = $currentUpdatedAt
    history_count = @($progress.history).Count
    events_count = @($progress.events).Count
    pending_markers = $pending.markers
}
$expectedJson = ConvertTo-Json -InputObject $expected -Depth 20 -Compress
$expectedBase64 = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($expectedJson))
$stateBase64 = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($stateDirectory))
$repoBase64 = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($repoRoot))

$pythonProbe = @"
import base64
import json
import os
import sys

repo_root = base64.b64decode("$repoBase64").decode("utf-8")
state_directory = base64.b64decode("$stateBase64").decode("utf-8")
expected = json.loads(base64.b64decode("$expectedBase64").decode("utf-8"))
sys.path.insert(0, repo_root)

from app import autonomous_engineering_state_broker as broker

if not broker.health():
    raise SystemExit("The second Windows account cannot connect to the state broker health endpoint.")

snapshot = broker.progress()
if snapshot.get("storage_configured") is not True:
    raise SystemExit("The second Windows account did not receive configured shared progress.")

pending = broker.pending_work()
actual = {
    "current_present": snapshot.get("current") is not None,
    "current_updated_at": str((snapshot.get("current") or {}).get("updated_at") or ""),
    "history_count": len(snapshot.get("history") or []),
    "events_count": len(snapshot.get("events") or []),
    "pending_markers": pending.get("markers"),
}
normalize = lambda value: json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
if normalize(actual) != normalize(expected):
    raise SystemExit(
        "The second account received a different shared progress or pending-work snapshot. "
        + "Expected=" + normalize(expected) + " Actual=" + normalize(actual)
    )

try:
    with os.scandir(state_directory):
        pass
except PermissionError:
    pass
else:
    raise SystemExit("The second account unexpectedly accessed the protected state directory directly.")

print("PASS: second account sees identical brokered progress and cannot directly enumerate protected state.")
"@

$probeId = [guid]::NewGuid().ToString("N")
$probePath = Join-Path $PSScriptRoot (".state-broker-probe-$probeId.py")
$stdoutPath = Join-Path $PSScriptRoot (".state-broker-probe-$probeId.stdout.log")
$stderrPath = Join-Path $PSScriptRoot (".state-broker-probe-$probeId.stderr.log")
try {
    [System.IO.File]::WriteAllText($probePath, $pythonProbe, [System.Text.UTF8Encoding]::new($false))
    $process = Start-Process -FilePath $PythonPath -Credential $SecondaryCredential -ArgumentList ('"' + $probePath + '"') -Wait -PassThru -RedirectStandardOutput $stdoutPath -RedirectStandardError $stderrPath
    if ($process.ExitCode -ne 0) {
        $stdout = if (Test-Path -LiteralPath $stdoutPath) { Get-Content -LiteralPath $stdoutPath -Raw } else { "" }
        $stderr = if (Test-Path -LiteralPath $stderrPath) { Get-Content -LiteralPath $stderrPath -Raw } else { "" }
        throw "Cross-account state-broker acceptance test failed (Python exit code $($process.ExitCode)). stdout: $stdout stderr: $stderr"
    }
    if (Test-Path -LiteralPath $stdoutPath) { Get-Content -LiteralPath $stdoutPath }
} finally {
    foreach ($temporaryPath in @($probePath, $stdoutPath, $stderrPath)) {
        if (Test-Path -LiteralPath $temporaryPath) {
            Remove-Item -LiteralPath $temporaryPath -Force -ErrorAction SilentlyContinue
        }
    }
}

Write-Host "PASS: broker is healthy; a second non-admin account sees the same progress and is denied direct state-directory access."
